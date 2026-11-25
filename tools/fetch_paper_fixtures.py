"""Restore SHA-256-pinned external paper fixtures without bundling them in Git.

Downloads serially, verifies bytes before writing, never extracts or executes TeX.
Existing mismatched files are reported, never overwritten. --verify is offline.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import time
import httpx

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 64 * 1024 * 1024


def restore(verify=False):
    records = json.loads((ROOT/'testdata/paper-fixtures.json').read_text())['files']
    problems = []
    with httpx.Client(follow_redirects=True, timeout=httpx.Timeout(90, connect=20),
                      headers={'User-Agent': 'FormulaSearch-fixture-restore/0.11'}) as client:
        for item in records:
            path = (ROOT/item['path']).resolve()
            if not path.is_relative_to(ROOT/'testdata'):
                raise ValueError('Fixture path must stay inside testdata.')
            try:
                if path.exists():
                    data = path.read_bytes()
                elif verify:
                    raise FileNotFoundError('not downloaded')
                else:
                    chunks, size = [], 0
                    with client.stream('GET', item['url']) as response:
                        response.raise_for_status()
                        for chunk in response.iter_bytes():
                            size += len(chunk)
                            if size > MAX_BYTES:
                                raise ValueError('download exceeds 64 MiB')
                            chunks.append(chunk)
                    data = b''.join(chunks)
                if len(data) != item['bytes'] or hashlib.sha256(data).hexdigest() != item['sha256']:
                    raise ValueError('bytes differ from the pinned fixture; not written')
                if not path.exists():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with path.open('xb') as output:
                        output.write(data)
                    time.sleep(3)  # Respect arXiv; one request at a time.
                print('OK', item['path'], flush=True)
            except (OSError, ValueError, httpx.HTTPError) as error:
                problems.append((item['path'], str(error)))
                print('FAIL', item['path'], str(error), flush=True)
    if problems:
        raise SystemExit(f'{len(problems)} fixture(s) unavailable. No mismatched file was overwritten.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify', action='store_true', help='Check local bytes without downloading')
    restore(parser.parse_args().verify)
