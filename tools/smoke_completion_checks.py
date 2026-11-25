"""Real preview/selection, with explicitly simulated recognition response.

Real model accuracy is checked independently against equation crops. This test
isolates the browser-to-region contract, stale selection and retry controls.
"""
import json


def verify_completion(view, out, report, paper):
    out.mkdir(parents=True, exist_ok=True)
    report['completion_method'] = __doc__
    for width in [420, 320]:
        view.set_viewport_size({'width':width, 'height':900})
        # Real /ocr-preview supplies actual PDF pixels and the session revision.
        # Simulate model availability so this check does not depend on downloads.
        def preview(route):
            response = route.fetch()
            data = response.json()
            data['ocr']['installed'] = True
            route.fulfill(response=response, json=data)
        captured = []
        def recognized(route):
            captured.append(route.request.post_data_json)
            route.fulfill(json={'formulas_found':1,'proposal_id':'a'*32,'latex':r'\mathcal{N}=16','requires_review':True})
        saves = []
        def save(route):
            saves.append(route.request.post_data_json)
            if len(saves) == 1:
                route.fulfill(status=503,json={'detail':'Temporary recognition save failure'})
            else:
                route.fulfill(json={'formulas_found':1})
        view.route('**/ocr-preview', preview)
        view.route('**/ocr-page', recognized)
        view.route('**/ocr-save', save)
        try:
            view.evaluate('() => { window._ocrAudit = runOcr(4); }')
            dialog = view.locator('dialog.ocr-selection')
            dialog.wait_for(state='visible')
            canvas = dialog.locator('canvas')
            bounds = canvas.bounding_box()
            view.mouse.move(bounds['x']+bounds['width']*.25, bounds['y']+bounds['height']*.2)
            view.mouse.down()
            view.mouse.move(bounds['x']+bounds['width']*.75, bounds['y']+bounds['height']*.3, steps=5)
            view.mouse.up()
            dialog.screenshot(path=str(out/f'crop-{width}.png'))
            dialog.get_by_role('button', name='Recognize equation').click()
            review = view.get_by_role('dialog').filter(has=view.get_by_role('heading',name='Check the recognized equation'))
            review.wait_for(state='visible')
            review.get_by_role('textbox',name='Recognized LaTeX').fill('N=16')
            review.screenshot(path=str(out/f'review-{width}.png'))
            review.get_by_role('button',name='Add equation to search').click()
            review.get_by_role('alert').wait_for(state='visible')
            assert review.get_by_role('textbox',name='Recognized LaTeX').input_value() == 'N=16'
            review.get_by_role('button',name='Add equation to search').click()
            view.wait_for_function('() => statusEl.textContent.includes("search again")')
            assert len(captured) == 1
            assert len(saves) == 2 and all(saved['latex'] == 'N=16' for saved in saves)
            data = captured[0]
            assert data['page'] == 3 and len(data['context_revision']) == 32
            assert len(data['region']) == 4 and data['region'][2] > data['region'][0]
            report['checks'].append({'case':'Real PDF preview, region selection, request and completion status',
                                     'width':width, 'recognition':'simulated', 'request':data})
            view.evaluate('() => { window._ocrAudit = runOcr(4); }')
            dialog.wait_for(state='visible')
            view.evaluate('() => documentSession.begin()')
            dialog.wait_for(state='detached')
            assert len(captured) == 1
            report['checks'].append({'case':'Document change cancels an open OCR selection without recognition','width':width})
            # Restore the active context for the next check.
            paper.bring_to_front()
            view.evaluate('() => init()')
            view.wait_for_function('() => !queryInput.disabled')
            view.bring_to_front()
        finally:
            view.unroute('**/ocr-preview', preview)
            view.unroute('**/ocr-page', recognized)
            view.unroute('**/ocr-save', save)
            # The browser logs the intentionally injected HTTP 503 too. Keep
            # those messages as expected evidence, rather than unexpected errors.
            expected = [message for message in report.get('console_warnings',[])
                        if 'status of 503' in message]
            report.setdefault('expected_console_messages',[]).extend(expected)
            report['console_warnings'] = [message for message in report.get('console_warnings',[])
                                          if 'status of 503' not in message]
    view.evaluate('''() => showDocumentWarning({pdf_cached:false,
        pdf_warning:'PDF unavailable; source formulas remain searchable.'}, _sourceUrl, documentSession.current)''')
    assert view.locator('#document-warning').inner_text().startswith('PDF unavailable')
    assert view.locator('#ocr-run-btn').is_disabled()
    view.screenshot(path=str(out/'source-only-warning.png'))
    report['checks'].append('Source-only warning explains missing PDF and disables recognition')
    view.evaluate('() => showDocumentWarning({pdf_cached:true}, _sourceUrl, documentSession.current)')
    assert view.locator('#document-warning').inner_text() == ''
