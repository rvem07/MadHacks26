"""Real App acceptance in a fresh private browser, with OAuth scoped to the App origin."""
import argparse
import json
import subprocess
import time
from pathlib import Path
from playwright.sync_api import sync_playwright,expect

p=argparse.ArgumentParser();p.add_argument('--run',action='store_true');p.add_argument('--run-id');a=p.parse_args()
url='https://agent-watcher-7474649299876234.aws.databricksapps.com'
token=json.loads(subprocess.check_output(['databricks','auth','token'],text=True))['access_token']
report={'app_url':url,'checks':{},'errors':[]}
with sync_playwright() as pw:
 browser=pw.chromium.launch(executable_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless=True)
 context=browser.new_context(viewport={'width':1500,'height':1050})
 context.route(url+'/**',lambda route:route.continue_(headers={**route.request.headers,'authorization':'Bearer '+token}))
 page=context.new_page();page.on('pageerror',lambda e:report['errors'].append(str(e)))
 expect.set_options(timeout=60000)
 try:
  page.goto(url);expect(page.get_by_role('heading',name='Agent Watcher',exact=True)).to_be_visible()
  page.wait_for_function('state.cy && state.graph')
  assert page.evaluate("state.cy.nodes().not('.stage').length")==10
  assert page.evaluate("state.cy.nodes('.review').length")==2
  assert page.evaluate("state.cy.edges('.quarantined').length")==2
  assert page.locator('#dangling line').count()==1
  pos=page.evaluate("state.cy.getElementById('nina.alvarez@fernwoodlabs.io').renderedPosition()");box=page.locator('#cy').bounding_box();page.mouse.move(box['x']+pos['x'],box['y']+pos['y']);expect(page.locator('#card')).to_contain_text('Nina Alvarez');
  page.select_option('#personSelect',label='Nina Alvarez');expect(page.locator('#card')).to_contain_text('Needs review')
  report['checks']['people_graph_and_review_relationships']='passed'
  page.click('#viewToggle')
  for tab in ['Raw CSV','Bronze','Silver','Gold']:
   page.get_by_role('button',name=tab,exact=True).click()
   page.wait_for_function("document.querySelector('#tableRows table') !== null")
   assert page.locator('#tableRows tbody tr').count()>0
  page.get_by_role('button',name='quarantine',exact=True).click()
  expect(page.locator('#tableRows tbody tr')).to_have_count(2)
  report['checks']['data_views_and_quarantine']='passed'
  page.click('#viewToggle');page.screenshot(path='data/fernwood-graph.png',full_page=True)
  page.click('#setup');expect(page.locator('#setupDialog')).to_be_visible()
  report['setup']=page.locator('#setupBody').inner_text();page.get_by_role('button',name='Done',exact=True).click()
  report['checks']['private_browser_live_app']='passed'
  if a.run or a.run_id:
   if a.run_id:
    page.evaluate('(id)=>{state.run=id;watch(++state.epoch).catch(error)}',a.run_id)
   else:
    page.check('#dry');page.click('#run')
   print('Waiting for real model run...',flush=True)
   page.wait_for_function("document.querySelector('#timeline [data-type=done]')||document.querySelector('#timeline [data-type=error]')",timeout=900000)
   rid=page.evaluate('state.run');report['run_id']=rid
   events=page.evaluate("()=>api('/runs/'+state.run+'/events')")
   answers=[e for e in events if e['type']=='answered']
   assert len(answers)>=6 and len({e['target_person'] for e in answers})==len(answers)
   assert all(e['duration_ms']>=1000 and e['payload']['simulated'] for e in answers)
   report['checks']['live_personas_and_dwell']='passed'
   report['checks']['real_search']='passed' if any(e['type']=='search_results' for e in events) else 'unavailable; no provider key configured'
   report['test_results']=[e['payload'] for e in events if e['type']=='test_result']
   report['outcome']={k:v for k,v in events[-1]['payload'].items() if k not in ['files','tests']};report['checks']['dry_run_generation']='passed' if events[-1]['type']=='done' else 'failed'
   assert not any(e['type']=='committed' for e in events)
   count=len(events);page.click('#replay');expect(page.locator('#mode')).to_have_text('REPLAY')
   page.wait_for_function("document.querySelector('#runStatus').textContent.includes('Replay complete')",timeout=180000)
   assert page.locator('#timeline .event').count()==count
   report['checks']['replay']='passed'
   page.screenshot(path='data/fernwood-run.png',full_page=True)
  if a.run or a.run_id:
   page.evaluate('(id)=>localStorage.setItem("fernwood.run",id)',report['run_id']);page.reload();expect(page.locator('#mode')).to_have_text('REPLAY');report['checks']['completed_refresh_labeled_replay']='passed'
  page.set_viewport_size({'width':390,'height':844});page.screenshot(path='data/fernwood-mobile.png',full_page=True)
  report['checks']['mobile_no_horizontal_overflow']='passed' if page.evaluate('document.documentElement.scrollWidth<=innerWidth') else 'failed'
 finally:
  Path('docs/browser-verification.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({k:v for k,v in report.items() if k!='outcome'},indent=2));browser.close()
