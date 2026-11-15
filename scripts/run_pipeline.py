"""Run locally or as a Databricks Job; input transformations are identical."""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from afd.pipeline import SAMPLE, publish_pipeline
from afd.watchstore import open_documents


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', type=Path, default=SAMPLE)
    parser.add_argument('--delta', action='store_true')
    parser.add_argument('--warehouse-id')
    parser.add_argument('--catalog', default=os.getenv('AFD_CATALOG', 'workspace'))
    parser.add_argument('--schema', default=os.getenv('AFD_WATCHER_SCHEMA', 'agent_watcher'))
    parser.add_argument('--bootstrap', action='store_true', help='Create schema, tables, and routing views')
    parser.add_argument('--repair', action='store_true', help='Replay durable document projections without ingesting new raw rows')
    args = parser.parse_args()
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
