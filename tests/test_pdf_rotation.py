"""Actual PDF /Rotate and crop geometry, including source and cached PDF paths."""
from pathlib import Path

import fitz
import pytest

from backend import cache
from backend.extractor.text_layer import FormulaCandidate, extract_formulas
from backend.extractor.arxiv_tex import assign_bboxes_from_pdf, build_line_index
from backend.matcher.index import build_index
from backend.matcher.fuzzy import search_formulas


def fixture(rotation, crop, unit=1):
    with fitz.open() as doc:
        page = doc.new_page(width=400, height=700)
        page.insert_text((50, 620), 'x=2+y', fontname='tiit', fontsize=12)
        if crop:
            page.set_cropbox(fitz.Rect(20, 40, 380, 680))
        page.set_rotation(rotation)
        expected = page.search_for('2+y')[0]*page.rotation_matrix*fitz.Matrix(unit, unit)
        full_expected = page.search_for('x=2+y')[0]*page.rotation_matrix*fitz.Matrix(unit, unit)
        # Measure the reference geometry before /UserUnit scales the same PDF.
        # MuPDF 1.27.2's rotation_matrix translation itself is not unit-scaled.
        if unit != 1:
            doc.xref_set_key(page.xref, 'UserUnit', str(unit))
            page = doc.reload_page(page)
        return doc.tobytes(), tuple(expected), tuple(page.rect)[2:], tuple(full_expected)


@pytest.mark.parametrize('rotation', [0, 90, 180, 270])
@pytest.mark.parametrize('crop', [False, True])
@pytest.mark.parametrize('source', ['text', 'tex'])
@pytest.mark.parametrize('unit', [1, 2])
def test_intrinsic_rotation_preserves_full_and_subexpression_locations(rotation, crop, source, unit, tmp_path, monkeypatch):
    raw, expected, size, full_expected = fixture(rotation, crop, unit)
    if source == 'text':
        candidates = extract_formulas(raw)
        monkeypatch.setattr(cache, 'CACHE_DIR', tmp_path)
        cache.store_candidates_cache('pdf-text', 'rotated', candidates)
        candidates = cache.load_candidates_cache('pdf-text', 'rotated')
    else:
        candidates = [FormulaCandidate('x=2+y', 'tex', env='equation')]
        assign_bboxes_from_pdf(candidates, raw)
    candidate = next(c for c in candidates if c.latex.replace(' ', '') == 'x=2+y')
    assert candidate.page_rotation == rotation
    # Extraction/mapping remain in the original text space, before /Rotate.
    assert candidate.bbox[1] > 550
    match, = search_formulas('2+y', build_index(candidates))
    assert match.bbox == pytest.approx(expected, abs=.01)
    assert (match.page_w, match.page_h) == size
    full, = search_formulas('x=2+y', build_index(candidates), mode='exact')
    assert full.bbox is not None and full.page == 0
    assert full.bbox == pytest.approx(full_expected, abs=.01)


@pytest.mark.parametrize('rotation', [90, 180, 270])
def test_lazy_source_http_result_copies_rotation_before_refining_bbox(rotation, monkeypatch):
    import backend.main as main
    raw, expected, size, _ = fixture(rotation, True)
    candidates = [FormulaCandidate('x=2+y', 'tex', env='equation')]
    session = main.SessionState(index_cache=build_index(candidates), pdf_bytes=raw, source_type='tex')
    main._set_lazy_bbox_state(session, candidates, build_line_index(raw, candidates))
    monkeypatch.setattr(main, '_get_session', lambda url: session)
    # Calls the real endpoint serializer and lazy callback, without a server.
    for _ in range(2):
        result, = main.search(main.SearchRequest(query='2+y', url='file:///rotation.pdf'))['matches']
        assert result['bbox'] == pytest.approx(expected, abs=.01)
        assert (result['page_w'], result['page_h']) == size


@pytest.mark.parametrize('rotation', [90, 180, 270])
def test_rotated_attention_fraction_uses_original_glyph_mapping(rotation):
    path = Path(__file__).resolve().parents[1]/'testdata/papers/1706.03762v7.pdf'
    if not path.exists():
        pytest.skip('Restore external fixtures with python tools/fetch_paper_fixtures.py')
    query = r'\frac{QK^T}{\sqrt{d_k}}'
    original, = search_formulas(query, build_index(extract_formulas(path.read_bytes())))
    with fitz.open(path) as doc:
        page = doc[3]
        page.set_rotation(rotation)
        expected = tuple(fitz.Rect(original.bbox)*page.rotation_matrix)
        raw = doc.tobytes()
        size = page.rect.width, page.rect.height
    match, = search_formulas(query, build_index(extract_formulas(raw)))
    assert match.page == 3
    assert match.bbox == pytest.approx(expected, abs=.01)
    assert (match.page_w, match.page_h) == size
