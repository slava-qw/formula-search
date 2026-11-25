# Contributing

Read [setup](docs/SETUP.md) and [architecture](docs/ARCHITECTURE.md). Keep notation
contracts, scalar assumptions, provenance, and location uncertainty explicit.

## Checks

Use Python 3.14, install `requirements-dev.txt`, and put Node.js 24 on `PATH`:

```powershell
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\python tools/check_extension.py
.\.venv\Scripts\python tools/check_publication.py
```

Default checks need no network. Missing external paper-dependent tests report
skips. Restore pinned fixtures for full document checks:

```powershell
.\.venv\Scripts\python tools/fetch_paper_fixtures.py
$env:RUN_PAPER_AUDITS='1'
$env:RUN_PAPER_AUDITS_RECOMPUTE='1'
.\.venv\Scripts\python -m pytest -q
```

Downloads are serial and SHA-256 checked. Do not replace expected hashes to hide
an upstream change. See [test-data details](testdata/README.md).

For browser checks, install Playwright Chromium (`python -m playwright install
chromium`) and desktop Chrome, then restore the corpus:

```powershell
python tools/smoke_search_browser.py --tex-toggles --completion --browser-channel chrome --out .media-work/chrome-check
python tools/smoke_search_browser.py --tex-toggles --completion --out .media-work/chromium-check
```

These use isolated profiles and a temporary backend. Popup/panel surfaces are
tested as extension pages, not OS shortcuts or toolbar gestures. Completion UI
smoke simulates recognition to test review/retry behavior;
`tools/verify_real_ocr.py` separately tests the actual model. See its help.
Historical baseline flags in imported audit helpers require the original
development history and are not public release checks.

## Changes and releases

Describe the problem, final behavior, and relevant checks. For extraction/mapping,
include a minimal case plus visually reviewed positive and negative locations.
Preserve paper versions and hashes. For browser work, check both restricted-viewer
fallbacks and the compatible native path. Never commit personal documents, caches,
weights, credentials, profiles, or local screenshots.

Use [the local export process](docs/PUBLISHING.md) for publication. Original-code
contributions use MIT; dependencies retain their [own terms](THIRD_PARTY_NOTICES.md).
