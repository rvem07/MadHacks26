"""Watcher HTTP API, using the existing FastAPI app and SSE transport."""
import asyncio
import json

from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .watcher import get_watcher
from .watchstore import Conflict

router = APIRouter(prefix='/api/watcher')


class StartRequest(BaseModel):
    task: str = Field(min_length=1, max_length=12000)
    requester: str = Field(min_length=1)
    scenario: str | None = None


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
    p = call(get_watcher().pipeline, batch)
    return {k: p[k] for k in ('batch_id', 'created_at', 'nodes', 'edges', 'stats', 'quarantine', 'silver_responsibilities')}


@router.get('/runs')
def runs():
    return get_watcher().runs()


@router.post('/runs')
def start(body: StartRequest):
    return call(get_watcher().start, body.task, body.requester, body.scenario)


@router.get('/runs/{run_id}')
def get(run_id: str):
    return call(get_watcher().get, run_id)


@router.get('/pending_requests')
def pending(person: str | None = None):
    return get_watcher().pending(person)


@router.post('/pending_requests/{request_id}/answer')
def answer(request_id: str, body: AnswerRequest):
    return call(get_watcher().answer, request_id, body.answer, body.person, body.option_id, body.explanation)


@router.post('/runs/{run_id}/{action}')
def control(run_id: str, action: str, body: ControlRequest = Body(default=ControlRequest())):
    return call(get_watcher().control, run_id, action, body.actor)


@router.get('/runs/{run_id}/stream')
def stream(run_id: str, after: int = Query(default=0, ge=0)):
    watcher = get_watcher()
    call(watcher.get, run_id)

    async def updates():
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
