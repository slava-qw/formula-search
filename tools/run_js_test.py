#!/usr/bin/env python3
"""Runs a JS test script against extracted extension source in a bare V8
context (py_mini_racer -- no real browser/DOM), using tools/js_fake_dom.js
for the minimal document/window/chrome shim this codebase's info-card and
chat code actually needs.

Extension code isn't written as modules (content.js/popup.js's injected
function both run as plain browser scripts), so this loads plain source
text into one persistent MiniRacer context, in order:
  1. the DOM/chrome shim
  2. every --whole file, in the order given
  3. every --block extract, in the order given
  4. the test script

A --block is a named region of a file delimited by two marker comments
(e.g. the chat-store functions inside popup.js's much bigger injected
function), so a test can load just that region without dragging in
unrelated top-level code that assumes a real popup.html DOM.

The test script collects results into globalThis.__results, one string per
assertion, each starting with "OK: " or "FAIL: ".

Example:
  python tools/run_js_test.py tests_js/smoke_dom.js
  python tools/run_js_test.py tests_js/chat_store_content.js --block "extension/content.js:=== CHAT STORE (content.js) BEGIN ===:=== CHAT STORE (content.js) END ==="
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from py_mini_racer import MiniRacer

REPO_ROOT = Path(__file__).resolve().parent.parent


def extract_block(path: Path, begin_marker: str, end_marker: str) -> str:
    lines = path.read_text(encoding="utf-8").splitlines()
    begin_i = next((i for i, l in enumerate(lines) if begin_marker in l), None)
    if begin_i is None:
        raise SystemExit(f"begin marker {begin_marker!r} not found in {path}")
    end_i = next((i for i, l in enumerate(lines) if end_marker in l and i > begin_i), None)
    if end_i is None:
        raise SystemExit(f"end marker {end_marker!r} not found in {path} after line {begin_i + 1}")
    return "\n".join(lines[begin_i + 1:end_i])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("test_script", type=Path, help="JS test file to run last")
    parser.add_argument("--dom", type=Path, default=REPO_ROOT / "tools" / "js_fake_dom.js", help="Fake DOM/chrome shim to load first")
    parser.add_argument("--whole", action="append", default=[], type=Path, help="Load this file's entire contents verbatim (repeatable); all --whole files load before any --block")
    parser.add_argument("--block", action="append", default=[], metavar="FILE:BEGIN:END", help="Load only the lines between two marker comments in FILE, relative to repo root (repeatable)")
    args = parser.parse_args()

    mr = MiniRacer()
    mr.eval(args.dom.read_text(encoding="utf-8"))

    for path in args.whole:
        mr.eval(path.read_text(encoding="utf-8"))

    for spec in args.block:
        file_part, begin, end = spec.split(":", 2)
        code = extract_block(REPO_ROOT / file_part, begin, end)
        mr.eval(code)

    mr.eval(args.test_script.read_text(encoding="utf-8"))
    # Each additional eval() flushes one more layer of the microtask queue,
    # letting chained .then() calls (chrome.storage.local's fake get/set
    # both return real Promises) resolve before results are read back.
    for _ in range(20):
        mr.eval("0")

    results = json.loads(mr.eval("JSON.stringify(globalThis.__results || [])"))
    if not results:
        print("NO RESULTS -- test script did not populate globalThis.__results")
        return 1
    failed = 0
    for line in results:
        print(line)
        if line.startswith("FAIL"):
            failed += 1
    print(f"\n{len(results) - failed}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
