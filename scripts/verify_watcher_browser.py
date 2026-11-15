"""Browser acceptance check for the guided wildlife demo against a LOCAL app."""
import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright, expect


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:8765')
    parser.add_argument('--screenshot', default='data/wildlife-demo.png')
    args = parser.parse_args()
    chrome = Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=str(chrome) if chrome.exists() else None, headless=True)
        page = browser.new_page(viewport={'width':1440,'height':1000})
        errors = []
        page.on('pageerror', lambda err: errors.append(str(err)))
        page.goto(args.url)
        expect(page.get_by_role('heading', name='Agent Watcher', exact=True)).to_be_visible()
        assert page.locator('main > section').count() == 3
        assert page.locator('nav, select, #pipelinePanel, #inbox, #watchControls, #detail').count() == 0
        expect(page.locator('#task')).to_contain_text('endangered-animal')
        page.locator('#start').click()
        expect(page.locator('#runMeta')).to_contain_text('Waiting for Maria Alvarez')
        assert page.evaluate('S.cy.nodes().length') == 14
        assert page.evaluate('S.cy.edges().length') == 9
        assert page.evaluate("S.cy.getElementById('maria.alvarez@badgernet.com').hasClass('waiting')")
        expect(page.locator('#start')).to_be_disabled()
        page.get_by_role('radio', name='Other', exact=True).check()
        expect(page.locator('#otherExplanation')).to_be_visible()
        page.locator('#otherExplanation').fill('Not sure, whatever works')
        page.get_by_role('button', name='Submit answer', exact=True).click()
        expect(page.locator('#demoQuestion legend')).to_contain_text('Which specific people')
        expect(page.locator('#log')).to_contain_text('needs a more specific answer')
        # Refresh must restore this exact follow-up rather than restarting the scenario.
        request_id = page.evaluate('W.run.requests.at(-1).request_id')
        page.reload()
        expect(page.locator('#demoQuestion legend')).to_contain_text('Which specific people')
        assert page.evaluate('W.run.requests.at(-1).request_id') == request_id
        page.get_by_role('radio', name='Other', exact=True).check()
        page.locator('#otherExplanation').fill('Field rangers and local veterinarians')
        page.get_by_role('button', name='Submit answer', exact=True).click()
        expect(page.locator('#runMeta')).to_contain_text('Waiting for Priya Sharma')
        for person, label in [
            ('Priya Sharma', 'Ranger reports, with offline capture'),
            ('Rachel Kim', 'Only rangers assigned to that reserve'),
            ('Lena Ortiz', 'App notification with an Acknowledge button'),
            ('Tom Becker', 'One reserve with a small ranger team')]:
            expect(page.locator('#runMeta')).to_contain_text('Waiting for ' + person)
            page.get_by_role('radio', name=label, exact=True).check()
            page.get_by_role('button', name='Submit answer', exact=True).click()
        expect(page.locator('#runMeta')).to_contain_text('Application plan ready')
        expect(page.locator('#demoAnswer')).to_contain_text('Field rangers and local veterinarians')
        expect(page.locator('#demoAnswer')).to_contain_text('Only rangers assigned to that reserve')
        expect(page.locator('#start')).to_have_text('Run again')
        expect(page.locator('#start')).to_be_enabled()
        assert page.evaluate('S.cy.nodes(".waiting").length') == 0
        assert page.evaluate('S.cy.nodes(".consulted").length') == 5
        assert not errors, errors
        page.evaluate('window.scrollTo(0, 0)')
        screenshot = Path(args.screenshot)
        screenshot.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(screenshot), full_page=True)
        page.set_viewport_size({'width':390,'height':844})
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        page.get_by_role('button', name='Run again', exact=True).click()
        expect(page.locator('#runMeta')).to_contain_text('Waiting for Maria Alvarez')
        print(json.dumps({'browser':'passed', 'checks':['three-panel UI','existing employee graph','explicit Maria routing',
            'multiple choice','Other with follow-up','refresh recovery','five employee decisions','final specification','mobile layout','run again'],
            'page_errors':errors,'screenshot':str(screenshot)}))
        browser.close()


if __name__ == '__main__':
    main()
