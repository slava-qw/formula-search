import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
import fitz


def _minimal_pdf() -> bytes:
    doc = fitz.open()
    doc.new_page()
    data = doc.tobytes()
    doc.close()
    return data


def _pix2tex_available() -> bool:
    try:
        import pix2tex  # noqa: F401
        return True
    except ImportError:
        return False


def test_render_page_image_returns_pil_image():
    from PIL import Image
    from backend.extractor.ocr_page import render_page_image
    pdf = _minimal_pdf()
    img = render_page_image(pdf, 0)
    assert isinstance(img, Image.Image)
    assert img.width > 0
    assert img.height > 0


def test_render_page_image_is_2x_scale():
    from backend.extractor.ocr_page import render_page_image
    pdf = _minimal_pdf()
    img = render_page_image(pdf, 0)
    with fitz.open(stream=pdf, filetype="pdf") as doc:
        rect = doc[0].rect
    # 2x Matrix → pixel dimensions should be exactly 2x the PDF point dimensions (±1 for rounding)
    assert abs(img.width - 2 * rect.width) <= 1
    assert abs(img.height - 2 * rect.height) <= 1


def test_ocr_page_returns_formula_candidate_when_model_returns_latex():
    from unittest.mock import patch, MagicMock
    from backend.extractor.text_layer import FormulaCandidate
    import backend.extractor.ocr_page as ocr_module
    pdf = _minimal_pdf()
    mock_model = MagicMock(return_value=r"\frac{d}{dx}")
    with patch.object(ocr_module, "_ocr_model", mock_model):
        from backend.extractor.ocr_page import ocr_page
        result = ocr_page(pdf, 0, (10, 10, 150, 60))
    assert isinstance(result, list)
    assert len(result) == 1
    candidate = result[0]
    assert isinstance(candidate, FormulaCandidate)
    assert candidate.source == "ocr"
    assert candidate.page == 0
    assert candidate.latex == r"\frac{d}{dx}"


def test_ocr_page_returns_empty_when_model_returns_empty():
    from unittest.mock import patch, MagicMock
    import backend.extractor.ocr_page as ocr_module
    pdf = _minimal_pdf()
    mock_model = MagicMock(return_value="")
    with patch.object(ocr_module, "_ocr_model", mock_model):
        from backend.extractor.ocr_page import ocr_page
        result = ocr_page(pdf, 0, (10, 10, 150, 60))
    assert result == []


@pytest.mark.skipif(
    not _pix2tex_available(),
    reason="pix2tex not installed"
)
def test_ocr_page_with_real_model_returns_list():
    from backend.extractor.ocr_page import ocr_page
    from backend.extractor.text_layer import FormulaCandidate
    pdf = _minimal_pdf()
    result = ocr_page(pdf, 0, (10, 10, 150, 60))
    assert isinstance(result, list)
    for candidate in result:
        assert isinstance(candidate, FormulaCandidate)
        assert candidate.source == "ocr"
        assert candidate.page == 0
