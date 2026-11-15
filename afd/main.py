"""Existing FastAPI/App scaffolding, now exclusively Fernwood."""
from contextlib import asynccontextmanager
from pathlib import Path
import logging
import threading
import time
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from .watcher_api import router

@asynccontextmanager
async def lifespan(app):
    from .watcher import get_watcher
    watcher=get_watcher()
    stopped=threading.Event()
    app.state.recovery={'status':'restoring'}
    def restore():
        for attempt in range(3):
            try:
                watcher.recover()
                app.state.recovery={'status':'complete'}
                return
            except Exception as e:
                logging.getLogger(__name__).warning('Run recovery attempt failed: %s',type(e).__name__)
                if attempt==2:
                    app.state.recovery={'status':'failed','retryable':True}
                    return
                if stopped.wait(2**attempt):return
    threading.Thread(target=restore,name='run-recovery',daemon=True).start()
    try:
        yield
    finally:
        stopped.set()

app=FastAPI(title='Fernwood Agent Watcher',lifespan=lifespan)
STATIC=Path(__file__).resolve().parent.parent/'static'
app.mount('/static',StaticFiles(directory=STATIC),name='static')
app.include_router(router,prefix='/api')

@app.get('/')
def index(): return FileResponse(STATIC/'index.html')

@app.get('/health')
def health(): return {'status':'ok','app':'Fernwood Agent Watcher','recovery':getattr(app.state,'recovery',{'status':'not started'})}

@app.middleware('http')
async def request_timing(request,call_next):
    started=time.monotonic()
    response=await call_next(request)
    elapsed=(time.monotonic()-started)*1000
    response.headers['Server-Timing']=f'app;dur={elapsed:.1f}'
    logging.getLogger(__name__).info('request route=%s status=%s duration_ms=%.1f',
        getattr(request.scope.get('route'),'path','static'),response.status_code,elapsed)
    return response
