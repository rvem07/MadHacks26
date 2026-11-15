"""Narrator: one plain-language sentence per milestone event (spec section 10).

Guarantees:
  * It narrates milestones only. Which events are milestones is decided by
    the tool wrapper, so the narrator cannot invent or skip steps.
  * Input is the structured event fields only (ids resolved to labels), never
    the agent's free text (its rationale or question wording).
  * Output is one sentence tied to the event id it came from.
  * Grounding check: every name, file path and number in an LLM sentence must
    appear in the structured input. If not, or if the model is slow or down,
    the template sentence built from the fields is used, so the log never stalls.
"""
from __future__ import annotations

import json
import logging
import re

from .ontology import Graph

log = logging.getLogger(__name__)

# Capitalized words that are fine even if absent from the input.
_ALLOWED_WORDS = {
    "the", "a", "an", "agent", "i", "it", "started", "asked", "found", "followed", "decided", "opened",
    "wrote", "created", "read", "replied", "blocked", "finished", "looked", "messaged", "profiled",
    "warning", "stage", "pull", "request", "branch", "from", "to", "and", "with", "then", "while",
    "waiting", "after", "before", "searched", "checked", "cited", "noted", "chose", "moved",
}


def narrator_input(event: dict, graph: Graph) -> dict:
    """The structured fields the narrator may use (labels resolved, no free text)."""
    res = event.get("result") or {}
    inp = event.get("input") or {}
    safe_result = {k: v for k, v in res.items() if k in (
        "count", "number", "branch", "path", "rows", "columns", "to", "reason", "status", "timeout_s",
        "answered_by", "url", "stage", "escalation")}
    if "nodes" in res:
        safe_result["found"] = [n["label"] for n in res["nodes"]]
    if "neighbors" in res:
        safe_result["neighbors"] = [n["node"]["label"] for n in res["neighbors"]]
    if "claims" in res:
        safe_result["claims"] = len(res["claims"])
        safe_result["claims_with_evidence"] = sum(1 for c in res["claims"] if c.get("evidence_refs"))
    safe_input = {k: v for k, v in inp.items() if k in ("path", "name", "stage", "query", "edge_types", "direction")}
    if inp.get("node_id"):
        safe_input["node"] = graph.label(inp["node_id"])
    if inp.get("person_id"):
        safe_input["person"] = graph.label(inp["person_id"])
    d = event.get("decision") or {}
    return {
        "event_id": event["event_id"], "stage": event["stage"], "action_type": event["action_type"],
        "tool": event.get("tool"), "status": event["status"], "actor": event["actor"],
        "nodes": [graph.label(n) for n in event.get("node_ids", [])],
        "edges": [graph.describe_edge(e) for eid in event.get("edge_ids", []) if (e := graph.edge(eid))],
        "input": safe_input, "result": safe_result,
        "decision": {"chosen": d.get("chosen"), "options": d.get("options")} if d else None,
        "warnings": event.get("warnings", []),
    }


def template_sentence(event: dict, graph: Graph) -> str:
    """Deterministic sentence from fields. Always available."""
    f = narrator_input(event, graph)
    tool, status, res, inp = f["tool"], f["status"], f["result"], f["input"]
    if f["action_type"] == "blocked":
        return f"Blocked {tool}: {res.get('reason', 'not allowed')}."
    if status == "error":
        return f"The {tool} step failed."
    warn = f" Warning: {f['warnings'][0]}" if f["warnings"] else ""
    if tool == "begin_stage":
        return f"Started the {f['stage']} stage."
    if tool == "ontology_lookup":
        found = res.get("found") or []
        return (f"Looked up \"{inp.get('query', '')}\" and found {found[0]}." if found
                else f"Looked up \"{inp.get('query', '')}\" and found nothing.") + warn
    if tool == "ontology_traverse":
        n = res.get("neighbors") or []
        types = "/".join(inp.get("edge_types") or ["all"])
        if not n:
            return f"From {inp.get('node')}, followed {types} edges and reached nothing (dead end).{warn}"
        more = f" and {len(n) - 3} more" if len(n) > 3 else ""
        return f"From {inp.get('node')}, followed {types} edges to {', '.join(n[:3])}{more}.{warn}"
    if tool == "record_decision":
        d = f["decision"] or {}
        others = [o for o in (d.get("options") or []) if o != d.get("chosen")]
        return f"Decided: {d.get('chosen')}" + (f" (over {', '.join(others)})." if others else ".")
    if tool == "ask_human":
        person = inp.get("person", "a teammate")
        if f["action_type"] == "observe":
            who = str(res.get("answered_by") or "")
            if who.startswith("simulated:"):
                return f"{person} replied (simulated teammate)."
            # Flat access: anyone may answer on someone's behalf; say who typed it.
            pid = (event.get("input") or {}).get("person_id", "")
            return f"{person} replied" + (f" (typed by {graph.label(who)})." if who and who != pid else ".")
        if status == "timeout":
            return f"{person} did not reply in time; escalation: {res.get('escalation', 'stop')}."
        role = f", listed as {_role(f['edges'], person)}" if f["edges"] else ""
        return f"Asked {person}{role}, and is waiting for a reply.{warn}"
    if tool == "message_human":
        return f"Messaged {res.get('to', inp.get('person'))} (email mirror in dry-run)."
    if tool == "read_file":
        return f"Read {inp.get('path')}."
    if tool == "edit_file":
        return f"Wrote {inp.get('path')} on branch {res.get('branch')}."
    if tool == "analyze_csv":
        return f"Profiled {inp.get('path')}: {res.get('rows')} rows, {len(res.get('columns') or [])} columns."
    if tool == "github_create_branch":
        return f"Created branch {inp.get('name')}."
    if tool == "github_open_pr":
        return f"Opened pull request #{res.get('number')} from {res.get('branch')}."
    if tool == "genie_query":
        return "Asked Genie a data question."
    if tool == "web_search":
        return f"Searched the web for \"{inp.get('query', '')}\"."
    if tool == "cite_evidence":
        return "Cited evidence for a claim."
    if tool == "conclude":
        return f"Finished with {res.get('claims', 0)} claims, {res.get('claims_with_evidence', 0)} backed by evidence."
    return f"{f['action_type'].capitalize()} step ({tool})."


def _role(edges: list[str], person: str) -> str:
    """'owner of Security specs' from 'Priya Shah owns Security specs'."""
    e = edges[0]
    if e.startswith(person + " "):
        rest = e[len(person) + 1:]
        verb, _, obj = rest.partition(" ")
        nouns = {"owns": "owner of", "responsible": "responsible for", "works": "working on", "member": "member of"}
        if verb == "responsible" and obj.startswith("for "):
            obj = obj[4:]
        if verb in ("works", "member") and obj.split(" ", 1)[0] in ("on", "of"):
            obj = obj.split(" ", 1)[1]
        return f"{nouns.get(verb, verb)} {obj}"
    return f"linked by '{e}'"


def grounded(sentence: str, fields: dict) -> bool:
    """True if every name, file and number in `sentence` appears in `fields`."""
    hay = json.dumps(fields).lower()
    numbers = re.findall(r"\d+(?:\.\d+)?", sentence)
    files = re.findall(r"[\w\-]+(?:/[\w\-.]+)+|[\w\-]+\.(?:py|md|csv|json|txt|yaml|yml)\b", sentence)
    words = re.findall(r"\b[A-Z][a-zA-Z]+\b", sentence)
    names = [w for i, w in enumerate(words) if w.lower() not in _ALLOWED_WORDS]
    return all(tok.lower() in hay for tok in numbers + files + names)


def narrate(event: dict, graph: Graph, mode: str = "template", endpoint: str = "",
            timeout_s: float = 4.0) -> tuple[str, str]:
    """Return (sentence, narrator) where narrator is 'llm' or 'template'."""
    fallback = template_sentence(event, graph)
    if mode != "llm":
        return fallback, "template"
    fields = narrator_input(event, graph)
    try:
        from .llm import chat

        text = chat(endpoint, [
            {"role": "system", "content": (
                "You narrate one step of an AI agent's run for a non-technical teammate. Write ONE short "
                "past-tense sentence. Use only names, files and numbers that appear in the JSON. Output only "
                "the sentence.")},
            {"role": "user", "content": json.dumps(fields)},
        ], max_tokens=80, timeout_s=timeout_s)
        text = text.strip().split("\n")[0].strip()
        if text and len(text) < 300 and grounded(text, fields):
            return text, "llm"
        log.info("narrator output failed grounding check, using template: %r", text)
    except Exception as e:  # slow, down, or misconfigured: never stall the log
        log.info("narrator unavailable (%s), using template", type(e).__name__)
    return fallback, "template"
