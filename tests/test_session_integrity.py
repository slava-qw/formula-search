"""Document state must publish atomically and stay paired with its PDF bytes."""
import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

import backend.main as main
from backend.extractor.text_layer import FormulaCandidate
from backend.extractor.arxiv_tex import ArxivDocument
from backend.matcher.index import build_index
from tests.test_api import _make_pdf


@pytest.mark.asyncio
async def test_invalid_upload_does_not_create_loaded_session():
    url = 'https://example.com/invalid.pdf'
    async with AsyncClient(transport=ASGITransport(app=main.app), base_url='http://test') as client:
        response = await client.post('/process', data={'url': url}, files={'file': ('bad.pdf', b'not a PDF', 'application/pdf')})
        status = (await client.get('/status', params={'url': url})).json()
    assert response.status_code == 422
    assert status['loaded'] is False
    assert url not in main._sessions


@pytest.mark.asyncio
async def test_invalid_replacement_preserves_existing_pdf_and_index():
    url = 'https://arxiv.org/pdf/1706.03762v7'
    original = _make_pdf('x^2')
    main._sessions[url] = main.SessionState(pdf_bytes=original, current_pdf_url=url, source_type='tex',
        index_cache=build_index([FormulaCandidate('x^2', 'tex')]))
    async with AsyncClient(transport=ASGITransport(app=main.app), base_url='http://test') as client:
        response = await client.post('/process', data={'url': url}, files={'file': ('bad.pdf', b'not a PDF', 'application/pdf')})
        matches = (await client.post('/search', json={'url': url, 'query': 'x^2'})).json()['matches']
    assert response.status_code == 422
    assert main._sessions[url].pdf_bytes == original
    assert matches and matches[0]['source'] == 'tex'


@pytest.mark.asyncio
async def test_changed_pdf_cannot_reuse_unrelated_tex_locations():
    url = 'https://arxiv.org/pdf/1706.03762v7'
    main._sessions[url] = main.SessionState(pdf_bytes=_make_pdf('x^2'), current_pdf_url=url, source_type='tex',
        index_cache=build_index([FormulaCandidate('x^2', 'tex', page=0, bbox=(1, 2, 3, 4))]))
    replacement = _make_pdf('z^9')
    async with AsyncClient(transport=ASGITransport(app=main.app), base_url='http://test') as client:
        response = await client.post('/process', data={'url': url}, files={'file': ('new.pdf', replacement, 'application/pdf')})
        old = (await client.post('/search', json={'url': url, 'query': 'x^2'})).json()['matches']
        new = (await client.post('/search', json={'url': url, 'query': 'z^9'})).json()['matches']
    assert response.json()['source_type'] == 'text'
    assert old == [] and len(new) == 1
    assert main._sessions[url].pdf_bytes == replacement


@pytest.mark.asyncio
async def test_older_source_request_cannot_overwrite_newer_uploaded_document(monkeypatch):
    started, release = asyncio.Event(), asyncio.Event()
    async def slow_source(arxiv_id, **kwargs):
        started.set()
        await release.wait()
        return ArxivDocument([FormulaCandidate('x^2', 'tex')])
    monkeypatch.setattr(main, 'fetch_arxiv_document', slow_source)
    url = 'https://arxiv.org/pdf/1706.03762v7'
    async with AsyncClient(transport=ASGITransport(app=main.app), base_url='http://test') as client:
        older = asyncio.create_task(client.post('/process', data={'url': url}))
        await asyncio.wait_for(started.wait(), 2)
        newer = await client.post('/process', data={'url': url}, files={'file': ('new.pdf', _make_pdf('z^9'), 'application/pdf')})
        release.set()
        stale = await older
        result = (await client.post('/search', json={'url': url, 'query': 'z^9'})).json()
    assert newer.status_code == 200
    assert stale.status_code == 409
    assert result['matches'] and result['matches'][0]['source'] == 'text'


@pytest.mark.asyncio
async def test_failed_newer_request_does_not_discard_valid_older_work(monkeypatch):
    started, release = asyncio.Event(), asyncio.Event()
    async def slow_source(arxiv_id, **kwargs):
        started.set()
        await release.wait()
        return ArxivDocument([FormulaCandidate('x^2', 'tex')])
    monkeypatch.setattr(main, 'fetch_arxiv_document', slow_source)
    url = 'https://arxiv.org/pdf/1706.03762v7'
    async with AsyncClient(transport=ASGITransport(app=main.app), base_url='http://test') as client:
        older = asyncio.create_task(client.post('/process', data={'url': url}))
        await asyncio.wait_for(started.wait(), 2)
        failed = await client.post('/process', data={'url': url}, files={'file': ('bad.pdf', b'bad', 'application/pdf')})
        release.set()
        valid = await older
    assert failed.status_code == 422 and valid.status_code == 200
    assert main._sessions[url].index_cache[0].latex == 'x^2'


@pytest.mark.asyncio
async def test_search_keeps_previous_document_until_replacement_is_ready(monkeypatch):
    started, release = asyncio.Event(), asyncio.Event()
    async def slow_source(arxiv_id, **kwargs):
        started.set()
        await release.wait()
        return ArxivDocument([FormulaCandidate('z^9', 'tex')])
    monkeypatch.setattr(main, 'fetch_arxiv_document', slow_source)
    url = 'https://arxiv.org/pdf/1706.03762v7'
    main._sessions[url] = main.SessionState(index_cache=build_index([FormulaCandidate('x^2', 'text')]))
    async with AsyncClient(transport=ASGITransport(app=main.app), base_url='http://test') as client:
        replacing = asyncio.create_task(client.post('/process', data={'url': url}))
        await asyncio.wait_for(started.wait(), 2)
        current = (await client.post('/search', json={'url': url, 'query': 'x^2'})).json()
        replacing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await replacing
        # Shared preparation may finish and fill the disk cache after its last
        # caller leaves. It must never publish without an active request ticket.
        release.set()
        async with asyncio.timeout(3):
            while main._sessions.preparations.stats()['jobs']:
                await asyncio.sleep(.01)
    assert current['matches'] and current['matches'][0]['source'] == 'text'
    assert main._sessions[url].index_cache[0].latex == 'x^2'


@pytest.mark.asyncio
async def test_ocr_cannot_modify_replacement_document(monkeypatch):
    started, release = asyncio.Event(), asyncio.Event()
    async def slow_ocr(pdf_bytes, page):
        started.set()
        await release.wait()
        return [FormulaCandidate('x^2', 'ocr', page=page)]
    monkeypatch.setattr(main, 'run_ocr', slow_ocr)
    url = 'https://example.com/changing.pdf'
    async with AsyncClient(transport=ASGITransport(app=main.app), base_url='http://test') as client:
        await client.post('/process', data={'url': url}, files={'file': ('old.pdf', _make_pdf('x^2'), 'application/pdf')})
        ocr = asyncio.create_task(client.post('/ocr-page', json={'url': url, 'page': 0}))
        await asyncio.wait_for(started.wait(), 2)
        await client.post('/process', data={'url': url}, files={'file': ('new.pdf', _make_pdf('z^9'), 'application/pdf')})
        release.set()
        result = await ocr
    assert result.status_code == 409
    assert all(c.source != 'ocr' for c in main._sessions[url].index_cache)


@pytest.mark.asyncio
async def test_older_ocr_result_cannot_replace_newer_result_for_same_page(monkeypatch):
    started, release = asyncio.Event(), asyncio.Event()
    calls = 0
    async def slow_first_ocr(pdf_bytes, page):
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            await release.wait()
            return [FormulaCandidate('x^2', 'ocr', page=page)]
        return [FormulaCandidate('z^9', 'ocr', page=page)]
    monkeypatch.setattr(main, 'run_ocr', slow_first_ocr)
    url = 'https://example.com/ocr.pdf'
    main._sessions[url] = main.SessionState(pdf_bytes=_make_pdf('x^2'))
    async with AsyncClient(transport=ASGITransport(app=main.app), base_url='http://test') as client:
        older = asyncio.create_task(client.post('/ocr-page', json={'url': url, 'page': 0}))
        await asyncio.wait_for(started.wait(), 2)
        newer = await client.post('/ocr-page', json={'url': url, 'page': 0})
        release.set()
        stale = await older
    assert newer.status_code == 200 and stale.status_code == 409
    assert [c.latex for c in main._sessions[url].index_cache] == ['z^9']


@pytest.mark.asyncio
async def test_upload_limit_preserves_existing_state(monkeypatch):
    monkeypatch.setattr(main, 'MAX_PDF_BYTES', 10)
    session = main.SessionState(index_cache=build_index([FormulaCandidate('x^2', 'tex')]))
    main._sessions[''] = session
    async with AsyncClient(transport=ASGITransport(app=main.app), base_url='http://test') as client:
        response = await client.post('/process', files={'file': ('big.pdf', b'x' * 11, 'application/pdf')})
    assert response.status_code == 413
    assert main._sessions[''] is session


@pytest.mark.asyncio
async def test_unversioned_arxiv_request_uses_cache_of_resolved_version(monkeypatch):
    seen = []
    versions = iter(['1706.03762v7', '1706.03762v8'])
    async def resolve(identifier):
        return next(versions)
    monkeypatch.setattr(main, 'resolve_arxiv_id', resolve)
    async def fake_source(arxiv_id, **kwargs):
        seen.append((arxiv_id, kwargs['cached_candidates']))
        return ArxivDocument([FormulaCandidate(f'x^{len(seen)}', 'tex')])
    monkeypatch.setattr(main, 'fetch_arxiv_document', fake_source)
    async with AsyncClient(transport=ASGITransport(app=main.app), base_url='http://test') as client:
        for _ in range(2):
            response = await client.post('/process', data={'url': 'https://arxiv.org/pdf/1706.03762'})
            assert response.status_code == 200 and response.json()['cache_hit'] is False
    assert seen == [('1706.03762v7', None), ('1706.03762v8', None)]
    assert main._sessions['https://arxiv.org/pdf/1706.03762'].resolved_arxiv_id == '1706.03762v8'


@pytest.mark.asyncio
async def test_source_processing_retains_mapping_pdf_and_uses_pinned_cache(monkeypatch):
    import backend.extractor.arxiv_tex as arxiv
    calls = []
    pdf = _make_pdf('E = mc^2')
    async def source(arxiv_id):
        calls.append('tex')
        return '$E = mc^2$'
    async def pdf_source(arxiv_id):
        calls.append('pdf')
        return pdf
    monkeypatch.setattr(arxiv, 'fetch_tex_source', source)
    monkeypatch.setattr(arxiv, '_fetch_pdf_bytes', pdf_source)
    url = 'https://arxiv.org/pdf/1706.03762v7'
    async with AsyncClient(transport=ASGITransport(app=main.app), base_url='http://test') as client:
        first = await client.post('/process', data={'url': url})
        # Exercise persisted caches after resident state has been released.
        del main._sessions[url]
        second = await client.post('/process', data={'url': url})
        found = (await client.post('/search', json={'url': url, 'query': 'E = mc^2'})).json()['matches']
    assert first.status_code == second.status_code == 200
    assert first.json()['cache_hit'] is False and second.json()['cache_hit'] is True
    assert calls.count('tex') == 1 and calls.count('pdf') == 1
    assert second.json()['pdf_cache_hit'] is True
    assert second.json()['pdf_cached'] is True
    assert main._sessions[url].pdf_bytes == pdf
    assert found[0]['page'] == 0 and found[0]['bbox']


@pytest.mark.asyncio
async def test_identical_validated_pdf_upload_reuses_source_without_extraction(monkeypatch):
    pdf = _make_pdf('x^2')
    url = 'https://arxiv.org/pdf/1706.03762v7'
    session = main.SessionState(pdf_bytes=pdf, page_count=1, current_pdf_url=url,
        source_type='tex', index_cache=build_index([FormulaCandidate('x^2', 'tex')]))
    main._sessions[url] = session
    async def unnecessary_work(*args):
        raise AssertionError('Identical validated PDF must not be extracted again')
    monkeypatch.setattr(main, 'run_document_job', unnecessary_work)
    async with AsyncClient(transport=ASGITransport(app=main.app), base_url='http://test') as client:
        response = await client.post('/process', data={'url': url}, files={'file': ('same.pdf', pdf, 'application/pdf')})
    assert response.status_code == 200 and response.json()['reused_session'] is True
    assert main._sessions[url] is session
