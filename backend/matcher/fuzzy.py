from dataclasses import dataclass, replace
import re
from typing import Callable, List, Literal, Optional, Tuple
from rapidfuzz import fuzz
from backend.matcher.normalizer import normalize_latex
from backend.matcher.semantic import equivalent_candidates
from backend.matcher.index import IndexedFormula
from backend.matcher.notation import notation, find_notation, highlight_notation
from backend.pdf_geometry import displayed_geometry

# Reuse the exact same normalization as the TeX-to-PDF mapper. This keeps search
# result bboxes aligned with the line-level mapping and prevents a query such as
# QK^T from falling back to a random command word like "Attention".
from backend.extractor.arxiv_tex import _norm_string, _looks_like_pdf_math_line, _union_bbox

FUZZY_RAW_THRESHOLD = 75.0
SEMANTIC_TIME_BUDGET_SECONDS = 0.8

BBox = Tuple[float, float, float, float]


@dataclass
class MatchResult:
    score: float
    match_type: Literal["exact", "partial", "semantic", "fuzzy"]
    latex: str
    label: str
    section: str
    source: str
    highlighted_latex: str = ""
    page: int = -1
    bbox: Optional[BBox] = None  # Initial displayed PDF coordinates, including /Rotate
    page_w: float = 0.0
    page_h: float = 0.0
    bbox_source: str = "candidate"
    location_method: str = ""
    location_score: float = 0.0
    # Position of the matched IndexedFormula in the list passed to
    # search_formulas. Lets a caller with the parallel FormulaCandidate list
    # (backend/main.py's lazy /process + /search path) map on demand for just
    # this handful of results instead of every candidate up front.
    candidate_index: int = -1
    source_file: str = ''
    source_line: int = 0
    source_location_kind: str = ''
    occurrence_id: str = ''
    occurrence_anchor: str = ''


def _valid_bbox(bbox: Optional[BBox], candidate: IndexedFormula) -> Optional[BBox]:
    if bbox is None:
        return None
    x0, y0, x1, y1 = (float(v) for v in bbox)
    if x1 <= x0 or y1 <= y0:
        return None
    if candidate.page_w > 0 and candidate.page_h > 0:
        x0 = max(0.0, min(x0, candidate.page_w))
        x1 = max(0.0, min(x1, candidate.page_w))
        y0 = max(0.0, min(y0, candidate.page_h))
        y1 = max(0.0, min(y1, candidate.page_h))
    if x1 <= x0 or y1 <= y0:
        return None
    return (x0, y0, x1, y1)


def _union_norm_char_boxes(norm_chars, start: int, end: int) -> Optional[BBox]:
    boxes = [box for _, box in norm_chars[start:end] if box[2] > box[0] and box[3] > box[1]]
    if not boxes:
        return None
    return _union_bbox(boxes)


def _bbox_center(bbox: Optional[BBox]) -> tuple[float, float]:
    if bbox is None:
        return (0.0, 0.0)
    return ((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0)


def _bbox_area(bbox: Optional[BBox]) -> float:
    if bbox is None:
        return 0.0
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])


def _span_boxes(norm_chars, start: int, end: int) -> list[BBox]:
    return [box for _, box in norm_chars[start:end] if box[2] > box[0] and box[3] > box[1]]


def _span_has_large_gap(norm_chars, start: int, end: int) -> bool:
    """Reject normalized hits made by concatenating different prose words.

    The normalized PDF line intentionally removes spaces, which is useful for
    formulas but dangerous for short queries: d_k can otherwise match the final
    'd' of one prose word followed by the first 'k' of the next.  Character
    bboxes expose that mistake as a much larger inter-glyph gap than real
    subscript/superscript math.
    """
    boxes = _span_boxes(norm_chars, start, end)
    if len(boxes) < 2:
        return False
    widths = [max(0.1, b[2] - b[0]) for b in boxes]
    heights = [max(0.1, b[3] - b[1]) for b in boxes]
    median_width = sorted(widths)[len(widths) // 2]
    median_height = sorted(heights)[len(heights) // 2]
    for prev, cur in zip(boxes, boxes[1:]):
        prev_cy = (prev[1] + prev[3]) / 2.0
        cur_cy = (cur[1] + cur[3]) / 2.0
        gap = cur[0] - prev[2]
        same_band = abs(prev_cy - cur_cy) <= max(3.4, median_height * 0.55)
        if same_band and gap > max(3.1, median_width * 0.75):
            return True
        if not same_band and gap > max(3.4, median_width * 0.65):
            return True
    return False


def _span_bbox_if_safe(norm_chars, start: int, end: int, *, sensitive: bool) -> Optional[BBox]:
    bbox = _union_norm_char_boxes(norm_chars, start, end)
    if bbox is None:
        return None
    if sensitive and _span_has_large_gap(norm_chars, start, end):
        return None
    height = max(0.1, bbox[3] - bbox[1])
    width = max(0.1, bbox[2] - bbox[0])
    # Very short symbolic queries should not span a whole phrase.
    if sensitive and width > max(34.0, height * 4.8 + 8.0):
        return None
    return bbox


def _all_occurrences(text: str, needle: str) -> list[int]:
    if not text or not needle:
        return []
    out: list[int] = []
    pos = text.find(needle)
    while pos >= 0:
        out.append(pos)
        pos = text.find(needle, pos + 1)
    return out


def _choose_occurrence_bbox(query: str, variant: str, positions: list[int], candidate: IndexedFormula) -> Optional[BBox]:
    norm_chars = candidate.pdf_line_norm_chars or []
    if not positions or not norm_chars:
        return None
    sensitive = len(variant) <= 4 or any(ch in query for ch in '_^')
    target = _bbox_center(candidate.bbox) if candidate.bbox else None
    candidates: list[tuple[float, float, BBox]] = []
    for pos in positions:
        bbox = _valid_bbox(_span_bbox_if_safe(norm_chars, pos, pos + len(variant), sensitive=sensitive), candidate)
        if bbox is None:
            continue
        if target is not None:
            cx, cy = _bbox_center(bbox)
            dist = (cx - target[0]) ** 2 + (cy - target[1]) ** 2
        else:
            dist = float(pos)
        candidates.append((dist, _bbox_area(bbox), bbox))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]))
    return candidates[0][2]



def _structured_math_query(query: str, normalized_query: str) -> bool:
    """Queries with TeX structure should not use loose raw fuzzy matching."""
    q = query or ""
    if re.search(r'[\\_^{}]', q):
        return True
    if len(normalized_query or "") <= 8 and re.search(r'[0-9A-Za-z]', q):
        return True
    if re.search(r'\d', q) and re.search(r'[()+*/=×]', q):
        return True
    return False


def _safe_query_subspan_hit(candidate: IndexedFormula, line_norm: str, start: int, end: int) -> bool:
    """Reject short hits that are embedded in ordinary prose words.

    The PDF text layer has no explicit math markup for many inline symbols. For
    short queries such as d_k, accepting a normalized span inside prose gives the
    exact bug reported by the user: the highlight lands on a word sequence rather
    than on a formula. Math-font/signal lines are exempt; plain prose requires
    clean alphanumeric boundaries around the match.
    """
    if end <= start:
        return False
    span = line_norm[start:end]
    if '|' in span:
        return False
    if len(span) > 4:
        return True
    if (candidate.pdf_line_math_fraction or 0.0) > 0.0 or candidate.pdf_line_has_math_signal or _looks_like_pdf_math_line(candidate.pdf_line_text):
        return True
    left = line_norm[start - 1] if start > 0 else '|'
    right = line_norm[end] if end < len(line_norm) else '|'
    if left.isalnum() or right.isalnum():
        return False
    return True



def _single_literal_bbox(candidate: IndexedFormula, literal: str) -> tuple[bool, Optional[BBox]]:
    """Scan single-glyph literals without rebuilding a line's text/box arrays.

    Empty or multi-character glyph data uses the general path. The caller
    excludes digits (which need neighboring-digit checks) and TeX structure.
    """
    best = None
    distance = None
    has_raw = False
    target = _bbox_center(candidate.bbox)
    for char, box in candidate.pdf_line_raw_chars or ():
        if char.isspace():
            continue
        if len(char) != 1:
            return False, None
        has_raw = True
        if char != literal and not (char == '−' and literal == '-'):
            continue
        # Match the general span union's rejection before float conversion.
        if not (box[2] > box[0] and box[3] > box[1]):
            continue
        bbox = _valid_bbox(box, candidate)
        if bbox is None:
            continue
        center = _bbox_center(bbox)
        delta = (center[0] - target[0]) ** 2 + (center[1] - target[1]) ** 2
        if distance is None or delta < distance:
            best, distance = bbox, delta
    return has_raw, best


def _legacy_query_specific_bbox(query: str, candidate: IndexedFormula) -> Optional[BBox]:
    """Return the rendered bbox for the query substring inside the mapped line.

    The line normalizer removes spaces, so this function is deliberately more
    conservative than a raw ``str.find``.  It checks every occurrence, rejects
    short hits that cross prose word gaps, and chooses the occurrence closest to
    the candidate's mapped bbox when a rendered line contains the same symbol
    several times.
    """
    if candidate.pdf_latex_chars:
        span = find_notation(notation(query).tokens, candidate.parsed_notation or notation(candidate.latex))
        if span:
            return _valid_bbox(_union_norm_char_boxes(candidate.pdf_latex_chars, *span), candidate)
        return None

    # Literal rendered notation keeps punctuation boxes that the legacy PDF
    # matching key discards. N(K+1) must include +1 and the closing parenthesis,
    # rather than shrinking to the easier-to-match prefix N(K.
    literal = normalize_latex(query)
    if not re.search(r'[\\{}]', query):
        literal = re.sub(r'\s+', '', query).replace('−', '-')
    if len(literal) == 1 and not literal.isdecimal() and literal not in '\\{}_^':
        supported, bbox = _single_literal_bbox(candidate, literal)
        if supported:
            return bbox
    # Structured queries use the normalized path below; avoid preparing a raw
    # line that they cannot consume. Nothing is retained beyond this call.
    raw = ([(ch.replace('−', '-'), box) for ch, box in (candidate.pdf_line_raw_chars or []) if not ch.isspace()]
           if literal and not re.search(r'[\\{}]', literal) else [])
    if raw:
        text = ''.join(ch for ch, _ in raw)
        options = []
        for start in _all_occurrences(text, literal):
            end = start + len(literal)
            if literal.isdecimal() and ((start and text[start - 1].isdecimal()) or (end < len(text) and text[end].isdecimal())):
                continue
            if literal.isalnum() and _span_has_large_gap(raw, start, end):
                continue
            bbox = _valid_bbox(_union_norm_char_boxes(raw, start, end), candidate)
            if bbox:
                target = _bbox_center(candidate.bbox)
                center = _bbox_center(bbox)
                options.append(((center[0] - target[0]) ** 2 + (center[1] - target[1]) ** 2, bbox))
        if options:
            return min(options, key=lambda item: item[0])[1]
        # If the full literal expression is missing, a flattened key must not
        # turn different operators or a shorter fragment into a verified hit.
        if not re.search(r'[_^]', literal):
            return None

    line_norm = candidate.pdf_line_norm or ""
    norm_chars = candidate.pdf_line_norm_chars or []
    if not line_norm or not norm_chars:
        return None

    # Only the whole query may earn "query-subspan" provenance. Candidate
    # alignment's partial-word variants are not proof of the query's location.
    variant = _norm_string(query, is_latex=True)
    variants = [variant] if len(variant) >= 2 else []
    for variant in variants:
        positions = [
            pos for pos in _all_occurrences(line_norm, variant)
            if _safe_query_subspan_hit(candidate, line_norm, pos, pos + len(variant))
        ]
        if not positions:
            continue
        bbox = _choose_occurrence_bbox(query, variant, positions, candidate)
        if bbox is not None:
            return bbox
    return None


def _query_specific_bbox(query: str, candidate: IndexedFormula) -> Optional[BBox]:
    previous = _legacy_query_specific_bbox(query, candidate)
    if previous is not None or candidate.bbox is None:
        return previous
    from backend.matcher.source_glyphs import verified_query_bbox
    return verified_query_bbox(query, candidate, _legacy_query_specific_bbox, _valid_bbox)


def _query_requires_verified_subspan(query: str) -> bool:
    longest = len(_norm_string(query, is_latex=True))
    return longest <= 4 or bool(re.search(r'[_^]', query or ''))

def _bbox_for_result(query: str, candidate: IndexedFormula, match_type: str) -> tuple[Optional[BBox], str]:
    if match_type in {"partial", "exact"}:
        refined = _query_specific_bbox(query, candidate)
        if refined is not None:
            return refined, "query-subspan"

        # For TeX-sourced partial matches, never reuse the whole candidate bbox
        # when the query is not present in the mapped PDF line. That is exactly
        # how a query such as QK^T can end up highlighting an unrelated word such
        # as "Attention": the TeX formula matches structurally, but the
        # formula-to-PDF mapper attached the candidate to the wrong rendered
        # line. Returning no bbox is safer than drawing a confidently wrong
        # highlight; the result can still show as a page-only jump.
        if (
            candidate.source == "tex"
            and (candidate.pdf_line_norm or candidate.pdf_line_raw_chars)
            and (match_type == "partial" or _query_requires_verified_subspan(query))
        ):
            return None, "query-subspan-missing"

    bbox = _valid_bbox(candidate.bbox, candidate)
    return bbox, candidate.bbox_quality or "candidate"


def _make_result(
    *,
    query: str,
    candidate: IndexedFormula,
    candidate_index: int,
    score: float,
    match_type: Literal["exact", "partial", "semantic", "fuzzy"],
    highlighted_latex: str = "",
) -> MatchResult:
    bbox, bbox_source = _bbox_for_result(query, candidate, match_type)
    bbox, page_w, page_h = displayed_geometry(bbox, candidate.page_w, candidate.page_h, candidate.page_rotation)
    return MatchResult(
        score=score,
        match_type=match_type,
        latex=candidate.latex,
        highlighted_latex=highlighted_latex,
        label=candidate.label,
        section=candidate.section,
        source=candidate.source,
        page=candidate.page,
        bbox=bbox,
        page_w=page_w,
        page_h=page_h,
        bbox_source=bbox_source,
        location_method=candidate.bbox_quality or candidate.source,
        location_score=candidate.bbox_score,
        candidate_index=candidate_index,
        source_file=candidate.source_file,
        source_line=candidate.source_line,
        source_location_kind=candidate.source_location_kind,
        occurrence_id=candidate.occurrence_id,
        occurrence_anchor=candidate.occurrence_anchor,
    )


def _rounded_bbox_key(bbox: Optional[BBox]) -> Optional[tuple[float, float, float, float]]:
    if bbox is None:
        return None
    return tuple(round(float(v), 1) for v in bbox)  # type: ignore[return-value]


def _dedupe_results(results: list[MatchResult]) -> list[MatchResult]:
    """Preserve source identities and withhold colliding heuristic highlights."""
    kept: list[MatchResult] = []
    seen: set[tuple] = set()
    source_locations: dict[tuple, list[int]] = {}
    for r in results:
        bbox_key = _rounded_bbox_key(r.bbox)
        source_identity = r.source == 'tex' and bool(r.occurrence_id)
        if source_identity:
            # Mapping is independent of source retrieval. A shared rectangle
            # cannot prove that two distinct source occurrences are duplicates.
            key = ('source-occurrence', r.occurrence_id)
        elif r.bbox_source == 'query-subspan' and bbox_key is not None:
            key = ('query-subspan', r.page, bbox_key)
        else:
            # Without a verified location, identical source strings may denote
            # different occurrences; do not silently merge them.
            key = (normalize_latex(r.latex or ''), r.page, bbox_key, r.match_type, r.bbox_source,
                   r.candidate_index if bbox_key is None else None)
        if key in seen:
            continue
        seen.add(key)
        if source_identity and r.page >= 0 and bbox_key is not None:
            source_locations.setdefault((r.page, bbox_key), []).append(len(kept))
        kept.append(r)
    for indices in source_locations.values():
        if len(indices) < 2:
            continue
        for index in indices:
            # Query-local uncertainty must not erase the indexed candidate's
            # location: a later, more specific query may disambiguate it.
            kept[index] = replace(kept[index], bbox=None,
                                  bbox_source='ambiguous-source-location', location_score=0.0)
    return kept


def search_formulas(
    query: str,
    candidates: List[IndexedFormula],
    *,
    on_candidate_matched: Optional[Callable[[int, IndexedFormula], None]] = None,
    mode: Literal["contains", "exact", "algebraic", "fuzzy"] = "contains",
    diagnostics: Optional[dict] = None,
) -> List[MatchResult]:
    """Find formulas matching query.

    on_candidate_matched, if given, is called with (candidate_index,
    candidate) as soon as a candidate is confirmed to match -- before its
    MatchResult (and in particular _bbox_for_result's read of
    candidate.bbox/pdf_line_*) is built. This is the hook backend/main.py's
    lazy /process + /search path uses to map a candidate's PDF location on
    demand, only for the handful of candidates a query actually matches,
    instead of every candidate up front.
    """
    if mode not in {"contains", "exact", "algebraic", "fuzzy"}:
        raise ValueError("Unsupported search mode")
    query_notation = notation(query)
    if not query_notation.tokens:
        return []
    normalized_query = normalize_latex(query)
    # Algebra is deliberately opt-in. Ordinary searches never pay its parsing
    # cost or inherit assumptions such as commuting scalar multiplication.
    semantic_candidates = []
    allow_raw_fuzzy = mode == "fuzzy" and not _structured_math_query(query, normalized_query)
    results: list[MatchResult] = []

    def add_result(result: MatchResult) -> None:
        results.append(result)

    def make_and_add(candidate_index: int, candidate: IndexedFormula, **kwargs) -> None:
        if on_candidate_matched is not None:
            on_candidate_matched(candidate_index, candidate)
        add_result(_make_result(query=query, candidate=candidate, candidate_index=candidate_index, **kwargs))

    for candidate_index, candidate in enumerate(candidates):
        if candidate.normalized_latex == normalized_query:
            make_and_add(candidate_index, candidate, score=1.0, match_type="exact")
            continue

        if mode == "exact":
            continue

        parsed = candidate.parsed_notation or notation(candidate.latex)
        span = find_notation(query_notation.tokens, parsed)
        if span is not None:
            hl = highlight_notation(candidate.latex, span)
            make_and_add(
                candidate_index, candidate,
                score=0.95 if hl else 0.85,
                match_type="partial",
                highlighted_latex=hl or "",
            )
            continue

        if mode == "algebraic":
            semantic_candidates.append((candidate_index, candidate.latex))

        if allow_raw_fuzzy:
            ratio = fuzz.ratio(query, candidate.latex)
            if ratio >= FUZZY_RAW_THRESHOLD:
                make_and_add(candidate_index, candidate, score=round(ratio / 100.0, 4), match_type="fuzzy")

    if mode == "algebraic":
        indices, status = equivalent_candidates(query, semantic_candidates, SEMANTIC_TIME_BUDGET_SECONDS)
        if diagnostics is not None:
            diagnostics.update(semantic_status=status, semantic_assumptions="commuting scalar symbols")
        for candidate_index in indices:
            make_and_add(candidate_index, candidates[candidate_index], score=0.80, match_type="semantic")

    # Ctrl+F-style ordering: page order is the primary signal (earlier pages
    # first, matching how a human scanning the document top-to-bottom would
    # expect), with the previous score-based ranking demoted to a same-page
    # tiebreak. A page-less match (page == -1, an unresolved candidate) sorts
    # last, after every page-mapped result.
    results.sort(
        key=lambda r: (
            1 if r.page >= 0 else 0,
            -r.page if r.page >= 0 else -10**9,
            r.score,
            1 if r.bbox_source == "query-subspan" else 0,
            # Prefer the smallest TeX candidate that satisfies the query. This
            # keeps W^Q_i from being listed behind the much larger MultiHead
            # equation that merely contains it.
            -len(r.latex or ""),
        ),
        reverse=True,
    )
    return _dedupe_results(results)
