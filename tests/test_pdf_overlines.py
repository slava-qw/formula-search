"""Drawn accents must preserve their argument, scripts and physical rectangle."""
import pytest

from backend.extractor.text_layer import extract_formulas
from tests.test_pdf_math import _pdf, _search
from tests.test_pdf_structures import _text, _root


@pytest.mark.parametrize('rectangle', [False, True])
def test_drawn_overline_keeps_rule_geometry_without_inventing_raw_glyphs(rectangle):
    def write(page):
        _text(page, 50, 110, 'A')
        if rectangle:
            page.draw_rect((49.8, 99.8, 57.8, 100.2), color=None, fill=(0, 0, 0))
        else:
            page.draw_line((49.8, 100), (57.8, 100), width=.4)
    pdf = _pdf(write)
    results = _search(pdf, r'\overline{A}')
    assert len(results) == 1
    assert results[0].bbox[0] == pytest.approx(49.8, abs=.001)
    assert results[0].bbox[2] == pytest.approx(57.8, abs=.001)
    assert results[0].bbox[1] <= 100 and results[0].bbox[3] >= 110
    candidate = next(c for c in extract_formulas(pdf) if r'\overline' in c.latex)
    assert ''.join(char for char, _ in candidate.pdf_line_raw_chars) == 'A'
    assert ''.join(char for char, _ in candidate.pdf_latex_chars) == candidate.latex
    assert not _search(pdf, r'\bar{A}')
    assert not _search(pdf, r'\underline{A}')


@pytest.mark.parametrize('marker,baseline', [('^', 104), ('_', 114)])
def test_script_stays_outside_single_letter_overline(marker, baseline):
    def write(page):
        _text(page, 50, 110, 'A')
        page.draw_line((49.8, 100), (57.8, 100), width=.4)
        _text(page, 58, baseline, 'k', 8)
    pdf = _pdf(write)
    assert _search(pdf, r'\overline{A}'+marker+'k')
    assert not _search(pdf, r'\overline{A'+marker+'k}')
    assert _search(pdf, r'\overline{A}')[0].bbox[2] < 58


@pytest.mark.parametrize('punctuation', [',', ';'])
def test_preceding_equation_punctuation_cannot_become_numerator(punctuation):
    def write(page):
        _text(page, 53, 90, punctuation)
        _text(page, 50, 110, 'K')
        page.draw_line((49.8, 100), (57.8, 100), width=.4)
    pdf = _pdf(write)
    assert _search(pdf, r'\overline{K}')
    assert not _search(pdf, r'\frac{'+punctuation+'}{K}')


def test_nearby_fraction_bar_is_not_an_overline_on_its_denominator():
    def write(page):
        _text(page, 50, 96, 'B')
        _text(page, 50, 110, 'A')
        page.draw_line((49.8, 100), (57.8, 100), width=.4)
    pdf = _pdf(write)
    assert _search(pdf, r'\frac{B}{A}')
    assert not _search(pdf, r'\overline{A}')


def test_unselected_roman_glyph_above_bar_is_not_discarded():
    def write(page):
        _text(page, 50, 96, 'I', italic=False)
        _text(page, 50, 110, 'A')
        page.draw_line((49.8, 100), (57.8, 100), width=.4)
    assert not _search(_pdf(write), r'\overline{A}')


@pytest.mark.parametrize('extra_y', [96, 93])
def test_stacked_rules_do_not_recover_only_the_inner_overline(extra_y):
    def write(page):
        _text(page, 50, 110, 'A')
        for y in [100, extra_y]:
            page.draw_line((49.8, y), (57.8, y), width=.4)
    assert not _search(_pdf(write), r'\overline{A}')


def test_two_letter_bar_is_not_split_into_single_letter_accents():
    def write(page):
        _text(page, 50, 110, 'AB')
        page.draw_line((49.8, 100), (65.8, 100), width=.4)
    pdf = _pdf(write)
    assert not _search(pdf, r'\overline{A}')
    assert not _search(pdf, r'\overline{B}')


@pytest.mark.parametrize('left,right,y', [(30, 80, 100), (49.8, 57.8, 85), (49.8, 57.8, 116)])
def test_wide_distant_or_underlining_rules_are_not_overlines(left, right, y):
    def write(page):
        _text(page, 50, 110, 'A')
        page.draw_line((left, y), (right, y), width=.4)
    assert not _search(_pdf(write), r'\overline{A}')


def test_radical_bar_is_reserved_for_its_root():
    def write(page):
        start, _ = _root(page, 50, 110, 12, 65)
        _text(page, start, 110, 'A')
    pdf = _pdf(write)
    assert _search(pdf, r'\sqrt{A}')
    assert not _search(pdf, r'\overline{A}')
