import fitz
import gzip
import io
import tarfile
import pytest
from unittest.mock import patch, AsyncMock, MagicMock

from backend.extractor.arxiv_tex import (
    is_arxiv_url,
    extract_arxiv_id,
    parse_tex_formulas,
    fetch_tex_source,
    assign_bboxes_from_pdf,
    _merge_tex_files,
)
from backend.extractor.text_layer import FormulaCandidate


def test_is_arxiv_url_pdf():
    assert is_arxiv_url("https://arxiv.org/pdf/1706.03762") is True


def test_is_arxiv_url_abs():
    assert is_arxiv_url("https://arxiv.org/abs/2301.00000v2") is True


def test_is_arxiv_url_non_arxiv():
    assert is_arxiv_url("https://example.com/paper.pdf") is False


def test_is_arxiv_url_empty():
    assert is_arxiv_url("") is False


def test_extract_arxiv_id_pdf():
    assert extract_arxiv_id("https://arxiv.org/pdf/1706.03762") == "1706.03762"


def test_extract_arxiv_id_versioned():
    assert extract_arxiv_id("https://arxiv.org/pdf/2301.00000v2") == "2301.00000v2"


def test_extract_arxiv_id_abs():
    assert extract_arxiv_id("https://arxiv.org/abs/1706.03762") == "1706.03762"


def test_parse_tex_formulas_equation_env():
    tex = r"""
\section{Introduction}
\begin{equation}
\label{eq:foo}
E = mc^2
\end{equation}
"""
    results = parse_tex_formulas(tex)
    assert len(results) == 1
    r = results[0]
    assert r.label == "eq:foo"
    assert r.section == "Introduction"
    assert r.env == "equation"
    assert "label" not in r.latex
    assert "E = mc^2" in r.latex


def test_parse_tex_formulas_keeps_standalone_math_symbols():
    tex = r"where $n$ is the count and $x^2 + y^2 = r^2$ is the circle"
    results = parse_tex_formulas(tex)
    latexes = [r.latex for r in results]
    assert "n" in latexes
    assert any("x^2" in l for l in latexes)


def test_parse_tex_formulas_section_tracking():
    tex = r"""
$\alpha + \beta$
\section{Results}
$\gamma^2$
"""
    results = parse_tex_formulas(tex)
    alpha = next((r for r in results if "alpha" in r.latex), None)
    gamma = next((r for r in results if "gamma" in r.latex), None)
    assert alpha is not None and alpha.section == ""
    assert gamma is not None and gamma.section == "Results"


def test_parse_tex_formulas_label_stripped_from_latex():
    tex = r"""
\begin{equation}
\label{eq:bar}
\int_0^1 f(x)\,dx
\end{equation}
"""
    results = parse_tex_formulas(tex)
    assert len(results) == 1
    assert "label" not in results[0].latex
    assert results[0].label == "eq:bar"


def test_parse_tex_formulas_display_bracket():
    tex = r"\[ \frac{1}{2} \]"
    results = parse_tex_formulas(tex)
    assert len(results) >= 1
    assert any(r.env == "display" and r"\frac" in r.latex for r in results)


def test_parse_tex_formulas_align_env():
    tex = r"""
\begin{align}
a &= b + c \\
d &= e
\end{align}
"""
    results = parse_tex_formulas(tex)
    assert len(results) == 1
    assert results[0].env == "align"


def test_parse_tex_formulas_comment_stripped():
    tex = r"""
% $\hidden$
$\visible$
"""
    results = parse_tex_formulas(tex)
    latexes = [r.latex for r in results]
    assert r"\hidden" not in latexes
    assert r"\visible" in latexes


@pytest.mark.asyncio
async def test_fetch_tex_source_tar_gz():
    tex_bytes = rb"\documentclass{article}\begin{document}Hello\end{document}"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(name="main.tex")
        info.size = len(tex_bytes)
        tar.addfile(info, io.BytesIO(tex_bytes))
    tar_content = buf.getvalue()

    with patch("backend.extractor.arxiv_tex._httpx_get", new=AsyncMock(return_value=tar_content)):
        result = await fetch_tex_source("1706.03762")

    assert "documentclass" in result


def _make_pdf_with_text(text: str) -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    for idx, line in enumerate(text.splitlines() or [text]):
        page.insert_text((50, 100 + idx * 34), line, fontsize=12)
    data = doc.tobytes()
    doc.close()
    return data


def test_assign_bboxes_hits_formula_not_section_heading():
    pdf_bytes = _make_pdf_with_text("Introduction\nE = mc^2")
    candidates = [FormulaCandidate(latex=r"E = mc^2", source="tex", section="Introduction", env="equation")]
    assign_bboxes_from_pdf(candidates, pdf_bytes)
    assert candidates[0].page == 0
    assert candidates[0].bbox is not None
    assert len(candidates[0].bbox) == 4
    x0, y0, x1, y1 = candidates[0].bbox
    assert x0 > 40
    assert y0 > 100
    assert x1 - x0 < 120


def test_assign_bboxes_missing_section_keeps_defaults():
    pdf_bytes = _make_pdf_with_text("Results")
    candidates = [FormulaCandidate(latex=r"x+y", source="tex", section="Introduction")]
    assign_bboxes_from_pdf(candidates, pdf_bytes)
    assert candidates[0].bbox is None
    assert candidates[0].page == -1


def test_assign_bboxes_empty_section_candidates_unmodified():
    pdf_bytes = _make_pdf_with_text("Introduction")
    candidates = [FormulaCandidate(latex=r"x+y", source="tex", section="")]
    assign_bboxes_from_pdf(candidates, pdf_bytes)
    assert candidates[0].bbox is None
    assert candidates[0].page == -1


def test_assign_bboxes_section_fallback_sets_page_only_case_insensitive():
    pdf_bytes = _make_pdf_with_text("INTRODUCTION")
    candidates = [FormulaCandidate(latex=r"x+y", source="tex", section="Introduction")]
    assign_bboxes_from_pdf(candidates, pdf_bytes)
    assert candidates[0].page == 0
    assert candidates[0].bbox is None
    assert candidates[0].page_w > 0
    assert candidates[0].page_h > 0


@pytest.mark.asyncio
async def test_fetch_and_parse_arxiv_pdf_failure_keeps_bbox_none():
    from backend.extractor.arxiv_tex import fetch_and_parse_arxiv

    async def _fake_tex(arxiv_id):
        return r"\section{Intro}" + "\n" + r"\begin{equation}x+y\end{equation}"

    async def _fail_pdf(arxiv_id):
        raise ValueError("PDF unavailable")

    with patch("backend.extractor.arxiv_tex.fetch_tex_source", side_effect=_fake_tex), \
         patch("backend.extractor.arxiv_tex._fetch_pdf_bytes", side_effect=_fail_pdf):
        result = await fetch_and_parse_arxiv("1706.03762v7")

    assert len(result) >= 1
    assert all(c.bbox is None for c in result)


def test_assign_bboxes_sets_page_w_h():
    pdf_bytes = _make_pdf_with_text("Introduction\nx + y")
    candidates = [FormulaCandidate(latex=r"x+y", source="tex", section="Introduction", env="equation")]
    assign_bboxes_from_pdf(candidates, pdf_bytes)
    assert candidates[0].page_w > 0
    assert candidates[0].page_h > 0
    assert isinstance(candidates[0].page_w, float)
    assert isinstance(candidates[0].page_h, float)
    assert candidates[0].bbox is not None




def test_assign_bboxes_uses_tight_inline_formula_bbox_when_safe():
    pdf_bytes = _make_pdf_with_text("Attention\nwhere softmax appears here")
    candidates = [FormulaCandidate(latex=r"\mathrm{softmax}", source="tex", section="Attention", env="inline")]
    assign_bboxes_from_pdf(candidates, pdf_bytes)
    assert candidates[0].page == 0
    assert candidates[0].bbox is not None
    x0, y0, x1, y1 = candidates[0].bbox
    assert y0 > 100
    assert 35 <= (x1 - x0) <= 80
    assert x0 > 80

def test_assign_bboxes_missing_section_keeps_zero_dims():
    pdf_bytes = _make_pdf_with_text("Results")
    candidates = [FormulaCandidate(latex=r"x+y", source="tex", section="Introduction")]
    assign_bboxes_from_pdf(candidates, pdf_bytes)
    assert candidates[0].page_w == 0.0
    assert candidates[0].page_h == 0.0


def test_assign_bboxes_nested_frac_display_uses_full_formula_line_not_prefix():
    pdf_bytes = _make_pdf_with_text(
        "Scaled Dot-Product Attention\n"
        "Attention(Q,K,V) = softmax(QKT / sqrt(dk)) V"
    )
    candidates = [FormulaCandidate(
        latex=r"\mathrm{Attention}(Q,K,V)=\mathrm{softmax}(\frac{QK^T}{\sqrt{d_k}})V",
        source="tex",
        section="Scaled Dot-Product Attention",
        env="equation",
    )]
    assign_bboxes_from_pdf(candidates, pdf_bytes)
    assert candidates[0].page == 0
    assert candidates[0].bbox is not None
    x0, y0, x1, y1 = candidates[0].bbox
    assert y0 > 100
    assert x0 > 40
    assert x1 - x0 > 190


def test_assign_bboxes_long_formula_does_not_snap_to_function_name_heading():
    pdf_bytes = _make_pdf_with_text(
        "Scaled Dot-Product Attention\n"
        "Attention\n"
        "Attention(Q,K,V) = softmax(QKT / sqrt(dk)) V"
    )
    candidates = [FormulaCandidate(
        latex=r"\mathrm{Attention}(Q,K,V)=\mathrm{softmax}(\frac{QK^T}{\sqrt{d_k}})V",
        source="tex",
        section="Scaled Dot-Product Attention",
        env="equation",
    )]
    assign_bboxes_from_pdf(candidates, pdf_bytes)
    assert candidates[0].page == 0
    assert candidates[0].bbox is not None
    x0, y0, x1, y1 = candidates[0].bbox
    assert y0 > 150
    assert x1 - x0 > 190


def test_assign_bboxes_rejects_plain_word_when_formula_line_missing():
    pdf_bytes = _make_pdf_with_text(
        "Scaled Dot-Product Attention\n"
        "Attention\n"
        "The appendix talks about attention in prose."
    )
    candidates = [FormulaCandidate(
        latex=r"\mathrm{Attention}(Q,K,V)=\mathrm{softmax}(\frac{QK^T}{\sqrt{d_k}})V",
        source="tex",
        section="Scaled Dot-Product Attention",
        env="equation",
    )]
    assign_bboxes_from_pdf(candidates, pdf_bytes)
    assert candidates[0].bbox is None


def test_display_bbox_rejects_prose_attention_word():
    pdf_bytes = _make_pdf_with_text(
        "Appendix\n"
        "The word attention appears here, but this is ordinary prose."
    )
    candidates = [FormulaCandidate(
        latex=r"\mathrm{Attention}(Q,K,V)=\mathrm{softmax}(QK^T)V",
        source="tex",
        section="Appendix",
        env="equation",
    )]
    assign_bboxes_from_pdf(candidates, pdf_bytes)
    assert candidates[0].page == 0  # page-only fallback is acceptable
    assert candidates[0].bbox is None


def test_search_qkt_returns_query_subspan_bbox_not_attention_word():
    from backend.matcher.index import build_index
    from backend.matcher.fuzzy import search_formulas

    pdf_bytes = _make_pdf_with_text(
        "Scaled Dot-Product Attention\n"
        "Attention(Q,K,V) = softmax(QKT / sqrt(dk)) V"
    )
    candidates = [FormulaCandidate(
        latex=r"\mathrm{Attention}(Q,K,V)=\mathrm{softmax}(QK^T/\sqrt{d_k})V",
        source="tex",
        section="Scaled Dot-Product Attention",
        env="equation",
    )]
    assign_bboxes_from_pdf(candidates, pdf_bytes)
    assert candidates[0].bbox is not None
    full_x0, full_y0, full_x1, full_y1 = candidates[0].bbox

    results = search_formulas(r"QK^T", build_index(candidates))
    assert results
    result = results[0]
    assert result.bbox is not None
    assert result.bbox_source == "query-subspan"
    q_x0, q_y0, q_x1, q_y1 = result.bbox
    assert q_x0 > full_x0
    assert q_x1 < full_x1
    assert (q_x1 - q_x0) < (full_x1 - full_x0) * 0.35
    assert full_y0 <= q_y0 <= q_y1 <= full_y1



def test_merge_tex_files_ignores_commented_input():
    files = {
        "main.tex": r"""
\documentclass{article}
\begin{document}
%\input{draft_appendix}
$visible + 1$
\end{document}
""",
        "draft_appendix.tex": r"$phantom + 1$",
    }
    merged = _merge_tex_files(files)
    results = parse_tex_formulas(merged)
    latexes = [r.latex for r in results]
    assert r"phantom + 1" not in latexes
    assert r"visible + 1" in latexes


def test_table_formula_can_map_before_section_heading():
    pdf_bytes = _make_pdf_with_text(
        "Table 1\n"
        "Self-Attention\n"
        "O(n2 · d)\n"
        "Why Self-Attention\n"
        "Later prose says O(n/r)."
    )
    candidates = [FormulaCandidate(
        latex=r"O(n^2 \cdot d)",
        source="tex",
        section="Why Self-Attention",
        env="inline",
        tex_context_before=r"\begin{tabular}{lccc} Self-Attention & ",
        tex_context_after=r" & $O(1)$ \\",
    )]
    assign_bboxes_from_pdf(candidates, pdf_bytes)
    assert candidates[0].page == 0
    assert candidates[0].bbox is not None
    assert candidates[0].pdf_line_text.startswith("O(n2")


def test_short_formula_uses_context_not_later_footnote():
    pdf_bytes = _make_pdf_with_text(
        "Multi-Head Attention\n"
        "linear projections to dk, dk and dv dimensions, respectively.\n"
        "variables with mean 0 and variance 1. Then q k has variance dk."
    )
    candidates = [FormulaCandidate(
        latex=r"d_k",
        source="tex",
        section="Multi-Head Attention",
        env="inline",
        tex_context_before="learned linear projections to ",
        tex_context_after=", $d_k$ and $d_v$ dimensions, respectively.",
    )]
    assign_bboxes_from_pdf(candidates, pdf_bytes)
    assert candidates[0].bbox is not None
    assert candidates[0].pdf_line_text.startswith("linear projections")


def test_wrapped_single_symbol_uses_context_for_bbox():
    pdf_bytes = _make_pdf_with_text(
        "Model Architecture\n"
        "of continuous representations z = (z1, ..., zn). Given z, the decoder then generates an output"
    )
    candidates = [FormulaCandidate(
        latex=r"\mathbf{z}",
        source="tex",
        section="Model Architecture",
        env="inline",
        tex_context_before="to a sequence of continuous representations ",
        tex_context_after=" = (z_1, ..., z_n). Given ",
    )]
    assign_bboxes_from_pdf(candidates, pdf_bytes)
    assert candidates[0].bbox is not None
    assert candidates[0].page == 0
    assert candidates[0].pdf_line_text.startswith("of continuous representations")


def test_table_duplicate_cells_use_row_and_column_hint():
    doc = fitz.open()
    page = doc.new_page()
    # Four table rows with repeated O(1) cells. The second Self-Attention O(1)
    # must not jump to the later restricted-row O(1).
    rows = [
        (140, "Self-Attention", ["O(n2 · d)", "O(1)", "O(1)"]),
        (152, "Recurrent", ["O(n · d2)", "O(n)", "O(n)"]),
        (164, "Convolutional", ["O(k · n · d2)", "O(1)", "O(logk(n))"]),
        (176, "Self-Attention (restricted)", ["O(r · n · d)", "O(1)", "O(n/r)"]),
    ]
    for y, label, cells in rows:
        page.insert_text((124, y), label, fontsize=10)
        for x, cell in zip((264, 351, 431), cells):
            page.insert_text((x, y), cell, fontsize=10)
    pdf_bytes = doc.tobytes()
    doc.close()

    candidate = FormulaCandidate(
        latex=r"O(1)",
        source="tex",
        section="Why Self-Attention",
        env="inline",
        tex_context_before=r"\rule{0pt}{2.0ex}Self-Attention & $O(n^2 \cdot d)$ & $O(1)$ & ",
        tex_context_after=r" \\ Recurrent & $O(n \cdot d^2)$ & $O(n)$ & $O(n)$",
    )
    assign_bboxes_from_pdf([candidate], pdf_bytes)
    assert candidate.bbox is not None
    assert 425 <= candidate.bbox[0] <= 440
    assert "O(1)" == candidate.pdf_line_text


def test_inline_fraction_stack_uses_nearest_source_context():
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 85), "except for the scaling factor of", fontsize=10)
    page.insert_text((125, 100), "1", fontsize=7)
    page.insert_text((120, 112), "sqrt(dk) . Additive attention", fontsize=10)
    page.insert_text((50, 145), "we scale the dot products by", fontsize=10)
    page.insert_text((445, 160), "1", fontsize=7)
    page.insert_text((440, 172), "sqrt(dk) .", fontsize=10)
    pdf_bytes = doc.tobytes()
    doc.close()

    candidate = FormulaCandidate(
        latex=r"\frac{1}{\sqrt{d_k}}",
        source="tex",
        section="Scaled Dot-Product Attention",
        env="inline",
        tex_context_before="except for the scaling factor of ",
        tex_context_after=". Additive attention computes",
    )
    assign_bboxes_from_pdf([candidate], pdf_bytes)
    assert candidate.bbox is not None
    assert candidate.bbox[0] < 200
    assert candidate.pdf_line_text.startswith("1 sqrt(dk)")


def test_section_map_handles_nested_label_in_heading():
    tex = r"""
\section{Methods \label{sec:methods}}
Before $a+b$.
\subsection{Nested \textbf{Title} \label{sec:nested}}
After $c+d$.
"""
    results = parse_tex_formulas(tex)
    assert [r.section for r in results] == ["Methods", "Nested Title"]


def test_query_bbox_rejects_short_symbol_spanning_prose_words():
    from backend.matcher.fuzzy import search_formulas
    from backend.matcher.index import build_index

    # Normalized prose line has d at the end of weighted and k at the start of keys.
    # The sentinel inserted for prose boundaries must prevent d_k from highlighting it.
    c = FormulaCandidate(
        latex=r"d_k",
        source="tex",
        env="inline",
        page=0,
        bbox=(40.0, 80.0, 180.0, 94.0),
        page_w=300.0,
        page_h=400.0,
        pdf_line_text="weighted keys",
        pdf_line_norm="weighted|keys",
        pdf_line_norm_chars=[
            (ch, (40.0 + i * 4.0, 80.0, 43.0 + i * 4.0, 90.0))
            for i, ch in enumerate("weighted|keys")
        ],
        bbox_quality="pdf-line-char",
    )
    results = search_formulas(r"d_k", build_index([c]))
    assert results[0].bbox is None
    assert results[0].bbox_source == "query-subspan-missing"


def test_absent_display_formula_never_maps_by_ordinal():
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((100, 130), 'x + y = 3', fontsize=12)
    pdf_bytes = doc.tobytes()
    doc.close()
    candidate = FormulaCandidate(r'\int_0^\infty e^{-t}dt = 1', 'tex', env='equation')
    assign_bboxes_from_pdf([candidate], pdf_bytes)
    assert candidate.bbox is None, f'Absent integral incorrectly mapped to {candidate.pdf_line_text!r}'
    assert candidate.page == -1
    assert candidate.bbox_score == 0


def test_failed_location_is_not_recomputed_on_every_search(monkeypatch):
    from backend.extractor import arxiv_tex
    doc = fitz.open()
    doc.new_page().insert_text((50, 90), 'x + y = 3')
    candidate = FormulaCandidate(r'\int e^{-t}dt', 'tex', env='equation')
    index = arxiv_tex.build_line_index(doc.tobytes(), [candidate])
    doc.close()
    assert not arxiv_tex.resolve_candidate_bbox(candidate, 0, index)
    monkeypatch.setattr(arxiv_tex, '_resolve_candidate_location', lambda *a: pytest.fail('must reuse failed location result'))
    assert not arxiv_tex.resolve_candidate_bbox(candidate, 0, index)


def test_section_start_is_an_estimate_without_a_formula_rectangle():
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 60), '1 Introduction', fontname='hebo', fontsize=15)
    page.insert_text((50, 100), 'x + y = 3')
    candidate = FormulaCandidate(r'\int_0^\infty e^{-t}dt = 1', 'tex', section='Introduction', env='equation')
    assign_bboxes_from_pdf([candidate], doc.tobytes())
    doc.close()
    assert candidate.page == 0 and candidate.bbox is None
    assert candidate.bbox_quality == 'section-estimate'
    assert candidate.bbox_score == 0
