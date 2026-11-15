"""Stage 7: the Agent Bricks host routes the agent's tool calls through the wrapper.

The real agent (agent_bricks/) POSTs each tool call to /api/agent-tools/{tool};
here a fake agent makes the same calls through call_tool_for_run(), so the
bridge, allowlists, events and the "you haven't concluded" continuation are
tested offline.
"""
import afd.agentbricks as ab
import afd.runs as runs_mod
from afd.config import get_settings
from afd.runs import reset_manager


class FakeResponse:
    def __init__(self, content):
        self._content = content

    def raise_for_status(self):
        pass

    def json(self):
        return {"output": [{"role": "assistant", "content": self._content}]}


def test_bridge_routes_calls_and_continues_once(store, monkeypatch):
    s = get_settings()
    s.auto_reply_s = 0.0
    mgr = reset_manager(store, s)
    calls = {"n": 0}

    def fake_post(url, json, headers, timeout):
        rid = json["run_id"]
        calls["n"] += 1
        call = lambda tool, **a: ab.call_tool_for_run(rid, tool, {"run_id": rid, **a})  # noqa: E731
        if calls["n"] == 1:
            call("begin_stage", stage="triage")
            assert call("edit_file", path="x.py", content="y")["status"] == "blocked"  # not allowed in triage
            call("ontology_lookup", query="invoicing backend", types=["task"])
            return FakeResponse("I looked around.")  # ends its turn without concluding
        assert "You stopped before calling conclude" in json["input"][0]["content"]
        call("begin_stage", stage="verify")
        call("conclude", answer="done", claims=[{"text": "found the task", "evidence_refs": [f"{rid}:4"]}])
        return FakeResponse("Concluded.")

    monkeypatch.setattr(ab.httpx, "post", fake_post)
    run = mgr.start("Build the backend for the invoicing feature", "p_leo", host="agentbricks", sync=True)
    assert calls["n"] == 2
    assert run["status"] == "completed" and run["answer"] == "done"
    evs = mgr.events(run["run_id"])
    assert [e["tool"] for e in evs if e["tool"]][:3] == ["begin_stage", "edit_file", "ontology_lookup"]
    assert any(e["action_type"] == "blocked" for e in evs)
    # The agent's own end-of-turn messages are recorded as events too.
    assert [e["result"]["final_message"] for e in evs if e["tool"] is None] == ["I looked around.", "Concluded."]
    assert mgr.wrapper_for(run["run_id"]) is None  # unregistered when done
    runs_mod._manager = None


def test_failed_invocation_is_recorded_and_continued(store, monkeypatch):
    """A mid-turn failure (e.g. a model 429) becomes an error event, then the run continues."""
    import httpx

    mgr = reset_manager(store, get_settings())
    calls = {"n": 0}

    def fake_post(url, json, headers, timeout):
        rid = json["run_id"]
        calls["n"] += 1
        call = lambda tool, **a: ab.call_tool_for_run(rid, tool, {"run_id": rid, **a})  # noqa: E731
        if calls["n"] == 1:
            call("begin_stage", stage="triage")
            req = httpx.Request("POST", url)
            raise httpx.HTTPStatusError("boom", request=req, response=httpx.Response(500, request=req))
        call("begin_stage", stage="verify")
        call("conclude", answer="recovered", claims=[{"text": "ok", "evidence_refs": [f"{rid}:1"]}])
        return FakeResponse("done")

    monkeypatch.setattr(ab.httpx, "post", fake_post)
    run = mgr.start("Build the backend for the invoicing feature", "p_leo", host="agentbricks", sync=True)
    assert run["status"] == "completed" and run["answer"] == "recovered"
    errors = [e for e in mgr.events(run["run_id"]) if e["status"] == "error"]
    assert errors and "HTTP 500" in errors[0]["result"]["error"]
    runs_mod._manager = None
