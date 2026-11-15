"""FastAPI app: REST + server-sent events + the static UI.

Run locally:   uvicorn afd.main:app --reload
In Databricks: app.yaml runs the same command (see README > Deploy).

Identity: access is flat (no permissions). For attribution we record the
teammate chosen in the UI ("You are: ..."), and, when present, the
X-Forwarded-Email header that Databricks Apps adds for the signed-in user.
Acting *as* the signed-in user (user authorization) is not used.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .agentcfg import ConfigError, ConfigService, render_instructions
from .config import get_settings
from .ontology import EDGE_TYPES, NODE_TYPES, OntologyError, OntologyService
from .repo import get_repo
from .runs import get_manager
from .seed import seed_all
from .store import get_store
from .toolspecs import TOOL_SPECS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("afd")

STATIC = Path(__file__).resolve().parent.parent / "static"
app = FastAPI(title="Agent Flow Debugger")
app.mount("/static", StaticFiles(directory=STATIC), name="static")

from .watcher_api import router as watcher_router
app.include_router(watcher_router)


@app.get("/api/mode")
def workflow_mode():
    return {"workflow": os.getenv("AFD_WORKFLOW", "watcher")}


@app.on_event("startup")
def _startup() -> None:
    if os.getenv("AFD_WORKFLOW", "watcher") == "watcher":
        from .watcher import get_watcher
        w = get_watcher()
        if w.db.mode == "local" and not w.db.get("active_pipeline"):
            w.publish()
        if w.db.mode == "delta":
            w.pipeline()  # The deployment pipeline must publish before app startup.
        w.recover()
        return
    if seed_all(get_store()):
        log.info("seeded a fresh store at %s", get_settings().data_dir)


def actor(request: Request, body_author: str | None) -> str:
    """Who is making this change (attribution, not authorization)."""
    forwarded = request.headers.get("x-forwarded-email") or request.headers.get("x-forwarded-preferred-username")
    who = body_author or "anonymous"
    return f"{who} ({forwarded})" if forwarded and forwarded not in who else who


def _err(e: Exception, code: int = 400):
    raise HTTPException(status_code=code, detail=str(e))


# ----------------------------------------------------------------- pages
@app.get("/")
def index():
    page = "watcher.html" if workflow_mode()["workflow"] == "watcher" else "index.html"
    return FileResponse(STATIC / page)


@app.get("/api/meta")
def meta():
    if workflow_mode()['workflow'] == 'watcher':
        from .watcher_api import meta as watcher_meta
        return watcher_meta()
    """Everything the UI needs to boot: people, types, tools, versions, modes."""
    s = get_settings()
    onto, cfg = OntologyService(get_store()), ConfigService(get_store())
    g = onto.graph()
    return {
        "people": [{"id": n["id"], "label": n["label"], "title": n["props"].get("title")}
                   for n in g.nodes.values() if n["type"] == "person"],
        "node_types": NODE_TYPES, "edge_types": EDGE_TYPES,
        "tools": {k: {kk: v[kk] for kk in ("description", "action_type", "app", "level", "marker")}
                  for k, v in TOOL_SPECS.items()},
        "ontology_version": onto.current_version(), "config_version": cfg.current_version(),
        "modes": {"agent_host": s.agent_host, "narrator": s.narrator_mode, "reviewer": s.reviewer_mode,
                  "github": s.github_mode, "gmail": s.gmail_mode, "delta": s.delta_enabled,
                  "genie": bool(s.genie_space_id), "auto_reply_s": s.auto_reply_s},
        "today": s.today().isoformat(),
    }


# -------------------------------------------------------------- ontology
@app.get("/api/ontology")
def get_ontology(version: int | None = None):
    if workflow_mode()['workflow'] == 'watcher':
        from .watcher_api import pipeline
        return pipeline()
    try:
        return OntologyService(get_store()).snapshot(version)
    except OntologyError as e:
        _err(e, 404)


@app.get("/api/ontology/versions")
def ontology_versions():
    return OntologyService(get_store()).versions()


@app.post("/api/ontology/ops")
def ontology_ops(request: Request, body: dict = Body(...)):
    """Direct edit from the ontology editor (applies immediately, versioned)."""
    try:
        row = OntologyService(get_store()).apply_ops(body["ops"], actor(request, body.get("author")),
                                                     body.get("note", "direct edit"))
        return {"version": row["version"]}
    except (OntologyError, KeyError) as e:
        _err(e)


@app.post("/api/ontology/rollback")
def ontology_rollback(request: Request, body: dict = Body(...)):
    try:
        row = OntologyService(get_store()).rollback(int(body["version"]), actor(request, body.get("author")))
        return {"version": row["version"]}
    except OntologyError as e:
        _err(e)


# ---------------------------------------------------------------- config
@app.get("/api/config")
def get_config(version: int | None = None):
    try:
        return ConfigService(get_store()).get(version)
    except ConfigError as e:
        _err(e, 404)


@app.get("/api/config/versions")
def config_versions():
    return ConfigService(get_store()).versions()


@app.get("/api/config/instructions")
def config_instructions(version: int | None = None):
    """The Markdown instructions the Agent Bricks agent receives for this config."""
    row = ConfigService(get_store()).get(version)
    return {"version": row["version"], "markdown": render_instructions(row["config"], row["version"])}


@app.put("/api/config")
def put_config(request: Request, body: dict = Body(...)):
    """Direct edit from the rule editor (whole config, applies immediately)."""
    try:
        row = ConfigService(get_store()).replace(body["config"], actor(request, body.get("author")),
                                                 body.get("note", "direct edit"))
        _sync_supervisor()
        return {"version": row["version"]}
    except (ConfigError, KeyError) as e:
        _err(e)


@app.post("/api/config/rollback")
def config_rollback(request: Request, body: dict = Body(...)):
    try:
        row = ConfigService(get_store()).rollback(int(body["version"]), actor(request, body.get("author")))
        _sync_supervisor()
        return {"version": row["version"]}
    except ConfigError as e:
        _err(e)


def _sync_supervisor() -> None:
    """Keep the Agent Bricks Supervisor's instructions in step with the config."""
    if get_settings().agent_host != "agentbricks":
        return
    from .agentbricks import sync_instructions

    threading.Thread(target=sync_instructions, daemon=True).start()


@app.get("/api/audit")
def audit_log(limit: int = 200):
    return list(reversed(get_store().all("audit_log")))[:limit]


# ------------------------------------------------------------------ runs
@app.get("/api/runs")
def list_runs():
    if workflow_mode()['workflow'] == 'watcher':
        from .watcher_api import runs
        return runs()
    return get_manager().list()


@app.post("/api/runs")
def start_run(request: Request, body: dict = Body(...)):
    if workflow_mode()['workflow'] == 'watcher':
        from .watcher_api import start, StartRequest, call
        return start(call(StartRequest.model_validate, body))
    task = (body.get("task") or "").strip()
    if not task:
        _err(ValueError("task is required"))
    return get_manager().start(task, requester=body.get("requester") or body.get("author") or "anonymous",
                               host=body.get("host"))


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    if workflow_mode()['workflow'] == 'watcher':
        from .watcher_api import get
        return get(run_id)
    m = get_manager()
    run = m.get(run_id)
    if not run:
        _err(KeyError(run_id), 404)
    return {"run": run, "events": m.events(run_id), "flags": m.flags(run_id), "proposals": m.proposals(run_id),
            "questions": get_store().find("questions", run_id=run_id)}


@app.get("/api/runs/{run_id}/stream")
async def stream_run(run_id: str, after: int = 0):
    if workflow_mode()['workflow'] == 'watcher':
        from .watcher_api import stream
        return stream(run_id, after)
    """Server-sent events: each new event as `event: ev`, plus run state.

    The run's thread writes events into the store; this endpoint tails them.
    (Chosen over polling a Delta table or Lakebase: lowest latency, no extra
    infrastructure, and Delta still receives every event via the mirror.)
    """
    m = get_manager()
    if not m.get(run_id):
        _err(KeyError(run_id), 404)

    async def gen():
        last, idle = after, 0
        while True:
            new = m.events(run_id, after_seq=last)
            for ev in new:
                last = ev["seq"]
                yield f"event: ev\ndata: {json.dumps(ev, default=str)}\n\n"
            run = m.get(run_id)
            qs = [q for q in get_store().find("questions", run_id=run_id) if q["status"] == "open"]
            yield f"event: state\ndata: {json.dumps({'run': run, 'open_questions': qs}, default=str)}\n\n"
            if run["status"] != "running" and not new:
                idle += 1
                if idle > 2:  # flags/proposals land right after the run ends
                    payload = {"flags": m.flags(run_id), "proposals": m.proposals(run_id)}
                    yield f"event: done\ndata: {json.dumps(payload, default=str)}\n\n"
                    return
            await asyncio.sleep(0.25)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/runs/{run_id}/mark")
def mark_run(run_id: str, request: Request, body: dict = Body(...)):
    if not body.get("expected_outcome"):
        _err(ValueError("say what the right outcome was"))
    return get_manager().mark_wrong(run_id, body["expected_outcome"], actor(request, body.get("author")))


@app.post("/api/runs/{run_id}/review")
def review_run(run_id: str):
    return get_manager().review(run_id, trigger="manual")


@app.post("/api/runs/{run_id}/rerun")
def rerun(run_id: str, request: Request, body: dict = Body(default={})):
    return get_manager().rerun(run_id, actor(request, body.get("author")))


@app.get("/api/compare")
def compare(a: str, b: str):
    return get_manager().compare(a, b)


@app.get("/api/repo")
def repo_state(branch: str | None = None):
    repo = get_repo(get_store())
    out: dict[str, Any] = {"branches": repo.branches(), "prs": repo.prs()}
    if branch:
        out["files"] = {p: repo.read(p, branch) for p in repo.list_files(branch)}
        out["diff"] = repo.diff(branch)
    return out


@app.get("/api/testcases")
def test_cases():
    return get_store().all("test_cases")


# ------------------------------------------------------- human in the loop
@app.get("/api/questions")
def questions(status: str | None = "open"):
    if workflow_mode()['workflow'] == 'watcher':
        from .watcher import get_watcher
        return [dict(r, question_id=r['request_id'], to_person=r['asked_person_email'], text=r['question'])
                for run in get_watcher().runs() for r in run['requests']
                if status is None or r['status'] == ('pending' if status == 'open' else status)]
    rows = get_store().all("questions")
    return [q for q in rows if status is None or q["status"] == status]


@app.post("/api/questions/{question_id}/reply")
def reply(question_id: str, request: Request, body: dict = Body(...)):
    if workflow_mode()['workflow'] == 'watcher':
        from .watcher_api import answer, AnswerRequest, call
        payload = call(AnswerRequest.model_validate, {'answer': body.get('text', ''), 'person': body.get('author', '')})
        return answer(question_id, payload)
    try:
        return get_manager().reply(question_id, body["text"], actor(request, body.get("author")))
    except (KeyError, ValueError) as e:
        _err(e)


# -------------------------------------------------------------- proposals
@app.post("/api/proposals/{proposal_id}/{decision}")
def decide(proposal_id: str, decision: str, request: Request, body: dict = Body(default={})):
    try:
        row = get_manager().decide_proposal(proposal_id, decision, actor(request, body.get("author")),
                                            edited_ops=body.get("ops"))
        if row.get("applied_version", {}).get("config"):
            _sync_supervisor()
        return row
    except (KeyError, ValueError, OntologyError, ConfigError) as e:
        _err(e)


# -------------------------------------------------------------- benchmark
_bench_lock = threading.Lock()


@app.get("/api/benchmark")
def benchmark_results():
    path = Path(get_settings().data_dir) / "benchmark.json"
    return json.loads(path.read_text()) if path.exists() else {"status": "not run"}


@app.post("/api/benchmark/run")
def benchmark_run():
    from .benchmark import run_benchmark

    if not _bench_lock.acquire(blocking=False):
        return {"status": "already running"}

    def go():
        try:
            run_benchmark(Path(get_settings().data_dir))
        finally:
            _bench_lock.release()

    threading.Thread(target=go, daemon=True).start()
    return {"status": "started"}


# ------------------------------------------------- Agent Bricks tool bridge
@app.get("/api/agent-tools")
def agent_tools():
    """Tool catalogue for an LLM host (OpenAI-style JSON schemas)."""
    from .toolspecs import json_schema

    return [json_schema(n) for n in TOOL_SPECS]


@app.post("/api/agent-tools/{tool}")
def agent_tool_call(tool: str, body: dict = Body(...)):
    """An Agent Bricks agent's tool call, routed through the same wrapper.

    The body must carry the run_id the agent was started with; every call is
    checked against that run's stage allowlist and emitted as an event.
    """
    from .agentbricks import call_tool_for_run

    try:
        return JSONResponse(call_tool_for_run(body.get("run_id", ""), tool, body))
    except KeyError as e:
        _err(e, 404)
