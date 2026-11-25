"""Actual source HTTP searches and excerpts from selected Mamba branches.

Both surfaces run as isolated extension page URLs. These checks validate source
occurrences and UI integration, not PDF-location accuracy of the new queries.
The parent smoke also checks the existing Attention native PDF jump.
"""
from collections import Counter
import hashlib
import json
from pathlib import Path

from backend import main as backend
from backend.extractor import arxiv_tex as tex
from backend.matcher.index import build_index
from backend.matcher.fuzzy import search_formulas

ROOT = Path(__file__).resolve().parents[1]
QUERIES = [r'g_t', r'2^6=64', r'N=16', r'\overline{\boldsymbol{A}} = \exp(\Delta \boldsymbol{A})']


def prepare_toggle_session():
    spec = json.loads((ROOT/'testdata/benchmarks/manifest.json').read_text())['papers']['2312.00752v2']
    data, hashes = {}, {}
    for kind, artifact in spec['artifacts'].items():
        data[kind] = (ROOT/artifact['path']).read_bytes()
        hashes[kind] = hashlib.sha256(data[kind]).hexdigest()
        assert hashes[kind] == artifact['sha256']
    path = ROOT/spec['artifacts']['pdf']['path']
    candidates = tex.parse_tex_formulas(tex._unpack_tex_source(data['source']))
    index = build_index(candidates)
    expected = {q: sorted(m.occurrence_id for m in search_formulas(q, index)) for q in QUERIES}
    session = backend.SessionState(index_cache=index, pdf_bytes=data['pdf'], current_pdf_url=path.as_uri(),
                                   source_type='tex', generation=1)
    backend._set_lazy_bbox_state(session, candidates, tex.build_line_index(data['pdf'], candidates))
    backend._sessions[path.as_uri()] = session
    return {'url': path.as_uri(), 'hashes': hashes, 'expected_ids': expected, 'candidates': len(candidates)}


def verify_toggles(popup, paper, spec, out, report):
    out.mkdir(parents=True, exist_ok=True)
    report['toggle_method'] = __doc__
    report['toggle_paper'] = spec
    report['toggle_results'] = []
    origin = popup.url.split('/popup/')[0]
    context = popup.context
    context.grant_permissions(['clipboard-read', 'clipboard-write'])
    paper.goto(spec['url'], wait_until='load')
    for surface, width, suffix in [('popup', 420, ''), ('panel', 320, '?surface=sidepanel')]:
        view = popup if surface == 'popup' else context.new_page()
        if view != popup:
            view.on('pageerror', lambda error: report['page_errors'].append(str(error)))
        view.set_viewport_size({'width': width, 'height': 900})
        paper.bring_to_front()
        view.goto(origin+'/popup/popup.html'+suffix)
        view.wait_for_function('() => !queryInput.disabled')
        view.bring_to_front()
        view.evaluate('''async url => {
            await chrome.storage.session.remove('formulaSearchLastQuery');
            const tab=(await chrome.tabs.query({})).find(t=>t.url===url);
            await init(tab,{restoreQuery:false});
        }''', spec['url'])
        view.locator('#search-mode').select_option('contains')
        for number, query in enumerate(QUERIES):
            view.locator('#query').fill(query)
            with view.expect_response(lambda r: r.url.endswith('/search')) as response:
                view.locator('#search-btn').click()
            body = response.value.json()
            view.wait_for_function('() => !statusEl.textContent.startsWith("Searching")')
            matches = view.evaluate('resultFilter.all')
            assert sorted(m['occurrence_id'] for m in matches) == spec['expected_ids'][query]
            assert [m['occurrence_id'] for m in matches] == [m['occurrence_id'] for m in body['matches']]
            refs = Counter((m['source_file'], m['source_line']) for m in matches)
            inactive = {('src/background.tex', 79), ('src/background.tex', 80),
                        ('src/method.tex', 290), ('src/experiments.tex', 29)}
            assert not (refs.keys() & inactive)
            target = [('src/method.tex', 281), ('src/synthetic.tex', 31),
                      ('src/benchmarks.tex', 5), ('src/background.tex', 71)][number]
            assert refs[target] == 1, (query, refs)
            i = next(i for i, m in enumerate(matches) if (m['source_file'], m['source_line']) == target)
            match = matches[i]
            card = view.locator('#results .result-item').nth(i)
            card.locator('.formula-preview').focus()
            assert not card.locator('.katex-error').count()
            reference = f'{target[0]}:{target[1]}'
            assert card.locator('.source-reference').inner_text() == reference
            card.locator('.source-reference').click()
            view.wait_for_function('() => [...document.querySelectorAll("#results .source-reference")].some(el=>el.textContent.startsWith("Copied"))')
            assert view.evaluate('navigator.clipboard.readText()') == reference
            exported = view.evaluate('JSON.parse(buildExportPayload(resultFilter.all))')
            assert exported == matches
            report['checks'].append({'surface': surface, 'case': 'HTTP search/export/copy retain active source occurrences',
                                     'query': query, 'count': len(matches), 'reference': reference})
            card.locator('.source-context summary').click()
            card.locator('.source-context-excerpt').wait_for(state='visible')
            state = backend._get_session(spec['url'])
            candidate = next(c for c, idx in zip(state.candidates_cache, state.index_cache)
                             if idx.occurrence_id == match['occurrence_id'])
            excerpt = card.locator('.source-context-excerpt').text_content()
            assert excerpt == candidate.tex_context_before[-420:] + match['latex'] + candidate.tex_context_after[:420]
            assert card.locator('.source-context-excerpt mark').text_content() == match['latex']
            assert r'\iftoggle' not in excerpt
            report['checks'].append({'surface': surface, 'case': 'Lazy excerpt uses selected source context', 'query': query})
            report['toggle_results'].append({'surface': surface, 'query': query, 'matches': matches, 'excerpt': excerpt})
            if number == 0:
                for theme in ['light', 'dark']:
                    view.evaluate('(theme) => applyTheme(theme)', theme)
                    card.locator('.source-context-body').scroll_into_view_if_needed()
                    assert view.evaluate('document.documentElement.scrollWidth <= innerWidth')
                    view.screenshot(path=str(out/f'{surface}-{theme}.png'))
                report['checks'].append({'surface': surface, 'case': 'Selected source preview and excerpt fit both themes'})
        if view != popup:
            view.close()
