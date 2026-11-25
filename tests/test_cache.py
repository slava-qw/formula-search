from backend.cache import load_candidates_cache, store_candidates_cache
from backend.extractor.text_layer import FormulaCandidate
from backend.matcher.index import build_index
import pytest


def test_candidate_cache_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr('backend.cache.CACHE_DIR', tmp_path)
    c = FormulaCandidate(
        latex=r"x_i^2",
        source="tex",
        env="inline",
        page=2,
        bbox=(10.0, 20.0, 30.0, 40.0),
        page_w=100.0,
        page_h=200.0,
        pdf_line_text="x_i^2",
        pdf_line_norm="xi2",
        pdf_line_norm_chars=[('x', (10.0, 20.0, 14.0, 30.0)), ('i', (15.0, 25.0, 18.0, 35.0))],
    )
    store_candidates_cache('unit', 'paper:1', [c])
    loaded = load_candidates_cache('unit', 'paper:1')
    assert loaded is not None
    assert len(loaded) == 1
    assert loaded[0].latex == c.latex
    assert loaded[0].bbox == c.bbox
    assert loaded[0].pdf_line_norm_chars == c.pdf_line_norm_chars
    index = build_index(loaded)
    assert index[0].page == 2


@pytest.mark.parametrize('old_version', [5, 8, 9, 10, 11])
def test_old_source_cache_is_rejected_without_deleting_file(tmp_path, monkeypatch, old_version):
    import gzip
    import json
    from backend import cache
    monkeypatch.setattr(cache, 'CACHE_DIR', tmp_path)
    store_candidates_cache('arxiv-tex', 'paper:legacy', [FormulaCandidate('x+y', 'tex')])
    path = cache._cache_path('arxiv-tex', 'paper:legacy')
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        payload = json.load(stream)
    payload['version'] = old_version
    with gzip.open(path, 'wt', encoding='utf-8') as stream:
        json.dump(payload, stream)
    assert load_candidates_cache('arxiv-tex', 'paper:legacy') is None
    assert path.exists()


def test_concurrent_cache_writers_publish_whole_files_and_keep_previous_readable(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from backend import cache
    monkeypatch.setattr(cache, 'CACHE_DIR', tmp_path)
    store_candidates_cache('unit', 'shared', [FormulaCandidate('original', 'tex')])
    original_open = cache.gzip.open
    opened = []
    lock = threading.Lock()
    ready, release = threading.Event(), threading.Event()

    def paused_open(filename, mode, **kwargs):
        stream = original_open(filename, mode, **kwargs)
        if mode == 'wb':
            with lock:
                opened.append(str(filename))
                if len(opened) == 8:
                    ready.set()
            if not release.wait(5):
                stream.close()
                raise TimeoutError('Cache test did not release writers')
        return stream

    monkeypatch.setattr(cache.gzip, 'open', paused_open)
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(store_candidates_cache, 'unit', 'shared', [FormulaCandidate(f'x^{i}', 'tex')]) for i in range(8)]
        try:
            assert ready.wait(5)
            assert load_candidates_cache('unit', 'shared')[0].latex == 'original'
        finally:
            release.set()
        for future in futures:
            future.result()
    assert len(set(opened)) == 8
    assert load_candidates_cache('unit', 'shared')[0].latex in {f'x^{i}' for i in range(8)}
    assert not list(tmp_path.glob('*.tmp'))
