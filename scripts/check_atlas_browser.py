"""Local browser smoke test for the refined UK atlas, with review screenshots."""
import argparse
import json
from pathlib import Path
import re
import sqlite3

ROOT=Path(__file__).resolve().parents[1]


def main():
    from playwright.sync_api import sync_playwright
    parser=argparse.ArgumentParser();parser.add_argument('--url',default='http://127.0.0.1:5055')
    parser.add_argument('--browser',default='/opt/google/chrome-beta/chrome');args=parser.parse_args()
    out=ROOT/'docs/visualisations';out.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(ROOT/'data/grid/uk_power_grid.sqlite') as db:
        tower=db.execute("SELECT node_id FROM nodes WHERE node_type='tower' AND structure_height_m IS NOT NULL AND terrain_elevation_m IS NOT NULL LIMIT 1").fetchone()[0]
        station=db.execute("SELECT station_node_id FROM generation_outages WHERE event_status='Active' LIMIT 1").fetchone()[0]
    errors=[];api_errors=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=args.browser,headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
        page=browser.new_page(viewport={'width':1440,'height':1100},device_scale_factor=1)
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.on('response',lambda r:api_errors.append({'status':r.status,'url':r.url}) if '/api/grid/' in r.url and r.status>=400 else None)
        page.goto(args.url,wait_until='domcontentloaded')
        page.get_by_role('status').filter(has_text='nodes represented in view').wait_for(timeout=60000)
        page.get_by_role('status').filter(has_text='routes / connections').wait_for(timeout=60000)
        page.screenshot(path=str(out/'network-hierarchy.png'),full_page=True)
        search=page.get_by_label('Find an asset by ID or name');search.fill(tower)
        page.get_by_role('button',name='Find asset →').click()
        page.get_by_text('Tower height (source or estimate)',exact=True).wait_for(timeout=30000)
        page.get_by_text(re.compile('Height basis:')).wait_for(timeout=30000)
        page.screenshot(path=str(out/'pylon-height.png'),full_page=True)
        search.fill(station);page.get_by_role('button',name='Find asset →').click()
        page.get_by_text('Reported unit unavailability · Elexon REMIT',exact=True).wait_for(timeout=30000)
        page.screenshot(path=str(out/'generation-unavailability.png'),full_page=True)
        page.get_by_role('tab',name=re.compile('Outage history')).click()
        page.get_by_label('Operator region').select_option('SSEN_LIVE')
        page.locator('.atlas-records tbody tr').first.wait_for(timeout=30000)
        page.locator('.atlas-records tbody tr').first.click()
        page.get_by_text('RECORDED OUTAGE REPORT',exact=True).wait_for(timeout=30000)
        page.get_by_text('Candidate circuit regions',exact=True).wait_for(timeout=30000)
        page.screenshot(path=str(out/'outage-circuit-regions.png'),full_page=True)
        page.set_viewport_size({'width':390,'height':844});page.wait_for_timeout(600)
        width=page.evaluate('({width:innerWidth,scroll:document.documentElement.scrollWidth})')
        assert width['scroll']<=width['width']+1,width
        page.screenshot(path=str(out/'outage-mobile.png'),full_page=False)
        assert not errors,errors
        assert not api_errors,api_errors
        result={'javascript_errors':errors,'api_errors':api_errors,'mobile_width':width,
                'checks':['hierarchical network overview','route and connection layers','tower height inspector',
                          'separate REMIT evidence','operator filtering','candidate circuit regions','mobile width'],
                'screenshots':['network-hierarchy.png','pylon-height.png','generation-unavailability.png','outage-circuit-regions.png','outage-mobile.png']}
        (out/'browser-validation.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
        browser.close()


if __name__=='__main__':main()
