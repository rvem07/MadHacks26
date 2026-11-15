"""Read-only live physical-table and input-volume verification."""
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from afd.watchstore import DeltaDocuments
from databricks.sdk import WorkspaceClient
w=WorkspaceClient();db=DeltaDocuments('1bc852920e5efadc','workspace','fernwood',w)
g=db.graph();s=g['stats'];expected={'raw_people_rows':12,'unique_people':10,'fuzzy_duplicates_merged':1,'exact_duplicates_collapsed':1,'relationships_quarantined':2,'raw_collab_rows':8,'valid_collab_edges':8}
assert all(s[k]==v for k,v in expected.items()),s
physical=db.sql('SELECT '+','.join(expected)+' FROM workspace.fernwood.pipeline_stats WHERE batch_id=:batch',{'batch':g['batch_id']})
assert list(map(int,physical[0]))==list(expected.values())
assert int(db.sql("SELECT count(*) FROM workspace.fernwood.nodes WHERE batch_id=:batch AND type='person'",{'batch':g['batch_id']})[0][0])==10
assert len(g['nodes'])==10 and {p['type'] for p in g['nodes']}=={'person'}
assert {p['name'] for p in g['nodes'] if p['needs_review']}=={'Nina Alvarez','Jordan Kim'}
q=db.pipeline_rows('quarantine',g['batch_id']);assert len(q)==2 and all(x['reason'] for x in q)
for f in ['org_chart.csv','collaborations.csv','person_context.csv']:
 actual=w.files.download('/Volumes/workspace/fernwood/inputs/'+f).contents.read()
 assert actual==(Path(__file__).resolve().parent.parent/'data'/f).read_bytes()
for t in ['nodes','edges','pipeline_stats','quarantine','events','persona_cache','run_context','research']:
 metadata=w.tables.get('workspace.fernwood.'+t);assert metadata.data_source_format.value=='DELTA'
 assert metadata.comment and all(c.comment for c in metadata.columns)
report={'checks':{'physical_counts':'passed','quarantine_reasons':'passed','volume_exact_bytes':'passed','delta_registration_and_comments':'passed'},'batch_id':g['batch_id'],'stats':expected,'catalog_ui':'not yet visually verified'}
Path('docs/delta-verification.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))
