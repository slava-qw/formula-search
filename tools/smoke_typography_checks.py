"""Live source typography checks on SMC, VAE and the existing 2506.01320 PDF.

Fresh extraction, actual HTTP search, popup/panel previews, bookmark previews,
clipboard/export preservation and native info cards. Native mappings are
pre-existing heuristics: these checks validate preview typography and document
identity, not the accuracy of every returned rectangle.
"""
import gzip
import hashlib
import json
from pathlib import Path

from backend import main as backend
from backend.extractor import arxiv_tex
from backend.matcher.index import build_index
from tools.audit_formula_mapping import DEFAULT_PAPERS
from tools.smoke_accent_checks import capture_settled_pdf

ROOT=Path(__file__).resolve().parents[1]


def prepare_typography_sessions():
    result={}
    for name in ['smc_sd','2506.01320','vae_tutorial']:
        pdf_path=DEFAULT_PAPERS[name]['pdf'] if name in DEFAULT_PAPERS else ROOT/'testdata/papers/2506.01320.pdf'
        source_path=DEFAULT_PAPERS[name]['src'] if name in DEFAULT_PAPERS else ROOT/'testdata/papers/arXiv-2506.01320.tar.gz'
        pdf,source=pdf_path.read_bytes(),source_path.read_bytes()
        snapshot=next(json.loads(gzip.decompress(p.read_bytes())) for p in (ROOT/'testing/audits/source-scoring-2026-11-22/corpus').glob('*.json.gz')
                      if json.loads(gzip.decompress(p.read_bytes()))['paper']==name)
        assert hashlib.sha256(pdf).hexdigest()==snapshot['hashes']['pdf']
        assert hashlib.sha256(source).hexdigest()==snapshot['hashes']['source']
        candidates=arxiv_tex.parse_tex_formulas(arxiv_tex._unpack_tex_source(source))
        session=backend.SessionState(index_cache=build_index(candidates),pdf_bytes=pdf,
            current_pdf_url=pdf_path.as_uri(),source_type='tex',generation=1)
        backend._set_lazy_bbox_state(session,candidates,arxiv_tex.build_line_index(pdf,candidates))
        backend._sessions[pdf_path.as_uri()]=session
        result[name]={'url':pdf_path.as_uri(),'pdf_sha256':snapshot['hashes']['pdf'],
                      'source_sha256':snapshot['hashes']['source']}
    return result


def verify_typography(popup,paper,papers,out,report,native_copy=False):
    out.mkdir(parents=True,exist_ok=True)
    report['typography_method']=__doc__
    report['typography_artifacts']=papers
    extension_origin=popup.url.split('/popup/')[0]
    context=popup.context
    context.grant_permissions(['clipboard-read','clipboard-write'])
    def record(surface,case,**data):
        report['checks'].append({'surface':surface,'case':case,**data})
    for surface,suffix,width in [('popup','',420),('panel','?surface=sidepanel',320)]:
        view=popup if surface=='popup' else context.new_page()
        if view!=popup:
            view.on('pageerror',lambda error:report['page_errors'].append(str(error)))
        view.set_viewport_size({'width':width,'height':880})
        for name,cases in [('smc_sd',[(r'\textrm{pow}',r'\textrm','pow',None),
                                      (r'\rho',r'\textnormal','def',None)]),
                           ('2506.01320',[(r'\textbf{M}',r'\textbf','M','bold')]),
                           ('vae_tutorial',[(r'\mbox{Bernoulli}',r'\mbox','Bernoulli',None)])]:
            url=papers[name]['url']
            if paper.url!=url:
                paper.goto(url,wait_until='load')
            paper.bring_to_front()
            if view==popup:
                view.evaluate('()=>chrome.storage.session.remove("formulaSearchLastQuery")')
            view.goto(extension_origin+'/popup/popup.html'+suffix)
            view.wait_for_function('()=>!queryInput.disabled')
            view.bring_to_front()
            view.evaluate('''async url=>{
                await chrome.storage.session.remove('formulaSearchLastQuery');
                const tab=(await chrome.tabs.query({})).find(t=>t.url===url);
                await init(tab,{restoreQuery:false});
                await chrome.storage.local.set({fs_bookmarks:[]});
            }''',url)
            for query,command,text,variant in cases:
                view.locator('#query').fill(query)
                with view.expect_response(lambda r:r.url.endswith('/search')) as response:
                    view.locator('#search-btn').click()
                expected=response.value.json()['matches']
                view.wait_for_function('()=>!statusEl.textContent.startsWith("Searching")')
                actual=view.evaluate('resultFilter.all.map(({source_url,resolved_source_url,...m})=>m)')
                assert actual==[{k:v for k,v in m.items() if k not in ('source_url','resolved_source_url')} for m in expected]
                index=next(i for i,m in enumerate(actual) if command in m['latex'])
                match=view.evaluate('(i)=>_matches[i]',index)
                card=view.locator('#results .result-item').nth(index)
                card.scroll_into_view_if_needed()
                card.locator('.formula-preview').focus()
                def check_text(locator):
                    assert locator.locator('.katex-error').count()==0
                    words=locator.locator('.katex-mathml mtext').evaluate_all('(items)=>items.map(el=>({text:el.textContent,variant:el.getAttribute("mathvariant")}))')
                    assert any(word['text'].strip()==text and word['variant']==variant for word in words),words
                check_text(card)
                payload=view.evaluate('(m)=>matchMessage(m,{showInfoPanel:true})',match)
                assert command in payload['infoPanel']['previewLatex']
                record(surface,'Actual HTTP result preserves source text typography',paper=name,query=query,match=match)
                for theme in ['light','dark']:
                    view.evaluate('(theme)=>applyTheme(theme)',theme)
                    card.scroll_into_view_if_needed()
                    assert view.evaluate('document.documentElement.scrollWidth<=innerWidth')
                    view.screenshot(path=str(out/f'{surface}-{name}-{text}-{theme}.png'))
                record(surface,'Light and dark source previews fit the viewport',paper=name,query=query)
                card.locator('.result-copy-btn').click()
                view.wait_for_function('()=>[...resultsList.querySelectorAll(".result-copy-btn")].some(b=>b.textContent==="✓")')
                copied=view.evaluate('navigator.clipboard.readText()')
                assert command in copied and match['latex'] in copied
                assert view.evaluate('JSON.parse(buildExportPayload(resultFilter.all))')==view.evaluate('resultFilter.all')
                record(surface,'Copy and export keep the original source notation',paper=name,query=query)
                view.evaluate('async()=>{await chrome.storage.local.set({fs_bookmarks:[]}); await refreshBookmarkStars();}')
                card.locator('.bookmark-star-btn').click()
                view.wait_for_function('async()=> (await loadBookmarks()).length===1')
                view.locator('#bookmarks-toggle-btn').click()
                view.wait_for_selector('#bookmarks-list .result-item')
                check_text(view.locator('#bookmarks-list .result-item'))
                assert view.evaluate('(async()=> (await loadBookmarks())[0].latex)()')==match['latex']
                view.locator('#bookmarks-toggle-btn').click()
                record(surface,'Saved bookmark preview keeps the text style and raw formula',paper=name,query=query)
                if surface=='popup':
                    view.evaluate('async m=>{await sendScrollToMatch(m,{showInfoPanel:true});}',match)
                    state=view.evaluate('''async()=>FormulaNativeBridge.withViewer(_activeTab.id,async evaluate=>{
                        const response=await evaluate(`JSON.stringify({
                            cards:document.querySelectorAll('[data-formula-search-info-panel]').length,
                            errors:document.querySelectorAll('.fs-info-card-formula .katex-error').length,
                            words:[...document.querySelectorAll('[data-formula-search-info-panel] .katex-mathml mtext')].map(el=>({text:el.textContent,variant:el.getAttribute('mathvariant')}))
                        })`);
                        if(response.exceptionDetails) throw Error(JSON.stringify(response.exceptionDetails));
                        return JSON.parse(response.result.value);
                    },{url:_activeTab.url})''')
                    assert state['cards']==1 and state['errors']==0,state
                    assert any(w['text'].strip()==text and w['variant']==variant for w in state['words']),state
                    paper.bring_to_front()
                    pixels=capture_settled_pdf(paper,out,f'{name}-{text}')
                    assert paper.url==url
                    view.bring_to_front()
                    record(surface,'Native info card keeps text typography and the PDF URL',paper=name,query=query,state=state,pixels=pixels)
                    if native_copy:
                        from tools.smoke_native_copy_checks import verify_native_copy
                        verify_native_copy(view,paper,match,out,report,f'{name}-{text}',exercise_failure=name=='smc_sd' and text=='pow')
        if view!=popup:
            view.close()
