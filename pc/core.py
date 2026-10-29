"""
Headless engine behind the desktop app: finds the tablet, connects, streams the pen into
Windows, calibrates, runs the tablet screen, and keeps settings. The interface (app.py, a
pywebview window) only calls the public methods and reads `snapshot()`/`live()`.

Plug-and-play: while the app runs, a watcher checks for the tablet's USB network link once a
second. When it appears (and setup is done) the engine connects, turns on pen output and the
tablet screen as configured. When the cable is pulled the stream ends, and the tablet itself
gives the pen and the screen back to the notebook app a few seconds later (keepalive in
rmpp-pen and rmpp-companion), so nothing is left behind on either side.
"""
import collections
import json
import math
import os
import socket
import sys
import threading
import time

import rmtablet as rt
import keys
import paths
import shapes

CAL_MODES = {
    "sheet9": {"label": "9 crosshairs on the calibration sheet", "numbers": [1, 2, 3, 4, 5, 6, 7, 8, 9], "inset": 0.1},
    "sheet4": {"label": "4 corner crosshairs on the sheet", "numbers": [1, 3, 9, 7], "inset": 0.1},
    "corners": {"label": "4 screen corners (no sheet)", "numbers": None, "inset": 0.0},
}
TARGET_NAMES = ["top-left", "top-middle", "top-right", "middle-left", "centre", "middle-right",
             "bottom-left", "bottom-middle", "bottom-right"]
CORNER_NAMES = ["top-left", "top-right", "bottom-right", "bottom-left"]
DEFAULTS = {
    "host": "10.11.99.1", "user": "root", "key": "", "monitor": 0, "fit": "crop", "edge_lift": True,
    "orientation": "as-calibrated", "area": [0.0, 0.0, 1.0, 1.0], "gamma": 1.0, "threshold": 0.0,
    "tilt": True, "grab": False, "feedback": "indirect", "companion_rot": "Match calibration",
    # plug-and-play
    "auto_connect": True, "auto_output": True, "auto_screen": True, "trail_ms": 1500,
    "onboarded": False,
    # window: colour theme (web/themes.css), and whether closing the window keeps the app in the tray
    "theme": "intaglio", "close_to_tray": True,
    # hold-to-snap shapes
    "snap": True, "snap_hold_ms": 600, "snap_apps": ", ".join(shapes.DRAWING_APPS),
    # tablet-screen side panel with shortcut buttons: left | right | off
    "panel": "left", "panel_open": True, "gestures": True,
    # touch contacts bigger than this are a palm; 0 = off. In Qt's units, about 0.75 x the panel's raw
    # TOUCH_MAJOR: measured fingertips 6-16 (raw 8-21), resting palm edge 19-49 (raw 25-63)
    "palm_size": 20,
    "panel_buttons": None,  # filled from DEFAULT_BUTTONS below; always the active profile's buttons
    # named button sets, cycled with the arrows on the tablet panel: [{"name", "buttons"}]
    "panel_profiles": None, "panel_profile": 0,
    # the tablet screen under the ink: paper pattern, a tracing image, the front light
    "paper": "plain", "paper_mm": 5.0,
    "trace_file": "", "trace_opacity": 0.5, "trace_scale": 1.0, "trace_x": 0.0, "trace_y": 0.0,
    "trace_adjust": False, "light": False,
    # trace mask: MASK_N x MASK_N cells over the tablet screen (hex, row by row); where a cell is set,
    # the pen does nothing in Windows. Painted on the tablet itself in edit mode.
    "mask": "", "mask_on": False, "mask_edit": False,
    "swipe_step": 100,      # three-finger swipe: tablet pixels of drag per undo / redo step (~11 mm)
    "rotate_keys": "r",     # five-finger twist: key held while dragging in a circle (Photoshop R; Krita shift+space)
    "taps": None,           # tap gestures, filled from DEFAULT_TAPS below
}
PANEL_PX = 302  # panel width on the tablet, in panel pixels (about 34 mm)
TAB_PX = 84     # the fold tab in the panel's top corner, also all that's left when folded
PANEL_SLOTS = 16  # 8 rows of 2, as drawn on the tablet
# kinds: shortcut (tap the keys), hold (keys down while held / locked), pen, snap, clear, none
DEFAULT_BUTTONS = [
    {"t": "Undo", "kind": "shortcut", "keys": "ctrl+z"}, {"t": "Redo", "kind": "shortcut", "keys": "ctrl+shift+z"},
    {"t": "Size -", "kind": "shortcut", "keys": "["}, {"t": "Size +", "kind": "shortcut", "keys": "]"},
    {"t": "Brush", "kind": "shortcut", "keys": "b"}, {"t": "Eraser", "kind": "shortcut", "keys": "e"},
    {"t": "Hand", "kind": "hold", "keys": "space"}, {"t": "Pick", "kind": "hold", "keys": "alt"},
    {"t": "Ctrl", "kind": "hold", "keys": "ctrl"}, {"t": "Shift", "kind": "hold", "keys": "shift"},
    {"t": "Zoom -", "kind": "shortcut", "keys": "ctrl+-"}, {"t": "Zoom +", "kind": "shortcut", "keys": "ctrl+="},
    {"t": "Fit", "kind": "shortcut", "keys": "ctrl+0"}, {"t": "Shapes", "kind": "snap", "keys": ""},
    {"t": "Pen", "kind": "pen", "keys": ""}, {"t": "Save", "kind": "shortcut", "keys": "ctrl+s"},
]
# The first layout ended in "Clear" (tablet ink), pointless while trails clear themselves:
# a saved copy of that untouched layout moves to the new default.
OLD_DEFAULT_BUTTONS = DEFAULT_BUTTONS[:-1] + [{"t": "Clear", "kind": "clear", "keys": ""}]
DEFAULTS["panel_buttons"] = DEFAULT_BUTTONS
# Tap gestures on the drawing area: "fingers x taps" -> title and keys ("" = nothing).
# Ctrl+Shift+Alt+N is Photoshop's "new layer" without the dialog.
TAP_SLOTS = {"1x2": "One finger, double tap", "1x3": "One finger, triple tap", "2x1": "Two-finger tap",
             "2x2": "Two-finger double tap", "3x1": "Three-finger tap", "5x2": "Five-finger double tap"}
# 5x2 resets the canvas rotation: in Photoshop, Esc while the rotate tool is held (R); Krita uses 5
DEFAULT_TAPS = {"1x2": {"t": "", "keys": ""}, "1x3": {"t": "New layer", "keys": "ctrl+shift+alt+n"},
                "2x1": {"t": "", "keys": ""}, "2x2": {"t": "", "keys": ""}, "3x1": {"t": "", "keys": ""},
                "5x2": {"t": "Reset rotation", "keys": "r+esc"}}
DEFAULTS["taps"] = DEFAULT_TAPS
BUTTON_KINDS = ("shortcut", "hold", "pen", "snap", "clear", "trace", "light", "mask", "none")
MASK_N = 72
# fingers: "shortcuts" turns gestures into keys (pan = Space+drag ...); "windows" hands every
# contact to Windows as real touch, so apps do their own scrolling, zooming and inertia
TOUCH_MODES = ("shortcuts", "windows")
PAPERS = ("plain", "lined", "squared", "dots", "cross", "blueprint", "isometric")
PANEL_PX_PER_MM = 2160 / 239.6  # Paper Pro: 1620 x 2160 px on a 179.7 x 239.6 mm panel (229 ppi)
TRACE_MAX_BYTES = 30 * 1024 * 1024
FRONTLIGHT = "/sys/class/backlight/rm_frontlight"


def clean_mask(h):
    """A mask as exactly MASK_N * MASK_N / 4 lower-case hex digits (empty or bad input: nothing masked)."""
    n = MASK_N * MASK_N // 4
    h = "".join(c for c in str(h or "").lower() if c in "0123456789abcdef")[:n]
    return h + "0" * (n - len(h))


def mask_bits(h):
    """Hex mask -> bytes of 0 / 1, one per cell."""
    return bytes((int(c, 16) >> (3 - k)) & 1 for c in clean_mask(h) for k in range(4))


def clean_profiles(profiles, current=None):
    """Validated profile list: at least one, each with a name and a full button layout."""
    out = []
    for i, p in enumerate(list(profiles or [])[:50]):
        p = p if isinstance(p, dict) else {}
        name = " ".join(str(p.get("name") or "").split())[:24] or f"Profile {i + 1}"
        touch = p.get("touch") if p.get("touch") in TOUCH_MODES else "shortcuts"
        out.append({"name": name, "buttons": clean_buttons(p.get("buttons")), "touch": touch})
    return out or [{"name": "Photoshop", "buttons": clean_buttons(current or DEFAULT_BUTTONS), "touch": "shortcuts"}]


def load_profiles(cfg):
    """Settings from before profiles had one layout: it becomes the first profile."""
    cfg["panel_profiles"] = clean_profiles(cfg.get("panel_profiles"), cfg.get("panel_buttons"))
    cfg["panel_profile"] = max(0, min(int(cfg.get("panel_profile") or 0), len(cfg["panel_profiles"]) - 1))
    cfg["panel_buttons"] = cfg["panel_profiles"][cfg["panel_profile"]]["buttons"]


def clean_buttons(btns):
    """Validated copy of a panel layout: exactly PANEL_SLOTS buttons, known kinds, real keys."""
    out = []
    for b in list(btns or [])[:PANEL_SLOTS]:
        b = b if isinstance(b, dict) else {}
        kind = b.get("kind") if b.get("kind") in BUTTON_KINDS else "none"
        k = str(b.get("keys") or "").lower().replace(" ", "")
        if kind in ("shortcut", "hold"):
            try:
                k = "+".join(keys.parse(k))
            except ValueError:
                kind, k = "none", ""
        else:
            k = ""
        out.append({"t": str(b.get("t") or "")[:14], "kind": kind, "keys": k})
    while len(out) < PANEL_SLOTS:
        out.append({"t": "", "kind": "none", "keys": ""})
    return out


def clean_taps(taps):
    """Validated copy of the tap gestures: every slot, real keys or none."""
    taps = taps if isinstance(taps, dict) else {}
    out = {}
    for slot in TAP_SLOTS:
        t = taps.get(slot) if isinstance(taps.get(slot), dict) else DEFAULT_TAPS.get(slot, {})  # new slots: default
        k = str(t.get("keys") or "").lower().replace(" ", "")
        try:
            k = "+".join(keys.parse(k)) if k else ""
        except ValueError:
            k = ""
        out[slot] = {"t": str(t.get("t") or "")[:20] if k else "", "keys": k}
    return out


ROTATIONS = {"Match calibration": None, "Portrait": 0, "Landscape, left side up": 3,
             "Landscape, right side up": 1, "Upside down": 2}
# How the tablet lies on the desk, seen from the user (portrait: power button top-left, cable
# bottom-left). rot = quarter turns of the tablet-screen content; orientation = cursor mapping
# used until a calibration captures it exactly.
POSITIONS = {
    "landscape-left-up": {"rot": 3, "orientation": "landscape-cw", "name": "Landscape, left side up"},
    "landscape-right-up": {"rot": 1, "orientation": "landscape-ccw", "name": "Landscape, right side up"},
    "portrait": {"rot": 0, "orientation": "portrait", "name": "Portrait"},
    "portrait-flipped": {"rot": 2, "orientation": "portrait-flipped", "name": "Upside down"},
}
APP_KEY = paths.app_key()


def use_profile_config():
    """Keep settings and calibration in the user profile (%APPDATA%\\PaperTablet), not next to
    the code, so a new copy of the app keeps them; in a portable copy, in its own data folder
    (see paths.py). On first run an older rmtablet.json next to this file is carried over."""
    target = os.path.join(paths.data_dir(), "rmtablet.json")
    if not os.path.exists(target) and not paths.portable():
        candidates = [os.path.join(rt.HERE, "rmtablet.json")]
        found = [c for c in candidates if os.path.isfile(c)]
        if found:
            import shutil
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copy2(max(found, key=os.path.getmtime), target)
            print(f"settings carried over from {max(found, key=os.path.getmtime)}", flush=True)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    rt.CONFIG_PATH = target
    return target


class RateMeter:
    """Pen report rate: how often the tablet's digitizer reports (kernel timestamps) and how
    often reports reach this PC, over the last stretch the pen was in range. Gaps over 50 ms
    (pen lifted out of range, pauses) don't count."""

    GAP = 0.05

    def __init__(self, keep=400):
        self.samples = collections.deque(maxlen=keep)  # (tablet time, arrival time)
        self.last = None  # last result with enough samples, kept after the pen leaves

    def add(self, t_tablet, t_arrival):
        self.samples.append((t_tablet, t_arrival))
        if len(self.samples) % 50 == 0:
            r = self.compute()
            if r:
                self.last = r

    @staticmethod
    def _hz(times, gap):
        dts = [b - a for a, b in zip(times, times[1:]) if 0 < b - a < gap]
        if len(dts) < 20:
            return None
        dts.sort()
        return 1 / dts[len(dts) // 2]  # median: robust to the odd late or bunched report

    def compute(self):
        tab = [a for a, _ in self.samples]
        pc = [b for _, b in self.samples]
        th = self._hz(tab, self.GAP)
        if th is None:
            return None
        # arrivals bunch up in network reads, so for the PC count reports per second of wall
        # clock over each unbroken stretch (split where the tablet's own clock shows a gap)
        n, span, start = 0, 0.0, 0
        for i in range(1, len(tab) + 1):
            if i == len(tab) or not 0 <= tab[i] - tab[i - 1] < self.GAP:
                if i - start > 1:
                    n += i - start - 1
                    span += pc[i - 1] - pc[start]
                start = i
        ph = n / span if span > 0.2 else None
        return {"tablet": round(th), "pc": round(ph) if ph else None, "n": len(tab)}


class NullPen:
    def send(self, f):
        pass

    def close(self):
        pass


def usb_link(host):
    """True when this PC has a route to the tablet on its own subnet, i.e. the tablet's USB
    network adapter is up. Asks the routing table only (a UDP connect sends nothing), so it
    is cheap enough to run every second without touching the tablet."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as u:
            u.connect((host, 9))
            mine = u.getsockname()[0]
        return mine.rsplit(".", 1)[0] == host.rsplit(".", 1)[0] and mine != host
    except OSError:
        return False


def port_open(host, port, timeout=0.6):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def ensure_app_key():
    """Create the app's own SSH key once (Ed25519, OpenSSH format, no passphrase, readable only
    by this Windows user's profile). Returns the public key line."""
    pub_path = APP_KEY + ".pub"
    if not os.path.exists(APP_KEY):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        key = Ed25519PrivateKey.generate()
        os.makedirs(os.path.dirname(APP_KEY), exist_ok=True)
        priv = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH,
                                 serialization.NoEncryption())
        pub = key.public_key().public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH)
        fd = os.open(APP_KEY, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(priv)
        with open(pub_path, "wb") as f:
            f.write(pub + b" rmtablet-app\n")
    with open(pub_path) as f:
        return f.read().strip()


class Companion:
    """The tablet screen (tablet/companion). While it runs, xochitl is stopped and the tablet
    shows targets, status and pen trails; closing it, or losing the PC, gives the screen back."""

    FILES = ("rmpp-companion", "run.sh")
    REMOTE = "/tmp/rmpp-companion"

    def __init__(self, engine):
        self.engine = engine
        self.chan = None
        self.tab = None
        self.trace_sent = None  # (path, mtime, size) of the image on the tablet
        self.log = collections.deque(maxlen=300)
        self.started_at = 0

    def trace_remote(self, path):
        """Copy the tracing image to the tablet (once per file version); its path there, or ""."""
        if not path or not self.tab:
            return ""
        try:
            st = os.stat(path)
        except OSError:
            return ""
        if st.st_size > TRACE_MAX_BYTES:
            return ""
        key = (path, st.st_mtime, st.st_size)
        ext = os.path.splitext(path)[1].lower()[:5] or ".img"
        remote = f"{self.REMOTE}/trace{ext}"
        if self.trace_sent != key:
            with open(path, "rb") as f:
                self.tab.run(f"cat > {remote}", f.read())
            self.trace_sent = key
        return remote

    def light(self, on):
        """Front light to full, or back to what it was when the screen was taken over
        (run.sh saved that, and puts it back itself however the session ends)."""
        if not self.tab:
            return
        cmd = (f"cat {FRONTLIGHT}/max_brightness > {FRONTLIGHT}/brightness" if on else
               f"[ -f /tmp/rmpp-companion.lock/light ] && cat /tmp/rmpp-companion.lock/light > {FRONTLIGHT}/brightness")
        try:
            self.tab.run(cmd)
        except Exception:
            pass

    @property
    def running(self):
        return self.chan is not None and not self.chan.exit_status_ready()

    def start(self, tab):
        if self.running or time.time() - self.started_at < 4:
            return  # one hand-over at a time: overlapping sessions fight over the panel
        self.started_at = time.time()
        self.tab, self.trace_sent = tab, None
        src = os.path.join(rt.HERE, "..", "tablet", "companion")
        tab.run(f"mkdir -p {self.REMOTE}")
        for name in self.FILES:
            with open(os.path.join(src, name), "rb") as f:
                tab.run(f"cat > {self.REMOTE}/{name} && chmod 755 {self.REMOTE}/{name}", f.read())
        chan = tab.client.get_transport().open_session()
        chan.exec_command(f"sh {self.REMOTE}/run.sh 2>&1")
        self.chan = chan
        threading.Thread(target=self._read, args=(chan,), daemon=True).start()
        threading.Thread(target=self._keepalive, args=(chan,), daemon=True).start()

    def _read(self, chan):
        buf = b""
        for chunk in iter(lambda: chan.recv(4096), b""):
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                self._line(line.decode(errors="replace"))
        self._line("companion: closed")
        self.engine.changed()

    def _line(self, line):
        if line.startswith("ACT:"):
            self.engine.tablet_action(line[4:].strip())
            return
        self.log.append(line)
        print("tablet screen:", line, flush=True)
        if "first frame drawn" in line:
            self.engine.refresh_mapper()  # the side panel now takes part of the tablet
            self.engine.sync_companion()
            if self.engine.cfg.get("light"):
                threading.Thread(target=self.light, args=(True,), daemon=True).start()
            self.engine.changed()
        if line == "companion: closed":
            self.engine.mouse.end()
            self.engine.keys.release_all()
            self.engine.refresh_mapper()

    def _keepalive(self, chan):
        # The app gives the screen back to xochitl if these stop (cable pulled, PC asleep).
        while not chan.closed and not chan.exit_status_ready():
            try:
                chan.sendall(b"ping\n")
            except Exception:
                return
            time.sleep(1)

    def send(self, line):
        if self.running:
            try:
                self.chan.sendall((line + "\n").encode())
            except Exception:
                pass

    def stop(self):
        if self.chan is not None:
            self.send("quit")
            try:
                self.chan.shutdown_write()  # EOF also makes it exit
            except Exception:
                pass
            self.chan = None
            self.started_at = time.time()


class Worker(threading.Thread):
    """Owns one SSH session: pen stream, Windows pen, calibration taps."""

    def __init__(self, engine):
        super().__init__(daemon=True)
        self.e = engine
        self.latest = None
        self.stop_flag = threading.Event()
        self.translator = None
        self.snapper = None
        self.chan = None
        self.tab = None
        self.connected = False

    def run(self):
        e = self.e
        pen_out = None
        try:
            e.set_status("connecting", "Connecting to the tablet…")
            self.tab, info = e.open_tablet()
            pen = rt.pick_pen(info, None)
            e.on_connected(info, pen)
            self.connected = True
            self.translator = rt.PenTranslator(e.mapper, e.cfg["tilt"] and "TILT_X" in pen["abs"],
                                               has_touch_key="TOUCH" in pen["keys"])
            self.snapper = shapes.Snapper(e.mapper, send=lambda f: None, hold_ms=e.cfg["snap_hold_ms"],
                                          allowed=e.snap_allowed, on_snap=e.on_snap,
                                          undo=lambda: e.keys.combo(keys.SHORTCUTS["undo"]))
            parser = rt.EvdevParser(rt.event_format(info))
            was_down = False
            grabbed = None
            usb = e.cfg["host"].startswith("10.11.99.")
            checked = time.time()
            while not self.stop_flag.is_set():
                if time.time() - checked > 1.0:
                    # A pulled cable never closes the TCP stream on this side; notice it from
                    # the network adapter going away instead of waiting for SSH timeouts.
                    checked = time.time()
                    if (usb and not usb_link(e.cfg["host"])) or not self.tab.client.get_transport().is_active():
                        break
                want = e.want_grab()
                if want != grabbed:  # (re)open the stream with or without the exclusive grab
                    if self.chan:
                        self.chan.close()
                    self.chan = self.tab.stream(pen["path"], grab=want)
                    self.chan.settimeout(0.05)  # wake often enough to notice the pen resting
                    parser = rt.EvdevParser(rt.event_format(info))
                    grabbed = want
                try:
                    chunk = self.chan.recv(65536)
                except socket.timeout:
                    chunk = None
                if chunk == b"":
                    break
                if e.output_on and pen_out is None:
                    pen_out = e.make_pen()
                    self.snapper.send = pen_out.send
                if not e.output_on and pen_out is not None:
                    self.release(pen_out)
                    pen_out = None
                snap = bool(e.cfg.get("snap")) and pen_out is not None and e.cal is None
                if not chunk:
                    if snap:
                        self.snapper.hold_ms = int(e.cfg.get("snap_hold_ms") or 600)
                        self.snapper.tick()
                    continue
                now = time.monotonic()
                for s in parser.feed(chunk):
                    self.latest = s
                    if s.in_range and s.t:
                        e.rate.add(s.t, now)
                    down = s.in_range and (s.touch or s.pressure > 0)
                    if e.cal is not None:
                        if down and not was_down:
                            e.on_tap((s.x, s.y))
                    elif pen_out is not None:
                        if e.over_panel(s) or e.masked(s):
                            s = rt.PenState()  # over the tablet's buttons: out of range for Windows
                        frames = self.translator.step(s)
                        if snap:
                            frames = self.snapper.feed(s, frames)
                        elif self.snapper.pts or self.snapper.snapped:
                            self.snapper.reset()
                        for frame in frames:
                            pen_out.send(frame)
                    was_down = down
                if snap:
                    self.snapper.tick()
        except Exception as ex:  # auth, network, helper problems
            # back off before the watcher tries again; a refused key won't fix itself
            e.retry_at = time.time() + (120 if isinstance(ex, PermissionError) else 5)
            e.set_status("error", e.explain(ex))
        finally:
            if pen_out is not None:
                try:
                    self.release(pen_out)
                except Exception:
                    pass
            self.close_tablet()
            self.connected = False
            e.on_disconnected(self)

    def release(self, pen_out):
        if self.translator:
            for frame in self.translator.leave():
                pen_out.send(frame)
        pen_out.close()

    def close_tablet(self):
        try:
            if self.chan:
                self.chan.close()
            if self.tab:
                self.tab.close()
        except Exception:
            pass
        self.chan = self.tab = None

    def shutdown(self):
        self.stop_flag.set()
        self.close_tablet()  # unblocks recv


class Engine:
    def __init__(self, on_change=None):
        self.lock = threading.RLock()
        self.on_change = on_change or (lambda: None)
        self.monitors = rt.monitors()  # sets DPI awareness on Windows before any window opens
        saved = rt.load_config()
        self.cfg = dict(DEFAULTS, **saved.get("gui", {}))
        if "autoconnect" in saved.get("gui", {}):  # settings from the old tkinter window
            self.cfg.setdefault("auto_connect", saved["gui"]["autoconnect"])
        if self.cfg.get("panel_buttons") == OLD_DEFAULT_BUTTONS:
            self.cfg["panel_buttons"] = DEFAULT_BUTTONS
        load_profiles(self.cfg)
        if self.cfg.get("palm_size") == 28:  # first guess, in raw units; Qt reports ~0.75 of that
            self.cfg["palm_size"] = 20
        self.calibration = saved.get("calibration")
        if self.calibration and not saved.get("gui", {}).get("onboarded"):
            self.cfg["onboarded"] = True  # an existing setup (calibrated before) needs no walkthrough
        if (self.calibration or {}).get("homography"):
            self.cfg["orientation"] = "as-calibrated"
        self.info = self.pen = None
        self.worker = None
        self.companion = Companion(self)
        self.output_on = False
        self.cal = None  # {"mode", "taps"} while calibrating
        self.cal_result = None
        self.status = ("idle", "Waiting for the tablet")
        self.link = False
        self.ssh_port = False
        self.manual_off = False  # user pressed Disconnect: don't auto-connect until replugged
        self.retry_at = 0
        self.peak = 0
        self.snap_last = None  # last snap or skip, for the UI
        self.keys = keys.Keys()
        self.mouse = keys.Mouse()
        self.pan_space = False
        self.last_action = None
        self.touch = None       # the latest finger report from the tablet, for the live view
        self.touch_out = None   # WindowsTouch, made when a profile first uses Windows touch
        self.mask = mask_bits(self.cfg.get("mask"))
        self.rate = RateMeter()
        self.mapper = None
        self.refresh_mapper()
        self._stop = threading.Event()
        threading.Thread(target=self._watch, daemon=True).start()

    # ------------------------------------------------------------------ state for the UI

    def changed(self):
        try:
            self.on_change()
        except Exception:
            pass

    def set_status(self, kind, text):
        self.status = (kind, text)
        print(f"[{time.strftime('%H:%M:%S')}] {kind}: {text}", flush=True)
        self.changed()

    @property
    def connected(self):
        return bool(self.worker and self.worker.connected)

    def snapshot(self):
        c = self.calibration or {}
        mon = [{"index": i, "label": f"Display {i + 1}{' (main)' if i == 0 else ''}  {r[2] - r[0]}×{r[3] - r[1]}"}
               for i, r in enumerate(self.monitors)]
        cal = None
        if c:
            res = c.get("residual_mm") or []
            cal = {"points": len(c.get("targets", [])) or 4, "date": c.get("date", ""),
                   "landscape": bool(c.get("swap")), "residual_mm": res,
                   "worst": max(res) if res else None}
        calibrating = None
        if self.cal is not None:
            i = len(self.cal["taps"])
            calibrating = {"mode": self.cal["mode"], "index": i, "total": len(self.cal_targets(self.cal["mode"])),
                           "number": self.cal_number(self.cal["mode"], i), "prompt": self.cal_prompt(self.cal["mode"], i),
                           "targets": self.cal_targets(self.cal["mode"])}
        return {
            "cfg": self.cfg, "touch_mode": self.touch_mode(), "status": {"kind": self.status[0], "text": self.status[1]},
            "link": self.link, "ssh": self.ssh_port, "connected": self.connected,
            "output": self.output_on, "screen": self.companion.running,
            "device": self.pen and {"name": self.pen["name"], "kernel": self.info.get("release", ""),
                                    "tilt": "TILT_X" in self.pen["abs"]},
            "monitors": mon, "calibration": cal, "calibrating": calibrating, "cal_result": self.cal_result,
            "cal_modes": {k: v["label"] for k, v in CAL_MODES.items()},
            "rotations": list(ROTATIONS), "aspect": self.mapper.oriented_aspect(),
            "used": self.mapper.used_region(), "pressure_max": self.mapper.absinfo["PRESSURE"]["max"],
            "has_key": os.path.exists(APP_KEY), "screen_log": list(self.companion.log)[-12:],
            "position": self.position(), "position_mismatch": self.position_mismatch(),
            "snap_last": self.snap_last, "last_action": self.last_action, "held_keys": sorted(self.keys.held),
            "view": self.view_size(), "panel_px": PANEL_PX, "tab_px": TAB_PX,
            "key_names": sorted(keys.VK), "key_labels": keys.LABEL, "default_buttons": DEFAULT_BUTTONS,
            "pen_rate": self.rate.last, "tap_slots": TAP_SLOTS, "taps": clean_taps(self.cfg.get("taps")),
        }

    def live(self):
        out = {"pen": None}
        t, a = self.touch, self.last_action
        if t and (t["pts"] or time.time() - t["at"] < 0.5):
            out["touch"] = t
        if a and time.time() - a["at"] < 1.5:
            out["gesture"] = a["action"]
        w = self.worker
        s = w.latest if w else None
        if s is None or not s.in_range or self.pen is None:
            return out
        p, q = self.mapper.oriented(*self.mapper.uv(s.x, s.y))
        touching = s.touch or s.pressure > 0
        pressure = self.mapper.pressure(s.pressure) if touching else 0
        tx, ty = self.mapper.tilt_xy(s.tilt_x, s.tilt_y)
        sx, sy = self.mapper.to_screen(s.x, s.y)
        if s.pressure > self.peak:
            self.peak = s.pressure
        return dict(out, pen={"p": p, "q": q, "touching": bool(touching), "eraser": bool(s.rubber), "raw": s.pressure,
                              "out": pressure, "tilt": [tx, ty], "screen": [sx, sy]}, peak=self.peak)

    # ------------------------------------------------------------------ settings

    def set(self, key, val):
        with self.lock:
            if key not in DEFAULTS:
                raise KeyError(key)
            if key == "panel_buttons":
                val = clean_buttons(val)
                profs = [dict(p) for p in self.cfg["panel_profiles"]]
                profs[self.cfg["panel_profile"]]["buttons"] = val
                self.cfg["panel_profiles"] = profs
            if key == "panel_profiles":
                val = clean_profiles(val, self.cfg.get("panel_buttons"))
                self.cfg["panel_profile"] = min(self.cfg["panel_profile"], len(val) - 1)
                self.cfg["panel_buttons"] = val[self.cfg["panel_profile"]]["buttons"]
            if key == "panel_profile":
                val = int(val) % len(self.cfg["panel_profiles"])
                self.cfg["panel_buttons"] = self.cfg["panel_profiles"][val]["buttons"]
                self.keys.release_all()  # a held key belongs to the old layout's buttons
            if key == "taps":
                val = clean_taps(val)
            if key == "rotate_keys":
                try:
                    val = "+".join(keys.parse(val))
                except ValueError:
                    val = DEFAULTS["rotate_keys"]
            if key == "swipe_step":
                val = max(40, min(400, int(val)))
            if key == "paper" and val not in PAPERS:
                val = "plain"
            if key == "paper_mm":
                val = max(2.0, min(30.0, float(val)))
            if key in ("trace_opacity",):
                val = max(0.05, min(1.0, float(val)))
            if key == "trace_scale":
                val = max(0.05, min(20.0, float(val)))
            if key in ("trace_x", "trace_y"):
                val = float(val)
            if key == "mask":
                val = clean_mask(val)
                self.mask = mask_bits(val)
            self.cfg[key] = val
            self.refresh_mapper()
            self.save()
        if key in ("companion_rot", "panel", "panel_open"):
            self.refresh_mapper()
        if key in ("area", "companion_rot", "trail_ms", "output", "snap", "panel", "panel_open",
                   "panel_buttons", "gestures", "swipe_step", "palm_size", "panel_profiles", "panel_profile",
                   "paper", "paper_mm", "trace_file", "trace_opacity", "trace_scale", "trace_x", "trace_y",
                   "trace_adjust", "light", "mask_on", "mask_edit"):
            self.sync_companion()
        if key == "light" and self.companion.running:
            threading.Thread(target=self.companion.light, args=(bool(val),), daemon=True).start()
        if key == "panel_profile" and self.companion.running:
            self.companion.send("unlatch")
            self.companion.send("toast " + self.cfg["panel_profiles"][self.cfg["panel_profile"]]["name"])
        if key == "tilt" and self.worker and self.worker.translator and self.pen:
            self.worker.translator.has_tilt = bool(val) and "TILT_X" in self.pen["abs"]
        self.changed()

    def view_size(self):
        """The tablet screen as it sits on the desk, in panel pixels (width, height)."""
        return (2160, 1620) if self.companion_rotation() % 2 else (1620, 2160)

    def panel_shown(self):
        return (self.cfg.get("panel", "left") != "off" and self.companion.running and self.cal is None)

    def panel_frac(self):
        """Share of the tablet (across, as it sits) the open side panel takes, or 0."""
        if not self.panel_shown() or not self.cfg.get("panel_open", True):
            return 0.0
        return PANEL_PX / self.view_size()[0]

    def effective_area(self):
        a = list(self.cfg["area"])
        f = self.panel_frac()
        if f and self.cfg.get("panel") == "right":
            a[2] = min(a[2], 1 - f)
        elif f:
            a[0] = max(a[0], f)
        if a[2] - a[0] < 0.1:
            a = list(self.cfg["area"])
        return a

    def masked(self, s):
        """Is the pen over the trace mask (or is the mask being painted)? Then Windows doesn't get it."""
        if not s.in_range or not self.mapper or self.cal is not None:
            return False
        if self.cfg.get("mask_edit") and self.companion.running:
            return True
        if not self.cfg.get("mask_on"):
            return False
        p, q = self.mapper.oriented(*self.mapper.uv(s.x, s.y))
        c, r = min(MASK_N - 1, max(0, int(p * MASK_N))), min(MASK_N - 1, max(0, int(q * MASK_N)))
        return self.mask[r * MASK_N + c] == 1

    def over_panel(self, s):
        """Is the pen above the tablet's side panel? Then it presses buttons there, not in Windows."""
        if not self.panel_shown() or not s.in_range or not self.mapper:
            return False
        p, q = self.mapper.oriented(*self.mapper.uv(s.x, s.y))
        right = self.cfg.get("panel") == "right"
        f = self.panel_frac()
        if f:
            return p > 1 - f if right else p < f
        # folded: only the small tab in the top corner is left
        w, h = self.view_size()
        tx, ty = TAB_PX / w, TAB_PX / h
        return q < ty and (p > 1 - tx if right else p < tx)

    def buttons(self):
        return clean_buttons(self.cfg.get("panel_buttons") or DEFAULT_BUTTONS)

    def companion_buttons(self):
        """The layout as the tablet draws it: title, second line, how a press behaves."""
        out = []
        for b in self.buttons():
            kind = {"shortcut": "tap", "clear": "local"}.get(b["kind"], b["kind"])
            sub = {"shortcut": keys.label(b["keys"]), "hold": "holds " + keys.label(b["keys"]),
                   "clear": "tablet ink", "trace": "image", "mask": "mask"}.get(b["kind"], "")
            out.append({"t": b["t"], "s": sub, "kind": kind})
        return out

    def pan_scale(self):
        """Screen pixels per tablet-screen pixel, so a two-finger drag moves the canvas 1:1."""
        if not self.mapper:
            return 1.0, 1.0
        w, h = self.view_size()
        a = self.mapper.area
        l, t, r, b = self.mapper.monitor
        axis, scale = self.mapper._fit_scale()
        sx = (r - l) / (max(0.05, a[2] - a[0]) * w) * (scale if axis == 0 else 1)
        sy = (b - t) / (max(0.05, a[3] - a[1]) * h) * (scale if axis == 1 else 1)
        return sx, sy

    def tablet_action(self, a):
        """A button or gesture on the tablet screen."""
        parts = a.split()
        if not parts:
            return
        name, arg = parts[0], parts[1] if len(parts) > 1 else ""
        if name == "touch":  # many per second: not an action for the activity line
            self.on_touch(parts)
            return
        if name in ("press", "release") and arg.isdigit():
            btns = self.buttons()
            i = int(arg)
            if i >= len(btns):
                return
            b = btns[i]
            if b["kind"] == "shortcut" and name == "press":
                self.keys.combo(b["keys"])
            elif b["kind"] == "hold":
                (self.keys.down if name == "press" else self.keys.up)(b["keys"])
            else:
                return
        elif name == "pan":
            self.pan(parts[1:])
            if arg != "end":
                return  # moves are many; only the start and end show in the UI
        elif name == "rotate":
            self.rotate(parts[1:])
            if arg != "end":
                return
        elif name == "tap" and len(parts) == 3:
            t = clean_taps(self.cfg.get("taps")).get(f"{parts[1]}x{parts[2]}")
            if not t or not t["keys"]:
                return  # not assigned
            self.keys.combo(t["keys"])
            if self.companion.running:
                self.companion.send("toast " + (t["t"] or keys.label(t["keys"])))
        elif name in keys.SHORTCUTS:  # gestures
            self.keys.combo(keys.SHORTCUTS[name])
        elif name == "panel":
            self.set("panel_open", arg == "1")
        elif name == "trace" and len(parts) == 4:  # the image was moved / zoomed on the tablet
            with self.lock:
                self.cfg["trace_x"], self.cfg["trace_y"] = float(parts[1]), float(parts[2])
                self.cfg["trace_scale"] = max(0.05, min(20.0, float(parts[3])))
                self.save()
        elif name == "mask" and len(parts) == 2:  # painted or moved on the tablet
            self.set("mask", parts[1])
            if not self.cfg.get("mask_on"):
                self.set("mask_on", True)
        elif name == "maskedit":
            self.set("mask_edit", not self.cfg.get("mask_edit"))
        elif name == "traceadjust":
            self.set("trace_adjust", not self.cfg.get("trace_adjust"))
        elif name == "light":
            self.set("light", not self.cfg.get("light"))
        elif name == "profile" and arg in ("next", "prev"):
            self.set("panel_profile", self.cfg["panel_profile"] + (1 if arg == "next" else -1))
        elif name == "pen":
            self.set_output(arg == "1")
        elif name == "snap":
            self.set("snap", arg == "1")
        else:
            return
        self.last_action = {"action": a, "at": time.time()}
        self.changed()

    def pan(self, args):
        """Two-finger drag on the tablet: hold Space and drag with the mouse, like the hand tool."""
        if not args:
            return
        if args[0] == "begin":
            self.pan_space = "space" not in self.keys.held
            if self.pan_space:
                self.keys.down("space")
                time.sleep(0.03)  # let the app switch to the hand before the button goes down
            self.mouse.begin()
        elif args[0] == "end":
            self.mouse.end()
            if self.pan_space:
                time.sleep(0.02)
                self.keys.up("space")
            self.pan_space = False
        elif len(args) == 2:
            try:
                dx, dy = float(args[0]), float(args[1])
            except ValueError:
                return
            sx, sy = self.pan_scale()
            self.mouse.move(int(round(dx * sx)), int(round(dy * sy)))

    ROT_RADIUS = 300  # screen pixels from the window centre to where the rotate drag runs

    def rotate(self, args):
        """Five-finger twist: hold the rotate key and drag around the window centre by the same
        angle (Photoshop's Rotate View, Krita's Shift+Space drag). The cursor goes back after."""
        if not args:
            return
        if args[0] == "begin":
            spec = self.cfg.get("rotate_keys") or "r"
            try:
                names = keys.parse(spec)
            except ValueError:
                names = ["r"]
            self.rot_pressed = [n for n in names if n not in self.keys.held]
            if self.rot_pressed:
                self.keys.down("+".join(self.rot_pressed))
                time.sleep(0.05)  # let the app switch tools before the button goes down
            self.rot_back = self.mouse._cursor()
            self.rot_c = keys.foreground_center()
            cx, cy = self.rot_c
            self.mouse.begin_at(cx, cy - self.ROT_RADIUS)
        elif args[0] == "end":
            if getattr(self, "rot_c", None) is None:
                return
            self.mouse.end()
            if self.rot_pressed:
                time.sleep(0.02)
                self.keys.up("+".join(self.rot_pressed))
            self.mouse.goto(*self.rot_back)
            self.rot_c = None
            self.rot_pressed = []
        else:
            if getattr(self, "rot_c", None) is None:
                return
            try:
                a = math.radians(float(args[0]) - 90)
            except ValueError:
                return
            cx, cy = self.rot_c
            x, y = cx + self.ROT_RADIUS * math.cos(a), cy + self.ROT_RADIUS * math.sin(a)
            sx, sy = self.mouse.start
            self.mouse.move(int(round(x - sx)), int(round(y - sy)))

    def snap_allowed(self, name):
        return shapes.app_allowed(name, str(self.cfg.get("snap_apps", "")).split(","))

    def on_snap(self, info, raws):
        """A stroke was snapped (raws = outline in digitizer units) or skipped (raws = None)."""
        self.snap_last = dict(info, at=time.time())
        if raws and self.companion.running and self.mapper:
            ax, ay = self.mapper.absinfo["X"], self.mapper.absinfo["Y"]
            fx = lambda v: (v - ax["min"]) / ((ax["max"] - ax["min"]) or 1)
            fy = lambda v: (v - ay["min"]) / ((ay["max"] - ay["min"]) or 1)
            pts = " ".join(f"{fx(x):.4f} {fy(y):.4f}" for x, y in raws[::2] + raws[-1:])
            self.companion.send(f"snap {info.get('pressure', 0.5):.2f} {pts}")
        self.changed()

    def save(self):
        cfg = rt.load_config()
        cfg["gui"] = {k: self.cfg[k] for k in DEFAULTS if k in self.cfg}
        if self.calibration is not None:
            cfg["calibration"] = self.calibration
        rt.save_config(cfg)

    def build_mapper(self, absinfo):
        cal = self.calibration or {}
        mon = self.monitors[min(int(self.cfg["monitor"]), len(self.monitors) - 1)]
        return rt.Mapper(absinfo=absinfo, monitor=mon, orientation=self.cfg["orientation"], fit=self.cfg["fit"],
                         swap=cal.get("swap", False), flip_u=cal.get("flip_u", False), flip_v=cal.get("flip_v", False),
                         homography=cal.get("homography"), correction=cal.get("correction"), aspect=cal.get("aspect"),
                         pressure_gamma=float(self.cfg["gamma"]), pressure_threshold=float(self.cfg["threshold"]),
                         area=tuple(self.effective_area()), edge_lift=bool(self.cfg["edge_lift"]))

    def refresh_mapper(self):
        absinfo = (self.pen or {}).get("abs") or {"X": {"min": 0, "max": 11180}, "Y": {"min": 0, "max": 15340},
                                                  "PRESSURE": {"min": 0, "max": 4096}}
        self.mapper = self.build_mapper(absinfo)
        if self.worker and self.worker.translator:
            self.worker.translator.m = self.mapper  # takes effect on the next pen report
        if self.worker and self.worker.snapper:
            self.worker.snapper.m = self.mapper

    def reset_peak(self):
        self.peak = 0

    # ------------------------------------------------------------------ connection

    def _watch(self):
        """Plug-and-play: notice the USB link coming and going, connect when it appears."""
        while not self._stop.is_set():
            host = self.cfg["host"]
            if self.connected:
                link, ssh = True, True  # the live session is proof enough; don't poke the tablet
            else:
                # 10.11.99.x is the tablet's USB network; any other address (SSH over Wi-Fi)
                # can only be found by knocking.
                link = usb_link(host) if host.startswith("10.11.99.") else port_open(host, 22)
                ssh = link and port_open(host, 22)
            if (link, ssh) != (self.link, self.ssh_port):
                self.link, self.ssh_port = link, ssh
                if not link:
                    self.manual_off = False  # unplugged: the next plug-in connects again
                    self.retry_at = 0
                    if not self.connected:
                        self.set_status("idle", "Waiting for the tablet: plug in the USB cable")
                self.changed()
            if (ssh and self.cfg["onboarded"] and self.cfg["auto_connect"] and not self.manual_off
                    and time.time() >= self.retry_at and not (self.worker and self.worker.is_alive())):
                self.connect()
            self._stop.wait(1.0)

    def open_tablet(self):
        import paramiko
        errors = []
        keys = [k for k in (self.cfg["key"], APP_KEY if os.path.exists(APP_KEY) else None) if k] + [None]
        for key in keys:
            try:
                tab = rt.Tablet(self.cfg["host"], self.cfg["user"], None, keyfile=key)
                return tab, tab.install_helper()
            except paramiko.AuthenticationException as ex:
                errors.append(ex)
            except paramiko.SSHException as ex:
                if "no authentication methods" in str(ex).lower() or "not a valid" in str(ex).lower():
                    errors.append(ex)
                else:
                    raise
        raise PermissionError("the tablet refused the login key")

    def explain(self, ex):
        msg = str(ex) or ex.__class__.__name__
        if isinstance(ex, PermissionError):
            return "The tablet didn't accept this computer's key. Open Setup and repeat the password step."
        if isinstance(ex, (socket.timeout, TimeoutError, ConnectionRefusedError)) or "timed out" in msg:
            return "The tablet stopped answering. Check the cable and that the tablet is awake."
        return f"Connection problem: {msg}"

    def connect(self):
        with self.lock:
            if self.worker and self.worker.is_alive():
                return
            self.manual_off = False
            self.retry_at = 0
            self.worker = Worker(self)
            self.worker.start()

    def disconnect(self):
        self.manual_off = True
        self.companion.stop()
        w, self.worker = self.worker, None
        if w:
            w.shutdown()
        self.output_on = False
        self.cal = None
        self.set_status("idle", "Disconnected. Press Connect, or unplug and plug in again.")

    def on_connected(self, info, pen):
        self.info, self.pen = info, pen
        self.refresh_mapper()
        self.set_status("connected", "Connected")
        if self.cfg["auto_output"]:
            self.output_on = True
        if self.cfg["auto_screen"] and self.worker:
            try:
                self.companion.start(self.worker.tab)
            except Exception as ex:
                self.set_status("connected", f"Connected, but the tablet screen didn't start: {ex}")
        self.changed()

    def on_disconnected(self, worker):
        self.companion.chan = None
        self.mouse.end()
        self.keys.release_all()  # never leave Ctrl/Alt/Shift/Space (or a pan drag) held down
        if worker is self.worker:
            self.output_on = False
            self.cal = None
            host = self.cfg["host"]
            if host.startswith("10.11.99.") and not usb_link(host):
                self.link = self.ssh_port = False
            if self.status[0] != "error":
                self.set_status("idle", "Tablet disconnected" if self.link else
                                "Tablet unplugged. The notebook app is back on the tablet.")
        self.changed()

    def want_grab(self):
        """Grab the pen away from xochitl when asked to, and always while calibrating (so taps
        don't open its menus), but never while the tablet screen runs: xochitl is stopped then,
        and a grab would starve the screen's own pen reader."""
        if self.companion.running:
            return False
        return bool(self.cfg["grab"]) or self.cal is not None

    def make_pen(self):
        if sys.platform != "win32":
            return NullPen()
        try:
            return rt.WindowsPen(feedback=self.cfg["feedback"])
        except Exception as ex:
            self.output_on = False
            self.set_status("error", f"Windows refused to create the pen: {ex}")
            return None

    def set_output(self, on):
        self.output_on = bool(on) and self.connected
        self.sync_companion()
        self.changed()

    # ------------------------------------------------------------------ tablet screen

    def set_screen(self, on):
        if on:
            if self.worker and self.worker.tab:
                self.companion.start(self.worker.tab)
        else:
            self.companion.stop()
        self.changed()

    def position(self):
        """The chosen desk position, or the one the calibration implies, or None."""
        for key, p in POSITIONS.items():
            if p["name"] == self.cfg.get("companion_rot"):
                return key
        if self.calibration:
            rot = self.calibrated_rotation()
            return next((k for k, p in POSITIONS.items() if p["rot"] == rot), None)
        return None

    def calibrated_rotation(self):
        c = self.calibration or {}
        key = (bool(c.get("swap")), bool(c.get("flip_u")), bool(c.get("flip_v")))
        return {(False, False, False): 0, (True, True, False): 3, (True, False, True): 1,
                (False, True, True): 2}.get(key)

    def position_mismatch(self):
        pos = self.position()
        if not pos or not (self.calibration or {}).get("homography"):
            return False
        return POSITIONS[pos]["rot"] != self.calibrated_rotation()

    def set_position(self, key):
        p = POSITIONS.get(key)
        if not p:
            return
        with self.lock:
            self.cfg["companion_rot"] = p["name"]
            if not (self.calibration or {}).get("homography"):
                self.cfg["orientation"] = p["orientation"]  # right direction even before calibrating
            self.refresh_mapper()
            self.save()
        self.sync_companion()
        self.changed()

    def companion_rotation(self):
        """Quarter turns for the tablet screen; 'Match calibration' reads it from how the
        calibrated frame sits on the digitizer (digitizer axes run with the panel's pixels)."""
        rot = ROTATIONS.get(self.cfg.get("companion_rot"))
        if rot is not None:
            return rot
        return self.calibrated_rotation() or 0

    def sync_companion(self):
        c = self.companion
        if not c.running:
            return
        self.refresh_mapper()  # the panel's share of the tablet depends on mode and rotation
        c.send(f"rot {self.companion_rotation()}")
        shown = self.panel_shown()
        c.send(f"panel {self.cfg.get('panel', 'left') if shown else 'off'} {PANEL_PX / self.view_size()[0]:.4f}")
        c.send(f"panelopen {int(bool(self.cfg.get('panel_open', True)))}")
        c.send(f"gestures {int(bool(self.cfg.get('gestures', True)))} {int(self.cfg.get('swipe_step', 100))} "
               f"{float(self.cfg.get('palm_size', 0))} {int(self.touch_mode() == 'windows')}")
        c.send("buttons " + json.dumps(self.companion_buttons(), separators=(",", ":")))
        profs = self.cfg["panel_profiles"]
        c.send(f"profile {self.cfg['panel_profile']} {len(profs)} {profs[self.cfg['panel_profile']]['name']}")
        c.send("area " + " ".join(f"{a:.4f}" for a in self.effective_area()))
        c.send(f"fade {int(self.cfg['trail_ms'])}")
        c.send(f"state pen {int(self.output_on)} snap {int(bool(self.cfg.get('snap')))} light {int(bool(self.cfg.get('light')))}")
        c.send(f"paper {self.cfg.get('paper', 'plain')} {float(self.cfg.get('paper_mm', 5)) * PANEL_PX_PER_MM:.1f}")
        try:
            remote = c.trace_remote(self.cfg.get("trace_file"))
        except Exception as ex:
            remote = ""
            c.send(f"toast Tracing image not sent: {ex}")
        c.send(f"trace {float(self.cfg['trace_opacity']):.2f} {float(self.cfg['trace_scale']):.4f} "
               f"{float(self.cfg['trace_x']):.1f} {float(self.cfg['trace_y']):.1f} {remote}")
        c.send(f"traceadjust {int(bool(self.cfg.get('trace_adjust')) and bool(remote))}")
        c.send(f"mask {int(bool(self.cfg.get('mask_on')))} {int(bool(self.cfg.get('mask_edit')))} {clean_mask(self.cfg.get('mask'))}")
        c.send("status Connected to PC. Pen " + ("on" if self.output_on else "off")
               + (", shapes on" if self.cfg.get("snap") else ""))
        if self.cal is not None:
            c.send("mode targets")
            c.send(f"next {self.cal_number(self.cal['mode'], len(self.cal['taps']))}")
        else:
            c.send("mode ink")

    # ------------------------------------------------------------------ calibration

    def cal_targets(self, mode):
        m = CAL_MODES[mode]
        if m["numbers"] is None:
            return rt.grid_targets(4, 0.0)
        grid = rt.grid_targets(9, m["inset"])
        return [grid[n - 1] for n in m["numbers"]]

    def cal_number(self, mode, i):
        nums = CAL_MODES[mode]["numbers"]
        return nums[i] if nums and i < len(nums) else 0

    def cal_prompt(self, mode, i):
        nums = CAL_MODES[mode]["numbers"]
        if nums:
            if i >= len(nums):
                return "Done"
            return f"Tap the centre of crosshair {nums[i]} ({TARGET_NAMES[nums[i] - 1]})"
        return f"Tap the {CORNER_NAMES[min(i, 3)]} corner of the screen"

    def start_calibration(self, mode):
        if mode not in CAL_MODES or not self.connected:
            return
        self.cal = {"mode": mode, "taps": []}
        self.cal_result = None
        self.sync_companion()
        self.changed()

    def cancel_calibration(self):
        self.cal = None
        self.sync_companion()
        self.changed()

    def on_tap(self, raw):
        cal = self.cal
        if cal is None:
            return
        cal["taps"].append(raw)
        targets = self.cal_targets(cal["mode"])
        if len(cal["taps"]) < len(targets):
            self.companion.send(f"next {self.cal_number(cal['mode'], len(cal['taps']))}")
            self.changed()
            return
        self.cal = None
        try:
            result = rt.grid_calibration(self.pen["abs"], cal["taps"][:len(targets)], targets)
        except ValueError as ex:
            self.cal_result = {"ok": False, "text": f"That didn't work: {ex}. Please try again."}
            self.sync_companion()
            self.changed()
            return
        result["device"] = self.pen["name"]
        result["date"] = time.strftime("%Y-%m-%d %H:%M")
        self.calibration = result
        self.cfg["orientation"] = "as-calibrated"  # the taps already capture how the tablet sits
        res = result.get("residual_mm") or []
        worst = max(range(len(res)), key=lambda k: res[k]) if res else None
        text = "Calibrated."
        if worst is not None and res[worst] > 2.0:
            text += f" Point {worst + 1} was {res[worst]:.1f} mm off; probably a mis-tap, consider redoing it."
        self.cal_result = {"ok": True, "text": text}
        self.save()
        self.refresh_mapper()
        self.sync_companion()
        self.changed()

    # ------------------------------------------------------------------ setup (onboarding)

    def install_key(self, password):
        """One-time: log in with the tablet's password, add this computer's key, verify the key
        works. The password is used once and never stored."""
        import paramiko
        pub = ensure_app_key()
        try:
            tab = rt.Tablet(self.cfg["host"], self.cfg["user"], password)
        except paramiko.AuthenticationException:
            return {"ok": False, "text": "The tablet didn't accept that password. Check it on the tablet "
                                         "(it's case-sensitive) and try again."}
        except Exception as ex:
            return {"ok": False, "text": self.explain(ex)}
        try:
            quoted = pub.replace("'", "")
            code, _, err = tab.run(
                "mkdir -p /home/root/.ssh && chmod 700 /home/root/.ssh && "
                "touch /home/root/.ssh/authorized_keys && "
                f"(grep -qxF '{quoted}' /home/root/.ssh/authorized_keys || "
                f"echo '{quoted}' >> /home/root/.ssh/authorized_keys) && "
                "chmod 600 /home/root/.ssh/authorized_keys")
        finally:
            tab.close()
        if code != 0:
            return {"ok": False, "text": f"Couldn't save the key on the tablet: {err.decode(errors='replace')}"}
        try:
            rt.Tablet(self.cfg["host"], self.cfg["user"], None, keyfile=APP_KEY).close()
        except Exception as ex:
            return {"ok": False, "text": f"The key was saved but the test login failed: {ex}"}
        return {"ok": True, "text": "This computer can now connect without a password."}

    def finish_onboarding(self):
        self.set("onboarded", True)

    def touch_mode(self):
        return self.cfg["panel_profiles"][self.cfg["panel_profile"]].get("touch", "shortcuts")

    def on_touch(self, parts):
        """'touch live|dead <palms> id p q ...' from the tablet: keep it for the live view and, in
        Windows-touch mode, pass the contacts on (dead = the pen or a palm took over: lift all)."""
        try:
            live, palms = parts[1] == "live", int(parts[2])
            pts = [(parts[i], float(parts[i + 1]), float(parts[i + 2])) for i in range(3, len(parts) - 2, 3)]
        except (IndexError, ValueError):
            return
        self.touch = {"live": live, "palms": palms, "pts": [[p, q] for _, p, q in pts], "at": time.time()}
        if self.touch_mode() != "windows" or self.mapper is None:
            if self.touch_out:
                self.touch_out.lift_all()
            return
        if self.touch_out is None:
            try:
                self.touch_out = rt.WindowsTouch() if sys.platform == "win32" else None
            except OSError as ex:
                self.set_status("error", f"Windows touch isn't available: {ex}")
                return
        if self.touch_out:
            self.touch_out.update({cid: self.mapper.frac_to_screen(p, q) for cid, p, q in pts} if live else {})

    def shutdown(self):
        if self.touch_out:
            self.touch_out.close()
        self.mouse.end()
        self.keys.release_all()
        self._stop.set()
        self.companion.stop()
        if self.worker:
            self.worker.shutdown()
