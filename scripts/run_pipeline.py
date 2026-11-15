"""Run locally or as a Databricks Job; input transformations are identical."""
import argparse
import json
import os
import sys
from pathlib import Path

# Serverless Python tasks may exec the file without defining __file__.
# The compiled code filename still identifies the uploaded workspace script.
entrypoint = Path(globals().get('__file__') or sys._getframe().f_code.co_filename)
sys.path.insert(0, str(entrypoint.resolve().parent.parent))
from afd.pipeline import SAMPLE, SENTINEL, publish_pipeline
from afd.watchstore import open_documents


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', type=Path, default=SAMPLE)
    parser.add_argument('--sentinel', action='store_true', help='Publish WildGuard Labs data; requires Delta')
    parser.add_argument('--delta', action='store_true')
    parser.add_argument('--warehouse-id')
    parser.add_argument('--catalog', default=os.getenv('AFD_CATALOG', 'workspace'))
    parser.add_argument('--schema', default=os.getenv('AFD_WATCHER_SCHEMA', 'sentinel_gold'))
    parser.add_argument('--bootstrap', action='store_true', help='Create schema, tables, and routing views')
    parser.add_argument('--repair', action='store_true', help='Replay durable document projections without ingesting new raw rows')
    args = parser.parse_args()
    if args.sentinel:
        if not args.delta:
            parser.error('--sentinel requires --delta; no local runtime stand-ins')
        args.directory = SENTINEL
    if args.delta:
        os.environ['AFD_DELTA'] = '1'
    if args.warehouse_id:
        os.environ['AFD_WAREHOUSE_ID'] = args.warehouse_id
    os.environ['AFD_CATALOG'] = args.catalog
    os.environ['AFD_WATCHER_SCHEMA'] = args.schema
    db = open_documents()
    if args.bootstrap and db.mode == 'delta':
        db.bootstrap()
    if args.repair:
        for doc in db.list('pipeline:') + db.list('run:'):
            db.project(doc)
        print('Projections repaired. No new batch ingested.')
        return
    pipeline = publish_pipeline(db, args.directory)
    print(json.dumps({'mode': db.mode, 'batch_id': pipeline['batch_id'], **pipeline['stats']}, indent=2))


if __name__ == '__main__':
    main()
