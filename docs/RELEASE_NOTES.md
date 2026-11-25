# Formula Search 0.11.1

This publication patch refreshes FastAPI/Starlette, multipart parsing, and Pillow
to remove advisory matches found in the former core pins. It also refreshes the
optional OCR runtime and records its separate verification. Advisory checks are
package-level inventories, not a claim that the complete application is secure.

Search mathematical notation in PDFs and arXiv papers using a Chrome extension
and local Python backend. This release combines notation search, source references,
page/section filters, occurrence bookmarks, exports, notes, and optional reviewed
equation OCR.

The completion patch improves source-to-PDF mapping, multi-line grouping, bounded
worker recovery, persistent caches, OCR crop/review/save behavior, and browser
navigation. On Chrome 158, page navigation and original-PDF excerpts are the
supported path; native overlays remain available in compatible older Chromium.

The public package includes Windows setup instructions, an original example PDF,
actual Chrome screenshots/demo, development checks, and pinned external-corpus
metadata. Original code is MIT; dependency terms remain separate.

Install Python 3.14, install `requirements.txt`, start the backend, then load the
`extension` folder unpacked in Chrome. The extension ZIP alone does not include
the backend. Optional OCR needs its separate Python 3.12 environment.

Known limits: incomplete support for some PDF glyph maps and complex structures,
heuristic source mapping, fallible OCR requiring review, scalar assumptions in
algebraic mode, and browser-version-dependent native PDF access. See the README
and validation guide for exact scope and evidence.

Chrome page jumps now wait for the requested URL to commit before reloading the PDF. This fixes intermittent jumps that opened the right excerpt while leaving the reading tab on its previous page.
