from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple
import re

import fitz

BBox = Tuple[float, float, float, float]
NormCharBox = Tuple[str, BBox]


@dataclass
class FormulaCandidate:
    latex: str
    source: str       # "tex" | "ocr" | "text"
    label: str = ""   # equation label, e.g. "eq:loss"
    section: str = "" # section heading at extraction time
    env: str = ""     # "equation" | "align" | "inline" | "display" | ...
    page: int = -1    # PDF page (0-indexed); -1 when unknown
    bbox: Optional[BBox] = None  # (x0, y0, x1, y1) in unrotated crop-relative points
    page_w: float = 0.0          # Unrotated crop width; 0.0 when unknown
    page_h: float = 0.0          # Unrotated crop height; 0.0 when unknown

    # Optional PDF text-layer context used only for query-specific bbox
    # refinement. It is not returned by /search; the backend uses it to return
    # a tighter bbox for partial queries such as QK^T inside a longer equation.
    pdf_line_text: str = ""
    pdf_line_norm: str = ""
    pdf_line_norm_chars: Optional[List[NormCharBox]] = None
    pdf_line_raw_chars: Optional[List[NormCharBox]] = None
    # Reconstructed PDF notation: one glyph box per character of `latex`,
    # including inserted script syntax. Allows notation-preserving highlights.
    pdf_latex_chars: Optional[List[NormCharBox]] = None
    pdf_line_math_fraction: float = 0.0
    pdf_line_has_math_signal: bool = False
    pdf_line_group_kind: str = ""
    bbox_score: float = 0.0
    bbox_quality: str = ""

    # TeX-side context. These fields are intentionally optional so the same
    # dataclass can be used for text/OCR candidates. For arXiv TeX candidates
    # they let the PDF mapper distinguish repeated short formulas like d_k by
    # the surrounding prose instead of by the formula string alone.
    tex_context_before: str = ""
    tex_context_after: str = ""
    source_order: int = -1
    page_rotation: int = 0      # PDF /Rotate; applied only when returning a match
    source_file: str = ''       # Archive-relative TeX name; empty if unavailable
    source_line: int = 0        # 1-based opening delimiter/call; 0 if unavailable
    source_location_kind: str = ''  # "literal" or "macro" (invocation, not definition)


def _looks_like_formula(text: str) -> bool:
    # Literal TeX is useful in generated/test PDFs, but an underscore in a URL
    # or a dollar price is not evidence of mathematics.
    if re.search(r'\\[A-Za-z]{2,}|[A-Za-z0-9)}]\s*\^\s*[{A-Za-z0-9]', text):
        return True
    for inner in re.findall(r'\$([^$]+)\$', text):
        inner = inner.strip()
        if (len(inner) == 1 and inner.isalpha()) or (
                any(ch.isalpha() for ch in inner) and re.search(r'[=+_^]', inner)):
            return True
    return False


def _union_bbox(boxes: Iterable[Tuple[float, float, float, float]]) -> BBox:
    boxes = list(boxes)
    return (
        float(min(b[0] for b in boxes)),
        float(min(b[1] for b in boxes)),
        float(max(b[2] for b in boxes)),
        float(max(b[3] for b in boxes)),
    )


def extract_formulas(pdf_bytes: bytes) -> List[FormulaCandidate]:
    # Import here to keep FormulaCandidate usable by the independent TeX/OCR
    # paths without introducing a circular dependency on the PDF mapper.
    from .pdf_math import extract_page
    from .pdf_fonts import DocumentFonts
    results: List[FormulaCandidate] = []
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        fonts = DocumentFonts(doc)
        for page_num, page in enumerate(doc):
            results.extend(extract_page(page, page_num, fonts))
    return results
