"""The JS harness emits UTF-8 for the extension checker's captured-output protocol."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('console_encoding', ['cp1252', 'utf-8'])
def test_js_results_are_utf8_under_windows_console_encodings(tmp_path, console_encoding):
    script = tmp_path/'unicode-results.js'
    message = 'OK: controls ✕ ⧉ and notation α²'
    script.write_text(f'globalThis.__results = [{message!r}];', encoding='utf-8')
    result = subprocess.run(
        [sys.executable, str(ROOT/'tools/run_js_test.py'), str(script)],
        cwd=ROOT, env={**os.environ, 'PYTHONIOENCODING': console_encoding},
        capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr.decode('utf-8', errors='replace')
    output = result.stdout.decode('utf-8')
    assert message in output
    assert '1/1 passed' in output
