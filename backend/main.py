from dataclasses import dataclass, field, replace
from contextlib import asynccontextmanager
import asyncio
import itertools
import threading
import time
import uuid
import re
from typing import Literal, Optional

import httpx as _httpx
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from backend.extractor.arxiv_tex import (
    LineIndex,
    compute_section_ordinals,
    extract_arxiv_id,
    fetch_arxiv_document,
    is_arxiv_url,
    resolve_candidate_bbox,
)
from backend.cache import load_candidates_cache, pdf_sha256, store_candidates_cache
from backend.document_identity import canonical_url, is_versioned
from backend.arxiv_versions import resolve_arxiv_id, VERSION_TTL_SECONDS
from backend.extractor.text_layer import FormulaCandidate
from backend.matcher.fuzzy import search_formulas
from backend.matcher.index import IndexedFormula, build_index
from backend.matcher.semantic import shutdown_semantic_workers
from backend.processing import PageOutOfRange, ProcessingBusy, prepare_pdf, run_document_job, run_ocr, shutdown_processing
from backend.capabilities import capabilities
from backend.ocr_runtime import shutdown_ocr
from backend.sessions import SessionStore, SessionTooLarge, StalePublication, estimate_session_bytes

@asynccontextmanager
async def lifespan(app):
    try:
        yield
    finally:
        await asyncio.to_thread(shutdown_semantic_workers)
        await asyncio.to_thread(shutdown_processing)
        await asyncio.to_thread(shutdown_ocr)


app = FastAPI(lifespan=lifespan)
_ALLOWED_ORIGIN = r'^(?:chrome-extension://[a-p]{32}|http://(?:localhost|127\.0\.0\.1)(?::[0-9]+)?)$'


@app.get('/health')
def health():
    return {'status': 'ok', 'version': '0.11.1', 'capabilities': capabilities()}

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=_ALLOWED_ORIGIN,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware('http')
async def verify_browser_origin(request, call_next):
    # CORS alone controls response access, not form POST side effects. Browser
    # callers must be extension/local UI origins before touching loaded papers.
    # Command-line/API clients without Origin remain supported.
    origin = request.headers.get('origin')
    if origin and not re.fullmatch(_ALLOWED_ORIGIN,origin):
        return JSONResponse({'detail':'This browser origin cannot access the local Formula Search backend.'},status_code=403)
    return await call_next(request)


@dataclass
class SessionState:
    """All per-document backend state that used to live in module globals.

    One instance per distinct document URL, so two papers open in two tabs
    (or two backend clients) never see or mutate each other's index. See
    _sessions below.
    """
    index_cache: list[IndexedFormula] = field(default_factory=list)
    pdf_bytes: bytes = b""
    current_pdf_url: str = ""
    source_type: str = ""  # "text" | "tex" | ""
    # Lazy bbox-mapping state for the arXiv/TeX path only (the PDF-text-layer
    # path already assigns bboxes cheaply while extracting, see
    # extract_formulas). candidates_cache mirrors index_cache 1:1 (same
    # order) but holds the original FormulaCandidate objects -- which carry
    # tex_context_before/after, needed for accurate table/float detection
    # during mapping -- instead of the IndexedFormula copies search_formulas
    # matches against.
    candidates_cache: list[FormulaCandidate] = field(default_factory=list)
    line_index: Optional[LineIndex] = None
    ordinals: list[int] = field(default_factory=list)
    generation: int = 0
    page_count: int = 0
    pdf_warning: str = ""
    resolved_arxiv_id: str = ""
    version_checked_at: float = 0.0
    ocr_generations: dict[int, int] = field(default_factory=dict)
    # In-memory identity of one shared preparation. Never persisted or sent to
    # clients; exact identity lets followers reuse the published mutable session.
    preparation_token: object = field(default=None, repr=False, compare=False)
    # Identifies the prepared source across UI requests, including backend
    # restarts. Shared preparations may reuse it because their source is equal.
    context_revision: str = field(default_factory=lambda: uuid.uuid4().hex)
    mapping_key: str = ''
    mapping_revision: int = 0
    persisted_mapping_revision: int = 0
    ocr_proposals: dict = field(default_factory=dict, repr=False)
    # Lazy mapping mutates candidate/index pairs. Search holds this lock until
    # both are synchronized; independent documents still search concurrently.
    lock: object = field(default_factory=threading.RLock, repr=False, compare=False)


_sessions = SessionStore.from_environment()
_sessions_lock = _sessions.lock
_generations = itertools.count(1)
MAX_PDF_BYTES = 64 * 1024 * 1024


def _normalize_url(url: str) -> str:
    return canonical_url(url)


def _get_session(url: str) -> SessionState:
    """For endpoints that only ever read a session: /search, /ocr-page,
    /status. Never inserts -- an unknown key returns a throwaway default
    SessionState() so callers see today's "nothing loaded" behavior without
    that key ever appearing in _sessions."""
    with _sessions_lock:
        return _sessions.get(_normalize_url(url)) or SessionState()


def _process_response(session: SessionState, *, cache_hit=False, reused_session=False, pdf_cache_hit=False,
                      shared_preparation=False):
    return {
        "formula_count": len(session.index_cache),
        "source_type": session.source_type,
        "pdf_cached": bool(session.pdf_bytes),
        "pdf_warning": session.pdf_warning,
        "cache_hit": cache_hit,
        "reused_session": reused_session,
        "pdf_cache_hit": pdf_cache_hit,
        "resolved_arxiv_id": session.resolved_arxiv_id,
        "shared_preparation": shared_preparation,
    }


def _set_lazy_bbox_state(
    session: SessionState, candidates: list[FormulaCandidate], line_index: Optional[LineIndex]
) -> None:
    session.candidates_cache = candidates
    session.line_index = line_index
    session.ordinals = compute_section_ordinals(candidates)


_RESOLVED_FIELDS = (
    "page", "bbox", "page_w", "page_h", "page_rotation", "pdf_line_text", "pdf_line_norm", "pdf_line_norm_chars",
    "pdf_line_raw_chars",
    "pdf_line_math_fraction", "pdf_line_has_math_signal", "pdf_line_group_kind", "bbox_score", "bbox_quality",
)


def _lazily_resolve_and_sync(session: SessionState, candidate_index: int, indexed: IndexedFormula) -> None:
    """search_formulas' on_candidate_matched hook for the lazy arXiv path.

    Maps the matched candidate's PDF location on demand (a no-op if it's
    already resolved -- from a warm .formula_cache write, or a previous
    search this session) and copies the result onto the IndexedFormula
    search_formulas is about to build a response from, so callers see it
    immediately and later searches for the same formula find it pre-resolved.
    """
    if session.line_index is None or candidate_index >= len(session.candidates_cache):
        return
    candidate = session.candidates_cache[candidate_index]
    old = (candidate.page, candidate.bbox, candidate.bbox_quality)
    resolve_candidate_bbox(candidate, session.ordinals[candidate_index], session.line_index)
    if old != (candidate.page, candidate.bbox, candidate.bbox_quality):
        session.mapping_revision += 1
    for field_name in _RESOLVED_FIELDS:
        setattr(indexed, field_name, getattr(candidate, field_name))


def _persist_locations(session):
    # Background work runs after the response, under the same document lock as
    # mapping. Newer snapshots cannot be overwritten by an older search.
    with session.lock:
        if not session.mapping_key or session.mapping_revision <= session.persisted_mapping_revision:
            return
        if store_candidates_cache('source-locations', session.mapping_key,
                                  (c for c in session.candidates_cache if c.bbox_quality)):
            session.persisted_mapping_revision = session.mapping_revision


@dataclass(frozen=True)
class PreparedDocument:
    session: SessionState
    cache_hit: bool = False
    pdf_cache_hit: bool = False


async def _prepare_upload(url, pdf_bytes, pdf_key):
    candidates = await asyncio.to_thread(load_candidates_cache, 'pdf-text', pdf_key)
    cache_hit = candidates is not None
    try:
        candidates, page_count = await run_document_job(prepare_pdf, pdf_bytes, candidates)
    except ValueError:
        raise HTTPException(status_code=422, detail='Invalid or unreadable PDF') from None
    if not cache_hit:
        await asyncio.to_thread(store_candidates_cache, 'pdf-text', pdf_key, candidates)
    session = SessionState(index_cache=await asyncio.to_thread(build_index, candidates),
        pdf_bytes=pdf_bytes, current_pdf_url=url, source_type='text', page_count=page_count,
        preparation_token=object())
    return PreparedDocument(session, cache_hit)


async def _prepare_source(url, arxiv_id):
    cache_key = f'arxiv:{arxiv_id}'
    candidates = await asyncio.to_thread(load_candidates_cache, 'arxiv-source', cache_key)
    cache_hit = candidates is not None
    try:
        document = await fetch_arxiv_document(arxiv_id, cached_candidates=candidates)
    except (ValueError, _httpx.HTTPError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if not cache_hit:
        await asyncio.to_thread(store_candidates_cache, 'arxiv-source', cache_key, document.candidates)
    session = SessionState(index_cache=await asyncio.to_thread(build_index, document.candidates),
        pdf_bytes=document.pdf_bytes, current_pdf_url=url, source_type='tex',
        page_count=document.page_count, pdf_warning=document.pdf_warning,
        resolved_arxiv_id=arxiv_id, version_checked_at=time.monotonic(), preparation_token=object())
    _set_lazy_bbox_state(session, document.candidates, document.line_index)
    if document.pdf_bytes:
        session.mapping_key = f'mapping-v2:{arxiv_id}:{pdf_sha256(document.pdf_bytes)}'
        saved = await asyncio.to_thread(load_candidates_cache, 'source-locations', session.mapping_key)
        if saved:
            by_anchor = {(c.source_file, c.source_line, c.source_order, c.latex): c for c in saved}
            for candidate, indexed in zip(session.candidates_cache, session.index_cache):
                previous = by_anchor.get((candidate.source_file, candidate.source_line, candidate.source_order, candidate.latex))
                if previous is not None:
                    for name in _RESOLVED_FIELDS:
                        setattr(candidate, name, getattr(previous, name))
                        setattr(indexed, name, getattr(previous, name))
    return PreparedDocument(session, cache_hit, document.pdf_cache_hit)


@app.post("/process")
async def process_pdf(
    url: str = Form(""),
    file: Optional[UploadFile] = File(None),
):
    """Prepare a complete replacement before publishing it for searches."""
    normalized_url = _normalize_url(url)
    store, generation = _sessions, None
    try:
        generation = store.begin(normalized_url)
        if file is not None:
            pdf_bytes = await file.read(MAX_PDF_BYTES + 1)
            if len(pdf_bytes) > MAX_PDF_BYTES:
                raise HTTPException(status_code=413, detail="PDF exceeds the 64 MiB processing limit.")
            session = await asyncio.to_thread(store.reuse_pdf, normalized_url, pdf_bytes, generation)
            if session is not None:
                return _process_response(session, reused_session=True)
            pdf_key = f"pdf:{await asyncio.to_thread(pdf_sha256, pdf_bytes)}"
            identity = ('pdf', normalized_url, pdf_key)
            factory = lambda: _prepare_upload(normalized_url, pdf_bytes, pdf_key)
        elif is_arxiv_url(normalized_url):
            try:
                arxiv_id = await resolve_arxiv_id(extract_arxiv_id(normalized_url))
            except (ValueError, _httpx.HTTPError) as exc:
                raise HTTPException(status_code=502, detail=str(exc)) from exc
            session = await asyncio.to_thread(store.reuse_version, normalized_url, arxiv_id, generation, time.monotonic())
            if session is not None:
                return _process_response(session, reused_session=True)
            # Share only one explicit version under the same document URL.
            identity = ('tex', normalized_url, arxiv_id)
            factory = lambda: _prepare_source(normalized_url, arxiv_id)
        else:
            raise HTTPException(status_code=400, detail="Provide an arXiv URL or a PDF file")
        async with store.preparations.acquire(identity, factory) as (prepared, shared):
            replacement = replace(prepared.session, generation=generation)
            session = await asyncio.to_thread(store.publish, normalized_url, replacement)
            return _process_response(session, cache_hit=prepared.cache_hit, pdf_cache_hit=prepared.pdf_cache_hit,
                                     shared_preparation=shared, reused_session=session is not replacement)
    except ProcessingBusy as exc:
        raise HTTPException(status_code=503, detail=str(exc), headers={"Retry-After": "2"}) from exc
    except StalePublication as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SessionTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    finally:
        if generation is not None:
            store.finish(normalized_url, generation)


@app.get("/status")
def status(url: str = ""):
    session = _get_session(url)
    stale = bool(session.resolved_arxiv_id and not is_versioned(extract_arxiv_id(session.current_pdf_url))
                 and time.monotonic() - session.version_checked_at >= VERSION_TTL_SECONDS)
    return {
        "loaded": session.generation > 0 and not stale,
        "formula_count": len(session.index_cache),
        "url": session.current_pdf_url,
        "source_type": session.source_type,
        "pdf_cached": bool(session.pdf_bytes),
        "pdf_warning": session.pdf_warning,
        "version_check_due": stale,
        "resolved_arxiv_id": session.resolved_arxiv_id,
    }


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2048)
    url: str
    mode: Literal["contains", "exact", "algebraic", "fuzzy"] = "contains"


def _match_location_status(match):
    if match.bbox_source == 'ambiguous-source-location':
        return 'ambiguous'
    if match.bbox is not None:
        return 'located'
    if match.location_method == 'section-estimate':
        return 'estimated'
    return 'page-only' if match.page >= 0 else 'unlocated'


@app.post("/search")
def search(req: SearchRequest, background_tasks: BackgroundTasks = None):
    started = time.perf_counter()
    session = _get_session(req.url)
    # OCR replaces the list; readers keep a stable reference to the preceding
    # complete list. Optional algebraic work must not hold the document lock.
    with session.lock:
        index = session.index_cache

    def _on_matched(candidate_index: int, indexed: IndexedFormula) -> None:
        with session.lock:
            _lazily_resolve_and_sync(session, candidate_index, indexed)

    diagnostics = {}
    matches = search_formulas(req.query, index, on_candidate_matched=_on_matched, mode=req.mode, diagnostics=diagnostics)
    if background_tasks is not None and session.mapping_revision > session.persisted_mapping_revision:
        background_tasks.add_task(_persist_locations, session)
    return {
        "mode": req.mode,
        "loaded": session.generation > 0,
        "resolved_source_url": f"https://arxiv.org/pdf/{session.resolved_arxiv_id}" if session.resolved_arxiv_id else session.current_pdf_url,
        "search_ms": round((time.perf_counter() - started) * 1000, 2),
        "source_context_revision": session.context_revision
            if session.generation > 0 and session.source_type == 'tex' and session.candidates_cache else '',
        **diagnostics,
        "matches": [
            {
                "score": m.score,
                "match_type": m.match_type,
                "latex": m.latex,
                "highlighted_latex": m.highlighted_latex,
                "label": m.label,
                "section": m.section,
                "source": m.source,
                "source_file": m.source_file,
                "source_line": m.source_line,
                "source_location_kind": m.source_location_kind,
                "occurrence_id": m.occurrence_id,
                "occurrence_anchor": m.occurrence_anchor,
                "mapping_revision": 'source-mapping2' if m.source == 'tex' else '',
                "page": m.page,
                "bbox": list(m.bbox) if m.bbox is not None else None,
                "bbox_source": getattr(m, "bbox_source", "candidate"),
                "location_status": _match_location_status(m),
                "location_method": m.location_method,
                "location_score": round(m.location_score, 4),
                "page_w": float(m.page_w),
                "page_h": float(m.page_h),
            }
            for m in matches
        ]
    }


class SourceContextRequest(BaseModel):
    url: str
    occurrence_id: str = Field(min_length=69, max_length=69, pattern=r'^occ1:[a-f0-9]{64}$')
    context_revision: str = Field(min_length=32, max_length=32, pattern=r'^[a-f0-9]{32}$')


@app.post("/source-context")
def source_context(req: SourceContextRequest):
    """Return retained source prose on demand, without PDF mapping or parsing."""
    session = _get_session(req.url)
    with session.lock:
        if not session.generation or req.context_revision != session.context_revision:
            raise HTTPException(status_code=409, detail="The paper changed or is no longer loaded. Search again to refresh its source context.")
        for number, indexed in enumerate(session.index_cache):
            if indexed.occurrence_id != req.occurrence_id or indexed.source != 'tex':
                continue
            if number >= len(session.candidates_cache):
                break
            candidate = session.candidates_cache[number]
            # Source records stay in their original order; OCR entries append
            # after them. Refuse mismatched state instead of exposing an excerpt
            # from an adjacent occurrence if that invariant ever changes.
            fields = ('source', 'latex', 'source_file', 'source_line', 'source_location_kind', 'source_order')
            if any(getattr(candidate, key) != getattr(indexed, key) for key in fields):
                raise HTTPException(status_code=409, detail="The formula list changed. Search again to refresh its source context.")
            return {
                'kind': 'tex', 'occurrence_id': indexed.occurrence_id,
                'context_revision': session.context_revision,
                'before': candidate.tex_context_before[-420:],
                'after': candidate.tex_context_after[:420],
            }
    raise HTTPException(status_code=404, detail="Source context is unavailable for this formula.")


class OcrRequest(BaseModel):
    page: int
    url: str
    region: Optional[tuple[float, float, float, float]] = None
    context_revision: str = ''
    preview_only: bool = False


class OcrSaveRequest(BaseModel):
    url: str
    proposal_id: str = Field(min_length=32, max_length=32)
    latex: str = Field(min_length=1, max_length=20000)


def _merge_recognition(session, url, page, region, generation, new_candidates):
    additions = build_index(new_candidates)
    with session.lock, _sessions_lock:
        if _sessions.get(_normalize_url(url)) is not session:
            raise HTTPException(status_code=409, detail='Document changed while OCR was running. Retry on the current document.')
        region_key = (page, region) if region else page
        if session.ocr_generations.get(region_key, 0) > generation:
            raise HTTPException(status_code=409, detail='A newer OCR result is already available for this region.')
        merged = [c for c in session.index_cache
                  if not (c.page == page and c.source == 'ocr' and (region is None or c.bbox == region))] + additions
        weight = estimate_session_bytes(replace(session, index_cache=merged))
        _sessions.reweigh(_normalize_url(url), session, weight)
        session.index_cache = merged
        session.ocr_generations[region_key] = generation


@app.post('/ocr-save')
def ocr_save(req: OcrSaveRequest):
    session = _get_session(req.url)
    with session.lock:
        proposal = session.ocr_proposals.get(req.proposal_id)
        if not proposal or time.monotonic() - proposal[0] > 600:
            raise HTTPException(status_code=409, detail='The equation preview expired or the paper changed. Select the equation again.')
        _, page, region, generation, candidate = proposal
        if not req.latex.strip():
            raise HTTPException(status_code=422, detail='Enter the recognized equation before saving.')
        try:
            _merge_recognition(session, req.url, page, region, generation, [replace(candidate, latex=req.latex.strip())])
        except (SessionTooLarge, StalePublication) as exc:
            raise HTTPException(status_code=413 if isinstance(exc, SessionTooLarge) else 409, detail=str(exc)) from exc
        session.ocr_proposals.pop(req.proposal_id, None)
    return {'formulas_found': 1}


@app.post('/ocr-preview')
async def ocr_preview(req: OcrRequest):
    from backend.extractor.ocr_page import page_preview
    session = _get_session(req.url)
    if not session.pdf_bytes:
        raise HTTPException(status_code=400, detail='PDF unavailable. Reload the paper before selecting an equation.')
    try:
        result = await run_document_job(page_preview, session.pdf_bytes, req.page)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ProcessingBusy as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    available = await asyncio.to_thread(capabilities)
    return {**result, 'context_revision': session.context_revision, 'ocr': available['ocr']}


@app.post('/formula-preview')
async def pdf_formula_preview(req: OcrRequest):
    from backend.extractor.ocr_page import formula_preview
    session = _get_session(req.url)
    if not session.pdf_bytes:
        raise HTTPException(status_code=400,detail='PDF unavailable. Reload the paper before opening its excerpt.')
    try:
        return await run_document_job(formula_preview,session.pdf_bytes,req.page,req.region)
    except ValueError as exc:
        raise HTTPException(status_code=400,detail=str(exc)) from exc
    except ProcessingBusy as exc:
        raise HTTPException(status_code=503,detail=str(exc)) from exc


@app.post("/ocr-page")
async def ocr_page_endpoint(req: OcrRequest):
    session = await asyncio.to_thread(_get_session, req.url)
    if not session.pdf_bytes:
        raise HTTPException(
            status_code=400,
            detail="No PDF bytes available. Open the PDF in Chrome and process it from the popup, or upload/process a PDF first.",
        )
    if req.context_revision and req.context_revision != session.context_revision:
        raise HTTPException(status_code=409, detail='The paper changed. Select the equation again.')
    with _sessions_lock:
        generation = next(_generations)
    try:
        new_candidates = await run_ocr(session.pdf_bytes, req.page, req.region) if req.region is not None else await run_ocr(session.pdf_bytes, req.page)
    except PageOutOfRange as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (ImportError, ProcessingBusy) as exc:
        detail = str(exc) if isinstance(exc, ProcessingBusy) else "Optional OCR dependencies are not installed."
        raise HTTPException(status_code=503, detail=detail) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if req.preview_only and new_candidates:
        with session.lock:
            if _get_session(req.url) is not session:
                raise HTTPException(status_code=409, detail='The paper changed. Select the equation again.')
            session.ocr_proposals = {key:value for key,value in session.ocr_proposals.items()
                                     if time.monotonic() - value[0] <= 600}
            while len(session.ocr_proposals) >= 10:
                session.ocr_proposals.pop(next(iter(session.ocr_proposals)))
            proposal_id = uuid.uuid4().hex
            session.ocr_proposals[proposal_id] = (time.monotonic(), req.page, req.region, generation, new_candidates[0])
        return {'formulas_found':1, 'proposal_id':proposal_id, 'latex':new_candidates[0].latex,
                'requires_review':True}
    # OCR may finish after another /process. Never apply its coordinates or
    # formulas to a replacement document, even when the URL stayed the same.
    try:
        await asyncio.to_thread(_merge_recognition, session, req.url, req.page, req.region, generation, new_candidates)
    except SessionTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except StalePublication as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"formulas_found": len(new_candidates)}
