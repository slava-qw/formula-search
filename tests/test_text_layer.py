import fitz
import pytest
from backend.extractor.text_layer import extract_formulas, FormulaCandidate, _looks_like_formula


def _make_pdf(text: str) -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 100), text, fontsize=12)
    data = doc.tobytes()
    doc.close()
    return data


def test_looks_like_formula_detects_backslash():
    assert _looks_like_formula(r"\frac{d}{dx}") is True


def test_looks_like_formula_detects_caret():
    assert _looks_like_formula("x^2 + y^2") is True


def test_looks_like_formula_ignores_plain_text():
    assert _looks_like_formula("Hello world, this is a sentence.") is False


def test_extract_formulas_finds_latex_text():
    pdf = _make_pdf(r"\frac{d}{dx}")
    results = extract_formulas(pdf)
    assert len(results) >= 1
    assert any(r"\frac" in c.latex for c in results)


def test_extract_formulas_skips_plain_text():
    pdf = _make_pdf("The quick brown fox jumps over the lazy dog.")
    results = extract_formulas(pdf)
    assert len(results) == 0


def test_extract_formulas_returns_correct_structure():
    pdf = _make_pdf(r"$E = mc^2$")
    results = extract_formulas(pdf)
    assert len(results) >= 1
    for c in results:
        assert isinstance(c, FormulaCandidate)
        assert c.page == 0
        assert c.source == "text"


def test_extract_formulas_multipage():
    doc = fitz.open()
    page0 = doc.new_page()
    page0.insert_text((50, 100), r"\alpha + \beta", fontsize=12)
    page1 = doc.new_page()
    page1.insert_text((50, 100), r"\gamma^2", fontsize=12)
    pdf = doc.tobytes()
    doc.close()

    results = extract_formulas(pdf)
    pages = {c.page for c in results}
    assert 0 in pages
    assert 1 in pages


def test_extract_formulas_bbox_is_four_floats():
    pdf = _make_pdf(r"\frac{d}{dx}")
    results = extract_formulas(pdf)
    assert len(results) >= 1
    for c in results:
        assert c.bbox is not None
        assert len(c.bbox) == 4
        assert all(isinstance(v, float) for v in c.bbox)


def test_extract_formulas_page_w_h():
    pdf = _make_pdf(r"\frac{d}{dx}")
    results = extract_formulas(pdf)
    assert len(results) >= 1
    for c in results:
        assert c.page_w > 0
        assert c.page_h > 0
        assert isinstance(c.page_w, float)
        assert isinstance(c.page_h, float)
