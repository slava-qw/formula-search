"""Product-level contracts: notation search must not invent equivalences."""
import pytest

from backend.extractor.text_layer import FormulaCandidate
from backend.matcher.fuzzy import search_formulas
from backend.matcher.index import build_index
from backend.matcher.normalizer import normalize_latex


@pytest.mark.parametrize("query,candidate", [
    ("AB", "BA"), (r"x_{12}", "x_1 2"), ("x_i", "x^i"),
    ("x+y", "X+Y"), (r"\frac{a}{b}", "ab"), (r"\alpha", r"\Alpha"),
    ("a+b", "a-b"), (r"\hat{x}", "x"), ("12", "312"),
    (r"\alpha b", r"\alphab"), (r"\mathbb{R}", "R"),
    (r"\{x\}", "{x}"), (r"x\notin X", r"x\in X"),
    (r"\mathcal{X}", "X"), (r"\mathbb{E}", r"\mathbf{E}"),
    (r"\epsilon", "ε"), (r"\varepsilon", "ϵ"),
    (r"\phi", "φ"), (r"\varphi", "ϕ"),
    (r"\theta", "ϑ"), (r"\rho", "ϱ"), (r"\ast", r"\star"),
])
def test_default_search_preserves_mathematical_distinctions(query, candidate):
    index = build_index([FormulaCandidate(candidate, "text")])
    assert search_formulas(query, index) == []


@pytest.mark.parametrize("left,right", [
    ("x_i", "x_{i}"), ("x^2", "x^{2}"),
    (r"\frac12", r"\frac{1}{2}"), (r"\dfrac{a}{b}", r"\frac{a}{b}"),
    (r"\left(x+y\right)", "(x+y)"), ("W_i^Q", "W^Q_i"),
    ("α+β", r"\alpha + \beta"), (r"x\, + y", "x+y"),
    ("N(K{+}1)", "N(K+1)"), ("{x}^2", "x^2"),
    (r"\left\{x\right\}", r"\lbrace x\rbrace"),
    ("x∉X", r"x\notin X"), ("x∈X", r"x\in X"),
    ("ϵ", r"\epsilon"), ("ε", r"\varepsilon"),
    ("ϕ", r"\phi"), ("φ", r"\varphi"), ("ϑ", r"\vartheta"),
    ("ϱ", r"\varrho"), ("µ", r"\mu"), ("∆", r"\Delta"),
    ("∗", r"\ast"), ("*", r"\ast"), ("◯", r"\bigcirc"), ("∘", r"\circ"),
])
def test_presentation_variants_have_the_same_notation(left, right):
    assert normalize_latex(left) == normalize_latex(right)


def test_default_search_never_invokes_symbolic_parser(monkeypatch):
    import backend.matcher.ast_match as symbolic
    def forbidden(*args, **kwargs):
        raise AssertionError("ordinary notation search must not parse algebra")
    monkeypatch.setattr(symbolic, "parse_query", forbidden)
    index = build_index([FormulaCandidate(r"a*(x+y)^2", "text")])
    result = search_formulas("x+y", index)
    assert result[0].highlighted_latex == r"a*({\textcolor{#f59e0b}{x+y}})^2"


def test_unlocated_source_match_is_not_lost():
    index = build_index([FormulaCandidate("x+y+z", "tex")])
    result = search_formulas("x+y", index)
    assert len(result) == 1
    assert result[0].bbox is None
    assert result[0].page == -1


def test_empty_query_has_no_matches():
    assert search_formulas("", build_index([FormulaCandidate("", "text")])) == []


def test_exact_mode_excludes_containing_expressions():
    index = build_index([FormulaCandidate("x+y", "text"), FormulaCandidate("x+y+z", "text")])
    assert [r.latex for r in search_formulas("x+y", index, mode="exact")] == ["x+y"]


def test_multi_atom_script_bases_remain_distinct():
    assert normalize_latex("{x+y}^2") != normalize_latex("x+y^2")


def test_big_o_is_not_equated_with_a_different_asymptotic_rate():
    index = build_index([FormulaCandidate("O(1/N)", "tex"), FormulaCandidate("O(1)", "tex")])
    assert [r.latex for r in search_formulas("O(1)", index)] == ["O(1)"]
