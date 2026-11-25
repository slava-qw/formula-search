"""Headless extension smoke against real local paper source/PDF and HTTP API.

Uses an isolated Chromium profile and temporary in-process backend on a free
port. Does not touch the user's browser, running backend, or persistent cache.
The popup is tested as an extension tab; this is not a toolbar-popup gesture
or OS keyboard-shortcut test. Native PDF navigation uses the real extension.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import uvicorn
from playwright.sync_api import sync_playwright
import backend.main as backend
from backend.extractor.arxiv_tex import _merge_tex_files, parse_tex_formulas, build_line_index
from backend.matcher.index import build_index
from tools.audit_formula_mapping import DEFAULT_PAPERS, _load_tex_files
from tools.verify_extension_ui import find_pdf_tab_id, _EVAL_HELPER_JS, get_rects


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--legacy-bridge', action='store_true', help='Compare the bridge from the first search checkpoint')
    parser.add_argument('--pdf-text', action='store_true', help='Use only the PDF glyph extractor, without TeX source')
    parser.add_argument('--delimiters', action='store_true', help='Also check PDF-only set braces and negation in the SMC paper')
    parser.add_argument('--accents', action='store_true', help='Check reviewed accents in four real PDFs; requires --pdf-text')
    parser.add_argument('--words', action='store_true', help='Check accents, upright words/operators and author exclusions; requires --pdf-text')
    parser.add_argument('--readiness', action='store_true', help='Check loading, cancellation, invalid pages and a delayed HTTP PDF')
    parser.add_argument('--readiness-baseline', action='store_true', help='Record prior navigation behavior for the same readiness cases')
    parser.add_argument('--navigation', action='store_true', help='Check native centering, zoom, scrolling and repeated jumps; requires --delimiters')
    parser.add_argument('--navigation-baseline', action='store_true', help='Record the initial native position without centering assertions')
    parser.add_argument('--layouts', action='store_true', help='Check native rotation and two-page layouts; requires --delimiters')
    parser.add_argument('--layouts-baseline', action='store_true', help='Record the old rotated highlight without alignment assertions')
    parser.add_argument('--typography', action='store_true', help='Check source text fonts on SMC, VAE and the existing 2506.01320 paper, on both surfaces and native info cards')
    parser.add_argument('--native-copy', action='store_true', help='Also exercise native Copy LaTeX through real input; enables --typography')
    parser.add_argument('--unicode-math', action='store_true', help='Check reviewed Unicode math alphabets in Mamba; requires --pdf-text')
    parser.add_argument('--drawn-overlines', action='store_true', help='Check reviewed drawn overlines and external scripts; requires --pdf-text')
    parser.add_argument('--tex-toggles', action='store_true', help='Check selected Mamba source branches, provenance and excerpts on both surfaces')
    parser.add_argument('--completion', action='store_true', help='Check OCR crop selection, cancellation, and degraded-state recovery')
    parser.add_argument('--browser-channel', choices=['chromium','chrome'], default='chromium')
    args = parser.parse_args()
    if args.native_copy:
        args.typography = True
    if args.delimiters and not args.pdf_text:
        parser.error('--delimiters requires --pdf-text')
    if args.tex_toggles and args.pdf_text:
        parser.error('--tex-toggles requires source mode')
    if (args.accents or args.words or args.unicode_math or args.drawn_overlines) and not args.pdf_text:
        parser.error('--accents / --words / --unicode-math / --drawn-overlines requires --pdf-text')
    if (args.navigation or args.navigation_baseline or args.layouts or args.layouts_baseline) and not args.delimiters:
        parser.error('--navigation / --layouts and their baseline flags require --delimiters')
    args.out.mkdir(parents=True, exist_ok=True)
    spec = DEFAULT_PAPERS['attention']
    url = spec['pdf'].as_uri()
    search_query = r'\frac{QK^T}{\sqrt{d_k}}' if args.pdf_text else r'QK^T'
    if args.pdf_text:
        from backend.processing import prepare_pdf
        candidates, page_count = prepare_pdf(spec['pdf'].read_bytes())
    else:
        candidates = parse_tex_formulas(_merge_tex_files(_load_tex_files(spec['src'])))
        page_count = 0
    session = backend.SessionState(index_cache=build_index(candidates), pdf_bytes=spec['pdf'].read_bytes(),
                                   current_pdf_url=url, source_type='text' if args.pdf_text else 'tex', page_count=page_count)
    if not args.pdf_text:
        backend._set_lazy_bbox_state(session, candidates, build_line_index(session.pdf_bytes, candidates))
    backend._sessions[url] = session
    if args.tex_toggles:
        from tools.smoke_tex_toggle_checks import prepare_toggle_session
        toggle_paper = prepare_toggle_session()
    if args.typography:
        from tools.smoke_typography_checks import prepare_typography_sessions
        typography_papers = prepare_typography_sessions()
    if args.delimiters:
        delimiter_pdf = DEFAULT_PAPERS['smc_sd']['pdf']
        delimiter_url = delimiter_pdf.as_uri()
        delimiter_bytes = delimiter_pdf.read_bytes()
        delimiter_candidates, delimiter_pages = prepare_pdf(delimiter_bytes)
        backend._sessions[delimiter_url] = backend.SessionState(
            index_cache=build_index(delimiter_candidates), pdf_bytes=delimiter_bytes,
            current_pdf_url=delimiter_url, source_type='text', page_count=delimiter_pages)
    if args.layouts:
        import fitz
        derived_pdf = Path(tempfile.mkdtemp(prefix='formula-rotated-paper-'))/'smc-rotated.pdf'
        with fitz.open(stream=delimiter_bytes, filetype='pdf') as document:
            for page in document:
                page.set_rotation(90)
            document.save(derived_pdf)
        derived_bytes = derived_pdf.read_bytes()
        derived_candidates, derived_pages = prepare_pdf(derived_bytes)
        backend._sessions[derived_pdf.as_uri()] = backend.SessionState(
            index_cache=build_index(derived_candidates), pdf_bytes=derived_bytes,
            current_pdf_url=derived_pdf.as_uri(), source_type='text', page_count=derived_pages)
    if args.accents or args.words or args.unicode_math or args.drawn_overlines:
        from tools.audit_pdf_text import PROBES
        accent_paths = {paper: DEFAULT_PAPERS[paper]['pdf'] if paper in DEFAULT_PAPERS
                        else ROOT/f'testdata/benchmarks/artifacts/{paper}.pdf'
                        for paper in dict.fromkeys(p['paper'] for p in PROBES if
                            (p.get('drawn_overline') if args.drawn_overlines else p.get('unicode_math') if args.unicode_math else p.get('math_accents')
                             or args.words and (p.get('roman_words') or p.get('math_operators'))))}
        for path in accent_paths.values():
            pdf_bytes = path.read_bytes()
            accent_candidates, accent_pages = prepare_pdf(pdf_bytes)
            backend._sessions[path.as_uri()] = backend.SessionState(
                index_cache=build_index(accent_candidates), pdf_bytes=pdf_bytes,
                current_pdf_url=path.as_uri(), source_type='text', page_count=accent_pages)
    if args.readiness or args.readiness_baseline:
        from tools.smoke_readiness_checks import install_delayed_pdf
        delayed_pdf = install_delayed_pdf(backend.app, spec['pdf'].read_bytes())
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(backend.app, log_level='error'))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError('Temporary backend did not start')
        time.sleep(0.05)
    profile = tempfile.mkdtemp(prefix='formula-browser-')
    report = {'profile': profile, 'browser': '', 'checks': [], 'page_errors': [], 'pdf_text_only': args.pdf_text}
    try:
        with sync_playwright() as p:
            browser_args = ['--allow-file-access-from-files','--no-first-run']
            browser_args += (['--enable-unsafe-extension-debugging'] if args.browser_channel == 'chrome' else
                             [f'--disable-extensions-except={ROOT / "extension"}', f'--load-extension={ROOT / "extension"}'])
            context = p.chromium.launch_persistent_context(
                profile, channel=args.browser_channel, headless=True, viewport={'width': 1200, 'height': 900},
                ignore_default_args=['--disable-extensions','--disable-component-extensions-with-background-pages'], args=browser_args,
            )
            try:
                if args.browser_channel == 'chrome':
                    browser_session = context.browser.new_browser_cdp_session()
                    loaded = browser_session.send('Extensions.loadUnpacked',{'path':str(ROOT/'extension')})
                    # CDP installs default to file access off, unlike the
                    # Chromium CLI loader. Configure only this temporary profile
                    # just as a user enables "Allow access to file URLs".
                    manager = context.new_page()
                    manager.goto('chrome://extensions/')
                    manager.evaluate('async id => chrome.developerPrivate.updateExtensionConfiguration({extensionId:id,fileAccess:true})', loaded['id'])
                    manager.close()
                report['browser'] = context.browser.version
                workers = [worker for worker in context.service_workers if worker.url.endswith('/service-worker.js')]
                sw = workers[0] if workers else context.wait_for_event('serviceworker',predicate=lambda worker:worker.url.endswith('/service-worker.js'))
                ext_id = sw.url.split('/')[2]
                sw.evaluate('(url) => chrome.storage.local.set({fs_settings: {backendUrl: url}})', f'http://127.0.0.1:{port}')
                pdf_page = context.new_page()
                pdf_page.goto(url, wait_until='load')
                popup = context.new_page()
                popup.on('pageerror', lambda error: report['page_errors'].append(str(error)))
                report['console_warnings'] = []
                popup.on('console', lambda msg: report['console_warnings'].append(msg.text) if msg.type in ('warning', 'error') else None)
                popup.goto(f'chrome-extension://{ext_id}/popup/popup.html')
                pdf_page.bring_to_front()
                popup.reload(wait_until='load')
                popup.wait_for_function("() => !document.querySelector('#search-btn').disabled")
                assert popup.evaluate('currentSessionUrl') == url
                report['checks'].append('Popup reopened on existing real-paper session')
                popup.locator('#query').fill(search_query)
                popup.locator('#search-btn').click()
                popup.wait_for_selector('#results .result-item')
                matches = popup.evaluate('_matches')
                assert len(matches) == 1 and matches[0]['page'] == 3 and matches[0]['bbox']
                assert matches[0]['match_type'] == 'partial'
                if args.pdf_text:
                    assert matches[0]['source'] == 'text'
                    assert 'PDF text fragment' in popup.locator('#results').inner_text()
                assert popup.locator('#results .katex-error').count() == 0
                report['matches'] = matches
                report['checks'].append('Contains search renders the correct Attention equation and page')
                if not args.pdf_text:
                    from tools.smoke_source_checks import verify_source_reference
                    verify_source_reference(popup, pdf_page, context, args.out, report)
                popup.screenshot(path=str(args.out / 'search-light.png'))
                popup.locator('#search-mode').select_option('exact')
                popup.locator('#search-btn').click()
                popup.wait_for_function("() => document.querySelector('#status').textContent.startsWith('No matches')")
                assert popup.locator('#results .result-item').count() == 0
                report['checks'].append('Exact mode excludes a proper subexpression through real HTTP')
                # Reorder two actual HTTP responses deliberately. Even if an
                # aborted fetch has already completed, its held response must
                # not overwrite the newer query, cards, badge or export data.
                popup.evaluate('''() => {
                    window.__realFetch = window.fetch;
                    window.__holdNextSearch = true;
                    window.fetch = async (url, options) => {
                        const hold = String(url).endsWith('/search') && window.__holdNextSearch;
                        if (hold) window.__holdNextSearch = false;
                        const response = await window.__realFetch(url, options);
                        if (!hold) return response;
                        const body = await response.text();
                        return new Promise(resolve => { window.__releaseSearch = () => resolve(new Response(body, {status: 200})); });
                    };
                    document.querySelector('#query').value = 'd_k';
                    document.querySelector('#search-mode').value = 'contains';
                    window.__olderSearch = runSearch();
                }''')
                popup.wait_for_function('() => !!window.__releaseSearch')
                popup.locator('#query').fill(search_query)
                popup.locator('#search-btn').click()
                popup.wait_for_selector('#results .result-item')
                await_older = popup.evaluate('''async () => {
                    window.__releaseSearch();
                    await window.__olderSearch;
                    window.fetch = window.__realFetch;
                    return {matches: _matches, count: document.querySelectorAll('#results .result-item').length,
                            badge: await chrome.action.getBadgeText({tabId: _activeTab.id})};
                }''')
                assert len(await_older['matches']) == await_older['count'] == 1
                assert await_older['matches'][0]['latex'] == matches[0]['latex']
                assert await_older['badge'] == '1'
                report['checks'].append('An older response arriving last cannot replace the newer query, cards or badge')
                if args.pdf_text:
                    for wrong_query in (r'\frac{QK^T}{d_k}', r'QK^T\sqrt{d_k}', r'\sqrt{QK^T/d_k}'):
                        popup.locator('#query').fill(wrong_query)
                        popup.locator('#search-btn').click()
                        popup.wait_for_function("() => document.querySelector('#status').textContent.startsWith('No matches')")
                        assert not popup.evaluate('_matches.length')
                        report['checks'].append('Rejects incorrect reconstructed structure: ' + wrong_query)
                    popup.locator('#query').fill(search_query)
                popup.locator('#search-mode').select_option('contains')
                popup.locator('#search-btn').click()
                popup.wait_for_selector('#results .result-item')
                tab_id = find_pdf_tab_id(popup, spec['pdf'].name)
                popup.evaluate(_EVAL_HELPER_JS)
                if args.legacy_bridge:
                    old = subprocess.check_output(['git', '-c', f'safe.directory={ROOT.as_posix()}', 'show', '951018c:extension/popup/popup.js'], cwd=ROOT, text=True, encoding='utf-8')
                    begin = old.index('async function executeNativeJumpViaDebugger(')
                    end = old.index('\nasync function ensureNativeContentScript', begin)
                    popup.evaluate('() => { executeNativeJumpViaDebugger = ' + old[begin:end] + '; }')
                # The toolbar popup normally closes after opening the info card.
                # Keep this test tab alive for independent debugger readback.
                popup.evaluate('() => {window.close = () => {}}')
                popup.locator('#results .info-arrow-btn').first.click()
                popup.wait_for_function("() => !document.querySelector('#status').textContent.startsWith('Jumping')")
                if popup.evaluate('FormulaPdfNavigation.restrictedViewer()'):
                    excerpt = popup.locator('dialog[data-formula-excerpt]')
                    excerpt.wait_for(state='visible')
                    excerpt.locator('img').evaluate('(image) => image.decode()')
                    report['native_status'] = popup.locator('#status').inner_text()
                    assert 'Opened page 4.' in report['native_status']
                    report['navigation_url_before_wait'] = pdf_page.url
                    pdf_page.wait_for_url(lambda value:value.split('#')[0] == url and 'page=4' in value)
                    report['navigation_url_after_wait'] = pdf_page.url
                    assert pdf_page.url.split('#')[0] == url and 'page=4' in pdf_page.url
                    visible_page = None
                    page_deadline = time.monotonic()+15
                    while time.monotonic() < page_deadline:
                        for frame in pdf_page.frames:
                            if not frame.url.startswith('chrome-extension://mhjfbmdgcfjbbpaeojofohoefgiehjai'):
                                continue
                            try:
                                visible_page = frame.evaluate("() => {const viewer=document.querySelector('pdf-viewer'); return (viewer?.viewport || viewer?.viewport_)?.getMostVisiblePage?.()}")
                            except Exception:
                                pass
                        if visible_page == 3:
                            break
                        time.sleep(.1)
                    assert visible_page == 3, visible_page
                    excerpt.screenshot(path=str(args.out/'supported-formula-excerpt.png'))
                    pdf_page.screenshot(path=str(args.out/'supported-native-page.png'))
                    excerpt.get_by_role('button',name='Close').click()
                    report['checks'].append('Supported PDF page navigation reaches page 4 and shows a highlighted original-PDF excerpt; no internal viewer access')
                else:
                    deadline = time.monotonic() + 12
                    rects = {}
                    while time.monotonic() < deadline:
                        try:
                            rects = get_rects(popup, tab_id)
                        except Exception:
                            time.sleep(0.2)
                            continue
                        if rects.get('found'):
                            break
                        time.sleep(0.2)
                    report['native_rects'] = rects
                    report['native_status'] = popup.locator('#status').inner_text()
                    pdf_page.screenshot(path=str(args.out / 'native-jump.png'))
                    if not rects.get('found'):
                        report['native_diagnostic'] = popup.evaluate('''async (tabId) => {
                            const root = {tabId}, events = [], contexts = [], sessions = [root];
                            const listener = (source, method, params) => {
                                if (source.tabId !== tabId) return;
                                if (method === 'Runtime.executionContextCreated') contexts.push({source, context: params.context});
                                if (method === 'Target.attachedToTarget') {
                                    events.push({source, method, params});
                                    const child = {tabId, sessionId: params.sessionId};
                                    sessions.push(child);
                                    chrome.debugger.sendCommand(child, 'Runtime.enable').catch(() => {});
                                }
                            };
                            chrome.debugger.onEvent.addListener(listener);
                            await chrome.debugger.attach(root, '1.3');
                            try {
                                await chrome.debugger.sendCommand(root, 'Runtime.enable');
                                let filterError = '';
                                try {
                                    await chrome.debugger.sendCommand(root, 'Target.setAutoAttach', {autoAttach: true, waitForDebuggerOnStart: false, flatten: true, filter: [{type: 'iframe', exclude: false}]});
                                } catch (error) { filterError = String(error); }
                                await chrome.debugger.sendCommand(root, 'Target.setAutoAttach', {autoAttach: true, waitForDebuggerOnStart: false, flatten: true});
                                await new Promise(resolve => setTimeout(resolve, 400));
                                const views = [];
                                for (const item of contexts) {
                                    try {
                                        const raw = await chrome.debugger.sendCommand(item.source, 'Runtime.evaluate', {
                                            contextId: item.context.id, returnByValue: true,
                                            expression: '({url:location.href, viewer:!!document.querySelector("pdf-viewer"), html:document.body?.innerHTML.slice(0,300)})'
                                        });
                                        views.push({context: item.context.id, source: item.source, value: raw.result?.value, error: raw.exceptionDetails});
                                    } catch (error) {views.push({error: String(error)});}
                                }
                                return {filterError, targets: await chrome.debugger.getTargets(), events, contexts, views};
                            } finally {
                                await chrome.debugger.detach(root);
                                chrome.debugger.onEvent.removeListener(listener);
                            }
                        }''', tab_id)
                    assert rects.get('found'), f'Native result card missing: {report["native_status"]}'
                    assert pdf_page.url == url
                    report['checks'].append('Real native-PDF result click creates the card without changing the PDF URL')
                popup.emulate_media(color_scheme='dark')
                popup.screenshot(path=str(args.out / 'search-dark.png'))
                if args.delimiters:
                    pdf_page.goto(delimiter_url, wait_until='load')
                    pdf_page.bring_to_front()
                    popup.reload(wait_until='load')
                    popup.wait_for_function('(url) => currentSessionUrl === url && !document.querySelector("#search-btn").disabled', arg=delimiter_url)
                    popup.emulate_media(color_scheme='light')
                    popup.locator('#search-mode').select_option('exact')
                    for query, visible, expected_page in [(r'\{1,...,N\}', '{1,...,N}', 2), (r'\notin\mathcal{X}', '∉X', 1), ('∉'+r'\mathcal{X}', '∉X', 1)]:
                        popup.locator('#query').fill(query)
                        popup.locator('#search-btn').click()
                        popup.wait_for_function('() => !document.querySelector("#search-btn").disabled && _matches.length === 1')
                        result = popup.evaluate('_matches[0]')
                        assert result['page'] == expected_page and result['bbox']
                        assert popup.locator('#results .katex-error').count() == 0
                        glyph_text = popup.locator('#results .katex-mathml').evaluate_all('nodes => nodes.map(node => [...node.querySelectorAll("mi,mo,mn,mtext")].map(n => n.textContent).join(""))')
                        assert glyph_text == [visible], glyph_text
                        if expected_page == 1:
                            assert popup.locator('#results .katex-mathml mi[mathvariant="script"]').text_content() == 'X'
                        report['checks'].append('Actual popup preserves visible PDF notation: ' + query)
                        popup.screenshot(path=str(args.out / ('set-preview.png' if expected_page == 2 else 'negation-preview.png')))
                    for wrong_query in ('{1,...,N}', r'\notin X'):
                        popup.locator('#query').fill(wrong_query)
                        popup.locator('#search-btn').click()
                        popup.wait_for_function("() => document.querySelector('#status').textContent.startsWith('No matches')")
                        assert not popup.evaluate('_matches.length')
                        report['checks'].append('Actual popup rejects changed delimiter/font notation: ' + wrong_query)
                    popup.locator('#query').fill(r'\{1,...,N\}')
                    popup.locator('#search-btn').click()
                    popup.wait_for_selector('#results .result-item')
                    popup.evaluate(_EVAL_HELPER_JS)
                    popup.evaluate('() => {window.close = () => {}}')
                    popup.locator('#results .info-arrow-btn').first.click()
                    popup.wait_for_function("() => !document.querySelector('#status').textContent.startsWith('Jumping')")
                    tab_id = find_pdf_tab_id(popup, delimiter_pdf.name)
                    deadline = time.monotonic()+12
                    rects = {}
                    while time.monotonic() < deadline:
                        try:
                            rects = get_rects(popup, tab_id)
                        except Exception:
                            pass
                        if rects.get('found'):
                            break
                        time.sleep(.2)
                    assert rects.get('found'), popup.locator('#status').inner_text()
                    assert pdf_page.url == delimiter_url
                    report['set_native_rects'] = rects
                    pdf_page.screenshot(path=str(args.out/'set-native-jump.png'))
                    report['checks'].append('Native PDF navigation highlights the real set and keeps its braces')
                    if args.navigation or args.navigation_baseline:
                        from tools.smoke_navigation_checks import verify_navigation
                        verify_navigation(popup, pdf_page, tab_id, args.out, report, baseline=args.navigation_baseline)
                    if args.layouts or args.layouts_baseline:
                        from tools.smoke_layout_checks import verify_layouts
                        verify_layouts(popup, pdf_page, tab_id, delimiter_pdf, args.out, report, baseline=args.layouts_baseline)
                    if args.layouts:
                        import hashlib
                        report['upright_layouts'] = report.pop('layouts')
                        pdf_page.goto(derived_pdf.as_uri(), wait_until='load')
                        pdf_page.bring_to_front()
                        popup.reload(wait_until='load')
                        popup.wait_for_function('(url) => currentSessionUrl === url && !document.querySelector("#search-btn").disabled', arg=derived_pdf.as_uri())
                        popup.evaluate('() => {window.close = () => {}}')
                        rotated_out = args.out/'intrinsic-rotation'
                        rotated_out.mkdir(exist_ok=True)
                        report['derived_pdf'] = {'original_sha256': hashlib.sha256(delimiter_bytes).hexdigest(),
                            'derived_sha256': hashlib.sha256(derived_bytes).hexdigest(),
                            'transformation': 'Set /Rotate=90 on every page of a temporary SMC PDF copy; original file untouched'}
                        verify_layouts(popup, pdf_page, tab_id, derived_pdf, rotated_out, report)
                        report['intrinsic_layouts'] = report.pop('layouts')
                if args.readiness or args.readiness_baseline:
                    from tools.smoke_readiness_checks import verify_readiness
                    verify_readiness(popup, pdf_page, tab_id, url, args.out, report,
                                     f'http://127.0.0.1:{port}/__formula_audit__/delayed.pdf', delayed_pdf,
                                     baseline=args.readiness_baseline)
                if args.accents or args.words or args.unicode_math or args.drawn_overlines:
                    from tools.smoke_accent_checks import verify_accents
                    verify_accents(popup, pdf_page, accent_paths, args.out, report, include_words=args.words,
                                  unicode_math=args.unicode_math, drawn_overlines=args.drawn_overlines)
                if args.typography:
                    from tools.smoke_typography_checks import verify_typography
                    verify_typography(popup, pdf_page, typography_papers, args.out/'typography', report, args.native_copy)
                if args.tex_toggles:
                    from tools.smoke_tex_toggle_checks import verify_toggles
                    verify_toggles(popup, pdf_page, toggle_paper, args.out/'toggles', report)
                if args.completion:
                    from tools.smoke_completion_checks import verify_completion
                    verify_completion(popup, args.out/'completion', report,pdf_page)
                assert not report['page_errors'], report['page_errors']
                report['checks'].append('No popup JavaScript errors')
            finally:
                context.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        (args.out / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
