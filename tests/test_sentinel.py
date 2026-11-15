"""Explicit test doubles only: production Sentinel refuses local persistence/models."""
import ast
import copy
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from afd.pipeline import SENTINEL, build_pipeline
from afd.sentinel import SentinelWatcher, DEMO_TASK
from afd.watchstore import Documents, Conflict

ELENA = 'elena.marsh@wildguardlabs.org'
BEN = 'ben.okafor@wildguardlabs.org'


class FixtureModel:
    def __init__(self, barrier=None):
        self.barrier = barrier
        self.calls = []

    def __call__(self, messages):
        system = messages[0]['content']
        question = messages[-1]['content']
        self.calls.append(copy.deepcopy(messages))
        if system.startswith('Match the task'):
            topics = ast.literal_eval(system.split('allowed: ')[1])
            topic = 'security incident' if question == 'data breach' else question
            return {'topic': topic if topic in topics else None, 'confidence': 1.0}
        if system.startswith('You are '):
            if self.barrier:
                self.barrier.wait(timeout=10)
            notes = ast.literal_eval(system.split('Context: ')[1].split('. Directory (')[0])
            if 'cafeteria' in question:
                return {'context_ids': [], 'unknown': True, 'refer_to': ELENA}
            return {'context_ids': list(range(len(notes))), 'unknown': False}
        evidence = ast.literal_eval(question)['evidence']
        return {'selections':[{'request_id':e['request_id'], 'sentence_ids':list(range(min(2,len(e['sentences']))))} for e in evidence]}


class RecordingDocuments(Documents):
    def __init__(self, path):
        super().__init__(path)
        self.checkpoints = []

    def project(self, doc):
        self.checkpoints.append(copy.deepcopy(doc))


@pytest.fixture
def sentinel(tmp_path):
    w = SentinelWatcher(RecordingDocuments(tmp_path), model=FixtureModel(), background=False)
    w.publish()
    return w


def test_pipeline_counts_identity_provenance_and_routing_exclusions():
    p = build_pipeline(SENTINEL)
    assert (p['stats']['raw_people'], p['stats']['unique_people'], p['stats']['duplicates_merged'], p['stats']['quarantined_count']) == (12, 10, 1, 4)
    assert len(p['quarantine']) == 6
    assert set(p['stats']['violation_counts']) == {'multiple_managers', 'missing_manager', 'reporting_cycle', 'unowned_topic'}
    assert p['bronze']['org_chart'][1]['email'] == ' Kofi.Mensah@wildguardlabs.org '
    assert p['bronze']['responsibilities'][3]['owner_email'] == ' Mei.Tanaka@wildguardlabs.org '
    people = [n for n in p['nodes'] if n['type'] == 'person']
    assert len(people) == 10
    assert next(n for n in people if n['name'] == 'Mei Tanaka')['props']['aliases'] == ['Mei T.', 'Mei Tanaka']
    review = {n['id'] for n in people if n['props']['needs_review']}
    assert review == {name + '@wildguardlabs.org' for name in ['jamal.reed','hannah.lindqvist','sofia.duarte','priya.nair']}
    assert not review & {r['person_email'] for r in p['routing_chains']}
    assert not review & {r['owner_email'] for r in p['routing_chains']}
    assert len([r for r in p['silver_responsibilities'] if r['topic'] == 'security incident']) == 1
    assert {e['type'] for e in p['edges']} == {'reports_to','member_of','responsible_for'}
    assert all({'properties','source','confidence','updated_at'} <= n.keys() for n in p['nodes'])
    assert all(2 <= sum(r['person_email'] == person['id'] for r in p['person_context']) <= 4 for person in people)


def test_start_routes_ben_pauses_resumes_then_mei_carlos_elena(sentinel):
    run = sentinel.start(DEMO_TASK)
    assert run['status'] == 'done', run.get('execution_error')
    assert [r['asked_person_email'].split('.')[0] for r in run['requests']] == ['ben','mei','carlos','elena']
    assert '14 external views' in run['requests'][0]['answer']
    assert '2:10 PM to 4:05 PM' in run['requests'][0]['answer']
    states = [e['state'] for e in run['events']]
    assert 'waiting_on_human' in states and 'resumed' in states
    snapshots = [d for d in sentinel.db.checkpoints if d.get('run_id') == run['run_id']]
    assert any(d['status'] == 'waiting_on_human' and d['requests'][0]['status'] == 'pending' for d in snapshots)
    route = sentinel.who_should_i_ask('data breach', sentinel.pipeline())
    assert [p['name'] for p in route['chain']] == ['Ben Okafor','Nadia Petrov','Elena Marsh']
    assert route['method'] == 'model-assisted match'
    assert sentinel.who_should_i_ask('map and location data', sentinel.pipeline())['owner'] is None


def test_run_all_dispatches_five_personas_concurrently(sentinel):
    sentinel.model = FixtureModel(threading.Barrier(5))
    run = sentinel.start(DEMO_TASK, run_all=True)
    assert run['status'] == 'done', run.get('execution_error')
    snapshots = [d for d in sentinel.db.checkpoints if d.get('run_id') == run['run_id']]
    assert any(sum(r['status'] == 'pending' for r in d['requests']) == 5 for d in snapshots)
    assert len([e for e in run['events'] if e['action'] == 'persona_answer']) == 5
    assert run['events'][-1]['action'] == 'conclude'


@pytest.mark.parametrize('run_all', [False, True])
def test_elena_manual_wait_persists_on_restart_and_resumes(sentinel, run_all):
    sentinel.preferences(ELENA, True)
    run = sentinel.start(DEMO_TASK, run_all=run_all)
    assert run['status'] == 'waiting_on_human'
    pending = [r for r in run['requests'] if r['status'] == 'pending']
    assert len(pending) == 1 and pending[0]['asked_person_email'] == ELENA
    restarted = SentinelWatcher(sentinel.db, model=FixtureModel(), background=False)
    revision = run['revision']
    restarted.recover()
    assert restarted.get(run['run_id'])['revision'] == revision
    done = restarted.answer(pending[0]['request_id'], 'CEO approval is required; no approval recorded for this incident.', ELENA)
    assert done['status'] == 'done'
    assert done['events'][-2]['action'] == 'human_answer'
    with pytest.raises(Conflict):
        restarted.answer(pending[0]['request_id'], 'duplicate answer', ELENA)


def test_persona_unknown_names_someone_and_invalid_context_fails(sentinel):
    answer = sentinel.persona(BEN, 'What is the cafeteria menu today?')
    assert "I don't know from my context" in answer and 'Elena Marsh' in answer
    sentinel.model = lambda _: {'context_ids':[999], 'unknown':False}
    with pytest.raises(ValueError, match='invalid context'):
        sentinel.persona(BEN, 'What do the logs show?')


def test_persona_preserves_partial_known_context_even_with_unknown_flag(sentinel):
    sentinel.model = lambda _: {'context_ids':[0,1], 'unknown':True, 'refer_to':ELENA}
    answer = sentinel.persona(BEN, 'What do the logs confirm and what remains unknown?')
    assert '14 external views' in answer and 'unpublished at 4:05 PM' in answer


def test_summary_refuses_unattributed_generated_facts(sentinel):
    model = FixtureModel()
    def ungrounded_summary(messages):
        if messages[0]['content'].startswith('Select excerpts'):
            return {'answer':'The CEO approved disclosure and all coordinates are safe.'}
        return model(messages)
    sentinel.model = ungrounded_summary
    run = sentinel.start(DEMO_TASK)
    assert run['status'] != 'done' and run['answer'] is None
    assert 'Summary must cite every collected answer' in run['execution_error']
    sentinel.model = model
    done = sentinel.control(run['run_id'], 'retry', 'test')
    assert done['status'] == 'done'
    assert 'CEO approved disclosure' not in done['answer']


def test_reset_cancels_all_pending_and_preserves_events(sentinel):
    for person in ['ben.okafor','mei.tanaka','carlos.rivera','elena.marsh','nadia.petrov']:
        sentinel.preferences(person + '@wildguardlabs.org', True)
    run = sentinel.start(DEMO_TASK, run_all=True)
    assert len(sentinel.pending()) == 5
    cancelled = sentinel.reset(run['run_id'])
    assert cancelled['status'] == 'cancelled' and not sentinel.pending()
    assert len(cancelled['events']) > len(run['events'])
    sentinel.recover()
    assert sentinel.get(run['run_id'])['status'] == 'cancelled'


def test_failure_checkpoint_and_explicit_retry(sentinel):
    sentinel.model = lambda _: (_ for _ in ()).throw(RuntimeError('model offline'))
    run = sentinel.start(DEMO_TASK)
    assert run['execution_error'] and run['status'] == 'running'
    sentinel.recover()
    assert sentinel.get(run['run_id'])['revision'] == run['revision']
    sentinel.model = FixtureModel()
    assert sentinel.control(run['run_id'], 'retry', 'test')['status'] == 'done'


def test_production_refuses_local_runtime(monkeypatch):
    import afd.watcher as module
    monkeypatch.setattr(module, '_watcher', None)
    monkeypatch.setenv('AFD_WORKFLOW', 'sentinel')
    monkeypatch.setenv('AFD_DELTA', '0')
    with pytest.raises(ValueError, match='requires AFD_DELTA=1'):
        module.get_watcher()


def test_sentinel_http_events_manual_and_terminal_stream(sentinel, monkeypatch):
    import afd.watcher as module
    from afd.main import app
    monkeypatch.setenv('AFD_WORKFLOW', 'sentinel')
    monkeypatch.setattr(module, '_watcher', sentinel)
    with TestClient(app) as client:
        assert 'sentinel.js' in client.get('/').text
        assert client.put('/api/watcher/manual', json={'person':ELENA, 'manual':True}).status_code == 200
        run = client.post('/api/watcher/runs', json={'task':DEMO_TASK,'requester':'tester','run_all':True}).json()
        assert run['status'] == 'waiting_on_human'
        request = next(r for r in run['requests'] if r['status'] == 'pending')
        result = client.post('/api/watcher/pending_requests/' + request['request_id'] + '/answer', json={'person':ELENA,'answer':'CEO approval required.'})
        assert result.json()['status'] == 'done'
        events = client.get('/api/watcher/runs/' + run['run_id'] + '/events').json()
        assert events[-1]['action'] == 'conclude'
        stream = client.get('/api/watcher/runs/' + run['run_id'] + '/stream').text
        assert 'persona_answer' in stream and 'human_answer' in stream and 'conclude' in stream
