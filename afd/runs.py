"""Run manager: the lifecycle of one agent run, and everything after it.

start()      pin the current ontology + config versions, start the host in a
             thread; the host's tool calls stream events into the store.
emit()       assign seq, narrate milestones, persist; the SSE endpoint tails it.
finish       compute automatic flags; if any, ask the reviewer for proposals.
mark_wrong() a teammate says what the right outcome was; we localize the first
             divergent/weak step, save a test case, and request proposals.
accept/edit/reject proposals -> applied through the versioned services.
rerun()      same task, same requester, *current* versions; compare() aligns
             two runs step by step for the side-by-side view.
"""
from __future__ import annotations

import logging
import threading
import uuid
from contextlib import nullcontext
from typing import Any

from . import audit
from .agentcfg import ConfigService
from .channels import HumanChannel
from .config import Settings, get_settings
from .events import make_event
from .narrator import narrate
from .ontology import OntologyService
from .repo import get_repo
from .store import LocalStore
from .tools import RunContext, ToolWrapper

log = logging.getLogger(__name__)


class RunManager:
    def __init__(self, store: LocalStore, settings: Settings | None = None):
        self.store = store
        self.settings = settings or get_settings()
        self.onto = OntologyService(store)
        self.cfg = ConfigService(store)
        self.channel = HumanChannel(store, gmail_mode=self.settings.gmail_mode,
                                    auto_reply_s=self.settings.auto_reply_s)
        self._seq_locks: dict[str, threading.Lock] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._contexts: dict[str, RunContext] = {}
        # Wrappers of runs currently driven by a remote (Agent Bricks) agent,
        # so its tool callbacks land in the right run.
        self._wrappers: dict[str, ToolWrapper] = {}

    def register_wrapper(self, run_id: str, wrapper: ToolWrapper) -> None:
        self._wrappers[run_id] = wrapper

    def unregister_wrapper(self, run_id: str) -> None:
        self._wrappers.pop(run_id, None)

    def wrapper_for(self, run_id: str) -> ToolWrapper | None:
        return self._wrappers.get(run_id)

    # ------------------------------------------------------------------ runs
    def start(self, task: str, requester: str, *, rerun_of: str | None = None,
              config_version: int | None = None, ontology_version: int | None = None,
              host: str | None = None, sync: bool = False, config_override: dict | None = None) -> dict:
        """Create a run and execute it (in a background thread unless sync)."""
        run = {
            "run_id": "r_" + uuid.uuid4().hex[:8], "task": task, "requester": requester,
            "status": "running", "host": host or self.settings.agent_host,
            "config_version": config_version or self.cfg.current_version(),
            "ontology_version": ontology_version or self.onto.current_version(),
            "rerun_of": rerun_of, "created_at": audit.now_iso(), "updated_at": audit.now_iso(),
            "ended_at": None, "answer": None, "claims": [], "outcome": None, "stop_reason": None,
            "marked_wrong": False, "expected_outcome": None, "mlflow_trace_id": None, "branch": None,
        }
        self.store.insert("runs", run)
        self._seq_locks[run["run_id"]] = threading.Lock()
        if sync:
            self._execute(run, config_override)
        else:
            t = threading.Thread(target=self._execute, args=(run, config_override), daemon=True,
                                 name=f"run-{run['run_id']}")
            self._threads[run["run_id"]] = t
            t.start()
        return self.get(run["run_id"])

    def _execute(self, run: dict, config_override: dict | None) -> None:
        graph = self.onto.graph(run["ontology_version"])
        config = config_override or self.cfg.get(run["config_version"])["config"]
        ctx = RunContext(run=run, graph=graph, config=config, repo=get_repo(self.store), channel=self.channel,
                         emit=lambda **kw: self.emit(run, graph, **kw), today=self.settings.today(),
                         gmail_mode=self.settings.gmail_mode, genie=self._genie_fn(),
                         edge_notes=self._edge_notes(run["ontology_version"]))
        self._contexts[run["run_id"]] = ctx
        wrapper = ToolWrapper(ctx)
        policy = None
        with _mlflow_span(f"agent run {run['run_id']}", self.settings) as span:
            if span is not None:
                run["mlflow_trace_id"] = getattr(span, "trace_id", None) or getattr(span, "request_id", None)
            try:
                if run["host"] == "agentbricks":
                    from .agentbricks import run_with_agentbricks

                    run_with_agentbricks(run, ctx, wrapper, self.settings)
                else:
                    from .policy import ScriptedPolicy

                    policy = ScriptedPolicy(ctx, wrapper)
                    policy.run()
                status = "completed" if ctx.concluded and not ctx.stop_reason else "stopped"
            except Exception as e:
                log.exception("run %s crashed", run["run_id"])
                ctx.stop_reason = f"host error: {type(e).__name__}: {e}"
                status = "error"
        conclusion = next((e for e in reversed(self.events(run["run_id"])) if e["action_type"] == "conclude"), None)
        outcome = policy.outcome if policy else _outcome_from_events(self.events(run["run_id"]))
        if outcome == "needs_human":
            status = "needs_human"
        self.store.update("runs", "run_id", run["run_id"], {
            "status": status, "ended_at": audit.now_iso(), "updated_at": audit.now_iso(),
            "answer": conclusion["result"].get("answer") if conclusion else None,
            "claims": conclusion["result"].get("claims", []) if conclusion else [],
            "outcome": outcome, "stop_reason": ctx.stop_reason, "branch": ctx.branch,
            "mlflow_trace_id": run.get("mlflow_trace_id"),
        })
        # Post-mortem: automatic flags on every run; proposals only when flagged.
        from .flags import compute_flags

        flags = compute_flags(self.get(run["run_id"]), self.events(run["run_id"]), graph, config, self.settings.today())
        for f in flags:
            self.store.insert("flags", f)
        if flags:
            self.review(run["run_id"], trigger="flags")

    def emit(self, run: dict, graph, **fields) -> dict:
        """Persist one event: assign seq, narrate if it is a milestone."""
        lock = self._seq_locks.setdefault(run["run_id"], threading.Lock())
        with lock:
            seq = run.get("_last_seq", 0) + 1
            run["_last_seq"] = seq
            ev = make_event(run=run, seq=seq, **fields)
            if ev["milestone"]:
                ev["plain_summary"], ev["narrator"] = narrate(
                    ev, graph, self.settings.narrator_mode, self.settings.narrator_endpoint,
                    self.settings.narrator_timeout_s)
            with _mlflow_span(f"{ev['stage']}:{ev['tool'] or ev['action_type']}", self.settings, attributes={
                    "event_id": ev["event_id"], "status": ev["status"]}):
                pass  # one span per semantic event, nested under the run span
            self.store.insert("events", ev)
        return ev

    # --------------------------------------------------------------- queries
    def get(self, run_id: str) -> dict | None:
        r = self.store.get("runs", run_id=run_id)
        return {k: v for k, v in r.items() if not k.startswith("_")} if r else None

    def list(self) -> list[dict]:
        return sorted(self.store.all("runs"), key=lambda r: r["created_at"], reverse=True)

    def events(self, run_id: str, after_seq: int = 0) -> list[dict]:
        return sorted([e for e in self.store.find("events", run_id=run_id) if e["seq"] > after_seq],
                      key=lambda e: e["seq"])

    def wait(self, run_id: str, timeout: float = 30) -> dict:
        """Block until a background run finishes (tests, benchmark)."""
        t = self._threads.get(run_id)
        if t:
            t.join(timeout)
        return self.get(run_id)

    def flags(self, run_id: str) -> list[dict]:
        return self.store.find("flags", run_id=run_id)

    def proposals(self, run_id: str | None = None) -> list[dict]:
        return self.store.find("proposals", run_id=run_id) if run_id else self.store.all("proposals")

    # ------------------------------------------------------ human in the loop
    def reply(self, question_id: str, text: str, author: str) -> dict:
        return self.channel.reply(question_id, text, answered_by=author)

    # ---------------------------------------------------------- post-mortem
    def mark_wrong(self, run_id: str, expected_outcome: str, author: str) -> dict:
        """A teammate marks a run wrong and says what should have happened."""
        from .flags import localize_divergence

        run = self.get(run_id)
        events = self.events(run_id)
        graph = self.onto.graph(run["ontology_version"])
        where = localize_divergence(run, events, self.flags(run_id), graph, expected_outcome)
        self.store.update("runs", "run_id", run_id, {"marked_wrong": True, "expected_outcome": expected_outcome,
                                                     "divergence": where, "updated_at": audit.now_iso()})
        tc = {"test_case_id": "tc_" + uuid.uuid4().hex[:8], "run_id": run_id, "task": run["task"],
              "requester": run["requester"], "expected_outcome": expected_outcome, "marked_by": author,
              "divergence": where, "created_at": audit.now_iso()}
        self.store.insert("test_cases", tc)
        audit.record(self.store, author, "run.mark_wrong", f"run@{run_id}", None,
                     {"expected_outcome": expected_outcome, "divergence": where})
        self.review(run_id, trigger="marked_wrong", expected_outcome=expected_outcome)
        return {"run": self.get(run_id), "divergence": where, "test_case": tc}

    def review(self, run_id: str, trigger: str, expected_outcome: str | None = None) -> list[dict]:
        """Ask the reviewer for proposals (only on flagged or marked runs)."""
        from .reviewer import propose

        run = self.get(run_id)
        # Proposals are diffs against the *current* ontology/config (that is what
        # accepting applies to), even when reviewing an older run.
        existing = {(p["target"], p["signature"]) for p in self.proposals()
                    if p["status"] == "pending" or p["run_id"] == run_id}
        props = propose(run, self.events(run_id), self.flags(run_id), self.onto.graph(),
                        self.cfg.get()["config"], self.settings, trigger=trigger, expected_outcome=expected_outcome)
        out = []
        for p in props:
            if (p["target"], p["signature"]) in existing:
                continue  # don't duplicate a proposal for the same fix
            self.store.insert("proposals", p)
            out.append(p)
        return out

    def decide_proposal(self, proposal_id: str, decision: str, author: str, edited_ops: list | None = None) -> dict:
        """accept | reject. Accept may carry edited ops (the "edit" path)."""
        p = self.store.get("proposals", proposal_id=proposal_id)
        if not p:
            raise KeyError(proposal_id)
        if p["status"] != "pending":
            raise ValueError(f"proposal is already {p['status']}")
        fields: dict[str, Any] = {"decided_by": author, "updated_at": audit.now_iso()}
        if decision == "reject":
            fields["status"] = "rejected"
        elif decision == "accept":
            ops = edited_ops if edited_ops is not None else p["ops"]
            note = f"accepted proposal {proposal_id}" + (" (edited)" if edited_ops is not None else "")
            if p["target"] == "ontology":
                row = self.onto.apply_ops(ops, author, note)
                fields["applied_version"] = {"ontology": row["version"]}
            else:
                row = self.cfg.apply_ops(ops, author, note)
                fields["applied_version"] = {"config": row["version"]}
            fields["status"] = "accepted_edited" if edited_ops is not None else "accepted"
            fields["applied_ops"] = ops
        else:
            raise ValueError("decision must be accept or reject")
        audit.record(self.store, author, f"proposal.{decision}", f"proposal@{proposal_id}", p.get("before"),
                     fields.get("applied_ops"), p.get("title", ""))
        return self.store.update("proposals", "proposal_id", proposal_id, fields)

    def rerun(self, run_id: str, author: str, sync: bool = False) -> dict:
        """Same task again, against the *current* ontology and config."""
        old = self.get(run_id)
        audit.record(self.store, author, "run.rerun", f"run@{run_id}")
        return self.start(old["task"], old["requester"], rerun_of=run_id, host=old["host"], sync=sync)

    def compare(self, a: str, b: str) -> dict:
        """Side-by-side milestone timelines of two runs, aligned by stage."""
        def view(rid: str) -> dict:
            run = self.get(rid)
            ms = [{"seq": e["seq"], "stage": e["stage"], "summary": e["plain_summary"], "status": e["status"],
                   "warnings": e["warnings"], "focus_node": e["focus_node"]}
                  for e in self.events(rid) if e["milestone"]]
            return {"run": run, "milestones": ms, "steps": len(self.events(rid)),
                    "people_asked": [e["input"].get("person_id") for e in self.events(rid)
                                     if e["tool"] == "ask_human" and e["status"] == "waiting"],
                    "flags": [f["type"] for f in self.flags(rid)]}
        return {"a": view(a), "b": view(b)}

    # --------------------------------------------------------------- helpers
    def _edge_notes(self, version: int) -> dict:
        """Warnings for edges whose endpoint was corrected in an earlier version.

        If an edge's `src` changed between versions and the old value was stale,
        the live view warns about it before the agent relies on the corrected edge.
        """
        from .flags import DEFAULT_STALE_DAYS
        from .ontology import Graph

        notes: dict = {}
        versions = [v for v in self.onto.versions() if v["version"] <= version]
        if len(versions) < 2:
            return notes
        current = self.onto.graph(version)
        today = self.settings.today()
        # Walk consecutive pairs (older, newer) from the most recent backwards;
        # the first pair where an edge's src changed is where it was corrected.
        for older_meta, changed_in in reversed(list(zip(versions, versions[1:]))):
            old, newer = self.onto.graph(older_meta["version"]), self.onto.graph(changed_in["version"])
            for eid, e in current.edges.items():
                prev, nxt = old.edge(eid), newer.edge(eid)
                if eid in notes or not prev or not nxt or prev["src"] == nxt["src"] or nxt["src"] != e["src"]:
                    continue
                age = Graph.edge_age_days(prev, today)
                stale = " (stale)" if age > DEFAULT_STALE_DAYS else ""
                notes[eid] = (f"Recently corrected: this edge said '{old.describe_edge(prev)}'{stale}, last verified "
                              f"{prev['last_verified']}; re-pointed to {current.label(e['src'])} in "
                              f"v{changed_in['version']} by {changed_in['author']}.")
        return notes

    def _genie_fn(self):
        if not self.settings.genie_space_id:
            return None
        from .genie import ask_genie

        return lambda q: ask_genie(self.settings.genie_space_id, q)


def _outcome_from_events(events: list[dict]) -> str:
    """Outcome for non-scripted hosts: who was last asked and answered."""
    asked = [e for e in events if e["tool"] == "ask_human" and e["action_type"] == "observe"]
    if asked:
        return f"asked:{asked[-1]['input'].get('person_id')}"
    if any(e["status"] == "timeout" for e in events):
        return "needs_human"
    return "escalated" if any(e["action_type"] == "conclude" for e in events) else "unknown"


def _mlflow_span(name: str, settings: Settings, attributes: dict | None = None):
    """MLflow Tracing span if mlflow is importable and enabled; else a no-op."""
    import os

    if os.environ.get("AFD_MLFLOW", "0") not in ("1", "true"):
        return nullcontext(None)
    try:
        import mlflow

        return mlflow.start_span(name=name, attributes=attributes or {})
    except Exception:
        return nullcontext(None)


_manager: RunManager | None = None


def get_manager() -> RunManager:
    global _manager
    if _manager is None:
        from .store import get_store

        _manager = RunManager(get_store())
    return _manager


def reset_manager(store: LocalStore, settings: Settings | None = None) -> RunManager:
    global _manager
    _manager = RunManager(store, settings)
    return _manager
