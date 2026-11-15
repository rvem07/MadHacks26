"""Real served-model generation preflight; fixture source context, no runtime substitutions."""
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from afd.pipeline import build_pipeline
from afd.watcher import model_json
from afd.build import validate_files,test_files,CONTRACT,REFERENCE,FILES
p=build_pipeline(Path(__file__).resolve().parent.parent/'data')
spec={'requirements':[{'requirement':n['context_notes'],'source_person_email':n['id']} for n in p['nodes']],
      'open_questions':['Who approves 6-10 days?','Which holidays and current balances should be configured?']}
failure=json.loads(Path('data/model-build-test.json').read_text())['result']['output'] if Path('data/model-build-test.json').exists() else None
for i in range(3):
    try:
        candidate=model_json('build',{'task':'Build an internal time-off request tracker for our team.','spec':spec,'research':[],'previous_failure':failure,'required_contract_tests':CONTRACT,'reference_files':{p:(REFERENCE/p).read_text() for p in FILES}},max_tokens=9500).get('files')
        Path('data/model-build-candidate.json').write_text(json.dumps(candidate,indent=2))
        files=validate_files(candidate)
        result=test_files(files)
    except Exception as e:
        result={'status':'failed','output':type(e).__name__+': '+str(e)}
    print(json.dumps({'attempt':i+1,**result}),flush=True)
    if result['status']=='passed':
        Path('data/model-build-test.json').write_text(json.dumps({'files':files,'result':result},indent=2));break
    failure=result['output']
else: raise SystemExit(1)
