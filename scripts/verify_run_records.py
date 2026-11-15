"""Verify live events/context/cache/artifacts, not a fixture run."""
import hashlib
import json
import re
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from afd.watchstore import DeltaDocuments
from afd.build import validate_files
from afd.security import PATTERN
from databricks.sdk import WorkspaceClient
rid='0ab8ce02f6c54278b192d9c34438aba8'
db=DeltaDocuments('1bc852920e5efadc','workspace','fernwood',WorkspaceClient());run=db.get('run:'+rid)
g=db.graph(run['batch_id']);people={p['id']:p for p in g['nodes']};events=db.events(rid)
assert events==run['events'] and run['status']=='done'
assert len(run['run_context'])==9 and len({r['person_email'] for r in run['run_context']})==9
for row in run['run_context']:
 notes=people[row['person_email']]['context_notes']
 if row['answer']=="I don't know from my context.":
  assert row['question'] in run['spec']['open_questions']
  fragments=[]
 else: fragments=re.split(r';\s*|(?<=[.!?])\s+',row['answer'])
 for fragment in fragments:
  assert fragment.rstrip('.!?') in notes,fragment
 digest=hashlib.sha256((row['question']+'\0'+notes).encode()).hexdigest()
 assert db.get('cache:'+row['person_email']+':'+digest)['answer']==row['answer']
assert all(e['duration_ms']>=3000 for e in events if e['type']=='answered')
assert not any(e['type']=='committed' for e in events)
assert not PATTERN.search(json.dumps(run))
files={r['path']:r['content'] for r in run['artifacts']};readme=files.pop('README.md');validate_files(files)
assert all(r['source_person_email'] in readme for r in run['spec']['requirements'])
assert sum(len(v.splitlines()) for v in files.values())+len(readme.splitlines())<400
assert run['test_result']['status']=='passed'
report={'run_id':rid,'physical_events_equal_checkpoint':'passed','distinct_personas':9,'minimum_dwell_ms':min(e['duration_ms'] for e in events if e['type']=='answered'),
        'grounded_answers':'passed','unknowns_preserved':sum("don't know" in r['answer'] for r in run['run_context']),'cached_answers_reused':sum(r['cached'] for r in run['run_context']),'cache_equality':'passed',
        'generated_files':len(files)+1,'total_lines_including_readme':sum(len(v.splitlines()) for v in files.values())+len(readme.splitlines()),
        'readme_attributions':'passed','generated_tests':run['test_result']['status'],'dry_run_no_commits':'passed','credential_format_scan':'passed'}
Path('docs/run-record-verification.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
# Keep a local inspection copy of actual artifacts only in ignored data/.
Path('data/live-generated.json').write_text(json.dumps({'files':dict(files,**{'README.md':readme})},indent=2))
