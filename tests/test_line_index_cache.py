import base64
from concurrent.futures import ThreadPoolExecutor
import gzip
import json
import math
import os
from pathlib import Path
import struct
import time

import fitz
import pytest

from backend import cache, line_index_cache as stored
from backend.extractor import arxiv_tex as tex
from backend.extractor.text_layer import FormulaCandidate


def sample():
    box = (math.nextafter(1.0, 2.0), 2.0, 3.0, 4.0)
    first = tex._PdfLine(0, 'x α', box, 612, 792, 'xalpha', [('x', box), ('a', box)],
                         {'x', 'alpha'}, nearby_text='Before\nx α\nafter', raw_chars=[('x', box), ('α', box)])
    second = tex._PdfLine(1, 'y', (5, 6, 7, 8), 612, 792, 'y', [('y', box)], {'y'},
                          is_group=True, group_kind='stack', line_index=1, page_rotation=90)
    return tex.LineIndex([first, second], ({'Intro': [first, second]}, {'Intro': [second]}),
                          {'Intro': ((0, 10.0), (1, 20.0))}, {0: (612, 792), 1: (792, 612)})


def key():
    return stored.make_key(b'PDF identity', [FormulaCandidate('x', 'tex', section='Intro')], 2)


def path(value=None):
    return stored._root() / f'{value or key()}.json.gz'


def test_exact_round_trip_preserves_coordinates_fields_and_shared_line_references():
    original = sample()
    stored.store(key(), original)
    restored = stored.load(key(), 2)
    assert restored == original
    assert restored.match_lines[0].norm_chars[0][1][0] == math.nextafter(1.0, 2.0)
    assert restored.section_line_cache[0]['Intro'][0] is restored.match_lines[0]
    assert restored.section_line_cache[1]['Intro'][0] is restored.match_lines[1]
    assert not list(stored._root().glob('*.tmp'))


def test_identity_tracks_pdf_sections_page_count_and_parser_versions(monkeypatch):
    a = FormulaCandidate('x', 'tex', section='Intro')
    b = FormulaCandidate('y', 'tex', section='Methods')
    base = stored.make_key(b'a', [a, b], 2)
    assert base == stored.make_key(b'a', [b, a, a], 2)
    assert base != stored.make_key(b'b', [a, b], 2)
    assert base != stored.make_key(b'a', [a], 2)
    assert base != stored.make_key(b'a', [a, b], 3)
    monkeypatch.setattr(cache, 'CACHE_VERSION', cache.CACHE_VERSION + 1)
    assert base != stored.make_key(b'a', [a, b], 2)
    after_source = stored.make_key(b'a', [a, b], 2)
    monkeypatch.setattr(cache, 'PDF_TEXT_CACHE_VERSION', cache.PDF_TEXT_CACHE_VERSION + 1)
    assert after_source != stored.make_key(b'a', [a, b], 2)
    after_pdf = stored.make_key(b'a', [a, b], 2)
    monkeypatch.setattr(fitz, 'VersionBind', 'different-engine')
    assert after_pdf != stored.make_key(b'a', [a, b], 2)


@pytest.mark.parametrize('mutate', [
    lambda data: data.update(schema=True),
    lambda data: data.update(schema=999),
    lambda data: data.update(key='0' * 64),
    lambda data: data.update(boxes_le_f64='not-base64!'),
    lambda data: data.update(boxes_le_f64=base64.b64encode(b'short').decode()),
    lambda data: data.update(boxes_le_f64=base64.b64encode(struct.pack('<dddd', float('nan'), 1, 2, 3)).decode()),
    lambda data: data['glyphs'][0].__setitem__(1, -1),
    lambda data: data['glyphs'][0].__setitem__(1, True),
    lambda data: data['lines'][0].__setitem__(0, 2),
    lambda data: data['lines'][0].__setitem__(6, [len(data['glyphs'])]),
    lambda data: data['lines'][0].__setitem__(15, 45),
    lambda data: data['lines'][0].__setitem__(9, float('inf')),
    lambda data: data['page_dims'].__setitem__('0', [0, 792]),
    lambda data: data['sections'][0].__setitem__('Intro', [999]),
    lambda data: data['regions'].__setitem__('Intro', [[3, 10], None]),
])
def test_invalid_cache_data_is_a_miss_and_can_be_rebuilt(mutate):
    stored.store(key(), sample())
    data = json.loads(gzip.decompress(path().read_bytes()))
    mutate(data)
    path().write_bytes(gzip.compress(json.dumps(data).encode()))
    assert stored.load(key(), 2) is None
    stored.store(key(), sample())
    assert stored.load(key(), 2) == sample()


@pytest.mark.parametrize('content', [b'', b'not gzip', gzip.compress(b'null'), gzip.compress(b'{broken')])
def test_truncated_or_malformed_files_are_cache_misses(content):
    stored.store(key(), sample())
    path().write_bytes(content)
    assert stored.load(key(), 2) is None


def test_decode_size_and_reference_limits_fail_closed(monkeypatch):
    stored.store(key(), sample())
    with monkeypatch.context() as patch:
        patch.setattr(stored, 'MAX_COMPRESSED_BYTES', 1)
        assert stored.load(key(), 2) is None
    with monkeypatch.context() as patch:
        patch.setattr(stored, 'MAX_DECODED_BYTES', 32)
        assert stored.load(key(), 2) is None
    with monkeypatch.context() as patch:
        patch.setattr(stored, 'MAX_REFERENCES', 1)
        assert stored.load(key(), 2) is None
    assert stored.load(key(), 2) == sample()


def test_over_budget_write_keeps_an_existing_complete_entry(monkeypatch):
    stored.store(key(), sample())
    before = path().read_bytes()
    monkeypatch.setattr(stored, 'MAX_DECODED_BYTES', 1)
    stored.store(key(), sample())
    assert path().read_bytes() == before


def test_atomic_replace_failure_preserves_previous_entry_and_removes_its_temp(monkeypatch):
    stored.store(key(), sample())
    before = path().read_bytes()
    monkeypatch.setattr(Path, 'replace', lambda *args: (_ for _ in ()).throw(PermissionError('busy')))
    stored.store(key(), sample())
    assert path().read_bytes() == before
    assert not list(stored._root().glob('*.tmp'))


def test_read_only_cache_remains_usable_when_access_time_cannot_be_refreshed(monkeypatch):
    stored.store(key(), sample())
    monkeypatch.setattr(Path, 'touch', lambda *a, **kw: (_ for _ in ()).throw(PermissionError('readonly')))
    assert stored.load(key(), 2) == sample()


def test_entry_and_byte_budgets_preserve_recent_reads_and_unrelated_files(monkeypatch):
    monkeypatch.setattr(stored, 'MAX_ENTRIES', 2)
    original = sample()
    keys = [stored.make_key(str(i).encode(), [], 2) for i in range(4)]
    stored.store(keys[0], original)
    stored.store(keys[1], original)
    unrelated = stored._root() / 'user-note.json.gz'
    unrelated.write_bytes(b'keep')
    sibling = cache.CACHE_DIR / 'other-cache.json'
    sibling.write_bytes(b'keep too')
    old = time.time() - 20
    os.utime(path(keys[0]), (old, old))
    os.utime(path(keys[1]), (old + 1, old + 1))
    assert stored.load(keys[0], 2) == original
    stored.store(keys[2], original)
    assert stored.load(keys[1], 2) is None
    assert stored.load(keys[0], 2) == original
    monkeypatch.setattr(stored, 'MAX_BYTES', path(keys[2]).stat().st_size + 10)
    stored.store(keys[3], original)
    own = [p for p in stored._root().glob('*.json.gz') if stored._HEX.fullmatch(p.name[:-8])]
    assert sum(p.stat().st_size for p in own) <= stored.MAX_BYTES
    assert stored.load(keys[3], 2) == original
    assert unrelated.read_bytes() == b'keep' and sibling.read_bytes() == b'keep too'


def test_expired_entries_are_misses_and_pruned_on_the_next_write():
    stored.store(key(), sample())
    old = time.time() - stored.MAX_AGE_SECONDS - 10
    os.utime(path(), (old, old))
    assert stored.load(key(), 2) is None
    stored.store('1' * 64, sample())
    assert not path().exists()


def test_just_written_entry_is_most_recent_even_if_filesystem_timestamps_sort_it_earlier(monkeypatch):
    monkeypatch.setattr(stored, 'MAX_ENTRIES', 1)
    stored.store('f' * 64, sample())
    original = stored._prune
    def coarse_timestamps(root, **kwargs):
        now = time.time()
        os.utime(path('f' * 64), (now, now))
        os.utime(path('0' * 64), (now - 1, now - 1))
        return original(root, **kwargs)
    monkeypatch.setattr(stored, '_prune', coarse_timestamps)
    stored.store('0' * 64, sample())
    assert stored.load('0' * 64, 2) == sample()
    assert stored.load('f' * 64, 2) is None


def test_concurrent_writes_leave_one_complete_entry():
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: stored.store(key(), sample()), range(12)))
    assert stored.load(key(), 2) == sample()
    assert not list(stored._root().glob('*.tmp'))


def pdf_and_candidates():
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((50, 60), '1 Introduction', fontname='hebo', fontsize=15)
        page.insert_text((50, 100), 'x + y = 3')
        pdf = document.tobytes()
    return pdf, [FormulaCandidate('x+y=3', 'tex', section='Introduction')]


def test_preparation_reuses_a_cached_index_and_corruption_rebuilds_it(monkeypatch):
    pdf, candidates = pdf_and_candidates()
    original = tex.build_line_index
    calls = []
    def build(*args):
        calls.append(1)
        return original(*args)
    monkeypatch.setattr(tex, 'build_line_index', build)
    first = tex._prepare_arxiv_document('', pdf, candidates, False, str(cache.CACHE_DIR))
    second = tex._prepare_arxiv_document('', pdf, candidates, False, str(cache.CACHE_DIR))
    assert first == second and len(calls) == 1 and first[2] == 1 and not first[3]
    file = path(stored.make_key(pdf, candidates, 1))
    file.write_bytes(b'broken')
    third = tex._prepare_arxiv_document('', pdf, candidates, False, str(cache.CACHE_DIR))
    assert third == first and len(calls) == 2


def test_unwritable_optional_cache_does_not_discard_a_valid_pdf(monkeypatch):
    pdf, candidates = pdf_and_candidates()
    monkeypatch.setattr(stored.tempfile, 'mkstemp', lambda *a, **kw: (_ for _ in ()).throw(PermissionError('readonly')))
    prepared = tex._prepare_arxiv_document('', pdf, candidates, False, str(cache.CACHE_DIR))
    assert prepared[1] is not None and prepared[2] == 1 and prepared[3] == ''


def test_direct_preparation_remains_cache_free_for_diagnostic_callers(monkeypatch):
    pdf, candidates = pdf_and_candidates()
    monkeypatch.setattr(stored, 'load', lambda *a, **kw: pytest.fail('Not opted into persistence'))
    assert tex._prepare_arxiv_document('', pdf, candidates, False)[1] is not None


@pytest.mark.asyncio
async def test_parent_cache_directory_is_explicitly_sent_to_the_worker(monkeypatch):
    from backend import processing
    pdf, candidates = pdf_and_candidates()
    async def identity(value): return value
    async def run(function, *args):
        assert args[-1] == str(cache.CACHE_DIR)
        return function(*args)
    monkeypatch.setattr(tex, 'resolve_arxiv_id', identity)
    monkeypatch.setattr(tex, 'load_pdf_artifact', lambda _: pdf)
    monkeypatch.setattr(processing, 'run_document_job', run)
    document = await tex.fetch_arxiv_document('1706.03762v7', cached_candidates=candidates)
    assert document.line_index is not None and document.page_count == 1
    assert path(stored.make_key(pdf, candidates, 1)).exists()
