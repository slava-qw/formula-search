"""Source provenance checks in an isolated real Chromium extension page."""


def verify_source_reference(popup, pdf_page, context, out, report):
    reference = 'model_architecture.tex:40'
    match = popup.evaluate('_matches[0]')
    assert (match['source_file'], match['source_line'], match['source_location_kind']) == (
        'model_architecture.tex', 40, 'literal')
    assert popup.locator('#results .source-reference').inner_text() == reference
    report['checks'].append('Real Attention search exposes the original included TeX file and line through HTTP')
    exported = popup.evaluate('JSON.parse(buildExportPayload(_matches))[0]')
    assert all(exported[key] == match[key] for key in ('source_file', 'source_line', 'source_location_kind'))
    report['checks'].append('JSON export retains source provenance')

    context.grant_permissions(['clipboard-read', 'clipboard-write'])
    popup.bring_to_front()
    popup.evaluate('''() => {
        window.__realSourceJump = sendScrollToMatch;
        window.__sourceJumpCount = 0;
        sendScrollToMatch = (...args) => {window.__sourceJumpCount++; return window.__realSourceJump(...args)};
    }''')
    popup.locator('#results .source-reference').click()
    popup.wait_for_function("() => document.querySelector('#results .source-reference').textContent.startsWith('Copied')")
    assert popup.evaluate('navigator.clipboard.readText()') == reference
    assert popup.evaluate('__sourceJumpCount') == 0
    popup.evaluate('() => {sendScrollToMatch = window.__realSourceJump}')
    report['checks'].append('Source-reference click writes the real clipboard without navigating the PDF')
    popup.locator('#results .result-copy-btn').click()
    popup.wait_for_function("() => document.querySelector('#results .result-copy-btn').textContent === '✓'")
    assert popup.evaluate('navigator.clipboard.readText()').endswith(reference)
    report['checks'].append('Copy-as-Markdown includes the original source reference')

    popup.locator('#results .bookmark-star-btn').click()
    popup.wait_for_function("async () => (await loadBookmarks()).length === 1")
    saved = popup.evaluate('(async () => (await loadBookmarks())[0])()')
    assert (saved['source_file'], saved['source_line'], saved['source_location_kind']) == (
        match['source_file'], match['source_line'], match['source_location_kind'])
    report['checks'].append('Bookmark acknowledgement retains source provenance')
    pdf_page.bring_to_front()
    popup.reload(wait_until='load')
    popup.wait_for_function('() => !document.querySelector("#search-btn").disabled')
    popup.locator('#bookmarks-toggle-btn').click()
    popup.wait_for_selector('#bookmarks-list .source-reference')
    assert popup.locator('#bookmarks-list .source-reference').inner_text() == reference
    popup.screenshot(path=str(out/'source-bookmark.png'))
    report['checks'].append('Saved source reference survives an extension-page reload and renders in bookmarks')
    popup.locator('#bookmarks-toggle-btn').click()
    popup.locator('#query').fill(r'QK^T')
    popup.locator('#search-btn').click()
    popup.wait_for_selector('#results .source-reference')

    # Synthetic edge cases exercise rendering, not real-paper provenance.
    original = popup.evaluate('_matches[0]')
    popup.evaluate('''() => {
        window.__sourceFixture = {..._matches[0], page: -1, bbox: null,
            source_file: 'appendix/<img src=x onerror=alert(1)>/' + 'long-name-'.repeat(12) + '.tex',
            source_line: 317, source_location_kind: 'macro'};
        document.querySelector('#results').replaceChildren(renderMatch(window.__sourceFixture, 0));
        document.body.dataset.surface = 'panel';
    }''')
    popup.set_viewport_size({'width': 320, 'height': 740})
    assert popup.locator('#results img').count() == 0
    assert popup.locator('#results .source-reference').inner_text().endswith(':317 (macro call)')
    assert 'PDF location unavailable' in popup.locator('#results').inner_text()
    bounds = popup.locator('#results .source-reference').bounding_box()
    assert bounds['x'] >= 0 and bounds['x'] + bounds['width'] <= 320
    for theme in ('light', 'dark'):
        popup.emulate_media(color_scheme=theme)
        popup.screenshot(path=str(out/f'source-long-path-{theme}.png'))
    report['checks'].append('Synthetic macro-call and long hostile filename render as text within a 320px panel, without a false PDF location')
    popup.evaluate('''() => {
        document.querySelector('#results').replaceChildren(renderMatch({latex:'x', page:-1, source:'text'}, 0));
    }''')
    assert popup.locator('#results .source-reference').count() == 0
    report['checks'].append('Results without source provenance do not invent a filename or line')
    popup.set_viewport_size({'width': 1200, 'height': 900})
    popup.emulate_media(color_scheme='light')
    popup.evaluate('''match => {
        document.body.dataset.surface = 'popup';
        document.querySelector('#results').replaceChildren(renderMatch(match, 0));
    }''', original)
