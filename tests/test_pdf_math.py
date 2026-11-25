"""End-to-end rendered-PDF extraction tests; no synthetic candidate objects."""
import fitz
import pytest

from backend.extractor.text_layer import extract_formulas
from backend.matcher.index import build_index
from backend.matcher.fuzzy import search_formulas


def _pdf(write):
    with fitz.open() as doc:
        page = doc.new_page()
        write(page)
        return doc.tobytes()


def _search(pdf, query, **kwargs):
    return search_formulas(query, build_index(extract_formulas(pdf)), **kwargs)


def test_plain_rendered_equation_without_latex_markers():
    pdf = _pdf(lambda p: p.insert_text((50, 100), 'x = 2 + y', fontsize=12))
    matches = _search(pdf, 'x=2+y')
    assert matches and matches[0].bbox_source == 'query-subspan'
    assert not _search(pdf, 'x=2-y')
    assert not _search(pdf, 'x=2y')


@pytest.mark.parametrize(('y', 'query', 'wrong'), [(95, 'x^2', 'x_2'), (103, 'x_2', 'x^2')])
def test_scripts_come_from_size_and_position(y, query, wrong):
    def write(p):
        p.insert_text((50, 100), 'x', fontname='tiit', fontsize=12)
        p.insert_text((57, y), '2', fontsize=8)
        p.insert_text((66, 100), '+y', fontname='tiit', fontsize=12)
    pdf = _pdf(write)
    matches = _search(pdf, query)
    assert matches
    assert not _search(pdf, wrong)
    assert not _search(pdf, 'x2')
    assert matches[0].bbox[0] == pytest.approx(50)
    assert matches[0].bbox[2] < 65  # query excludes the adjacent +y


def test_superscript_and_subscript_split_across_raw_pdf_lines():
    def write(p):
        p.insert_text((50, 100), 'W', fontname='tiit', fontsize=12)
        p.insert_text((61, 94), 'Q', fontname='tiit', fontsize=8)
        p.insert_text((61, 104), 'i', fontname='tiit', fontsize=8)
    matches = _search(_pdf(write), 'W^Q_i')
    assert matches and matches[0].latex == 'W_{i}^{Q}'
    assert matches[0].bbox[2] > 66


def test_adjacent_indices_keep_their_own_base():
    def write(p):
        p.insert_text((50, 100), 'w', fontname='tiit', fontsize=12)
        p.insert_text((58, 103), 'i', fontname='tiit', fontsize=8)
        p.insert_text((61, 100), 'x', fontname='tiit', fontsize=12)
        p.insert_text((67, 103), 'i', fontname='tiit', fontsize=8)
    pdf = _pdf(write)
    assert _search(pdf, 'w_i x_i')
    assert not _search(pdf, 'w_{ii}x')


def test_fraction_bar_prevents_a_false_script():
    def write(p):
        p.insert_text((50, 100), 'x', fontname='tiit', fontsize=12)
        p.insert_text((57, 94), 'y', fontname='tiit', fontsize=8)
        p.draw_line((56, 96), (65, 96), width=.4)
    pdf = _pdf(write)
    assert _search(pdf, 'x') and _search(pdf, 'y')
    assert not _search(pdf, 'x^y')
    assert not _search(pdf, 'xy')


def test_stacked_same_size_rows_never_flatten_to_multiplication():
    def write(p):
        p.insert_text((50, 90), 'x', fontname='tiit', fontsize=12)
        p.insert_text((50, 110), 'y', fontname='tiit', fontsize=12)
        p.draw_line((49, 98), (59, 98), width=.4)
    pdf = _pdf(write)
    assert _search(pdf, 'x') and _search(pdf, 'y')
    assert not _search(pdf, 'xy')


@pytest.mark.parametrize('text', [
    'Hello world, this is a sentence.',
    'Download https://example.com/a_file.pdf for details.',
    'The cost is $20 per month.',
    'The cost rose from $20 to $30 per month.',
    'This is a heading with italic emphasis.',
])
def test_prose_and_addresses_are_not_formula_candidates(text):
    pdf = _pdf(lambda p: p.insert_text((50, 100), text, fontname='tiit', fontsize=12))
    candidates = extract_formulas(pdf)
    # Single italic letters are ambiguous, but whole prose must not be indexed.
    assert all(len(c.latex) <= 1 for c in candidates)
    assert not any('example' in c.latex or 'month' in c.latex for c in candidates)


def test_inline_math_does_not_absorb_surrounding_prose():
    def write(p):
        p.insert_text((50, 100), 'Given the value', fontsize=12)
        p.insert_text((140, 100), 'x', fontname='tiit', fontsize=12)
        p.insert_text((147, 103), 'i', fontname='tiit', fontsize=8)
        p.insert_text((155, 100), 'we can continue.', fontsize=12)
    pdf = _pdf(write)
    matches = _search(pdf, 'x_i')
    assert matches and matches[0].latex == 'x_{i}'
    assert not _search(pdf, 'continue')
    assert not _search(pdf, 'value')


def test_pdf_glyph_mapping_survives_cache_roundtrip(tmp_path, monkeypatch):
    from backend import cache
    monkeypatch.setattr(cache, 'CACHE_DIR', tmp_path)
    pdf = _pdf(lambda p: p.insert_text((50, 100), 'x = 2 + y', fontsize=12))
    candidates = extract_formulas(pdf)
    cache.store_candidates_cache('pdf-text', 'new', candidates)
    loaded = cache.load_candidates_cache('pdf-text', 'new')
    assert loaded == candidates
    result = search_formulas('2+y', build_index(loaded))[0]
    assert result.bbox[0] > candidates[0].bbox[0]
    assert result.bbox_source == 'query-subspan'


@pytest.mark.parametrize('old_pdf_version', [8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19])
def test_previous_pdf_cache_is_rebuilt_but_tex_cache_remains_valid(tmp_path, monkeypatch, old_pdf_version):
    import gzip
    import json
    from backend import cache
    monkeypatch.setattr(cache, 'CACHE_DIR', tmp_path)
    pdf = _pdf(lambda p: p.insert_text((50, 100), 'x=2', fontsize=12))
    for kind in ('pdf-text', 'arxiv-source'):
        cache.store_candidates_cache(kind, 'old', extract_formulas(pdf))
        path = cache._cache_path(kind, 'old')
        with gzip.open(path, 'rt') as f:
            value = json.load(f)
        value['version'] = old_pdf_version if kind == 'pdf-text' else cache.CACHE_VERSION
        with gzip.open(path, 'wt') as f:
            json.dump(value, f)
    assert cache.load_candidates_cache('pdf-text', 'old') is None
    assert cache.load_candidates_cache('arxiv-source', 'old') is not None


def test_bold_variables_remain_distinct_from_plain_variables():
    def write(p):
        p.insert_text((50, 100), 'w', fontname='tibo', fontsize=12)
        p.insert_text((60, 100), '=x', fontname='tiit', fontsize=12)
    pdf = _pdf(write)
    assert _search(pdf, r'\mathbf{w}=x')
    assert not _search(pdf, 'w=x')


def test_real_pdf_only_locations_and_negative_queries():
    import hashlib
    from tools.audit_pdf_text import PROBES, NEGATIVE_PROBES, EXCLUDED_REGIONS, overlaps, ROOT
    from tools.audit_formula_mapping import DEFAULT_PAPERS
    checked_papers = 0
    for paper in dict.fromkeys(probe['paper'] for probe in PROBES):
        path = DEFAULT_PAPERS[paper]['pdf'] if paper in DEFAULT_PAPERS else ROOT / f'testdata/benchmarks/artifacts/{paper}.pdf'
        if not path.exists():
            path = ROOT / f'testdata/papers/{paper}.pdf'
        if not path.exists():
            continue  # additional downloaded corpus is optional for fresh clones
        pdf = path.read_bytes()
        checked_papers += 1
        candidates = extract_formulas(pdf)
        assert all(len(c.latex) == len(c.pdf_latex_chars) for c in candidates)
        index = build_index(candidates)
        for probe in (p for p in PROBES if p['paper'] == paper):
            assert hashlib.sha256(pdf).hexdigest() == probe['sha256']
            matches = search_formulas(probe['query'], index)
            assert any(m.page == probe['page'] and m.bbox and m.bbox_source == 'query-subspan'
                       and all(abs(a-b)<1 for a,b in zip(m.bbox, probe['bbox'])) for m in matches), probe
            for wrong_query in probe.get('reject_queries', []):
                assert not any(m.page == probe['page'] and m.bbox and all(abs(a-b)<1 for a,b in zip(m.bbox, probe['bbox']))
                               for m in search_formulas(wrong_query, index)), (probe, wrong_query)
        for probe in (p for p in NEGATIVE_PROBES if p['paper'] == paper):
            assert not search_formulas(probe['query'], index), probe
        for region in (p for p in EXCLUDED_REGIONS if p['paper'] == paper):
            assert hashlib.sha256(pdf).hexdigest() == region['sha256']
            assert not any(c.page == region['page'] and overlaps(c.bbox, region['bbox']) for c in candidates), region
    if not checked_papers:
        pytest.skip('Restore external PDF fixtures with python tools/fetch_paper_fixtures.py')
