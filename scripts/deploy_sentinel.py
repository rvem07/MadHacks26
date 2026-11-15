"""Deploy the existing bundle, publish on Databricks, grant UC access, start the App.

Uses external SDK/CLI authentication only. Never writes or prints tokens.
The manifest defaults (workspace.sentinel_gold) are intentional and shared here.
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main():
    from databricks.sdk import WorkspaceClient
    from afd.watchstore import DeltaDocuments

    parser = argparse.ArgumentParser()
    parser.add_argument('--profile')
    parser.add_argument('--warehouse-id', help='Defaults to an accessible running warehouse, then an accessible serverless warehouse')
    parser.add_argument('--model-endpoint', default='databricks-qwen3-next-80b-a3b-instruct')
    parser.add_argument('--skip-pipeline', action='store_true', help='Redeploy an App against an already published schema without appending an ingestion batch')
    args = parser.parse_args()
    client = WorkspaceClient(profile=args.profile)
    client.current_user.me()  # Fail before any mutation if login has expired.
    warehouses = list(client.warehouses.list())
    warehouse = args.warehouse_id
    if not warehouse:
        candidates = sorted(warehouses, key=lambda w: (getattr(w.state, 'value', '') != 'RUNNING', not w.enable_serverless_compute, w.id))
        if not candidates:
            raise RuntimeError('No accessible SQL warehouse. Supply --warehouse-id after one is provisioned.')
        warehouse = candidates[0].id
    client.serving_endpoints.get(args.model_endpoint)
    flags = ['--var', 'warehouse_id=' + warehouse, '--var', 'model_endpoint=' + args.model_endpoint]
    if args.profile:
        flags += ['--profile', args.profile]

    def bundle(*command):
        subprocess.run(['databricks', 'bundle', *command, *flags], cwd=ROOT, check=True)

    bundle('validate')
    bundle('deploy', '--auto-approve')
    if not args.skip_pipeline:
        bundle('run', 'watcher_pipeline')  # Actual ingestion runs in the Databricks Job.
    app = client.apps.get('agent-watcher')
    principal = app.service_principal_client_id
    if not principal or not re.fullmatch(r'[a-zA-Z0-9-]+', principal):
        raise RuntimeError('App service principal client ID is unavailable')
    db = DeltaDocuments(warehouse, 'workspace', 'sentinel_gold', client=client)
    db.sql(f'GRANT USE CATALOG ON CATALOG workspace TO `{principal}`')
    db.sql(f'GRANT USE SCHEMA ON SCHEMA workspace.sentinel_gold TO `{principal}`')
    db.sql(f'GRANT SELECT, MODIFY ON SCHEMA workspace.sentinel_gold TO `{principal}`')
    bundle('run', 'watcher')
    app = client.apps.get('agent-watcher')
    print(json.dumps({'app_url': app.url, 'warehouse_id': warehouse,
                      'schema': 'workspace.sentinel_gold', 'model_endpoint': args.model_endpoint}, indent=2))


if __name__ == '__main__':
    main()
