"""PDF prose context, atomic function names, and upright word subscripts."""
import fitz
import pytest

from backend.extractor.text_layer import extract_formulas
from tests.test_pdf_math import _pdf, _search
from tests.test_pdf_fonts import _embedded


@pytest.mark.parametrize('equation', ['A=2', 'a=2', 'x=a', 'a+x=3', '2=A', 'a= A+1'])
@pytest.mark.parametrize('spaced', [False, True])
def test_roman_a_is_preserved_beside_math_operators(equation, spaced):
    text = ' '.join(equation.replace(' ', '')) if spaced else equation
    pdf = _pdf(lambda p: p.insert_text((50, 100), text, fontsize=12))
    assert _search(pdf, equation, mode='exact')


def test_article_a_does_not_join_a_later_formula():
    pdf = _pdf(lambda p: p.insert_text((50, 100), 'A useful estimate is x = 2.', fontsize=12))
    assert _search(pdf, 'x=2', mode='exact')
    assert not _search(pdf, 'A', mode='exact')
    assert not _search(pdf, 'Ax=2')


def _prefix_math_pdf(word, symbol, *, left_relation=False, math_word=False):
    def write(p):
        x = 50
        if left_relation:
            p.insert_text((x, 100), 'y=', fontname='tiit', fontsize=12)
            x += 17
        if math_word:
            data = _embedded('gan_tutorial', 'CMMI10')
            font = fitz.Font(fontbuffer=data)
            ref = p.insert_font(fontname='MATH', fontbuffer=data)
            p.parent.xref_set_key(ref, 'BaseFont', '/CMMI10')
            p.insert_text((x, 100), word, fontname='MATH', fontsize=12)
            x += font.text_length(word, fontsize=12)
        else:
            p.insert_text((x, 100), word, fontsize=12)
            x += fitz.get_text_length(word, fontsize=12)
        family = 'CMMI10' if symbol == '∂' else 'CMSY10'
        data = _embedded('gan_tutorial', family)
        font = fitz.Font(fontbuffer=data)
        assert font.has_glyph(ord(symbol))
        ref = p.insert_font(fontname='PREFIX', fontbuffer=data)
        p.parent.xref_set_key(ref, 'BaseFont', '/'+family)
        p.insert_text((x+3, 100), symbol, fontname='PREFIX', fontsize=12)
        p.insert_text((x+3+font.text_length(symbol, fontsize=12), 100), 'x', fontname='tiit', fontsize=12)
    return _pdf(write)


@pytest.mark.parametrize(('word', 'symbol', 'query'), [('the', '∂', r'\partial x'), ('consider', '∇', r'\nabla x')])
def test_prose_word_before_prefix_operator_does_not_become_roman_math(word, symbol, query):
    pdf = _prefix_math_pdf(word, symbol)
    assert _search(pdf, query, mode='exact')
    assert not _search(pdf, r'\mathrm{'+word+'}')
    match, = _search(pdf, query)
    assert match.bbox[0] > 50+fitz.get_text_length(word, fontsize=12)


def test_roman_coefficient_before_derivative_is_retained():
    assert _search(_prefix_math_pdf('a', '∂'), r'a\partial x', mode='exact')


def test_roman_word_before_derivative_with_its_own_relation_is_retained():
    assert _search(_prefix_math_pdf('loss', '∂', left_relation=True), r'y=\mathrm{loss}\partial x', mode='exact')


def test_math_font_letter_product_before_derivative_is_retained():
    assert _search(_prefix_math_pdf('the', '∂', math_word=True), r'the\partial x', mode='exact')


@pytest.mark.parametrize('font', ['helv', 'tiit', 'tibo'])
def test_prose_name_and_initials_with_affiliation_do_not_become_math(font):
    def write(p):
        name = 'J. Alice Smith'
        p.insert_text((50, 100), name, fontname=font, fontsize=12)
        end = 50+fitz.get_text_length(name, fontname=font, fontsize=12)
        p.insert_text((end+1, 96), '1,2', fontsize=8)
        p.insert_text((end+30, 100), 'x=2', fontname='tiit', fontsize=12)
    pdf = _pdf(write)
    candidates = extract_formulas(pdf)
    assert len(candidates) == 1 and candidates[0].latex == 'x=2'


def test_math_font_product_with_exponent_is_not_a_prose_footnote():
    def write(p):
        data = _embedded('gan_tutorial', 'CMMI10')
        font = fitz.Font(fontbuffer=data)
        ref = p.insert_font(fontname='MATH', fontbuffer=data)
        p.parent.xref_set_key(ref, 'BaseFont', '/CMMI10')
        p.insert_text((50, 100), 'xyz', fontname='MATH', fontsize=12)
        end = 50+font.text_length('xyz', fontsize=12)
        p.insert_text((end, 96), '2', fontsize=8)
    assert _search(_pdf(write), 'xyz^2', mode='exact')


def _function_pdf(name, italic=False, script=False):
    def write(p):
        font = 'tiit' if italic else 'helv'
        p.insert_text((50, 100), name, fontname=font, fontsize=12)
        end = 50+fitz.get_text_length(name, fontname=font, fontsize=12)
        if script:
            p.insert_text((end, 96), '2', fontsize=8)
            end += 5
        p.insert_text((end, 100), '(x)', fontname='tiit', fontsize=12)
    return _pdf(write)


@pytest.mark.parametrize('name', ['sin', 'max', 'log', 'argmin', 'arcsin'])
def test_upright_function_uses_its_tex_operator(name):
    pdf = _function_pdf(name)
    query = '\\'+name+'(x)'
    assert _search(pdf, query, mode='exact')
    assert not _search(pdf, name+'(x)', mode='exact')
    operator, = _search(pdf, '\\'+name)
    assert operator.bbox[0] == pytest.approx(50)
    assert operator.bbox[2] == pytest.approx(50+fitz.get_text_length(name, fontsize=12), abs=.01)


def test_italic_letter_product_does_not_become_named_sine():
    pdf = _function_pdf('sin', italic=True)
    assert _search(pdf, 'sin(x)', mode='exact')
    assert not _search(pdf, r'\sin(x)')


def test_function_power_belongs_to_the_whole_operator():
    pdf = _function_pdf('log', script=True)
    assert _search(pdf, r'\log^2(x)', mode='exact')
    assert not _search(pdf, 'log^2(x)', mode='exact')


@pytest.mark.parametrize('name', ['softmax', 'Bessel', 'FFN'])
def test_custom_roman_function_and_its_script_keep_upright_letters(name):
    pdf = _function_pdf(name, script=True)
    assert _search(pdf, r'\mathrm{'+name+r'}^2(x)', mode='exact')
    assert not _search(pdf, name+'^2(x)', mode='exact')


def test_roman_subscript_word_is_atomic_and_maps_its_own_highlight():
    def write(p):
        p.insert_text((50, 100), 'p', fontname='tiit', fontsize=12)
        p.insert_text((56, 104), 'data', fontsize=8)
        p.insert_text((76, 100), '+x', fontname='tiit', fontsize=12)
    pdf = _pdf(write)
    assert _search(pdf, r'p_{\mathrm{data}}+x', mode='exact')
    assert not _search(pdf, 'p_{data}+x')
    match, = _search(pdf, r'\mathrm{data}')
    assert match.bbox[0] == pytest.approx(56)
    assert match.bbox[2] < 76
    candidate, = extract_formulas(pdf)
    assert ''.join(ch for ch, _ in candidate.pdf_latex_chars) == candidate.latex


def test_plain_prose_function_word_does_not_supply_math_evidence():
    pdf = _pdf(lambda p: p.insert_text((50, 100), 'The max temperature was recorded.', fontsize=12))
    assert not extract_formulas(pdf)


def test_operator_script_above_an_unrelated_figure_rule():
    def write(p):
        p.insert_text((50, 100), 'argmin', fontsize=12)
        end = 50+fitz.get_text_length('argmin', fontsize=12)
        p.insert_text((end, 104), 'q', fontname='tiit', fontsize=8)
        p.insert_text((end+7, 100), 'D', fontname='tiit', fontsize=12)
        p.draw_line((end-6, 115), (end+50, 115), width=.4)
    assert _search(_pdf(write), r'\argmin_q D', mode='exact')


@pytest.mark.parametrize(('name', 'query'), [('arg min', r'\argmin'), ('arg max', r'\argmax'), ('lim sup', r'\limsup')])
def test_thin_space_inside_a_compound_operator(name, query):
    assert _search(_function_pdf(name), query+'(x)', mode='exact')


@pytest.mark.parametrize('side', ['left', 'right'])
@pytest.mark.parametrize('script', ['2', 'train'])
def test_word_identifier_with_a_script_and_relation_is_not_prose(side, script):
    def write(p):
        x = 50
        if side == 'right':
            p.insert_text((x, 100), 'y=', fontname='tiit', fontsize=12)
            x += 17
        p.insert_text((x, 100), 'loss', fontsize=12)
        end = x+fitz.get_text_length('loss', fontsize=12)
        p.insert_text((end, 96), script, fontsize=8)
        if side == 'left':
            end += fitz.get_text_length(script, fontsize=8)
            p.insert_text((end+2, 100), '=y', fontname='tiit', fontsize=12)
    power = '2' if script == '2' else r'\mathrm{train}'
    word = r'\mathrm{loss}^{'+power+'}'
    query = word+'=y' if side == 'left' else 'y='+word
    assert _search(_pdf(write), query, mode='exact')


def test_roman_word_script_inside_function_arguments_is_not_prose():
    def write(p):
        p.insert_text((50, 100), 'Concat(head', fontsize=12)
        end = 50+fitz.get_text_length('Concat(head', fontsize=12)
        p.insert_text((end, 104), 'i', fontname='tiit', fontsize=8)
        p.insert_text((end+5, 100), ')', fontsize=12)
    assert _search(_pdf(write), r'\mathrm{Concat}(\mathrm{head}_i)', mode='exact')


@pytest.mark.parametrize(('y', 'marker'), [(90, '^'), (112, '_')])
def test_centered_operator_limit_is_attached_and_included_in_highlight(y, marker):
    def write(p):
        p.insert_text((30, 100), 'y=', fontname='tiit', fontsize=12)
        p.insert_text((50, 100), 'max', fontsize=12)
        center = 50+fitz.get_text_length('max', fontsize=12)/2
        p.insert_text((center-1.5, y), 'i', fontname='tiit', fontsize=8)
        p.insert_text((80, 100), '(x)', fontname='tiit', fontsize=12)
    pdf = _pdf(write)
    result, = _search(pdf, r'\max'+marker+'i')
    assert r'\limits' in result.latex
    assert result.bbox[1] < y and result.bbox[3] > y
    assert not _search(pdf, r'\max(x)')


def test_fraction_bar_prevents_a_false_centered_operator_limit():
    def write(p):
        p.insert_text((30, 90), 'y=', fontname='tiit', fontsize=12)
        p.insert_text((50, 90), 'max', fontsize=12)
        p.insert_text((60, 110), 'i', fontname='tiit', fontsize=8)
        p.draw_line((49, 99), (74, 99), width=.4)
    pdf = _pdf(write)
    assert not _search(pdf, r'\max_i')
    assert _search(pdf, r'\frac{\max}{i}')


def test_limit_beside_another_variable_stays_with_its_operator():
    def write(p):
        p.insert_text((30, 100), 'y=', fontname='tiit', fontsize=12)
        p.insert_text((50, 100), 'argmax', fontsize=12)
        end = 50+fitz.get_text_length('argmax', fontsize=12)
        p.insert_text(((50+end)/2-1.5, 112), 'i', fontname='tiit', fontsize=8)
        p.insert_text((end+4, 100), 'x', fontname='tiit', fontsize=12)
        p.insert_text((end+10, 104), 'j', fontname='tiit', fontsize=8)
    pdf = _pdf(write)
    assert _search(pdf, r'\argmax_i x_j')
    assert not _search(pdf, r'\argmax_j x_i')


def test_centered_limit_between_overlapping_operators_is_ambiguous():
    def write(p):
        p.insert_text((30, 100), 'y=', fontname='tiit', fontsize=12)
        p.insert_text((50, 100), 'max', fontsize=12)
        p.insert_text((57, 100), 'max', fontname='tiro', fontsize=12)
        p.insert_text((63, 112), 'i', fontname='tiit', fontsize=8)
    assert not _search(_pdf(write), r'\max_i')
