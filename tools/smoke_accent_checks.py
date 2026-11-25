"""Actual popup, HTTP search and native highlights for reviewed PDF notation."""
import json
import time
import hashlib

from tools.audit_pdf_rendering import check_accents, check_variants, check_words
from tools.audit_pdf_text import PROBES, NEGATIVE_PROBES
from tools.verify_extension_ui import find_pdf_tab_id


def capture_settled_pdf(page, out, label):
    # Native PDF pixels can lag behind the viewport's reported page/geometry.
    # Retain that evidence when it occurs; do not repeat the navigation click.
    first = previous = page.screenshot()
    deadline = time.monotonic()+6
    stable, samples = 0, 1
    while time.monotonic() < deadline:
        page.wait_for_timeout(250)
        current = page.screenshot()
        samples += 1
        stable = stable+1 if current == previous else 0
        previous = current
        if stable >= 2:
            break
    else:
        raise AssertionError('Native PDF screenshot did not settle: '+label)
    (out/f'{label}-native.png').write_bytes(current)
    if current != first:
        (out/f'{label}-native-initial.png').write_bytes(first)
    return {'samples': samples, 'initial_sha256': hashlib.sha256(first).hexdigest(),
            'settled_sha256': hashlib.sha256(current).hexdigest(), 'changed': current != first}


def verify_accents(popup, pdf_page, paths, out, report, include_words=False, unicode_math=False, drawn_overlines=False):
    probes = [p for p in PROBES if (p.get('drawn_overline') if drawn_overlines else p.get('unicode_math') if unicode_math else
              p.get('math_accents') or include_words and (p.get('roman_words') or p.get('math_operators')))]
    results = []
    report['drawn_overlines' if drawn_overlines else 'unicode_math' if unicode_math else 'word_notation' if include_words else 'accents'] = results
    for paper, path in paths.items():
        url = path.as_uri()
        pdf_page.goto(url, wait_until='load')
        pdf_page.bring_to_front()
        popup.reload(wait_until='load')
        popup.wait_for_function('(url) => currentSessionUrl === url && !document.querySelector("#search-btn").disabled', arg=url)
        popup.evaluate('() => {window.close = () => {}}')
        popup.locator('#search-mode').select_option('contains')
        tab_id = find_pdf_tab_id(popup, path.name)
        for probe in (p for p in probes if p['paper'] == paper):
            popup.locator('#query').fill(probe['query'])
            popup.locator('#search-btn').click()
            popup.wait_for_function('() => !document.querySelector("#search-btn").disabled && _matches.length > 0')
            matches = popup.evaluate('_matches')
            index, match = next((i, m) for i, m in enumerate(matches) if m['page'] == probe['page']
                                and m['bbox'] and all(abs(a-b)<1 for a, b in zip(m['bbox'], probe['bbox'])))
            card = popup.locator('#results .result-item').nth(index)
            # Offscreen cards defer KaTeX until viewport/focus interaction.
            card.locator('.info-arrow-btn').focus()
            assert not card.locator('.katex-error').count()
            mathml = card.locator('.katex-mathml').first.inner_html()
            check_accents(mathml, probe.get('math_accents', []))
            check_variants(mathml, probe.get('math_variants', {}))
            check_words(mathml, probe.get('roman_words', []), probe.get('math_operators', []), probe.get('operator_limits', []))
            label = f'{paper}-{len(results)+1}'
            popup.screenshot(path=str(out/f'{label}-preview.png'))
            report['checks'].append('Actual popup preserves reviewed notation and mathematical font: '+probe['query'])
            popup.locator('#results .info-arrow-btn').nth(index).click()
            popup.wait_for_function("() => !document.querySelector('#status').textContent.startsWith('Jumping')")
            # The expected rectangle is manually reviewed paper geometry, not
            # the returned search rectangle or the extension's transform code.
            expression = '''(() => { try {
                const v = document.querySelector('pdf-viewer'), vp = v.viewport || v.viewport_;
                const clip = (v.shadowRoot || v.renderRoot).querySelector('#scroller').getBoundingClientRect();
                const page = vp.getPageScreenRect(PAGE), b = BOX;
                const expected = {left:clip.left+page.x+b[0]/WIDTH*page.width,
                    top:clip.top+page.y+b[1]/HEIGHT*page.height,
                    right:clip.left+page.x+b[2]/WIDTH*page.width,
                    bottom:clip.top+page.y+b[3]/HEIGHT*page.height};
                const overlay = document.querySelector('[data-formula-search-overlay]');
                const card = document.querySelector('[data-formula-search-info-panel]');
                return {expected, overlay:overlay ? {...overlay.getBoundingClientRect().toJSON(),
                    display:overlay.style.display,opacity:overlay.style.opacity}:null,
                    cardVisible:!!card && card.style.display !== 'none', page:vp.getMostVisiblePage()};
            } catch (error) { return {geometry_pending:String(error)}; }
            })()'''.replace('PAGE', str(probe['page'])).replace('BOX', json.dumps(probe['bbox'])).replace('WIDTH', str(match['page_w'])).replace('HEIGHT', str(match['page_h']))
            deadline = time.monotonic()+12
            state = None
            pending = []
            while time.monotonic() < deadline:
                state = popup.evaluate('''async ([tabId, expression]) => FormulaNativeBridge.withViewer(tabId, async evaluate => {
                    const result = await evaluate(expression);
                    if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
                    return result.result?.value;
                })''', [tab_id, expression])
                if state.get('geometry_pending'):
                    pending.append(state['geometry_pending'])
                overlay = state.get('overlay')
                if overlay and state['cardVisible'] and overlay['display'] != 'none' and float(overlay['opacity']) > .9:
                    if max(abs(overlay[e]-state['expected'][e]) for e in ('left', 'top', 'right', 'bottom')) < 2:
                        break
                popup.wait_for_timeout(100)
            else:
                report['failed_native_accent'] = {'probe': probe, 'state': state,
                    'status': popup.locator('#status').inner_text(), 'pending': pending}
                raise AssertionError((probe, state))
            assert state['page'] == probe['page'] and pdf_page.url == url, state
            capture = capture_settled_pdf(pdf_page, out, label)
            results.append({'probe': probe, 'match': match, 'native': state, 'pending_geometry_reads': pending,
                            'native_capture': capture})
            report['checks'].append('Native highlight agrees with the reviewed notation rectangle: '+probe['query'])
            wrong = probe['reject_queries'][0]
            popup.locator('#query').fill(wrong)
            popup.locator('#search-btn').click()
            popup.wait_for_function('() => !document.querySelector("#search-btn").disabled')
            assert not any(m['page'] == probe['page'] and m['bbox']
                           and all(abs(a-b)<1 for a,b in zip(m['bbox'], probe['bbox'])) for m in popup.evaluate('_matches'))
            report['checks'].append('Actual popup excludes the altered notation at the reviewed location: '+wrong)
        if include_words:
            for negative in (p for p in NEGATIVE_PROBES if p['paper'] == paper and p.get('category') == 'author'):
                popup.locator('#query').fill(negative['query'])
                popup.locator('#search-btn').click()
                popup.wait_for_function("() => document.querySelector('#status').textContent.startsWith('No matches')")
                assert not popup.evaluate('_matches.length')
                report['checks'].append('Author name is excluded through actual HTTP search: '+paper)
