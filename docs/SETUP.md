# Setup and troubleshooting

The verified setup uses Windows, Python 3.14.3, Chrome 158.0.8127.37, and
Playwright Chromium 151.0.7922.34. Development checks also use Node 24.22.1.
Other operating systems have not had an equivalent acceptance run. This release
is an unpacked developer extension, with no Chrome Web Store installation.

## Core installation

Install Python 3.14, then run the README commands from the project root.
`requirements.txt` and `requirements-core-lock.txt` capture the verified Windows
dependencies; they are not a hash-locked cross-platform build. Python 3.14 is
required for the worker termination API used in deadline recovery.

Start with `./start.ps1`, or use the interpreter directly:

```powershell
.\.venv\Scripts\python -m uvicorn backend.main:app --host 127.0.0.1 --port 7878
```

The direct command also works when PowerShell blocks scripts, without changing
machine execution policy. To use another port, run `./start.ps1 -Port 7879` and
update **Backend URL** in extension settings. Keep the server terminal open.
Visit <http://127.0.0.1:7878/health> to check readiness and optional capabilities.

Load the unpacked `extension` folder in `chrome://extensions`. Enable **Allow
access to file URLs** in its details for local PDFs. Open a PDF before opening
the extension. The shortcut is `Ctrl+Shift+F` (`Command+Shift+F` on macOS);
another application may reserve it. Check `chrome://extensions/shortcuts`.
Only Windows browser behavior has been verified.

## Optional OCR

With [uv](https://docs.astral.sh/uv/) installed:

```powershell
uv venv --seed --python 3.12 .venv-ocr
.\.venv-ocr\Scripts\python -m pip install -r requirements-ocr.txt
```

If Python 3.12 is registered with the Windows launcher,
`py -3.12 -m venv .venv-ocr` also creates the environment. The backend discovers
`.venv-ocr`; set `FORMULA_SEARCH_OCR_PYTHON` for an interpreter elsewhere.
Restart after installing or changing it. `/health` reports package availability,
not whether weights have downloaded or whether recognition is accurate.

The pinned stack uses CPU PyTorch wheels. Allow several gigabytes for installation.
Filesystems with large allocation units (such as some exFAT disks) can require
substantially more space than the package download sizes. Installation and
upgrades also need temporary space.
Weights download on first recognition and are not bundled. Inference is local.
Select one tight equation crop, compare the output with the image, and correct
the editable LaTeX before saving. Added equations live in the backend session.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Backend unreachable | Keep the server running; visit `/health`; check URL and port |
| Worker errors | Use Python 3.14 for core; recreate the environment after changing Python |
| Local PDF fails | Enable file URL access and reopen the extension on that PDF tab |
| No matches | Try a shorter distinctive query; scanned PDFs need OCR; some structures are unsupported |
| Page opens without an overlay | Expected on Chrome 158; use the detail action for a highlighted PDF excerpt |
| Estimated/missing location | Source retrieval succeeded but precise mapping could not be established |
| OCR unavailable | Check the Python 3.12 installation, restart, inspect `/health` and server output |
| Large paper rejected | PDF upload limit is 64 MiB; processing has bounded workers and deadlines |

## Updates and local data

Restart the backend after Python changes; reload the extension after browser-code
changes. Back up bookmarks before moving an unpacked extension: a different path
can produce a different extension ID with separate Chrome local storage.

Runtime caches normally live in `.formula_cache` and can contain document text.
Set `FORMULA_SEARCH_CACHE_DIR` for another location. Session defaults are 8
documents, 256 MiB, and 2700 seconds; configure with
`FORMULA_SEARCH_MAX_SESSIONS`, `FORMULA_SEARCH_SESSION_MIB`, and
`FORMULA_SEARCH_SESSION_TTL_SECONDS`. Keep the backend on loopback.
