from pathlib import Path
import os

import pytest

from backend.extractor.arxiv_tex import _merge_tex_files, assign_bboxes_from_pdf, parse_tex_formulas
from backend.matcher.fuzzy import search_formulas
from backend.matcher.index import build_index


@pytest.mark.skipif(
    not os.environ.get("ATTENTION_TEX_SRC") or not os.environ.get("ATTENTION_PDF"),
    reason="set ATTENTION_TEX_SRC and ATTENTION_PDF to run the uploaded Attention paper regression",
)
def test_attention_is_all_you_need_formula_locations_regression():
    src = Path(os.environ["ATTENTION_TEX_SRC"])
    pdf = Path(os.environ["ATTENTION_PDF"])
    files = {str(p.relative_to(src)): p.read_text(errors="replace") for p in src.rglob("*.tex")}
    candidates = parse_tex_formulas(_merge_tex_files(files))
    assign_bboxes_from_pdf(candidates, pdf.read_bytes())

    # Current active source formulas after stripping commented-out inputs and
    # non-formula noise. Keep this as a range so harmless wrapper filtering does
    # not break the regression while still catching major parser regressions.
    assert 95 <= len(candidates) <= 115
    assert all(c.page >= 0 for c in candidates)
    assert sum(1 for c in candidates if c.bbox is not None) >= 90

    index = build_index(candidates)
    probes = {
        r"QK^T": 3,
        r"\frac{1}{\sqrt{d_k}}": 3,
        r"W^Q_i": 4,
        r"q \cdot k": 3,
        r"O(n^2 \cdot d)": 5,
    }
    for query, zero_based_page in probes.items():
        match = search_formulas(query, index)[0]
        assert match.page == zero_based_page
        assert match.bbox is not None
