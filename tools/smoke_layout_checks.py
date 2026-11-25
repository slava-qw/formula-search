"""Native PDF layout audit, with a PyMuPDF rotation oracle and real Chrome UI."""
import json
import time

import fitz


def verify_layouts(popup, pdf_page, tab_id, pdf_path, out, report, baseline=False):
    def native(expression):
        return popup.evaluate('''async ([tabId, expression]) => FormulaNativeBridge.withViewer(tabId, async evaluate => {
            const result = await evaluate(expression, {userGesture: true});
            if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
            return result.result?.value;
        })''', [tab_id, expression])

    def wait_native(expression):
        deadline = time.monotonic()+10
        while not native(expression):
            if time.monotonic() >= deadline:
                raise AssertionError(expression)
            popup.wait_for_timeout(80)

    popup.locator('#query').fill(r'\{1,...,N\}')
    popup.locator('#search-mode').select_option('exact')
    popup.locator('#search-btn').click()
    popup.wait_for_function('() => !document.querySelector("#search-btn").disabled && _matches.length === 1')
    match = popup.evaluate('_matches[0]')
    original_url = pdf_page.url
    expression = '''(() => {
        const v = document.querySelector('pdf-viewer'), vp = v.viewport || v.viewport_;
        const root = v.shadowRoot || v.renderRoot, clip = root.querySelector('#scroller').getBoundingClientRect();
        const overlay = document.querySelector('[data-formula-search-overlay]');
        const card = document.querySelector('[data-formula-search-info-panel]');
        return {rotation: vp.getClockwiseRotations(), twoUp: vp.twoUpViewEnabled(),
            pageRect: vp.getPageScreenRect(PAGE), size: vp.size, position: vp.position,
            zoom: vp.getZoom(), mostVisible: vp.getMostVisiblePage(), clip: clip.toJSON(),
            overlay: overlay ? {...overlay.getBoundingClientRect().toJSON(), opacity: overlay.style.opacity} : null,
            coveredByCard: !!globalThis.__formulaSearchOverlayController?.coveredByCard,
            cardVisible: !!card && card.style.display !== 'none'};
    })()'''
    report['layouts'] = {'match': match, 'oracle': 'PyMuPDF page.rotation_matrix applied to the reviewed original PDF rectangle', 'samples': []}
    doc = fitz.open(pdf_path)
    original_rotations = [p.rotation for p in doc]

    def snapshot(label):
        value = native(expression.replace('PAGE', str(match['page'])))
        page = doc[match['page']]
        page.set_rotation(original_rotations[match['page']])
        box = fitz.Rect(match['bbox'])*page.derotation_matrix
        page.set_rotation(original_rotations[match['page']]+value['rotation']*90)
        box *= page.rotation_matrix
        p, c = value['pageRect'], value['clip']
        expected = [c['left']+p['x']+box.x0/page.rect.width*p['width'],
                    c['top']+p['y']+box.y0/page.rect.height*p['height'],
                    c['left']+p['x']+box.x1/page.rect.width*p['width'],
                    c['top']+p['y']+box.y1/page.rect.height*p['height']]
        value['expected'] = expected
        value['verticalFraction'] = ((expected[1]+expected[3])/2-c['top'])/c['height']
        o = value['overlay']
        value['edgeError'] = max(abs(o[k]-e) for k, e in zip(('left','top','right','bottom'), expected)) if o else None
        return {'case': label, **value}

    def jump():
        popup.locator('#results .info-arrow-btn').first.click()
        popup.wait_for_function("() => !document.querySelector('#status').textContent.startsWith('Jumping')")

    def checked(label, centered=True):
        deadline = time.monotonic()+8
        while True:
            value = snapshot(label)
            if value['overlay'] and value['edgeError'] < 2 and float(value['overlay']['opacity']) > .9 and value['cardVisible']:
                if not centered or .47 < value['verticalFraction'] < .53:
                    break
            if time.monotonic() >= deadline:
                report['layouts']['samples'].append(value)
                pdf_page.screenshot(path=str(out/(label+'-failed.png')))
                raise AssertionError(value)
            popup.wait_for_timeout(80)
        assert pdf_page.url == original_url
        report['layouts']['samples'].append(value)
        report['checks'].append('Native layout target and highlight agree with independent PDF geometry: '+label)
        return value

    def reflow_checked(label):
        deadline = time.monotonic()+8
        while True:
            value = snapshot(label)
            x0, y0, x1, y1 = value['expected']
            c, o = value['clip'], value['overlay']
            clipped = [max(x0-1, c['left']), max(y0-1, c['top']),
                       min(x1+1, c['right']), min(y1+1, c['bottom'])]
            visible = max(0, clipped[2]-clipped[0])*max(0, clipped[3]-clipped[1])/((x1-x0+2)*(y1-y0+2))
            if visible < .08:
                correct = not value['cardVisible'] and (not o or float(o['opacity']) == 0)
            else:
                error = max(abs(o[k]-e) for k, e in zip(('left','top','right','bottom'), clipped)) if o else float('inf')
                correct = error < 2 and value['cardVisible'] and (value['coveredByCard'] or float(o['opacity']) > .9)
            if correct:
                break
            if time.monotonic() >= deadline:
                report['layouts']['samples'].append(value)
                pdf_page.screenshot(path=str(out/(label+'-failed.png')))
                raise AssertionError(value)
            popup.wait_for_timeout(80)
        report['layouts']['samples'].append(value)
        report['checks'].append('Existing native highlight follows or hides after layout change without another jump: '+label)

    try:
        native("(() => {const v = document.querySelector('pdf-viewer'); v.viewport.setZoom(1); return true;})()")
        jump()
        # Instrument geometry reads while the viewer is idle. Restored before
        # other actions; no production counters or polling are introduced.
        popup.wait_for_timeout(600)
        native("(() => {const vp = document.querySelector('pdf-viewer').viewport; window.__fsReads=0; window.__fsOriginalRect=vp.getPageScreenRect; vp.getPageScreenRect=function(...args){window.__fsReads++; return window.__fsOriginalRect.apply(this,args)}; return true;})()")
        popup.wait_for_timeout(1000)
        report['layouts']['idle_geometry_reads_1s'] = native("(() => {const vp = document.querySelector('pdf-viewer').viewport; vp.getPageScreenRect=window.__fsOriginalRect; return window.__fsReads;})()")
        for rotation in (1, 2, 3, 0):
            native("(() => {document.querySelector('pdf-viewer').rotateClockwise(); return true;})()")
            wait_native(f"document.querySelector('pdf-viewer').viewport.getClockwiseRotations() === {rotation}")
            popup.wait_for_timeout(250)
            if baseline:
                report['layouts']['samples'].append(snapshot('rotation-90-before-jump'))
            else:
                reflow_checked(f'reflow-rotation-{rotation*90}')
            jump()
            if baseline:
                popup.wait_for_timeout(250)
                report['layouts']['samples'].append(snapshot('rotation-90-after-jump'))
                pdf_page.screenshot(path=str(out/'rotation-baseline.png'))
                return
            checked(f'rotation-{rotation*90}')
            pdf_page.screenshot(path=str(out/f'rotation-{rotation*90}.png'))
        for enabled in (True, False):
            native("(() => {document.querySelector('pdf-viewer').onTwoUpViewChanged_(new CustomEvent('two-up-view-changed', {detail: ENABLED})); return true;})()".replace('ENABLED', json.dumps(enabled)))
            wait_native("document.querySelector('pdf-viewer').viewport.twoUpViewEnabled() === "+json.dumps(enabled))
            popup.wait_for_timeout(250)
            reflow_checked('reflow-two-page' if enabled else 'reflow-single-page')
            jump()
            checked('two-page' if enabled else 'single-page-restored')
            pdf_page.screenshot(path=str(out/('two-page.png' if enabled else 'single-page-restored.png')))
            if enabled:
                popup.locator('#query').fill(r'\notin\mathcal{X}')
                popup.locator('#search-btn').click()
                popup.wait_for_function('() => !document.querySelector("#search-btn").disabled && _matches.length === 1')
                match = popup.evaluate('_matches[0]')
                assert match['page'] == 1
                jump()
                checked('two-page-right')
                pdf_page.screenshot(path=str(out/'two-page-right.png'))
    finally:
        doc.close()
