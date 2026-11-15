"""Generate real source, run real isolated logic tests, then hand off to a configured writer."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from .audit import now_iso

REFERENCE=Path(__file__).resolve().parent.parent/'reference_app'
FILES={'package.json','index.html','src/types.ts','src/logic.ts','src/main.ts','src/style.css','tests/logic.test.ts'}
GROUPS=[('Scaffold and attributed requirements',['package.json','index.html','README.md']),
        ('Define request data model',['src/types.ts']),('Implement time-off rules',['src/logic.ts']),
        ('Build request and calendar UI',['src/main.ts','src/style.css']),('Test time-off calculations',['tests/logic.test.ts'])]

CONTRACT="""import {test} from 'node:test';
import assert from 'node:assert/strict';
import {businessDays,carryover,accruedPto,noticeValid,salesBlackout} from './src/logic.ts';
test('weekends and explicit holidays',()=>{assert.equal(businessDays('2026-11-13','2026-11-16',[]),2);assert.equal(businessDays('2026-11-13','2026-11-16',['2026-11-16']),1);assert.equal(businessDays('2026-11-14','2026-11-15',[]),0)});
test('reject invalid and reversed ranges',()=>{assert.throws(()=>businessDays('2026-11-16','2026-11-13'));assert.throws(()=>businessDays('bad','2026-11-13'))});
test('carryover and accrual caps',()=>{assert.equal(carryover(8),5);assert.equal(carryover(3),3);assert.equal(carryover(-1),0);assert.ok(Math.abs(accruedPto(1)-1.67)<.001);assert.ok(accruedPto(12)<=20)});
test('14 day PTO notice and sick exemption',()=>{assert.equal(noticeValid('2026-11-16','2026-11-18','PTO','2026-11-03',[]),false);assert.equal(noticeValid('2026-11-16','2026-11-18','PTO','2026-11-02',[]),true);assert.equal(noticeValid('2026-11-16','2026-11-18','sick','2026-11-16',[]),true)});
test('quarter blackout boundaries and spanning ranges',()=>{assert.equal(salesBlackout('2026-03-17','2026-03-17'),false);assert.equal(salesBlackout('2026-03-18','2026-03-18'),true);assert.equal(salesBlackout('2026-03-17','2026-04-01'),true);assert.equal(salesBlackout('2026-04-01','2026-04-02'),false);assert.equal(salesBlackout('2026-12-18','2026-12-31'),true)});
"""


def validate_files(files):
    if not isinstance(files,dict) or set(files)!=FILES: raise ValueError('Unexpected generated files')
    if any(not isinstance(v,str) or not v.strip() or len(v)>45000 for v in files.values()): raise ValueError('Invalid file contents')
    files={p:'\n'.join(line for line in text.splitlines() if line.strip())+'\n' for p,text in files.items()}
    line_count=sum(len(v.splitlines()) for v in files.values())
    if line_count>350: raise ValueError(f'Generated app has {line_count} nonempty lines; maximum350. Preserve the compact reference scaffold.')
    combined='\n'.join(files.values())
    if re.search(r'(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_|dapi[a-f0-9]{20,}|AKIA[A-Z0-9]{16})',combined): raise ValueError('Secret-like content rejected')
    for marker in ['mock-login','request-form','request-list','approver-view','team-calendar','localStorage']:
        if marker not in files['src/main.ts']: raise ValueError('Missing required UI feature: '+marker)
    pkg=json.loads(files['package.json'])
    if set(pkg.get('dependencies',{})) or set(pkg.get('devDependencies',{}))!= {'vite','typescript'}: raise ValueError('Exactly Vite/TypeScript dev dependencies required')
    if any(not re.fullmatch(r'[~^]?\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?',v) for v in pkg['devDependencies'].values()): raise ValueError('Dependency versions must use registry semver')
    expected_scripts=json.loads((REFERENCE/'package.json').read_text())['scripts']
    if pkg.get('scripts')!=expected_scripts: raise ValueError('Use the reference package scripts exactly')
    if set(pkg)-{'name','version','type','scripts','devDependencies','private','description','license'}: raise ValueError('Unexpected package configuration')
    if pkg.get('type')!='module': raise ValueError('ES modules required')
    # Test only the pure logic module in a process with no environment secrets and restricted filesystem.
    if re.search(r'\b(import|require|eval|Function|process|fetch|globalThis)\b',files['src/logic.ts']): raise ValueError('Logic must be pure standalone functions')
    for path,content in files.items():
        if path.endswith('.ts'):
            imports=re.findall(r'(?:from\s*|import\s*|import\s*\()([\"\'])(.*?)\1',content)
            allowed={'./style.css','./logic.ts','./types.ts','../src/logic.ts','node:test','node:assert/strict'}
            if any(spec not in allowed for _,spec in imports):raise ValueError('Unexpected source import: '+path)
        if re.search(r'<script[^>]+src=[\"\']https?://|\b(fetch|XMLHttpRequest|WebSocket)\s*\(',content):
            raise ValueError('External code/network calls are not part of the mock app: '+path)
    return files


def test_files(files):
    node=shutil.which('node')
    if not node: return {'status':'not run','output':'Node is unavailable in this Databricks runtime.'}
    version=subprocess.run([node,'--version'],capture_output=True,text=True,timeout=5).stdout.strip()
    if int(version.lstrip('v').split('.')[0])<22: return {'status':'not run','output':'Node >=22.6 is required; runtime '+version}
    with tempfile.TemporaryDirectory(prefix='fernwood-tests-') as directory:
        directory=str(Path(directory).resolve())
        for path in ['package.json','src/logic.ts','tests/logic.test.ts']:
            dest=Path(directory)/path;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_text(files[path])
        (Path(directory)/'contract.test.ts').write_text(CONTRACT)
        (Path(directory)/'runner.mjs').write_text("await import('./tests/logic.test.ts'); await import('./contract.test.ts');\n")
        try:
            result=subprocess.run([node,'--experimental-strip-types',('--permission' if int(version.lstrip('v').split('.')[0])>=23 else '--experimental-permission'),
                '--allow-fs-read='+directory,'runner.mjs'],
                cwd=directory,env={'PATH':str(Path(node).parent),'TZ':'UTC','NODE_NO_WARNINGS':'1'},
                capture_output=True,text=True,timeout=20)
            return {'status':'passed' if result.returncode==0 else 'failed','exit_code':result.returncode,
                    'runtime':version,'output':(result.stdout+'\n'+result.stderr)[-12000:]}
        except subprocess.TimeoutExpired:
            return {'status':'failed','output':'Real test execution exceeded the 20-second limit.'}


def verify_app(files):
    from .buildcheck import production_check
    logic=test_files(files)
    if logic['status']!='passed':return dict(logic,status='failed')
    production=production_check(files)
    return {'status':production['status'],'logic_tests':logic['status'],'production_build':production['status'],
            'output':logic['output']+'\n'+production['output']}


def implementation_summary(doc,files):
    rules=[('notice','noticeValid','src/logic.ts','Check notice periods before accepting PTO requests.'),
           ('carry','carryover','src/logic.ts','Cap the unused PTO carried into the next year.'),
           ('accru','accruedPto','src/logic.ts','Calculate accrued PTO with the annual cap.'),
           ('blackout','salesBlackout','src/logic.ts','Check date ranges against sales blackout periods.'),
           ('approv','approver-view','src/main.ts','Provide a separate approval view for managers.'),
           ('calendar','team-calendar','src/main.ts','Show requests together on a team calendar.')]
    decisions=[]
    for word,marker,path,explanation in rules:
        requirement=next((r for r in doc['spec']['requirements'] if word in r['requirement'].lower()),None)
        if requirement and marker in files[path]:
            excerpt=next((line.strip() for line in files[path].splitlines() if marker in line and not line.startswith('import')), '')[:500]
            decisions.append(dict(requirement,explanation=explanation,path=path,excerpt=excerpt))
    return {'decisions':decisions,'files':sorted(files),'message':'Implementation choices linked to collected requirements and generated source.'}


def readme(doc):
    lines=['# Fernwood time-off tracker','',doc['task'],'','Small fictional demo: mock login, localStorage only. No live Slack, Google Workspace or Calendar integration. Do not use with real employee data.','',
           '## Requirements and sources','']
    lines += [f"- {r['requirement']} — {r['source_person_email']}" for r in doc['spec']['requirements']]
    lines += ['', '## Unresolved questions','']+[f'- {q}' for q in doc['spec'].get('open_questions',[])]
    lines += ['','## Run','', '`npm install` · `npm run dev` · `npm test` · `npm run build`','',
              'Requires Node >=22.6. Holidays and balances are demo configuration, not company records.',
              'For this demo, notice uses business days for request length and calendar days for the notice period. Approval for 6–10 days must be clarified; do not autoapprove it.','',
              '## Research sources','']+[f"- {r['title']}: {r['url']}" for r in doc['research']]
    return '\n'.join(lines)+'\n'


def build(watcher,doc):
    if not doc.get('artifacts'):
        failure=doc.get('generation_failure')
        previous_files=doc.get('generation_candidate')
        for attempt in range(max(doc.get('build_attempts',0),sum(e['type']=='test_result' for e in doc['events'])),3):
            doc['build_attempts']=attempt+1
            saved=watcher.commit(doc);doc.clear();doc.update(saved)
            watcher.event(doc,'phase_started',{'stage':'code','message':f'Writing and testing app source · attempt {attempt+1}.'})
            try:
                files=validate_files(watcher.model('build',{'task':doc['task'],'spec':doc['spec'],
                    'research':doc['research'],'previous_failure':failure,'previous_files':previous_files,'required_contract_tests':CONTRACT,'reference_files':{p:(REFERENCE/p).read_text() for p in FILES}},max_tokens=9500).get('files'))
                previous_files=files
                result=verify_app(files)
            except (ValueError,KeyError,TypeError) as e:
                result={'status':'failed','output':'Generated source validation failed: '+str(e)}
            doc['generation_failure']=result.get('output') if result['status']=='failed' else None
            doc['generation_candidate']=previous_files
            watcher.event(doc,'test_result',dict(result,attempt=attempt+1))
            if result['status']=='passed': break
            failure=result['output']
        else: raise ValueError('Generation failed after two retries')
        watcher.event(doc,'implementation',implementation_summary(doc,files))
        files['README.md']=readme(doc)
        doc.pop('generation_candidate',None)
        doc.pop('generation_failure',None)
        doc['artifacts']=[dict(id=doc['run_id']+':'+path,run_id=doc['run_id'],path=path,content=content,created_at=now_iso()) for path,content in files.items()]
        doc['test_result']=result
        saved=watcher.commit(doc);doc.clear();doc.update(saved)
    if doc['dry_run']:
        doc['status']='done'
        watcher.event(doc,'done',{'outcome':'dry_run','message':'Generated source only. No Git or workspace files written; run history and artifacts saved in Delta.',
                      'files':doc['artifacts'],'learned':doc['spec']['requirements'],'tests':doc['test_result']})
        return
    if doc.get('test_result',{}).get('production_build')!='passed':
        result=verify_app({r['path']:r['content'] for r in doc['artifacts']})
        doc['test_result']=result;watcher.event(doc,'test_result',result)
        if result['status']!='passed':raise ValueError('Saved source failed production verification')
    from .git_output import publish
    publish(watcher,doc)
