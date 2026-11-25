from backend.matcher.ast_match import (
    ast_partial_match, token_partial_match,
    token_match, ast_highlight, parse_query,
    structural_signature, signatures_compatible, semantically_equal, MAX_SEMANTIC_OPS,
)


def test_ast_match_subtree_hit():
    result = ast_partial_match(r"x+y", r"a*(x+y)^2")
    assert result is not None
    assert r"\textcolor" in result


def test_ast_match_no_hit():
    result = ast_partial_match(r"x+z", r"a*(x+y)^2")
    assert result is None


def test_ast_match_commutativity():
    # SymPy normalizes Add, so y+x == x+y structurally
    result = ast_partial_match(r"y+x", r"a*(x+y)^2")
    assert result is not None


def test_ast_match_parse_failure_returns_none():
    # Malformed LaTeX: unclosed brace — parse_latex raises, must return None
    result = ast_partial_match(r"\frac{1{", r"x+y")
    assert result is None


def test_ast_match_highlighted_latex_contains_color():
    # Use a*(x+y)^2 where x+y is a direct structural subtree (Pow base);
    # x+y+z is a flat 3-arg Add where has() returns True but xreplace finds nothing.
    result = ast_partial_match(r"x+y", r"a*(x+y)^2")
    assert result is not None
    assert r"\textcolor{#f59e0b}" in result


def test_token_match_hit():
    assert token_partial_match(r"x+y", r"a*(x+y)^2") is True


def test_token_match_no_hit():
    assert token_partial_match(r"x+z", r"a*(x+y)^2") is False


def test_token_match_whitespace_insensitive():
    # "x + y" and "x+y" both tokenize to ['x', '+', 'y']
    assert token_partial_match(r"x + y", r"x+y") is True


def test_token_match_empty_query():
    assert token_partial_match(r"", r"x+y") is False


def test_token_match_empty_candidate():
    assert token_partial_match(r"x", r"") is False


def test_token_match_direct_hit():
    assert token_match(['x', '+', 'y'], ['x', '+', 'y', '+', 'z']) is True


def test_token_match_direct_no_hit():
    assert token_match(['x', '+', 'z'], ['x', '+', 'y']) is False


def test_token_match_empty_qt():
    assert token_match([], ['x', '+', 'y']) is False


def test_ast_highlight_hit():
    q = parse_query(r"x+y")
    c = parse_query(r"a*(x+y)^2")
    result = ast_highlight(q, c)
    assert result is not None
    assert r"\textcolor" in result


def test_ast_highlight_none_q_expr():
    c = parse_query(r"x+y")
    assert ast_highlight(None, c) is None


def test_ast_highlight_none_c_expr():
    q = parse_query(r"x+y")
    assert ast_highlight(q, None) is None


def test_structural_signature_returns_none_for_none():
    assert structural_signature(None) is None


def test_structural_signature_counts_symbols_and_ops():
    expr = parse_query(r"x+y")
    sig = structural_signature(expr)
    assert sig is not None
    symbols, ops = sig
    assert symbols == 2
    assert ops >= 1


def test_structural_signature_returns_none_for_fallback_expr():
    # Force the non-SymPy fallback path the same way ast_highlight's own
    # tests exercise it: malformed-but-balanced input that _fallback_expr
    # can tokenize but parse_latex cannot parse into a real SymPy tree.
    # If the ANTLR parser isn't installed at all, parse_query always
    # returns a _FallbackExpr for any input -- either way this must be None.
    from backend.matcher.ast_match import _fallback_expr
    fallback = _fallback_expr(r"x+y")
    assert fallback is not None
    assert structural_signature(fallback) is None


def test_signatures_compatible_within_tolerance():
    assert signatures_compatible((2, 3), (2, 4)) is True
    assert signatures_compatible((2, 3), (3, 4)) is True


def test_signatures_compatible_rejects_large_difference():
    assert signatures_compatible((2, 3), (5, 3)) is False
    assert signatures_compatible((2, 3), (2, 10)) is False


def test_semantically_equal_commutative():
    q_expr = parse_query(r"a+b")
    c_expr = parse_query(r"b+a")
    assert semantically_equal(q_expr, c_expr) is True

    # a+b/b+a alone is vacuous: SymPy's own parser already canonicalizes
    # commutative reordering during parsing, so both sides become the
    # identical object before semantically_equal() is ever called -- this
    # doesn't exercise .equals() itself. x+x/2*x stays structurally
    # distinct after parsing (parse_latex does not auto-collect like
    # terms), so this pair genuinely exercises the .equals() call.
    q_expr2 = parse_query(r"x+x")
    c_expr2 = parse_query(r"2*x")
    assert semantically_equal(q_expr2, c_expr2) is True


def test_semantically_equal_rejects_different_expressions():
    q_expr = parse_query(r"a+b")
    c_expr = parse_query(r"a-b")
    assert semantically_equal(q_expr, c_expr) is False


def test_semantically_equal_rejects_over_complexity_ceiling():
    # 45 chained terms guarantees at least 44 addition operations --
    # comfortably over MAX_SEMANTIC_OPS (40) regardless of exactly how
    # SymPy's count_ops() weighs a flat n-ary Add.
    terms = "+".join(f"x_{i}" for i in range(MAX_SEMANTIC_OPS + 5))
    q_expr = parse_query(terms)
    c_expr = parse_query(terms)
    assert q_expr is not None and c_expr is not None, "test setup: terms must actually parse"
    assert semantically_equal(q_expr, c_expr) is False


def test_semantically_equal_returns_false_for_none():
    assert semantically_equal(None, parse_query(r"x+y")) is False
    assert semantically_equal(parse_query(r"x+y"), None) is False
