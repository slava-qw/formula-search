# Validation and measurement scope

## Completed 0.11.0 implementation checks

Before publication preparation, local commit `1e769af` completed with:

| Check | Result |
| --- | --- |
| Python, full installed corpus with recomputation | 797 passed, 2 skipped |
| Node regressions | 201 passed |
| Extracted JavaScript assertions | 265 passed |
| Browser smoke | 37 checks each on Chromium 151 and Chrome 158 |
| Reviewed source-location gates | 106 positive, 7 negative across nine papers |

These are historical results from the development checkout, not a claim that
every public clone automatically runs the same corpus. The two Python skips
included an optional fixture path and an older direct OCR-model check. Actual
isolated OCR was tested separately. Browser completion smoke simulates recognition
to check UI review/retry behavior; actual model verification is a separate tool.

The mapper comparison covered 3,867 source candidates, including 90 multi-line
candidates. Of 84 changed locations, 77 weak rectangles were withheld and seven
groups were relocated/regrouped and visually checked. Retrieval remained available
for uncertain locations. Selected gates are not a corpus-wide precision/recall
measurement.

## Representative local timings

The 0.11.1 publication benchmark used pinned bytes, a fresh cache, local HTTP calls,
spawned preparation, search, and process/cache restart. It excluded downloads,
browser rendering, OCR, and any network-based arXiv latency. All returned match
records were compared across cold, resident, and restart runs.

| Paper | Cold preparation | First search | Restart preparation | Search after restart |
| --- | ---: | ---: | ---: | ---: |
| Attention | 1,938.3 ms | 16.5 ms | 1,521.3 ms | 3.3 ms |
| Llama 2 | 5,930.1 ms | 33.3 ms | 2,192.0 ms | 3.9 ms |
| Matrix calculus | 2,721.9 ms | 2,625.8 ms | 2,050.4 ms | 68.4 ms |
| Mamba | 2,690.7 ms | 202.2 ms | 1,854.1 ms | 15.3 ms |

Resident preparation was around 2–3 ms in that run. These machine-specific
measurements are not latency guarantees or speedup claims against another product.
This was a local verification run on exFAT storage, not a controlled latency comparison between releases. Complete match records also matched the archived 0.11.0 run for all four papers. See [the report](media/http-restart.json). Reproduce with `tools/benchmark_completion.py` after restoring the fixtures.

## Publication checks

The 0.11.1 publication patch updates the HTTP/image dependency stack and records
the core lock from an isolated Python 3.14 environment. Full installed-corpus
verification was rerun: **797 passed, 2 skipped**. The clean export passed **713 Python tests with 86 explicit optional skips**, plus **204 Node tests and 265 source-block assertions**. Missing-corpus checks do not silently pass empty inputs. The same 37 browser checks
passed on each verified browser after the update. The current package advisory
inventories returned zero advisory matches for 31 core pins and 68 optional OCR pins, with no lookup errors. The reports are [core](media/core-dependencies.json) and
[optional OCR](media/ocr-dependencies.json); their scope and lookup errors are
explicit. A zero-match inventory is not a complete security assessment.

The publication pass verifies the clean export rather than publishing the original
history. Its receipt is [publication-checks.json](media/publication-checks.json).
It records rerun Python/JavaScript checks and the exact scope. Missing optional
papers are explicitly skipped in fresh snapshots. GitHub-hosted Actions have
been prepared but cannot be called passing until run on GitHub.

[capture.json](media/capture.json) records the separate unmocked Chrome demo:
the original example PDF is uploaded by the extension, processed by the backend,
searched through HTTP, filtered, bookmarked, and inspected as a PDF excerpt.
The panel is opened as an extension tab for capture, not through a toolbar gesture.
No recognition response, result, or API endpoint is mocked in that demo.

## Reproduction and limits

See [CONTRIBUTING.md](../CONTRIBUTING.md) for commands and
[testdata](../testdata/README.md) for restoration. The source/PDF gates depend
on exact archived bytes; a changed upstream artifact must not silently update
the expected hash. No external corpus is included in the clean source ZIP.

PDF extraction still supports only a subset of layouts/fonts/structures. OCR
model probes included correct Attention and Mamba crops and a font-confusion
diagnostic; this is not a recognition-accuracy benchmark. Complex source mapping
can remain uncertain. Neither headless browser checks nor DOM accessibility
checks establish OS-shortcut, toolbar-gesture, or screen-reader compatibility.

The refreshed OCR runtime passed both required real-model crops and retained the known font-confusion diagnostic. All three crops retained their coordinates. See [real OCR results](media/real-ocr.json); this small check does not establish general OCR accuracy.

The publication acceptance pass also exercised 12 alternating native page jumps on Chrome 158. The URL-commit barrier fixes a reproduced reload race, with three new Node regressions for commitment, cancellation, and timeout. The reading page is checked independently through browser automation; product code does not gain access to restricted PDF frames.
