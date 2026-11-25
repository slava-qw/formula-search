# Presentation review — 25 November 2026

## Direction

Selected: **follow the notation**, a result-first workflow from a repeated symbol
to a saved occurrence. The other directions, **a reader's field guide** and
**inside the search engine**, remain in [STORYLINES.md](STORYLINES.md).
They were not built as competing films.

The presentation uses the actual extension icon, genuine product captures,
high-resolution renders of the original example, and a clearly conceptual diagram.
There are no invented UI elements, fabricated performance comparisons, or copied
reference-project screenshots. The palette, typography, and framing are shared
across the README, film, gallery, and social image.

## Verification

- A fresh Chrome 158 workflow asserted preparation, a numerator result, two
  squared-weight results, a page filter, bookmark persistence, the original PDF
  excerpt, correct page navigation, and dark appearance. No page/backend errors.
- Separate takes provide native-density video and double-density still images.
  Both use the same original document and unmocked workflow assertions.
- HyperFrames passed lint, runtime, layout, motion, and contrast checks with
  zero errors and zero warnings in those categories. Its sole craft warning is
  deliberate silence for muted README playback.
- The H.264/YUV420p export was probed at **1920 x 1080, 30 fps, 32.0 seconds**.
  An optional English subtitle track and external VTT accompany it. The GIF is
  960 x 540, 8 fps, and 12 seconds; product actions are not accelerated.
- Source ranges and the excerpt's **3.6-second freeze hold** are explicit in
  [edit.json](edit.json). The uncut recording remains available.
- Decoded beat, cut, and 360-pixel review sheets were inspected. An independent
  visual review confirmed the earlier padding and wrong-query opening were fixed,
  with no remaining material P0/P1 findings in the final sheets.
- Desktop/mobile gallery checks found no horizontal overflow, missing images,
  page errors, or video playback errors. The final publication check verifies
  source-media provenance, output hashes, local links, and the export manifest.

Initial review fixes: prevent inactive-tab screencast padding, start the search
beat on `w^2`, fit both result cards, bring the complete excerpt into frame, and
raise explanatory/provenance text to the 28 px film floor.

## Deliberate limits

The film is silent; no audio listening review was needed. Its interface recording
is native 420 x 860, so live text is softer than the supplied double-density
stills. The enlarged real result cards and individual screenshots provide the
reading detail. At phone width, the film communicates the task; use full-screen
playback or the individual captures to inspect every control.

The Chrome panel is captured as an extension tab, not a native side-panel opening
gesture. Chrome-158 in-viewer overlays and OCR are not claimed or demonstrated.
Timing is illustrative and is not a performance benchmark.

## Editable delivery

The [local gallery](review.html) contains the film, stills, transcript, and download
links. The HTML/HyperFrames sources can also be edited in the CLI preview or the
[HyperFrames desktop app](https://hyperframes.dev/studio/download?os=windows&src=cli).
The verified render uses `--frames-cache-dir off` for exFAT storage; optional
authoring history records can warn about unavailable links on this filesystem.
This does not affect the delivered frames or application runtime.
