"""Automatic post-mortem flags: rules over the trace and the ontology.

Spec section 11. Every flag is labeled *inferred* (our system's judgment),
points at the exact event (seq) it is about, and names the edges/nodes
involved so the UI can highlight them on the graph.

Flag types
  stale_edge_used      a decision/question/action relied on an edge older than the staleness limit
  low_confidence_edge  ... on an edge below the confidence floor
  loop                 the same call repeated, or the hop budget ran out
  dead_end             a traversal or lookup came back empty
  unsupported_claim    a claim with no evidence_refs
  blocked_action       the wrapper refused an action
  human_timeout        a question went unanswered past the timeout
"""
from __future__ import annotations

import json
import uuid
from datetime import date

from .agentcfg import active_checks
from .audit import now_iso
from .ontology import Graph

DEFAULT_STALE_DAYS = 180
DEFAULT_MIN_CONFIDENCE = 0.7


def _limits(config: dict) -> tuple[int, float]:
    stale, conf = DEFAULT_STALE_DAYS, DEFAULT_MIN_CONFIDENCE
    for s in config["stages"]:
        for c in active_checks(s, "stale_owner"):
            stale = min(stale, int(c.get("max_age_days", stale)))
        for c in active_checks(s, "min_confidence"):
            conf = max(conf, float(c.get("min_confidence", conf)))
    return stale, conf


def _relied_edges(ev: dict) -> list[str]:
    """Edges an event *relied on* (not merely looked at).

    For a decision, the first edge is the one behind the chosen option; extra
    edges are context (e.g. the stale edge it decided *against*).
    """
    if ev["action_type"] == "decide" and ev["tool"] == "record_decision":
        return ev["edge_ids"][:1]
    if ev["action_type"] in ("ask_human", "act"):
        return ev["edge_ids"]
    return []


def compute_flags(run: dict, events: list[dict], graph: Graph, config: dict, today: date) -> list[dict]:
    stale_days, min_conf = _limits(config)
    flags: list[dict] = []
    seen: set = set()

    def add(ev: dict, ftype: str, severity: str, title: str, detail: str, node_ids=(), edge_ids=()):
        key = (ftype, tuple(edge_ids) or ev["seq"])
        if key in seen:
            return
        seen.add(key)
        flags.append({"flag_id": "f_" + uuid.uuid4().hex[:8], "run_id": run["run_id"], "seq": ev["seq"],
                      "event_id": ev["event_id"], "type": ftype, "severity": severity, "title": title,
                      "detail": detail, "node_ids": list(node_ids), "edge_ids": list(edge_ids),
                      "fidelity": "inferred", "created_at": now_iso()})

    call_counts: dict[str, list[dict]] = {}
    for ev in events:
        # stale / low-confidence edges that something relied on
        for eid in _relied_edges(ev):
            e = graph.edge(eid)
            if not e:
                continue
            age = Graph.edge_age_days(e, today)
            if age > stale_days:
                add(ev, "stale_edge_used", "high", f"Relied on a stale edge: {graph.describe_edge(e)}",
                    f"Edge {eid} was last verified {e['last_verified']} ({age} days before {today}), "
                    f"source '{e['source']}'. The limit is {stale_days} days.",
                    [e["src"], e["dst"]], [eid])
            if e["confidence"] < min_conf:
                add(ev, "low_confidence_edge", "medium", f"Relied on a low-confidence edge: {graph.describe_edge(e)}",
                    f"Edge {eid} has confidence {e['confidence']} (floor {min_conf}).", [e["src"], e["dst"]], [eid])
        # repeated identical calls -> loop
        if ev["tool"] and ev["action_type"] not in ("decide", "blocked"):
            sig = ev["tool"] + json.dumps(ev["input"], sort_keys=True)
            call_counts.setdefault(sig, []).append(ev)
            if len(call_counts[sig]) == 3:
                add(ev, "loop", "high", f"Loop: {ev['tool']} called 3 times with the same input",
                    f"Same call at seq {[x['seq'] for x in call_counts[sig]]}.", ev["node_ids"][:1])
        # empty traversals / lookups
        if ev["tool"] == "ontology_traverse" and ev["status"] == "ok" and ev["result"].get("count", 0) == 0:
            types = ev["input"].get("edge_types")
            add(ev, "dead_end", "medium" if set(types or []) & {"owns", "responsible_for"} else "low",
                f"Dead end at {graph.label(ev['input'].get('node_id', ''))}",
                f"No {'/'.join(types or ['any'])} edges ({ev['input'].get('direction', 'both')}) from "
                f"{ev['input'].get('node_id')}.", [ev["input"].get("node_id", "")])
        if ev["tool"] == "ontology_lookup" and ev["status"] == "ok" and not ev["result"].get("nodes"):
            add(ev, "dead_end", "medium", "Lookup found nothing", f"No node matched {ev['input'].get('query')!r}.")
        # claims without evidence
        if ev["action_type"] == "conclude":
            for c in ev["result"].get("claims", []):
                if not c.get("evidence_refs"):
                    add(ev, "unsupported_claim", "high", "Claim with no supporting evidence", c.get("text", ""))
        if ev["tool"] == "cite_evidence" and not ev["evidence_refs"]:
            add(ev, "unsupported_claim", "medium", "Evidence citation with no references", ev["input"].get("claim", ""))
        if ev["action_type"] == "blocked":
            reason = ev["result"].get("reason", "")
            add(ev, "blocked_action", "medium" if "revisit" not in reason else "low",
                f"Blocked: {ev['tool']}", reason)
            if "revisit" in reason:
                add(ev, "loop", "medium", "Loop prevented by the no-revisit rule", reason)
        if ev["status"] == "timeout":
            add(ev, "human_timeout", "medium", f"No reply from {graph.label(ev['input'].get('person_id', ''))}",
                f"Escalation: {ev['result'].get('escalation')}.", [ev["input"].get("person_id", "")])
    if run.get("outcome") == "budget_exhausted":
        last = events[-1] if events else {"seq": 0, "event_id": f"{run['run_id']}:0"}
        add(last, "loop", "high", "Hop budget exhausted", "Owner resolution walked the graph without finishing.")
    return flags


def localize_divergence(run: dict, events: list[dict], flags: list[dict], graph: Graph, expected: str) -> dict:
    """Find the first step where the path diverged from the expected outcome.

    If the teammate names a person ("should have asked Marcus"), the
    divergence is the first decision that chose someone else; we also report
    whether the expected person was ever visible on the agent's path, which
    separates "chose wrong" from "the ontology never led there".
    """
    expected_l = expected.lower()
    expected_people = [n for n in graph.nodes.values() if n["type"] == "person"
                       and (n["label"].lower() in expected_l or n["label"].split()[0].lower() in expected_l)]
    if expected_people:
        want = expected_people[0]
        visible = [e["seq"] for e in events if want["id"] in e["node_ids"] and e["action_type"] != "decide"]
        for e in events:
            d = e.get("decision") or {}
            people = [n for n in e["node_ids"] if (graph.node(n) or {}).get("type") == "person"]
            if e["tool"] == "record_decision" and people and people[0] != want["id"]:
                reason = (f"Chose {d.get('chosen')} instead of {want['label']}. "
                          + (f"{want['label']} was visible at step(s) {visible} but not chosen."
                             if visible else f"{want['label']} never appeared on the agent's path, so the "
                                            f"ontology did not lead there (edge {e['edge_ids'][:1]} was trusted)."))
                return {"seq": e["seq"], "event_id": e["event_id"], "reason": reason,
                        "expected_person": want["id"], "edge_ids": e["edge_ids"][:1]}
    if flags:
        f = min(flags, key=lambda f: f["seq"])
        return {"seq": f["seq"], "event_id": f["event_id"], "reason": f"First flagged step: {f['title']}",
                "expected_person": None, "edge_ids": f["edge_ids"]}
    conclude = next((e for e in reversed(events) if e["action_type"] == "conclude"), events[-1] if events else None)
    if conclude is None:
        return {"seq": 0, "event_id": None, "reason": "run has no events", "expected_person": None, "edge_ids": []}
    return {"seq": conclude["seq"], "event_id": conclude["event_id"],
            "reason": "No weak step found; the conclusion itself disagrees with the expected outcome.",
            "expected_person": None, "edge_ids": []}
