"""Watcher HTTP API, using the existing FastAPI app and SSE transport."""
import asyncio
import json

from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import StreamingResponse
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .watcher import get_watcher
from .watchstore import Conflict

router = APIRouter(prefix='/api/watcher')


class StartRequest(BaseModel):
    task: str = Field(min_length=1, max_length=12000)
    requester: str = Field(min_length=1)
    scenario: str | None = None
    run_all: bool = False


class ManualRequest(BaseModel):
    person: str
    manual: bool


class AnswerRequest(BaseModel):
    answer: str = Field(default='', max_length=20000)
    option_id: str | None = None
    explanation: str = Field(default='', max_length=20000)
    person: str = Field(min_length=1)


class ControlRequest(BaseModel):
    actor: str = 'demo user'



def call(fn, *args):
    try:
        return fn(*args)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Conflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get('/meta')
def meta():
    w = get_watcher()
    pipeline = call(w.pipeline)
    return {'mode': w.db.mode, 'agent': 'databricks' if w.model else 'local (deterministic offline adapter)',
            'people': pipeline['nodes'], 'batch_id': pipeline['batch_id']}


@router.get('/pipeline')
def pipeline(batch: str | None = None):
    w = get_watcher()
    p = call(w.pipeline, batch)
    return dict({k: p[k] for k in ('batch_id', 'created_at', 'nodes', 'edges', 'stats', 'quarantine', 'silver_responsibilities')}, storage_mode=w.db.mode)


@router.get('/runs')
def runs():
    return get_watcher().runs()


@router.post('/runs')
def start(body: StartRequest):
    w = get_watcher()
    if hasattr(w, 'preferences'):
        return call(w.start, body.task, body.requester, body.scenario, body.run_all)
    return call(w.start, body.task, body.requester, body.scenario)


@router.get('/manual')
def manual_preferences():
    return call(get_watcher().preferences)


@router.put('/manual')
def manual_preference(body: ManualRequest):
    return call(get_watcher().preferences, body.person, body.manual)


@router.post('/persona')
def persona(body: AnswerRequest):
    return {'answer': call(get_watcher().persona, body.person, body.answer)}


@router.get('/runs/{run_id}/events')
def run_events(run_id: str):
    w = get_watcher()
    doc = call(w.get, run_id)
    return w.db.events(run_id) if w.db.mode == 'delta' else doc['events']


@router.get('/runs/{run_id}')
def get(run_id: str):
    return call(get_watcher().get, run_id)


@router.get('/runs/{run_id}/plan')
def build_plan(run_id: str):
    return call(get_watcher().build_plan, run_id)


@router.get('/runs/{run_id}/plan.md')
def download_plan(run_id: str):
    plan = call(get_watcher().build_plan, run_id)
    return Response(plan['markdown'], media_type='text/markdown',
                    headers={'Content-Disposition':'attachment; filename="plan.md"'})


@router.get('/pending_requests')
def pending(person: str | None = None):
    return get_watcher().pending(person)


@router.post('/pending_requests/{request_id}/answer')
def answer(request_id: str, body: AnswerRequest):
    return call(get_watcher().answer, request_id, body.answer, body.person, body.option_id, body.explanation)


@router.post('/runs/{run_id}/{action}')
def control(run_id: str, action: str, body: ControlRequest = Body(default=ControlRequest())):
    if action == 'reset' and hasattr(get_watcher(), 'reset'):
        return call(get_watcher().reset, run_id)
    return call(get_watcher().control, run_id, action, body.actor)


@router.get('/runs/{run_id}/stream')
def stream(run_id: str, after: int = Query(default=0, ge=0)):
    watcher = get_watcher()
    call(watcher.get, run_id)

    async def updates():
        if hasattr(watcher, 'subscribe'):
            queue = asyncio.Queue()
            subscription = watcher.subscribe(run_id, asyncio.get_running_loop(), queue)
            last = after
            try:
                while True:
                    doc = await asyncio.to_thread(watcher.get, run_id)
                    events = await asyncio.to_thread(watcher.db.events, run_id) if watcher.db.mode == 'delta' else doc['events']
                    doc['events'] = events
                    for event in events:
                        if event['step'] > last:
                            yield f'event: ev\ndata: {json.dumps(event)}\n\n'
                            last = event['step']
                    yield f'event: state\ndata: {json.dumps(doc)}\n\n'
                    if doc['status'] in {'done', 'cancelled'}:
                        return
                    await queue.get()  # Commit notification; no polling or timer.
            finally:
                watcher.unsubscribe(subscription)
            return
        last = after
        revision = None
        while True:
            # UI observation only. Human waiting never schedules agent work.
            doc = await asyncio.to_thread(watcher.get, run_id)
            if doc['revision'] != revision:
                for event in doc['events']:
                    if event['step'] > last:
                        yield f'event: ev\ndata: {json.dumps(event)}\n\n'
                        last = event['step']
                yield f'event: state\ndata: {json.dumps(doc)}\n\n'
                revision = doc['revision']
            else:
                yield ': keepalive\n\n'
            if doc['status'] in {'done', 'cancelled'}:
                return
            await asyncio.sleep(1 if watcher.db.mode == 'local' else 3)

    return StreamingResponse(updates(), media_type='text/event-stream',
                             headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})
