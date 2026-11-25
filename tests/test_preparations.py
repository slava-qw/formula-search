import asyncio

import pytest

from backend.preparations import PreparationPool
from backend.processing import ProcessingBusy


@pytest.mark.asyncio
async def test_completed_work_is_shared_until_callers_finish_publication():
    pool = PreparationPool()
    calls = 0
    async def prepare():
        nonlocal calls
        calls += 1
        return object()
    async with pool.acquire('a', prepare) as (first, shared):
        assert not shared
        async with pool.acquire('a', prepare) as (second, shared):
            assert shared and second is first and calls == 1
        assert pool.stats()['jobs'] == 1
    assert pool.stats()['jobs'] == 0


@pytest.mark.asyncio
async def test_cancelled_waiters_do_not_duplicate_running_work_or_release_capacity():
    pool = PreparationPool(max_jobs=1)
    started, release = asyncio.Event(), asyncio.Event()
    calls = 0
    async def prepare():
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return 'ready'
    async def request():
        async with pool.acquire('a', prepare) as result:
            return result
    first = asyncio.create_task(request())
    await started.wait()
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert pool.stats() == {'jobs': 1, 'waiters': 0, 'max_jobs': 1}
    with pytest.raises(ProcessingBusy):
        async with pool.acquire('different', prepare):
            pass
    second = asyncio.create_task(request())
    await asyncio.sleep(0)
    release.set()
    assert await second == ('ready', True)
    assert calls == 1 and pool.stats()['jobs'] == 0


@pytest.mark.asyncio
async def test_failed_work_is_removed_and_a_retry_runs_again():
    pool = PreparationPool()
    calls = 0
    async def prepare():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ValueError('broken source')
        return 'good'
    with pytest.raises(ValueError, match='broken'):
        async with pool.acquire('a', prepare):
            pass
    async with pool.acquire('a', prepare) as result:
        assert result == ('good', False)
    assert calls == 2 and pool.stats()['jobs'] == 0
