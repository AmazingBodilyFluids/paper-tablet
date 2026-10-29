# Paper Tablet: technical notes

How it works under the hood, what was measured on real hardware, and the command-line tool.
For using the app, see the [README](../README.md).

```
tablet: /dev/input/eventN ──ssh──▶ PC: parser ▶ pen state machine ▶ mapper ▶ InjectSyntheticPointerInput (PT_PEN)
```

Code layout: `pc/app.py` (pywebview window, splash) → `pc/core.py` (engine: plug watcher, SSH, pen, calibration,
tablet screen, panel and gestures, pairing) → `pc/rmtablet.py` (event parser, mapper, Windows Ink injection);
`pc/shapes.py` (hold-to-snap recogniser), `pc/keys.py` (keystrokes and the pan drag), `pc/paths.py` (where
settings live), `pc/web/` (the interface). On the tablet: `tablet/rmpp-pen.c` (input helper) and
`tablet/companion/` (the tablet-screen app).

## What is verified and what is not

| Claim | Status | Source |
|---|---|---|
| 64-bit ARM, 24-byte evdev records | **Confirmed** (aarch64, kernel 6.12.49 imx8mm-ferrari) | `rmpp-pen info` on a Paper Pro (OS 3.29), 2026-10-27 |
| Pen device | **Confirmed**: `/dev/input/event2` "Elan marker input" (other units report event3, so it is picked by capability) | same |
| X 0–11180, Y 0–15340, pressure 0–4096 | **Confirmed** | same |
| Hover | **Works**: `BTN_TOOL_PEN` arrives before contact and X/Y update in the air. `ABS_DISTANCE` is noisy (non-zero even while touching), so contact is decided from `BTN_TOUCH` + pressure only | `rmpp-pen watch`, same tablet |
| Tilt | **Works**: observed −5300…6000 / −5907…5800 (≈ ±55–60°, centidegrees), in hover and contact | `watch` |
| Pressure | **Works**: full 0–4095 reached | `watch` |
| Eraser end | **Works**: `TOOL_PEN 0` → `TOOL_RUBBER 1`, with its own touch and pressure | `watch` |
| Barrel buttons | **None**: `STYLUS/STYLUS2` declared by the driver, but the Marker Plus has no side buttons | `watch` + hardware |
| Digitizer resolution 2832 / 2064 | **Bogus**: would mean a 4 × 7 mm surface; ignored, 3:4 used | same |
| Raw axis orientation vs. the screen | X is the short side, so portrait; direction pending `calibrate` | same |

Because of that, the tool **does not hard-code any of these**. On every connect it copies a 48 KB static helper
(`tablet/rmpp-pen`, source alongside) to `/tmp` and asks the kernel directly (`EVIOCGABS`) for the real device
names, axes, ranges and resolutions. It picks the pen by capability (has `BTN_TOOL_PEN` + pressure), not by number.
Orientation is learned with a two-tap `calibrate`. Tilt is used only if the kernel says the axis exists.

Since then the whole chain has been used for real: hover, full pressure, tilt and the eraser reach Windows
as a native pen (pressure confirmed in Photoshop), the tablet screen draws, and the shortcut panel, hold-to-snap
shapes and finger gestures work on the device.

### reMarkable 2 and 1 (beta, not yet run on hardware)

| Claim | Status | Source |
|---|---|---|
| 32-bit ARM (Cortex-A7 on the rM2, A9 on the rM1), 16-byte evdev records | From the SDKs; the parser already takes 16-byte records | reMarkable's 3.28.0.172 rm1 / rm2 SDKs |
| Same Qt (6.10.3) and e-paper scene graph as the Paper Pro, including `EPScreenModeItem` with the same constructor | **Confirmed in the SDK sysroots** (`nm -DC libqsgepaper.so`); the rM2 build drives its screen through `EPFramebufferSwtcon` inside that library | same |
| Tablet-screen app starts and its QML loads on those Qt libraries | **Confirmed under emulation** (qemu-arm, the SDK sysroot, offscreen platform); the real e-paper platform is untested | 2026-10-29 |
| Pen = Wacom digitizer turned 270° against the panel (raw X up the long side from the bottom, raw Y to the right) | From libremarkable (MIT) `get_wacom_placement`; the app turns each report to run with the panel, so calibration, shapes and the mask stay model-neutral | libremarkable `src/device/mod.rs` |
| Touch turned 180° (rM2 also mirrored in X) | From reMarkable's Qt guide; `run.sh` sets `QT_QPA_EVDEV_TOUCHSCREEN_PARAMETERS` | developer.remarkable.com/documentation/qt_epaper |
| Model from `/sys/devices/soc0/machine` ("reMarkable 1.0" / "reMarkable 2.0") | libremarkable reads the same file; the helper now reports it | same |
| No developer mode needed | reMarkable: developer mode "is not relevant for the first two paper tablet generations" | developer.remarkable.com/documentation/developer-mode |
| Tilt direction, palm-size thresholds, digitizer reach past the screen edge | **Unknown**: needs one session on a real tablet | |

## Tablet screen (companion app)

`tablet/companion/` is a small Qt Quick app built with reMarkable's official Paper Pro SDK (OS 3.28 SDK,
Qt 6.10.3; runs on OS 3.29), rendered through the documented `epaper` backend
(developer.remarkable.com/documentation/qt_epaper). The GUI copies it to `/tmp/rmpp-companion` (gone on reboot)
and runs it with `run.sh`, which:

- stops the notebook app (xochitl) and holds a wakelock so the tablet can't autosleep mid-session;
- **always restarts xochitl** when the app ends: Stop button, closing the GUI, cable pulled / SSH dropped,
  tablet power button, or a crash;
- runs only one session at a time (a second Start is refused instead of fighting over the screen), and
  keeps its last output in `/tmp/rmpp-companion/last.log` on the tablet and the app's log on the PC;
- clears xochitl's start-rate counter first, because its unit has `StartLimitBurst=4` per 10 min with
  `OnFailure=emergency.target`, and repeated sessions must never trip that.

If the tablet ever stays blank after stopping it, hold the power button to restart it; nothing persistent
was changed.

## Command line (no window)

1. Tablet: enable developer mode, plug in USB. The root password is in
   *Settings → General → Help → About → Copyrights and licenses*. Check `ssh root@10.11.99.1` works.
2. PC: install Python 3.10+ from python.org, then in this folder:
   ```
   pip install -r pc\requirements.txt
   ```
3. Check the hardware:
   ```
   python pc\rmtablet.py info
   python pc\rmtablet.py watch      # hover, press, tilt the pen, flip to the eraser end; Ctrl+C
   ```
4. Calibrate once, **with the tablet where and how you draw on it** (any rotation, e.g. landscape
   with its left side up). Tap the top-left, then the top-right corner of the screen as you see it:
   ```
   python pc\rmtablet.py calibrate
   ```
5. Run:
   ```
   python pc\rmtablet.py run
   ```
   Add `--dry-run` to print pen frames instead of moving the cursor.

With an SSH key on the tablet (`ssh-keygen -t ed25519`, then append the .pub to `/home/root/.ssh/authorized_keys`) the tool logs in without asking; otherwise it prompts for the password.

### Options
- `--orientation`: leave at `as-calibrated`; calibration already captures how the tablet sits. Re-run `calibrate` if you turn it.
- `--monitor N` (0 = primary), `--fit crop|letterbox|stretch` (crop keeps the aspect ratio and reaches the whole monitor).
- `--area 0.1,0.1,0.9,0.9`: use a smaller part of the tablet.
- `--pressure-gamma 0.7` (softer) / `1.5` (firmer), `--pressure-threshold 0.02`.
- `--grab`: the tablet's own UI stops receiving the pen (no ink on the e-ink screen, no accidental toolbar taps).
- `--no-tilt`, `--device /dev/input/eventN`, `--feedback indirect|default|none`.

## The e-ink side
- **Seeing strokes on the tablet: free.** Without `--grab`, xochitl still gets the pen, so drawing in an open blank
  notebook leaves ink on the e-ink at the device's native (fast) latency, while the PC gets the same strokes.
  The tablet does not know about PC undo, layers, zoom or panning, so treat it as a tracing preview only. Keep
  `--fit stretch` if you want the tablet ink to line up 1:1 with a full-screen canvas.
- **External display: not worthwhile.** The colour e-ink panel redraws in a fraction of a second at best and much
  slower in colour, and the only official route for third-party drawing is a Qt Quick app with xochitl stopped.
  Mirroring a live Photoshop canvas would ghost and lag badly. A static reference image is feasible but a separate project.
- **Custom on-tablet stroke renderer** (draw locally, sync with PC): feasible in principle via reMarkable's Qt epaper
  SDK, but it replaces xochitl while running and gains little over the free notebook trick.

## Risk notes
- Read-only on the tablet. The helper lives in `/tmp` (gone on reboot). `--grab` is released when the SSH session ends.
- `AutoAddPolicy` trusts the tablet's SSH host key on first use; fine over the USB link, not on untrusted Wi-Fi.
