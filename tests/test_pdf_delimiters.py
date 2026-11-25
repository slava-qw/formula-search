"""Visible PDF delimiters and overprinted relations retain their meaning."""
import fitz
import pytest

from backend.extractor.text_layer import extract_formulas
from tests.test_pdf_math import _pdf, _search


def test_literal_set_braces_are_not_tex_groups():
    pdf = _pdf(lambda p: p.insert_text((50, 100), 'S={x,y}', fontsize=12))
    for query in (r'S=\{x,y\}', r'S=\lbrace x,y\rbrace'):
        match = _search(pdf, query, mode='exact')[0]
        assert match.latex == r'S=\{x,y\}'
    assert not _search(pdf, 'S={x,y}')
    assert not _search(pdf, 'S=x,y')
    brace = _search(pdf, r'\{')[0]
    assert brace.bbox[2]-brace.bbox[0] < 5
    assert brace.bbox_source == 'query-subspan'


@pytest.mark.parametrize(('text', 'query'), [('x={', r'x=\{'), ('}=x', r'\}=x')])
def test_unpaired_visible_braces_are_valid_fragments(text, query):
    pdf = _pdf(lambda p: p.insert_text((50, 100), text, fontsize=12))
    assert _search(pdf, query, mode='exact')
    candidate = extract_formulas(pdf)[0]
    assert ''.join(ch for ch, _ in candidate.pdf_latex_chars) == query


def test_script_on_set_delimiter_retains_literal_brace():
    def write(p):
        p.insert_text((50, 100), 'S={x}', fontsize=12)
        end = 50+fitz.get_text_length('S={x}', fontsize=12)
        p.insert_text((end, 104), 'i', fontname='tiit', fontsize=8)
    pdf = _pdf(write)
    assert _search(pdf, r'S=\{x\}_i', mode='exact')
    assert not _search(pdf, 'S=x_i')


def test_visible_backslash_is_not_a_tex_command():
    pdf = _pdf(lambda p: p.insert_text((50, 100), 'x=1+\\', fontsize=12))
    assert _search(pdf, r'x=1+\backslash', mode='exact')


def test_literal_tex_fallback_keeps_its_own_syntax():
    pdf = _pdf(lambda p: p.insert_text((50, 100), r'\frac{x}{y}', fontsize=12))
    assert _search(pdf, r'\frac{x}{y}', mode='exact')


def _overprinted_relation(relation, reverse=False, slash_x=62, slash_y=100):
    def write(p):
        p.insert_text((50, 100), 'j', fontname='tiit', fontsize=12)
        pairs = [((62, 100), relation), ((slash_x, slash_y), '\u0338')]
        for position, text in (reversed(pairs) if reverse else pairs):
            writer = fitz.TextWriter(p.rect)
            writer.append(position, text, fontsize=12)
            writer.write_text(p)
        p.insert_text((73, 100), 'i', fontname='tiit', fontsize=12)
    return _pdf(write)


@pytest.mark.parametrize('reverse', [False, True])
@pytest.mark.parametrize(('relation', 'query', 'wrong'), [('=', r'j\neq i', 'j=i'), ('∈', r'j\notin i', r'j\in i')])
def test_overprinted_negation_is_not_deleted_or_attached_to_variable(relation, query, wrong, reverse):
    pdf = _overprinted_relation(relation, reverse)
    match = _search(pdf, query, mode='exact')[0]
    assert not _search(pdf, wrong)
    assert match.bbox[0] == pytest.approx(50)
    assert match.bbox[2] > 75


@pytest.mark.parametrize(('x', 'y'), [(49, 100), (62, 90), (80, 100)])
def test_separate_slash_cannot_negate_a_nearby_relation(x, y):
    pdf = _overprinted_relation('=', slash_x=x, slash_y=y)
    assert not _search(pdf, r'j\neq i')
    assert not _search(pdf, r'\neq')
