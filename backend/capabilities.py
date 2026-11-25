"""Cheap capability discovery. Never import the model or download weights here."""
from importlib.util import find_spec
from functools import lru_cache
import subprocess
import sys
from backend.ocr_runtime import interpreter


@lru_cache(maxsize=4)
def _external_installed(path):
    try:
        result = subprocess.run([path, '-c', "import importlib.util; print(int(importlib.util.find_spec('pix2tex') is not None))"],
                                capture_output=True, text=True, timeout=5,
                                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        return result.returncode == 0 and result.stdout.strip() == '1'
    except (OSError, subprocess.TimeoutExpired):
        return False


def capabilities():
    external = interpreter()
    local_package = external.parent.parent/'Lib/site-packages/pix2tex/cli.py'
    available = local_package.is_file() or (str(external) == sys.executable and find_spec('pix2tex') is not None)
    if not available and str(external) != sys.executable and external.is_file():
        available = _external_installed(str(external))
    return {'python': sys.version.split()[0], 'worker_deadlines': sys.version_info >= (3, 14),
            'ocr': {'installed': available, 'input': 'equation-region',
                    'message': 'Model loads on first recognition; weights may need downloading.' if available
                    else 'Optional OCR is unavailable. Follow the OCR setup in README.md and restart the backend.'}}
