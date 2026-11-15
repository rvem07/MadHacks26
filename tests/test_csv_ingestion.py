import csv
import io
from pathlib import Path
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from afd.pipeline import build_uploaded_pipeline,UPLOAD_REQUIRED
from afd.ingestion import Ingestor
from afd.watcher import Watcher

SAMPLE=Path('samples/team.csv').read_text()


def team_csv(rows):
    stream=io.StringIO();writer=csv.DictWriter(stream,fieldnames=UPLOAD_REQUIRED)
    writer.writeheader();writer.writerows(rows);return stream.getvalue()


def test_single_csv_uses_same_dedup_and_quarantine_pipeline():
    p=build_uploaded_pipeline(SAMPLE,'team.csv')
    assert p['stats']['raw_people_rows']==12 and len(p['nodes'])==10
    assert len(p['quarantine'])==2 and p['stats']['valid_collab_edges']==8
    assert p['raw_files'][0]['content']==SAMPLE
    assert p['raw_files'][0]['filename']=='team.csv'
    assert len(p['bronze']['person_context'])==10
    assert all(p['batch_id']==n['batch_id'] for n in p['nodes'])


@pytest.mark.parametrize('text',['','name,email\nA,a@example.test\n',','.join(UPLOAD_REQUIRED)+'\n',','.join(UPLOAD_REQUIRED)+'\nA,b\n',','.join(UPLOAD_REQUIRED)+'\n"unterminated'])
def test_bad_csv_is_rejected_before_publication(text):
    with pytest.raises(ValueError):build_uploaded_pipeline(text)


def test_unknown_manager_is_reviewed_and_empty_collaborators_allowed():
    text=team_csv([dict(name='Ada Example',email='ada@example.test',title='Engineer',team='Product',manager_email='missing@example.test',expertise='platform',context_notes='Use a simple web interface.')])
    p=build_uploaded_pipeline('\ufeff'+text)
    assert len(p['nodes'])==1 and p['nodes'][0]['needs_review']
    assert p['quarantine'][0]['reason']=='manager_not_in_directory'
    assert p['stats']['valid_collab_edges']==0


def test_conflicting_duplicate_context_is_rejected():
    rows=list(csv.DictReader(io.StringIO(SAMPLE)))
    rows[-1]['context_notes']='A different set of requirements.'
    stream=io.StringIO();writer=csv.DictWriter(stream,fieldnames=rows[0].keys());writer.writeheader();writer.writerows(rows)
    with pytest.raises(ValueError,match='conflicting'):build_uploaded_pipeline(stream.getvalue())


def test_upload_publishes_before_switching_active_batch(db):
    old=db.put({'id':'active_pipeline','batch_id':'old'})
    ingestor=Ingestor(db,background=False);job=ingestor.start(SAMPLE,'team.csv')
    assert db.get('active_pipeline')['batch_id']=='old'
    ingestor.execute(job['job_id']);result=ingestor.get(job['job_id'])
    assert result['status']=='done' and db.get('active_pipeline')['batch_id']==result['batch_id']
    assert db.get(result['pipeline']['id'])['raw_files'][0]['content']==SAMPLE
    ingestor.execute(job['job_id'])
    assert db.get('active_pipeline')['revision']==old['revision']+1


def test_projection_failure_keeps_previous_ontology(db,monkeypatch):
    db.put({'id':'active_pipeline','batch_id':'old'})
    ingestor=Ingestor(db,background=False);job=ingestor.start(SAMPLE,'team.csv')
    monkeypatch.setattr(db,'project',lambda _:(_ for _ in ()).throw(RuntimeError('warehouse unavailable')))
    ingestor.execute(job['job_id'])
    assert ingestor.get(job['job_id'])['status']=='error'
    assert db.get('active_pipeline')['batch_id']=='old'


def test_new_team_expertise_does_not_stop_interviews(db):
    graph={'nodes':[dict(id='engineer@example.test',expertise='platform')],'edges':[]}
    choice=Watcher(db,background=False).next_question({'run_context':[]},graph)
    assert choice['person']=='engineer@example.test'


def test_csv_api_and_separate_sample_download(db,monkeypatch):
    import afd.watcher_api as api
    w=Watcher(db,background=False);monkeypatch.setattr(api,'get_watcher',lambda:w)
    app=FastAPI();app.include_router(api.router,prefix='/api')
    with TestClient(app) as client:
        sample=client.get('/api/samples/team.csv')
        assert sample.status_code==200 and sample.content==Path('samples/team.csv').read_bytes()
        assert 'attachment' in sample.headers['content-disposition']
        bad=client.post('/api/ingestions',json={'filename':'bad.csv','csv_text':'name,email\nA,b'})
        assert bad.status_code==400 and 'columns' in bad.json()['detail']
        response=client.post('/api/ingestions',json={'filename':'team.csv','csv_text':SAMPLE})
        assert response.status_code==200
        job=response.json();assert 'pipeline' not in job
        w.ingestor.execute(job['job_id'])
        status=client.get('/api/ingestions/'+job['job_id']).json()
        assert status['status']=='done' and status['stats']['unique_people']==10
        assert 'pipeline' not in status
        assert client.get('/api/ingestions/missing').status_code==404


def test_ingestion_recovers_pending_jobs(db,monkeypatch):
    ingestor=Ingestor(db,background=False);job=ingestor.start(SAMPLE,'team.csv')
    scheduled=[];monkeypatch.setattr(ingestor,'schedule',scheduled.append)
    ingestor.recover();assert scheduled==[job['job_id']]
