"""Real browser/UI checks using explicit gated fixtures, never production fallback.

Requires requirements-browser.txt and an installed Chromium/Chrome.
"""
import socket
import threading
from pathlib import Path

import pytest
import uvicorn

from afd.sentinel import SentinelWatcher
from afd.watchstore import Documents
from test_sentinel import FixtureModel


def test_sentinel_browser_with_explicit_fixtures(tmp_path, monkeypatch, request):
    if not request.config.getoption('--run-sentinel-browser'):
        pytest.skip('Enable explicitly with --run-sentinel-browser (requires Chrome and localhost access)')
    playwright = pytest.importorskip('playwright.sync_api')
    import afd.watcher as module
    from afd.main import app

    gate = threading.Event()
    base_model = FixtureModel()

    def model(messages):
        if messages[0]['content'].startswith('You are '):
            assert gate.wait(timeout=30), 'Browser never released persona test fixture'
        return base_model(messages)

    watcher = SentinelWatcher(Documents(tmp_path), model=model, background=True)
    watcher.publish()
    monkeypatch.setattr(module, '_watcher', watcher)
    monkeypatch.setenv('AFD_WORKFLOW', 'sentinel')
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    sock.listen()
    server = uvicorn.Server(uvicorn.Config(app, log_level='error', lifespan='off'))
    thread = threading.Thread(target=server.run, kwargs={'sockets':[sock]}, daemon=True)
    thread.start()
    chrome = Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
    try:
        with playwright.sync_playwright() as p:
            browser = p.chromium.launch(executable_path=str(chrome) if chrome.exists() else None, headless=True)
            page = browser.new_page(viewport={'width':1500, 'height':1100})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(f'http://127.0.0.1:{port}')
            playwright.expect(page.get_by_role('heading', name='Agent Watcher', exact=True)).to_be_visible()
            playwright.expect(page.locator('#stats')).to_contain_text('12 raw → 10 people')
            assert page.evaluate("S.cy.nodes('.person').length") == 10
            assert page.evaluate("S.cy.nodes('.review').length") == 4
            page.get_by_role('button', name='Start run', exact=True).click()
            page.wait_for_function("S.cy.nodes('.waiting').length === 1")
            assert page.evaluate("S.cy.getElementById('ben.okafor@wildguardlabs.org').hasClass('waiting')")
            gate.set()
            playwright.expect(page.locator('#runMeta')).to_contain_text('done')
            assert page.evaluate('T.run.requests.length') == 4
            page.get_by_role('button', name='Reset', exact=True).click()
            gate.clear()
            page.get_by_role('button', name='Run all', exact=True).click()
            page.wait_for_function("S.cy.nodes('.waiting').length === 5")
            gate.set()
            playwright.expect(page.locator('#runMeta')).to_contain_text('done')
            playwright.expect(page.locator('#summary')).to_contain_text('14 external views')
            page.get_by_role('button', name='Reset', exact=True).click()
            with page.expect_response(lambda response: response.url.endswith('/manual') and response.request.method == 'PUT'):
                page.get_by_role('checkbox', name='Manual mode for Elena Marsh', exact=True).check()
            page.get_by_role('button', name='Start run', exact=True).click()
            field = page.get_by_role('textbox', name='Answer for Elena Marsh', exact=True)
            playwright.expect(field).to_be_visible()
            request_id = page.evaluate("T.run.requests.at(-1).request_id")
            page.reload()
            playwright.expect(field).to_be_visible()
            assert page.evaluate("T.run.requests.at(-1).request_id") == request_id
            field.fill('CEO approval is required; no approval has been granted.')
            page.get_by_role('button', name='Submit answer for Elena Marsh', exact=True).click()
            playwright.expect(page.locator('#runMeta')).to_contain_text('done')
            assert page.evaluate("S.cy.nodes('.waiting').length") == 0
            assert not errors, errors
            screenshot = Path(__file__).resolve().parent.parent / 'data' / 'sentinel-test-browser.png'
            page.screenshot(path=str(screenshot), full_page=True)
            page.set_viewport_size({'width':390,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
            browser.close()
    finally:
        gate.set()
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()
