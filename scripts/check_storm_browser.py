"""Local desktop/mobile smoke test; requires Playwright and an installed Chrome.

Run the built Flask app first, then:
  .venv/bin/python scripts/check_storm_browser.py --url http://127.0.0.1:5056
"""
import argparse
import json


def main():
    from playwright.sync_api import sync_playwright
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:5001')
    parser.add_argument('--browser', default='/usr/bin/google-chrome')
    parser.add_argument('--screenshot', default='/tmp/storm-scenario-mobile.png')
    args = parser.parse_args()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=args.browser, headless=True,
                                              args=['--no-sandbox', '--disable-dev-shm-usage'])
        page = browser.new_page(viewport={'width': 1440, 'height': 1100})
        errors, requests = [], []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('request', lambda request: requests.append(request.post_data_json)
                if request.method == 'POST' and request.url.endswith('/api/storm-scenarios/runs') else None)
        page.goto(args.url.rstrip('/') + '/#storm')
        page.locator('.storm-track').wait_for()

        def click_map(lon, lat):
            # Existing outline projection, plus the map's Atlantic padding.
            point = page.evaluate('''([lon, lat]) => {
              const svg = document.querySelector('.uk-map');
              const p = new DOMPoint((lon + 8.144824) * .5674610192148555 * 106.89494999726548 + 260,
                (60.831885 - lat) * 106.89494999726548).matrixTransform(svg.getScreenCTM());
              return {x:p.x,y:p.y};
            }''', [lon, lat])
            page.mouse.click(point['x'], point['y'])

        page.get_by_role('button', name='Place in ocean', exact=True).click()
        click_map(-3, 53)
        assert page.get_by_role('button', name='Cancel placement').is_visible()
        click_map(-9, 51)
        page.get_by_text('Tap the map toward the destination.', exact=True).wait_for()
        click_map(-3, 55)
        page.get_by_role('button', name='Place in ocean', exact=True).wait_for()
        page.get_by_text('Starting conditions', exact=True).click()
        assert abs(float(page.get_by_label('Start longitude', exact=True).input_value()) + 9) < .05
        assert abs(float(page.get_by_label('Start latitude', exact=True).input_value()) - 51) < .05
        page.get_by_label('Duration (hours)', exact=True).fill('6')
        page.get_by_label('Random seed', exact=True).fill('73')
        page.get_by_label('Storm type', exact=True).select_option('double_peak')
        slider = page.get_by_role('slider', name='Storm travel direction')
        before = float(slider.get_attribute('aria-valuenow'))
        slider.focus()
        page.keyboard.press('ArrowRight')
        assert float(slider.get_attribute('aria-valuenow')) == (before + 5) % 360
        # Dragging the direction handle must not pan the map or trigger placement.
        box = slider.bounding_box()
        view = page.locator('.uk-map').get_attribute('viewBox')
        page.mouse.move(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)
        page.mouse.down()
        page.mouse.move(box['x'] + box['width'] / 2 + 25, box['y'] + box['height'] / 2, steps=5)
        page.mouse.up()
        assert page.locator('.uk-map').get_attribute('viewBox') == view
        page.get_by_role('button', name='Simulate storm', exact=True).click()
        page.get_by_text('Completed run:', exact=False).wait_for(timeout=30000)
        assert requests[-1]['mode'] == 'single'
        assert requests[-1]['storm_config']['seed'] == 73
        assert abs(requests[-1]['storm_config']['origin']['lon'] + 9) < .05
        page.get_by_role('button', name='Pause', exact=True).click()
        page.get_by_label('Storm playback hour', exact=True).fill('3')
        assert 'Hour 3 of 6' in page.locator('.status').inner_text()
        page.get_by_text('Planning settings and weather import', exact=True).click()
        page.get_by_label('Budget units', exact=True).fill('0')
        page.get_by_role('button', name='Compare mitigation', exact=True).click()
        page.get_by_role('button', name='Apply plan & rerun same storm', exact=True).wait_for(timeout=60000)
        assert requests[-1]['mode'] == 'compare'
        assert requests[-1]['storm_config'] == requests[-2]['storm_config']
        page.get_by_role('button', name='Pause', exact=True).click()
        with page.expect_download() as downloaded:
            page.get_by_role('button', name='Export', exact=True).click()
        exported = json.loads(open(downloaded.value.path()).read())
        assert exported['replay']['request']['storm_config']['seed'] == 73
        page.set_viewport_size({'width': 390, 'height': 844})
        page.wait_for_timeout(300)
        dimensions = page.evaluate('({width:innerWidth,scroll:document.documentElement.scrollWidth})')
        assert dimensions['scroll'] <= dimensions['width'], dimensions
        page.screenshot(path=args.screenshot, full_page=True)
        assert not errors, errors
        print(json.dumps({'checks': ['ocean placement', 'land rejection', 'track direction', 'keyboard direction',
                                     'drag direction without pan', 'single run', 'playback scrub', 'comparison',
                                     'scenario-preserving export', 'mobile width'],
                          'mobile': dimensions, 'javascript_errors': errors, 'screenshot': args.screenshot}))
        browser.close()


if __name__ == '__main__':
    main()
