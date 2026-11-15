"""Databricks Job: upload exact inputs to a UC volume, then publish immutable Delta batch."""
import argparse
import io
import json
import os
import sys
import tempfile
from pathlib import Path
entrypoint=Path(globals().get('__file__') or sys._getframe().f_code.co_filename)
sys.path.insert(0,str(entrypoint.resolve().parent.parent))
from afd.pipeline import INPUTS,publish_pipeline
from afd.watchstore import open_documents

def main():
    from databricks.sdk.service.catalog import VolumeType
    p=argparse.ArgumentParser()
    p.add_argument('--warehouse-id',required=True)
    p.add_argument('--catalog',default='workspace')
    p.add_argument('--schema',default='fernwood')
    args=p.parse_args()
    os.environ.update(AFD_WAREHOUSE_ID=args.warehouse_id,AFD_CATALOG=args.catalog,AFD_WATCHER_SCHEMA=args.schema)
    db=open_documents(); db.bootstrap()
    volume=f'/Volumes/{args.catalog}/{args.schema}/inputs'
    try: db.client.volumes.read(f'{args.catalog}.{args.schema}.inputs')
    except Exception as e:
        if getattr(e,'error_code','') not in ['RESOURCE_DOES_NOT_EXIST','NOT_FOUND']: raise
        db.client.volumes.create(args.catalog,args.schema,'inputs',VolumeType.MANAGED,comment='Exact Fernwood source CSVs')
    source=entrypoint.resolve().parent.parent/'data'
    with tempfile.TemporaryDirectory() as temp:
        for name in INPUTS:
            filename=name+'.csv'
            original=(source/filename).read_bytes()
            db.client.files.upload(volume+'/'+filename,io.BytesIO(original),overwrite=True)
            # Pipeline reads back the uploaded volume, not a runtime CSV substitute.
            downloaded=db.client.files.download(volume+'/'+filename).contents.read()
            if downloaded!=original: raise ValueError('Volume upload mismatch')
            (Path(temp)/filename).write_bytes(downloaded)
        result=publish_pipeline(db,temp,volume)
    print(json.dumps({'batch_id':result['batch_id'],**result['stats']},indent=2))

if __name__=='__main__': main()
