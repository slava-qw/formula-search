"""Real rendered PDF geometry, including deliberately misleading drawn rules."""
import fitz
import pytest

from tests.test_pdf_math import _pdf, _search
from backend.extractor.text_layer import extract_formulas


def _text(page, x, y, text, size=12, italic=True):
    page.insert_text((x, y), text, fontname='tiit' if italic else 'helv', fontsize=size)


def _root(page, x, baseline, size, end):
    font = fitz.Font('symb')
    writer = fitz.TextWriter(page.rect)
    writer.append((x, baseline), '√', font=font, fontsize=size)
    writer.write_text(page)
    top = baseline - font.glyph_bbox(ord('√')).y1 * size
    start = x + font.glyph_advance(ord('√')) * size
    page.draw_line((start, top), (end, top), width=.4)
    return start, top


@pytest.mark.parametrize('numeric', [False, True])
def test_centered_fraction_keeps_both_operands_and_rule(numeric):
    def write(p):
        _text(p, 50, 90, '1' if numeric else 'x', italic=not numeric)
        _text(p, 50, 110, '2' if numeric else 'y', italic=not numeric)
        p.draw_line((49, 98), (59, 98), width=.4)
    pdf = _pdf(write)
    query = r'\frac{1}{2}' if numeric else r'\frac{x}{y}'
    matches = _search(pdf, query)
    assert len(matches) == 1
    assert matches[0].bbox[0] <= 49 and matches[0].bbox[2] >= 59
    assert matches[0].bbox[1] < 90 and matches[0].bbox[3] > 110
    assert not _search(pdf, '12' if numeric else 'xy')
    assert not _search(pdf, r'\frac{2}{1}' if numeric else r'\frac{y}{x}')


def test_nested_fraction_has_the_correct_tree():
    def write(p):
        _text(p, 62, 82, 'x', 10)
        _text(p, 62, 94, 'y', 10)
        p.draw_line((60, 87), (69, 87), width=.4)
        _text(p, 62, 112, 'z', 10)
        p.draw_line((59, 100), (70, 100), width=.4)
    pdf = _pdf(write)
    assert _search(pdf, r'\frac{\frac{x}{y}}{z}')
    assert not _search(pdf, r'\frac{x}{\frac{y}{z}}')
    assert not _search(pdf, 'xyz')


def test_radical_extent_comes_from_overbar_not_the_whole_line():
    def write(p):
        x, _ = _root(p, 50, 100, 12, 70)
        _text(p, x, 100, 'x', 12)
        _text(p, 71, 100, '+y', 12)
    pdf = _pdf(write)
    assert _search(pdf, r'\sqrt{x}+y')
    assert not _search(pdf, r'\sqrt{x+y}')
    match = _search(pdf, r'\sqrt{x}')[0]
    assert match.bbox[2] <= 70.1


def test_root_of_a_fraction_does_not_lose_denominator():
    def write(p):
        _root(p, 50, 111, 24, 77)
        _text(p, 66, 94, 'x', 10)
        _text(p, 66, 110, 'y', 10)
        p.draw_line((64, 100), (75, 100), width=.4)
    pdf = _pdf(write)
    assert _search(pdf, r'\sqrt{\frac{x}{y}}')
    assert not _search(pdf, r'\frac{\sqrt{x}}{y}')


def test_power_on_fraction_remains_in_the_reconstructed_formula():
    def write(p):
        _text(p, 50, 90, 'x')
        _text(p, 50, 110, 'y')
        p.draw_line((49, 98), (59, 98), width=.4)
        _text(p, 61, 94, '2', 8, False)
    pdf = _pdf(write)
    assert _search(pdf, r'\frac{x}{y}^2')
    assert not _search(pdf, r'\frac{x}{y}_2')


def test_inline_fraction_cannot_become_the_left_variables_subscript():
    def write(p):
        _text(p, 50, 100, 'f')
        _text(p, 58, 94, 'dg', 8)
        _text(p, 58, 106, 'dx', 8)
        p.draw_line((57, 98), (67, 98), width=.4)
    pdf = _pdf(write)
    assert _search(pdf, r'f\frac{dg}{dx}')
    assert not _search(pdf, r'f_{dx}')


def test_comma_between_fractions_is_not_dropped_as_multiplication():
    def write(p):
        for x, top, bottom in ((50, 'x', 'y'), (67, 'a', 'b')):
            _text(p, x, 90, top)
            _text(p, x, 110, bottom)
            p.draw_line((x-1, 98), (x+9, 98), width=.4)
        _text(p, 60, 101, ',', 12, False)
    pdf = _pdf(write)
    assert _search(pdf, r'\frac{x}{y},\frac{a}{b}')
    assert not _search(pdf, r'\frac{x}{y}\frac{a}{b}')


def test_large_sum_cannot_turn_an_adjacent_argument_into_a_fraction_power():
    def write(p):
        writer = fitz.TextWriter(p.rect)
        writer.append((35, 103), '∑', font=fitz.Font('symb'), fontsize=18)
        writer.write_text(p)
        _text(p, 50, 90, 'x')
        _text(p, 50, 110, 'y')
        p.draw_line((49, 98), (59, 98), width=.4)
        _text(p, 60, 101, ',', 12, False)
        _text(p, 64, 101, 'z')
        _text(p, 70, 96, '2', 8, False)
    pdf = _pdf(write)
    assert _search(pdf, r'\frac{x}{y},z^2')
    assert not _search(pdf, r'\frac{x}{y}^{z^2}')


def test_wide_table_rule_cannot_turn_rows_into_a_fraction():
    def write(p):
        _text(p, 50, 90, 'x')
        _text(p, 50, 110, 'y')
        p.draw_line((20, 98), (150, 98), width=.4)
    assert not _search(_pdf(write), r'\frac{x}{y}')


def test_omitted_accent_prevents_an_incorrect_fraction():
    def write(p):
        _text(p, 50, 90, 'x')
        _text(p, 50, 110, 'y')
        _text(p, 52, 82, '^', 6, False)
        p.draw_line((49, 98), (59, 98), width=.4)
    assert not _search(_pdf(write), r'\frac{x}{y}')


def test_root_without_overbar_is_not_guessed():
    def write(p):
        font = fitz.Font('symb')
        writer = fitz.TextWriter(p.rect)
        writer.append((50, 100), '√', font=font, fontsize=12)
        writer.write_text(p)
        _text(p, 57, 100, 'x')
    assert not _search(_pdf(write), r'\sqrt{x}')


def test_unhandled_root_index_cannot_become_a_square_root():
    def write(p):
        x, top = _root(p, 50, 100, 12, 70)
        _text(p, x, 100, 'x')
        _text(p, 46, top+1, '3', 7, False)
    assert not _search(_pdf(write), r'\sqrt{x}')
