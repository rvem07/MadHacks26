"""Stages 2-3: the observed agent, the post-mortem and the fix loop.

These walk the spec's decided demo (section 13) end to end on the scripted
host, through the real tool wrapper, flags and reviewer.
"""
import time

import pytest

from afd.agentcfg import ConfigService
from afd.config import get_settings
from afd.narrator import grounded, narrator_input, template_sentence
from afd.ontology import OntologyService
from afd.runs import RunManager

TASK = "Build the backend for the invoicing feature"


@pytest.fixture
def mgr(store):
    s = get_settings()
    s.auto_reply_s = 0.0  # simulated teammates answer instantly
    return RunManager(store, s)


def asked(m, run_id):
    return [e["input"]["person_id"] for e in m.events(run_id)
            if e["tool"] == "ask_human" and e["status"] == "waiting"]  # the moment of asking


def test_demo_first_run_asks_priya_and_opens_pr(mgr):
    run = mgr.start(TASK, requester="p_leo", sync=True)
    assert run["status"] == "completed", run
    assert asked(mgr, run["run_id"]) == ["p_priya"]
    evs = mgr.events(run["run_id"])
    log = [e["plain_summary"] for e in evs if e["milestone"]]
    assert "Asked Priya Shah, listed as owner of Security specs, and is waiting for a reply." in log
    assert any(e["tool"] == "github_open_pr" and e["status"] == "ok" for e in evs)
    # The agent built on Priya's (old-policy) answer.
    sec = [e for e in evs if e["tool"] == "edit_file" and e["input"]["path"].endswith("security.py")][0]
    assert 'TOKEN_ALGORITHM = "HS256"' in sec["input"]["content"]
    # Every event carries the pinned versions and fidelity labels.
    assert all(e["config_version"] == 1 and e["ontology_version"] == 1 for e in evs)
    assert all(e["fidelity"]["result"] in ("observed", "reported") for e in evs)
    # The human reply is its own event, attributed to the human.
    reply = [e for e in evs if e["actor"] == "human:p_priya"]
    assert reply and reply[0]["action_type"] == "observe"


def test_post_mortem_flags_stale_edge_and_proposes_two_fixes(mgr):
    run = mgr.start(TASK, requester="p_leo", sync=True)
    flags = mgr.flags(run["run_id"])
    stale = [f for f in flags if f["type"] == "stale_edge_used"]
    assert stale and stale[0]["edge_ids"] == ["e_priya_owns_secspec"]
    props = mgr.proposals(run["run_id"])
    targets = sorted(p["target"] for p in props)
    assert targets == ["config", "ontology"]
    onto_p = next(p for p in props if p["target"] == "ontology")
    assert onto_p["ops"][0]["set"]["src"] == "p_marcus"
    assert onto_p["label"] == "AI-inferred" and onto_p["status"] == "pending"
    cfg_p = next(p for p in props if p["target"] == "config")
    assert cfg_p["ops"][0]["rule"]["check"]["type"] == "stale_owner"
    # Nothing was applied automatically.
    assert OntologyService(mgr.store).current_version() == 1
    assert ConfigService(mgr.store).current_version() == 1


def test_accept_both_then_rerun_goes_to_marcus_with_warning(mgr):
    run = mgr.start(TASK, requester="p_leo", sync=True)
    for p in mgr.proposals(run["run_id"]):
        mgr.decide_proposal(p["proposal_id"], "accept", author="p_dana")
    rerun = mgr.rerun(run["run_id"], author="p_dana", sync=True)
    assert rerun["rerun_of"] == run["run_id"]
    assert rerun["ontology_version"] == 2 and rerun["config_version"] == 2
    assert asked(mgr, rerun["run_id"]) == ["p_marcus"]
    warnings = [w for e in mgr.events(rerun["run_id"]) for w in e["warnings"]]
    assert any("Recently corrected" in w and "Priya" in w for w in warnings)
    sec = [e for e in mgr.events(rerun["run_id"]) if e["tool"] == "edit_file" and "security" in e["input"]["path"]][0]
    assert 'TOKEN_ALGORITHM = "RS256"' in sec["input"]["content"]
    assert not [f for f in mgr.flags(rerun["run_id"]) if f["type"] == "stale_edge_used"]
    cmp = mgr.compare(run["run_id"], rerun["run_id"])
    assert cmp["a"]["people_asked"] == ["p_priya"] and cmp["b"]["people_asked"] == ["p_marcus"]
    # Audit log attributes the accepted changes.
    actions = [a["action"] for a in mgr.store.all("audit_log") if a["actor"] == "p_dana"]
    assert actions.count("proposal.accept") == 2


def test_rule_only_rerun_uses_second_source_and_warns(mgr):
    run = mgr.start(TASK, requester="p_leo", sync=True)
    cfg_p = next(p for p in mgr.proposals(run["run_id"]) if p["target"] == "config")
    mgr.decide_proposal(cfg_p["proposal_id"], "accept", author="p_sam")
    rerun = mgr.rerun(run["run_id"], author="p_sam", sync=True)
    assert asked(mgr, rerun["run_id"]) == ["p_marcus"]
    warnings = [w for e in mgr.events(rerun["run_id"]) for w in e["warnings"]]
    assert any(w.startswith("Stale owner") for w in warnings)


def test_edit_then_accept_applies_edited_ops(mgr):
    run = mgr.start(TASK, requester="p_leo", sync=True)
    p = next(p for p in mgr.proposals(run["run_id"]) if p["target"] == "ontology")
    ops = p["ops"]
    ops[0]["set"]["confidence"] = 0.95
    out = mgr.decide_proposal(p["proposal_id"], "accept", author="p_ana", edited_ops=ops)
    assert out["status"] == "accepted_edited"
    assert OntologyService(mgr.store).graph().edge("e_priya_owns_secspec")["confidence"] == 0.95
    with pytest.raises(ValueError):
        mgr.decide_proposal(p["proposal_id"], "reject", author="p_ana")


def test_mark_wrong_localizes_divergence_and_saves_test_case(mgr):
    run = mgr.start(TASK, requester="p_leo", sync=True)
    res = mgr.mark_wrong(run["run_id"], "It should have asked Marcus, who owns security now.", author="p_dana")
    div = res["divergence"]
    assert div["expected_person"] == "p_marcus"
    ev = next(e for e in mgr.events(run["run_id"]) if e["seq"] == div["seq"])
    assert ev["tool"] == "record_decision" and ev["decision"]["chosen"] == "Priya Shah"
    assert "never appeared" in div["reason"]
    assert mgr.store.all("test_cases")[0]["expected_outcome"].startswith("It should have asked Marcus")


def test_blocked_actions_are_events(store):
    from afd.repo import Repo
    from afd.tools import RunContext, ToolWrapper
    from afd.channels import HumanChannel

    events = []
    cfg = ConfigService(store).get()["config"]
    run = {"run_id": "r_test", "config_version": 1, "ontology_version": 1, "task": "t"}
    ctx = RunContext(run=run, graph=OntologyService(store).graph(), config=cfg, repo=Repo(store),
                     channel=HumanChannel(store), today=get_settings().today(),
                     emit=lambda **kw: events.append(kw) or {"event_id": f"r_test:{len(events)}", **kw})
    w = ToolWrapper(ctx)
    assert w.call("ontology_lookup", {"query": "x"})["status"] == "blocked"        # no stage yet
    w.call("begin_stage", {"stage": "triage"})
    assert w.call("edit_file", {"path": "a", "content": "b"})["status"] == "blocked"  # not allowed in triage
    w.call("begin_stage", {"stage": "act"})
    assert w.call("github_create_branch", {"name": "main"})["status"] == "blocked"   # wrong prefix
    assert w.call("edit_file", {"path": "a.py", "content": "b", "branch": "main"})["status"] == "blocked"
    assert w.call("ontology_lookup", {"query": "x"})["status"] == "blocked"         # not in act's tools
    blocked = [e for e in events if e["action_type"] == "blocked"]
    assert len(blocked) == 5 and all(e["milestone"] for e in blocked)
    assert "protected branch main" in blocked[3]["result"]["reason"]


def test_timeout_escalates_to_second_source_then_needs_human(store):
    s = get_settings()
    s.auto_reply_s = -1.0  # nobody answers
    cfg = ConfigService(store)
    c = cfg.get()["config"]
    c["stages"][1]["escalation"]["ask_timeout_s"] = 0.2
    cfg.replace(c, author="p_leo")
    m = RunManager(store, s)
    run = m.start(TASK, requester="p_leo", sync=True)
    assert asked(m, run["run_id"]) == ["p_priya", "p_marcus"]
    assert run["status"] == "needs_human"
    assert any(f["type"] == "human_timeout" for f in m.flags(run["run_id"]))


def test_in_app_reply_unblocks_the_agent(store):
    s = get_settings()
    s.auto_reply_s = -1.0
    m = RunManager(store, s)
    run = m.start(TASK, requester="p_leo")
    for _ in range(100):
        if m.channel.open_questions():
            break
        time.sleep(0.05)
    q = m.channel.open_questions()[0]
    assert q["to_person"] == "p_priya" and q["email"]["mode"] == "dry_run"
    assert m.get(run["run_id"])["status"] == "running"  # paused, waiting
    m.reply(q["question_id"], "Use RS256 via the auth service, 90 days rotation.", author="p_marcus")
    done = m.wait(run["run_id"], timeout=10)
    assert done["status"] == "completed"
    reply_ev = [e for e in m.events(run["run_id"]) if e["actor"] == "human:p_priya"][0]
    assert reply_ev["result"]["answered_by"] == "p_marcus"  # attribution: who actually typed it


def test_narrator_grounding(store):
    g = OntologyService(store).graph()
    ev = {"event_id": "r:1", "stage": "investigate", "action_type": "ask_human", "tool": "ask_human",
          "status": "waiting", "actor": "agent", "node_ids": ["p_priya", "doc_security_specs"],
          "edge_ids": ["e_priya_owns_secspec"], "input": {"person_id": "p_priya", "question": "secret text"},
          "result": {"question_id": "q1"}, "decision": None, "warnings": []}
    fields = narrator_input(ev, g)
    assert "secret text" not in str(fields)  # the agent's free text never reaches the narrator
    assert grounded("Asked Priya Shah about the Security specs.", fields)
    assert not grounded("Asked Marcus Reed about the Security specs.", fields)  # invented name
    assert not grounded("Asked Priya Shah 3 times.", fields)                    # invented number
    assert template_sentence(ev, g) == "Asked Priya Shah, listed as owner of Security specs, and is waiting for a reply."
