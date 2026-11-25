import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

import backend.arxiv_versions as versions
import backend.main as main
from tests.test_api import _make_pdf


def feed(identifier):
    return f'<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>https://arxiv.org/abs/{identifier}</id></entry></feed>'.encode()


@pytest.mark.parametrize('payload', [
    b'not xml', b'<feed/>', feed('1706.03762'), feed('1706.09999v7'),
    feed('1706.03762v0'), feed('1706.03762v7').replace(b'</feed>', b'<entry><id>https://arxiv.org/abs/1706.03762v8</id></entry></feed>'),
])
def test_version_metadata_must_identify_one_matching_explicit_version(payload):
    with pytest.raises(ValueError):
        versions.version_from_atom(payload, '1706.03762')


@pytest.mark.asyncio
async def test_pinned_version_bypasses_metadata(monkeypatch):
    async def unexpected(*args):
        raise AssertionError('Pinned identifiers need no metadata request')
    monkeypatch.setattr(versions, 'fetch_version_metadata', unexpected)
    assert await versions.resolve_arxiv_id('1706.03762v7') == '1706.03762v7'


@pytest.mark.asyncio
async def test_shared_lookup_survives_one_cancelled_caller_and_expires(monkeypatch):
    now = [0.0]
    started, release = asyncio.Event(), asyncio.Event()
    calls = []
    async def fetch(identifier):
        calls.append(identifier)
        started.set()
        await release.wait()
        return feed('1706.03762v' + str(6 + len(calls)))
    monkeypatch.setattr(versions, 'fetch_version_metadata', fetch)
    resolver = versions.ArxivVersionResolver(ttl=10, interval=0, clock=lambda: now[0])
    first = asyncio.create_task(resolver.resolve('1706.03762'))
    await asyncio.wait_for(started.wait(), 2)
    others = [asyncio.create_task(resolver.resolve('1706.03762')) for _ in range(20)]
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    release.set()
    assert await asyncio.gather(*others) == ['1706.03762v7'] * 20
    assert await resolver.resolve('1706.03762') == '1706.03762v7'
    assert len(calls) == 1
    now[0] = 11
    assert await resolver.resolve('1706.03762') == '1706.03762v8'
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_failed_lookup_can_retry_and_metadata_cache_is_bounded(monkeypatch):
    calls = []
    async def fetch(identifier):
        calls.append(identifier)
        if len(calls) == 1:
            raise ValueError('Temporary metadata error')
        return feed(identifier + 'v1')
    monkeypatch.setattr(versions, 'fetch_version_metadata', fetch)
    resolver = versions.ArxivVersionResolver(max_entries=2, interval=0)
    with pytest.raises(ValueError):
        await resolver.resolve('1706.00001')
    for identifier in ['1706.00001', '1706.00002', '1706.00003']:
        await resolver.resolve(identifier)
    assert len(resolver._cache) == 2
    await resolver.resolve('1706.00001')
    assert calls.count('1706.00001') == 3


@pytest.mark.asyncio
async def test_api_pins_source_pdf_and_cache_together_across_latest_version_change(monkeypatch):
    import backend.extractor.arxiv_tex as arxiv
    current = ['1706.03762v7']
    clock = [0.0]
    calls = []
    async def metadata(identifier):
        return feed(current[0])
    async def tex(identifier):
        calls.append(('tex', identifier))
        return '$E = mc^2$' if identifier.endswith('v7') else '$F = ma$'
    async def pdf(identifier):
        calls.append(('pdf', identifier))
        return _make_pdf('E = mc^2' if identifier.endswith('v7') else 'F = ma')
    monkeypatch.setattr(versions, 'fetch_version_metadata', metadata)
    monkeypatch.setattr(versions, '_resolver', versions.ArxivVersionResolver(interval=0, clock=lambda: clock[0]))
    monkeypatch.setattr(arxiv, 'fetch_tex_source', tex)
    monkeypatch.setattr(arxiv, '_fetch_pdf_bytes', pdf)
    async with AsyncClient(transport=ASGITransport(app=main.app), base_url='http://test') as client:
        first = await client.post('/process', data={'url': 'https://arxiv.org/abs/1706.03762'})
        assert first.status_code == 200 and first.json()['resolved_arxiv_id'] == '1706.03762v7'
        status = (await client.get('/status', params={'url': 'http://export.arxiv.org/pdf/1706.03762.pdf'})).json()
        assert status['loaded'] is True and status['resolved_arxiv_id'] == '1706.03762v7'
        current[0], clock[0] = '1706.03762v8', 601
        session = main._sessions['https://arxiv.org/pdf/1706.03762']
        session.version_checked_at -= versions.VERSION_TTL_SECONDS + 1
        status = (await client.get('/status', params={'url': 'https://arxiv.org/abs/1706.03762'})).json()
        assert status['loaded'] is False and status['version_check_due'] is True
        latest = await client.post('/process', data={'url': 'https://arxiv.org/pdf/1706.03762'})
        assert latest.status_code == 200 and latest.json()['resolved_arxiv_id'] == '1706.03762v8'
        assert latest.json()['cache_hit'] is False and latest.json()['pdf_cache_hit'] is False
        pinned = await client.post('/process', data={'url': 'https://arxiv.org/src/1706.03762v7'})
        assert pinned.status_code == 200 and pinned.json()['cache_hit'] and pinned.json()['pdf_cache_hit']
        old = (await client.post('/search', json={'url': 'https://arxiv.org/abs/1706.03762v7', 'query': 'E = mc^2'})).json()
        new = (await client.post('/search', json={'url': 'https://arxiv.org/abs/1706.03762', 'query': 'E = mc^2'})).json()
    assert old['matches'] and not new['matches']
    assert old['resolved_source_url'] == 'https://arxiv.org/pdf/1706.03762v7'
    assert new['resolved_source_url'] == 'https://arxiv.org/pdf/1706.03762v8'
    assert calls == [('tex', '1706.03762v7'), ('pdf', '1706.03762v7'), ('tex', '1706.03762v8'), ('pdf', '1706.03762v8')]


@pytest.mark.asyncio
async def test_failed_version_resolution_preserves_loaded_document(monkeypatch):
    async def fail(identifier):
        raise ValueError('Metadata unavailable')
    monkeypatch.setattr(versions, 'fetch_version_metadata', fail)
    url = 'https://arxiv.org/pdf/1706.03762'
    session = main.SessionState(pdf_bytes=_make_pdf('E = mc^2'), current_pdf_url=url, source_type='text')
    main._sessions[url] = session
    async with AsyncClient(transport=ASGITransport(app=main.app), base_url='http://test') as client:
        result = await client.post('/process', data={'url': url})
    assert result.status_code == 502
    assert main._sessions[url] is session
