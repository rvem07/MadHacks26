"""Attempt Catalog Explorer visually, reporting SSO instead of assuming a pass."""
import json
import subprocess
from pathlib import Path
from playwright.sync_api import sync_playwright
host='https://dbc-4702f9e0-0c88.cloud.databricks.com'
url=host+'/explore/data/workspace/fernwood/nodes'
token=json.loads(subprocess.check_output(['databricks','auth','token'],text=True))['access_token']
with sync_playwright() as p:
 b=p.chromium.launch(executable_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless=True)
 c=b.new_context(viewport={'width':1400,'height':950});c.route(host+'/**',lambda r:r.continue_(headers={**r.request.headers,'authorization':'Bearer '+token}))
 page=c.new_page();page.goto(url);page.wait_for_timeout(8000)
 text=page.locator('body').inner_text()
 blocked=any(x in page.url.lower() for x in ['login','signin']) or 'Sign in' in text
 r={'url':url,'result':'blocked by interactive workspace SSO' if blocked else 'needs visual inspection','title':page.title()}
 page.screenshot(path='data/catalog.png',full_page=True);b.close()
Path('docs/catalog-verification.json').write_text(json.dumps(r,indent=2)+'\n');print(r)
