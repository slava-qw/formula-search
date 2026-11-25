"""Sharing must save work without weakening document publication/isolation."""
import asyncio
import threading

import pytest
from httpx import ASGITransport, AsyncClient

import backend.main as main
from backend.extractor.arxiv_tex import ArxivDocument
from backend.extractor.text_layer import FormulaCandidate
from tests.test_api import _make_pdf

URL = 'https://arxiv.org/pdf/1706.03762v7'


async def wait_for(predicate):
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(.01)


def client():
    return AsyncClient(transport=ASGITransport(app=main.app), base_url='http://test')


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['source', 'upload'])
async def test_four_same_document_requests_share_preparation_and_resident_reuse(monkeypatch, mode):
    started, release = asyncio.Event(), asyncio.Event()
    calls = []
    pdf = _make_pdf('x^2')
    async def prepare(*args, **kwargs):
        calls.append('prepare')
        started.set()
        await release.wait()
        candidate = FormulaCandidate('x^2', 'tex' if mode == 'source' else 'text')
        return ArxivDocument([candidate], pdf_bytes=pdf, page_count=1) if mode == 'source' else ([candidate], 1)
    monkeypatch.setattr(main, 'fetch_arxiv_document' if mode == 'source' else 'run_document_job', prepare)
    original = main.build_index
    def index(cs):
        calls.append('index')
        return original(cs)
    monkeypatch.setattr(main, 'build_index', index)
    kwargs = {'data': {'url': URL}}
    if mode == 'upload':
        kwargs['files'] = {'file': ('same.pdf', pdf, 'application/pdf')}
    async with client() as http:
        tasks = [asyncio.create_task(http.post('/process', **kwargs)) for _ in range(4)]
        await started.wait()
        await wait_for(lambda: main._sessions.preparations.stats()['waiters'] == 4)
        release.set()
        responses = await asyncio.gather(*tasks)
        assert [r.status_code for r in responses] == [200]*4
        assert sum(r.json()['shared_preparation'] for r in responses) == 3
        assert calls == ['prepare', 'index']
        session = main._sessions[URL]
        again = await http.post('/process', **kwargs)
        assert again.json()['reused_session'] and calls == ['prepare', 'index']
        assert main._sessions[URL] is session
    assert main._sessions.stats()['pending'] == 0
    assert main._sessions.preparations.stats()['jobs'] == 0


@pytest.mark.asyncio
async def test_late_follower_does_not_replace_newer_shared_session_or_its_ocr(monkeypatch):
    prepared, release_prepare = asyncio.Event(), asyncio.Event()
    first_publish, release_publish = threading.Event(), threading.Event()
    pdf = _make_pdf('x^2')
    async def prepare(*args, **kwargs):
        prepared.set()
        await release_prepare.wait()
        return ArxivDocument([FormulaCandidate('x^2', 'tex')], pdf_bytes=pdf, page_count=1)
    monkeypatch.setattr(main, 'fetch_arxiv_document', prepare)
    store, publish = main._sessions, main._sessions.publish
    def reordered(key, session):
        if session.generation == 1:
            first_publish.set()
            if not release_publish.wait(5):
                raise TimeoutError('Publication fixture not released')
        return publish(key, session)
    monkeypatch.setattr(store, 'publish', reordered)
    async def ocr(*args):
        return [FormulaCandidate('z^9', 'ocr', page=0)]
    monkeypatch.setattr(main, 'run_ocr', ocr)
    async with client() as http:
        older = asyncio.create_task(http.post('/process', data={'url': URL}))
        await prepared.wait()
        newer = asyncio.create_task(http.post('/process', data={'url': URL}))
        await wait_for(lambda: store.preparations.stats()['waiters'] == 2)
        release_prepare.set()
        try:
            await wait_for(first_publish.is_set)
            assert (await newer).status_code == 200
            session = store[URL]
            assert (await http.post('/ocr-page', json={'url': URL, 'page': 0})).status_code == 200
        finally:
            release_publish.set()
        assert (await older).status_code == 200
    assert store[URL] is session and session.generation == 2
    assert [c.latex for c in session.index_cache] == ['x^2', 'z^9']


@pytest.mark.asyncio
@pytest.mark.parametrize('all_cancel', [False, True])
async def test_cancellation_keeps_other_callers_alive_but_never_publishes_abandoned_work(monkeypatch, all_cancel):
    started, release = asyncio.Event(), asyncio.Event()
    async def prepare(*args, **kwargs):
        started.set()
        await release.wait()
        return ArxivDocument([FormulaCandidate('x^2', 'tex')])
    monkeypatch.setattr(main, 'fetch_arxiv_document', prepare)
    store = main._sessions
    async with client() as http:
        tasks = [asyncio.create_task(http.post('/process', data={'url': URL})) for _ in range(2)]
        await started.wait()
        await wait_for(lambda: store.preparations.stats()['waiters'] == 2)
        for task in tasks if all_cancel else tasks[:1]:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert store.preparations.stats()['jobs'] == 1
        release.set()
        if not all_cancel:
            assert (await tasks[1]).status_code == 200
        await wait_for(lambda: store.preparations.stats()['jobs'] == 0)
    assert (URL in store) is (not all_cancel)
    assert store.stats()['pending'] == 0


@pytest.mark.asyncio
async def test_same_pdf_at_different_urls_has_independent_mutable_indexes(monkeypatch):
    release = asyncio.Event()
    pdf = _make_pdf('x^2')
    calls = 0
    async def prepare(*args):
        nonlocal calls
        calls += 1
        await release.wait()
        return [FormulaCandidate('x^2', 'text')], 1
    monkeypatch.setattr(main, 'run_document_job', prepare)
    async with client() as http:
        tasks = [asyncio.create_task(http.post('/process', data={'url': url},
                 files={'file': ('same.pdf', pdf, 'application/pdf')})) for url in ['a', 'b']]
        await wait_for(lambda: calls == 2)
        release.set()
        assert all(r.status_code == 200 for r in await asyncio.gather(*tasks))
    a, b = main._sessions['a'], main._sessions['b']
    assert a.index_cache is not b.index_cache and a.index_cache[0] is not b.index_cache[0]
    assert a.lock is not b.lock
    a.index_cache[0].bbox = (1, 2, 3, 4)
    assert b.index_cache[0].bbox is None


@pytest.mark.asyncio
async def test_changed_bytes_never_join_old_work_and_old_work_cannot_publish(monkeypatch):
    old_pdf, new_pdf = _make_pdf('x^2'), _make_pdf('z^9')
    started, release = asyncio.Event(), asyncio.Event()
    async def prepare(function, pdf, cached):
        if pdf == old_pdf:
            started.set()
            await release.wait()
        return [FormulaCandidate('x^2' if pdf == old_pdf else 'z^9', 'text')], 1
    monkeypatch.setattr(main, 'run_document_job', prepare)
    async with client() as http:
        older = asyncio.create_task(http.post('/process', data={'url': URL}, files={'file': ('a.pdf', old_pdf)}))
        await started.wait()
        newer = await http.post('/process', data={'url': URL}, files={'file': ('b.pdf', new_pdf)})
        assert newer.status_code == 200 and not newer.json()['shared_preparation']
        release.set()
        assert (await older).status_code == 409
    assert main._sessions[URL].pdf_bytes == new_pdf


@pytest.mark.asyncio
async def test_changed_resolved_version_does_not_join_old_source_work(monkeypatch):
    started, release = asyncio.Event(), asyncio.Event()
    versions = iter(['1706.03762v7', '1706.03762v8'])
    calls = []
    async def resolve(identifier):
        return next(versions)
    async def prepare(identifier, **kwargs):
        calls.append(identifier)
        if identifier.endswith('v7'):
            started.set()
            await release.wait()
        return ArxivDocument([FormulaCandidate('x^7' if identifier.endswith('v7') else 'x^8', 'tex')])
    monkeypatch.setattr(main, 'resolve_arxiv_id', resolve)
    monkeypatch.setattr(main, 'fetch_arxiv_document', prepare)
    url = 'https://arxiv.org/pdf/1706.03762'
    async with client() as http:
        older = asyncio.create_task(http.post('/process', data={'url': url}))
        await started.wait()
        newer = await http.post('/process', data={'url': url})
        assert newer.status_code == 200 and not newer.json()['shared_preparation']
        release.set()
        assert (await older).status_code == 409
    assert calls == ['1706.03762v7', '1706.03762v8']
    assert main._sessions[url].resolved_arxiv_id == '1706.03762v8'


@pytest.mark.asyncio
async def test_shared_failure_reaches_every_caller_and_a_later_retry_is_fresh(monkeypatch):
    started, release = asyncio.Event(), asyncio.Event()
    calls = 0
    async def prepare(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            await release.wait()
            raise ValueError('unreadable source')
        return ArxivDocument([FormulaCandidate('x^2', 'tex')])
    monkeypatch.setattr(main, 'fetch_arxiv_document', prepare)
    async with client() as http:
        tasks = [asyncio.create_task(http.post('/process', data={'url': URL})) for _ in range(3)]
        await started.wait()
        await wait_for(lambda: main._sessions.preparations.stats()['waiters'] == 3)
        release.set()
        assert [r.status_code for r in await asyncio.gather(*tasks)] == [502]*3
        assert not main._sessions and main._sessions.preparations.stats()['jobs'] == 0
        assert (await http.post('/process', data={'url': URL})).status_code == 200
    assert calls == 2 and main._sessions.stats()['pending'] == 0


@pytest.mark.asyncio
async def test_eight_duplicate_clients_use_one_job_and_request_admission_stays_bounded(monkeypatch):
    started, release = asyncio.Event(), asyncio.Event()
    calls = 0
    async def prepare(*args, **kwargs):
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return ArxivDocument([FormulaCandidate('x^2', 'tex')])
    monkeypatch.setattr(main, 'fetch_arxiv_document', prepare)
    async with client() as http:
        tasks = [asyncio.create_task(http.post('/process', data={'url': URL})) for _ in range(8)]
        try:
            await wait_for(lambda: main._sessions.preparations.stats()['waiters'] == 8)
            # Admission can finish while the shared job still awaits its disk
            # cache read. Wait for the mocked fetch rather than assuming that
            # eight admitted callers imply it has already started.
            await asyncio.wait_for(started.wait(), timeout=5)
            denied = await http.post('/process', data={'url': URL})
            assert denied.status_code == 503 and denied.headers['retry-after'] == '2'
            assert main._sessions.preparations.stats()['jobs'] == 1 and calls == 1
        finally:
            release.set()
            responses = await asyncio.wait_for(asyncio.gather(*tasks), timeout=5)
        assert all(r.status_code == 200 for r in responses)
    assert main._sessions.stats()['pending'] == main._sessions.preparations.stats()['jobs'] == 0
