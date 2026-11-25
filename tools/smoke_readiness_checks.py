"""Native readiness checks using real Chrome PDF objects and a delayed HTTP PDF.

The geometry and load-state faults are controlled injections, not network
measurements. The streamed-paper check separately delays actual PDF bytes.
"""
import asyncio
import hashlib
import subprocess
import time

from fastapi.responses import StreamingResponse
from tools.smoke_accent_checks import capture_settled_pdf


def install_delayed_pdf(app, data):
    events = []

    @app.get('/__formula_audit__/delayed.pdf')
    async def delayed_pdf():
        event = {'started': time.monotonic()}
        events.append(event)

        async def chunks():
            yield data[:1024]
            await asyncio.sleep(2)
            event['released'] = time.monotonic()
            yield data[1024:]
        return StreamingResponse(chunks(), media_type='application/pdf',
                                 headers={'Content-Length': str(len(data)), 'Cache-Control': 'no-store'})
    return {'sha256': hashlib.sha256(data).hexdigest(), 'events': events}


def verify_readiness(popup, pdf_page, tab_id, url, out, report, delayed_url, delayed, baseline=False):
    results = {'method': __doc__, 'baseline': baseline, 'delayed_http': delayed}
    report['readiness'] = results
    if baseline:
        # Reproduce the previous production navigation logic in this isolated
        # profile; current backend and corpus remain identical.
        root = __import__('pathlib').Path(__file__).resolve().parents[1]
        old = subprocess.check_output(['git', '-c', f'safe.directory={root.as_posix()}', 'show',
                                       'ff92e7e:extension/popup/popup.js'], cwd=root, text=True, encoding='utf-8')
        start = old.index('function nativePdfInstantJump(')
        end = old.index('\nfunction normalizeInjectionResult', start)
        popup.evaluate('() => {nativePdfInstantJump = '+old[start:end]+';}')
        bridge = subprocess.check_output(['git', '-c', f'safe.directory={root.as_posix()}', 'show',
                                          'ff92e7e:extension/native-bridge.js'], cwd=root, text=True, encoding='utf-8')
        popup.evaluate('() => {'+bridge+'}')

    def native(expression):
        return popup.evaluate('''async ([tabId, expression]) => FormulaNativeBridge.withViewer(tabId, async evaluate => {
            const r = await evaluate(expression);
            if (r.exceptionDetails) throw Error(JSON.stringify(r.exceptionDetails));
            return r.result?.value;
        })''', [tab_id, expression])

    def jump(page=None, timeout=6000, abort_after=None):
        return popup.evaluate('''async ([tabId, url, page, timeoutMs, abortAfter]) => {
            const payload = matchMessage({..._matches[0], ...(page === null ? {} : {page})}, {showInfoPanel:true});
            payload.navigationUrl = url;
            const controller = new AbortController(), start = performance.now();
            const timer = abortAfter === null ? null : setTimeout(() => controller.abort(), abortAfter);
            try {
                const result = await executeNativeJumpViaDebugger(tabId, payload, {url, timeoutMs, signal:controller.signal});
                return {result, elapsed_ms:performance.now()-start};
            } finally {clearTimeout(timer);}
        }''', [tab_id, url, page, timeout, abort_after])

    def block_geometry(ms):
        return native('''(() => {
            const v = document.querySelector('pdf-viewer'), vp = v.viewport;
            const original = vp.getPageScreenRect, originalScroll = vp.scrollTo;
            const until = performance.now()+MILLIS;
            window.__fsReadiness = {blockedUntil:until, scrolls:0, geometryReads:0};
            vp.getPageScreenRect = function(...args) {
                window.__fsReadiness.geometryReads++;
                if (performance.now()<until) throw Error('controlled pending geometry');
                return original.apply(this,args);
            };
            vp.scrollTo = function(...args) {window.__fsReadiness.scrolls++; return originalScroll.apply(this,args);};
            setTimeout(() => {vp.getPageScreenRect=original; vp.scrollTo=originalScroll;}, MILLIS+1500);
            return {loadState:v.loadState_, originalUrl:v.originalUrl, pageCount:v.documentDimensions.pageDimensions.length};
        })()'''.replace('MILLIS', str(ms)))

    def wait_fault_end():
        # Readiness guards intentionally block ordinary native readback until
        # geometry settles; this is not another navigation attempt.
        return native('({test:window.__fsReadiness, page:document.querySelector("pdf-viewer").viewport.getMostVisiblePage()})')

    results['initial'] = native('''(() => {
        const v=document.querySelector('pdf-viewer');
        return {loadState:v.loadState_, originalUrl:v.originalUrl, pages:v.documentDimensions.pageDimensions.length};
    })()''')
    results['invalid_page'] = jump(1000)
    if not baseline:
        assert results['invalid_page']['result']['code'] == 'page-unavailable', results
        assert not results['invalid_page']['result']['ok']
        report['checks'].append('Unavailable PDF page fails before navigation instead of clamping to a different page')
    jump()  # reset after the baseline's invalid navigation, outside the tested action

    block_geometry(1200)
    results['delayed_geometry'] = jump()
    if not baseline:
        assert results['delayed_geometry']['result']['ok'], results
        assert results['delayed_geometry']['elapsed_ms'] >= 900, results
        results['delayed_geometry']['after'] = wait_fault_end()
        assert results['delayed_geometry']['after']['test']['scrolls'] == 1, results
        report['checks'].append('One result action waits for geometry and scrolls once after readiness')
    else:
        popup.wait_for_timeout(2800)
    capture_settled_pdf(pdf_page, out, 'delayed-geometry')
    popup.wait_for_timeout(1600)  # restore the instrumentation before the next independent case

    block_geometry(1200)
    results['cancelled_wait'] = jump(abort_after=120)
    if not baseline:
        assert results['cancelled_wait']['result']['code'] == 'cancelled', results
        results['cancelled_wait']['after'] = wait_fault_end()
        assert results['cancelled_wait']['after']['test']['scrolls'] == 0, results
        report['checks'].append('Cancelling a pending request performs no scroll')
    popup.wait_for_timeout(2800)

    block_geometry(1200)
    results['rapid_clicks'] = popup.evaluate('''async () => {
        const first = sendScrollToMatch(_matches[0], {showInfoPanel:true});
        await new Promise(resolve => setTimeout(resolve,100));
        const second = sendScrollToMatch(_matches[0], {showInfoPanel:true});
        await Promise.all([first,second]);
        return {status:document.querySelector('#status').textContent};
    }''')
    if not baseline:
        results['rapid_clicks']['after'] = wait_fault_end()
        assert results['rapid_clicks']['after']['test']['scrolls'] == 1, results
        assert results['rapid_clicks']['status'].startswith('Highlighted'), results
        report['checks'].append('A newer result click supersedes the pending older click; only one scroll executes')
    popup.wait_for_timeout(2800)

    # Actual slow HTTP delivery: starts with a PDF header, holds the remaining
    # bytes for two seconds, and records server and action timing independently.
    results['streamed_papers'] = []
    for stream_attempt in range(3):
        with popup.context.expect_page() as created_page:
            tab = popup.evaluate('(url) => chrome.tabs.create({url,active:false})', delayed_url)
        stream_page = created_page.value
        popup.wait_for_function('''async ([id,url]) => (await chrome.tabs.get(id)).url === url''', arg=[tab['id'], delayed_url])
        started = time.monotonic()
        stream_result = popup.evaluate('''async ([tabId,url]) => {
            const payload=matchMessage(_matches[0],{showInfoPanel:true}); payload.navigationUrl=url;
            const start=performance.now();
            const events=[];
            const listener=(source,method,params) => {
                if(source.tabId===tabId && ['Runtime.executionContextCreated','Runtime.executionContextDestroyed',
                    'Runtime.executionContextsCleared','Target.attachedToTarget','Target.detachedFromTarget'].includes(method)) {
                    events.push({source,method,params});
                }
            };
            const detached=(source,reason) => {if(source.tabId===tabId) events.push({source,reason});};
            chrome.debugger.onEvent.addListener(listener); chrome.debugger.onDetach.addListener(detached);
            try {
                const result=await executeNativeJumpViaDebugger(tabId,payload,{url});
                return {result,elapsed_ms:performance.now()-start,events,
                        targets:(await chrome.debugger.getTargets()).filter(t=>t.tabId===tabId || t.type==='iframe')};
            } finally {chrome.debugger.onEvent.removeListener(listener);chrome.debugger.onDetach.removeListener(detached);}
        }''', [tab['id'], delayed_url])
        stream_result['started'] = started
        results['streamed_papers'].append(stream_result)
        if not baseline:
            assert stream_result['result']['ok'], results
            assert delayed['events'] and started < delayed['events'][-1]['released'], results
            report['checks'].append('An action initiated before real delayed PDF bytes finish waits and navigates successfully')
        popup.wait_for_timeout(2300 if baseline else 500)
        capture_settled_pdf(stream_page, out, f'streamed-paper-{stream_attempt+1}')
        stream_page.close()

    # Verify a closed tab and changed URL with fresh real tabs, while keeping
    # the original test PDF available for the remaining regression suite.
    for action in ('close', 'navigate'):
        with popup.context.expect_page() as created_page:
            tab = popup.evaluate('(url) => chrome.tabs.create({url,active:false})', url)
        extra = created_page.value
        # Finish Playwright's own initial target attachment before attaching
        # the extension debugger for a controlled loading-state fixture.
        extra.wait_for_load_state('load')
        extra.evaluate('document.readyState')
        result = popup.evaluate('''async ([id,url,action]) => {
            try {
                await FormulaNativeBridge.withViewer(id, async evaluate => evaluate(`(() => {
                    const v=document.querySelector('pdf-viewer'); v.loadState_='loading'; return true;
                })()`));
            } catch(error) {return {preparationError:String(error),code:error.code,detachReason:error.detachReason};}
            const timer=setTimeout(() => {
                if(action==='close') chrome.tabs.remove(id);
                else chrome.tabs.update(id,{url:'about:blank'});
            },120);
            try {
                const payload=matchMessage(_matches[0]); payload.navigationUrl=url;
                return await executeNativeJumpViaDebugger(id,payload,{url});
            } finally {clearTimeout(timer);}
        }''', [tab['id'], url, action])
        results[action] = result
        if not baseline:
            assert not result['ok'], results
            assert result['code'] in ('tab-closed', 'viewer-detached', 'document-changed'), results
            report['checks'].append('Pending navigation stops when the target tab '+('closes' if action == 'close' else 'changes document'))
        if not extra.is_closed():
            extra.close()
