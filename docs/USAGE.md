# Using Formula Search

Open the [example PDF](../examples/formula-playground.pdf) in Chrome. Search
`QK^T` with **Contains this notation** to find its occurrence on page 1.
Choose **Keep open ↗** for the persistent side panel. Search `w^2` for occurrences
on both pages, then narrow the list with the page filter.

## Results and matching

Results can include notation, PDF page, source file and line, context, and
location quality. UI page numbers count PDF pages from 1 and can differ from
printed page numbers. Source section filters depend on available source metadata.

TeX retrieval and PDF mapping are separate: a source match can have an estimated,
ambiguous, or unavailable PDF location. On Chrome 158, navigation opens the PDF
page and the detail action shows a highlighted excerpt of the original PDF.
Compatible older Chromium can show native overlays and information cards.

Use **Contains this notation** for subexpressions and **Same notation only** for
a whole normalized expression. Case, operator order, grouping, fractions, scripts,
and supported mathematical fonts matter. TeX input is useful for structured math.

**Algebraically equal (scalars)** performs bounded symbolic work and assumes
commuting scalars. Do not use that assumption for arbitrary matrix products.
An incomplete check does not establish that no equivalence exists.

## Keep your findings

- Copy LaTeX or Markdown, including source references when known.
- Star an occurrence and open **Bookmarks**. **Back up** exports JSON; **Restore**
  adds missing bookmarks and keeps existing ones.
- Export all matches or the matches selected by page and section filters.
- Use **Notes** for local saved notes. This is not an AI chat service.

These items use Chrome local storage. Back up before moving installations.

## arXiv and OCR

Open an arXiv abstract or PDF tab and search. The backend resolves the version and
fetches available TeX and PDF data. Source results retain file/line references and
context. Extraction supports a subset of TeX; it does not execute paper TeX code.

With the optional OCR model installed, open **Recognize formulas from an image**,
choose a page, and select one equation in the preview. Review the editable LaTeX
against the selected crop, correct errors, and save. Crop geometry stays attached.
OCR is fallible; no general recognition-accuracy claim is made. Added equations
remain in the current backend session. Export or bookmark what you need before
eviction or server restart.
