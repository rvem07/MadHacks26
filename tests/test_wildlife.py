"""Guided demo decisions use the real Watcher checkpoint/request lifecycle."""
import pytest
from fastapi.testclient import TestClient

from afd.watcher import Watcher
from afd.watchstore import Documents, Conflict
from afd.wildlife import DEFAULT_PROMPT, SCENARIO, STAGES, classify


@pytest.fixture
def demo(tmp_path, monkeypatch):
    monkeypatch.setenv('AFD_WATCHER_AGENT', 'local')
    w = Watcher(Documents(tmp_path), background=False)
    w.publish()
    return w


def begin(demo):
    return demo.start(DEFAULT_PROMPT, 'aisha.rahman@badgernet.com', SCENARIO)


def select(demo, request, option='other', explanation=''):
    return demo.answer(request['request_id'], '', request['asked_person_email'], option, explanation)


def test_scenario_routes_to_maria_instead_of_default_requester(demo):
    run = begin(demo)
    assert run['status'] == 'waiting_on_human'
    assert run['scenario'] == SCENARIO
    assert run['route']['method'] == 'explicit scenario routing'
    assert demo.pending()[0]['asked_person_email'] == STAGES[0]['person']
    assert len(demo.pending()[0]['choices']) == 4
    assert demo.pending()[0]['choices'][-1]['id'] == 'other'


def test_complete_demo_uses_five_employees_and_actual_answers(demo):
    run = begin(demo)
    for stage in STAGES:
        req = demo.pending()[0]
        assert req['asked_person_email'] == stage['person']
        assert req['decision_id'] == stage['id']
        run = select(demo, req, stage['choices'][0][0])
    assert run['status'] == 'done'
    assert not demo.pending()
    assert len(run['scenario_decisions']) == 5
    assert len(run['requests']) == 5
    assert all(stage['choices'][0][1] in run['answer'] for stage in STAGES)
    assert 'Security foundation:' in run['answer']
    assert 'Build sequence:' in run['answer']
    assert [e['step'] for e in run['events']] == list(range(1, len(run['events']) + 1))
    # Preserve actual graph; only Lena -> Tom is a direct reporting edge in this path.
    assert [e['edge'] for e in run['events'] if e['edge']] == ['reports:lena.ortiz@badgernet.com']


def test_vague_other_stays_with_employee_and_followup_resolves(demo):
    run = begin(demo)
    first = demo.pending()[0]
    run = select(demo, first, explanation='Not sure, whatever works')
    followup = demo.pending()[0]
    assert run['status'] == 'waiting_on_human'
    assert not run['scenario_decisions']
    assert followup['asked_person_email'] == first['asked_person_email']
    assert followup['followup']
    assert followup['question'] != first['question']
    assert run['requests'][0]['answer'] == 'Not sure, whatever works'
    assert run['requests'][0]['resolved'] is False
    run = select(demo, followup, 'rangers')
    assert demo.pending()[0]['asked_person_email'] == STAGES[1]['person']
    assert run['scenario_decisions']['users']['answer'] == 'Field rangers'


def test_concrete_custom_other_is_used_in_specification(demo):
    begin(demo)
    run = select(demo, demo.pending()[0], explanation='Field rangers and local veterinarians')
    assert run['scenario_decisions']['users']['answer'] == 'Field rangers and local veterinarians'
    for stage in STAGES[1:]:
        run = select(demo, demo.pending()[0], stage['choices'][0][0])
    assert 'Field rangers and local veterinarians' in run['answer']


def test_invalid_or_empty_choices_do_not_answer_request(demo):
    begin(demo)
    req = demo.pending()[0]
    for option, explanation in [('invented',''), ('other',' '), (None,'')]:
        with pytest.raises(ValueError):
            select(demo, req, option, explanation)
        assert demo.pending()[0]['request_id'] == req['request_id']
    select(demo, req, 'rangers')
    with pytest.raises(Conflict):
        select(demo, req, 'researchers')


@pytest.mark.parametrize('stage,concrete,vague', [
    (0, 'Rangers and local veterinarians', 'Whatever works'),
    (1, 'Reports from ranger mobile phones', 'We should collect sightings'),
    (2, 'Only assigned reserve rangers', 'Not sure who should see locations'),
    (3, 'Radio call with a dispatcher acknowledgement', 'Please send an alert'),
    (4, 'One reserve with a trained ranger team', 'Start a pilot later'),
])
def test_each_stage_requires_its_missing_decision(stage, concrete, vague):
    assert classify(STAGES[stage], 'other', concrete)['resolved']
    assert not classify(STAGES[stage], 'other', vague)['resolved']


def test_refresh_recovery_preserves_followup_and_graph(demo):
    original = demo.pipeline()
    run = begin(demo)
    select(demo, demo.pending()[0], explanation='Not sure')
    pending = demo.pending()[0]
    restarted = Watcher(Documents(demo.db.directory.parent), background=False)
    restarted.recover()
    assert restarted.pending()[0] == pending
    assert restarted.get(run['run_id'])['status'] == 'waiting_on_human'
    assert restarted.pipeline()['nodes'] == original['nodes']
    assert restarted.pipeline()['edges'] == original['edges']


def test_scenario_needs_no_hosted_model(demo):
    def forbidden(_):
        raise AssertionError('Guided demo should run without a hosted model')
    demo.model = forbidden
    begin(demo)
    for stage in STAGES:
        run = select(demo, demo.pending()[0], stage['choices'][0][0])
    assert run['status'] == 'done'


def test_demo_api_accepts_structured_choices_and_preserves_text_api(demo, monkeypatch):
    import afd.watcher as module
    from afd.main import app
    monkeypatch.setattr(module, '_watcher', demo)
    monkeypatch.setenv('AFD_WORKFLOW', 'watcher')
    with TestClient(app) as client:
        run = client.post('/api/watcher/runs', json={'task':'build an application for our startup',
            'requester':'aisha.rahman@badgernet.com', 'scenario':SCENARIO}).json()
        req = run['requests'][0]
        payload = {'person':req['asked_person_email'], 'option_id':'other', 'explanation':'Not sure'}
        response = client.post(f'/api/watcher/pending_requests/{req["request_id"]}/answer', json=payload)
        assert response.status_code == 200
        assert response.json()['requests'][-1]['followup']
        unknown = client.post('/api/watcher/runs', json={'task':'test','requester':'someone','scenario':'unknown'})
        assert unknown.status_code == 400
