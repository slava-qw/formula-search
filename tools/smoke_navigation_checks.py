"""Real native-PDF viewport checks for smoke_search_browser --navigation."""
import json
import time


def verify_navigation(popup, pdf_page, tab_id, out, report, baseline=False):
    def native(expression):
        return popup.evaluate('''async ([tabId, expression]) => FormulaNativeBridge.withViewer(tabId, async evaluate => {
            const result = await evaluate(expression, {userGesture: true});
            if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
            return result.result?.value;
        })''', [tab_id, expression])

    match = popup.evaluate('_matches[0]')
    original_url = pdf_page.url
    state_expression = '''(() => {
        const m = MATCH, v = document.querySelector('pdf-viewer');
        const vp = v.viewport || v.viewport_, root = v.shadowRoot || v.renderRoot;
        const scroller = root.querySelector('#scroller'), clip = scroller.getBoundingClientRect();
        const page = vp.getPageScreenRect(m.page), b = m.bbox;
        const box = {left: clip.left + page.x + b[0]/m.page_w*page.width,
            top: clip.top + page.y + b[1]/m.page_h*page.height,
            right: clip.left + page.x + b[2]/m.page_w*page.width,
            bottom: clip.top + page.y + b[3]/m.page_h*page.height};
        const overlay = document.querySelector('[data-formula-search-overlay]');
        const card = document.querySelector('[data-formula-search-info-panel]');
        return {page: m.page, mostVisible: vp.getMostVisiblePage(), zoom: vp.getZoom(),
            position: vp.position, size: vp.size, box, pageRect: page,
            clip: {left: clip.left, top: clip.top, width: clip.width, height: clip.height},
            overlay: overlay ? {...overlay.getBoundingClientRect().toJSON(), opacity: overlay.style.opacity, display: overlay.style.display} : null,
            cardVisible: !!card && card.style.display !== 'none',
            verticalFraction: ((box.top+box.bottom)/2-clip.top)/clip.height};
    })()'''

    def state():
        return native(state_expression.replace('MATCH', json.dumps(match)))

    report['navigation'] = {'query': popup.locator('#query').input_value(), 'match': match, 'samples': []}
    samples = report['navigation']['samples']
    initial = state()
    samples.append({'case': 'initial-bottom-of-page', **initial})
    if baseline:
        pdf_page.screenshot(path=str(out/'navigation-baseline.png'))
        return

    def check_centered(label):
        deadline = time.monotonic()+8
        while True:
            value = state()
            if .47 <= value['verticalFraction'] <= .53 and value['cardVisible']:
                break
            if time.monotonic() >= deadline:
                raise AssertionError((label, value))
            popup.wait_for_timeout(70)
        box, clip, overlay = value['box'], value['clip'], value['overlay']
        assert value['mostVisible'] == match['page'], (label, value)
        assert overlay and overlay['display'] != 'none' and float(overlay['opacity']) > .9, (label, value)
        assert abs(overlay['top']-box['top']) < 2 and abs(overlay['left']-box['left']) < 2, (label, value)
        assert box['left'] >= clip['left'] and box['right'] <= clip['left']+clip['width'], (label, value)
        assert pdf_page.url == original_url
        samples.append({'case': label, **value})
        report['checks'].append('Native PDF target centered with aligned highlight, correct page and unchanged URL: '+label)
        return value

    check_centered('bottom-of-page')
    pdf_page.screenshot(path=str(out/'navigation-centered.png'))
    for zoom in (.75, 1.25, 2.5):
        native(f"(() => {{const v = document.querySelector('pdf-viewer'); (v.viewport || v.viewport_).setZoom({zoom}); return true;}})()")
        popup.wait_for_timeout(180)
        popup.locator('#results .info-arrow-btn').first.click()
        popup.wait_for_function("() => !document.querySelector('#status').textContent.startsWith('Jumping')")
        value = check_centered(f'zoom-{zoom}')
        assert abs(value['zoom']-zoom) < .01
    pdf_page.screenshot(path=str(out/'navigation-zoomed.png'))
    old_position = value['position']
    native("(() => {const v = document.querySelector('pdf-viewer'); (v.viewport || v.viewport_).scrollBy({x:0,y:1400}); return true;})()")
    popup.wait_for_timeout(600)
    away = state()
    assert not away['cardVisible'] and (not away['overlay'] or away['overlay']['display'] == 'none' or float(away['overlay']['opacity']) == 0), away
    assert away['position']['y'] > old_position['y']+1000, away
    # Reflow may move the overlay but must never pull the reader back to it.
    popup.wait_for_timeout(500)
    assert abs(state()['position']['y']-away['position']['y']) < 1
    report['checks'].append('Scrolling away hides the card/highlight without recentering the reader')
    popup.locator('#results .info-arrow-btn').first.click()
    popup.wait_for_function("() => !document.querySelector('#status').textContent.startsWith('Jumping')")
    check_centered('repeat-after-scroll-away')
    # Viewport size changes must affect the next jump's context calculation.
    pdf_page.set_viewport_size({'width': 860, 'height': 650})
    popup.wait_for_timeout(250)
    popup.locator('#results .info-arrow-btn').first.click()
    popup.wait_for_function("() => !document.querySelector('#status').textContent.startsWith('Jumping')")
    check_centered('smaller-window')
    pdf_page.screenshot(path=str(out/'navigation-small-window.png'))
    # Selecting other real occurrences must replace the target, not keep
    # positioning against the initial formula or accumulate pinned overlays.
    pdf_page.set_viewport_size({'width': 1200, 'height': 900})
    native("(() => {const v = document.querySelector('pdf-viewer'); (v.viewport || v.viewport_).setZoom(1); return true;})()")
    for query, page, left in [(r'N(K+1)', 4, 310.9), (r'\mathbb{E}', 5, 341.92), (r'\notin\mathcal{X}', 1, 368.15)]:
        popup.locator('#query').fill(query)
        popup.locator('#search-mode').select_option('contains')
        popup.locator('#search-btn').click()
        popup.wait_for_function('() => !document.querySelector("#search-btn").disabled && _matches.length > 0')
        matches = popup.evaluate('_matches')
        index, match = next((i, m) for i, m in enumerate(matches) if m['page'] == page and m['bbox'] and abs(m['bbox'][0]-left) < 1)
        popup.locator('#results .info-arrow-btn').nth(index).click()
        popup.wait_for_function("() => !document.querySelector('#status').textContent.startsWith('Jumping')")
        check_centered('new-target-'+query)
        counts = native("({overlays: document.querySelectorAll('[data-formula-search-overlay]').length, cards: document.querySelectorAll('[data-formula-search-info-panel]').length})")
        assert counts == {'overlays': 1, 'cards': 1}, counts
    pdf_page.screenshot(path=str(out/'navigation-new-target.png'))
    # At low zoom the first-page speedup figure is closer to the document start
    # than half a viewport. The browser must clamp there, not create blank space.
    native("(() => {const v = document.querySelector('pdf-viewer'); (v.viewport || v.viewport_).setZoom(.75); return true;})()")
    popup.locator('#query').fill(r'2.36\times')
    popup.locator('#search-btn').click()
    popup.wait_for_function('() => !document.querySelector("#search-btn").disabled && _matches.length > 0')
    index, match = next((i, m) for i, m in enumerate(popup.evaluate('_matches')) if m['page'] == 0)
    popup.locator('#results .info-arrow-btn').nth(index).click()
    popup.wait_for_function("() => !document.querySelector('#status').textContent.startsWith('Jumping')")
    popup.wait_for_timeout(250)
    first = state()
    assert first['position']['y'] == 0 and first['mostVisible'] == 0 and first['cardVisible'], first
    assert .3 < first['verticalFraction'] < .5, first
    assert pdf_page.url == original_url
    samples.append({'case': 'first-page-clamp', **first})
    report['checks'].append('A real first-page match clamps at document start with its target still visible')
    pdf_page.screenshot(path=str(out/'navigation-first-page.png'))
