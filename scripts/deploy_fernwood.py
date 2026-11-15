"""Deploy existing bundle, run Delta pipeline, grant App access, deploy UI."""
import argparse
import subprocess
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from databricks.sdk import WorkspaceClient
from afd.watchstore import DeltaDocuments

p=argparse.ArgumentParser();p.add_argument('--warehouse-id',required=True);p.add_argument('--skip-pipeline',action='store_true')
a=p.parse_args();w=WorkspaceClient()
args=['--var','warehouse_id='+a.warehouse_id]
subprocess.run(['databricks','bundle','deploy','--auto-approve',*args],check=True)
if not a.skip_pipeline:subprocess.run(['databricks','bundle','run','watcher_pipeline',*args],check=True)
app=w.apps.get('agent-watcher');principal=app.service_principal_client_id
import re
if not re.fullmatch('[a-zA-Z0-9-]+',principal):raise ValueError('Missing App identity')
db=DeltaDocuments(a.warehouse_id,'workspace','fernwood',w)
db.sql(f'GRANT USE CATALOG ON CATALOG workspace TO `{principal}`')
db.sql(f'GRANT USE SCHEMA ON SCHEMA workspace.fernwood TO `{principal}`')
db.sql(f'GRANT SELECT, MODIFY ON SCHEMA workspace.fernwood TO `{principal}`')
subprocess.run(['databricks','bundle','run','watcher',*args],check=True)
print(app.url)
