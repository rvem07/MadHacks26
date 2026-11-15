"""Watcher acceptance tests: real persistent transitions, no simulated timers."""
import copy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from afd.pipeline import build_pipeline
from afd.watcher import Watcher
from afd.watchstore import Conflict, DeltaDocuments, Documents

PRIYA = 'priya.sharma@badgernet.com'
TOM = 'tom.becker@badgernet.com'
MARIA = 'maria.alvarez@badgernet.com'


@pytest.fixture
def watcher(tmp_path, monkeypatch):
    monkeypatch.setenv('AFD_WATCHER_AGENT', 'local')
    w = Watcher(Documents(tmp_path), background=False)
    w.publish()
    return w


def start(w):
    run = w.start('network outage: who should handle this?', MARIA)
    assert run['status'] == 'waiting_on_human', run
    return run


def test_pipeline_expected_stats_and_provenance():
    p = build_pipeline()
    assert p['stats'] == {'raw_records': 24, 'raw_people': 16, 'raw_responsibilities': 8,
        'duplicates_merged': 2, 'violations_quarantined': 6,
        'violation_counts': {'multiple_managers': 2, 'missing_manager': 1, 'reporting_cycle': 2, 'unowned_topic': 1},
        'violation_groups': 4, 'gold_nodes': 14, 'gold_edges': 9, 'owned_topics': 7}
    assert p['bronze']['org_chart'][1]['email'] == 'Tom.Becker@badgernet.com '
    assert all({'source', 'confidence', 'updated_at'} <= row.keys() for row in p['nodes'] + p['edges'])
    invalid = {'rachel.kim@badgernet.com', 'evan.brooks@badgernet.com', 'pat.lee@badgernet.com', 'quinn.ray@badgernet.com'}
    assert invalid <= {p['id'] for p in p['nodes']}
    assert not invalid & {e['src'] for e in p['edges']}
    assert [r['person_email'] for r in p['routing_chains'] if r['topic'] == 'network outage'] == [PRIYA, TOM, MARIA]


def test_pause_and_resume_from_disk(watcher):
    run = start(watcher)
    req = watcher.pending(PRIYA)[0]
    assert req['asked_person_email'] == PRIYA
    assert run['events'][1]['action'] == 'who_should_i_ask'
    assert run['events'][-1]['target_node'] == PRIYA
    assert not watcher.workers
    reloaded = Watcher(Documents(watcher.db.directory.parent), background=False)
    reloaded.recover()
    assert reloaded.get(run['run_id'])['revision'] == run['revision']
    done = reloaded.answer(req['request_id'], 'Affected 12 customers; isolate the failed uplink and dispatch the field team.', PRIYA)
    assert done['status'] == 'done'
    assert '12 customers' in done['answer']
    assert [e['state'] for e in done['events']][-2:] == ['resumed', 'done']
    assert [e['step'] for e in done['events']] == list(range(1, len(done['events']) + 1))
    assert not reloaded.pending()


def test_reroute_nudge_cancel_and_stale_answers(watcher):
    run = start(watcher)
    original = watcher.pending()[0]
    nudged = watcher.control(run['run_id'], 'nudge', MARIA)
    assert nudged['requests'][0]['nudge_count'] == 1
    routed = watcher.control(run['run_id'], 'reroute', MARIA)
    assert routed['requests'][0]['status'] == 'superseded'
    assert routed['requests'][1]['asked_person_email'] == TOM
    assert routed['requests'][1]['previous_request_id'] == original['request_id']
    assert any(e['edge'] == f'reports:{PRIYA}' for e in routed['events'])
    with pytest.raises(Conflict):
        watcher.answer(original['request_id'], 'Too late', PRIYA)
    pending = watcher.pending()[0]
    cancelled = watcher.control(run['run_id'], 'cancel', MARIA)
    assert cancelled['status'] == 'cancelled'
    with pytest.raises(Conflict):
        watcher.answer(pending['request_id'], 'Too late', TOM)
    watcher.recover()
    assert watcher.get(run['run_id'])['status'] == 'cancelled'


def test_referral_follows_person_then_unknown_title_clarifies(watcher):
    run = start(watcher)
    q = watcher.pending()[0]
    referred = watcher.answer(q['request_id'], 'Please refer to Tom Becker for the current outage scope.', PRIYA)
    assert referred['status'] == 'waiting_on_human'
    assert watcher.pending()[0]['asked_person_email'] == TOM
    q = watcher.pending()[0]
    clarified = watcher.answer(q['request_id'], 'Refer to CEO', TOM)
    assert clarified['status'] == 'waiting_on_human'
    q = watcher.pending()[0]
    assert q['asked_person_email'] == TOM
    assert 'name or email' in q['question']
    watcher.answer(q['request_id'], 'Maria Alvarez', TOM)
    assert watcher.pending()[0]['asked_person_email'] == MARIA


def test_identity_validation_and_duplicate_answer(watcher):
    start(watcher)
    q = watcher.pending()[0]
    with pytest.raises(ValueError):
        watcher.answer(q['request_id'], 'Answer', TOM)
    with pytest.raises(ValueError):
        watcher.answer(q['request_id'], ' ', PRIYA)
    watcher.answer(q['request_id'], 'Confirmed outage; dispatch field services.', PRIYA)
    with pytest.raises(Conflict):
        watcher.answer(q['request_id'], 'Duplicate', PRIYA)


def test_no_manager_rejects_reroute_without_losing_request(watcher):
    run = watcher.start('security incident', MARIA)
    request = watcher.pending()[0]
    with pytest.raises(ValueError, match='No validated manager'):
        watcher.control(run['run_id'], 'reroute', MARIA)
    assert watcher.pending()[0]['request_id'] == request['request_id']


def test_local_cas_prevents_lost_updates(watcher):
    run = start(watcher)
    a, b = copy.deepcopy(run), copy.deepcopy(run)
    watcher.commit(a)
    with pytest.raises(Conflict):
        watcher.commit(b)


def test_concurrent_answer_and_cancel_only_one_wins(watcher):
    run = start(watcher)
    req = watcher.pending()[0]
    def answer():
        try:
            watcher.answer(req['request_id'], 'Dispatch field service.', PRIYA)
        except Conflict:
            pass
    def cancel():
        try:
            watcher.control(run['run_id'], 'cancel', MARIA)
        except Conflict:
            pass
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(lambda f: f(), [answer, cancel]))
    result = watcher.get(run['run_id'])
    assert result['status'] in {'done', 'cancelled'}
    assert not watcher.pending()
    assert len([e for e in result['events'] if e['action'] == 'conclude']) <= 1


def test_recover_ready_continuation(watcher):
    run = start(watcher)
    # Save answer without scheduling, then construct a fresh process manager.
    watcher.schedule = lambda _: None
    watcher.answer(watcher.pending()[0]['request_id'], 'Confirmed fiber cut; dispatch repair.', PRIYA)
    restarted = Watcher(Documents(watcher.db.directory.parent), background=False)
    restarted.recover()
    assert restarted.get(run['run_id'])['status'] == 'done'


def test_projection_failure_preserves_checkpoint_and_is_repairable(watcher, monkeypatch):
    run = start(watcher)
    def fail(_):
        raise RuntimeError('warehouse projection failed')
    monkeypatch.setattr(watcher.db, 'project', fail)
    with pytest.raises(RuntimeError):
        watcher.answer(watcher.pending()[0]['request_id'], 'Dispatch repair.', PRIYA)
    saved = watcher.get(run['run_id'])
    assert saved['status'] == 'resumed'
    assert saved['requests'][0]['answer'] == 'Dispatch repair.'
    monkeypatch.setattr(watcher.db, 'project', lambda _: None)
    watcher.recover()
    assert watcher.get(run['run_id'])['status'] == 'done'


def test_model_driven_questions_referral_and_conclusion(tmp_path):
    calls = []
    decisions = iter([
        {'action':'ask', 'person_email':PRIYA, 'question':'Which network segments are affected?', 'rationale':'Topic owner knows the scope.'},
        {'action':'ask', 'person_email':TOM, 'question':'Can we fail over the affected segment?', 'rationale':'Following Priya’s referral.'},
        {'action':'conclude', 'answer':'Fail over the west segment, then verify customer connectivity.', 'rationale':'Tom confirmed failover.'}])
    def model(messages):
        calls.append(messages)
        if 'Match the task' in messages[0]['content']:
            return {'topic':'network outage', 'confidence':0.95}
        return next(decisions)
    w = Watcher(Documents(tmp_path), model=model, background=False)
    w.publish()
    run = start(w)
    assert w.pending()[0]['question'] == 'Which network segments are affected?'
    w.answer(w.pending()[0]['request_id'], 'West segment. Ask Tom about failover.', PRIYA)
    assert w.pending()[0]['asked_person_email'] == TOM
    done = w.answer(w.pending()[0]['request_id'], 'Failover approved.', TOM)
    assert done['status'] == 'done'
    assert any('Failover approved.' in m['content'] for m in calls[-1])
    assert done['events'][-1]['rationale'] == 'Tom confirmed failover.'


def test_execution_error_requires_explicit_retry(tmp_path):
    def fail(_):
        raise RuntimeError('model unavailable')
    w = Watcher(Documents(tmp_path), model=fail, background=False)
    w.publish()
    run = w.start('network outage', MARIA)
    assert run['status'] == 'running' and run['execution_error']
    revision = run['revision']
    w.recover()
    assert w.get(run['run_id'])['revision'] == revision
    w.model = None
    result = w.control(run['run_id'], 'retry', MARIA)
    assert result['status'] == 'waiting_on_human'


def test_pipeline_rerun_preserves_history_and_pinned_graph(watcher):
    run = start(watcher)
    old_batch = run['batch_id']
    new = watcher.publish()
    assert new['batch_id'] != old_batch
    assert watcher.pipeline(old_batch)['batch_id'] == old_batch
    assert len(watcher.db.list('pipeline:')) == 2
    assert watcher.get(run['run_id'])['batch_id'] == old_batch


def test_http_inbox_controls_and_terminal_sse(watcher, monkeypatch):
    import afd.watcher as module
    from afd.main import app
    monkeypatch.setattr(module, '_watcher', watcher)
    monkeypatch.setenv('AFD_WORKFLOW', 'watcher')
    with TestClient(app) as client:
        run = client.post('/api/watcher/runs', json={'task':'network outage', 'requester':MARIA}).json()
        assert run['status'] == 'waiting_on_human'
        requests = client.get('/api/watcher/pending_requests', params={'person':PRIYA}).json()
        assert len(requests) == 1
        assert not client.get('/api/watcher/pending_requests', params={'person':TOM}).json()
        req = requests[0]['request_id']
        assert client.post(f'/api/watcher/pending_requests/{req}/answer', json={'person':TOM,'answer':'wrong employee'}).status_code == 400
        done = client.post(f'/api/watcher/pending_requests/{req}/answer', json={'person':PRIYA,'answer':'Dispatch repair.'}).json()
        assert done['status'] == 'done'
        assert client.post(f'/api/watcher/pending_requests/{req}/answer', json={'person':PRIYA,'answer':'duplicate'}).status_code == 409
        stream = client.get(f'/api/watcher/runs/{run["run_id"]}/stream').text
        assert 'event: state' in stream and 'human_answer' in stream and 'conclude' in stream
        assert client.get('/api/watcher/pipeline').json()['stats']['gold_nodes'] == 14


def test_watcher_escalation_has_no_waiters_or_timers():
    source = Path('afd/watcher.py').read_text()
    for forbidden in ('threading.Timer', 'threading.Event', '.wait(', 'sleep(', 'auto_reply', 'on_timeout'):
        assert forbidden not in source


def test_delta_projection_bronze_is_insert_only_and_parameterized():
    db = DeltaDocuments.__new__(DeltaDocuments)
    statements = []
    db.fq = '`workspace`.`agent_watcher`'
    db.sql = lambda sql, values=None: statements.append((sql, values))
    db._project_row('bronze_org_chart', 'test', {'name':"O'Hara", 'email':' Mixed@Example.com '}, immutable=True)
    sql, values = statements[0]
    assert 'WHEN MATCHED' not in sql
    import json
    assert "O'Hara" not in sql and json.loads(values['rows'])[0]['name'] == "O'Hara"
    assert json.loads(values['rows'])[0]['email'] == ' Mixed@Example.com '


def test_delta_bootstrap_has_physical_tables_and_routing_view():
    db = DeltaDocuments.__new__(DeltaDocuments)
    db.fq = '`workspace`.`agent_watcher`'
    statements = []
    db.sql = lambda sql, values=None: statements.append(sql)
    db.bootstrap()
    assert any('`pending_requests`' in s and 'CREATE TABLE' in s for s in statements)
    assert any('`events`' in s and 'target_node' in s for s in statements)
    assert any('topic_routing' in s and 'depth' in s for s in statements)
    assert all('COMMENT' in s for s in statements if 'CREATE TABLE' in s)


def test_unknown_topic_clarification_routes_then_asks_owner(watcher):
    run = watcher.start('contract renewal', MARIA)
    assert run['status'] == 'waiting_on_human'
    q = watcher.pending()[0]
    assert q['asked_person_email'] == MARIA and q['routing_only']
    clarified = watcher.answer(q['request_id'], 'network outage', MARIA)
    assert clarified['status'] == 'waiting_on_human'
    assert watcher.pending()[0]['asked_person_email'] == PRIYA


def test_saved_next_action_replays_without_model_call(watcher):
    run = start(watcher)
    watcher.schedule = lambda _: None
    resumed = watcher.answer(watcher.pending()[0]['request_id'], 'Dispatch repair.', PRIYA)
    resumed['next_action'] = {'action':'conclude', 'answer':'Dispatch repair and verify connectivity.'}
    watcher.commit(resumed)
    def forbidden(_):
        raise AssertionError('Saved next action must be replayed without another model decision')
    restarted = Watcher(Documents(watcher.db.directory.parent), model=forbidden, background=False)
    restarted.recover()
    assert restarted.get(run['run_id'])['answer'] == 'Dispatch repair and verify connectivity.'


def test_existing_api_routes_use_watcher(watcher, monkeypatch):
    import afd.watcher as module
    from afd.main import app
    monkeypatch.setattr(module, '_watcher', watcher)
    monkeypatch.setenv('AFD_WORKFLOW', 'watcher')
    with TestClient(app) as client:
        assert client.get('/api/meta').json()['mode'] == 'local'
        assert len(client.get('/api/ontology').json()['nodes']) == 14
        assert client.post('/api/runs', json={'task': None}).status_code == 400
        run = client.post('/api/runs', json={'task':'network outage', 'requester':MARIA}).json()
        assert client.get(f'/api/runs/{run["run_id"]}').json()['status'] == 'waiting_on_human'
        q = client.get('/api/questions').json()[0]
        assert q['to_person'] == PRIYA
        response = client.post(f'/api/questions/{q["question_id"]}/reply', json={'text':'Dispatch repair.', 'author':PRIYA})
        assert response.json()['status'] == 'done'


def test_waiting_projection_failure_is_visible_and_repair_does_not_resume(watcher, monkeypatch):
    run = start(watcher)
    original = watcher.db.project
    monkeypatch.setattr(watcher.db, 'project', lambda _: (_ for _ in ()).throw(RuntimeError('projection offline')))
    with pytest.raises(RuntimeError):
        watcher.control(run['run_id'], 'nudge', MARIA)
    saved = watcher.get(run['run_id'])
    assert saved['status'] == 'waiting_on_human'
    assert 'projection offline' in saved['projection_error']
    monkeypatch.setattr(watcher.db, 'project', original)
    repaired = watcher.control(run['run_id'], 'repair', MARIA)
    assert repaired['status'] == 'waiting_on_human'
    assert 'projection_error' not in repaired
    assert watcher.pending()[0]['request_id'] == run['requests'][0]['request_id']
