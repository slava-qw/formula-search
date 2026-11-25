from backend.extractor.text_layer import FormulaCandidate
from backend.matcher.index import IndexedFormula, build_index


def test_build_index_fills_normalized_latex():
    result = build_index([FormulaCandidate(latex=r"x+y", source="text")])
    assert result[0].normalized_latex != ""


def test_build_index_fills_tokens():
    result = build_index([FormulaCandidate(latex=r"x+y", source="text")])
    assert result[0].tokens == ['x', '+', 'y']


def test_build_index_fills_sympy_expr_for_parseable():
    result = build_index([FormulaCandidate(latex=r"x+y", source="text")])
    assert result[0].sympy_expr is not None


def test_build_index_sympy_expr_none_for_unparseable():
    # Malformed LaTeX — parse_latex raises, sympy_expr must be None
    result = build_index([FormulaCandidate(latex=r"\frac{1{", source="text")])
    assert result[0].sympy_expr is None


def test_build_index_preserves_all_raw_fields():
    c = FormulaCandidate(latex=r"\alpha", source="tex", label="eq:1",
                         section="2.1", page=3)
    r = build_index([c])[0]
    assert r.latex == r"\alpha"
    assert r.source == "tex"
    assert r.label == "eq:1"
    assert r.section == "2.1"
    assert r.page == 3


def test_build_index_empty_list():
    assert build_index([]) == []


def test_build_index_copies_bbox():
    from backend.extractor.text_layer import FormulaCandidate
    c = FormulaCandidate(latex=r"\alpha", source="text", page=2, bbox=(10.0, 20.0, 100.0, 40.0))
    result = build_index([c])
    assert len(result) == 1
    assert result[0].bbox == (10.0, 20.0, 100.0, 40.0)


def test_build_index_copies_none_bbox():
    from backend.extractor.text_layer import FormulaCandidate
    c = FormulaCandidate(latex=r"\beta", source="text", page=0)
    result = build_index([c])
    assert len(result) == 1
    assert result[0].bbox is None


def test_build_index_copies_page_w_h():
    from backend.extractor.text_layer import FormulaCandidate
    c = FormulaCandidate(latex=r"\alpha", source="text", page=0,
                         bbox=(10.0, 20.0, 100.0, 40.0), page_w=612.0, page_h=792.0)
    result = build_index([c])
    assert result[0].page_w == 612.0
    assert result[0].page_h == 792.0


def test_build_index_copies_zero_page_dims():
    from backend.extractor.text_layer import FormulaCandidate
    c = FormulaCandidate(latex=r"\beta", source="tex")
    result = build_index([c])
    assert result[0].page_w == 0.0
    assert result[0].page_h == 0.0
