import httpx
import pytest

from backend import downloads


class Chunks(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = False
    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk
    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
@pytest.mark.parametrize('header', [None, '9'])
async def test_download_limits_work_with_and_without_content_length(monkeypatch, header):
    stream = Chunks([b'12345', b'6789'])
    async def respond(request):
        return httpx.Response(200, headers={'content-length': header} if header else {}, stream=stream)
    factory = httpx.AsyncClient
    monkeypatch.setattr(downloads.httpx, 'AsyncClient', lambda **kwargs: factory(transport=httpx.MockTransport(respond), **kwargs))
    with pytest.raises(ValueError, match='limit'):
        await downloads.download_bytes('https://arxiv.org/pdf/1706.03762v7', max_bytes=8)
    assert stream.closed


@pytest.mark.asyncio
async def test_download_success_and_http_failure(monkeypatch):
    async def respond(request):
        return httpx.Response(404 if request.url.path == '/missing' else 200, content=b'12345678')
    factory = httpx.AsyncClient
    monkeypatch.setattr(downloads.httpx, 'AsyncClient', lambda **kwargs: factory(transport=httpx.MockTransport(respond), **kwargs))
    assert await downloads.download_bytes('https://arxiv.org/ok', max_bytes=8) == b'12345678'
    with pytest.raises(ValueError, match='HTTP 404'):
        await downloads.download_bytes('https://arxiv.org/missing')
