import asyncio
import threading

import pytest
from httpx import ASGITransport, AsyncClient

import backend.main as main
from backend.extractor.arxiv_tex import ArxivDocument
from backend.extractor.text_layer import FormulaCandidate
from backend.matcher.index import build_index
from backend.sessions import SessionStore, StalePublication, SessionTooLarge, estimate_session_bytes
from backend.processing import ProcessingBusy
from tests.test_api import _make_pdf


def state(latex='x^2', pdf=b''):
    return main.SessionState(index_cache=build_index([FormulaCandidate(latex, 'tex')]), pdf_bytes=pdf)


def use_store(monkeypatch, **limits):
    store = SessionStore(**limits)
    monkeypatch.setattr(main, '_sessions', store)
    monkeypatch.setattr(main, '_sessions_lock', store.lock)
    return store


def test_session_count_lru_and_byte_budget():
    store = SessionStore(max_entries=2)
    store['a'], store['b'] = state('a^2'), state('b^2')
    assert store['a'].index_cache[0].latex == 'a^2'
    store['c'] = state('c^2')
    assert set(store) == {'a', 'c'}
    sample = state(pdf=b'x' * 100)
    budget = estimate_session_bytes(sample)
    store = SessionStore(max_bytes=budget)
    store['a'], store['b'] = sample, state(pdf=b'y' * 100)
    assert list(store) == ['b']
    assert store.stats()['estimated_bytes'] <= budget


def test_expiry_releases_store_reference_but_existing_reader_remains_valid():
    now = [0]
    store = SessionStore(ttl=10, clock=lambda: now[0])
    store['a'] = state()
    reader = store['a']
    now[0] = 11
    assert store.get('a') is None and len(store) == 0
    assert reader.index_cache[0].latex == 'x^2'
    assert store.stats()['estimated_bytes'] == 0


def test_oversized_replacement_preserves_prior_session():
    prior = state()
    store = SessionStore(max_bytes=estimate_session_bytes(prior))
    store['a'] = prior
    with pytest.raises(SessionTooLarge):
        store['a'] = state(pdf=b'x' * 10000)
    assert store['a'] is prior
    assert store.stats()['pending'] == 0


def test_newer_publication_barrier_survives_eviction_until_older_request_finishes():
    store = SessionStore(max_entries=1)
    old, new = store.begin('a'), store.begin('a')
    newer = state('z^9')
    newer.generation = new
    store.publish('a', newer)
    store.finish('a', new)
    store['b'] = state('b^2')
    assert 'a' not in store
    stale = state('x^2')
    stale.generation = old
    with pytest.raises(StalePublication):
        store.publish('a', stale)
    store.finish('a', old)
    assert store.stats()['pending'] == store.stats()['publication_barriers'] == 0
    assert list(store) == ['b']


def test_cancelled_ticket_cannot_publish_from_a_late_background_thread():
    store = SessionStore()
    ticket = store.begin('a')
    late = state()
    late.generation = ticket
    store.finish('a', ticket)
    with pytest.raises(StalePublication):
        store.publish('a', late)
    assert not store


def test_pending_capacity_recovers_after_failure_without_resident_tombstones():
    store = SessionStore(max_pending=1)
    ticket = store.begin('a')
    with pytest.raises(ProcessingBusy):
        store.begin('b')
    store.finish('a', ticket)
    for i in range(100):
        ticket = store.begin(str(i))
        store.finish(str(i), ticket)
    assert store.stats()['pending'] == store.stats()['publication_barriers'] == 0


@pytest.mark.asyncio
async def test_http_evicted_session_is_distinct_from_empty_search(monkeypatch):
    store = use_store(monkeypatch, max_entries=1)
    async with AsyncClient(transport=ASGITransport(app=main.app), base_url='http://test') as client:
        for name in ['a', 'b']:
            response = await client.post('/process', data={'url': name}, files={'file': ('x.pdf', _make_pdf('x^2'), 'application/pdf')})
            assert response.status_code == 200
        missing = (await client.post('/search', json={'url': 'a', 'query': 'x^2'})).json()
        empty = (await client.post('/search', json={'url': 'b', 'query': 'z^9'})).json()
        assert missing['loaded'] is False and missing['matches'] == []
        assert empty['loaded'] is True and empty['matches'] == []
        assert len(store) == 1


@pytest.mark.asyncio
async def test_http_old_request_cannot_resurrect_evicted_newer_document(monkeypatch):
    store = use_store(monkeypatch, max_entries=1)
    started, release = asyncio.Event(), asyncio.Event()
    async def slow_source(identifier, **kwargs):
        started.set()
        await release.wait()
        return ArxivDocument([FormulaCandidate('x^2', 'tex')])
    monkeypatch.setattr(main, 'fetch_arxiv_document', slow_source)
    url = 'https://arxiv.org/pdf/1706.03762v7'
    async with AsyncClient(transport=ASGITransport(app=main.app), base_url='http://test') as client:
        old = asyncio.create_task(client.post('/process', data={'url': url}))
        await asyncio.wait_for(started.wait(), 2)
        for target in [url, 'different-paper']:
            response = await client.post('/process', data={'url': target}, files={'file': ('x.pdf', _make_pdf('z^9'), 'application/pdf')})
            assert response.status_code == 200
        release.set()
        assert (await old).status_code == 409
    assert list(store) == ['different-paper']
    assert store.stats()['publication_barriers'] == 0


@pytest.mark.asyncio
async def test_http_oversized_session_does_not_replace_current_document(monkeypatch):
    store = use_store(monkeypatch, max_bytes=6000)
    async with AsyncClient(transport=ASGITransport(app=main.app), base_url='http://test') as client:
        good = await client.post('/process', files={'file': ('x.pdf', _make_pdf('ordinary prose'), 'application/pdf')})
        assert good.status_code == 200
        previous = store['']
        large = await client.post('/process', files={'file': ('x.pdf', _make_pdf('x^2'), 'application/pdf')})
        assert large.status_code == 413
    assert store[''] is previous
    assert store.stats()['pending'] == 0


@pytest.mark.asyncio
async def test_resident_source_request_reuses_mapping_index_without_download_or_rebuild(monkeypatch):
    url = 'https://arxiv.org/pdf/1706.03762v7'
    session = state(pdf=_make_pdf('x^2'))
    session.source_type, session.page_count, session.resolved_arxiv_id = 'tex', 1, '1706.03762v7'
    main._sessions[url] = session
    async def unnecessary(*args, **kwargs):
        raise AssertionError('Resident version should reuse its complete index')
    monkeypatch.setattr(main, 'fetch_arxiv_document', unnecessary)
    async with AsyncClient(transport=ASGITransport(app=main.app), base_url='http://test') as client:
        response = await client.post('/process', data={'url': 'https://arxiv.org/abs/1706.03762v7'})
    assert response.status_code == 200 and response.json()['reused_session'] is True
    assert main._sessions[url] is session
    assert main._sessions.stats()['pending'] == 0


@pytest.mark.asyncio
async def test_ocr_budget_rejection_keeps_existing_results(monkeypatch):
    session = state(pdf=_make_pdf('x^2'))
    store = use_store(monkeypatch, max_bytes=estimate_session_bytes(session))
    store['a'] = session
    async def recognize(*args):
        return [FormulaCandidate('z^9', 'ocr', page=0)]
    monkeypatch.setattr(main, 'run_ocr', recognize)
    async with AsyncClient(transport=ASGITransport(app=main.app), base_url='http://test') as client:
        response = await client.post('/ocr-page', json={'url': 'a', 'page': 0})
    assert response.status_code == 413
    assert [item.latex for item in session.index_cache] == ['x^2']


def test_resident_limits_can_be_configured_without_changing_code(monkeypatch):
    monkeypatch.setenv('FORMULA_SEARCH_MAX_SESSIONS', '3')
    monkeypatch.setenv('FORMULA_SEARCH_SESSION_MIB', '512')
    monkeypatch.setenv('FORMULA_SEARCH_SESSION_TTL_SECONDS', '60')
    store = SessionStore.from_environment()
    assert store.max_entries == 3 and store.max_bytes == 512 * 1024 * 1024 and store.ttl == 60


@pytest.mark.asyncio
async def test_slow_algebraic_search_does_not_block_notation_search_for_same_paper(monkeypatch):
    started, release = threading.Event(), threading.Event()
    original = main.search_formulas
    def slow_optional(query, index, **kwargs):
        if kwargs['mode'] == 'algebraic':
            started.set()
            if not release.wait(5):
                raise TimeoutError('Test did not release optional search')
            return []
        return original(query, index, **kwargs)
    monkeypatch.setattr(main, 'search_formulas', slow_optional)
    main._sessions['a'] = state()
    async with AsyncClient(transport=ASGITransport(app=main.app), base_url='http://test') as client:
        expensive = asyncio.create_task(client.post('/search', json={'url': 'a', 'query': 'x^2', 'mode': 'algebraic'}))
        try:
            assert await asyncio.to_thread(started.wait, 2)
            ordinary = await asyncio.wait_for(client.post('/search', json={'url': 'a', 'query': 'x^2'}), 1)
            assert ordinary.json()['matches'] and not expensive.done()
        finally:
            release.set()
            await expensive
