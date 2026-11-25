# Local publication package

Everything in this task is prepared locally. No remote repository, push, public
release, or Chrome Web Store upload has been performed.

## Export the release

From the working project root:

```powershell
python tools/check_publication.py
python tools/prepare_github.py --out dist/github/formula-search-0.11.1
```

The destination must be new; use a different name for another build. This creates
a source folder, a source ZIP, and an unpacked-extension ZIP next to that folder.
It copies current selected working-tree files and writes a SHA-256 manifest.
It never resets your tree, deletes historical files, initializes Git, or publishes.

The clean snapshot includes code, tests, public docs/media, original examples,
licenses, pinned external-fixture metadata, and supported maintenance tools with
their imported helpers. It excludes assistant settings, environments, credentials,
caches, downloaded papers, bulk audits, internal development plans, Git history,
and the unused PDF.js viewer. Original history and excluded files remain local.

**Use this clean folder for the future public repository.** A push from the
development checkout would also expose its existing tracked development history;
adding `.gitignore` alone does not remove previously tracked content. History has
not been rewritten as part of this preparation.

Before publication, review README/media and choose your repository owner/name.
The original-code MIT license has been selected by the owner. Dependency terms,
including PyMuPDF's AGPL/commercial choice, are stated separately in the notices.
The prepared workflow has local equivalents tested; GitHub-hosted execution will
only be known after a repository exists.

Run `python tools/audit_dependencies.py --out .media-work/core-advisories.json`
to refresh the package advisory inventory before a later release. Use
`--requirements requirements-ocr-lock.txt` for the optional stack. The tool sends
package names/versions to PyPI and returns nonzero for findings or lookup errors.
It does not establish application exploitability or replace code review.

## Suggested repository fields

Name: `formula-search`

Description: `Search mathematical notation in PDFs and arXiv papers with a Chrome extension and a local Python backend.`

Topics: `chrome-extension`, `latex`, `pdf`, `arxiv`, `math`, `formula-search`,
`fastapi`, `local-first`

Social image: `docs/media/social-preview.png` (1280 × 640).

Release title: `Formula Search 0.11.1`

Release text: [RELEASE_NOTES.md](RELEASE_NOTES.md). Attach the extension ZIP with
the reminder that the Python backend is still required. The source ZIP includes
the complete public setup. There are no invented badges, stars, deployment URLs,
or maintainer contact addresses in the README.

## Later GitHub step

When publishing is requested, initialize a fresh repository in the clean folder,
review staged contents, create the chosen GitHub repository, and push that initial
snapshot. Upload the demo MP4 to a GitHub issue/release or discussion if an inline
hosted player is desired, then insert its resulting URL. The current README uses
relative images and a local MP4 link, so it has no placeholder hosting URL.
Enable private security reporting if desired; no such setting is assumed yet.

## Presentation references

The presentation refresh was reviewed on 25 November 2026. It uses genuine
double-density product captures, a 32-second widescreen film, an inline GIF preview,
enlarged workflow illustrations, and a conceptual local-processing diagram.
Editable sources and the provenance ledger are in [presentation/](presentation/README.md).

The README structure was informed by GitHub's
[README guidance](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-readmes),
and the public [LaTeX-OCR](https://github.com/lukas-blecher/LaTeX-OCR) and
[Texify](https://github.com/VikParuchuri/texify) repositories: show a real example,
make installation copyable, separate optional model requirements, and state
limitations. No source project's screenshots or marketing claims were copied.

The refresh also reviewed [Cap](https://github.com/CapSoftware/Cap),
[Refined GitHub](https://github.com/refined-github/refined-github),
[Linear's feature presentation](https://linear.app/features), and the November
2026 project presentations of [ScreenSearch](https://github.com/nicolasestrem/screensearch)
and [nextDash](https://nextdash.cc/). The useful patterns were an immediate product
example, a short demo, readable feature evidence, copyable setup, and deeper
technical detail below. This is design research, not a feature or quality comparison.
