"""Capture the real Chrome extension on the original example PDF.

No API responses, candidates, searches, or recognition are mocked. The panel is
opened as an extension tab with the panel URL for reproducible recording. This
does not demonstrate a toolbar/side-panel opening gesture. No OCR is shown.
Requires installed Chrome and Playwright. Use a new --work directory each time.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def write_captions(report, output):
    def stamp(seconds):
        ms=round(seconds*1000)
        return f'{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02}.{ms%1000:03}'
    captions=['WEBVTT','']
    steps=report['steps']
    for i,step in enumerate(steps):
        end=steps[i+1]['capture_seconds'] if i+1<len(steps) else step['capture_seconds']+2.3
        captions.extend([f'{stamp(step["capture_seconds"])} --> {stamp(end)}',step['name'],''])
    (output/'demo.vtt').write_text('\n'.join(captions),encoding='utf-8',newline='\n')


def capture(work: Path, output: Path, ffmpeg: str | None, navigation_repeats=0, scale=1):
    work = work.resolve()
    work.mkdir(parents=True, exist_ok=False)
    output.mkdir(parents=True, exist_ok=True)
    os.environ['FORMULA_SEARCH_CACHE_DIR'] = str(work/'cache')
    os.environ['TEMP'] = os.environ['TMP'] = str(work)
    sys.path.insert(0, str(ROOT))
    import socket
    import threading
    import uvicorn
    from playwright.sync_api import sync_playwright
    from backend.main import app

    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
    thread.start()
    deadline = time.monotonic()+15
    while not server.started:
        if time.monotonic() > deadline:
            raise TimeoutError('Demo backend did not start')
        time.sleep(.05)
    report = {'capture': __doc__, 'browser': None, 'steps': [], 'page_errors': [],
              'network_errors': [], 'recognition': 'not demonstrated',
              'timing': 'Recorded live, including startup. No speed changes or substituted product frames.'}
    report['extension_version'] = json.loads((ROOT/'extension/manifest.json').read_text())['version']
    report['still_pixel_density'] = scale
    report['video_dimensions'] = {'width':420,'height':860}
    report['backend_dependencies'] = 'requirements-core-lock.txt'
    video = None
    try:
        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(str(work/'profile'), channel='chrome',
                headless=True, viewport={'width':420,'height':860}, color_scheme='light', device_scale_factor=scale,
                # Screencast frames from an inactive Chrome tab can be limited
                # to CSS pixels even at DPR 2. A larger video canvas pads those
                # frames; keep its size stable while still images retain DPR 2.
                record_video_dir=str(work/'videos'), record_video_size={'width':420,'height':860},
                ignore_default_args=['--disable-extensions','--disable-component-extensions-with-background-pages'],
                args=['--allow-file-access-from-files','--no-first-run','--enable-unsafe-extension-debugging'])
            try:
                report['browser'] = context.browser.version
                cdp = context.browser.new_browser_cdp_session()
                loaded = cdp.send('Extensions.loadUnpacked', {'path':str(ROOT/'extension')})
                manager = context.new_page()
                manager.goto('chrome://extensions/')
                manager.evaluate('async id => chrome.developerPrivate.updateExtensionConfiguration({extensionId:id,fileAccess:true})', loaded['id'])
                manager.close()
                workers = [w for w in context.service_workers if w.url.endswith('/service-worker.js')]
                worker = workers[0] if workers else context.wait_for_event('serviceworker',predicate=lambda w:w.url.endswith('/service-worker.js'))
                worker.evaluate('(backendUrl) => chrome.storage.local.set({fs_settings:{backendUrl,theme:"light"}})',
                    f'http://127.0.0.1:{listener.getsockname()[1]}')
                pdf = context.new_page()
                pdf.set_viewport_size({'width':860,'height':1000})
                pdf.goto((ROOT/'examples/formula-playground.pdf').as_uri())
                capture_started = time.monotonic()
                panel = context.new_page()
                video = panel.video
                panel.on('pageerror', lambda error: report['page_errors'].append(str(error)))
                panel.on('response', lambda response: report['network_errors'].append({'status':response.status,'path':response.url.split('/')[-1]})
                         if response.status >= 400 and '/127.0.0.1:' in response.url else None)
                pdf.bring_to_front()
                panel.goto(f'chrome-extension://{loaded["id"]}/popup/popup.html?surface=sidepanel')
                panel.wait_for_function("() => !document.querySelector('#search-btn').disabled",timeout=60000)
                assert panel.evaluate('currentSessionUrl').endswith('formula-playground.pdf')
                assert panel.evaluate('document.body.dataset.surface') == 'panel'
                def mark(name):
                    report['steps'].append({'name':name,'capture_seconds':round(time.monotonic()-capture_started,3)})
                mark('Ready: real PDF prepared')
                panel.wait_for_timeout(1200)
                panel.locator('#query').press_sequentially('QK^T',delay=160)
                panel.locator('#search-btn').click()
                panel.wait_for_selector('#results .result-item')
                matches = panel.evaluate('_matches')
                assert len(matches) == 1 and matches[0]['page'] == 0 and matches[0]['bbox']
                assert matches[0]['source'] == 'text'
                mark('Contains search: matrix numerator')
                panel.locator('#query').blur()
                panel.screenshot(path=str(output/'search-light.png'))
                panel.locator('#results').screenshot(path=str(output/'numerator-results.png'))
                pdf.screenshot(path=str(output/'native-pdf-page1.png'))
                panel.locator('#results .info-arrow-btn').click()
                numerator_dialog = panel.locator('dialog[data-formula-excerpt]')
                numerator_dialog.wait_for(state='visible')
                numerator_dialog.locator('img').evaluate('(img) => img.decode()')
                panel.screenshot(path=str(output/'numerator-excerpt.png'))
                numerator_dialog.locator('img').screenshot(path=str(output/'numerator-crop.png'))
                numerator_dialog.get_by_role('button',name='Close',exact=True).click()
                panel.wait_for_timeout(3200)
                mark('Enter squared weight query')
                panel.locator('#query').fill('w^2')
                panel.locator('#search-btn').click()
                panel.wait_for_function('() => _matches.length === 2')
                assert sorted(m['page'] for m in panel.evaluate('_matches')) == [0,1]
                mark('Squared weight: two real occurrences')
                panel.locator('#query').blur()
                panel.screenshot(path=str(output/'two-pages.png'))
                panel.locator('#results').screenshot(path=str(output/'two-page-results.png'))
                panel.wait_for_timeout(2600)
                options = panel.locator('#result-page option').evaluate_all('(options) => options.map(o=>({value:o.value,text:o.textContent}))')
                page2 = next(o['value'] for o in options if o['text'].strip().startswith('Page 2'))
                panel.locator('#result-page').select_option(page2)
                panel.wait_for_function("() => document.querySelectorAll('#results .result-item').length === 1")
                mark('Page filter: second occurrence')
                panel.screenshot(path=str(output/'filtered-page.png'))
                panel.wait_for_timeout(2400)
                panel.locator('#results .bookmark-star-btn').click()
                panel.wait_for_function('async () => (await loadBookmarks()).length === 1')
                panel.locator('#bookmarks-toggle-btn').click()
                panel.wait_for_selector('#bookmarks-list li')
                mark('Saved occurrence: bookmark')
                panel.screenshot(path=str(output/'bookmarks.png'))
                panel.locator('#bookmarks-section').screenshot(path=str(output/'bookmark-detail.png')) if panel.locator('#bookmarks-section').count() else None
                panel.wait_for_timeout(2600)
                panel.locator('#bookmarks-toggle-btn').click()
                panel.locator('#result-page').select_option(page2)
                panel.wait_for_function("() => document.querySelectorAll('#results .result-item').length === 1")
                panel.locator('#results .info-arrow-btn').click()
                dialog = panel.locator('dialog[data-formula-excerpt]')
                dialog.wait_for(state='visible')
                dialog.locator('img').evaluate('(img) => img.decode()')
                report['navigation'] = {'before_wait':pdf.url.rsplit('/',1)[-1],'dialog':dialog.locator('h3').inner_text(),
                    'filter':panel.locator('#result-page').input_value(),
                    'status':panel.locator('#status').inner_text()}
                def settled_native_page(expected):
                    # Automation-only readback; product code does not access the
                    # restricted PDF frame. Page.url can lag the browser tab URL.
                    deadline=time.monotonic()+15
                    state={}
                    while time.monotonic()<deadline:
                        state=panel.evaluate('''async () => {
                            const tab=await chrome.tabs.get(_activeTab.id);
                            return {url:tab.url.split('/').at(-1),pending:tab.pendingUrl?.split('/').at(-1),status:tab.status};
                        }''')
                        visible=None
                        for frame in pdf.frames:
                            if frame.url.startswith('chrome-extension://mhjfbmdgcfjbbpaeojofohoefgiehjai'):
                                try:
                                    visible=frame.evaluate("() => {const v=document.querySelector('pdf-viewer');return(v?.viewport||v?.viewport_)?.getMostVisiblePage?.()}")
                                except Exception:
                                    pass
                        state['visible_pdf_page_zero_based']=visible
                        state['playwright_url']=pdf.url.rsplit('/',1)[-1]
                        if f'page={expected}' in state['url'] and visible==expected-1 and state['status']=='complete':
                            return state
                        time.sleep(.1)
                    raise AssertionError(f'Native PDF did not settle on page {expected}: {state}')
                report['navigation']['verified_state']=settled_native_page(2)
                mark('Original PDF excerpt and page navigation')
                panel.screenshot(path=str(output/'pdf-excerpt.png'))
                dialog.locator('img').screenshot(path=str(output/'weight-crop.png'))
                pdf.screenshot(path=str(output/'native-pdf.png'))
                panel.wait_for_timeout(3500)
                dialog.get_by_role('button',name='Close',exact=True).click()
                if navigation_repeats:
                    panel.locator('#result-page').select_option('all')
                    report['repeated_navigation']=[]
                    for iteration in range(navigation_repeats):
                        expected=iteration%2+1
                        panel.locator('#results .info-arrow-btn').nth(expected-1).click()
                        repeated_dialog=panel.locator('dialog[data-formula-excerpt]')
                        repeated_dialog.wait_for(state='visible')
                        report['repeated_navigation'].append(settled_native_page(expected))
                        repeated_dialog.get_by_role('button',name='Close',exact=True).click()
                options_page = context.new_page()
                options_page.goto(f'chrome-extension://{loaded["id"]}/options/options.html')
                options_page.locator('#theme').select_option('dark')
                options_page.locator('#save-btn').click()
                options_page.wait_for_function("() => document.querySelector('#save-status').textContent.includes('Saved')")
                options_page.close()
                pdf.bring_to_front()
                panel.reload()
                panel.wait_for_function("() => document.documentElement.dataset.theme === 'dark'")
                panel.wait_for_function("() => !document.querySelector('#search-btn').disabled")
                panel.locator('#query').fill('w^2')
                panel.locator('#search-btn').click()
                panel.wait_for_function('() => _matches.length === 2')
                panel.locator('#query').blur()
                mark('Dark appearance')
                panel.screenshot(path=str(output/'search-dark.png'))
                panel.wait_for_timeout(2500)
                assert not report['page_errors'] and not report['network_errors'], report
                report['checks'] = ['Actual PDF upload/preparation via extension', 'Unmocked HTTP notation search',
                    'One QK^T result on page 1', 'Two w^2 results across two pages',
                    'Page filter', 'Persisted bookmark', 'Original-PDF excerpt', 'Page-2 URL navigation',
                    'Dark theme', 'No page errors or failed backend responses']
                panel.close()
                video.save_as(str(work/'panel.webm'))
            finally:
                context.close()
        if ffmpeg:
            write_captions(report,output)
            # Preserve the recorded UI pixels; encode without invented product frames.
            subprocess.run([ffmpeg,'-hide_banner','-loglevel','error','-y','-i',str(work/'panel.webm'),'-i',str(output/'demo.vtt'),
                '-c:v','libx264','-pix_fmt','yuv420p','-crf','23','-movflags','+faststart','-an',
                '-c:s','mov_text','-metadata:s:s:0','language=eng',
                str(output/'demo.mp4')],check=True)
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        (output/'capture.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8',newline='\n')
    print(json.dumps({'browser':report['browser'],'checks':report.get('checks'),'steps':report['steps']},indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work',type=Path,required=True)
    parser.add_argument('--out',type=Path,default=ROOT/'docs/media')
    parser.add_argument('--ffmpeg',help='Optional ffmpeg executable to encode demo.mp4')
    parser.add_argument('--navigation-repeats',type=int,default=0,help='Extra alternating page-jump acceptance checks, not the short public demo')
    parser.add_argument('--scale',type=int,choices=[1,2],default=1,help='Use 1 for stable native-density video; 2 for a separate double-density still take')
    args = parser.parse_args()
    capture(args.work,args.out,args.ffmpeg,args.navigation_repeats,args.scale)
