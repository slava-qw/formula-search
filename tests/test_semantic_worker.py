import sys
import time
import pytest

from backend.matcher import semantic
from backend.matcher import worker_pool


def test_worker_is_killed_and_reaped_on_hard_timeout(monkeypatch):
    assert semantic.WORKER_TIMEOUT_SECONDS == 4.0
    pool = worker_pool.ScalarWorkerPool([sys.executable, '-c', 'import time; time.sleep(30)'], None)
    retired = []
    original = worker_pool._Worker.close
    def close(worker):
        original(worker)
        retired.append(worker)
    monkeypatch.setattr(worker_pool._Worker, 'close', close)
    monkeypatch.setattr(semantic, '_get_pool', lambda: pool)
    try:
        start = time.monotonic()
        indices, status = semantic.equivalent_candidates("x+x", [(0, "2*x")])
        assert indices == [] and status == "timeout"
        assert 3.8 <= time.monotonic()-start < 8
        assert len(retired) == 1
        assert retired[0].closed and retired[0].process.poll() is not None
        assert not retired[0].thread.is_alive()
    finally:
        pool.close()


def test_oversized_expression_does_not_start_worker(monkeypatch):
    monkeypatch.setattr(worker_pool.subprocess, "Popen", lambda *a, **kw: pytest.fail("worker should not start"))
    assert semantic.equivalent_candidates("x" * 513, [(0, "x")]) == ([], "limited")


def test_worker_unavailability_does_not_break_search(monkeypatch):
    def unavailable(*args, **kwargs):
        raise OSError("test executable unavailable")
    monkeypatch.setattr(worker_pool.subprocess, "Popen", unavailable)
    assert semantic.equivalent_candidates("x+x", [(0, "2*x")]) == ([], "unavailable")


def test_supplementary_unicode_at_candidate_limit_fits_reusable_protocol(monkeypatch):
    monkeypatch.setattr(semantic, 'WORKER_TIMEOUT_SECONDS', 15)
    candidates = [(i, '𝛼'*512) for i in range(80)]
    # Unsupported query avoids spending the comparison budget on long products;
    # this verifies transport of permitted input, not symbolic parsing speed.
    assert semantic.equivalent_candidates('(', candidates) == ([], 'unsupported')


def test_api_lifespan_reaps_reusable_scalar_children(monkeypatch):
    from fastapi.testclient import TestClient
    from backend.main import app
    monkeypatch.setattr(semantic, 'WORKER_TIMEOUT_SECONDS', 15)
    with TestClient(app):
        assert semantic.equivalent_candidates('x+x', [(0, '2*x')]) == ([0], 'complete')
        pool = semantic._get_pool()
        worker = next(iter(pool._workers))
        assert worker.process.poll() is None
    assert worker.closed and worker.process.poll() is not None
    assert pool.snapshot() == {'closed': True, 'workers': []}
