"""Benchmark: does the ontology (and fixing it) make the agent more accurate?

Spec section 14 (open item, decided here): compare four conditions on
accuracy, share of claims with evidence, and steps taken, over a set of
cases with seeded failures.

Conditions
  no_ontology          ontology tools removed; the agent only has the (stale) team wiki page
  genie_only           ontology tools removed; the agent asks the Genie space who owns what
                       (skipped unless AFD_GENIE_SPACE_ID is set)
  ontology             our ontology + rules v1 (as seeded)
  ontology_after_edit  run once, accept every AI proposal from that run, run again

Seeded failures (all live in afd/seed.py so the demo graph is the benchmark graph)
  stale_owner          the demo: Priya's year-old "owns Security specs" edge
  conflicting_sources  an old confident edge vs a fresh less-confident one
  missing_edge         nothing "owns" the API guidelines; only CODEOWNERS knows
  loop                 the runbook depends on two services that depend on each other
  wrong_mapping        the frontend style guide is mapped to the backend repo area
  (control)            nothing wrong

Every case is a real run through the same ToolWrapper with the scripted
host, in its own isolated store, so the numbers are reproducible. This
measures the *system* (ontology + rules + fix loop) with a deterministic
policy; it is not a measurement of an LLM's accuracy. The wrong_mapping case
is included on purpose: the proposer cannot see that business fact, so the
"after edit" condition does not fix it (spec section 11's honest limit).
"""
from __future__ import annotations

import copy
import json
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .agentcfg import ConfigService
from .config import Settings, get_settings
from .runs import RunManager
from .seed import seed_all
from .store import LocalStore

CASES = [
    {"id": "demo_build", "question": "Build the backend for the invoicing feature", "failure": "stale_owner",
     "expected": "p_marcus", "expected_label": "ask Marcus"},
    {"id": "stale_owner", "question": "Who should I ask about the security specs?", "failure": "stale_owner",
     "expected": "p_marcus", "expected_label": "Marcus"},
    {"id": "conflicting", "question": "Who should I ask about the data retention policy?",
     "failure": "conflicting_sources", "expected": "p_priya", "expected_label": "Priya"},
    {"id": "missing_edge", "question": "Who should I ask about the API guidelines?", "failure": "missing_edge",
     "expected": "p_leo", "expected_label": "Leo"},
    {"id": "loop", "question": "Who should I ask about the on-call runbook?", "failure": "loop",
     "expected": "escalated", "expected_label": "escalate (no owner)"},
    {"id": "wrong_mapping", "question": "Who should I ask about the frontend style guide?",
     "failure": "wrong_mapping", "expected": "p_sam", "expected_label": "Sam"},
    {"id": "control_invoice", "question": "Who should I ask about the invoice requirements?", "failure": "none (control)",
     "expected": "p_dana", "expected_label": "Dana"},
    {"id": "control_auth", "question": "Who should I ask about the auth service?", "failure": "none (control)",
     "expected": "p_marcus", "expected_label": "Marcus"},
]

CONDITIONS = [
    {"id": "no_ontology", "label": "No ontology",
     "description": "Only the team wiki page (last edited 2025-06)."},
    {"id": "genie_only", "label": "Genie only",
     "description": "Asks the Genie space over the company tables; no graph walking."},
    {"id": "ontology", "label": "Our ontology",
     "description": "Graph walking with rules v1, as seeded."},
    {"id": "ontology_after_edit", "label": "Ontology after edit",
     "description": "Same case after accepting every AI-proposed fix from the first run."},
]

ONTOLOGY_TOOLS = {"ontology_lookup", "ontology_traverse"}


def _strip_ontology(cfg: dict, add: list[str]) -> dict:
    """Config variant without graph tools (plus `add` tools in investigate)."""
    cfg = copy.deepcopy(cfg)
    for s in cfg["stages"]:
        s["allowed_tools"] = [t for t in s["allowed_tools"] if t not in ONTOLOGY_TOOLS]
        if s["name"] == "investigate":
            # Keep conditions distinct: the wiki condition can't ask Genie, and
            # the Genie condition can't read the wiki.
            drop = {"genie_query"} if "genie_query" not in add else {"read_file"}
            s["allowed_tools"] = [t for t in s["allowed_tools"] if t not in drop]
            for t in add:
                if t not in s["allowed_tools"]:
                    s["allowed_tools"].append(t)
    return cfg


def _score(case: dict, mgr: RunManager, run: dict) -> dict:
    events = mgr.events(run["run_id"])
    outcome = run.get("outcome") or "unknown"
    correct = (outcome == "escalated") if case["expected"] == "escalated" else outcome == f"asked:{case['expected']}"
    claims = run.get("claims") or []
    with_ev = sum(1 for c in claims if c.get("evidence_refs"))
    label = outcome
    if outcome.startswith("asked:"):
        label = "asked " + mgr.onto.graph().label(outcome.split(":", 1)[1]).split()[0]
    return {"run_id": run["run_id"], "outcome": outcome, "outcome_label": label, "correct": correct,
            "steps": sum(1 for e in events if e["tool"] and e["tool"] != "begin_stage"),
            "claims": len(claims), "claims_with_evidence": with_ev,
            "flags": [f["type"] for f in mgr.flags(run["run_id"])]}


def _run_case(case: dict, condition: str, base: Settings, workdir: Path) -> dict:
    """One case under one condition, in a fresh isolated store."""
    d = workdir / f"{condition}-{case['id']}"
    d.mkdir(parents=True)
    store = LocalStore(d)
    seed_all(store)
    settings = copy.copy(base)
    settings.data_dir, settings.auto_reply_s = d, 0.0
    settings.agent_host = "scripted"
    mgr = RunManager(store, settings)
    cfg = ConfigService(store).get()["config"]
    if condition == "no_ontology":
        run = mgr.start(case["question"], "p_leo", sync=True, config_override=_strip_ontology(cfg, ["read_file"]))
    elif condition == "genie_only":
        if not settings.genie_space_id:
            return {"skipped": "AFD_GENIE_SPACE_ID not set"}
        run = mgr.start(case["question"], "p_leo", sync=True, config_override=_strip_ontology(cfg, ["genie_query"]))
    elif condition == "ontology":
        run = mgr.start(case["question"], "p_leo", sync=True)
    else:  # ontology_after_edit
        first = mgr.start(case["question"], "p_leo", sync=True)
        for p in mgr.proposals(first["run_id"]):
            if p["status"] == "pending":
                try:
                    mgr.decide_proposal(p["proposal_id"], "accept", author="benchmark")
                except Exception:
                    pass  # a proposal made obsolete by an earlier accept
        run = mgr.rerun(first["run_id"], author="benchmark", sync=True)
    return _score(case, mgr, mgr.get(run["run_id"]))


def run_benchmark(out_dir: Path, settings: Settings | None = None) -> dict:
    """Run every case under every condition; write out_dir/benchmark.json."""
    settings = settings or get_settings()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "benchmark.json"
    workdir = Path(tempfile.mkdtemp(prefix="afd-bench-"))
    results: dict = {c["id"]: {} for c in CONDITIONS}
    total = len(CASES) * len(CONDITIONS)
    try:
        for i, (cond, case) in enumerate((c, k) for c in CONDITIONS for k in CASES):
            path.write_text(json.dumps({"status": "running", "progress": f"{i}/{total}"}))
            results[cond["id"]][case["id"]] = _run_case(case, cond["id"], settings, workdir)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    summary = {}
    for cond in CONDITIONS:
        rows = [r for r in results[cond["id"]].values() if not r.get("skipped")]
        if not rows:
            summary[cond["id"]] = {"skipped": next(iter(results[cond["id"]].values())).get("skipped")}
            continue
        claims = sum(r["claims"] for r in rows)
        summary[cond["id"]] = {
            "accuracy": sum(r["correct"] for r in rows) / len(rows), "correct": sum(r["correct"] for r in rows),
            "total": len(rows), "evidence_share": (sum(r["claims_with_evidence"] for r in rows) / claims) if claims else 0.0,
            "avg_steps": sum(r["steps"] for r in rows) / len(rows),
        }
    out = {"status": "done", "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "note": ("Deterministic harness: every case is a real run through the tool wrapper with the scripted "
                    "host, in an isolated store. It measures the ontology + rules + fix loop, not an LLM. "
                    "'After edit' accepts every AI proposal from the first run, then reruns."),
           "conditions": CONDITIONS, "cases": CASES, "results": results, "summary": summary}
    path.write_text(json.dumps(out, indent=1))
    return out
