"""Fix proposals: before/after diffs to the agent rules and the ontology.

Spec section 11. Triggered on flagged runs and on runs a teammate marks
wrong, not on every run. Nothing is applied automatically: a teammate
accepts (optionally after editing the ops) or rejects each proposal.

Two proposers:
  * "rules" (default): deterministic fixes that follow directly from a flag,
    e.g. stale owner edge -> re-point it to the fresh second source, and add
    the "confirm stale owners" rule.
  * "llm" (AFD_REVIEWER=llm): a stronger hosted model (default
    databricks-gpt-oss-120b) reads the trace and proposes ops in the same
    format. Its output is validated; anything invalid is dropped and the
    deterministic proposals are kept.

Honest limit (shown in the UI on every proposal): the proposer only sees the
trace and the graph. It shares the agent's blind spots and is weak at business
facts it cannot see (for example, a document mapped to the wrong repo area).

Proposal shape:
{
  "proposal_id", "run_id", "seq", "event_id", "flag_id",
  "target": "ontology" | "config", "title", "rationale",
  "ops": [...],                      # the exact ops apply_ops() will run
  "before": ..., "after": ...,       # the diff shown in the UI
  "signature": "...",                # dedupe key
  "status": "pending" | "accepted" | "accepted_edited" | "rejected",
  "proposer": "rules" | "llm", "label": "AI-inferred", "limitations": "..."
}
"""
from __future__ import annotations

import copy
import json
import logging
import uuid
from datetime import date

from .agentcfg import active_checks, stage_by_name
from .audit import now_iso
from .config import Settings
from .ontology import Graph

log = logging.getLogger(__name__)

LIMITATIONS = ("Inferred from the trace and the graph only. The proposer shares the agent's blind spots: it is "
               "best at fixes that follow from the trace and weak at business facts it cannot see. Check before "
               "accepting.")

RULE_LIBRARY = {
    "stale_owner": {
        "stage": "investigate", "id": "r_stale_owner",
        "text": "If an owner was last verified over 6 months ago, confirm with a second source before asking them.",
        "check": {"type": "stale_owner", "max_age_days": 180, "require_second_source": True},
    },
    "fallback_repo_owner": {
        "stage": "investigate", "id": "r_fallback_repo_owner",
        "text": "If nothing owns a document, ask whoever is responsible for the repo area it lives in (CODEOWNERS).",
        "check": {"type": "fallback_repo_owner"},
    },
    "no_revisit": {
        "stage": "investigate", "id": "r_no_revisit",
        "text": "Never walk into the same node twice while looking for an owner; escalate to the requester instead.",
        "check": {"type": "no_revisit"},
    },
    "require_evidence": {
        "stage": "verify", "id": "r_require_evidence",
        "text": "Every claim in the conclusion must cite evidence.",
        "check": {"type": "require_evidence"},
    },
}


def _proposal(run: dict, ev_seq: int, ev_id: str, flag_id: str | None, target: str, title: str,
              rationale: str, ops: list, before, after, signature: str, proposer: str = "rules") -> dict:
    return {"proposal_id": "pr_" + uuid.uuid4().hex[:8], "run_id": run["run_id"], "seq": ev_seq, "event_id": ev_id,
            "flag_id": flag_id, "target": target, "title": title, "rationale": rationale, "ops": ops,
            "before": before, "after": after, "signature": signature, "status": "pending",
            "proposer": proposer, "label": "AI-inferred", "limitations": LIMITATIONS,
            "created_at": now_iso(), "updated_at": now_iso()}


def _rule_proposal(run, flag, config: dict, key: str, why: str) -> dict | None:
    """Propose adding a library rule to its stage, unless already active."""
    spec = RULE_LIBRARY[key]
    try:
        stage = stage_by_name(config, spec["stage"])
    except Exception:
        return None
    if active_checks(stage, spec["check"]["type"]):
        return None
    existing = next((r for r in stage["rules"] if r["id"] == spec["id"]), None)
    rule = {"id": spec["id"], "text": spec["text"], "enabled": True, "check": spec["check"]}
    if existing:  # present but disabled -> re-enable it
        ops = [{"op": "update_rule", "stage": spec["stage"], "rule_id": spec["id"], "set": {"enabled": True}}]
    else:
        ops = [{"op": "add_rule", "stage": spec["stage"], "rule": rule}]
    before = {"stage": spec["stage"], "rules": [r["text"] for r in stage["rules"] if r.get("enabled", True)]}
    after = {"stage": spec["stage"], "rules": before["rules"] + [spec["text"]]}
    return _proposal(run, flag["seq"], flag["event_id"], flag["flag_id"], "config",
                     f"Add rule to {spec['stage']}: {spec['text']}", why, ops, before, after,
                     signature=f"rule:{key}")


def second_source(graph: Graph, node_id: str, exclude: set, today: date, max_age: int = 180) -> dict | None:
    """Fresh person responsible_for the repo area `node_id` depends on."""
    for e, area in graph.neighbors(node_id, ["depends_on"], "out"):
        if area["type"] != "repo_area":
            continue
        for e2, person in graph.neighbors(area["id"], ["responsible_for"], "in"):
            if person["type"] == "person" and person["id"] not in exclude and Graph.edge_age_days(e2, today) <= max_age:
                return {"person": person, "edge": e2, "via": area}
    return None


def rules_proposals(run, events, flags, graph: Graph, config: dict, today: date,
                    expected_outcome: str | None = None) -> list[dict]:
    out: list[dict] = []
    for f in sorted(flags, key=lambda f: f["seq"]):
        if f["type"] in ("stale_edge_used", "low_confidence_edge") and f["edge_ids"]:
            edge = graph.edge(f["edge_ids"][0])
            if edge:
                alt = second_source(graph, edge["dst"], exclude={edge["src"]}, today=today)
                if alt:
                    new = copy.deepcopy(edge)
                    new.update({"src": alt["person"]["id"], "confidence": alt["edge"]["confidence"],
                                "source": f"second source: {graph.describe_edge(alt['edge'])} ({alt['edge']['source']})",
                                "last_verified": today.isoformat()})
                    out.append(_proposal(
                        run, f["seq"], f["event_id"], f["flag_id"], "ontology",
                        f"Point '{graph.describe_edge(edge)}' to {alt['person']['label']}",
                        (f"The agent relied on '{graph.describe_edge(edge)}' (verified {edge['last_verified']}, "
                         f"source '{edge['source']}'). A fresh second source, '{graph.describe_edge(alt['edge'])}' "
                         f"({alt['edge']['source']}, verified {alt['edge']['last_verified']}), and "
                         f"{graph.label(edge['dst'])} living in {alt['via']['label']} both point to "
                         f"{alt['person']['label']}. Accepting records today's date as verified by you."),
                        [{"op": "update_edge", "id": edge["id"],
                          "set": {k: new[k] for k in ("src", "confidence", "source", "last_verified")}}],
                        edge, new, signature=f"edge:{edge['id']}->{alt['person']['id']}"))
            p = _rule_proposal(run, f, config, "stale_owner",
                               "The run relied on an owner edge older than six months without confirming it; "
                               "this rule makes the agent look for a second source first.")
            if p:
                out.append(p)
        elif f["type"] == "dead_end" and f["severity"] == "medium":
            p = _rule_proposal(run, f, config, "fallback_repo_owner",
                               "Owner lookup hit a node nothing owns. Repo-area responsibility (CODEOWNERS) is a "
                               "fresh, independent source the agent could fall back to.")
            if p:
                out.append(p)
        elif f["type"] == "loop":
            p = _rule_proposal(run, f, config, "no_revisit",
                               "The agent walked in a circle. Blocking revisits turns the loop into an early, "
                               "explicit escalation.")
            if p:
                out.append(p)
        elif f["type"] == "unsupported_claim":
            p = _rule_proposal(run, f, config, "require_evidence", "A claim reached the conclusion with no evidence.")
            if p:
                out.append(p)
        elif f["type"] == "human_timeout":
            for s in config["stages"]:
                if s["escalation"].get("on_timeout") == "stop" and s["name"] == next(
                        (e["stage"] for e in events if e["seq"] == f["seq"]), None):
                    esc = dict(s["escalation"], on_timeout="escalate_to_second_source")
                    out.append(_proposal(run, f["seq"], f["event_id"], f["flag_id"], "config",
                                         f"Escalate to a second source on timeout in {s['name']}",
                                         "A question timed out and the run stopped.",
                                         [{"op": "set_stage_field", "stage": s["name"], "field": "escalation", "value": esc}],
                                         s["escalation"], esc, signature=f"esc:{s['name']}"))
    # A teammate's marking names the right person: that is a business fact we
    # can only get from them, so turn it into an ontology fix.
    if expected_outcome:
        div = (run.get("divergence") or {})
        want, edge_ids = div.get("expected_person"), div.get("edge_ids") or []
        edge = graph.edge(edge_ids[0]) if edge_ids else None
        if want and edge and edge["src"] != want and edge["type"] in ("owns", "responsible_for"):
            new = dict(copy.deepcopy(edge), src=want, source=f"teammate correction: {expected_outcome}",
                       confidence=0.9, last_verified=today.isoformat())
            out.append(_proposal(run, div["seq"], div["event_id"], None, "ontology",
                                 f"Point '{graph.describe_edge(edge)}' to {graph.label(want)} (from your marking)",
                                 f"You marked this run wrong: \"{expected_outcome}\".",
                                 [{"op": "update_edge", "id": edge["id"],
                                   "set": {k: new[k] for k in ("src", "confidence", "source", "last_verified")}}],
                                 edge, new, signature=f"edge:{edge['id']}->{want}"))
    # Several flags can point at the same fix (a stale edge is often also low
    # confidence); keep one proposal per signature.
    unique: dict[str, dict] = {}
    for p in out:
        unique.setdefault(p["signature"], p)
    return list(unique.values())


def llm_proposals(run, events, flags, graph: Graph, config: dict, settings: Settings) -> list[dict]:
    """Ask the review model for proposals; keep only ones whose ops validate."""
    from .llm import chat

    trace = [{"seq": e["seq"], "stage": e["stage"], "tool": e["tool"], "status": e["status"],
              "summary": e["plain_summary"], "edges": e["edge_ids"], "decision": e.get("decision")}
             for e in events if e["milestone"]]
    edges = {eid: graph.edge(eid) for e in events for eid in e["edge_ids"] if graph.edge(eid)}
    prompt = {
        "task": run["task"], "trace": trace, "flags": [{k: f[k] for k in ("seq", "type", "title", "detail")} for f in flags],
        "edges": edges, "stages": [{"name": s["name"], "rules": s["rules"]} for s in config["stages"]],
        "op_formats": {
            "ontology": ["update_edge {id, set}", "add_edge {edge}", "delete_edge {id}"],
            "config": ["add_rule {stage, rule:{id,text,enabled,check}}", "update_rule {stage, rule_id, set}"],
            "check_types": ["stale_owner", "fallback_repo_owner", "no_revisit", "require_evidence", "min_confidence"],
        },
    }
    text = chat(settings.reviewer_endpoint, [
        {"role": "system", "content": (
            "You review an AI agent's run and propose minimal fixes to its rules (config) and to the company "
            "ontology. Each fix must be tied to the step (seq) that caused it. Respond with JSON only: "
            "{\"proposals\":[{\"seq\":int,\"target\":\"ontology\"|\"config\",\"title\":str,\"rationale\":str,"
            "\"ops\":[...]}]}. Do not invent people or nodes that are not in the data.")},
        {"role": "user", "content": json.dumps(prompt, default=str)},
    ], max_tokens=1500, timeout_s=60)
    start, end = text.find("{"), text.rfind("}")
    data = json.loads(text[start:end + 1])
    out = []
    for p in data.get("proposals", []):
        ops = p.get("ops") or []
        if not _ops_valid(ops, p.get("target"), graph, config):
            continue
        ev = next((e for e in events if e["seq"] == p.get("seq")), events[-1])
        before = [graph.edge(o.get("id")) for o in ops if o.get("op", "").endswith("_edge") and o.get("id")]
        out.append(_proposal(run, ev["seq"], ev["event_id"], None, p["target"], p.get("title", "proposal"),
                             p.get("rationale", ""), ops, before or None, ops,
                             signature="llm:" + json.dumps(ops, sort_keys=True)[:200], proposer="llm"))
    return out


def _ops_valid(ops: list, target: str, graph: Graph, config: dict) -> bool:
    if target not in ("ontology", "config") or not ops:
        return False
    for o in ops:
        kind = o.get("op")
        if target == "ontology":
            if kind in ("update_edge", "delete_edge") and not graph.edge(o.get("id", "")):
                return False
            if kind == "update_edge" and "src" in o.get("set", {}) and not graph.node(o["set"]["src"]):
                return False
            if kind not in ("update_edge", "add_edge", "delete_edge", "update_node"):
                return False
        else:
            if kind not in ("add_rule", "update_rule", "set_stage_field"):
                return False
            try:
                stage_by_name(config, o.get("stage", ""))
            except Exception:
                return False
    return True


def propose(run, events, flags, graph: Graph, config: dict, settings: Settings, trigger: str,
            expected_outcome: str | None = None) -> list[dict]:
    """Entry point used by the run manager."""
    today = settings.today()
    base = rules_proposals(run, events, flags, graph, config, today, expected_outcome)
    if settings.reviewer_mode != "llm":
        return base
    try:
        extra = llm_proposals(run, events, flags, graph, config, settings)
    except Exception as e:
        log.info("review model unavailable (%s); keeping deterministic proposals", type(e).__name__)
        return base
    seen = {(p["target"], json.dumps(p["ops"], sort_keys=True)) for p in base}
    return base + [p for p in extra if (p["target"], json.dumps(p["ops"], sort_keys=True)) not in seen]
