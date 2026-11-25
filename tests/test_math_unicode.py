"""Font-aware Unicode math input and positioned PDF rendering contracts."""
import pytest

from backend.math_unicode import math_unicode_latex
from backend.matcher.notation import notation, find_notation
from backend.matcher.index import build_index
from backend.matcher.fuzzy import search_formulas
from backend.extractor.pdf_math import Glyph, Row, _render
from backend.extractor.text_layer import FormulaCandidate


@pytest.mark.parametrize('unicode,tex', [
    ('ℎ_𝑡', 'h_t'), ('𝑨', r'\boldsymbol{A}'), ('𝐀', r'\mathbf{A}'),
    ('𝝉', r'\boldsymbol{\tau}'), ('𝜏_Δ', r'\tau_\Delta'),
    ('𝔸', r'\mathbb{A}'), ('ℝ^𝑁', r'\mathbb{R}^N'),
    ('𝛤', r'\mathit{\Gamma}'), ('𝟐', r'\mathbf{2}'),
    ('x_𝑨', r'x_{\boldsymbol{A}}'),
])
def test_unicode_math_has_the_same_tokens_as_its_explicit_tex_font(unicode, tex):
    assert notation(unicode).tokens == notation(tex).tokens


@pytest.mark.parametrize('a,b', [('𝑨', 'A'), ('𝐀', '𝑨'), ('ℝ', 'R'),
                               ('𝔸', '𝐀'), ('𝛤', r'\Gamma'), ('𝟐', '2'),
                               ('𝜙', r'\varphi'), ('𝜑', r'\phi')])
def test_unicode_math_font_and_variant_distinctions_are_not_erased(a, b):
    assert notation(a).tokens != notation(b).tokens


def test_unicode_expansion_preserves_exact_source_spans_and_unbraced_script_tokens():
    text = 'x+𝑨_i'
    start, stop = find_notation(notation(r'\boldsymbol{A}_i').tokens, notation(text))
    assert text[start:stop] == '𝑨_i'
    assert notation('x_𝑨y').tokens == notation(r'x_{\boldsymbol{A}}y').tokens
    assert notation('x_𝑨y').tokens != notation(r'x_{\boldsymbol{A}y}').tokens
    assert find_notation(notation('h_t').tokens, notation('ℎ_𝑡')) == (0, 3)


def test_only_supported_font_decompositions_are_translated():
    for char in ['x', '²', '①', 'Ａ', 'ﬀ', '𝓐', '𝘼', '𝕒', '𝟚', '\U0001d455']:
        assert math_unicode_latex(char) is None
    assert math_unicode_latex('word') is None
    assert math_unicode_latex('𝒙') == r'\boldsymbol{x}'


def test_unicode_font_overrides_generic_pdf_span_flags_without_changing_raw_glyphs_or_coordinates():
    box = (1.125, 2.25, 3.375, 4.5)
    glyphs = [Glyph(ch, box, 1, 4, 10, 'GenericMath', True, italic=False, bold=True)
              for ch in 'ℎ𝑨𝐀']
    rendered, mapping, raw = _render(Row(glyphs, 4, 10))
    assert rendered == r'h\boldsymbol{A}\mathbf{A}'
    assert ''.join(ch for ch, _ in mapping) == rendered
    assert all(b == box for _, b in mapping)
    assert raw == [(ch, box) for ch in 'ℎ𝑨𝐀']


def test_unicode_query_can_search_original_tex_without_rewriting_saved_formula_or_highlight_span():
    index = build_index([FormulaCandidate(r'h_t+\boldsymbol{A}', 'tex')])
    rows = search_formulas('ℎ_𝑡', index)
    assert len(rows) == 1 and rows[0].latex == r'h_t+\boldsymbol{A}'
    assert rows[0].highlighted_latex
    assert len(search_formulas('𝑨', index)) == 1
    assert not search_formulas('𝐀', index)
