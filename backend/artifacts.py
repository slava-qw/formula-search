"""Best-effort content-addressed PDF cache, separate from legacy formula caches.

Only this module's versioned subdirectory is pruned. Reads verify both version
identity and content hash. Interrupted writes and missing files are cache misses.
"""
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time

from backend import cache
from backend.document_identity import arxiv_id, is_versioned

MAX_BYTES = 512 * 1024 * 1024
MAX_ENTRIES = 128
MAX_AGE_SECONDS = 30 * 24 * 3600
_lock = threading.RLock()
_HEX = re.compile(r'[0-9a-f]{64}')


def _root():
    return cache.CACHE_DIR / 'pdf-artifacts-v1'


def _reference(root: Path, identifier: str):
    return root / (hashlib.sha256(identifier.encode()).hexdigest() + '.json')


def _metadata(path):
    with path.open('rb') as stream:
        data = json.loads(stream.read(4097))
    if (not isinstance(data, dict) or data.get('schema') != 1 or not _HEX.fullmatch(data.get('sha256', ''))
            or type(data.get('bytes')) is not int or data['bytes'] < 1):
        raise ValueError('Invalid PDF cache metadata')
    return data


def _atomic_write(path: Path, content: bytes):
    fd, name = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def load_pdf_artifact(identifier: str):
    if not is_versioned(identifier):
        return None
    with _lock:
        try:
            root = _root()
            reference = _reference(root, identifier)
            data = _metadata(reference)
            if data['arxiv_id'] != identifier or time.time() - reference.stat().st_mtime > MAX_AGE_SECONDS:
                return None
            path = root / (data['sha256'] + '.pdf')
            if data['bytes'] > MAX_BYTES or path.stat().st_size != data['bytes']:
                return None
            content = path.read_bytes()
            if hashlib.sha256(content).hexdigest() != data['sha256']:
                return None
            reference.touch()
            return content
        except (OSError, ValueError, TypeError, KeyError):
            return None


def store_pdf_artifact(identifier: str, content: bytes):
    if not is_versioned(identifier) or arxiv_id(f'https://arxiv.org/pdf/{identifier}') != identifier:
        return
    if not content or len(content) > MAX_BYTES:
        return
    with _lock:
        try:
            root = _root()
            root.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256(content).hexdigest()
            _atomic_write(root / (digest + '.pdf'), content)
            _atomic_write(_reference(root, identifier), json.dumps({
                'schema': 1, 'arxiv_id': identifier, 'sha256': digest, 'bytes': len(content),
            }).encode())
            _prune(root)
        except (OSError, ValueError, TypeError):
            # Caching is optional; successful processing must still be usable.
            pass


def _prune(root: Path):
    references = []
    for path in root.glob('*.json'):
        if not _HEX.fullmatch(path.stem):
            continue
        try:
            data = _metadata(path)
            accessed = path.stat().st_mtime
            if time.time() - accessed > MAX_AGE_SECONDS:
                path.unlink(missing_ok=True)
            else:
                references.append((accessed, path, data['sha256']))
        except (ValueError, KeyError, TypeError):
            path.unlink(missing_ok=True)
    references.sort(key=lambda row: row[0])
    counts = Counter(row[2] for row in references)
    objects = {path.stem: path for path in root.glob('*.pdf') if _HEX.fullmatch(path.stem)}
    sizes = {digest: path.stat().st_size for digest, path in objects.items()}
    total = sum(size for digest, size in sizes.items() if counts[digest])
    while references and (len(references) > MAX_ENTRIES or total > MAX_BYTES):
        _, path, digest = references.pop(0)
        path.unlink(missing_ok=True)
        counts[digest] -= 1
        if counts[digest] == 0:
            total -= sizes.get(digest, 0)
    for digest, path in objects.items():
        if not counts[digest]:
            path.unlink(missing_ok=True)
