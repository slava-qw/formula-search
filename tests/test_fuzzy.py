import pytest
from unittest.mock import patch
from backend.matcher.fuzzy import search_formulas, MatchResult
from backend.extractor.text_layer import FormulaCandidate
from backend.matcher.index import build_index, IndexedFormula


def _candidate(latex: str, page: int = 0) -> IndexedFormula:
    return build_index([FormulaCandidate(latex=latex, source="text", page=page)])[0]


def test_exact_match_returns_score_1():
    results = search_formulas(r"\frac{d}{dx}", [_candidate(r"\frac{d}{dx}")])
    assert len(results) == 1
    assert results[0].match_type == "exact"
    assert results[0].score == 1.0


def test_fuzzy_match_detects_minor_syntax_difference():
    results = search_formulas(r"\int_0^{\infty}", [_candidate(r"\int_{0}^{\infty}")])
    assert len(results) == 1
    assert results[0].match_type in ("exact", "fuzzy")


def test_no_match_below_threshold():
    results = search_formulas(r"\frac{d}{dx}", [_candidate(r"\alpha + \beta + \gamma")])
    assert len(results) == 0


def test_results_sorted_by_score_descending():
    candidates = [
        _candidate(r"\int_{0}^{\infty}", page=0),
        _candidate(r"\int_0^{\infty}", page=1),
    ]
    results = search_formulas(r"\int_0^{\infty}", candidates)
    if len(results) > 1:
        assert results[0].score >= results[1].score


def test_match_result_has_correct_fields():
    results = search_formulas(r"\alpha", [_candidate(r"\alpha")])
    assert len(results) == 1
    r = results[0]
    assert isinstance(r, MatchResult)
    assert r.latex == r"\alpha"
    assert r.source == "text"


def test_multiple_candidates_multiple_matches():
    candidates = [_candidate(r"\frac{d}{dx}", page=i) for i in range(3)]
    results = search_formulas(r"\frac{d}{dx}", candidates)
    assert len(results) == 3


def test_empty_candidates_returns_empty():
    results = search_formulas(r"\frac{d}{dx}", [])
    assert results == []


def test_query_location_requires_the_whole_expression_not_a_shorter_variant():
    c = FormulaCandidate(r'\pi_\lambda', 'tex', page=0, bbox=(0, 0, 20, 10),
                         pdf_line_norm='lambda',
                         pdf_line_norm_chars=[(ch, (i * 4, 0, i * 4 + 3, 10)) for i, ch in enumerate('lambda')])
    result = search_formulas(r'\pi_\lambda', build_index([c]))[0]
    assert result.bbox is None
    assert result.bbox_source == 'query-subspan-missing'


def test_literal_query_location_includes_operators_and_closing_parenthesis():
    c = FormulaCandidate('BN(K{+}1)', 'tex', page=0, bbox=(0, 0, 20, 10),
                         pdf_line_norm='bnk1',
                         pdf_line_raw_chars=[(ch, (i * 6, 0, i * 6 + 5, 10)) for i, ch in enumerate('BN(K+1)')])
    result = search_formulas('N(K+1)', build_index([c]))[0]
    assert result.bbox == (6, 0, 41, 10)
    assert result.bbox_source == 'query-subspan'


def test_literal_location_does_not_equate_different_operators():
    c = FormulaCandidate('x+y', 'tex', page=0, bbox=(0, 0, 20, 10),
                         pdf_line_norm='xy',
                         pdf_line_raw_chars=[(ch, (i * 6, 0, i * 6 + 5, 10)) for i, ch in enumerate('x-y')])
    result = search_formulas('x+y', build_index([c]))[0]
    assert result.bbox is None


def test_partial_ast_match_type_and_score():
    # x+y is a structural subtree of a*(x+y)^2
    # a*(x+y)^2 is SymPy-parseable, so AST matching will be attempted
    candidates = [_candidate(r"a*(x+y)^2")]
    results = search_formulas(r"x+y", candidates)
    assert len(results) == 1
    assert results[0].match_type == "partial"
    assert results[0].score == 0.95
    assert r"\textcolor" in results[0].highlighted_latex


def test_partial_token_fallback_score_and_empty_highlight():
    # A command-only search is useful but wrapping \frac without its arguments
    # would corrupt the displayed TeX. Return the match without a preview span.
    candidates = [_candidate(r"\frac{x+y}{z}")]
    results = search_formulas(r"\frac", candidates)
    assert len(results) == 1
    assert results[0].match_type == "partial"
    assert results[0].score == 0.85
    assert results[0].highlighted_latex == ""


def test_results_ordering_exact_before_partial():
    candidates = [
        _candidate(r"a*(x+y)^2"),  # partial match
        _candidate(r"x+y"),         # exact match
    ]
    results = search_formulas(r"x+y", candidates)
    assert results[0].match_type == "exact"
    assert results[1].match_type == "partial"
    assert results[1].score == 0.95  # confirms AST path, not token fallback (0.85)


def test_highlighted_latex_field_present_on_exact():
    results = search_formulas(r"\alpha", [_candidate(r"\alpha")])
    assert len(results) == 1
    assert results[0].highlighted_latex == ""


def test_highlighted_latex_field_present_on_fuzzy():
    # \int_0^{\infty} vs \int_{0}^{\infty} — close but not identical, fuzzy match
    results = search_formulas(r"\int_0^{\infty}", [_candidate(r"\int_{0}^{\infty}")])
    assert len(results) >= 1
    assert results[0].match_type in ("exact", "fuzzy")  # either is fine for this pair
    assert results[0].highlighted_latex == ""


def test_match_result_page_and_bbox_propagated():
    from backend.extractor.text_layer import FormulaCandidate
    from backend.matcher.index import build_index
    c = FormulaCandidate(latex=r"\alpha", source="text", page=3, bbox=(5.0, 10.0, 200.0, 30.0))
    idx = build_index([c])
    results = search_formulas(r"\alpha", idx)
    assert len(results) == 1
    assert results[0].page == 3
    assert results[0].bbox == (5.0, 10.0, 200.0, 30.0)


def test_match_result_none_bbox_propagated():
    from backend.extractor.text_layer import FormulaCandidate
    from backend.matcher.index import build_index
    c = FormulaCandidate(latex=r"\beta", source="text", page=0)
    idx = build_index([c])
    results = search_formulas(r"\beta", idx)
    assert len(results) == 1
    assert results[0].page == 0
    assert results[0].bbox is None


def _norm_chars_for(norm: str, x0: float = 0.0, y0: float = 0.0):
    return [
        (ch, (x0 + i * 5.0, y0, x0 + i * 5.0 + 4.0, y0 + 8.0))
        for i, ch in enumerate(norm)
    ]


def test_partial_query_uses_query_subspan_bbox_from_pdf_line():
    c = FormulaCandidate(
        latex=r"\mathrm{Attention}(Q,K,V)=\mathrm{softmax}(QK^T)V",
        source="tex",
        env="equation",
        page=3,
        bbox=(10.0, 10.0, 300.0, 30.0),
        page_w=600.0,
        page_h=800.0,
        pdf_line_text="Attention(Q,K,V)=softmax(QKᵀ)V",
        pdf_line_norm="attentionqkvsoftmaxqktv",
        pdf_line_norm_chars=_norm_chars_for("attentionqkvsoftmaxqktv", 20.0, 100.0),
        bbox_quality="pdf-line",
    )
    results = search_formulas(r"QK^T", build_index([c]))
    assert results
    assert results[0].bbox_source == "query-subspan"
    # The bbox should be the QKT sub-expression, not the whole candidate line.
    assert results[0].bbox is not None
    assert results[0].bbox[0] > c.bbox[0]
    assert (results[0].bbox[2] - results[0].bbox[0]) < (c.bbox[2] - c.bbox[0])


def test_partial_query_does_not_reuse_wrong_tex_bbox_when_pdf_line_lacks_query():
    c = FormulaCandidate(
        latex=r"\mathrm{Attention}(Q,K,V)=\mathrm{softmax}(QK^T)V",
        source="tex",
        env="equation",
        page=12,
        bbox=(50.0, 120.0, 120.0, 134.0),
        page_w=600.0,
        page_h=800.0,
        pdf_line_text="attention",
        pdf_line_norm="attention",
        pdf_line_norm_chars=_norm_chars_for("attention", 50.0, 120.0),
        bbox_quality="pdf-line-char",
    )
    results = search_formulas(r"QK^T", build_index([c]))
    assert len(results) == 1
    assert results[0].bbox is None
    assert results[0].bbox_source == "query-subspan-missing"


def test_partial_query_subspan_result_sorts_before_stale_tex_bbox():
    stale = FormulaCandidate(
        latex=r"\mathrm{Attention}(Q,K,V)=\mathrm{softmax}(QK^T)V",
        source="tex",
        env="equation",
        page=12,
        bbox=(50.0, 120.0, 120.0, 134.0),
        page_w=600.0,
        page_h=800.0,
        pdf_line_text="attention",
        pdf_line_norm="attention",
        pdf_line_norm_chars=_norm_chars_for("attention", 50.0, 120.0),
        bbox_quality="pdf-line-char",
    )
    good = FormulaCandidate(
        latex=r"\mathrm{Attention}(Q,K,V)=\mathrm{softmax}(QK^T)V",
        source="tex",
        env="equation",
        page=3,
        bbox=(10.0, 10.0, 300.0, 30.0),
        page_w=600.0,
        page_h=800.0,
        pdf_line_text="Attention(Q,K,V)=softmax(QKᵀ)V",
        pdf_line_norm="attentionqkvsoftmaxqktv",
        pdf_line_norm_chars=_norm_chars_for("attentionqkvsoftmaxqktv", 20.0, 100.0),
        bbox_quality="pdf-line",
    )
    results = search_formulas(r"QK^T", build_index([stale, good]))
    assert len(results) == 2
    assert results[0].bbox_source == "query-subspan"
    assert results[0].page == 3
    assert results[1].bbox is None


def test_structured_short_query_does_not_raw_fuzzy_neighbor_formula():
    candidates = build_index([
        FormulaCandidate(latex=r"k_\lambda", source="tex", page=0),
        FormulaCandidate(latex=r"m_\lambda", source="tex", page=0),
    ])
    assert search_formulas(r"Z_\lambda", candidates) == []


def test_compact_partial_handles_unbraced_hat_query_without_bare_symbol_noise():
    candidates = build_index([
        FormulaCandidate(latex=r"r_{01}", source="tex", page=0),
        FormulaCandidate(
            latex=r"\hat{r}_{01}",
            source="tex",
            page=1,
            bbox=(10.0, 20.0, 50.0, 32.0),
            page_w=100.0,
            page_h=120.0,
            pdf_line_text="Return ˆr01.",
            pdf_line_norm="return|r01",
            pdf_line_norm_chars=_norm_chars_for("return|r01", 10.0, 20.0),
            bbox_quality="pdf-line-char",
        ),
    ])
    results = search_formulas(r"\hat r_{01}", candidates)
    assert len(results) == 1
    assert results[0].latex == r"\hat{r}_{01}"
    assert results[0].bbox_source == "query-subspan"


def test_search_results_ordered_by_page_not_score():
    """Ctrl+F-style ordering: earlier pages first, even if a later-page
    match would otherwise score higher. This is what a human scanning a
    paper top-to-bottom expects, not a relevance ranking."""
    candidates = build_index([
        FormulaCandidate(latex=r"x^2", source="text", page=5),
        FormulaCandidate(latex=r"x^2 + 1", source="text", page=1),
    ])
    results = search_formulas("x^2", candidates)
    assert len(results) == 2
    assert results[0].page == 1
    assert results[1].page == 5


def test_search_results_page_less_match_sorts_last():
    """A page-less match (page == -1, an unresolved candidate) must still
    sort after every page-mapped result, even though -1 is numerically the
    smallest page. This is exactly what the -10**9 sentinel exists to
    guarantee once page becomes the primary sort key."""
    candidates = build_index([
        FormulaCandidate(latex=r"x^2", source="text", page=-1),
        FormulaCandidate(latex=r"x^2 + 1", source="text", page=7),
    ])
    results = search_formulas("x^2", candidates)
    assert len(results) == 2
    assert results[0].page == 7
    assert results[1].page == -1


def test_distinct_source_candidates_at_same_query_subspan_remain_visible_as_ambiguous():
    common = dict(
        source="tex",
        page=4,
        bbox=(100.0, 200.0, 180.0, 216.0),
        page_w=500.0,
        page_h=700.0,
        pdf_line_text=r"W_i^Q in R^{dmodel x dk}, W_i^K in R^{dmodel x dk}",
        pdf_line_norm="wiqinrdmodeldk|wiikinrdmodeldk",
        pdf_line_norm_chars=_norm_chars_for("wiqinrdmodeldk|wiikinrdmodeldk", 100.0, 200.0),
        bbox_quality="pdf-line-char",
    )
    candidates = build_index([
        FormulaCandidate(latex=r"W^Q_i \in \mathbb{R}^{d_{model}\times d_k}", **common),
        FormulaCandidate(latex=r"W^K_i \in \mathbb{R}^{d_{model}\times d_k}", **common),
    ])
    results = search_formulas(r"d_k", candidates)
    assert len(results) == 2
    assert {r.occurrence_id for r in results} == {c.occurrence_id for c in candidates}
    assert all(r.bbox is None and r.bbox_source == 'ambiguous-source-location' for r in results)


@pytest.fixture
def semantic_functional_deadline(monkeypatch):
    # These tests verify real subprocess math and ranking, not startup speed.
    # Cold Windows Python/SymPy startup can itself exceed the production 4s
    # wall limit. Keep a bounded test allowance and assert completed work.
    # test_semantic_worker independently verifies the unchanged 4s kill/reap.
    from backend.matcher import semantic
    monkeypatch.setattr(semantic, 'WORKER_TIMEOUT_SECONDS', 15.0)


def test_semantic_match_detects_algebraic_equivalence(semantic_functional_deadline):
    # Distinct written notation, equal under the optional scalar assumptions.
    diagnostics = {}
    results = search_formulas(r"x+x", [_candidate(r"2*x")], mode="algebraic", diagnostics=diagnostics)
    assert diagnostics['semantic_status'] == 'complete'
    assert len(results) == 1
    assert results[0].match_type == "semantic"
    assert results[0].score == 0.80


def test_semantic_match_not_produced_when_exact_already_matches():
    results = search_formulas(r"a+b", [_candidate(r"a+b")], mode="algebraic")
    assert len(results) == 1
    assert results[0].match_type == "exact"


def test_semantic_tier_degrades_gracefully_when_budget_exhausted(monkeypatch):
    import backend.matcher.fuzzy as fuzzy_module
    # Negative budget guarantees the deadline is already in the past the
    # instant it's set, deterministically (no reliance on clock
    # resolution/timing flakiness the way a 0.0 budget might have).
    monkeypatch.setattr(fuzzy_module, "SEMANTIC_TIME_BUDGET_SECONDS", -1.0)
    # Distinct written forms ensure this reaches the optional algebraic tier.
    results = search_formulas(r"x+x", [_candidate(r"2*x")], mode="algebraic")
    assert all(r.match_type != "semantic" for r in results)


def test_semantic_match_excluded_for_structurally_close_but_unequal_formulas(semantic_functional_deadline):
    # A timeout must not be mistaken for a successful non-equivalence check.
    diagnostics = {}
    results = search_formulas(r"x+x", [_candidate(r"3*x")], mode="algebraic", diagnostics=diagnostics)
    assert diagnostics['semantic_status'] == 'complete'
    assert all(r.match_type != "semantic" for r in results)


def test_semantic_match_ranks_after_partial_match_on_same_page(semantic_functional_deadline):
    # On the same page, the written subexpression ranks above scalar equivalence.
    candidates = [
        _candidate(r"a*(x+x)^2", page=2),  # Contains the written "x+x"
        _candidate(r"2*x", page=2),        # semantic match for "x+x"
    ]
    diagnostics = {}
    results = search_formulas(r"x+x", candidates, mode="algebraic", diagnostics=diagnostics)
    assert diagnostics['semantic_status'] == 'complete'
    assert [r.match_type for r in results] == ["partial", "semantic"]
    assert results[0].score == 0.95
    assert results[1].score == 0.80


def test_semantic_tier_prefilter_does_not_force_parse_when_budget_exhausted(monkeypatch):
    """Regression test for the critical whole-branch-review finding: the
    semantic tier's time budget must gate the FIRST expensive operation --
    candidate.sympy_expr access, which triggers a real ANTLR LaTeX parse via
    IndexedFormula's lazy property -- not just the later semantically_equal()
    call. Before the fix, structural_signature(candidate.sympy_expr) ran
    unconditionally as the first line inside the `if q_sig is not None:`
    block, so every candidate that reached that block paid the parse cost
    regardless of whether the budget was already exhausted. Measured on a
    real 665-formula cached paper, that turned a ~0.4s nominal budget into a
    +13.5s first-search regression.
    """
    import backend.matcher.fuzzy as fuzzy_module
    from backend.matcher.index import _SYMPY_EXPR_UNSET

    # None of these candidates contain "x", so none can hit the exact,
    # token_match ("partial"), or _compact_partial_match tiers for the query
    # "x+x" below -- every single one reaches the semantic-tier check.
    candidates = [_candidate(rf"y_{i}+z_{i}") for i in range(60)]

    # Negative budget guarantees the deadline is already in the past the
    # instant it's set, deterministically (no reliance on clock
    # resolution/timing flakiness the way a 0.0 budget might have).
    monkeypatch.setattr(fuzzy_module, "SEMANTIC_TIME_BUDGET_SECONDS", -1.0)
    search_formulas(r"x+x", candidates, mode="algebraic")

    untouched = sum(1 for c in candidates if c._sympy_expr_cache is _SYMPY_EXPR_UNSET)
    assert untouched == len(candidates), (
        f"expected .sympy_expr to never be accessed once the semantic-tier "
        f"budget was already exhausted before the loop began, but "
        f"{len(candidates) - untouched} of {len(candidates)} candidates had "
        f"it computed anyway"
    )
