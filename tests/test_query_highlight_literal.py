"""Query-highlight contracts independent of the chosen scan/preparation path."""
import math
from dataclasses import asdict

import pytest

from backend.extractor.text_layer import FormulaCandidate
from backend.matcher.fuzzy import _query_specific_bbox, _single_literal_bbox, search_formulas
from backend.matcher.index import IndexedFormula, build_index


def candidate(raw, bbox=(0, 0, 2, 2), **kwargs):
    return IndexedFormula(latex='x', source='tex', bbox=bbox, page_w=100, page_h=100,
                          pdf_line_raw_chars=raw, **kwargs)


def test_single_literal_chooses_nearest_valid_glyph_without_changing_source_data():
    raw = [('x', (0, 0, 2, 2)), (' ', (2, 0, 3, 2)), ('x', (8, 8, 10, 10))]
    item = candidate(raw, (7, 7, 11, 11))
    assert _query_specific_bbox('x', item) == (8, 8, 10, 10)
    assert item.pdf_line_raw_chars is raw and raw == [('x', (0, 0, 2, 2)), (' ', (2, 0, 3, 2)), ('x', (8, 8, 10, 10))]
    assert item.bbox == (7, 7, 11, 11)


def test_single_literal_tie_uses_the_first_occurrence():
    item = candidate([('x', (0, 0, 2, 2)), ('x', (4, 0, 6, 2))], (2, 0, 4, 2))
    assert _query_specific_bbox('x', item) == (0, 0, 2, 2)


def test_single_literal_rejects_degenerate_and_nan_boxes_before_selecting():
    item = candidate([('x', (0, 0, 0, 1)), ('x', (2, 2, 1, 3)),
                      ('x', (0, 0, math.nan, 2)), ('x', (8, 8, 10, 10))])
    assert _query_specific_bbox('x', item) == (8, 8, 10, 10)


def test_minus_normalization_preserves_exact_fractional_coordinates():
    coordinate = math.nextafter(2.0, 3.0)
    box = (coordinate, 1.25, 4.125, 2.875)
    item = candidate([('\n', (0, 0, 0, 0)), ('−', box)])
    assert _query_specific_bbox('-', item) == box
    assert _query_specific_bbox('−', item) == box


def test_single_literal_clamps_to_page_before_distance_comparison():
    item = candidate([('x', (-3, -4, 2, 2)), ('x', (95, 95, 110, 120))], (95, 95, 100, 100))
    assert _query_specific_bbox('x', item) == (95, 95, 100, 100)


@pytest.mark.parametrize('raw', [None, [], [(' ', (0, 0, 2, 2)), ('\n', (0, 0, 0, 0))]])
def test_missing_literal_glyph_data_retains_normalized_symbol_fallback(raw):
    box = (0, 0, 2, 2)
    item = candidate(raw, pdf_line_norm='sqrt', pdf_line_norm_chars=[(ch, box) for ch in 'sqrt'],
                     pdf_line_has_math_signal=True)
    assert _query_specific_bbox('√', item) == box


def test_digit_boundary_rules_are_not_bypassed_by_single_glyph_search():
    item = candidate([(ch, (i*2, 0, i*2+2, 2)) for i, ch in enumerate('312')])
    assert _query_specific_bbox('1', item) is None
    assert _query_specific_bbox('12', item) is None


def test_nonmatching_literal_glyphs_do_not_invent_a_normalized_hit():
    box = (0, 0, 2, 2)
    item = candidate([('y', box)], pdf_line_norm='sqrt',
                     pdf_line_norm_chars=[(ch, box) for ch in 'sqrt'], pdf_line_has_math_signal=True)
    assert _query_specific_bbox('√', item) is None


def test_script_query_still_uses_full_verified_normalized_span():
    item = candidate([('x', (0, 0, 2, 2)), ('1', (2, 1, 3, 3))],
                     pdf_line_norm='x1', pdf_line_norm_chars=[('x', (0, 0, 2, 2)), ('1', (2, 1, 3, 3))],
                     pdf_line_has_math_signal=True)
    assert _query_specific_bbox('x_1', item) == (0, 0, 3, 3)


@pytest.mark.parametrize('raw', [None, [], [('xy', (0, 0, 2, 2))],
                                [('x', (0, 0, 2, 2)), ('yz', (2, 0, 4, 2))]])
def test_specialized_scan_defers_unsupported_glyph_data_to_the_general_path(raw):
    assert _single_literal_bbox(candidate(raw), 'x') == (False, None)


@pytest.mark.parametrize('ascii_query,unicode_query', [('-', '−'), ('x-y', 'x−y')])
def test_unicode_minus_preserves_entire_ascii_search_record(ascii_query, unicode_query):
    raw = [('x', (0, 0, 2, 2)), ('−', (2, 0, 3, 2)), ('y', (3, 0, 5, 2))]
    index = build_index([FormulaCandidate('x-y', 'tex', page=0, bbox=(0, 0, 5, 2),
                       page_w=100, page_h=100, pdf_line_raw_chars=raw, pdf_line_norm='xy')])
    ascii_results = search_formulas(ascii_query, index)
    unicode_results = search_formulas(unicode_query, index)
    assert len(unicode_results) == 1
    assert [asdict(row) for row in unicode_results] == [asdict(row) for row in ascii_results]
    assert unicode_results[0].bbox == ((2, 0, 3, 2) if ascii_query == '-' else (0, 0, 5, 2))
    assert unicode_results[0].bbox_source == 'query-subspan'
