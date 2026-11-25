# Test data

The public snapshot contains query/location annotations, identity cases, and
manifests. It excludes downloaded research PDFs, TeX archives, and runtime caches.
The original example PDF is in `examples/`.

Restore external fixtures with `python tools/fetch_paper_fixtures.py`. Use
`--verify` to check existing files without network access. The manifest pins 18
files by byte length and SHA-256 across nine papers. The downloader is serial,
checks bytes before writing, never executes/extracts TeX, and never overwrites an
existing mismatch. arXiv rate limits or changed historical unversioned responses
can prevent restoration; do not silently accept replacement hashes.

Missing optional paper-dependent tests report skips. Set `RUN_PAPER_AUDITS=1`
and `RUN_PAPER_AUDITS_RECOMPUTE=1` after restoration to enable corpus gates.
Historical cached-report tests skip in the clean snapshot because development
reports are omitted; current source/PDF location gates recompute from inputs.

The corpus includes Attention, Sequential Monte Carlo inference, log normalizing
constants, VAE and GAN tutorials, Llama 2, matrix calculus, Mamba, and arXiv
2506.01320. URLs and exact byte identities are in `paper-fixtures.json` and
`benchmarks/`. Papers are external works with their own licenses. This project's
MIT license does not apply to their content.
