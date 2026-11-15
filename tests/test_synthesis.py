import copy
import json

import pytest
from pydantic import ValidationError

from afd.synthesis import BuildPlan, markdown, sentences, synthesize


def fixture_plan(payload):
    answers = payload['answers']
    excerpts = [s for a in answers for s in sentences(a['answer'])]
    topics = [r['topic'] for r in payload['ontology'] if r['routing_allowed']]
    return {'summary':' '.join(excerpts[:3]),
            'findings':[{'finding':sentences(a['answer'])[0], 'source_person_email':a['person_email']} for a in answers],
            'security_requirements':[excerpts[0]],
            'tasks':[{'title':f'Implement workflow task {i+1}', 'description':'Implement this constraint: '+excerpts[i % len(excerpts)],
                      'owner_email':'', 'topic':topics[i % len(topics)]} for i in range(7)],
            'risks':[excerpts[-1]],
            'open_questions':['Who were the external viewers?', 'What is the sighting timestamp?', 'Has CEO approval been recorded?']}


@pytest.fixture
def completed(tmp_path):
    from test_sentinel import FixtureModel, RecordingDocuments
    from afd.sentinel import SentinelWatcher
    w = SentinelWatcher(RecordingDocuments(tmp_path), model=FixtureModel(), background=False)
    w.publish()
    doc = w.start(run_all=True)
    assert doc['status'] == 'done', doc.get('execution_error')
    return w, doc


def test_plan_owners_are_routed_safe_and_synthesis_checkpointed(completed):
    w, doc = completed
    plan = BuildPlan.model_validate(doc['build_plan'])
    assert 6 <= len(plan.tasks) <= 8
    owners = {r['topic']:r['owner_email'] for r in w.pipeline()['silver_responsibilities'] if r.get('routing_allowed')}
    assert all(t.owner_email == owners[t.topic] for t in plan.tasks)
    event = next(e for e in doc['events'] if e['action'] == 'synthesis')
    assert set(event['result']['task_owners']) == {t.owner_email for t in plan.tasks}
    assert doc['plan_created_at']
    assert any(d.get('answer') and not d.get('build_plan') for d in w.db.checkpoints if d.get('run_id') == doc['run_id'])


def draft_for(w, doc):
    return fixture_plan({'answers':[{'person_email':r['asked_person_email'],'answer':r['answer']} for r in doc['requests']],
                         'ontology':w.pipeline()['silver_responsibilities']})


def model_with_draft(w, draft, audit=None):
    prior = w.model
    def model(messages):
        if messages[0]['content'].startswith('SYNTHESIZE_SENTINEL_PLAN:'):
            return copy.deepcopy(draft)
        if messages[0]['content'].startswith('AUDIT_SENTINEL_PLAN:'):
            return audit or {'supported':True,'issues':[]}
        return prior(messages)
    w.model = model


def test_wrong_or_quarantined_model_owner_is_corrected(completed):
    w, doc = completed
    draft = draft_for(w,doc)
    draft['tasks'][0]['owner_email'] = 'jamal.reed@wildguardlabs.org'
    model_with_draft(w,draft)
    plan, corrections = synthesize(w,doc,w.pipeline())
    assert corrections and plan['tasks'][0]['owner_email'] != 'jamal.reed@wildguardlabs.org'


def test_quarantined_or_unowned_topics_are_flagged_not_published(completed):
    w, doc = completed
    for topic in ['map and location data','partner ngo agreements']:
        draft = draft_for(w,doc)
        draft['tasks'][0]['topic'] = topic
        model_with_draft(w,draft)
        with pytest.raises(ValueError,match='ontology review'):
            synthesize(w,doc,w.pipeline())


@pytest.mark.parametrize('field', ['summary','findings'])
def test_unsupported_fact_or_false_source_is_rejected(completed, field):
    w, doc = completed
    draft = draft_for(w,doc)
    if field == 'summary':
        draft['summary'] = 'The CEO approved disclosure. All coordinates are safe. No views occurred.'
    else:
        draft['findings'][0]['source_person_email'] = 'elena.marsh@wildguardlabs.org'
    model_with_draft(w,draft)
    with pytest.raises(ValueError, match='unsupported|exact excerpt'):
        synthesize(w,doc,w.pipeline())


def test_explicit_unknown_question_is_always_retained(completed):
    w, doc = completed
    doc['requests'][0]['question'] = 'Who were the fourteen external viewers by name?'
    doc['requests'][0]['answer'] = "I don't know from my context. Nadia Petrov (CTO) might know."
    draft = draft_for(w,doc)
    draft['open_questions'] = []
    model_with_draft(w,draft)
    plan, _ = synthesize(w,doc,w.pipeline())
    assert doc['requests'][0]['question'] in plan['open_questions']
    assert not any('Alice' in f['finding'] for f in plan['findings'])


def test_audit_rejects_unsupported_security_details(completed):
    w,doc=completed
    model_with_draft(w,draft_for(w,doc), {'supported':False,'issues':['Unsupported implementation claim']})
    with pytest.raises(ValueError,match='grounding audit failed'):
        synthesize(w,doc,w.pipeline())


def test_json_schema_rejects_wrong_task_count_or_summary(completed):
    w,doc=completed
    draft=draft_for(w,doc)
    draft['tasks']=draft['tasks'][:5]
    with pytest.raises(ValidationError):
        BuildPlan.model_validate(draft)
    draft=draft_for(w,doc)
    draft['summary']='Just one sentence.'
    with pytest.raises(ValidationError):
        BuildPlan.model_validate(draft)


def test_plan_api_markdown_download_and_no_legacy_backfill(completed, monkeypatch):
    from fastapi.testclient import TestClient
    import afd.watcher as module
    from afd.main import app
    w,doc=completed
    monkeypatch.setattr(module,'_watcher',w)
    monkeypatch.setenv('AFD_WORKFLOW','sentinel')
    with TestClient(app) as client:
        data=client.get(f'/api/watcher/runs/{doc["run_id"]}/plan').json()
        assert len(data['plan']['tasks']) == 7
        result=client.get(f'/api/watcher/runs/{doc["run_id"]}/plan.md')
        assert result.status_code == 200 and 'plan.md' in result.headers['content-disposition']
        assert '#person=' in result.text and '## Open questions' in result.text
        assert data['markdown'] == result.text
    raw=copy.deepcopy(doc)
    raw.pop('build_plan')
    w.db.put(raw,raw['revision'])
    with pytest.raises(KeyError):
        w.build_plan(doc['run_id'])


def test_plans_physical_projection_precedes_synthesis_event(completed):
    from afd.watchstore import DeltaDocuments
    import threading
    _,doc=completed
    db=DeltaDocuments.__new__(DeltaDocuments)
    db.lock=threading.RLock()
    db.get=lambda _:doc
    writes=[]
    db._project_row=lambda table,key,row,**kwargs:writes.append((table,row))
    db._project_rows=lambda table,rows,**kwargs:writes.append((table,rows))
    db.project(doc)
    tables=[t for t,_ in writes]
    assert tables.index('plans') < tables.index('events')
    row=next(row for table,row in writes if table=='plans')
    assert json.loads(row['plan_json'])==doc['build_plan'] and row['run_id']==doc['run_id']


def test_one_schema_repair_is_bounded(completed):
    w,doc=completed
    base=w.model
    calls=[]
    def model(messages):
        if messages[0]['content'].startswith('SYNTHESIZE_SENTINEL_PLAN:'):
            calls.append(messages)
            if len(calls)==1:
                draft=draft_for(w,doc)
                draft['summary']='Only one sentence.'
                return draft
        return base(messages)
    w.model=model
    plan,_=synthesize(w,doc,w.pipeline())
    assert len(calls)==2 and len(sentences(plan['summary']))==3


def test_failed_synthesis_retries_without_reasking_personas(tmp_path):
    from test_sentinel import FixtureModel, RecordingDocuments
    from afd.sentinel import SentinelWatcher
    model=FixtureModel()
    def fail_audit(messages):
        if messages[0]['content'].startswith('AUDIT_SENTINEL_PLAN:'):
            return {'supported':False,'issues':['Review needed']}
        return model(messages)
    w=SentinelWatcher(RecordingDocuments(tmp_path),model=fail_audit,background=False)
    w.publish()
    failed=w.start(run_all=True)
    assert failed['answer'] and not failed.get('build_plan') and failed['execution_error']
    requests=[r['request_id'] for r in failed['requests']]
    w.model=model
    done=w.control(failed['run_id'],'retry','test')
    assert done['status']=='done' and done['build_plan']
    assert requests==[r['request_id'] for r in done['requests']]
    assert sum(c[0]['content'].startswith('You are ') for c in model.calls)==5


def test_overlong_summary_is_trimmed_only_when_every_sentence_is_grounded(completed):
    w,doc=completed
    draft=draft_for(w,doc)
    original=sentences(draft['summary'])
    fourth=sentences(doc['requests'][1]['answer'])[0]
    draft['summary']=' '.join(original+[fourth])
    model_with_draft(w,draft)
    plan,_=synthesize(w,doc,w.pipeline())
    assert plan['summary']==' '.join(original)


def test_unmentioned_technical_mechanism_in_description_is_rejected(completed):
    w,doc=completed
    draft=draft_for(w,doc)
    draft['tasks'][0]['description']='Implement this constraint: Require a signed digital approval token.'
    model_with_draft(w,draft)
    with pytest.raises(ValueError,match='exact source constraint'):
        synthesize(w,doc,w.pipeline())


def test_routing_tool_cannot_return_quarantined_person(completed):
    w,doc=completed
    w.who_should_i_ask=lambda *args:{'owner':{'id':'jamal.reed@wildguardlabs.org'}}
    with pytest.raises(ValueError,match='invalid or quarantined'):
        synthesize(w,doc,w.pipeline())
