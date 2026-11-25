import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest


@pytest.fixture(autouse=True)
def isolate_scalar_workers():
    from backend.matcher.semantic import shutdown_semantic_workers
    shutdown_semantic_workers()
    yield
    shutdown_semantic_workers()


@pytest.fixture(autouse=True)
def reset_formula_cache():
    import backend.main
    from backend.sessions import SessionStore
    backend.main._sessions = SessionStore()
    backend.main._sessions_lock = backend.main._sessions.lock
    yield
    backend.main._sessions = SessionStore()
    backend.main._sessions_lock = backend.main._sessions.lock


@pytest.fixture(autouse=True)
def isolate_persistent_cache_dir(tmp_path, monkeypatch):
    """Keep tests off the bundled .formula_cache/ dir.

    Without this, tests that mock fetch_and_parse_arxiv/extract_formulas for a
    real bundled paper (e.g. arXiv 1706.03762) can get a cache hit from the
    warm-started .formula_cache/ fixtures instead of exercising the mock.
    """
    import backend.cache
    monkeypatch.setattr(backend.cache, "CACHE_DIR", tmp_path / "formula_cache")


@pytest.fixture(autouse=True)
def isolate_arxiv_version_resolver(monkeypatch):
    import backend.arxiv_versions as versions
    monkeypatch.setattr(versions, '_resolver', versions.ArxivVersionResolver(interval=0))
