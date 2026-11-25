"""Exercise actual spawn workers, HTTP responsiveness, and capacity recovery."""
import asyncio
from concurrent.futures import Future
from functools import partial
import os
from pathlib import Path
import time

import pytest
from httpx import ASGITransport, AsyncClient

import backend.main as main
import backend.processing as processing
from tests.test_api import _make_pdf


def _paused_pdf(ready: str, release: str, pdf_bytes, candidates):
    Path(ready).write_text(str(os.getpid()))
    deadline = time.monotonic() + 10
    while not Path(release).exists():
        if time.monotonic() > deadline:
            raise TimeoutError('Test did not release PDF worker')
        time.sleep(.01)
    return processing.prepare_pdf(pdf_bytes, candidates)


@pytest.mark.asyncio
async def test_pdf_worker_is_separate_and_api_remains_responsive(tmp_path, monkeypatch):
    ready, release = tmp_path / 'ready', tmp_path / 'release'
    monkeypatch.setattr(main, 'prepare_pdf', partial(_paused_pdf, str(ready), str(release)))
    async with AsyncClient(transport=ASGITransport(app=main.app), base_url='http://test') as client:
        task = asyncio.create_task(client.post('/process', files={'file': ('x.pdf', _make_pdf('x^2'), 'application/pdf')}))
        try:
            async with asyncio.timeout(8):
                while not ready.exists():
                    assert not task.done(), 'PDF processing ended before worker barrier'
                    await asyncio.sleep(.01)
            assert int(ready.read_text()) != os.getpid()
            status = await asyncio.wait_for(client.get('/status'), 1)
            search = await asyncio.wait_for(client.post('/search', json={'url': '', 'query': 'x^2'}), 1)
            assert status.json()['loaded'] is False
            assert search.status_code == 200 and not task.done()
        finally:
            release.write_text('go')
        result = await task
    assert result.status_code == 200 and result.json()['formula_count'] == 1


@pytest.mark.asyncio
async def test_cancelled_running_job_still_counts_toward_capacity(monkeypatch):
    future = Future()
    future.set_running_or_notify_cancel()
    class Pool:
        def submit(self, *args):
            return future
    monkeypatch.setattr(processing, '_pool', Pool())
    monkeypatch.setattr(processing, '_pending', 0)
    monkeypatch.setattr(processing, 'MAX_PENDING', 1)
    task = asyncio.create_task(processing.run_document_job(os.getpid))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert processing._pending == 1
    with pytest.raises(processing.ProcessingBusy):
        await processing.run_document_job(os.getpid)
    future.set_result(123)
    await asyncio.sleep(0)
    assert processing._pending == 0


@pytest.mark.asyncio
async def test_dead_document_worker_recovers_for_next_request():
    with pytest.raises(processing.ProcessingBusy, match='restarted'):
        await processing.run_document_job(os._exit, 7)
    worker_pid = await processing.run_document_job(os.getpid)
    assert worker_pid != os.getpid()
    assert processing._pending == 0


@pytest.mark.asyncio
async def test_busy_processing_returns_retryable_status_without_publishing(monkeypatch):
    async def busy(*args):
        raise processing.ProcessingBusy('Processing is busy')
    monkeypatch.setattr(main, 'run_document_job', busy)
    async with AsyncClient(transport=ASGITransport(app=main.app), base_url='http://test') as client:
        response = await client.post('/process', files={'file': ('x.pdf', _make_pdf('x^2'), 'application/pdf')})
    assert response.status_code == 503 and response.headers['retry-after'] == '2'
    assert not main._sessions
