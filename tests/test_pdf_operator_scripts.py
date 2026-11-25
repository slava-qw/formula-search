"""Positioned derivative/gradient indices and powers retain operator scope."""
import fitz
import pytest

from backend.extractor.text_layer import extract_formulas
from tests.test_pdf_math import _pdf, _search
from tests.test_pdf_fonts import _embedded


def _operator_pdf(symbol='∇', script='λ', y=103, gap=0, size=8, rule=False, following=False):
    def write(page):
        family = 'CMSY10' if symbol == '∇' else 'CMMI10'
        data = _embedded('gan_tutorial', family)
        font = fitz.Font(fontbuffer=data)
        assert font.has_glyph(ord(symbol))
        ref = page.insert_font(fontname='OPERATOR', fontbuffer=data)
        page.parent.xref_set_key(ref, 'BaseFont', '/'+family)
        page.insert_text((50, 100), symbol, fontname='OPERATOR', fontsize=12)
        end = 50+font.text_length(symbol, fontsize=12)
        if following:
            page.insert_text((end+3, 100), 'x', fontname='tiit', fontsize=12)
            end += 3+fitz.get_text_length('x', fontname='tiit', fontsize=12)
        small_family = 'CMR10' if script.isdigit() else 'CMMI10'
        small = _embedded('gan_tutorial', small_family)
        small_font = fitz.Font(fontbuffer=small)
        assert small_font.has_glyph(ord(script))
        ref = page.insert_font(fontname='INDEX', fontbuffer=small)
        page.parent.xref_set_key(ref, 'BaseFont', '/'+small_family)
        page.insert_text((end+gap, y), script, fontname='INDEX', fontsize=size)
        if rule:
            page.draw_line((49, (y+100)/2), (end+small_font.text_length(script, fontsize=size)+1, (y+100)/2), width=.4)
    return _pdf(write)


@pytest.mark.parametrize(('symbol', 'command'), [('∇', r'\nabla'), ('∂', r'\partial')])
@pytest.mark.parametrize(('y', 'marker'), [(103, '_'), (94, '^')])
def test_operator_scripts_keep_direction_and_complete_highlight(symbol, command, y, marker):
    pdf = _operator_pdf(symbol=symbol, y=y)
    query = command+marker+r'\lambda'
    match, = _search(pdf, query, mode='exact')
    assert not _search(pdf, command+('^' if marker == '_' else '_')+r'\lambda')
    assert not _search(pdf, command+r'\lambda', mode='exact')
    assert match.bbox[0] == pytest.approx(50)
    with fitz.open(stream=pdf, filetype='pdf') as doc:
        chars = [c for b in doc[0].get_text('rawdict')['blocks'] for line in b.get('lines', [])
                 for span in line['spans'] for c in span['chars'] if not c['c'].isspace()]
    assert match.bbox[2] >= max(c['bbox'][2] for c in chars)-.01
    candidate, = extract_formulas(pdf)
    assert ''.join(ch for ch, _ in candidate.pdf_latex_chars) == candidate.latex


@pytest.mark.parametrize(('symbol', 'command'), [('∇', r'\nabla'), ('∂', r'\partial')])
def test_squared_operator_is_searchable(symbol, command):
    assert _search(_operator_pdf(symbol=symbol, script='2', y=94), command+'^2', mode='exact')


@pytest.mark.parametrize('y', [103, 94])
def test_fraction_rule_vetoes_operator_script(y):
    pdf = _operator_pdf(y=y, rule=True)
    assert not _search(pdf, r'\nabla_\lambda')
    assert not _search(pdf, r'\nabla^\lambda')


@pytest.mark.parametrize(('gap', 'size'), [(16, 8), (0, 12)])
def test_far_or_same_size_row_is_not_an_operator_index(gap, size):
    pdf = _operator_pdf(gap=gap, size=size)
    assert not _search(pdf, r'\nabla_\lambda')
    assert not _search(pdf, r'\nabla^\lambda')


def test_operator_does_not_steal_following_variables_index():
    pdf = _operator_pdf(following=True)
    assert _search(pdf, r'\nabla x_\lambda', mode='exact')
    assert not _search(pdf, r'\nabla_\lambda x')
