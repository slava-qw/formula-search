from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Iterable, List, Optional, Tuple

from backend.extractor.text_layer import BBox, FormulaCandidate, NormCharBox
from backend.matcher.normalizer import normalize_latex
from backend.matcher.notation import Notation, notation
from backend.matcher.occurrence import identify_occurrences, source_anchor

# Sentinel distinguishing "not computed yet" from a legitimate computed
# value of None/"" for the lazy properties below.
_SYMPY_EXPR_UNSET = object()
_NORMALIZED_LATEX_UNSET = object()


@dataclass
class IndexedFormula:
    # Raw fields carried from FormulaCandidate
    latex: str
    source: str
    label: str = ""
    section: str = ""
    env: str = ""
    page: int = -1
    bbox: Optional[BBox] = None
    page_w: float = 0.0
    page_h: float = 0.0

    # PDF text-layer context for query-specific highlights.
    pdf_line_text: str = ""
    pdf_line_norm: str = ""
    pdf_line_norm_chars: Optional[List[NormCharBox]] = None
    pdf_line_raw_chars: Optional[List[NormCharBox]] = None
    pdf_latex_chars: Optional[List[NormCharBox]] = None
    pdf_line_math_fraction: float = 0.0
    pdf_line_has_math_signal: bool = False
    pdf_line_group_kind: str = ""
    bbox_score: float = 0.0
    bbox_quality: str = ""
    source_order: int = -1
    page_rotation: int = 0
    source_file: str = ''
    source_line: int = 0
    source_location_kind: str = ''
    occurrence_id: str = ''
    occurrence_anchor: str = ''

    # Pre-computed at index time
    tokens: List[str] = field(default_factory=list)
    parsed_notation: Optional[Notation] = field(default=None, repr=False, compare=False)

    # Cheap syntax normalization; never invokes SymPy. Cache per record because
    # document sessions may outlive the module-level bounded normalization cache.
    _normalized_latex_cache: Any = field(default=_NORMALIZED_LATEX_UNSET, repr=False, compare=False)

    @property
    def normalized_latex(self) -> str:
        if self._normalized_latex_cache is _NORMALIZED_LATEX_UNSET:
            self._normalized_latex_cache = normalize_latex(self.latex)
        return self._normalized_latex_cache

    # Compatibility for callers explicitly requesting symbolic parsing.
    # Ordinary search never accesses this property. Algebraic search runs
    # separately in a process with a hard time limit.
    _sympy_expr_cache: Any = field(default=_SYMPY_EXPR_UNSET, repr=False, compare=False)

    @property
    def sympy_expr(self) -> Optional[Any]:
        if self._sympy_expr_cache is _SYMPY_EXPR_UNSET:
            from backend.matcher.ast_match import parse_query
            self._sympy_expr_cache = parse_query(self.normalized_latex)
        return self._sympy_expr_cache


def build_index(candidates: Iterable[FormulaCandidate]) -> List[IndexedFormula]:
    result = []
    source_sites = {}
    for c, occurrence_id in identify_occurrences(candidates):
        site = (c.source_file, c.source_line, c.source_location_kind, c.latex)
        ordinal = source_sites.get(site, 0)
        if c.source == 'tex':
            source_sites[site] = ordinal + 1
        result.append(IndexedFormula(
            latex=c.latex,
            source=c.source,
            label=c.label,
            section=c.section,
            env=c.env,
            page=c.page,
            bbox=c.bbox,
            page_w=c.page_w,
            page_h=c.page_h,
            page_rotation=c.page_rotation,
            pdf_line_text=c.pdf_line_text,
            pdf_line_norm=c.pdf_line_norm,
            pdf_line_norm_chars=c.pdf_line_norm_chars,
            pdf_line_raw_chars=c.pdf_line_raw_chars,
            pdf_latex_chars=c.pdf_latex_chars,
            pdf_line_math_fraction=getattr(c, "pdf_line_math_fraction", 0.0),
            pdf_line_has_math_signal=getattr(c, "pdf_line_has_math_signal", False),
            pdf_line_group_kind=getattr(c, "pdf_line_group_kind", ""),
            bbox_score=c.bbox_score,
            bbox_quality=c.bbox_quality,
            source_order=getattr(c, "source_order", -1),
            source_file=c.source_file,
            source_line=c.source_line,
            source_location_kind=c.source_location_kind,
            occurrence_id=occurrence_id,
            occurrence_anchor=source_anchor(c, ordinal),
            tokens=list(notation(c.latex).tokens),
            parsed_notation=notation(c.latex),
            _normalized_latex_cache=normalize_latex(c.latex),
        ))
    return result
