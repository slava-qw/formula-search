"""Reuse the tested kill/reap transport with a separate optional interpreter."""
import atexit
import os
from pathlib import Path
import sys
import threading

from backend.matcher.worker_pool import ScalarWorkerPool

ROOT = Path(__file__).resolve().parents[1]
_pool = None
_lock = threading.Lock()


def interpreter():
    explicit = os.environ.get('FORMULA_SEARCH_OCR_PYTHON')
    if explicit:
        return Path(explicit)
    local = ROOT / '.venv-ocr' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    return local if local.is_file() else Path(sys.executable)


class OcrPool(ScalarWorkerPool):
    @staticmethod
    def _validate(response, request_id, payload):
        if not isinstance(response, dict) or response.get('id') != request_id:
            raise ValueError('Mismatched OCR response')
        result = response.get('result')
        if (not isinstance(result, dict) or result.get('status') not in {'complete', 'unsupported'}
                or not isinstance(result.get('latex'), str) or len(result['latex']) > 20000):
            raise ValueError('Invalid OCR response')
        return result


def recognize(image):
    global _pool
    with _lock:
        if _pool is None:
            _pool = OcrPool([str(interpreter()), '-u', str(ROOT/'backend/ocr_worker.py')], ROOT,
                            max_workers=1, max_requests=128, idle_seconds=120)
        pool = _pool
    result = pool.request({'image': image}, timeout=120)
    if result['status'] != 'complete':
        raise RuntimeError('Equation OCR could not finish: ' + result.get('error', result['status']) +
                           '. Check the optional OCR environment, then retry.')
    return result['latex']


def shutdown_ocr():
    global _pool
    with _lock:
        pool, _pool = _pool, None
    if pool:
        pool.close()


atexit.register(shutdown_ocr)
