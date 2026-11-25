# Editable project presentation

The presentation uses genuine Chrome captures and the project's original example
PDF. The public [media guide](../MEDIA.md) explains the editing and transcript.

- `stills.html`: hero, workflow illustration, architecture diagram, poster, social card.
- `index.html` and `compositions/`: the 1920 x 1080, 30 fps, 32-second HyperFrames film.
- `assets/`: captured inputs, original-PDF renders, existing extension icon, and Inter fonts.
- `edit.json`: exact source ranges and integrity record for the current film.
- `SPEC.md`, `STORYLINES.md`, `REVIEW.md`: creative direction and verification.
- `review.html`: local gallery with the actual video player and downloads.

## Edit and render

The project pins HyperFrames **0.8.207**. Node.js 24, desktop Chrome, and FFmpeg
are required. From this directory:

```powershell
npm run check
npx --yes hyperframes@0.8.207 preview --background
npm run render -- --quality delivery --output ../media/demo-video.mp4
```

The HyperFrames desktop app offers a timeline editor as another way to edit the
same sources. The CLI preview can also be used directly; it requires no app.

On exFAT disks, add `--frames-cache-dir off` to the render command; exFAT does
not support the frame cache's directory links. `--video-frame-format png` keeps
source UI captures out of JPEG extraction. The verified local render used these
flags with software browser rendering (`--no-browser-gpu`).

The delivered MP4 includes an optional subtitle track. From the repository root,
finish the render, build the GIF, and check dimensions and capture assertions:

```powershell
python tools/finish_presentation.py --video docs/media/demo-video.mp4 --ffmpeg ffmpeg
```

Run `python tools/make_publication_graphics.py` from the repository root to rebuild
the PNG/SVG artwork from `stills.html` and the supplied captures. It does not render
the film or overwrite its edited caption file.

## Refresh the product captures

From the project root, using a new work directory and FFmpeg on PATH:

Use separate takes for double-density stills and stable native-density video:

```powershell
python tools/record_demo.py --work .media-work/new-stills --out docs/presentation/assets --scale 2 --ffmpeg ffmpeg
Copy-Item docs/presentation/assets/capture.json docs/presentation/assets/stills-capture.json
python tools/record_demo.py --work .media-work/new-video --out .media-work/video-outputs --scale 1 --ffmpeg ffmpeg
Copy-Item .media-work/video-outputs/demo.mp4,.media-work/video-outputs/demo.vtt,.media-work/video-outputs/capture.json docs/presentation/assets/
```

This runs real searches and asserts result counts, page navigation, persisted
bookmarks, and an error-free backend session. A new capture has new timestamps:
review `assets/capture.json` and update `edit.json` and the two trimmed film inputs
before rendering. Do not reuse old edit times without inspecting the new footage.
The public `walkthrough.mp4` is the uncut recording; `demo.mp4` is the edited film.

The panel is recorded in an extension tab, not as a captured Chrome side panel.
No UI has been repainted and no responses are mocked. Crops, titles, camera moves,
and scene cuts are editorial. The architecture illustration is conceptual.

Video actions run at normal speed. The excerpt shot holds its final real frame
for 3.6 seconds so the equation and browser explanation can be read. The edit
record lists this hold and the source ranges. Stills use a separate double-density
take; video uses native density because Chrome can change inactive-tab screencast
scaling at DPR 2. Both takes run the same unmocked assertions.

## Rights

Original project artwork and recordings follow the root MIT license. Inter is
SIL OFL 1.1: retain [its license](assets/fonts/OFL-inter.txt). The existing extension
icon is used as supplied by this project. HyperFrames and GSAP retain their
upstream terms; they are authoring tools, not included extension dependencies.
