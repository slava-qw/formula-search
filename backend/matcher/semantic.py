"""Optional scalar algebra, isolated so one hard expression cannot hang search."""
import atexit
from pathlib import Path
import sys
import threading
from .worker_pool import ScalarWorkerPool

_pool_lock = threading.Lock()
_pool = None
MAX_CANDIDATES = 80
MAX_EXPRESSION_CHARS = 512
WORKER_TIMEOUT_SECONDS = 4.0


def _get_pool():
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ScalarWorkerPool(
                [sys.executable, '-u', '-m', 'backend.matcher.semantic_worker', '--serve'],
                str(Path(__file__).resolve().parents[2]))
        return _pool


def shutdown_semantic_workers():
    global _pool
    # Do not create a replacement pool until the old children are reaped.
    with _pool_lock:
        if _pool is not None:
            _pool.close()
            _pool = None


atexit.register(shutdown_semantic_workers)


def equivalent_candidates(query: str, candidates, budget: float = 0.8):
    """Return (candidate indices, status). Status describes incomplete checks.

The child accepts only data and uses the project's LaTeX parser, never eval.
The wall timeout covers startup, writing, parsing, equals() and reading. The
lazy pool retains at most two children, for 32 requests or 60 seconds idle, and
kills/reaps expired or invalid workers before releasing their capacity.
"""
    if budget <= 0 or len(query) > MAX_EXPRESSION_CHARS:
        return [], "limited"
    eligible = [(i, text) for i, text in candidates if len(text) <= MAX_EXPRESSION_CHARS]
    limited = len(eligible) != len(candidates) or len(eligible) > MAX_CANDIDATES
    eligible = eligible[:MAX_CANDIDATES]
    if not eligible:
        return [], "limited" if limited else "complete"
    result = _get_pool().request({
        "query": query, "candidates": eligible, "budget": min(budget, 1.5),
    }, timeout=WORKER_TIMEOUT_SECONDS)
    status = result['status']
    return result['indices'], 'limited' if limited and status == 'complete' else status
