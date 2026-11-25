"""Bounded process isolation for PDF and other expensive document work.

PyMuPDF explicitly does not support concurrent threads. A persistent spawn pool
keeps all production PDF operations out of both the API loop and its threads.
Cancellation stops waiting, not an already running job; capacity is released
only when that job actually finishes.
"""
from __future__ import annotations

import asyncio
import atexit
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
import multiprocessing
import os
import threading
from typing import Callable, TypeVar

T = TypeVar("T")
_lock = threading.Lock()
_pool: ProcessPoolExecutor | None = None
_pending = 0
MAX_PENDING = 6
DOCUMENT_TIMEOUT_SECONDS = float(os.environ.get('FORMULA_SEARCH_DOCUMENT_TIMEOUT', '120'))
if not 0 < DOCUMENT_TIMEOUT_SECONDS <= 600:
    raise ValueError('FORMULA_SEARCH_DOCUMENT_TIMEOUT must be between 0 and 600 seconds.')


class ProcessingBusy(RuntimeError):
    pass


class ProcessingTimeout(ProcessingBusy):
    pass


def shutdown_processing() -> None:
    global _pool
    with _lock:
        pool, _pool = _pool, None
    if pool is not None:
        pool.kill_workers()


atexit.register(shutdown_processing)


async def run_document_job(function: Callable[..., T], *args) -> T:
    global _pool, _pending
    with _lock:
        if _pending >= MAX_PENDING:
            raise ProcessingBusy("Document processing is busy. Please retry shortly.")
        if _pool is None:
            _pool = ProcessPoolExecutor(max_workers=2, mp_context=multiprocessing.get_context("spawn"))
        pool = _pool
        _pending += 1

    def retire():
        global _pool
        with _lock:
            if _pool is pool:
                _pool = None
        # Completion callbacks run inside executor management. In particular,
        # a broken pool sets exceptions while holding its shutdown lock. Never
        # re-enter shutdown there, or hold our own lock while entering submit.
        threading.Thread(target=pool.shutdown, kwargs={"wait": False, "cancel_futures": True}, daemon=True).start()

    timed_out = threading.Event()

    def expire():
        global _pool
        with _lock:
            if future.done() or _pool is not pool:
                return
            timed_out.set()
            _pool = None
        # Kill outside the admission lock: completion callbacks also use it.
        # All peers in this pool receive retryable failures; future requests get
        # a fresh pool. The watchdog outlives a cancelled HTTP caller.
        pool.kill_workers()

    try:
        future = pool.submit(function, *args)
    except Exception as exc:
        with _lock:
            _pending -= 1
        if isinstance(exc, BrokenProcessPool):
            retire()
            raise ProcessingBusy("Document worker restarted. Please retry.") from None
        raise

    watchdog = threading.Timer(DOCUMENT_TIMEOUT_SECONDS, expire)
    watchdog.daemon = True

    def finished(completed):
        global _pending
        watchdog.cancel()
        with _lock:
            _pending -= 1
            broken = not completed.cancelled() and isinstance(completed.exception(), BrokenProcessPool)
        if broken:
            retire()

    future.add_done_callback(finished)
    watchdog.start()
    # wrap_future consumes child exceptions even if the HTTP request is cancelled.
    try:
        return await asyncio.wrap_future(future)
    except BrokenProcessPool:
        if timed_out.is_set():
            raise ProcessingTimeout('Document processing exceeded its time limit. Workers restarted; retry or use a smaller document.') from None
        raise ProcessingBusy("Document worker restarted. Please retry.") from None


def validate_pdf(pdf_bytes: bytes) -> int:
    import fitz
    try:
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            if not doc.is_pdf or doc.needs_pass or len(doc) == 0:
                raise ValueError("Invalid, encrypted, or empty PDF")
            return len(doc)
    except Exception as exc:
        raise ValueError("Invalid or unreadable PDF") from exc


def prepare_pdf(pdf_bytes: bytes, cached_candidates=None):
    from backend.extractor.text_layer import extract_formulas
    page_count = validate_pdf(pdf_bytes)
    candidates = cached_candidates if cached_candidates is not None else extract_formulas(pdf_bytes)
    return candidates, page_count


class PageOutOfRange(ValueError):
    pass


def recognize_page(pdf_bytes: bytes, page: int, region=None):
    page_count = validate_pdf(pdf_bytes)
    if page < 0 or page >= page_count:
        raise PageOutOfRange(f"Page {page} out of range (0-{page_count - 1}).")
    from backend.extractor.ocr_page import ocr_page
    return ocr_page(pdf_bytes, page, region)


async def run_ocr(pdf_bytes: bytes, page: int, region=None):
    from backend.extractor.ocr_page import equation_crop
    from backend.ocr_runtime import recognize
    from backend.extractor.text_layer import FormulaCandidate
    # Model inference never occupies a general document worker.
    crop = await run_document_job(equation_crop, pdf_bytes, page, region)
    latex = await asyncio.to_thread(recognize, crop['image'])
    return [FormulaCandidate(latex, 'ocr', page=page, bbox=crop['bbox'],
                             page_w=crop['page_w'], page_h=crop['page_h'],
                             bbox_quality='ocr-region')] if latex else []
