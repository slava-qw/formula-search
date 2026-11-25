"""Real child processes exercise leases, pipe stalls, retirement and shutdown."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import threading
import time

import pytest

from backend.matcher.worker_pool import ScalarWorkerPool

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = r'''
import json,sys,time
if len(sys.argv)>1 and sys.argv[1]=='no-read': time.sleep(30)
for line in sys.stdin:
    data=json.loads(line)
    query=data['query']
    if query=='hang': time.sleep(30)
    if query=='slow': time.sleep(.4)
    if query=='crash': sys.exit(7)
    if query=='malformed': print('broken',flush=True); continue
    result={'indices':[data['candidates'][0][0]],'status':'complete'}
    if query=='bad-index': result['indices']=[9999]
    if query=='bad-status': result['status']='invented'
    if query=='duplicate': result['indices']*=2
    print(json.dumps({'id':'stale' if query=='stale' else data['id'],'result':result}),flush=True)
'''


@pytest.fixture
def pools():
    created = []
    def make(**kwargs):
        command = kwargs.pop('command', [sys.executable, '-u', '-c', FIXTURE])
        pool = ScalarWorkerPool(command, str(ROOT), **kwargs)
        created.append(pool)
        return pool
    yield make
    for pool in created:
        pool.close()
        assert pool.snapshot() == {'closed': True, 'workers': []}


def payload(query='ok'):
    return {'query': query, 'candidates': [[3, 'x']], 'budget': .8}


def wait_until(predicate, timeout=5):
    deadline = time.monotonic()+timeout
    while not predicate():
        assert time.monotonic() < deadline, 'Lifecycle transition did not complete'
        time.sleep(.01)


def test_reuses_one_child_then_retires_at_request_limit(pools):
    pool = pools(max_requests=2)
    assert pool.request(payload(), 3) == {'indices': [3], 'status': 'complete'}
    first = next(iter(pool._workers))
    assert first.requests == 1
    assert pool.request(payload(), 3)['status'] == 'complete'
    assert first.closed and first.process.poll() is not None
    assert pool.snapshot()['workers'] == []
    assert pool.request(payload(), 3)['status'] == 'complete'
    assert pool.snapshot()['workers'][0]['pid'] != first.process.pid


def test_hung_child_is_killed_reaped_and_replaced(pools):
    pool = pools()
    assert pool.request(payload(), 3)['status'] == 'complete'
    first = next(iter(pool._workers))
    started = time.monotonic()
    assert pool.request(payload('hang'), .15)['status'] == 'timeout'
    assert time.monotonic()-started < 3
    assert first.closed and first.process.poll() is not None
    assert not first.thread.is_alive()
    assert pool.request(payload(), 3)['status'] == 'complete'
    assert pool.snapshot()['workers'][0]['pid'] != first.process.pid


def test_deadline_includes_blocked_pipe_write(pools):
    pool = pools(command=[sys.executable, '-u', '-c', FIXTURE, 'no-read'])
    large = {'query': 'x', 'candidates': [[i, 'x'*512] for i in range(80)], 'budget': .8}
    started = time.monotonic()
    assert pool.request(large, .2)['status'] == 'timeout'
    assert time.monotonic()-started < 3
    assert pool.snapshot()['workers'] == []


@pytest.mark.parametrize('query', ['crash', 'malformed', 'bad-index', 'bad-status', 'duplicate', 'stale'])
def test_invalid_child_response_retires_worker_and_next_request_recovers(pools, query):
    pool = pools()
    assert pool.request(payload(), 3)['status'] == 'complete'
    first = next(iter(pool._workers))
    assert pool.request(payload(query), 3)['status'] == 'unavailable'
    assert first.closed and first.process.poll() is not None
    assert pool.request(payload(), 3)['status'] == 'complete'


def test_third_request_is_busy_while_two_children_are_leased(pools):
    pool = pools()
    with ThreadPoolExecutor(max_workers=2) as executor:
        jobs = [executor.submit(pool.request, payload('slow'), 3) for _ in range(2)]
        wait_until(lambda: len(pool.snapshot()['workers']) == 2)
        assert all(w['state'] == 'leased' for w in pool.snapshot()['workers'])
        assert pool.request(payload(), 3)['status'] == 'busy'
        assert all(job.result()['status'] == 'complete' for job in jobs)
    assert len(pool.snapshot()['workers']) == 2


def test_idle_expiry_reaps_child_and_allows_fresh_work(pools):
    pool = pools(idle_seconds=.1)
    assert pool.request(payload(), 3)['status'] == 'complete'
    first = next(iter(pool._workers))
    wait_until(lambda: first.closed)
    assert first.process.poll() is not None
    assert pool.snapshot()['workers'] == []
    assert pool.request(payload(), 3)['status'] == 'complete'


def test_shutdown_interrupts_running_child_and_never_reopens_closed_pool(pools):
    pool = pools()
    with ThreadPoolExecutor(max_workers=1) as executor:
        request = executor.submit(pool.request, payload('hang'), 3)
        wait_until(lambda: bool(pool.snapshot()['workers']))
        first = next(iter(pool._workers))
        pool.close()
        assert request.result()['status'] == 'unavailable'
    assert first.closed and first.process.poll() is not None
    assert pool.request(payload(), 3)['status'] == 'unavailable'


def test_retirement_keeps_capacity_until_child_is_reaped(pools, monkeypatch):
    pool = pools(max_workers=1, max_requests=2)
    assert pool.request(payload(), 3)['status'] == 'complete'
    worker = next(iter(pool._workers))
    entered, release = threading.Event(), threading.Event()
    original = worker.close
    def held_close():
        entered.set()
        assert release.wait(5)
        original()
    monkeypatch.setattr(worker, 'close', held_close)
    with ThreadPoolExecutor(max_workers=1) as executor:
        request = executor.submit(pool.request, payload(), 3)
        try:
            assert entered.wait(3)
            assert worker.process.poll() is None
            assert pool.request(payload(), 3)['status'] == 'busy'
            assert len(pool.snapshot()['workers']) == 1
        finally:
            release.set()
        assert request.result()['status'] == 'complete'
    assert worker.closed


def test_idle_retirement_also_holds_its_slot_until_reaped(pools, monkeypatch):
    pool = pools(max_workers=1, idle_seconds=.2)
    assert pool.request(payload(), 3)['status'] == 'complete'
    worker = next(iter(pool._workers))
    entered, release = threading.Event(), threading.Event()
    original = worker.close
    def held_close():
        entered.set()
        assert release.wait(5)
        original()
    monkeypatch.setattr(worker, 'close', held_close)
    try:
        assert entered.wait(3)
        assert pool.request(payload(), 3)['status'] == 'busy'
        assert worker.process.poll() is None
    finally:
        release.set()
    wait_until(lambda: not pool.snapshot()['workers'])
    assert worker.closed


def test_old_idle_timer_cannot_kill_a_new_lease(pools):
    pool = pools()
    assert pool.request(payload(), 3)['status'] == 'complete'
    worker = next(iter(pool._workers))
    generation = worker.generation
    with ThreadPoolExecutor(max_workers=1) as executor:
        request = executor.submit(pool.request, payload('slow'), 3)
        wait_until(lambda: worker.state == 'leased')
        pool._expire(worker, generation)
        assert worker.process.poll() is None
        assert request.result()['status'] == 'complete'
    assert worker in pool._workers and not worker.closed


def test_failed_idle_timer_creation_retires_instead_of_leaking_a_child(pools, monkeypatch):
    pool = pools()
    def unavailable(*args):
        raise RuntimeError('cannot start timer thread')
    monkeypatch.setattr(pool, '_timer', unavailable)
    assert pool.request(payload(), 3)['status'] == 'complete'
    assert pool.snapshot()['workers'] == []
    assert pool.request(payload(), 3)['status'] == 'complete'
    assert pool.snapshot()['workers'] == []


def test_failed_timer_retry_reaps_idle_child_before_allowing_new_startup(pools, monkeypatch):
    pool = pools()
    assert pool.request(payload(), 3)['status'] == 'complete'
    worker = next(iter(pool._workers))
    def unavailable(*args):
        raise RuntimeError('cannot start timer thread')
    monkeypatch.setattr(pool, '_timer', unavailable)
    assert pool._slots.acquire(False) and pool._slots.acquire(False)
    try:
        pool._expire(worker, worker.generation)
        assert worker.closed and worker.process.poll() is not None
        assert pool.snapshot()['workers'] == []
    finally:
        pool._slots.release()
        pool._slots.release()
