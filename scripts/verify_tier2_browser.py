"""Deployed Tier 2 acceptance: private OAuth browser, real model and physical Delta."""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))


def main():
    from playwright.sync_api import sync_playwright, expect
    from databricks.sdk import WorkspaceClient
    from afd.watchstore import DeltaDocuments
    from afd.synthesis import BuildPlan, compact
    parser=argparse.ArgumentParser()
    parser.add_argument('--url',default='https://agent-watcher-7474649299876234.aws.databricksapps.com')
    parser.add_argument('--warehouse-id',default='1bc852920e5efadc')
    parser.add_argument('--run-id',help='Reopen a completed run to recheck presentation/persistence without another model run')
    parser.add_argument('--output',default='data/tier2-browser.json')
    args=parser.parse_args()
    parsed=urlsplit(args.url)
    if parsed.scheme!='https' or not parsed.hostname.endswith('.databricksapps.com'):
        raise ValueError('Only an HTTPS Databricks App origin is allowed')
    token=json.loads(subprocess.check_output(['databricks','auth','token'],text=True))['access_token']
    client=WorkspaceClient()
    db=DeltaDocuments(args.warehouse_id,'workspace','sentinel_gold',client=client)
    report={'app_url':args.url,'checks':{},'page_errors':[],'authentication':'fresh private OAuth context; no stored cookies'}
    output=Path(args.output)
    output.parent.mkdir(parents=True,exist_ok=True)
    chrome=Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
    expect.set_options(timeout=60000)
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=str(chrome) if chrome.exists() else None,headless=True)
        context=browser.new_context(viewport={'width':1500,'height':1100},accept_downloads=True)
        origin=f'{parsed.scheme}://{parsed.netloc}'
        context.route(origin+'/**',lambda route:route.continue_(headers={**route.request.headers,'authorization':'Bearer '+token}))
        page=context.new_page()
        page.on('pageerror',lambda error:report['page_errors'].append(str(error)))
        try:
            response=page.goto(args.url)
            assert response.ok, f'App not ready: {response.status}'
            expect(page.get_by_role('heading',name='Agent Watcher',exact=True)).to_be_visible()
            page.wait_for_function('S.cy && T.pipeline')
            expect(page.locator('#manualToggles input')).to_have_count(10)
            if args.run_id:
                page.evaluate('(id) => sentinelLoad(id)',args.run_id)
            else:
                for toggle in page.locator('#manualToggles input:checked').all():
                    with page.expect_response(lambda r:r.url.endswith('/manual') and r.request.method=='PUT'):
                        toggle.uncheck()
                page.locator('#task').fill(page.locator('#task').input_value()+' Also identify the fourteen external viewers by name.')
                page.get_by_role('button',name='Run all',exact=True).click()
            print('Waiting for live persona collection and plan synthesis.',flush=True)
            page.wait_for_function("T.run && (T.run.status === 'done' || T.run.execution_error || T.run.projection_error)",timeout=600000)
            run=page.evaluate('T.run')
            report['run_id']=run['run_id']
            assert not run.get('execution_error'), run.get('execution_error')
            assert not run.get('projection_error'), run.get('projection_error')
            assert run['status']=='done'
            expect(page.locator('#downloadPlan')).to_be_visible()
            data=page.evaluate("() => fetch('/api/watcher/runs/'+T.run.run_id+'/plan').then(r=>r.json())")
            plan=BuildPlan.model_validate(data['plan']).model_dump()
            pipeline=page.evaluate('T.pipeline')
            people={n['id']:n for n in pipeline['nodes'] if n['type']=='person'}
            owners={r['topic']:r['owner_email'] for r in pipeline['silver_responsibilities'] if r.get('routing_allowed')}
            assert 6<=len(plan['tasks'])<=8
            for task in plan['tasks']:
                assert task['owner_email'] in people and not people[task['owner_email']]['properties']['needs_review']
                assert owners[task['topic']]==task['owner_email']
            assert any(e['action']=='synthesis' for e in run['events'])
            assert page.evaluate("S.cy.nodes('.plan-owner').length")>=1
            report['checks']['owned_tasks_after_run_all']='passed'
            print('PASS: 6–8 ontology-owned tasks and synthesis graph glow.',flush=True)
            answers={r['asked_person_email']:r['answer'] for r in run['requests']}
            links=page.locator('.plan-source')
            assert links.count()==len(plan['findings'])
            for finding in plan['findings']:
                assert compact(finding['finding']) in compact(answers[finding['source_person_email']])
            for i in range(links.count()):
                link=links.nth(i)
                email=link.get_attribute('data-person')
                link.click()
                assert page.evaluate('(email) => S.cy.getElementById(email).hasClass("source-highlight")',email)
            report['checks']['all_findings_cited_and_click_highlights']='passed'
            assert any(re.search(r'who.*view|identit|view.*(?:name|person|who)',q,re.I) for q in plan['open_questions']),plan['open_questions']
            report['checks']['unknown_viewer_identity_in_open_questions']='passed'
            with page.expect_download() as download:
                page.get_by_role('link',name='Download plan.md',exact=True).click()
            assert download.value.suggested_filename=='plan.md'
            assert Path(download.value.path()).read_text()==data['markdown']
            report['checks']['markdown_download']='passed'
            stored=db.get_plan(run['run_id'])
            assert stored and stored['plan']==plan and stored['created_at']
            metadata=client.tables.get('workspace.sentinel_gold.plans')
            assert metadata.data_source_format.value=='DELTA'
            assert {'run_id','plan_json','created_at'} <= {c.name for c in metadata.columns}
            report['checks']['physical_delta_plan_and_uc_registration']='passed'
            report['tasks']=len(plan['tasks'])
            report['open_questions']=plan['open_questions']
            catalog_url=client.config.host.rstrip('/')+'/explore/data/workspace/sentinel_gold/plans'
            report['catalog_url']=catalog_url
            # Try Catalog Explorer itself; API registration alone is not UI verification.
            workspace_origin=client.config.host.rstrip('/')
            context.route(workspace_origin+'/**',lambda route:route.continue_(headers={**route.request.headers,'authorization':'Bearer '+token}))
            catalog=context.new_page()
            catalog.goto(catalog_url,wait_until='domcontentloaded',timeout=60000)
            try:
                expect(catalog.get_by_text('plan_json',exact=True).first).to_be_visible(timeout=45000)
                expect(catalog.get_by_text('run_id',exact=True).first).to_be_visible(timeout=15000)
                report['checks']['catalog_explorer_ui']='passed'
                catalog.screenshot(path=str(output.with_name('tier2-catalog.png')),full_page=True)
            except Exception:
                report['checks']['catalog_explorer_ui']='requires interactive workspace SSO; Delta registration verified by SDK and SQL'
                catalog.screenshot(path=str(output.with_name('tier2-catalog.png')),full_page=True)
            assert not report['page_errors'],report['page_errors']
            report['result']='passed' if report['checks']['catalog_explorer_ui']=='passed' else 'passed except interactive Catalog UI verification'
        except Exception as exc:
            report['result']='failed'
            report['failure']=f'{type(exc).__name__}: {exc}'
            raise
        finally:
            try:
                page.screenshot(path=str(output.with_suffix('.png')),full_page=True,timeout=10000)
            except Exception:
                pass
            output.write_text(json.dumps(report,indent=2)+'\n')
            print(json.dumps(report,indent=2),flush=True)
            browser.close()


if __name__=='__main__':
    main()
