# Paper Tablet website

A static site: `docs/index.html` plus this folder (styles, script, fonts). It reuses the screenshots in
`docs/screenshots/`. No build step.

**Preview locally:** open `docs/index.html` in a browser.

**Host on GitHub Pages:** repo Settings → Pages → Deploy from a branch → `main`, folder `/docs`.
The site appears at `https://amazingbodilyfluids.github.io/paper-tablet/`.

**New release:** edit the version and file names in the Download section of `index.html`, move the
"Next" entries of the Version history into a new numbered entry, and change the `Next` badges on
features that shipped to the version number.

## Footage

The six demos are canvas animations until real recordings exist. To swap one for a video, put the
file in `site/media/` and add `data-video="site/media/<name>.mp4"` (and optionally
`data-poster="site/media/<name>.jpg"`) to that demo's tab button in `index.html`. It plays muted
and looped; if it fails to load, the animation stays.

Record at 1920 × 1200 (16:10) or crop to it, 8 to 15 seconds each, H.264 MP4 under ~4 MB, no sound
needed. Each clip works best as a split view: the tablet filmed from above on the left, a screen
capture of the PC on the right, in sync.

| File | What to show |
|---|---|
| `pressure.mp4` | One slow stroke in Photoshop with a Pen Pressure brush, light to heavy and back; the Home page pressure readout in view |
| `tilt.mp4` | A flat or calligraphy brush with Pen Tilt angle control; lean the pen left and right; then hover above the screen to show the cursor following |
| `snap.mp4` | Draw a rough circle, hold, it snaps; then a line and a triangle |
| `panel.mp4` | Tap Brush, Size + twice, Undo, then hold Hand and pan with the pen |
| `gestures.mp4` | Three-finger swipe left (undo ×3) and right (redo), two-finger pinch zoom, five-finger twist and double tap reset |
| `mask.mp4` | Add a mask image on Screen area, place it, then hatch across it on the tablet so strokes fade in grey and stop in black; flip Invert from the corner tab |
