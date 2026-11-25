from __future__ import annotations

import gzip
import hashlib
import json
import os
import tempfile
import time
import threading
from pathlib import Path
from typing import Iterable, Optional

from backend.extractor.text_layer import FormulaCandidate

# Version 12 excludes known inactive etoolbox source branches.
# Old files stay on disk; PDF extraction has an independent version.
CACHE_VERSION = 12
# PDF extraction evolves independently of source parsing. Do not invalidate
# warm TeX source caches for a change affecting uploaded PDFs alone.
PDF_TEXT_CACHE_VERSION = 20
CACHE_DIR = Path(os.environ.get(
    "FORMULA_SEARCH_CACHE_DIR",
    str(Path(__file__).resolve().parents[1] / ".formula_cache"),
))
MAX_CACHE_BYTES = 512 * 1024 * 1024
MAX_CACHE_ENTRIES = 128
MAX_ENTRY_BYTES = 16 * 1024 * 1024
MAX_DECODED_BYTES = 64 * 1024 * 1024
MAX_CACHE_AGE = 30 * 86400
_cache_lock = threading.RLock()

_FORMULA_FIELDS = [
    "latex", "source", "label", "section", "env", "page", "bbox", "page_w", "page_h", "page_rotation",
    "pdf_line_text", "pdf_line_norm", "pdf_line_norm_chars", "pdf_line_math_fraction",
    "pdf_line_raw_chars", "pdf_latex_chars",
    "pdf_line_has_math_signal", "pdf_line_group_kind", "bbox_score", "bbox_quality",
    "tex_context_before", "tex_context_after", "source_order",
    "source_file", "source_line", "source_location_kind",
]


def _cache_path(kind: str, key: str) -> Path:
    safe_kind = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in kind)[:40] or "cache"
    digest = hashlib.sha256(key.encode("utf-8", errors="replace")).hexdigest()[:32]
    return CACHE_DIR / 'candidates-v1' / f"{safe_kind}-{digest}.json.gz"


def _prune_candidates(protected=None):
    """Prune only our owned namespace; leave legacy/user cache files alone."""
    paths = sorted(_cache_path('', '').parent.glob('*.json.gz'), key=lambda p: (p == protected, p.stat().st_mtime))
    total = sum(p.stat().st_size for p in paths)
    for number, path in enumerate(paths):
        if (len(paths) - number > MAX_CACHE_ENTRIES or total > MAX_CACHE_BYTES
                or time.time() - path.stat().st_mtime > MAX_CACHE_AGE):
            size = path.stat().st_size
            path.unlink(missing_ok=True)
            total -= size


def pdf_sha256(pdf_bytes: bytes) -> str:
    return hashlib.sha256(pdf_bytes).hexdigest()


def _bbox_to_list(bbox):
    return [float(v) for v in bbox] if bbox is not None else None


def _norm_chars_to_json(norm_chars):
    if not norm_chars:
        return None
    return [[ch, _bbox_to_list(box)] for ch, box in norm_chars]


def _norm_chars_from_json(value):
    if not value:
        return None
    out = []
    for item in value:
        try:
            ch, box = item
            if box is None:
                continue
            out.append((str(ch), tuple(float(v) for v in box)))
        except Exception:
            continue
    return out


def candidate_to_dict(candidate: FormulaCandidate) -> dict:
    data = {}
    for field in _FORMULA_FIELDS:
        value = getattr(candidate, field, None)
        if field == "bbox":
            value = _bbox_to_list(value)
        elif field in {"pdf_line_norm_chars", "pdf_line_raw_chars", "pdf_latex_chars"}:
            value = _norm_chars_to_json(value)
        data[field] = value
    return data


def candidate_from_dict(data: dict) -> FormulaCandidate:
    c = FormulaCandidate(
        latex=str(data.get("latex") or ""),
        source=str(data.get("source") or "tex"),
        label=str(data.get("label") or ""),
        section=str(data.get("section") or ""),
        env=str(data.get("env") or ""),
        page=int(data.get("page", -1) if data.get("page") is not None else -1),
        bbox=tuple(float(v) for v in data["bbox"]) if data.get("bbox") else None,
        page_w=float(data.get("page_w") or 0.0),
        page_h=float(data.get("page_h") or 0.0),
        page_rotation=int(data.get("page_rotation") or 0),
        pdf_line_text=str(data.get("pdf_line_text") or ""),
        pdf_line_norm=str(data.get("pdf_line_norm") or ""),
        pdf_line_norm_chars=_norm_chars_from_json(data.get("pdf_line_norm_chars")),
        pdf_line_raw_chars=_norm_chars_from_json(data.get("pdf_line_raw_chars")),
        pdf_latex_chars=_norm_chars_from_json(data.get("pdf_latex_chars")),
        pdf_line_math_fraction=float(data.get("pdf_line_math_fraction") or 0.0),
        pdf_line_has_math_signal=bool(data.get("pdf_line_has_math_signal") or False),
        pdf_line_group_kind=str(data.get("pdf_line_group_kind") or ""),
        bbox_score=float(data.get("bbox_score") or 0.0),
        bbox_quality=str(data.get("bbox_quality") or ""),
        tex_context_before=str(data.get("tex_context_before") or ""),
        tex_context_after=str(data.get("tex_context_after") or ""),
        source_order=int(data.get("source_order", -1) if data.get("source_order") is not None else -1),
        source_file=str(data.get('source_file') or ''),
        source_line=int(data.get('source_line') or 0),
        source_location_kind=str(data.get('source_location_kind') or ''),
    )
    return c


def load_candidates_cache(kind: str, key: str) -> Optional[list[FormulaCandidate]]:
    path = _cache_path(kind, key)
    if not path.exists():
        # One-way compatibility read. New writes never prune legacy files.
        path = CACHE_DIR / path.name
        if not path.exists():
            return None
    try:
        if path.stat().st_size > MAX_ENTRY_BYTES or time.time() - path.stat().st_mtime > MAX_CACHE_AGE:
            return None
        with gzip.open(path, 'rb') as fh:
            decoded = fh.read(MAX_DECODED_BYTES + 1)
        if len(decoded) > MAX_DECODED_BYTES:
            return None
        payload = json.loads(decoded)
        version = PDF_TEXT_CACHE_VERSION if kind == 'pdf-text' else CACHE_VERSION
        if (payload.get("version") != version or payload.get("kind") != kind
                or payload.get("key_hash") != hashlib.sha256(key.encode("utf-8", errors="replace")).hexdigest()):
            return None
        result = [candidate_from_dict(item) for item in payload.get("candidates", [])]
        path.touch()
        return result
    except Exception:
        return None


def store_candidates_cache(kind: str, key: str, candidates: Iterable[FormulaCandidate]) -> bool:
    tmp = None
    try:
        path = _cache_path(kind, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Different requests/processes may store the same document at once.
        # Each gets its own temporary file; readers see only a complete replace.
        fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
        os.close(fd)
        tmp = Path(temp_name)
        payload = {
            "version": PDF_TEXT_CACHE_VERSION if kind == 'pdf-text' else CACHE_VERSION,
            "kind": kind,
            "key_hash": hashlib.sha256(key.encode("utf-8", errors="replace")).hexdigest(),
            "candidates": [candidate_to_dict(c) for c in candidates],
        }
        decoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode('utf-8')
        if len(decoded) > MAX_DECODED_BYTES:
            return False
        with gzip.open(tmp, 'wb') as fh:
            fh.write(decoded)
        if tmp.stat().st_size > MAX_ENTRY_BYTES:
            return False
        with _cache_lock:
            tmp.replace(path)
            _prune_candidates(protected=path)
        return path.exists()
    except Exception:
        # Cache failures must never break indexing.
        return False
    finally:
        if tmp is not None:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
