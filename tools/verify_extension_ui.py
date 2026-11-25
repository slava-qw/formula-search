#!/usr/bin/env python3
"""Visually verify the extension's injected highlight/info-card/chat UI in real Chrome.

Launches Playwright's bundled Chromium (branded Google Chrome silently ignores
--load-extension on the stable channel; Chromium still honors it) with the
unpacked extension loaded, opens a local PDF, and drives the exact same
extension code real users hit -- popup.js's matchMessage() for the payload
shape and its executeNativeJumpViaScripting/ContentBridge/Debugger transport
chain for delivery -- to open the info card on a real formula match pulled
from the locally running backend. It then drives real mouse/keyboard input
(positioned using exact DOM rects read back over chrome.debugger, since
Chrome's native PDF viewer renders in a guest view that neither
chrome.scripting nor Playwright's normal frame tree can see into) to exercise
drag, resize, chat, and the off-screen hide/show cycle, saving a screenshot
after each step.

Why chrome.debugger for coordinate readback: confirmed empirically that
document.querySelector('pdf-viewer') finds nothing via page.evaluate() on the
PDF tab (the guest view's DOM is invisible to it) even though it's visibly
rendering -- exactly why the extension itself has a chrome.debugger fallback
transport. This script reuses that same attach/Runtime.evaluate mechanism.

Prerequisites (one-time):
  pip install -r requirements-dev.txt
  python -m playwright install chromium

The backend (backend/main.py via uvicorn) must already be running and have
the target PDF processed -- pass --process to have this script upload it for
you first.

Examples:
  python -m uvicorn backend.main:app --port 7878 &

  # arXiv paper: --url hits the TeX-source path (far richer formula
  # coverage than a plain PDF upload's text-layer extraction), matching
  # what the real extension does for an arXiv tab. Needs network access
  # unless this paper is already cached under .formula_cache/.
  python tools/verify_extension_ui.py --pdf testdata/papers/2506.01320.pdf \\
      --query '\\psi^*' --url https://arxiv.org/abs/2506.01320

  # Local/offline PDF, text-layer path:
  python tools/verify_extension_ui.py --pdf testdata/papers/1706.03762v7.pdf \\
      --query 'positional encoding' --process

  # Quick sanity check, no drag/resize/chat/scroll steps, browser left open
  # for manual poking:
  python tools/verify_extension_ui.py --pdf testdata/papers/1706.03762v7.pdf \\
      --query 'positional encoding' --process --skip-interactions --keep-open
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import fitz
import httpx
from playwright.sync_api import Page, sync_playwright

REPO_ROOT = Path(__file__).resolve().parent.parent
EXTENSION_DIR = REPO_ROOT / "extension"

# Reads element positions out of the PDF guest view by attaching a real
# chrome.debugger session and auto-attaching to its iframe-typed sub-target
# -- the same mechanism extension/popup/popup.js's executeNativeJumpViaDebugger
# uses. `expr` must be a JS expression string (not a function) since it's
# shipped through Runtime.evaluate, not executeScript.
_EVAL_HELPER_JS = """
window.__evalInPdf = async (tabId, expr) => {
    const rootSession = { tabId };
    const sessions = [rootSession];
    const setupPromises = [];
    const onEvent = (source, method, params) => {
        if (!source || source.tabId !== tabId) return;
        if (method !== 'Target.attachedToTarget' || !params || !params.sessionId) return;
        const session = { tabId, sessionId: params.sessionId };
        sessions.push(session);
        setupPromises.push(chrome.debugger.sendCommand(session, 'Runtime.enable').catch(() => {}));
    };
    chrome.debugger.onEvent.addListener(onEvent);
    await chrome.debugger.attach(rootSession, '1.3');
    try {
        await chrome.debugger.sendCommand(rootSession, 'Runtime.enable').catch(() => {});
        await chrome.debugger.sendCommand(rootSession, 'Target.setAutoAttach', {
            autoAttach: true, waitForDebuggerOnStart: false, flatten: true,
        }).catch(() => {});
        await new Promise((r) => setTimeout(r, 200));
        if (setupPromises.length) await Promise.allSettled(setupPromises);
        for (const session of sessions) {
            try {
                const raw = await chrome.debugger.sendCommand(session, 'Runtime.evaluate', {
                    expression: expr, returnByValue: true, awaitPromise: false,
                });
                const value = raw && raw.result ? raw.result.value : undefined;
                if (value && value.found) return value;
            } catch { /* try the next attached session */ }
        }
        return { found: false };
    } finally {
        chrome.debugger.onEvent.removeListener(onEvent);
        await chrome.debugger.detach(rootSession).catch(() => {});
    }
};
true;
"""

_RECT_EXPR = """(() => {
    const plain = (r) => r ? {
        left: r.left, top: r.top, right: r.right, bottom: r.bottom,
        width: r.width, height: r.height, cx: r.left + r.width / 2, cy: r.top + r.height / 2,
    } : null;
    const card = document.querySelector('[data-formula-search-info-panel]');
    if (!card) return { found: false };
    const header = card.children[0];
    const seHandle = card.querySelector('[data-fs-resize-se]');
    const input = card.querySelector('.fs-chat-input');
    const send = card.querySelector('.fs-chat-send');
    const backBtn = card.querySelector('.fs-info-card-back');
    const closeBtn = card.querySelector('.fs-info-card-close');
    const overlay = document.querySelector('[data-formula-search-overlay]');
    return {
        found: true,
        card: plain(card.getBoundingClientRect()),
        header: plain(header.getBoundingClientRect()),
        seHandle: plain(seHandle && seHandle.getBoundingClientRect()),
        input: plain(input && input.getBoundingClientRect()),
        send: plain(send && send.getBoundingClientRect()),
        backBtn: plain(backBtn && backBtn.getBoundingClientRect()),
        closeBtn: plain(closeBtn && closeBtn.getBoundingClientRect()),
        cardDisplay: card.style.display,
        overlayOpacity: overlay ? overlay.style.opacity : null,
        connectorCount: document.querySelectorAll('[data-formula-search-connector]').length,
        chatBubbleCount: card.querySelectorAll('.fs-chat-msg').length,
        chatTexts: Array.from(card.querySelectorAll('.fs-chat-msg')).map((el) => el.textContent),
        firstBubbleHasTime: (() => {
            const b = card.querySelector('.fs-chat-msg');
            if (!b) return null;
            return { hasTime: !!b.querySelector('.fs-chat-msg-time') };
        })(),
        // Task 2 removed the per-message delete control entirely -- no
        // element anywhere in the card should carry a "delete"-flavored
        // class or aria-label. Checked broadly (not just inside .fs-chat-msg)
        // so a leftover delete control anywhere in the card would be caught.
        deleteControlFound: !!card.querySelector('[class*="delete" i], [aria-label*="delete" i]'),
    };
})()"""


def log(msg: str) -> None:
    print(f"[verify_ui] {msg}", flush=True)


def find_extension_id(context) -> str:
    """Reads the loaded extension's id off chrome://extensions.

    This extension does now have a background/service_worker (see
    extension/service-worker.js and extension/manifest.json), so
    context.service_workers could also locate it directly -- but
    chrome://extensions works regardless of whether an extension has a
    background context, so this stays the one path that works uniformly.
    """
    page = context.new_page()
    page.goto("chrome://extensions/", wait_until="load")
    time.sleep(1)
    ext_id = page.evaluate(
        """() => {
            const mgr = document.querySelector('extensions-manager');
            const list = mgr.shadowRoot.querySelector('extensions-item-list');
            const items = list.shadowRoot.querySelectorAll('extensions-item');
            for (const item of items) {
                const name = item.shadowRoot.querySelector('#name');
                if (name && name.textContent.includes('Formula Search')) return item.id;
            }
            return items.length ? items[0].id : null;
        }"""
    )
    page.close()
    if not ext_id:
        raise RuntimeError(
            "Could not find the extension id via chrome://extensions -- "
            "is the unpacked extension actually loading? Re-run with "
            "--keep-open and check the window that appears."
        )
    return ext_id


def find_pdf_tab_id(popup_page: Page, pdf_filename: str) -> int:
    tab_id = popup_page.evaluate(
        """async (needle) => {
            const tabs = await chrome.tabs.query({});
            const tab = tabs.find((t) => t.url && t.url.includes(needle));
            return tab ? tab.id : null;
        }""",
        pdf_filename,
    )
    if not tab_id:
        raise RuntimeError(f"Could not find a tab with '{pdf_filename}' in its URL")
    return tab_id


def get_rects(popup_page: Page, tab_id: int) -> dict[str, Any]:
    return popup_page.evaluate(
        "async ([tabId, expr]) => await window.__evalInPdf(tabId, expr)",
        [tab_id, _RECT_EXPR],
    )


def fetch_match(backend_url: str, pdf_path: Path, query: str, process: bool, arxiv_url: str | None) -> dict[str, Any]:
    with httpx.Client(timeout=60) as client:
        if arxiv_url:
            # arXiv URL processing (no file upload) hits the TeX-source
            # path, which finds far more/richer formulas than the plain
            # text-layer path a raw PDF upload uses -- matches what the
            # real extension does for an arXiv tab. Needs network access
            # unless this paper is already in .formula_cache/ from a
            # previous run (a cache hit needs none).
            log(f"POST {backend_url}/process (url={arxiv_url})")
            resp = client.post(f"{backend_url}/process", data={"url": arxiv_url})
            resp.raise_for_status()
            log(f"processed: {resp.json()}")
        elif process:
            log(f"POST {backend_url}/process ({pdf_path.name})")
            with pdf_path.open("rb") as f:
                resp = client.post(f"{backend_url}/process", files={"file": (pdf_path.name, f, "application/pdf")})
            resp.raise_for_status()
            log(f"processed: {resp.json()}")

        # /search now requires a `url` field (batch-c-backend-capabilities Task
        # 1's per-document sessions) -- must match whatever key /process just
        # used above: arxiv_url for the TeX-source branch, or "" for the plain
        # upload branch (that branch's own /process call above never sends a
        # url either, so it lands in the "" session bucket).
        session_url = arxiv_url or ""
        log(f"POST {backend_url}/search query={query!r} url={session_url!r}")
        resp = client.post(f"{backend_url}/search", json={"query": query, "url": session_url, "mode": "algebraic"})
        resp.raise_for_status()
        matches = resp.json().get("matches", [])
        if not matches:
            raise RuntimeError(f"Backend returned no matches for query {query!r}")
        # Prefer an exact match with a real bbox (a page-only match can't
        # anchor the highlight/card, which is the whole point of this check).
        match = next(
            (m for m in matches if m.get("match_type") == "exact" and m.get("bbox")),
            next((m for m in matches if m.get("bbox")), matches[0]),
        )
        log(f"using match: page={match['page']} type={match['match_type']} latex={match['latex']!r}")
        return match


# ============================================================================
# Task 3 (semantic-formula-matching): live verification of the "semantic"
# match tier Tasks 1-2 wired into backend/matcher/fuzzy.py::search_formulas()
# (structural_signature/signatures_compatible/semantically_equal in
# backend/matcher/ast_match.py), plus the CSS badge this task adds in
# extension/popup/popup.html. Unit tests already cover the tier's matching
# logic and the DOM-fake-driven rendering; what's left to confirm live is (a)
# the real running backend actually returns match_type=="semantic" over a
# real HTTP request, and (b) the real popup actually paints the new
# `.result-item.semantic` CSS rule and badge text correctly.
#
# Formula pair: the brief's own suggested example (a_1+b_1 vs its commutative
# rewrite b_1+a_1) was checked directly against search_formulas() before
# writing this section and turned out to be a false lead -- normalize_latex()
# (used by the pre-existing "exact" tier) round-trips every formula through
# SymPy, whose Add/Mul canonically reorder commutative operands, so
# a_1+b_1/b_1+a_1 normalize to the identical string and get caught by the
# "exact" tier before ever reaching "semantic" (confirmed: search_formulas()
# returns match_type=='exact', score=1.0 for that pair, not 'semantic').
# x_1+x_1 (query) vs 2*x_1 (document formula) was verified instead: the two
# are algebraically equal but normalize_latex() does NOT canonicalize them to
# the same string, so the pair genuinely reaches the "semantic" tier
# (confirmed: search_formulas() returns match_type=='semantic', score=0.8).
# It also contains a LATEX_MARKERS character (the underscore in `_1`), so
# backend/extractor/text_layer.py's _looks_like_formula() actually extracts
# "2*x_1" as a formula candidate from the synthetic PDF's text layer below --
# unlike Task 2's own bare x+x/2*x pair, which has no marker character and
# would never be extracted as a candidate in the first place.
# ============================================================================
def _make_synthetic_pdf(text: str) -> bytes:
    """Builds a minimal one-page PDF whose text layer is exactly `text`, the
    same way this project's own tests/test_api.py::_make_pdf helper does
    (fitz.Document.insert_text + tobytes()). Reimplemented here rather than
    imported since tests/ isn't on this script's import path.
    """
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 100), text, fontsize=12)
    data = doc.tobytes()
    doc.close()
    return data


def verify_semantic_match_backend(backend_url: str) -> bool:
    """Direct HTTP against the real running backend (same pattern fetch_match()
    above already uses -- no mocking): processes a synthetic PDF whose text
    layer contains the document formula "2*x_1", then POSTs /search with the
    algebraically-equal-but-not-string-identical query "x_1+x_1" and asserts
    the real JSON response contains a match with match_type=="semantic" and
    score==0.8. See the module comment above for why this specific pair was
    chosen over the brief's original suggestion.
    """
    doc_text = "2*x_1"
    query = "x_1+x_1"
    session_url = "http://local-verify-ui-test/semantic-match-check.pdf"

    pdf_bytes = _make_synthetic_pdf(doc_text)
    with httpx.Client(timeout=30) as client:
        log(f"POST {backend_url}/process (synthetic PDF, text layer={doc_text!r}, url={session_url!r})")
        resp = client.post(
            f"{backend_url}/process",
            files={"file": ("semantic-check.pdf", pdf_bytes, "application/pdf")},
            data={"url": session_url},
        )
        resp.raise_for_status()
        log(f"processed: {resp.json()}")

        log(f"POST {backend_url}/search query={query!r} url={session_url!r}")
        resp = client.post(f"{backend_url}/search", json={"query": query, "url": session_url})
        resp.raise_for_status()
        matches = resp.json().get("matches", [])

    log(f"[semantic-match backend] /search response matches: {matches!r}")
    semantic_matches = [m for m in matches if m.get("match_type") == "semantic"]
    if not semantic_matches:
        log(f"SEMANTIC MATCH FAILED: expected a match_type=='semantic' result for query {query!r} "
            f"against document formula {doc_text!r}, got {matches!r}")
        return False
    m = semantic_matches[0]
    if m.get("score") != 0.8:
        log(f"SEMANTIC MATCH FAILED: expected score 0.8 on the semantic match, got {m.get('score')!r} ({m!r})")
        return False
    log(f"real running backend returns match_type='semantic', score=0.8 for a genuine algebraic-equivalence "
        f"pair not caught by the exact tier -- OK ({m!r})")
    return True


def verify_semantic_badge_rendering(popup_page: Page) -> bool:
    """Reflection on the real popup (the same injection technique this script
    already uses elsewhere -- e.g. the bookmarks/badge/export sections above
    -- to exercise rendering states without a live search): injects a
    synthetic match with match_type=="semantic" into the real _matches array,
    calls the real renderResults() by name, then reads back the real DOM to
    confirm the rendered .result-item's class list, computed
    border-left-color, and badge text.
    """
    state = popup_page.evaluate(
        """() => {
            _viewMode = 'list';
            _matches = [{
                latex: 'x_1+x_1', label: '', section: '', source: 'text',
                match_type: 'semantic', score: 0.8, page: 0, bbox: null,
                page_w: 0, page_h: 0, bbox_source: '',
            }];
            renderResults();
            const li = document.querySelector('#results .result-item');
            if (!li) return { found: false };
            const badge = li.querySelector('.badge');
            return {
                found: true,
                className: li.className,
                borderLeftColor: getComputedStyle(li).borderLeftColor,
                badgeText: badge ? badge.textContent : null,
            };
        }"""
    )
    log(f"[semantic badge] rendered #results .result-item state: {state!r}")
    if not state.get("found"):
        log("SEMANTIC BADGE FAILED: no .result-item was rendered after injecting a synthetic semantic match")
        return False
    if "semantic" not in state["className"].split():
        log(f"SEMANTIC BADGE FAILED: expected 'semantic' in the rendered .result-item's class list, got {state['className']!r}")
        return False
    if state["borderLeftColor"] != "rgb(139, 92, 246)":
        log(f"SEMANTIC BADGE FAILED: expected computed border-left-color rgb(139, 92, 246) (#8b5cf6), got {state['borderLeftColor']!r}")
        return False
    if state["badgeText"] != "semantic":
        log(f"SEMANTIC BADGE FAILED: expected the badge text to read 'semantic', got {state['badgeText']!r}")
        return False
    log(f"popup renders '.result-item.semantic' with the correct purple border and badge text -- OK ({state!r})")
    return True


def build_payload(match: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "scrollToFormula",
        "page": match["page"],
        "bbox": match.get("bbox"),
        "page_w": match["page_w"],
        "page_h": match["page_h"],
        "latex": match.get("latex", ""),
        "source": match.get("source", ""),
        "match_type": match.get("match_type", ""),
        "bbox_source": match.get("bbox_source", ""),
        # Filled in once the real extension id is known (see main() -- it
        # isn't known yet at build_payload() call time). Mirrors what
        # matchMessage() stamps onto every real jump payload: the id the
        # chrome.debugger-realm copy of nativePdfInstantJump needs to relay
        # chat reads/writes through the background service worker, since
        # that realm has chrome.runtime but no chrome.storage of its own.
        "extensionId": "",
        "showInfoPanel": True,
        "infoPanel": {
            "rawLatex": match.get("latex", ""),
            "previewLatex": match.get("latex", ""),
            "label": match.get("label", ""),
            "section": match.get("section", ""),
            "sourceType": match.get("source", ""),
            "matchType": match.get("match_type", ""),
            "katexJsUrl": None,
            "katexCssUrl": None,
            "katexMacros": {},
        },
    }


def run_jump(popup_page: Page, tab_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    payload["infoPanel"]["katexJsUrl"] = popup_page.evaluate("KATEX_JS_URL")
    payload["infoPanel"]["katexCssUrl"] = popup_page.evaluate("KATEX_CSS_URL")
    result = popup_page.evaluate(
        """async ([tabId, payload]) => {
            for (const runner of [
                () => executeNativeJumpViaScripting(tabId, payload),
                () => executeNativeJumpViaContentBridge(tabId, payload),
                () => executeNativeJumpViaDebugger(tabId, payload),
            ]) {
                const r = await runner();
                if (r && r.ok) return r;
            }
            return { ok: false, error: 'all transports failed' };
        }""",
        [tab_id, payload],
    )
    return result


def resize_card_via_se_handle(popup_page: Page, tab_id: int, dx: int, dy: int) -> None:
    """Grows the card from its bottom-right corner by (dx, dy).

    Dispatches PointerEvents directly via JS instead of Playwright's
    mouse.move/down/up: real synthetic mouse drags don't reliably keep
    pointer capture on this small 12x12 handle across the PDF guest-view
    boundary (confirmed by testing both -- the resize logic itself is fine,
    this is purely an automation-input limitation of dragging a tiny target
    through this particular boundary).
    """
    steps = 10
    expr = f"""(() => {{
        const card = document.querySelector('[data-formula-search-info-panel]');
        const seHandle = card.querySelector('[data-fs-resize-se]');
        const r = seHandle.getBoundingClientRect();
        const startX = r.left + r.width / 2;
        const startY = r.top + r.height / 2;
        function fire(el, type, x, y) {{
            el.dispatchEvent(new PointerEvent(type, {{
                bubbles: true, cancelable: true, composed: true,
                pointerId: 1, pointerType: 'mouse', isPrimary: true,
                clientX: x, clientY: y, button: 0, buttons: type === 'pointerup' ? 0 : 1,
            }}));
        }}
        fire(seHandle, 'pointerdown', startX, startY);
        for (let i = 1; i <= {steps}; i++) {{
            fire(seHandle, 'pointermove', startX + i * ({dx} / {steps}), startY + i * ({dy} / {steps}));
        }}
        fire(seHandle, 'pointerup', startX + {dx}, startY + {dy});
        return {{ found: true }};
    }})()"""
    popup_page.evaluate("async ([tabId, expr]) => await window.__evalInPdf(tabId, expr)", [tab_id, expr])


# ============================================================================
# Task 6 (batch-b-discovery-ui) helpers: real keyboard-shortcut delivery.
#
# manifest.json's `commands` block gives next-result/prev-result default
# suggested_key bindings, but toggle-chat deliberately ships with none -- a
# real user has to bind one by hand via chrome://extensions/shortcuts before
# it exists at all. assign_toggle_chat_shortcut() does exactly that, through
# the real settings UI (a real mouse click on the real "edit" button, a real
# keypress to record the combo), not by writing to chrome.storage/preferences
# directly.
#
# send_os_keystroke() answers the actual open question this task exists to
# settle: does a keyboard shortcut dispatched through the real OS input path
# reach chrome.commands.onCommand in this Playwright-driven environment?
# Confirmed empirically while building this check (see task-6-report.md):
# Playwright/CDP's page.keyboard.press() -- synthetic Input.dispatchKeyEvent,
# delivered straight to a renderer -- never fires chrome.commands.onCommand,
# even for commands with default suggested_key bindings. Windows' own
# SendKeys API, aimed at the real foreground browser window via WScript.Shell
# AppActivate, does. The former is what earlier Batch-B code (popup.js,
# service-worker.js) already exercises indirectly through the fake DOM; the
# latter is the first time this repo has driven an actual accelerator
# end-to-end, which is the whole reason this helper -- and not another
# page.keyboard.press() call -- exists.
# ============================================================================
def assign_toggle_chat_shortcut(context, keys: str) -> str:
    """Assigns `keys` (a Playwright keyboard-combo string, e.g. 'Alt+C') as
    the toggle-chat command's shortcut via the real chrome://extensions/
    shortcuts settings UI. Returns the shortcut string Chrome recorded (e.g.
    'Alt + C'), or '' if the command's row/edit control couldn't be found.
    """
    sc_page = context.new_page()
    try:
        sc_page.goto("chrome://extensions/shortcuts", wait_until="load")
        time.sleep(1)
        rect = sc_page.evaluate(
            """() => {
                const mgr = document.querySelector('extensions-manager');
                const ksc = mgr.shadowRoot.querySelector('extensions-keyboard-shortcuts');
                const cards = ksc.shadowRoot.querySelectorAll('.shortcut-card');
                for (const card of cards) {
                    for (const row of card.querySelectorAll('.command-entry')) {
                        if (!row.querySelector('select[data-command-name="toggle-chat"]')) continue;
                        const shortcutInput = row.querySelector('cr-shortcut-input');
                        const editBtn = shortcutInput.shadowRoot.querySelector('#edit');
                        const r = editBtn.getBoundingClientRect();
                        return { found: true, cx: r.left + r.width / 2, cy: r.top + r.height / 2 };
                    }
                }
                return { found: false };
            }"""
        )
        if not rect.get("found"):
            return ""
        sc_page.mouse.click(rect["cx"], rect["cy"])
        time.sleep(0.3)
        sc_page.keyboard.press(keys)
        time.sleep(0.5)
        return sc_page.evaluate(
            """() => {
                const mgr = document.querySelector('extensions-manager');
                const ksc = mgr.shadowRoot.querySelector('extensions-keyboard-shortcuts');
                const cards = ksc.shadowRoot.querySelectorAll('.shortcut-card');
                for (const card of cards) {
                    for (const row of card.querySelectorAll('.command-entry')) {
                        if (!row.querySelector('select[data-command-name="toggle-chat"]')) continue;
                        return row.querySelector('cr-shortcut-input').shadowRoot.querySelector('#input').value;
                    }
                }
                return '';
            }"""
        )
    finally:
        sc_page.close()


def send_os_keystroke(window_title_needle: str, sendkeys_code: str, log_fn) -> bool:
    """Sends a REAL OS-level keystroke to the visible Chrome window, via
    Windows' SendKeys aimed at that window through WScript.Shell.AppActivate
    -- routed through the actual OS input pipeline, unlike Playwright/CDP's
    page.keyboard.press() (see the module-level comment above this function
    for why that distinction is the entire point). Windows-only; callers
    should check sys.platform first and treat a False return (or a caught
    exception) as "could not be tested here", not as "the shortcut is
    broken".
    """
    ps_script = f"""
$ErrorActionPreference = 'Stop'
$proc = Get-Process chrome -ErrorAction SilentlyContinue | Where-Object {{ $_.MainWindowTitle -like '*{window_title_needle}*' }} | Select-Object -First 1
if (-not $proc) {{ Write-Output 'NO_WINDOW'; exit 1 }}
Add-Type -AssemblyName System.Windows.Forms
$shell = New-Object -ComObject WScript.Shell
$activated = $shell.AppActivate($proc.Id)
Start-Sleep -Milliseconds 400
[System.Windows.Forms.SendKeys]::SendWait('{sendkeys_code}')
Start-Sleep -Milliseconds 300
Write-Output "ACTIVATED=$activated"
"""
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            capture_output=True, text=True, timeout=20,
        )
    except Exception as exc:
        log_fn(f"send_os_keystroke: powershell invocation failed: {exc}")
        return False
    ok = "ACTIVATED=True" in result.stdout
    log_fn(f"send_os_keystroke({sendkeys_code!r} -> window matching {window_title_needle!r}): stdout={result.stdout.strip()!r} stderr={result.stderr.strip()!r} ok={ok}")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pdf", required=True, type=Path, help="Local PDF file to open (also used to locate the tab)")
    parser.add_argument("--query", required=True, help="Search query sent to the backend to find a real formula match")
    parser.add_argument("--backend-url", default="http://127.0.0.1:7878", help="Backend base URL")
    parser.add_argument("--process", action="store_true", help="POST the PDF file to /process before searching (text-layer path)")
    parser.add_argument("--url", default=None, help="arXiv URL to process instead of uploading the file (richer TeX-sourced formulas, matching how the real extension handles arXiv tabs)")
    parser.add_argument("--out-dir", type=Path, default=None, help="Where to save screenshots (default: a fresh temp dir)")
    parser.add_argument("--profile-dir", type=Path, default=None, help="Chromium profile dir (default: a fresh temp dir)")
    parser.add_argument("--skip-interactions", action="store_true", help="Only open the card and screenshot it; skip chat/resize/drag/scroll")
    parser.add_argument("--keep-open", action="store_true", help="Leave the browser open at the end for manual inspection")
    args = parser.parse_args()

    pdf_path = args.pdf.resolve()
    if not pdf_path.is_file():
        parser.error(f"PDF not found: {pdf_path}")

    out_dir = args.out_dir or Path(tempfile.mkdtemp(prefix="formula-search-ui-"))
    out_dir.mkdir(parents=True, exist_ok=True)
    profile_dir = args.profile_dir or Path(tempfile.mkdtemp(prefix="formula-search-chrome-profile-"))

    def shot(page: Page, name: str) -> None:
        path = out_dir / f"{name}.png"
        page.screenshot(path=str(path))
        log(f"screenshot: {path}")

    log("=== Task 3 (semantic-formula-matching) live verification: backend semantic-match tier ===")
    if not verify_semantic_match_backend(args.backend_url):
        return 1
    log("=== backend semantic-match check complete ===")

    match = fetch_match(args.backend_url, pdf_path, args.query, args.process, args.url)
    payload = build_payload(match)
    # Mirrors fetch_match()'s own session_url logic exactly (args.url for the
    # arXiv-URL branch, "" for the plain-upload branch) -- this is the real
    # backend session key that already has args.query's results in it.
    # Reflection-based popup state setup below (the bookmarks section) needs
    # to seat currentSessionUrl to this same value, or its real #search-btn
    # click sends url:'' to a session that was never populated and finds
    # nothing (batch-c-backend-capabilities Task 1 made `url` load-bearing
    # for /search; this script predates that and needs updating to match).
    bootstrap_session_url = args.url or ""

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            str(profile_dir),
            headless=False,
            args=[
                f"--disable-extensions-except={EXTENSION_DIR}",
                f"--load-extension={EXTENSION_DIR}",
                "--no-first-run",
                "--window-size=1400,1000",
            ],
            viewport={"width": 1400, "height": 900},
            accept_downloads=True,
        )
        try:
            context.grant_permissions(["clipboard-read", "clipboard-write"])
        except Exception as exc:
            log(f"could not grant clipboard permissions, per-message copy check may be skipped: {exc}")
        try:
            pdf_page = context.new_page()
            pdf_page.goto(pdf_path.as_uri(), wait_until="load", timeout=30000)
            time.sleep(3)

            ext_id = find_extension_id(context)
            log(f"extension id: {ext_id}")
            payload["extensionId"] = ext_id

            popup_page = context.new_page()
            popup_page.goto(f"chrome-extension://{ext_id}/popup/popup.html", wait_until="load")
            time.sleep(1)
            popup_page.evaluate(_EVAL_HELPER_JS)

            log("=== Task 3 (semantic-formula-matching) live verification: popup semantic-match badge rendering ===")
            if not verify_semantic_badge_rendering(popup_page):
                return 1
            log("=== popup semantic-match badge check complete ===")

            tab_id = find_pdf_tab_id(popup_page, pdf_path.name)
            log(f"pdf tab id: {tab_id}")

            result = run_jump(popup_page, tab_id, payload)
            log(f"jump result: ok={result.get('ok')} transport={result.get('transport')}")
            if not result.get("ok"):
                log(f"jump FAILED: {json.dumps(result)}")
                return 1

            pdf_page.bring_to_front()
            time.sleep(1.5)
            shot(pdf_page, "01_card_opened")

            rects = get_rects(popup_page, tab_id)
            if not rects.get("found"):
                log("ERROR: card not found in the DOM after opening")
                return 1
            log(f"card rect: {json.dumps(rects['card'])}")

            if not args.skip_interactions:
                # Chat: click the input, type, send via Enter.
                pdf_page.mouse.click(rects["input"]["cx"], rects["input"]["cy"])
                time.sleep(0.3)
                pdf_page.keyboard.type("What does the star superscript mean here?", delay=15)
                shot(pdf_page, "02_chat_typed")
                pdf_page.keyboard.press("Enter")
                time.sleep(0.6)
                shot(pdf_page, "03_chat_sent")

                rects_after_send = get_rects(popup_page, tab_id)
                bubble_info = rects_after_send.get("firstBubbleHasTime")
                if not bubble_info or not bubble_info.get("hasTime"):
                    log(f"CHAT UI FAILED: expected the sent message's bubble to have a timestamp, got {bubble_info!r}")
                    return 1
                log(f"chat bubble UI OK -- {bubble_info!r}")

                if rects_after_send.get("deleteControlFound"):
                    log("DELETE CONTROL FOUND: Task 2 removed the per-message delete control -- none should be present anywhere in the card")
                    return 1
                log("no delete control present in the card -- OK")

                # Task 4's core fix, and the single most important thing this
                # whole live pass exists to re-confirm: sending a SECOND
                # message in the SAME card session must be appended
                # alongside the first (stacked), not replace it. Reuses the
                # freshly-read input rect (rects_after_send) rather than the
                # pre-send `rects`, in case the bubble list's growth shifted
                # the input's position at all.
                second_message_text = "Does it relate to the attention weights?"
                pdf_page.mouse.click(rects_after_send["input"]["cx"], rects_after_send["input"]["cy"])
                time.sleep(0.3)
                pdf_page.keyboard.type(second_message_text, delay=15)
                pdf_page.keyboard.press("Enter")
                time.sleep(0.6)
                shot(pdf_page, "03b_chat_second_sent")

                rects_after_second = get_rects(popup_page, tab_id)
                bubble_count = rects_after_second.get("chatBubbleCount")
                chat_texts_in_card = rects_after_second.get("chatTexts") or []
                if bubble_count != 2:
                    log(f"MESSAGE STACKING FAILED: expected exactly 2 message bubbles in the card after two sends, got {bubble_count}: {chat_texts_in_card!r}")
                    return 1
                if not any("star superscript" in t for t in chat_texts_in_card):
                    log(f"MESSAGE STACKING FAILED: the first message is missing after the second send (replaced, not stacked) -- got {chat_texts_in_card!r}")
                    return 1
                if not any("attention weights" in t for t in chat_texts_in_card):
                    log(f"MESSAGE STACKING FAILED: the second message never landed -- got {chat_texts_in_card!r}")
                    return 1
                log(f"message stacking OK -- both messages present, stacked: {chat_texts_in_card!r}")

                # Task 6: the info card's close AND back buttons. Both must
                # be present (queried straight off the live card, same rect
                # helper used for everything else) and both must actually
                # close the card on a real, CDP-dispatched mouse click (not
                # just a JS-synthetic .click(), which previously missed the
                # onHeaderPointerDown drag-exclusion bug Task 6 found and
                # fixed). Visual centering is confirmed by eye via
                # 01_card_opened.png, per the brief.
                back_rect = rects_after_second.get("backBtn")
                close_rect = rects_after_second.get("closeBtn")
                if not back_rect or not close_rect:
                    log(f"BACK/CLOSE BUTTON FAILED: expected both present in the card header, got back={back_rect!r} close={close_rect!r}")
                    return 1
                log(f"back/close buttons both present -- back cx,cy=({back_rect['cx']:.0f},{back_rect['cy']:.0f}) close cx,cy=({close_rect['cx']:.0f},{close_rect['cy']:.0f})")

                log("clicking the close (x) button with a real mouse click")
                pdf_page.mouse.click(close_rect["cx"], close_rect["cy"])
                time.sleep(0.4)
                rects_after_close = get_rects(popup_page, tab_id)
                if rects_after_close.get("found"):
                    log("CLOSE BUTTON FAILED: card is still present after clicking the close (x) button")
                    return 1
                log("close button OK -- card removed from the DOM")

                log("re-jumping to reopen the card for the back-button check")
                result_reopen = run_jump(popup_page, tab_id, payload)
                if not result_reopen.get("ok"):
                    log(f"re-jump (for back-button check) FAILED: {json.dumps(result_reopen)}")
                    return 1
                time.sleep(1.0)
                rects_reopened = get_rects(popup_page, tab_id)
                back_rect2 = rects_reopened.get("backBtn")
                if not rects_reopened.get("found") or not back_rect2:
                    log(f"BACK BUTTON FAILED: card/back button not found after reopening, got {rects_reopened!r}")
                    return 1

                log("clicking the back (<-) button with a real mouse click")
                pdf_page.mouse.click(back_rect2["cx"], back_rect2["cy"])
                time.sleep(0.4)
                rects_after_back = get_rects(popup_page, tab_id)
                if rects_after_back.get("found"):
                    log("BACK BUTTON FAILED: card is still present after clicking the back (<-) button")
                    return 1
                log("back button OK -- card removed from the DOM (openPopup() reopen is best-effort/unverified under CDP automation, not asserted here -- see Task 6's report)")

                log("re-jumping once more to restore the card for the remaining resize/drag/scroll/persistence steps")
                result_restore = run_jump(popup_page, tab_id, payload)
                if not result_restore.get("ok"):
                    log(f"re-jump (restore after back-button check) FAILED: {json.dumps(result_restore)}")
                    return 1
                time.sleep(1.0)
                shot(pdf_page, "03c_reopened_after_back")

                log("checking popup-only features (draft persistence, unread badge, per-message copy)")
                popup_page.click("#chat-toggle-btn")
                popup_page.fill("#chat-input", "draft check")
                popup_page.wait_for_timeout(500)  # let the 300ms debounce fire
                popup_page.click("#chat-toggle-btn")  # close
                popup_page.click("#chat-toggle-btn")  # reopen
                draft_value = popup_page.input_value("#chat-input")
                if draft_value != "draft check":
                    log(f"DRAFT PERSISTENCE FAILED: expected 'draft check', got {draft_value!r}")
                    return 1
                popup_page.fill("#chat-input", "")
                log("draft persistence OK")

                # Copy is now a per-message control on each bubble (no more
                # whole-thread #chat-copy-btn), so it only needs one message
                # to act on. The earlier chat-sent step (03_chat_sent) only
                # guarantees that when the jump used the scripting/
                # content-script transport -- the debugger transport
                # (Chrome's own built-in PDF viewer, which local file://
                # PDFs hit) has no chrome.storage access at all (see the
                # "known, accepted limitation" note further down), so that
                # message never actually reached fs_chat_history. Send one
                # from the popup's own chat input instead, which always has
                # real chrome.storage access regardless of the PDF tab's
                # transport, so Copy has guaranteed content to act on.
                popup_page.fill("#chat-input", "verify copy works too")
                popup_page.click("#chat-send-btn")
                popup_page.wait_for_timeout(300)

                popup_page.click("#chat-messages .chat-msg:last-child .chat-msg-copy")
                popup_page.wait_for_timeout(200)
                # A readText() failure here is ambiguous -- it could mean the
                # click handler never wrote anything (a real bug) OR that this
                # environment just doesn't have clipboard-read permission
                # despite the grant_permissions() call above (sandboxed CI,
                # certain Linux desktops without a system clipboard, etc).
                # Those two cases must not be conflated: silently treating a
                # read failure as a pass (e.g. by substituting the expected
                # string) would let a genuinely broken copy handler hide
                # behind an unrelated environment limitation. So a failed
                # read is logged and SKIPPED (not scored as pass or fail);
                # only an actual mismatched/successful read is asserted on.
                try:
                    clipboard_text = popup_page.evaluate("() => navigator.clipboard.readText()")
                except Exception as exc:
                    log(f"SKIPPED per-message copy content check -- could not read clipboard back in this environment (not a copy failure, just unverifiable here): {exc}")
                else:
                    if clipboard_text != "verify copy works too":
                        log(f"COPY FAILED: expected clipboard to hold just that message's text, got {clipboard_text!r}")
                        return 1
                    log("per-message copy-to-clipboard OK")

                # Task 3: clicking the list/step toggle while chat is open
                # must switch away from chat cleanly, and neither toggle
                # button is ever .disabled. Chat is still open here (last
                # popup action above was reopening it for the copy check),
                # so this needs no new PDF jump, per the brief.
                log("checking the list/step toggle switches away from chat without disabling anything")
                popup_page.click("#view-toggle-btn")
                toggle_state = popup_page.evaluate(
                    """() => ({
                        chatHidden: document.getElementById('chat-view').classList.contains('hidden'),
                        resultsHidden: document.getElementById('results').classList.contains('hidden'),
                        viewToggleDisabled: document.getElementById('view-toggle-btn').disabled,
                        chatToggleDisabled: document.getElementById('chat-toggle-btn').disabled,
                    })"""
                )
                if not toggle_state["chatHidden"] or toggle_state["resultsHidden"]:
                    log(f"VIEW TOGGLE FAILED: expected leaving chat to hide the chat view and show the results list, got {toggle_state!r}")
                    return 1
                if toggle_state["viewToggleDisabled"] or toggle_state["chatToggleDisabled"]:
                    log(f"VIEW TOGGLE FAILED: a toggle button was .disabled, got {toggle_state!r}")
                    return 1
                log(f"view toggle OK -- switched away from chat, neither toggle button disabled: {toggle_state!r}")

                # Resize from the bottom-right corner before dragging, so the
                # header position used for the drag step is still valid (SE
                # resize keeps the top-left anchored).
                resize_card_via_se_handle(popup_page, tab_id, dx=220, dy=220)
                time.sleep(0.5)
                shot(pdf_page, "04_resized")

                rects = get_rects(popup_page, tab_id)
                header = rects["header"]
                drag_to = (header["cx"] - 300, header["cy"] + 350)
                pdf_page.mouse.move(header["cx"], header["cy"])
                pdf_page.mouse.down()
                pdf_page.mouse.move(*drag_to, steps=15)
                pdf_page.mouse.up()
                time.sleep(0.4)
                shot(pdf_page, "05_dragged")

                # Scroll the formula off-screen, then back. Wheel events land
                # at the mouse's current position -- has to be over the
                # document pane, not the card, and not the thumbnail sidebar
                # (x < ~290), which has its own independent scroll.
                pdf_page.mouse.move(400, 500)
                pdf_page.mouse.wheel(0, 2000)
                time.sleep(0.6)
                shot(pdf_page, "06_scrolled_away")
                rects = get_rects(popup_page, tab_id)
                log(f"scrolled away -- cardDisplay={rects.get('cardDisplay')!r} overlayOpacity={rects.get('overlayOpacity')!r} connectors={rects.get('connectorCount')}")

                pdf_page.mouse.wheel(0, -2000)
                time.sleep(0.6)
                shot(pdf_page, "07_scrolled_back")
                rects = get_rects(popup_page, tab_id)
                log(f"scrolled back -- cardDisplay={rects.get('cardDisplay')!r} overlayOpacity={rects.get('overlayOpacity')!r} connectors={rects.get('connectorCount')}")

                # Persistence check: re-jump to the exact same match, which
                # closes the current card via closeInfoPanel() and opens a
                # fresh one (the same code path a user hits by clicking a
                # result again), then confirm the earlier chat message is
                # still there without retyping it -- the whole point of the
                # chat-persistence feature is that chrome.storage.local
                # survives the card being destroyed and recreated.
                #
                # Previously a known, accepted limitation for the `debugger`
                # transport (that transport runs the card's code directly in
                # the PDF guest view's own raw page realm -- confirmed live
                # to be Chrome's own built-in PDF Viewer extension's page,
                # chrome-extension://mhjfbmdgcfjbbpaeojofohoefgiehjai/, which
                # has no chrome.storage of its own). Fixed: the background
                # service worker (extension/service-worker.js) now relays
                # chat reads/writes for that realm through
                # chrome.runtime.sendMessage(payload.extensionId, ...) (see
                # RELAY_EXTENSION_ID / relayChatMessage in popup.js's
                # nativePdfInstantJump), so persistence now holds for every
                # transport, debugger included -- asserted unconditionally
                # below. That covers reads-at-open-time and writes-that-
                # persist across close/reopen, which is exactly what this
                # re-jump exercises -- it does not mean an already-open
                # debugger-realm card gets a live push if another window
                # sends a message while this card stays open; chrome.storage
                # .onChanged, which every other surface relies on for that,
                # cannot register in this realm at all.
                log("re-jumping to the same match to verify chat history persists across close/reopen")
                result2 = run_jump(popup_page, tab_id, payload)
                if not result2.get("ok"):
                    log(f"re-jump FAILED: {json.dumps(result2)}")
                    return 1
                time.sleep(1.0)
                shot(pdf_page, "08_reopened")
                rects2 = get_rects(popup_page, tab_id)
                chat_texts = rects2.get("chatTexts") or []
                persisted = any("star superscript" in t for t in chat_texts)
                if not persisted:
                    log(f"PERSISTENCE FAILED: expected the earlier chat message to still be present, got {chat_texts!r}")
                    return 1
                log(f"persistence OK (transport={result.get('transport')!r}) -- chat history after reopen: {chat_texts!r}")

                # Task 5: bookmarking/starring formulas, exercised through
                # the real popup UI (search box, result star, bookmarks
                # toggle/list) rather than the low-level executeNativeJumpVia*
                # helpers used above. Several seams are poked directly by
                # name -- the same reflection this script already relies on
                # for KATEX_JS_URL/executeNativeJumpViaScripting/etc --
                # because this script opens popup.html as an ordinary tab, so
                # chrome.tabs.query({active:true, currentWindow:true}) inside
                # the popup's own init() resolves to the popup's own tab, not
                # the PDF tab: init() takes the "no PDF detected" branch and
                # leaves #query/#search-btn permanently .disabled (only its
                # PDF-detected branch re-enables them). _activeTab is
                # reseated to the real PDF tab_id so a genuine click on a
                # bookmark list item can drive sendScrollToMatch() exactly
                # like a real user would; queryInput/searchBtn are manually
                # re-enabled the same way init() itself would have; BACKEND
                # is reseated so the popup's own fetch() targets
                # --backend-url instead of whatever chrome.storage settings
                # (or its localhost:7878 default) would otherwise pick.
                # currentSessionUrl is likewise reseated to bootstrap_session_url
                # (batch-c-backend-capabilities Task 2) -- without this, the real
                # #search-btn click below sends url:'' to /search, which is a
                # session that was never processed, and the wait_for_selector
                # right after this block times out with zero rendered results.
                log("checking bookmarks: star a result, open the bookmarks view, jump from it, then un-star")
                popup_page.evaluate(
                    """([tabId, backendUrl, sessionUrl]) => {
                        _activeTab = { id: tabId };
                        BACKEND = backendUrl;
                        currentSessionUrl = sessionUrl;
                        queryInput.disabled = false;
                        searchBtn.disabled = false;
                        // Force list mode: in step mode, runSearch() itself
                        // calls sendScrollToMatch() right after rendering
                        // results, which would set #status to an "Instant
                        // jump..." message before the bookmark item is even
                        // clicked below and make that later check ambiguous.
                        _viewMode = 'list';
                    }""",
                    [tab_id, args.backend_url, bootstrap_session_url],
                )
                popup_page.fill("#query", args.query)
                popup_page.click("#search-btn")
                popup_page.wait_for_selector("#results .result-item", timeout=15000)

                star_selector = "#results .result-item:first-child .bookmark-star-btn"
                popup_page.wait_for_selector(star_selector, timeout=5000)
                popup_page.click(star_selector)
                popup_page.wait_for_timeout(300)
                star_text = popup_page.eval_on_selector(star_selector, "el => el.textContent")
                if star_text != "★":
                    log(f"BOOKMARKS FAILED: expected the result's star button to show ★ after clicking it, got {star_text!r}")
                    return 1
                log("starring a result turns ☆ into ★ -- OK")

                popup_page.click("#bookmarks-toggle-btn")
                popup_page.wait_for_timeout(200)
                bm_state = popup_page.evaluate(
                    """() => ({
                        bookmarksHidden: document.getElementById('bookmarks-view').classList.contains('hidden'),
                        resultsHidden: document.getElementById('results').classList.contains('hidden'),
                        chatHidden: document.getElementById('chat-view').classList.contains('hidden'),
                        itemCount: document.querySelectorAll('#bookmarks-list .result-item').length,
                    })"""
                )
                if bm_state["bookmarksHidden"] or not bm_state["resultsHidden"] or not bm_state["chatHidden"]:
                    log(f"BOOKMARKS FAILED: expected #bookmarks-view visible with #results/#chat-view hidden, got {bm_state!r}")
                    return 1
                if bm_state["itemCount"] != 1:
                    log(f"BOOKMARKS FAILED: expected exactly 1 item in #bookmarks-list, got {bm_state!r}")
                    return 1
                log(f"#bookmarks-toggle-btn shows the bookmarked item with #results/#chat-view hidden -- OK ({bm_state!r})")

                # Cleared (rather than just snapshotted) immediately before
                # the click so the post-click assertion below can't be
                # confused by a leftover "Instant jump..." message from an
                # earlier, unrelated action in this script.
                popup_page.eval_on_selector("#status", "el => { el.textContent = ''; }")
                popup_page.click("#bookmarks-list .result-item")
                popup_page.wait_for_timeout(1200)
                status_after_bookmark_click = popup_page.eval_on_selector("#status", "el => el.textContent")
                log(f"status after clicking bookmark item: {status_after_bookmark_click!r}")
                if "Instant jump" not in status_after_bookmark_click:
                    log(f"BOOKMARKS FAILED: clicking a bookmark item did not report a successful native jump, got status={status_after_bookmark_click!r}")
                    return 1
                # statusForNativeResponse() embeds " via {transport}" straight
                # from the executeNativeJumpVia* response, the same signal
                # the run_jump()/log() calls elsewhere in this script surface
                # as transport=... -- read back here rather than logged
                # separately since sendScrollToMatch() (unlike run_jump())
                # doesn't return its response object to the caller.
                log(f"clicking a bookmark item drove a real native jump -- OK (status: {status_after_bookmark_click!r})")

                popup_page.click("#bookmarks-list .bookmark-star-btn")
                popup_page.wait_for_timeout(300)
                empty_state = popup_page.evaluate(
                    """() => ({
                        hasEmptyMsg: !!document.getElementById('bookmarks-empty'),
                        itemCount: document.querySelectorAll('#bookmarks-list .result-item').length,
                    })"""
                )
                if not empty_state["hasEmptyMsg"] or empty_state["itemCount"] != 0:
                    log(f"BOOKMARKS FAILED: expected #bookmarks-list back to its empty state after un-starring, got {empty_state!r}")
                    return 1
                log("un-starring from the bookmarks view returns #bookmarks-list to its empty state -- OK")

                # ---------------------------------------------------------------
                # Task 7 (batch-a-storage-settings): live verification of the
                # remaining Batch A surfaces the sections above don't touch --
                # the options/settings page, the theme setting, the
                # search-history dropdown, the recently-opened-papers list
                # (including Task 6's warm-cache fast-path fix, which was only
                # ever unit-tested at the store level before this), and the
                # maxChatHistory cap. Placed last since editing backendUrl/
                # maxChatHistory here would otherwise perturb the checks above.
                # ---------------------------------------------------------------
                log("checking the options page: documented defaults load, edits round-trip through save + reopen")
                options_page = context.new_page()
                options_page.goto(f"chrome-extension://{ext_id}/options/options.html", wait_until="load")
                time.sleep(0.5)

                def read_options_form() -> dict[str, Any]:
                    return options_page.evaluate(
                        """() => ({
                            backendUrl: document.getElementById('backend-url').value,
                            maxChatHistory: document.getElementById('max-chat-history').value,
                            defaultView: document.getElementById('default-view').value,
                            theme: document.getElementById('theme').value,
                            badgeEnabled: document.getElementById('badge-enabled').checked,
                        })"""
                    )

                initial_options = read_options_form()
                expected_defaults = {
                    "backendUrl": "http://localhost:7878",
                    "maxChatHistory": "500",
                    "defaultView": "list",
                    "theme": "auto",
                    "badgeEnabled": True,
                }
                if initial_options != expected_defaults:
                    log(f"OPTIONS PAGE FAILED: expected documented defaults {expected_defaults!r}, got {initial_options!r}")
                    return 1
                log(f"options page loads the documented defaults -- OK ({initial_options!r})")
                shot(options_page, "09_options_auto_theme")

                # Theme toggle: confirm it visibly re-colors the page (not just
                # flips a select value) by reading the computed background
                # color back for both explicit themes, screenshotting each.
                options_page.select_option("#theme", "light")
                time.sleep(0.2)
                light_bg = options_page.evaluate("() => getComputedStyle(document.body).backgroundColor")
                shot(options_page, "10_options_light_theme")

                options_page.select_option("#theme", "dark")
                time.sleep(0.2)
                dark_bg = options_page.evaluate("() => getComputedStyle(document.body).backgroundColor")
                shot(options_page, "11_options_dark_theme")

                if light_bg == dark_bg:
                    log(f"THEME FAILED: light/dark theme selection did not change the rendered background color, both were {light_bg!r}")
                    return 1
                log(f"theme selection visibly re-colors the page -- OK (light={light_bg!r} dark={dark_bg!r})")

                # Edit every field to a non-default value (dark theme is left
                # selected from the toggle check above), save, then reload the
                # SAME tab (equivalent to closing and reopening Settings) and
                # confirm the new values persisted rather than reverting to
                # defaults. maxChatHistory is set deliberately tiny here so the
                # cap-enforcement check further down has something to trim.
                # #badge-enabled is intentionally left alone (and asserted
                # disabled) below -- badgeEnabled has no consumer yet in this
                # batch (chrome.action.setBadgeText is scoped to Batch B), so
                # the final whole-branch review disabled the checkbox in the
                # UI rather than ship a control that visibly does nothing.
                CHAT_CAP = 3
                options_page.fill("#backend-url", args.backend_url)
                options_page.fill("#max-chat-history", str(CHAT_CAP))
                options_page.select_option("#default-view", "list")
                if options_page.is_enabled("#badge-enabled"):
                    log("OPTIONS PAGE FAILED: #badge-enabled is enabled -- it should be disabled until Batch B wires up a real consumer")
                    return 1
                options_page.click("#save-btn")
                options_page.wait_for_timeout(300)
                save_status = options_page.eval_on_selector("#save-status", "el => el.textContent")
                log(f"save status: {save_status!r}")

                options_page.reload(wait_until="load")
                time.sleep(0.5)
                reloaded_options = read_options_form()
                expected_after_save = {
                    "backendUrl": args.backend_url,
                    "maxChatHistory": str(CHAT_CAP),
                    "defaultView": "list",
                    "theme": "dark",
                    "badgeEnabled": True,  # disabled control, never toggled -- stays at its loaded value
                }
                if reloaded_options != expected_after_save:
                    log(f"OPTIONS PAGE FAILED: expected saved values to persist across reopen {expected_after_save!r}, got {reloaded_options!r}")
                    return 1
                log(f"options page persists edits across a reopen -- OK ({reloaded_options!r})")
                options_page.close()

                # Search-history dropdown: the earlier bookmarks section
                # already ran one real search (args.query) through the popup's
                # own #query/#search-btn, which populates fs_search_history --
                # reused here rather than re-searching. Return from the
                # bookmarks view to the results list first (bookmarksToggleBtn
                # is a toggle: clicking it again switches back to _viewMode).
                log("checking the search-history dropdown: empty-focus shows it, clicking an entry re-runs that search")
                popup_page.click("#bookmarks-toggle-btn")
                popup_page.wait_for_timeout(150)
                popup_page.fill("#query", "")
                popup_page.click("#query")
                popup_page.wait_for_timeout(200)
                dropdown_state = popup_page.evaluate(
                    """() => ({
                        hidden: document.getElementById('query-history-dropdown').classList.contains('hidden'),
                        itemCount: document.querySelectorAll('#query-history-dropdown li').length,
                        firstText: (document.querySelector('#query-history-dropdown li') || {}).textContent,
                    })"""
                )
                if dropdown_state["hidden"] or dropdown_state["itemCount"] < 1:
                    log(f"SEARCH HISTORY FAILED: expected the dropdown visible with >=1 entry on empty-field focus, got {dropdown_state!r}")
                    return 1
                if dropdown_state["firstText"] != args.query:
                    log(f"SEARCH HISTORY FAILED: expected the dropdown's entry to be the earlier query {args.query!r}, got {dropdown_state!r}")
                    return 1
                log(f"search-history dropdown shows on empty-field focus -- OK ({dropdown_state!r})")

                popup_page.eval_on_selector("#status", "el => { el.textContent = ''; }")
                popup_page.click("#query-history-dropdown li:first-child")
                popup_page.wait_for_timeout(1000)
                after_click_state = popup_page.evaluate(
                    """() => ({
                        queryValue: document.getElementById('query').value,
                        dropdownHidden: document.getElementById('query-history-dropdown').classList.contains('hidden'),
                        status: document.getElementById('status').textContent,
                        resultCount: document.querySelectorAll('#results .result-item').length,
                    })"""
                )
                if after_click_state["queryValue"] != args.query or not after_click_state["dropdownHidden"]:
                    log(f"SEARCH HISTORY FAILED: expected clicking the entry to fill the query and hide the dropdown, got {after_click_state!r}")
                    return 1
                if after_click_state["resultCount"] < 1:
                    log(f"SEARCH HISTORY FAILED: expected clicking the entry to actually re-run the search and render results, got {after_click_state!r}")
                    return 1
                log(f"clicking a search-history entry re-runs that search -- OK ({after_click_state!r})")

                # Recently-opened papers: fresh popup shows the current paper,
                # a search hides it. fs_recent_papers is still empty at this
                # point in the script (fetch_match() talked to the backend
                # directly over httpx, never through the popup's own
                # processTab()/processArxiv(), so recordRecentPaper() has never
                # actually fired yet) -- seeded for real via the same
                # recordRecentPaper() the real cold path calls, rather than
                # driving processTab()'s own file:// PDF fetch (which a freshly
                # unpacked/loaded extension has no "Allow access to file URLs"
                # permission for, an orthogonal environment limitation this
                # check has no need to depend on). The backend is separately
                # re-tagged with this tab's real file:// url below so the
                # warm-cache fast-path check right after this can find a
                # matching document.
                log("checking the recently-opened-papers list: fresh popup shows it, a search hides it")
                pdf_file_uri = pdf_path.as_uri()
                with httpx.Client(timeout=60) as client:
                    with pdf_path.open("rb") as f:
                        retag_resp = client.post(
                            f"{args.backend_url}/process",
                            data={"url": pdf_file_uri},
                            files={"file": (pdf_path.name, f, "application/pdf")},
                        )
                    retag_resp.raise_for_status()
                    seed_formula_count = retag_resp.json().get("formula_count")
                log(f"re-tagged backend state with url={pdf_file_uri!r} so /status matches this tab -- formula_count={seed_formula_count}")

                seed_state = popup_page.evaluate(
                    """async ([tabId, url, formulaCount]) => {
                        _activeTab = await chrome.tabs.get(tabId);
                        // The search-history-dropdown check just above left a
                        // real search active (queryInput.value='=' and
                        // _matches populated) -- renderRecentPapers() now
                        // deliberately no-ops while a search is in flight/has
                        // results (final whole-branch review, Finding #3: it
                        // used to be able to flash back over search results).
                        // Reset both here, via the page's own globals rather
                        // than Playwright's .fill() (which would also fire
                        // the 'input' listener and pop the history dropdown),
                        // so this check's "fresh popup" precondition is real
                        // and isn't just testing stale state from the
                        // previous check.
                        queryInput.value = '';
                        _matches = [];
                        await recordRecentPaper(url, formulaCount);
                        const papers = await loadRecentPapers();
                        renderRecentPapers(papers);
                        return {
                            hidden: recentPapersEl.classList.contains('hidden'),
                            itemCount: recentPapersEl.querySelectorAll('li').length,
                            text: recentPapersEl.textContent,
                            // recordRecentPaper() stores _activeTab.title (falling
                            // back to the url) as the entry's `title` -- capture
                            // the exact string renderRecentPapers() actually puts
                            // in the DOM (via .recent-paper-title) here, once,
                            // rather than assuming it equals pdf_path.name. Reused
                            // below by the fast-path check to confirm the
                            // re-rendered entry corresponds to *this* paper, not
                            // just that some entry happens to be present.
                            title: (_activeTab && _activeTab.title) || url,
                        };
                    }""",
                    [tab_id, pdf_file_uri, seed_formula_count],
                )
                if seed_state["hidden"] or seed_state["itemCount"] < 1:
                    log(f"RECENT PAPERS FAILED: expected the list visible with >=1 item right after recording this paper, got {seed_state!r}")
                    return 1
                if seed_state["title"] not in seed_state["text"]:
                    log(f"RECENT PAPERS FAILED: expected the rendered entry to include this paper's title {seed_state['title']!r}, got text={seed_state['text']!r}")
                    return 1
                log(f"recently-opened-papers list renders after recording a paper -- OK ({seed_state!r})")
                shot(popup_page, "12_recent_papers_shown")

                popup_page.fill("#query", args.query)
                popup_page.click("#search-btn")
                popup_page.wait_for_selector("#results .result-item", timeout=15000)
                hidden_after_search = popup_page.eval_on_selector(
                    "#recent-papers", "el => el.classList.contains('hidden')"
                )
                if not hidden_after_search:
                    log("RECENT PAPERS FAILED: expected the list to hide once a search is run, it is still visible")
                    return 1
                log("recently-opened-papers list hides once a search is run -- OK")

                # The single most important check in this task: Task 6 fixed a
                # bug where loadRecentPapers()/renderRecentPapers() weren't
                # reached when init() takes its warm-cache fast path (backend
                # /status already reports the doc loaded, so
                # processArxiv()/processTab() are never called) -- fixed at the
                # store-test level only, never against a real running
                # extension+backend. Reproduced here as close to the real user
                # flow as this harness allows: reload the SAME popup tab fresh
                # (a real popup close+reopen fully destroys and recreates its
                # page too) while the PDF tab is the window's real active tab,
                # so init()'s own chrome.tabs.query({active, currentWindow})
                # -- not a reflection-based override like the bookmarks section
                # above uses -- genuinely resolves to the PDF tab, exactly like
                # a real popup open would. The backend already reports this
                # exact url as loaded (re-tagged above), so this is expected to
                # take the fast path. Proven with a real signal, not just
                # "the list rendered" (which the cold path would also produce
                # eventually): a network listener confirms no POST /process
                # request happens at all -- only the fast path skips it.
                process_requests: list[str] = []

                def _on_request(request):
                    if request.url.startswith(f"{args.backend_url}/process"):
                        process_requests.append(request.url)

                popup_page.on("request", _on_request)
                try:
                    # fs_last_query (formulaSearchLastQuery, chrome.storage.
                    # session with a chrome.storage.local fallback) would
                    # otherwise make the reload's automatic restoreLastQuery()
                    # fire a second, unrelated search right after init()
                    # finishes -- runSearch() unconditionally re-hides
                    # #recent-papers, which would make this fast-path check
                    # observe a false "hidden" state that has nothing to do
                    # with the bug it exists to catch. Cleared from both
                    # storage areas so init()'s own effect can be observed
                    # cleanly.
                    popup_page.evaluate(
                        """async () => {
                            const key = 'formulaSearchLastQuery';
                            if (chrome.storage.session) await chrome.storage.session.remove(key).catch(() => {});
                            await chrome.storage.local.remove(key).catch(() => {});
                        }"""
                    )
                    pdf_page.bring_to_front()
                    time.sleep(0.3)
                    popup_page.reload(wait_until="load")
                    time.sleep(1.5)
                    fastpath_state = popup_page.evaluate(
                        """() => ({
                            hidden: recentPapersEl.classList.contains('hidden'),
                            itemCount: recentPapersEl.querySelectorAll('li').length,
                            text: recentPapersEl.textContent,
                            status: statusEl.textContent,
                            queryDisabled: queryInput.disabled,
                            sourceUrl: typeof _sourceUrl !== 'undefined' ? _sourceUrl : null,
                        })"""
                    )
                finally:
                    popup_page.remove_listener("request", _on_request)

                log(f"post-reload popup state: {fastpath_state!r}")
                if process_requests:
                    log(f"FAST PATH FAILED: init() issued a POST /process on reopen even though the backend already reported this exact document loaded -- the cold path ran instead of the fast path: {process_requests!r}")
                    return 1
                log("confirmed init() took the warm-cache fast path on this reopen -- no POST /process request was made")
                if fastpath_state["hidden"] or fastpath_state["itemCount"] < 1:
                    log(f"RECENT PAPERS FAST-PATH FAILED: expected the recently-opened-papers list still visible with >=1 item after a fresh popup open hit the warm-cache fast path, got {fastpath_state!r}")
                    return 1
                # Confirm the re-rendered entry is genuinely *this* paper, not
                # just that some entry (possibly stale/unrelated) happens to be
                # present. renderRecentPapers() puts each entry's `title` (never
                # the raw url) into .recent-paper-title -- checked here against
                # seed_state["title"], the exact string captured straight off
                # the same _activeTab.title recordRecentPaper() used when this
                # entry was created above. An earlier version of this assertion
                # compared the raw file:// url instead (never actually rendered,
                # so it could never truly confirm a match) and was guarded by a
                # redundant `and not fastpath_state["itemCount"]` that the
                # itemCount < 1 check above already rules out -- making that
                # whole branch unreachable dead code. Fixed to an unconditional,
                # reachable assertion on a field the code actually renders.
                if seed_state["title"] not in fastpath_state["text"]:
                    log(f"RECENT PAPERS FAST-PATH FAILED: list rendered but did not appear to include this paper (expected title {seed_state['title']!r} in text), got {fastpath_state!r}")
                    return 1
                log(f"recently-opened-papers list still renders on a fresh popup open that hits the fast path -- OK (Task 6's fix holds) ({fastpath_state!r})")
                shot(popup_page, "13_recent_papers_after_fastpath_reload")

                # This reload also naturally re-exercises loadSettings() for
                # real (not via reflection), so the saved dark theme and
                # backendUrl should both show up on the popup too.
                popup_theme = popup_page.evaluate("() => document.documentElement.getAttribute('data-theme')")
                if popup_theme != "dark":
                    log(f"THEME FAILED: expected the popup to pick up the saved dark theme after reopening, got data-theme={popup_theme!r}")
                    return 1
                log("popup picks up the saved theme setting on reopen -- OK")

                # maxChatHistory cap: send more messages than CHAT_CAP (saved
                # above) through the popup's own chat view and confirm the
                # rendered/stored history is trimmed down to exactly CHAT_CAP,
                # keeping the newest and dropping the oldest -- using this
                # very popup_page, whose BACKEND/search state is now fully
                # real (set by the reload's own loadSettings()+init(), not by
                # reflection), so #chat-input/#chat-send-btn work exactly as a
                # real user's would.
                log(f"checking maxChatHistory caps live chat history at the configured value ({CHAT_CAP})")
                popup_page.click("#chat-toggle-btn")
                popup_page.wait_for_timeout(200)
                cap_probe_messages = [f"cap test message {i}" for i in range(1, 6)]
                for msg in cap_probe_messages:
                    popup_page.fill("#chat-input", msg)
                    popup_page.click("#chat-send-btn")
                    popup_page.wait_for_timeout(250)
                cap_state = popup_page.evaluate(
                    """() => ({
                        bubbleCount: document.querySelectorAll('#chat-messages .chat-msg').length,
                        texts: Array.from(document.querySelectorAll('#chat-messages .chat-msg-text')).map((el) => el.textContent),
                    })"""
                )
                log(f"chat state after sending {len(cap_probe_messages)} messages with cap={CHAT_CAP}: {cap_state!r}")
                if cap_state["bubbleCount"] != CHAT_CAP:
                    log(f"MAX CHAT HISTORY FAILED: expected exactly {CHAT_CAP} messages rendered after exceeding the cap, got {cap_state['bubbleCount']}: {cap_state['texts']!r}")
                    return 1
                if not any(cap_probe_messages[-1] in t for t in cap_state["texts"]):
                    log(f"MAX CHAT HISTORY FAILED: expected the newest message to survive trimming, got {cap_state['texts']!r}")
                    return 1
                if any(cap_probe_messages[0] in t for t in cap_state["texts"]) or any(cap_probe_messages[1] in t for t in cap_state["texts"]):
                    log(f"MAX CHAT HISTORY FAILED: expected the oldest messages to be trimmed away, got {cap_state['texts']!r}")
                    return 1
                log(f"maxChatHistory={CHAT_CAP} correctly caps live chat history, keeping the newest -- OK ({cap_state['texts']!r})")
                shot(popup_page, "14_chat_history_capped")

                # ====================================================================
                # Task 6 (batch-b-discovery-ui): live verification of B1 (badge),
                # B2 (copy-as-markdown), B3 (context-menu search), B4 (keyboard
                # shortcuts), B5 (export) -- Batch B's five discovery/UI-polish
                # features. None of these could be meaningfully unit-tested against
                # the fake DOM (tools/js_fake_dom.js): they depend on real
                # chrome.action.getBadgeText, real OS clipboard/keyboard/download
                # behavior, and (for B3) a real running service worker, none of
                # which the fake DOM simulates. Runs last, reusing popup_page/
                # pdf_page/tab_id/ext_id/args already established above -- by this
                # point init() has already run for real via the warm-cache
                # fast-path-reload check earlier in this script, so _activeTab/
                # _sourceUrl/BACKEND are all genuine, not reflected-in.
                # ====================================================================
                log("=== Batch B live verification: badge / copy-as-markdown / context-menu search / keyboard shortcuts / export ===")

                # The maxChatHistory check just above left the popup showing the
                # chat view (#chat-toggle-btn was the last thing clicked), which
                # keeps #results under a 'hidden' class regardless of _viewMode
                # (see showView() in popup.js) -- switch back to the results list
                # first so the searches below actually render visibly.
                popup_page.evaluate("() => { _viewMode = 'list'; showView('list'); }")
                popup_page.wait_for_timeout(150)

                # --- B1: toolbar match-count badge --------------------------------
                log("[B1] badge: a real search with results should set the badge text to the match count")
                popup_page.fill("#query", args.query)
                popup_page.click("#search-btn")
                popup_page.wait_for_selector("#results .result-item", timeout=15000)
                popup_page.wait_for_timeout(300)  # let the searchComplete message + updateBadge() settle
                match_count = popup_page.evaluate("() => _matches.length")
                badge_text = popup_page.evaluate("async () => await chrome.action.getBadgeText({})")
                if badge_text != str(match_count):
                    log(f"BADGE FAILED: expected the badge to show the match count {match_count!r} after a real search, got {badge_text!r}")
                    return 1
                log(f"badge shows the match count after a real search -- OK (matches={match_count}, badge={badge_text!r})")

                log("[B1] badge: a zero-result search should clear the badge")
                popup_page.fill("#query", "zzzznonexistentformulazzzz12345")
                popup_page.click("#search-btn")
                popup_page.wait_for_timeout(600)
                zero_match_count = popup_page.evaluate("() => _matches.length")
                badge_after_zero = popup_page.evaluate("async () => await chrome.action.getBadgeText({})")
                if zero_match_count != 0:
                    log(f"BADGE FAILED (test precondition): expected the nonsense query to return 0 matches, got {zero_match_count}")
                    return 1
                if badge_after_zero != "":
                    log(f"BADGE FAILED: expected the badge to clear after a zero-result search, got {badge_after_zero!r}")
                    return 1
                log("badge clears after a zero-result search -- OK")

                log("[B1] badge: a fresh /process should clear the badge immediately, before any results come back")
                popup_page.fill("#query", args.query)
                popup_page.click("#search-btn")
                popup_page.wait_for_selector("#results .result-item", timeout=15000)
                popup_page.wait_for_timeout(300)
                badge_before_reprocess = popup_page.evaluate("async () => await chrome.action.getBadgeText({})")
                if badge_before_reprocess == "":
                    log(f"BADGE FAILED (test precondition): expected a nonzero badge before re-processing, got {badge_before_reprocess!r}")
                    return 1
                # processTab()'s FIRST statement sends searchComplete/matchCount:0
                # (Task 1) -- calling the real function by name (the same
                # reflection this script already relies on for recordRecentPaper/
                # loadRecentPapers/renderRecentPapers above) exercises that real
                # code path. The rest of processTab() may go on to fail (a freshly
                # loaded unpacked extension has no "Allow access to file URLs"
                # permission, so its file:// fetch will likely throw) -- caught and
                # ignored, since only the badge-clearing side effect at the top is
                # what this check cares about.
                popup_page.evaluate(
                    """async () => {
                        try { await processTab(_sourceUrl); } catch (e) { /* ignore -- see comment above */ }
                    }"""
                )
                popup_page.wait_for_timeout(300)
                badge_after_reprocess = popup_page.evaluate("async () => await chrome.action.getBadgeText({})")
                if badge_after_reprocess != "":
                    log(f"BADGE FAILED: expected the badge to clear the moment a fresh /process began, got {badge_after_reprocess!r}")
                    return 1
                log("badge clears the moment a fresh /process begins -- OK")

                # --- B2: copy-as-markdown -------------------------------------------
                log("[B2] copy-as-markdown: click a result's copy button, confirm the checkmark feedback, and read the clipboard")
                popup_page.fill("#query", args.query)
                popup_page.click("#search-btn")
                popup_page.wait_for_selector("#results .result-item", timeout=15000)
                expected_copy_text = popup_page.evaluate(
                    """() => {
                        const m = _matches[0];
                        const pageText = m.page >= 0 ? `page ${m.page + 1}` : 'no page map';
                        const sectionText = m.section || 'untitled section';
                        return `$${m.latex || ''}$ — ${pageText}, ${sectionText}`;
                    }"""
                )
                copy_selector = "#results .result-item:first-child .result-copy-btn"
                popup_page.click(copy_selector)
                popup_page.wait_for_timeout(150)
                copy_btn_text = popup_page.eval_on_selector(copy_selector, "el => el.textContent")
                if copy_btn_text != "✓":
                    log(f"COPY-AS-MARKDOWN FAILED: expected the checkmark feedback glyph after clicking copy, got {copy_btn_text!r}")
                    return 1
                log("copy button shows the checkmark feedback -- OK")
                try:
                    clipboard_text = popup_page.evaluate("() => navigator.clipboard.readText()")
                except Exception as exc:
                    log(f"SKIPPED clipboard content check for copy-as-markdown -- could not read the clipboard back in this environment (not a copy failure, just unverifiable here): {exc}")
                else:
                    if clipboard_text != expected_copy_text:
                        log(f"COPY-AS-MARKDOWN FAILED: expected the clipboard to hold {expected_copy_text!r}, got {clipboard_text!r}")
                        return 1
                    log(f"clipboard holds the expected '$<latex>$ — page N, section' text -- OK ({clipboard_text!r})")

                # --- B3: context-menu search -----------------------------------------
                log("[B3] context-menu search: a real native OS right-click context menu cannot be driven by Playwright/CDP at all, "
                    "and Chrome's extension APIs expose no way to invoke a registered chrome.contextMenus.onClicked listener from "
                    "outside either -- so this verifies the underlying mechanism instead: writing fs_pending_query via the real "
                    "chrome.storage API, executed inside the extension's actual RUNNING service worker (not a fake/mocked one), "
                    "the same key/value the real onClicked handler writes, then confirming a fresh popup open consumes it. "
                    "The menu-click routing itself (info.selectionText -> onClicked -> this write) is NOT exercised -- see the report.")
                service_workers = context.service_workers
                sw = next((w for w in service_workers if ext_id in w.url), None)
                if not sw:
                    try:
                        sw = context.wait_for_event("serviceworker", timeout=5000)
                    except Exception:
                        sw = None
                if not sw:
                    log("CONTEXT-MENU FAILED: could not locate the extension's real running service worker via context.service_workers")
                    return 1
                log(f"found the extension's real running service worker: {sw.url}")
                # Reuses args.query (guaranteed by fetch_match() at the top of
                # this script to return at least one real backend match) rather
                # than a fabricated LaTeX string, so the "fresh popup consumes
                # it and runs the search" assertion below can check for actual
                # rendered results, not just an empty-but-attempted search.
                context_menu_text = args.query
                sw.evaluate(
                    "(text) => chrome.storage.local.set({ fs_pending_query: text })",
                    context_menu_text,
                )
                pending_after_write = popup_page.evaluate(
                    "async () => (await chrome.storage.local.get('fs_pending_query')).fs_pending_query"
                )
                if pending_after_write != context_menu_text:
                    log(f"CONTEXT-MENU FAILED: expected fs_pending_query to hold {context_menu_text!r} right after the real service worker wrote it, got {pending_after_write!r}")
                    return 1
                log(f"fs_pending_query written for real inside the extension's own service worker -- OK ({pending_after_write!r})")

                # "A fresh popup open" -- reload the SAME popup tab (this script's
                # established stand-in for closing/reopening the real toolbar
                # popup, same technique the warm-cache fast-path check above uses),
                # which re-runs init() -> ... -> consumePendingQuery() for real.
                # pdf_page must be the active tab first, same reasoning as the
                # fast-path check above: init()'s own chrome.tabs.query({active,
                # currentWindow}) needs to resolve to the PDF tab, not popup_page's
                # own tab.
                pdf_page.bring_to_front()
                time.sleep(0.3)
                popup_page.reload(wait_until="load")
                popup_page.wait_for_timeout(1500)
                consumed_state = popup_page.evaluate(
                    """async () => ({
                        queryValue: document.getElementById('query').value,
                        resultCount: document.querySelectorAll('#results .result-item').length,
                        pendingAfter: (await chrome.storage.local.get('fs_pending_query')).fs_pending_query,
                    })"""
                )
                log(f"popup state after a fresh open: {consumed_state!r}")
                if consumed_state["queryValue"] != context_menu_text:
                    log(f"CONTEXT-MENU FAILED: expected the fresh popup to fill the query box with {context_menu_text!r}, got {consumed_state['queryValue']!r}")
                    return 1
                if consumed_state["pendingAfter"]:
                    log(f"CONTEXT-MENU FAILED: expected fs_pending_query to be cleared once consumed, got {consumed_state['pendingAfter']!r}")
                    return 1
                if consumed_state["resultCount"] < 1:
                    log(f"CONTEXT-MENU FAILED: expected the fresh popup to have actually run the search (not just filled the box), got {consumed_state!r}")
                    return 1
                log("a fresh popup open consumed the pending query: filled the search box, ran the search, and cleared the storage key -- OK")

                # --- B4: keyboard shortcuts -------------------------------------------
                log("[B4] keyboard shortcuts: dispatching the REAL OS-level key combo (not a synthetic call into the message "
                    "handler) -- this is the item the design spec flagged as a genuine open question")
                if not sys.platform.startswith("win"):
                    log("SKIPPED [B4] -- no OS-level keystroke helper implemented for this platform (this harness's send_os_keystroke is Windows-only, via PowerShell SendKeys)")
                else:
                    window_title_needle = pdf_path.name

                    # next-result already has a default suggested_key (Alt+Down per
                    # manifest.json) -- no manual setup needed.
                    popup_page.fill("#query", args.query)
                    popup_page.click("#search-btn")
                    popup_page.wait_for_selector("#results .result-item", timeout=15000)
                    popup_page.evaluate("() => { _viewMode = 'step'; renderResults(); }")
                    popup_page.wait_for_timeout(200)
                    step_before = popup_page.evaluate("() => _stepIndex")
                    displayed_before = popup_page.evaluate(
                        "() => { const el = document.querySelector('#results .result-item .formula-preview'); return el ? el.textContent : null; }"
                    )
                    pdf_page.bring_to_front()
                    time.sleep(0.3)
                    send_os_keystroke(window_title_needle, "%{DOWN}", log)
                    time.sleep(1.2)
                    step_after = popup_page.evaluate("() => _stepIndex")
                    displayed_after = popup_page.evaluate(
                        "() => { const el = document.querySelector('#results .result-item .formula-preview'); return el ? el.textContent : null; }"
                    )
                    log(f"next-result (real OS Alt+Down): _stepIndex {step_before} -> {step_after}, displayed result changed={displayed_before != displayed_after}")
                    if step_after != step_before:
                        log("KEYBOARD SHORTCUT [next-result] FIRED -- OK: a real, OS-dispatched Alt+Down changed _stepIndex and the displayed result")
                    else:
                        log("KEYBOARD SHORTCUT [next-result] DID NOT FIRE: _stepIndex was unchanged after a real, OS-level Alt+Down was dispatched to the focused Chrome window. "
                            "This is a genuine negative result, not a script bug -- see task-6-report.md for the full investigation.")

                    # toggle-chat has NO suggested_key in manifest.json -- a real
                    # user would have to bind one by hand via chrome://extensions/
                    # shortcuts before this command has any shortcut at all.
                    # Assigned here through that real settings UI, the same way a
                    # real user would.
                    assigned = assign_toggle_chat_shortcut(context, "Alt+C")
                    log(f"assigned toggle-chat's shortcut via chrome://extensions/shortcuts -- Chrome recorded: {assigned!r}")

                    popup_page.evaluate("() => { showView('list'); }")
                    popup_page.wait_for_timeout(150)
                    popup_page.evaluate("async () => { await chrome.storage.local.set({ fs_pending_toggle_chat: false }); }")
                    chat_hidden_before = popup_page.evaluate("() => document.getElementById('chat-view').classList.contains('hidden')")
                    pdf_page.bring_to_front()
                    time.sleep(0.3)
                    send_os_keystroke(window_title_needle, "%c", log)
                    time.sleep(1.5)
                    chat_hidden_after = popup_page.evaluate("() => document.getElementById('chat-view').classList.contains('hidden')")
                    flag_after = popup_page.evaluate(
                        "async () => (await chrome.storage.local.get('fs_pending_toggle_chat')).fs_pending_toggle_chat"
                    )
                    log(f"toggle-chat (real OS Alt+C, popup already open): chatHidden {chat_hidden_before} -> {chat_hidden_after}, fs_pending_toggle_chat afterward={flag_after!r}")
                    if chat_hidden_before and not chat_hidden_after:
                        log("KEYBOARD SHORTCUT [toggle-chat] FIRED AND LIVE-RELAYED -- OK: a real, OS-dispatched Alt+C opened chat in the already-open popup, and the durable flag was cleared by the relay handler")
                    elif flag_after:
                        log("KEYBOARD SHORTCUT [toggle-chat] FIRED BUT NOT LIVE-RELAYED: chrome.commands.onCommand ran (fs_pending_toggle_chat was left true, the durable fallback) but the already-open popup's live relay did not flip it to chat in time. "
                            "This is exactly the race the design spec flagged as an open question -- see task-6-report.md.")
                    else:
                        log("KEYBOARD SHORTCUT [toggle-chat] DID NOT FIRE: neither the chat view nor the durable flag changed after a real, OS-level Alt+C. "
                            "This is a genuine negative result, not a script bug -- see task-6-report.md for the full investigation.")

                # --- B5: export results -----------------------------------------------
                # B4's toggle-chat check (immediately above) may have actually fired the
                # real OS-level Alt+C keystroke and opened the chat view live (its own
                # log line says whether it did) -- showView('chat') hides #results via
                # the 'hidden' class, same as the [B1]/badge section's own leading
                # `_viewMode = 'list'; showView('list');` reset guards against. Without
                # this, the wait_for_selector below can find a real-but-hidden
                # `.result-item` and time out. Whether B4's keystroke fires at all is
                # environment-dependent (see its own comments), so this reset must not
                # assume either outcome.
                popup_page.evaluate("() => { _viewMode = 'list'; showView('list'); }")
                popup_page.wait_for_timeout(150)
                log("[B5] export: the export button is disabled with no results, and enabled + triggers a correctly-named/shaped download once there are results")
                popup_page.fill("#query", "zzzznonexistentformulazzzz12345")
                popup_page.click("#search-btn")
                popup_page.wait_for_timeout(600)
                export_disabled_zero = popup_page.eval_on_selector("#export-btn", "el => el.disabled")
                if not export_disabled_zero:
                    log("EXPORT FAILED: expected #export-btn to be disabled when there are no results")
                    return 1
                log("export button is disabled with zero results -- OK")

                popup_page.fill("#query", args.query)
                popup_page.click("#search-btn")
                popup_page.wait_for_selector("#results .result-item", timeout=15000)
                export_disabled_nonzero = popup_page.eval_on_selector("#export-btn", "el => el.disabled")
                if export_disabled_nonzero:
                    log("EXPORT FAILED: expected #export-btn to be enabled once there are results")
                    return 1
                log("export button is enabled once there are results -- OK")

                # Thin pass-through wrapper around the REAL chrome.downloads.
                # download() -- still calls straight through to the original --
                # purely to observe the exact {filename, ...} object the real
                # button-click handler passes. Necessary because Playwright's
                # automated download handling (which suppresses the native "Save
                # As" dialog chrome.downloads.download({saveAs:true}) would
                # otherwise show, so this check doesn't hang on it) independently
                # substitutes its own generated filename for the artifact actually
                # saved to disk -- confirmed via chrome.downloads.search() showing
                # a Playwright temp path, not the extension's requested name, as
                # the real saved filename. That's a Playwright/CDP automation
                # quirk, not a bug in the extension: this wrapper reads the
                # filename the real code actually asked for, independent of what
                # the automation layer did with it afterward. See task-6-report.md.
                popup_page.evaluate(
                    """() => {
                        window.__lastDownloadArgs = null;
                        const orig = chrome.downloads.download.bind(chrome.downloads);
                        chrome.downloads.download = (opts, cb) => { window.__lastDownloadArgs = opts; return orig(opts, cb); };
                    }"""
                )
                try:
                    with popup_page.expect_download(timeout=8000) as dl_info:
                        popup_page.click("#export-btn")
                    download = dl_info.value
                    downloaded_text = Path(download.path()).read_text(encoding="utf-8")
                    downloaded_json = json.loads(downloaded_text)
                except Exception as exc:
                    log(f"EXPORT FAILED: clicking #export-btn did not produce a catchable download: {exc}")
                    return 1

                real_download_args = popup_page.evaluate("() => window.__lastDownloadArgs") or {}
                log(f"real chrome.downloads.download() call args (captured via pass-through wrapper): {real_download_args!r}")
                filename = real_download_args.get("filename", "")
                if not re.match(r"^formula-search-results-\d+\.json$", filename):
                    log(f"EXPORT FAILED: expected the real download filename to match formula-search-results-<timestamp>.json, got {filename!r}")
                    return 1
                log(f"the real chrome.downloads.download() call used the correct filename pattern -- OK ({filename!r})")

                expected_matches = popup_page.evaluate("() => _matches")
                if downloaded_json != expected_matches:
                    log(f"EXPORT FAILED: downloaded JSON content does not match the current _matches -- got {len(downloaded_json)} items vs {len(expected_matches)} expected")
                    return 1
                log(f"downloaded content parses as JSON and matches the current results exactly -- OK ({len(downloaded_json)} matches)")

                log("=== Batch B live verification complete ===")

                # ====================================================================
                # Task 4 (batch-c-backend-capabilities): live verification of C1 (OCR
                # wiring) and C2 (multi-document backend sessions), plus a check that
                # real /search requests carry Task 2's `url` field and that Task 3's
                # OCR controls are disabled/enabled at the right times. Runs last,
                # reusing popup_page/pdf_page/tab_id/ext_id/args already established
                # above -- by this point currentSessionUrl is genuinely set via a real
                # init()/processTab() run (the warm-cache fast-path reload earlier in
                # this script), not reflected in.
                #
                # Building this section found a real bug (fixed in popup.js, not in
                # this script): init()'s warm-cache fast path -- the branch taken on
                # every popup reopen once a document is already processed, the single
                # most common real flow -- never set currentSessionUrl, so /search and
                # /ocr-page would send url:'' after a plain popup reopen. Fixed by
                # assigning currentSessionUrl = _sourceUrl in both `sameDocument`
                # branches of init(). The C1 url-field assertion below would have
                # failed without that fix.
                # ====================================================================
                log("=== Task 4 live verification: OCR wiring (C1) / multi-session isolation (C2) ===")

                # --- disabled-before-processed / enabled-after-processed (Task 3 Step 5) ---
                # A brand new tab navigated straight to popup.html becomes the
                # window's own active tab (see the bookmarks section's comment above
                # for why -- chrome.tabs.query({active,currentWindow}) then resolves
                # to the popup's own chrome-extension:// url, not a PDF), so its
                # init() takes the "No PDF detected" branch and never calls
                # enableSearch() -- leaving the OCR controls at their initial
                # `= true` disabled state. A real, independent popup instance, not a
                # reflection-based override.
                log("[C1 pre-check] #ocr-page-input/#ocr-run-btn must start disabled on a popup with no document processed")
                fresh_popup = context.new_page()
                fresh_popup.goto(f"chrome-extension://{ext_id}/popup/popup.html", wait_until="load")
                time.sleep(1.0)
                fresh_ocr_state = fresh_popup.evaluate(
                    """() => ({
                        inputDisabled: document.getElementById('ocr-page-input').disabled,
                        btnDisabled: document.getElementById('ocr-run-btn').disabled,
                        status: document.getElementById('status').textContent,
                    })"""
                )
                fresh_popup.close()
                if not fresh_ocr_state["inputDisabled"] or not fresh_ocr_state["btnDisabled"]:
                    log(f"OCR WIRING FAILED: expected #ocr-page-input/#ocr-run-btn disabled on a fresh popup with no PDF active, got {fresh_ocr_state!r}")
                    return 1
                log(f"#ocr-page-input/#ocr-run-btn start disabled with no document processed -- OK ({fresh_ocr_state!r})")

                log("[C1 pre-check] #ocr-page-input/#ocr-run-btn must be enabled on the already-processed popup")
                enabled_state = popup_page.evaluate(
                    """() => ({
                        inputDisabled: document.getElementById('ocr-page-input').disabled,
                        btnDisabled: document.getElementById('ocr-run-btn').disabled,
                        currentSessionUrl: typeof currentSessionUrl !== 'undefined' ? currentSessionUrl : null,
                    })"""
                )
                if enabled_state["inputDisabled"] or enabled_state["btnDisabled"]:
                    log(f"OCR WIRING FAILED: expected #ocr-page-input/#ocr-run-btn enabled once search is enabled, got {enabled_state!r}")
                    return 1
                log(f"#ocr-page-input/#ocr-run-btn are enabled on the processed document's popup -- OK ({enabled_state!r})")

                # --- C1: real POST /ocr-page fires with the correct url field --------
                log("[C1] typing a page number and clicking #ocr-run-btn must fire a real POST /ocr-page with url=currentSessionUrl")
                expected_session_url = enabled_state["currentSessionUrl"]
                if not expected_session_url:
                    log(f"OCR WIRING FAILED (test precondition): currentSessionUrl is empty/falsy on the processed popup, got {expected_session_url!r} -- cannot verify the request's url field")
                    return 1
                log(f"expecting /ocr-page requests to carry url={expected_session_url!r} (the real currently-loaded document's URL)")

                ocr_requests: list[dict[str, Any]] = []

                def _on_ocr_request(request):
                    if request.url.startswith(f"{args.backend_url}/ocr-page"):
                        try:
                            body = json.loads(request.post_data or "{}")
                        except Exception:
                            body = {"_unparseable_post_data": request.post_data}
                        ocr_requests.append(body)

                popup_page.on("request", _on_ocr_request)
                try:
                    popup_page.eval_on_selector("#status", "el => { el.textContent = ''; }")
                    popup_page.fill("#ocr-page-input", "1")
                    popup_page.click("#ocr-run-btn")
                    popup_page.wait_for_timeout(2000)
                finally:
                    popup_page.remove_listener("request", _on_ocr_request)

                if not ocr_requests:
                    log("OCR WIRING FAILED: clicking #ocr-run-btn did not produce a POST /ocr-page request at all")
                    return 1
                ocr_body = ocr_requests[0]
                log(f"real POST /ocr-page body captured: {ocr_body!r}")
                if ocr_body.get("url") != expected_session_url:
                    log(f"OCR WIRING FAILED: expected the request's JSON body url field to be {expected_session_url!r} (the currently-loaded document), got {ocr_body.get('url')!r}")
                    return 1
                log(f"POST /ocr-page fires with the correct url field -- OK (url={ocr_body.get('url')!r}, page={ocr_body.get('page')!r})")

                ocr_status_after = popup_page.eval_on_selector("#status", "el => el.textContent")
                log(f"#status after the OCR run: {ocr_status_after!r} -- NOTE: this environment does not have pix2tex "
                    "installed (see tests/test_ocr_page.py's own SKIPPED happy-path test, and backend/extractor/"
                    "ocr_page.py's `from pix2tex.cli import LatexOCR` import), so the backend's OCR call itself fails "
                    "with an uncaught 500 here -- the popup's \"OCR'd page <n> -- found <k> new formulas\" success-text "
                    "format is NOT asserted by this run (only the request wiring/shape is). Confirmed directly against "
                    "the real backend during this task that the 500's plain-text body degrades to the client's own "
                    "'Server error 500' fallback text, which is not itself a wiring bug.")

                # --- C1: verbatim 400 detail when the session has no PDF bytes -------
                log("[C1] OCR against a session with no PDF bytes must surface the backend's verbatim 400 detail")
                never_processed_url = "http://local-test/never-processed-for-ocr-c1.pdf"
                popup_page.evaluate("(u) => { currentSessionUrl = u; }", never_processed_url)
                popup_page.eval_on_selector("#status", "el => { el.textContent = ''; }")
                popup_page.fill("#ocr-page-input", "1")
                popup_page.click("#ocr-run-btn")
                popup_page.wait_for_timeout(1200)
                no_pdf_status = popup_page.eval_on_selector("#status", "el => el.textContent")
                expected_400_detail = (
                    "No PDF bytes available. Open the PDF in Chrome and process it from the popup, "
                    "or upload/process a PDF first."
                )
                if no_pdf_status != expected_400_detail:
                    log(f"OCR WIRING FAILED: expected #status to show the backend's verbatim 400 detail {expected_400_detail!r}, got {no_pdf_status!r}")
                    return 1
                log(f"#status shows the backend's exact 400 detail for a PDF-bytes-less session -- OK ({no_pdf_status!r})")
                popup_page.evaluate("(u) => { currentSessionUrl = u; }", expected_session_url)
                log("restored currentSessionUrl to the real processed document for the remaining checks")

                # --- Task 2: /search also carries the url field, via a real popup search ---
                log("[support check] confirming a real popup #search-btn click sends url=currentSessionUrl on /search too")
                search_requests: list[dict[str, Any]] = []

                def _on_search_request(request):
                    if request.url.startswith(f"{args.backend_url}/search"):
                        try:
                            body = json.loads(request.post_data or "{}")
                        except Exception:
                            body = {"_unparseable_post_data": request.post_data}
                        search_requests.append(body)

                popup_page.on("request", _on_search_request)
                try:
                    popup_page.fill("#query", args.query)
                    popup_page.click("#search-btn")
                    popup_page.wait_for_selector("#results .result-item", timeout=15000)
                finally:
                    popup_page.remove_listener("request", _on_search_request)

                if not search_requests:
                    log("SEARCH URL FIELD FAILED: clicking #search-btn did not produce a captured POST /search request")
                    return 1
                if search_requests[-1].get("url") != expected_session_url:
                    log(f"SEARCH URL FIELD FAILED: expected the real popup's /search body url field to be {expected_session_url!r}, got {search_requests[-1].get('url')!r}")
                    return 1
                log(f"real popup /search requests include the correct url field -- OK ({search_requests[-1]!r})")

                # --- C2: multi-document backend sessions stay isolated ----------------
                log("[C2] multi-document session isolation: verified directly against the real running backend over "
                    "HTTP, not through two real PDF tabs in the popup UI -- this script only ever opens one PDF tab, "
                    "and C2 is a backend-only concern per the brief, so a direct httpx check against the same real, "
                    "already-running backend process this whole script talks to is the more direct and equally "
                    "meaningful path. Two genuinely different real papers are used (not two urls for the same PDF), so "
                    "a false pass from accidental content overlap between the two sessions is not possible.")
                doc_a_url = "https://arxiv.org/abs/1706.03762"   # Attention Is All You Need
                doc_b_url = "https://arxiv.org/abs/1810.01382"   # Unbiased estimation of log normalizing constants...
                query_only_in_a = "softmax"
                query_only_in_b = "\\log Z"
                with httpx.Client(timeout=90) as c2_client:
                    resp_a = c2_client.post(f"{args.backend_url}/process", data={"url": doc_a_url})
                    resp_a.raise_for_status()
                    data_a = resp_a.json()
                    resp_b = c2_client.post(f"{args.backend_url}/process", data={"url": doc_b_url})
                    resp_b.raise_for_status()
                    data_b = resp_b.json()
                    log(f"C2: processed doc A ({doc_a_url}) -- {data_a!r}")
                    log(f"C2: processed doc B ({doc_b_url}) -- {data_b!r}")
                    if not data_a.get("formula_count") or not data_b.get("formula_count"):
                        log(f"MULTI-SESSION FAILED (test precondition): expected both documents to index a nonzero formula count, got A={data_a!r} B={data_b!r}")
                        return 1

                    status_a = c2_client.get(f"{args.backend_url}/status", params={"url": doc_a_url}).json()
                    status_b = c2_client.get(f"{args.backend_url}/status", params={"url": doc_b_url}).json()
                    log(f"C2: /status A={status_a!r} B={status_b!r}")
                    if status_a.get("formula_count") == status_b.get("formula_count"):
                        log(f"MULTI-SESSION FAILED (test precondition): expected distinct formula counts for two different real papers, both reported {status_a.get('formula_count')!r}")
                        return 1
                    if status_a.get("url") == status_b.get("url"):
                        log(f"MULTI-SESSION FAILED: /status reported the same url for both sessions, got A={status_a!r} B={status_b!r}")
                        return 1

                    matches_a_for_a = c2_client.post(f"{args.backend_url}/search", json={"query": query_only_in_a, "url": doc_a_url}).json()["matches"]
                    matches_a_for_b = c2_client.post(f"{args.backend_url}/search", json={"query": query_only_in_a, "url": doc_b_url}).json()["matches"]
                    log(f"C2: query {query_only_in_a!r} -- A gave {len(matches_a_for_a)} matches, B gave {len(matches_a_for_b)} matches")
                    if not matches_a_for_a:
                        log(f"MULTI-SESSION FAILED (test precondition): expected {query_only_in_a!r} to match at least one formula in doc A, got 0")
                        return 1
                    if matches_a_for_b:
                        log(f"MULTI-SESSION FAILED: doc A's formulas leaked into doc B's session -- query {query_only_in_a!r} (0 expected under B) returned {len(matches_a_for_b)} matches from B")
                        return 1

                    matches_b_for_b = c2_client.post(f"{args.backend_url}/search", json={"query": query_only_in_b, "url": doc_b_url}).json()["matches"]
                    matches_b_for_a = c2_client.post(f"{args.backend_url}/search", json={"query": query_only_in_b, "url": doc_a_url}).json()["matches"]
                    log(f"C2: query {query_only_in_b!r} -- B gave {len(matches_b_for_b)} matches, A gave {len(matches_b_for_a)} matches")
                    if not matches_b_for_b:
                        log(f"MULTI-SESSION FAILED (test precondition): expected {query_only_in_b!r} to match at least one formula in doc B, got 0")
                        return 1
                    if matches_b_for_a:
                        log(f"MULTI-SESSION FAILED: doc B's formulas leaked into doc A's session -- query {query_only_in_b!r} (0 expected under A) returned {len(matches_b_for_a)} matches from A")
                        return 1

                log(f"multi-document backend sessions stay fully isolated (bidirectional, two real distinct papers, direct HTTP against the live backend) -- OK "
                    f"(A: {status_a.get('formula_count')} formulas, B: {status_b.get('formula_count')} formulas)")

                log("=== Task 4 live verification complete ===")

            log(f"done. screenshots in {out_dir}")

            if args.keep_open:
                input("Browser left open -- press Enter here to close it...")
        finally:
            if not args.keep_open:
                context.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
