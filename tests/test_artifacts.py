from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
import time
import pytest

from backend import artifacts, cache


def test_pdf_cache_checks_version_and_content_hash():
    artifacts.store_pdf_artifact('1706.03762v7', b'first PDF')
    assert artifacts.load_pdf_artifact('1706.03762v7') == b'first PDF'
    assert artifacts.load_pdf_artifact('1706.03762v6') is None
    assert artifacts.load_pdf_artifact('1706.03762') is None
    path = artifacts._root() / (hashlib.sha256(b'first PDF').hexdigest() + '.pdf')
    path.write_bytes(b'other PDF')
    assert artifacts.load_pdf_artifact('1706.03762v7') is None


def test_reference_cannot_alias_another_paper():
    artifacts.store_pdf_artifact('1706.03762v7', b'first PDF')
    reference = artifacts._reference(artifacts._root(), '1706.03762v7')
    data = json.loads(reference.read_bytes())
    data['arxiv_id'] = '1706.09999v1'
    reference.write_text(json.dumps(data))
    assert artifacts.load_pdf_artifact('1706.03762v7') is None


@pytest.mark.parametrize('payload', [b'null', b'[]', b'{broken', b'{"schema":1,"sha256":null}'])
def test_malformed_cache_metadata_is_a_miss_and_does_not_break_future_writes(payload):
    artifacts.store_pdf_artifact('1706.03762v7', b'first PDF')
    artifacts._reference(artifacts._root(), '1706.03762v7').write_bytes(payload)
    assert artifacts.load_pdf_artifact('1706.03762v7') is None
    artifacts.store_pdf_artifact('1706.03762v8', b'new PDF')
    assert artifacts.load_pdf_artifact('1706.03762v8') == b'new PDF'


def test_artifact_lru_budget_preserves_recent_reads_and_unrelated_files(monkeypatch):
    monkeypatch.setattr(artifacts, 'MAX_BYTES', 8)
    cache.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    unrelated = cache.CACHE_DIR / 'existing-cache.json'
    unrelated.write_text('keep')
    artifacts.store_pdf_artifact('1706.00001v1', b'aaaa')
    artifacts.store_pdf_artifact('1706.00002v1', b'bbbb')
    old = time.time() - 10
    os.utime(artifacts._reference(artifacts._root(), '1706.00002v1'), (old, old))
    assert artifacts.load_pdf_artifact('1706.00001v1') == b'aaaa'
    artifacts.store_pdf_artifact('1706.00003v1', b'cccc')
    assert artifacts.load_pdf_artifact('1706.00002v1') is None
    assert artifacts.load_pdf_artifact('1706.00001v1') == b'aaaa'
    assert artifacts.load_pdf_artifact('1706.00003v1') == b'cccc'
    assert sum(p.stat().st_size for p in artifacts._root().glob('*.pdf')) <= 8
    assert unrelated.read_text() == 'keep'


def test_artifact_entry_expiry_and_deduplicated_blobs(monkeypatch):
    monkeypatch.setattr(artifacts, 'MAX_ENTRIES', 2)
    for i in range(1, 4):
        artifacts.store_pdf_artifact(f'1706.0000{i}v1', b'same bytes')
    assert len(list(artifacts._root().glob('*.json'))) == 2
    assert len(list(artifacts._root().glob('*.pdf'))) == 1
    reference = artifacts._reference(artifacts._root(), '1706.00003v1')
    old = time.time() - artifacts.MAX_AGE_SECONDS - 10
    os.utime(reference, (old, old))
    assert artifacts.load_pdf_artifact('1706.00003v1') is None
    artifacts.store_pdf_artifact('1706.00004v1', b'new')
    assert not reference.exists()


def test_concurrent_artifact_writes_are_complete_and_cleanup_temporary_files():
    values = [f'PDF-{i}'.encode() for i in range(20)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda value: artifacts.store_pdf_artifact('1706.03762v7', value), values))
    assert artifacts.load_pdf_artifact('1706.03762v7') in values
    assert not list(artifacts._root().glob('*.tmp'))
