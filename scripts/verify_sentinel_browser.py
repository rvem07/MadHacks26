"""Acceptance checks against an actual App URL in a new browser context.

--login lets a user complete SSO in that fresh context; no cookies are saved.
No injected API answers or models are used by this verifier.
"""
import argparse
import json
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright, expect


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', required=True)
    parser.add_argument('--login', action='store_true', help='Visible new context for interactive Databricks SSO')
    parser.add_argument('--oauth', action='store_true', help='Authenticate a fresh private context with an external CLI OAuth token; never saved')
    parser.add_argument('--screenshot', default='data/sentinel-browser.png')
    args = parser.parse_args()
    chrome = Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
    report = {'url': args.url, 'checks': {}, 'errors': []}
    expect.set_options(timeout=240000)

    def completed(page):
        page.wait_for_function("T.run && (T.run.status === 'done' || T.run.execution_error || T.run.projection_error)", timeout=240000)
        run = page.evaluate('T.run')
        assert not run.get('execution_error'), run.get('execution_error')
        assert not run.get('projection_error'), run.get('projection_error')
        assert run['status'] == 'done'
        return run
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.login, executable_path=str(chrome) if chrome.exists() else None)
        context = browser.new_context(viewport={'width':1500,'height':1100})
        if args.oauth:
            parsed = urlsplit(args.url)
            if parsed.scheme != 'https' or not parsed.hostname.endswith('.databricksapps.com'):
                raise ValueError('--oauth only sends credentials to an HTTPS Databricks App origin')
            token = json.loads(subprocess.check_output(['databricks', 'auth', 'token'], text=True))['access_token']
            origin = f'{parsed.scheme}://{parsed.netloc}'
            # Scope credentials to the exact App origin, never to the graph CDN or redirects.
            context.route(origin + '/**', lambda route: route.continue_(headers={**route.request.headers, 'authorization':'Bearer ' + token}))
            report['authentication'] = 'fresh private context; CLI OAuth; no saved cookies'
        page = context.new_page()
        page.on('pageerror', lambda error: report['errors'].append(str(error)))
        try:
            response = page.goto(args.url)
            print(f'App navigation HTTP {response.status}', flush=True)
            assert response.ok, f'App is not ready: HTTP {response.status}. Wait for startup and rerun.'
            expect(page.get_by_role('heading', name='Agent Watcher', exact=True)).to_be_visible(timeout=60000)
            report['checks']['fresh_context_app_access'] = 'passed'
            data = page.evaluate("() => fetch('/api/watcher/pipeline').then(r=>r.json())")
            assert data['storage_mode'] == 'delta', 'Deployed acceptance requires live Delta persistence'
            stats = data['stats']
            assert [stats[k] for k in ['raw_people','unique_people','duplicates_merged','quarantined_count']] == [12,10,1,4]
            assert len(data['quarantine']) == 6
            page.wait_for_function('S.cy && T.pipeline')
            assert page.evaluate("S.cy.nodes('.person').length") == 10
            report['checks']['pipeline_counts_and_graph'] = 'passed'
            print('PASS: authenticated fresh context and live pipeline graph.', flush=True)
            elena = page.get_by_role('checkbox', name='Manual mode for Elena Marsh', exact=True)
            # First run checks the ordinary sequential auto-persona flow.
            if elena.is_checked():
                with page.expect_response(lambda response: response.url.endswith('/manual') and response.request.method == 'PUT'):
                    elena.uncheck()
            page.get_by_role('button', name='Start run', exact=True).click()
            print('Started sequential live run.', flush=True)
            run = completed(page)
            assert [r['asked_person_email'].split('.')[0] for r in run['requests']] == ['ben','mei','carlos','elena']
            assert all(r['status'] == 'answered' for r in run['requests'])
            assert any(e['state'] == 'waiting_on_human' for e in run['events'])
            assert any(e['state'] == 'resumed' for e in run['events'])
            assert '14' in run['requests'][0]['answer']
            report['checks']['sequential_pause_persona_resume'] = 'passed'
            print('PASS: live sequential pause, context personas, resume.', flush=True)
            page.get_by_role('button', name='Reset', exact=True).click()
            # Record actual graph class changes while Run all executes.
            page.evaluate("window.maxWaiting=0; S.cy.on('class', 'node', () => { window.maxWaiting=Math.max(window.maxWaiting,S.cy.nodes('.waiting').length); });")
            page.get_by_role('button', name='Run all', exact=True).click()
            run = completed(page)
            assert len(run['requests']) == 5 and run['answer']
            assert page.evaluate('window.maxWaiting') >= 2, 'Never observed multiple glowing nodes'
            report['checks']['parallel_glowing_nodes_and_summary'] = 'passed'
            print('PASS: live parallel personas, multiple glowing nodes, summary.', flush=True)
            page.get_by_role('button', name='Reset', exact=True).click()
            with page.expect_response(lambda response: response.url.endswith('/manual') and response.request.method == 'PUT'):
                elena.check()
            page.get_by_role('button', name='Start run', exact=True).click()
            expect(page.get_by_role('textbox', name='Answer for Elena Marsh', exact=True)).to_be_visible()
            assert page.evaluate("T.run.status") == 'waiting_on_human'
            request_id = page.evaluate("T.run.requests.find(r=>r.status==='pending').request_id")
            page.reload()
            expect(page.get_by_role('textbox', name='Answer for Elena Marsh', exact=True)).to_be_visible()
            assert page.evaluate("T.run.requests.find(r=>r.status==='pending').request_id") == request_id
            page.get_by_role('textbox', name='Answer for Elena Marsh', exact=True).fill('CEO approval is required before partner disclosure. This answer does not grant approval for this incident.')
            page.get_by_role('button', name='Submit answer for Elena Marsh', exact=True).click()
            completed(page)
            report['checks']['manual_elena_refresh_and_resume'] = 'passed'
            with page.expect_response(lambda response: response.url.endswith('/manual') and response.request.method == 'PUT'):
                elena.uncheck()
            answer = page.evaluate("() => fetch('/api/watcher/persona', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({person:'ben.okafor@wildguardlabs.org',answer:'What is the cafeteria menu today?'})}).then(r=>r.json())")
            assert "I don't know from my context" in answer['answer'] and 'might know' in answer['answer']
            report['checks']['out_of_context_persona'] = 'passed'
            page.set_viewport_size({'width':390,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
            assert not report['errors'], report['errors']
            report['result'] = 'passed'
        except Exception as exc:
            report['result'] = 'failed'
            report['failure'] = f'{type(exc).__name__}: {exc}'
            raise
        finally:
            screenshot = Path(args.screenshot)
            screenshot.parent.mkdir(parents=True, exist_ok=True)
            try:
                page.screenshot(path=str(screenshot), full_page=True, timeout=10000)
            except Exception:
                pass
            screenshot.with_suffix('.json').write_text(json.dumps(report, indent=2))
            print(json.dumps(report, indent=2))
            browser.close()


if __name__ == '__main__':
    main()
