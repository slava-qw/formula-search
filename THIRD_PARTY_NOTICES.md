# Third-party notices

The [MIT license](LICENSE) applies to original project code and documentation.
It does not change dependency, font, model, or paper terms.

## Bundled browser assets

**KaTeX 0.16.10**, Copyright (c) 2013–2020 Khan Academy and other contributors,
uses MIT. Its JavaScript, CSS, and 60 font files are bundled under
`extension/popup/lib/`. The full [upstream license](extension/popup/lib/LICENSE)
is included. [Asset hashes](licenses/katex-assets.json) were checked against
the official npm release; these 62 files match byte for byte.
[Upstream](https://github.com/KaTeX/KaTeX/tree/v0.16.10).

The working development repository also has an unused **PDF.js 3.11.174** viewer,
Copyright 2023 Mozilla Foundation, Apache 2.0. Its [license](licenses/PDFjs-Apache-2.0.txt)
is retained, but the public source and extension exports omit that unused viewer.
[Upstream](https://github.com/mozilla/pdf.js/tree/v3.11.174).

## Installed Python dependencies

**PyMuPDF 1.27.2 / MuPDF** is offered under AGPL or a commercial agreement.
The project's MIT license does not waive those obligations for a combined or
redistributed application. See [upstream licensing](https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright)
and the license supplied with the installed package. Do not describe the complete
stack as MIT-only. No commercial license is supplied by this project.

Other core packages include FastAPI (MIT), Uvicorn (BSD), HTTPX (BSD), RapidFuzz
(MIT), SymPy (BSD), ANTLR Python runtime (BSD), python-multipart (Apache 2.0),
and Pillow (MIT-CMU/HPND family). Installed transitive packages have their own
license metadata and notices. Requirements and resolved constraints identify the
versions; no Python environment or binary distribution is bundled here.

Optional OCR uses [pix2tex / LaTeX-OCR](https://github.com/lukas-blecher/LaTeX-OCR)
and its published model weights, plus PyTorch and related packages. They are
downloaded separately. Consult the upstream code/model terms for the specific
artifacts you install. Model weights and the OCR environment are not relicensed
or included in the source export.

## Example PDF and fonts

The example text and TeX source are original project material under MIT.
Its embedded Latin Modern fonts are by Bogusław Jackowski and Janusz M. Nowacki,
based on Donald Knuth's Computer Modern. See the
[GUST font license](licenses/Latin-Modern-GUST.txt), its referenced
[LPPL 1.3c](licenses/LPPL-1.3c.txt), and
[AMS font distribution notes](licenses/AMSFonts-README.txt).
Font information: [Latin Modern](https://ctan.org/pkg/lm),
[Computer Modern](https://ctan.org/pkg/cm).

## Presentation fonts and authoring tools

The editable presentation bundles **Inter** 400/700 WOFF2 fonts, Copyright 2016
The Inter Project Authors. They use the
[SIL Open Font License 1.1](docs/presentation/assets/fonts/OFL-inter.txt).
These are presentation assets, separate from the extension's KaTeX fonts.
HyperFrames and GSAP are separate authoring tools with upstream terms; no
HyperFrames or GSAP runtime is added to the extension.

## External papers

Research-paper files, TeX archives, and cache contents are excluded from the
public export. The fixture manifest records URLs and hashes for local checks.
Each paper remains under its authors' applicable terms; arXiv availability does
not automatically grant a blanket redistribution license. Screenshots and demo
media in the public package use the original example document.
