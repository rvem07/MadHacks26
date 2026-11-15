"""Small Fernwood API; graph/data/events are physical Delta reads."""
from fastapi import APIRouter,HTTPException,Query
from pydantic import BaseModel,Field
from .watcher import get_watcher,DEFAULT_TASK
from .preflight import check_connection

router=APIRouter()
class StartRequest(BaseModel):
    task:str=Field(default=DEFAULT_TASK,min_length=1,max_length=4000)
    dry_run:bool=False
    fresh:bool=False

@router.get('/graph')
def graph(batch_id:str|None=None): return get_watcher().db.graph(batch_id)

@router.get('/data/{table}')
def data(table:str):
    db=get_watcher().db
    try: return {'rows':db.pipeline_rows(table,db.get('active_pipeline')['batch_id'])}
    except ValueError as e: raise HTTPException(400,str(e))

@router.post('/runs')
def start(body:StartRequest): return get_watcher().start(body.task,body.dry_run,body.fresh)

@router.get('/runs')
def runs():
    return [{'run_id':r['run_id'],'status':r['status'],'created_at':r['created_at'],'task':r['task'],'batch_id':r['batch_id']}
            for r in sorted(get_watcher().db.list('run:'),key=lambda r:r['created_at'],reverse=True)]

@router.get('/runs/{rid}/events')
def events(rid:str,after:int=Query(default=0,ge=0)): return get_watcher().db.events(rid,after)

@router.post('/runs/{rid}/retry')
def retry(rid:str):
    w=get_watcher();doc=w.get(rid)
    if doc['status']!='error': raise HTTPException(409,'Only failed runs can be retried')
    doc['status']='running';w.commit(doc);w.schedule(rid)
    return {'run_id':rid}

@router.get('/setup')
def setup():
    import os
    return {'git_folder_path':os.getenv('GIT_FOLDER_PATH',''),'repo_url':os.getenv('GITHUB_REPO_URL',''),
      'git_path':('github_api' if os.getenv('GITHUB_TOKEN') else 'github_api_pending_token'),'search_provider':'Tavily',
      'search_configured':bool(os.getenv('TAVILY_API_KEY')),'git_token_configured':bool(os.getenv('GITHUB_TOKEN')),
      'dwell_seconds':get_watcher().dwell,'branch_policy':'agent-run-<timestamp>; main is not enabled',
      'storage':'Unity Catalog Delta','schema':get_watcher().db.fq}

@router.post('/setup/test')
def test_connection(): return check_connection(write_probe=False)

@router.post('/setup/preflight')
def preflight(): return check_connection(write_probe=True)

@router.post('/runs/{rid}/publish')
def publish_saved(rid:str):
    w=get_watcher()
    with w.lock:
        doc=w.get(rid)
        if doc['status']=='running' or not doc.get('artifacts'):
            raise HTTPException(409,'Wait for generated files before publishing')
        if doc.get('commits') and doc['status']=='done':
            return {'run_id':rid}
        doc['dry_run']=False
        doc['status']='running'
        w.commit(doc)
        w.schedule(rid)
    return {'run_id':rid}


class CsvRequest(BaseModel):
    filename:str=Field(min_length=1,max_length=120)
    csv_text:str=Field(min_length=1,max_length=1048576)


@router.post('/ingestions')
def ingest_csv(body:CsvRequest):
    try:return get_watcher().ingestor.start(body.csv_text,body.filename)
    except ValueError as e:raise HTTPException(400,str(e))


@router.get('/ingestions/{job_id}')
def ingestion_status(job_id:str):
    ingestor=get_watcher().ingestor
    try:return ingestor.summary(ingestor.get(job_id))
    except KeyError:raise HTTPException(404,'Ingestion not found')


@router.get('/samples/team.csv')
def sample_csv():
    from pathlib import Path
    from fastapi.responses import FileResponse
    return FileResponse(Path(__file__).resolve().parent.parent/'samples'/'team.csv',media_type='text/csv',filename='team.csv')
