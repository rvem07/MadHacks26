"""Existing FastAPI/App scaffolding, now exclusively Fernwood."""
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from .watcher_api import router

@asynccontextmanager
async def lifespan(app):
    from .watcher import get_watcher
    get_watcher().recover()
    yield

app=FastAPI(title='Fernwood Agent Watcher',lifespan=lifespan)
STATIC=Path(__file__).resolve().parent.parent/'static'
app.mount('/static',StaticFiles(directory=STATIC),name='static')
app.include_router(router,prefix='/api')

@app.get('/')
def index(): return FileResponse(STATIC/'index.html')

@app.get('/health')
def health(): return {'status':'ok','app':'Fernwood Agent Watcher'}
