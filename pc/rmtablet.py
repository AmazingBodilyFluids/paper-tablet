#!/usr/bin/env python3
"""
rmtablet: use a reMarkable Paper Pro as a Windows Ink pen tablet.

Pipeline:  tablet /dev/input/eventN  --ssh-->  evdev parser  -->  pen state machine
           -->  coordinate mapper  -->  Windows synthetic pen (InjectSyntheticPointerInput)

Nothing is installed on the tablet. A small static helper (rmpp-pen) is copied to /tmp
on each connect; it reports the real axis ranges via EVIOCGABS and streams raw events.

Commands:
  python rmtablet.py info        show every input device with its real ranges (JSON)
  python rmtablet.py watch       print decoded pen events (verify hover / tilt / eraser)
  python rmtablet.py calibrate   learn the digitizer orientation (two corner taps)
  python rmtablet.py run         drive the Windows pen (default command)
"""
import argparse
import ctypes
import getpass
import json
import math
import os
import struct
import sys
import threading
import time
from dataclasses import dataclass, field

HERE = os.path.dirname(os.path.abspath(__file__))
HELPER_LOCAL = os.path.join(HERE, "..", "tablet", "rmpp-pen")
CONFIG_PATH = os.path.join(HERE, "rmtablet.json")
REMOTE_DIRS = ["/tmp", "/home/root"]

# linux/input-event-codes.h
EV_SYN, EV_KEY, EV_ABS = 0x00, 0x01, 0x03
SYN_REPORT, SYN_DROPPED = 0, 3
ABS_X, ABS_Y, ABS_PRESSURE, ABS_DISTANCE, ABS_TILT_X, ABS_TILT_Y = 0x00, 0x01, 0x18, 0x19, 0x1A, 0x1B
BTN_TOOL_PEN, BTN_TOOL_RUBBER, BTN_TOUCH, BTN_STYLUS = 0x140, 0x141, 0x14A, 0x14B


# --------------------------------------------------------------------------- tablet side

class Tablet:
    """SSH session to the tablet plus the rmpp-pen helper."""

    def __init__(self, host, user, password, port=22, keyfile=None):
        import paramiko  # imported lazily so the unit tests run without it
        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.client.connect(host, port=port, username=user, password=password,
                            key_filename=keyfile, look_for_keys=keyfile is None and password is None,
                            allow_agent=password is None, timeout=8, compress=False)
        self.client.get_transport().set_keepalive(5)
        self.helper = None

    def run(self, cmd, stdin_bytes=None):
        chan = self.client.get_transport().open_session()
        chan.exec_command(cmd)
        if stdin_bytes is not None:
            chan.sendall(stdin_bytes)
            chan.shutdown_write()
        out = b"".join(iter(lambda: chan.recv(65536), b""))
        err = b"".join(iter(lambda: chan.recv_stderr(65536), b""))
        return chan.recv_exit_status(), out, err

    def install_helper(self):
        """Copy rmpp-pen with `cat >` (works without sftp-server) and check it executes."""
        with open(HELPER_LOCAL, "rb") as f:
            blob = f.read()
        # Helpers left behind by an earlier session whose SSH side vanished get re-parented to
        # PID 1; some still hold an EVIOCGRAB, which keeps the pen from xochitl and the
        # companion. Only those orphans are stopped; a helper another live session owns is kept.
        self.run('for d in /proc/[0-9]*; do [ "$(cat $d/comm 2>/dev/null)" = rmpp-pen ] || continue; '
                 'set -- $(cat $d/stat 2>/dev/null); [ "$4" = 1 ] && kill ${d#/proc/}; done; true')
        errors = []
        for d in REMOTE_DIRS:
            path = f"{d}/rmpp-pen"
            # Write beside it and rename, so a copy that is still running never blocks the update.
            self.run(f"cat > {path}.new && chmod 755 {path}.new && mv -f {path}.new {path}", blob)
            code, out, err = self.run(f"{path} info")
            if code == 0 and out.startswith(b"{"):
                self.helper = path
                return json.loads(out)
            errors.append(f"{path}: exit {code} {err.decode(errors='replace').strip()}")
        raise RuntimeError("could not run helper on tablet: " + "; ".join(errors))

    def stream(self, dev, grab=False):
        """Raw event stream. A newline goes to the helper every second; if they stop (cable
        pulled, PC asleep) it releases the pen on its own after a few seconds, because the
        tablet never hears that such a connection is gone."""
        chan = self.client.get_transport().open_session()
        chan.exec_command(f"{self.helper} stream {dev}{' grab' if grab else ''} keepalive")

        def keepalive():
            while not chan.closed:
                try:
                    chan.sendall(b"\n")
                except Exception:
                    return
                time.sleep(1)

        threading.Thread(target=keepalive, daemon=True).start()
        return chan

    def close(self):
        self.client.close()


def pick_pen(info, override=None):
    devs = [d for d in info["devices"] if "abs" in d]
    if override:
        for d in devs:
            if d["path"] == override:
                return d
        raise RuntimeError(f"{override} not found on tablet")
    pens = [d for d in devs if "TOOL_PEN" in d["keys"] and "PRESSURE" in d["abs"] and "X" in d["abs"]]
    if not pens:
        raise RuntimeError("no pen digitizer found; run `info` and pass --device")
    return pens[0]


def event_format(info):
    size = info.get("input_event_size", 24)
    # struct input_event { struct timeval time; __u16 type; __u16 code; __s32 value; }
    return {24: "<qqHHi", 16: "<llHHi"}[size]


# --------------------------------------------------------------------------- pen state

@dataclass
class PenState:
    x: int = 0
    y: int = 0
    pressure: int = 0
    distance: int = 0
    tilt_x: int = 0
    tilt_y: int = 0
    pen: bool = False
    rubber: bool = False
    touch: bool = False
    barrel: bool = False
    t: float = 0.0  # kernel timestamp of the report (seconds), for measuring the report rate

    @property
    def in_range(self):
        return self.pen or self.rubber


class EvdevParser:
    """Splits a byte stream into input_event records and yields a PenState copy per SYN_REPORT."""

    def __init__(self, fmt):
        self.st = struct.Struct(fmt)
        self.buf = b""
        self.state = PenState()
        self.dropping = False

    def feed(self, data):
        self.buf += data
        n = len(self.buf) // self.st.size * self.st.size
        chunk, self.buf = self.buf[:n], self.buf[n:]
        s = self.state
        for sec, usec, etype, code, value in self.st.iter_unpack(chunk):
            if etype == EV_SYN:
                if code == SYN_DROPPED:
                    self.dropping = True  # kernel buffer overrun: discard until next report
                elif code == SYN_REPORT:
                    s.t = sec + usec / 1e6
                    if not self.dropping:
                        yield PenState(**vars(s))
                    self.dropping = False
            elif etype == EV_ABS:
                if code == ABS_X: s.x = value
                elif code == ABS_Y: s.y = value
                elif code == ABS_PRESSURE: s.pressure = value
                elif code == ABS_DISTANCE: s.distance = value
                elif code == ABS_TILT_X: s.tilt_x = value
                elif code == ABS_TILT_Y: s.tilt_y = value
            elif etype == EV_KEY:
                if code == BTN_TOOL_PEN: s.pen = bool(value)
                elif code == BTN_TOOL_RUBBER: s.rubber = bool(value)
                elif code == BTN_TOUCH: s.touch = bool(value)
                elif code == BTN_STYLUS: s.barrel = bool(value)


# --------------------------------------------------------------------------- mapping

ORIENTATIONS = ("as-calibrated", "portrait", "landscape-cw", "landscape-ccw", "portrait-flipped")


@dataclass
class Mapper:
    """Raw digitizer units -> screen pixels.

    Calibration (swap/flip) turns raw axes into (u, v) in the frame the tablet was calibrated
    in, u to the right and v down, both 0..1. Calibrating in the position the tablet sits on the
    desk absorbs any rotation ("as-calibrated", same as "portrait"); the other orientations
    rotate further, and the result is fitted onto the monitor rectangle.
    """
    absinfo: dict
    monitor: tuple  # left, top, right, bottom in pixels
    orientation: str = "portrait"
    fit: str = "crop"  # crop: whole monitor reachable; letterbox: whole tablet used; stretch
    swap: bool = False
    invert_tilt_y: bool = True
    flip_u: bool = False
    flip_v: bool = False
    pressure_gamma: float = 1.0
    pressure_threshold: float = 0.0
    area: tuple = (0.0, 0.0, 1.0, 1.0)  # sub-area of the oriented tablet, as fractions
    homography: tuple = None  # point calibration: normalized raw (x, y) -> (u, v)
    correction: dict = None  # 9-point residual mesh on top of the homography
    aspect: float = None  # measured width/height of the calibrated frame
    edge_lift: bool = True  # a stroke that leaves the mapped area lifts instead of running along the screen edge

    def _norm(self, axis, value):
        a = self.absinfo[axis]
        return min(1.0, max(0.0, (value - a["min"]) / ((a["max"] - a["min"]) or 1)))

    def tablet_aspect(self):
        """Physical width/height of the calibrated frame (uses axis resolution if any)."""
        if self.aspect:
            return self.aspect
        ax, ay = self.absinfo["X"], self.absinfo["Y"]
        w, h = 3.0, 4.0  # Paper Pro panel is 1620 x 2160
        if ax.get("res") and ay.get("res"):
            rw = (ax["max"] - ax["min"]) / ax["res"]
            rh = (ay["max"] - ay["min"]) / ay["res"]
            # The Paper Pro's Elan driver reports bogus resolutions (2832/2064 -> a 4 x 7 mm
            # "tablet"), so only trust them when they give a plausible panel shape.
            if 0.65 <= min(rw, rh) / max(rw, rh) <= 0.85:
                w, h = rw, rh
        if self.swap:
            w, h = h, w
        return w / h

    def uv(self, x, y):
        nx, ny = self._norm("X", x), self._norm("Y", y)
        if self.homography:
            u, v = _apply_h(self.homography, nx, ny)
            if self.correction:
                u, v = _correct(self.correction, u, v)
            return min(1.0, max(0.0, u)), min(1.0, max(0.0, v))
        u, v = (ny, nx) if self.swap else (nx, ny)
        if self.flip_u: u = 1 - u
        if self.flip_v: v = 1 - v
        return u, v

    def oriented(self, u, v):
        if self.orientation == "landscape-cw":   return 1 - v, u
        if self.orientation == "landscape-ccw":  return v, 1 - u
        if self.orientation == "portrait-flipped": return 1 - u, 1 - v
        return u, v

    def oriented_aspect(self):
        aspect = self.tablet_aspect()
        if self.orientation.startswith("landscape"):
            aspect = 1 / aspect
        return aspect

    def _fit_scale(self):
        """(axis, scale) applied around the area centre: axis 0 = horizontal, 1 = vertical."""
        ax0, ay0, ax1, ay1 = self.area
        aspect = self.oriented_aspect() * (ax1 - ax0) / (ay1 - ay0)
        l, t, r, b = self.monitor
        m_aspect = (r - l) / (b - t)
        if self.fit == "stretch":
            return 0, 1.0
        if (self.fit == "crop") == (aspect > m_aspect):
            # use a horizontal slice of the tablet (crop) / pad the monitor vertically (letterbox)
            return 0, aspect / m_aspect
        return 1, m_aspect / aspect

    def used_region(self):
        """Part of the oriented tablet (fractions) that lands on the monitor, for previews."""
        ax0, ay0, ax1, ay1 = self.area
        axis, scale = self._fit_scale()
        half = 0.5 / scale if scale > 1 else 0.5
        if axis == 0:
            c, w = (ax0 + ax1) / 2, (ax1 - ax0)
            return c - half * w, ay0, c + half * w, ay1
        c, h = (ay0 + ay1) / 2, (ay1 - ay0)
        return ax0, c - half * h, ax1, c + half * h

    def _pq(self, x, y):
        """Position on the monitor as fractions, before clamping (outside 0..1 = off the mapped area)."""
        return self.fit_frac(*self.oriented(*self.uv(x, y)))

    def fit_frac(self, p, q):
        """Oriented tablet fractions (as the tablet screen shows them) -> monitor fractions."""
        ax0, ay0, ax1, ay1 = self.area
        p = (p - ax0) / (ax1 - ax0)
        q = (q - ay0) / (ay1 - ay0)
        axis, scale = self._fit_scale()
        if axis == 0:
            p = (p - 0.5) * scale + 0.5
        else:
            q = (q - 0.5) * scale + 0.5
        return p, q

    def inside(self, x, y):
        p, q = self._pq(x, y)
        return 0.0 <= p <= 1.0 and 0.0 <= q <= 1.0

    def frac_to_screen(self, p, q):
        """A finger at oriented tablet fractions -> monitor pixel (clamped), like the pen."""
        return self._pixel(*self.fit_frac(p, q))

    def to_screen(self, x, y):
        return self._pixel(*self._pq(x, y))

    def _pixel(self, p, q):
        l, t, r, b = self.monitor
        mw, mh = r - l, b - t
        p = min(1.0, max(0.0, p))
        q = min(1.0, max(0.0, q))
        return int(round(l + p * (mw - 1))), int(round(t + q * (mh - 1)))

    def pressure(self, raw):
        n = self._norm("PRESSURE", raw)
        if n <= self.pressure_threshold:
            return 0
        n = (n - self.pressure_threshold) / (1 - self.pressure_threshold)
        return max(1, min(1024, int(round(1024 * n ** self.pressure_gamma))))

    def tilt(self, axis, raw):
        """evdev tilt -> degrees (-90..90). Kernel doc: tilt resolution is units per radian."""
        a = self.absinfo.get(axis)
        if not a:
            return 0
        if a.get("res"):
            deg = math.degrees(raw / a["res"])
        elif max(abs(a["min"]), abs(a["max"])) >= 900:
            deg = raw / 100.0  # centidegrees, as on the reMarkable 2 (+-9000)
        else:
            deg = raw
        return int(max(-90, min(90, round(deg))))

    def tilt_xy(self, tx, ty):
        """Rotate the tilt vector with the same calibration and orientation as position."""
        dx, dy = self.tilt("TILT_X", tx), self.tilt("TILT_Y", ty)
        # The Paper Pro's TILT_Y runs opposite to its position Y axis (TILT_X agrees with X).
        # Measured 2026-10-27 in left-side-up landscape: leaning the pen top right read -60
        # and left read +60, while toward/away from the user read correctly.
        if self.invert_tilt_y: dy = -dy
        if self.swap: dx, dy = dy, dx
        if self.flip_u: dx = -dx
        if self.flip_v: dy = -dy
        if self.orientation == "landscape-cw":   dx, dy = -dy, dx
        elif self.orientation == "landscape-ccw": dx, dy = dy, -dx
        elif self.orientation == "portrait-flipped": dx, dy = -dx, -dy
        return dx, dy


def _solve(a, b):
    """Gaussian elimination with partial pivoting (small dense systems, no numpy)."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(m[r][c]))
        if abs(m[piv][c]) < 1e-12:
            raise ValueError("calibration points are degenerate (taps too close or in a line)")
        m[c], m[piv] = m[piv], m[c]
        for r in range(n):
            if r != c:
                f = m[r][c] / m[c][c]
                m[r] = [x - f * y for x, y in zip(m[r], m[c])]
    return [m[i][n] / m[i][i] for i in range(n)]


PANEL_MM = (179.8, 239.8)  # Paper Pro active area along raw X and raw Y (11.8" 4:3)


def grid_targets(n, inset=0.1):
    """Target positions (u, v) in reading order: n=4 corners, n=9 a 3x3 grid."""
    lo, hi = inset, 1 - inset
    if n == 4:
        return [(lo, lo), (hi, lo), (hi, hi), (lo, hi)]
    ticks = [lo, 0.5, hi]
    return [(u, v) for v in ticks for u in ticks]


def _apply_h(h, x, y):
    w = h[6] * x + h[7] * y + h[8]
    return (h[0] * x + h[1] * y + h[2]) / w, (h[3] * x + h[4] * y + h[5]) / w


def grid_calibration(absinfo, taps, targets):
    """Raw taps on known targets (u, v as seen from the user's seat) -> calibration dict.

    A least-squares homography absorbs rotation, mirroring, offset and keystone. With a
    3x3 grid the leftover error at each point is kept as a bilinear correction mesh, so
    the mapping is exact at every target. Residuals (mm, before the mesh) are reported
    per point: a large one usually means a mis-tap rather than digitizer error."""
    ax, ay = absinfo["X"], absinfo["Y"]
    n = lambda p: ((p[0] - ax["min"]) / (ax["max"] - ax["min"]), (p[1] - ay["min"]) / (ay["max"] - ay["min"]))
    src = [n(p) for p in taps]
    rows, rhs = [], []
    for (x, y), (u, v) in zip(src, targets):
        rows.append([x, y, 1, 0, 0, 0, -u * x, -u * y]); rhs.append(u)
        rows.append([0, 0, 0, x, y, 1, -v * x, -v * y]); rhs.append(v)
    # normal equations (exact solve when there are 4 points)
    ata = [[sum(r[i] * r[j] for r in rows) for j in range(8)] for i in range(8)]
    atb = [sum(r[i] * b for r, b in zip(rows, rhs)) for i in range(8)]
    h = _solve(ata, atb) + [1.0]

    # physical size of the calibrated frame, from where the targets landed on the digitizer
    mmd = lambda p, q: math.hypot((p[0] - q[0]) * PANEL_MM[0], (p[1] - q[1]) * PANEL_MM[1])
    def span(i, j, k):  # distance between taps i and j over their target spacing on axis k
        return mmd(src[i], src[j]) / abs(targets[j][k] - targets[i][k])
    corners = [targets.index(t) for t in grid_targets(4, targets[0][0])]
    tl, tr, br, bl = corners
    width = (span(tl, tr, 0) + span(bl, br, 0)) / 2
    height = (span(tl, bl, 1) + span(tr, br, 1)) / 2

    res, resid_mm = [], []
    for (x, y), (u, v) in zip(src, targets):
        fu, fv = _apply_h(h, x, y)
        res.append((u - fu, v - fv))
        resid_mm.append(round(math.hypot((u - fu) * width, (v - fv) * height), 2))
    correction = None
    if len(targets) == 9:
        ticks = sorted({t[0] for t in targets})
        grid = [[None] * 3 for _ in range(3)]
        for (u, v), d in zip(targets, res):
            grid[ticks.index(v)][ticks.index(u)] = d
        correction = {"ticks": ticks, "grid": grid}

    dx, dy = src[tr][0] - src[tl][0], src[tr][1] - src[tl][1]
    swap = abs(dy) > abs(dx)
    if swap:
        flip_u, flip_v = dy < 0, (src[bl][0] - src[tl][0]) < 0
    else:
        flip_u, flip_v = dx < 0, (src[bl][1] - src[tl][1]) < 0
    return {"homography": h, "correction": correction, "aspect": width / height, "swap": swap,
            "flip_u": flip_u, "flip_v": flip_v, "taps": [list(t) for t in taps],
            "targets": [list(t) for t in targets], "residual_mm": resid_mm,
            "size_mm": [round(width, 1), round(height, 1)], "inset": targets[0][0]}


def corner_calibration(absinfo, tl, tr, br, bl, inset=0.0):
    """Four corner taps (or crosshairs `inset` in from each edge) -> calibration dict."""
    return grid_calibration(absinfo, [tl, tr, br, bl], grid_targets(4, inset))


def _correct(corr, u, v):
    """Add the residual mesh. The mesh is indexed by target position, so solve q = h + r(q)
    by fixed-point iteration; at each calibration tap that lands exactly on its target."""
    q = (u, v)
    for _ in range(4):
        d = _mesh(corr, *q)
        q = (u + d[0], v + d[1])
    return q


def _mesh(corr, u, v):
    """Bilinear interpolation of the residual mesh (held constant beyond the outer targets)."""
    t, g = corr["ticks"], corr["grid"]
    def cell(x):
        x = min(t[-1], max(t[0], x))
        i = 0 if x <= t[1] else 1
        return i, (x - t[i]) / (t[i + 1] - t[i])
    i, fu = cell(u)
    j, fv = cell(v)
    d = [0.0, 0.0]
    for k in range(2):
        a = g[j][i][k] * (1 - fu) + g[j][i + 1][k] * fu
        b = g[j + 1][i][k] * (1 - fu) + g[j + 1][i + 1][k] * fu
        d[k] = a * (1 - fv) + b * fv
    return d


# --------------------------------------------------------------------------- frames

@dataclass
class Frame:
    """One Windows pointer frame, independent of the injection backend."""
    flags: set
    x: int
    y: int
    pressure: int = 0
    tilt_x: int = 0
    tilt_y: int = 0
    eraser: bool = False
    barrel: bool = False
    button_change: str = ""  # "down" / "up" for the tip, "" otherwise


class PenTranslator:
    """Turns successive PenStates into the frame sequence Windows Ink expects:
    hover = INRANGE|UPDATE, tip down = INRANGE|INCONTACT|DOWN, drag = ...|UPDATE,
    tip up = INRANGE|UP, leaving range = UPDATE with no INRANGE."""

    def __init__(self, mapper, has_tilt, has_touch_key=True):
        self.m = mapper
        self.has_tilt = has_tilt
        self.has_touch_key = has_touch_key  # without BTN_TOUCH, contact = pressure above threshold
        self.prev = PenState()
        self.last_xy = (0, 0)
        self.last_eraser = False

    def _frame(self, s, flags, change=""):
        x, y = self.m.to_screen(s.x, s.y)
        self.last_xy = (x, y)
        f = Frame(flags=set(flags), x=x, y=y, eraser=s.rubber, barrel=s.barrel, button_change=change)
        if "INCONTACT" in flags:
            f.pressure = self.m.pressure(s.pressure) or 1
        if self.has_tilt:
            f.tilt_x, f.tilt_y = self.m.tilt_xy(s.tilt_x, s.tilt_y)
        return f

    def leave(self):
        """Frames that end any pen activity (used on tool switch, disconnect and exit)."""
        out = []
        p = self.prev
        if p.in_range and p.touch:
            out.append(self._frame(p, {"INRANGE", "UP"}, "up"))
        if p.in_range:
            x, y = self.last_xy
            out.append(Frame(flags={"UPDATE"}, x=x, y=y, eraser=p.rubber))
        self.prev = PenState()
        return out

    def step(self, s):
        out = []
        p = self.prev
        if p.in_range and s.in_range and p.rubber != s.rubber:
            out += self.leave()  # Windows needs the pen to leave range to switch ends
            p = self.prev
        touching = (s.in_range and (s.touch or not self.has_touch_key)
                    and self.m.pressure(s.pressure) > 0)
        if touching and self.m.edge_lift and not self.m.inside(s.x, s.y):
            touching = False  # off the mapped area: hover at the edge rather than draw along it
        was_touching = p.in_range and p.touch
        if not s.in_range:
            if p.in_range:
                out += self.leave()
            self.prev = s
            return out
        if touching and not was_touching:
            out.append(self._frame(s, {"INRANGE", "INCONTACT", "DOWN"}, "down"))
        elif touching:
            out.append(self._frame(s, {"INRANGE", "INCONTACT", "UPDATE"}))
        elif was_touching:
            out.append(self._frame(s, {"INRANGE", "UP"}, "up"))
        else:
            out.append(self._frame(s, {"INRANGE", "UPDATE"}))
        s = PenState(**vars(s))
        s.touch = touching
        self.prev = s
        return out


# --------------------------------------------------------------------------- Windows output

def _pointer_api():
    """user32 with the synthetic-pointer calls typed, and the POINTER_TYPE_INFO structure."""
    from ctypes import wintypes as wt

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    class POINTER_INFO(ctypes.Structure):
        _fields_ = [("pointerType", wt.DWORD), ("pointerId", ctypes.c_uint32),
                    ("frameId", ctypes.c_uint32), ("pointerFlags", ctypes.c_uint32),
                    ("sourceDevice", wt.HANDLE), ("hwndTarget", wt.HWND),
                    ("ptPixelLocation", POINT), ("ptHimetricLocation", POINT),
                    ("ptPixelLocationRaw", POINT), ("ptHimetricLocationRaw", POINT),
                    ("dwTime", wt.DWORD), ("historyCount", ctypes.c_uint32),
                    ("InputData", ctypes.c_int32), ("dwKeyStates", wt.DWORD),
                    ("PerformanceCount", ctypes.c_uint64), ("ButtonChangeType", ctypes.c_int)]

    class POINTER_PEN_INFO(ctypes.Structure):
        _fields_ = [("pointerInfo", POINTER_INFO), ("penFlags", ctypes.c_uint32),
                    ("penMask", ctypes.c_uint32), ("pressure", ctypes.c_uint32),
                    ("rotation", ctypes.c_uint32), ("tiltX", ctypes.c_int32), ("tiltY", ctypes.c_int32)]

    class POINTER_TOUCH_INFO(ctypes.Structure):
        _fields_ = [("pointerInfo", POINTER_INFO), ("touchFlags", ctypes.c_uint32),
                    ("touchMask", ctypes.c_uint32), ("rcContact", RECT), ("rcContactRaw", RECT),
                    ("orientation", ctypes.c_uint32), ("pressure", ctypes.c_uint32)]

    class _U(ctypes.Union):  # union must be sized for the larger touch member
        _fields_ = [("touchInfo", POINTER_TOUCH_INFO), ("penInfo", POINTER_PEN_INFO)]

    class POINTER_TYPE_INFO(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = [("type", wt.DWORD), ("u", _U)]

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.CreateSyntheticPointerDevice.restype = wt.HANDLE
    user32.CreateSyntheticPointerDevice.argtypes = [wt.DWORD, wt.ULONG, wt.DWORD]
    user32.InjectSyntheticPointerInput.argtypes = [wt.HANDLE, ctypes.POINTER(POINTER_TYPE_INFO), ctypes.c_uint32]
    user32.DestroySyntheticPointerDevice.argtypes = [wt.HANDLE]
    return user32, POINTER_TYPE_INFO


class WindowsPen:
    """Synthetic Windows Ink pen via CreateSyntheticPointerDevice / InjectSyntheticPointerInput
    (Windows 10 1809+). Apps see a real pen with pressure and tilt, no driver required."""

    PT_PEN = 3
    FLAG = {"NEW": 0x1, "INRANGE": 0x2, "INCONTACT": 0x4, "FIRSTBUTTON": 0x10,
            "SECONDBUTTON": 0x20, "PRIMARY": 0x2000, "DOWN": 0x10000, "UPDATE": 0x20000, "UP": 0x40000}
    PEN_FLAG_BARREL, PEN_FLAG_INVERTED, PEN_FLAG_ERASER = 0x1, 0x2, 0x4
    PEN_MASK_PRESSURE, PEN_MASK_TILT_X, PEN_MASK_TILT_Y = 0x1, 0x4, 0x8
    FEEDBACK = {"default": 1, "indirect": 2, "none": 3}
    CHANGE = {"": 0, "down": 1, "up": 2}  # POINTER_CHANGE_FIRSTBUTTON_DOWN / _UP

    def __init__(self, feedback="indirect", pointer_id=0):
        self.user32, self.POINTER_TYPE_INFO = _pointer_api()
        self.dev = self.user32.CreateSyntheticPointerDevice(self.PT_PEN, 1, self.FEEDBACK[feedback])
        if not self.dev:
            raise OSError(f"CreateSyntheticPointerDevice failed: {ctypes.get_last_error()}")
        self.pointer_id = pointer_id
        self.frame_id = 0

    def send(self, f):
        info = self.POINTER_TYPE_INFO()
        info.type = self.PT_PEN
        pen = info.penInfo
        pi = pen.pointerInfo
        pi.pointerType = self.PT_PEN
        pi.pointerId = self.pointer_id
        self.frame_id += 1
        pi.frameId = self.frame_id
        flags = set(f.flags)
        if "INRANGE" in flags:
            flags.add("PRIMARY")
        if "INCONTACT" in flags:
            flags.add("FIRSTBUTTON")
        if f.barrel and "INRANGE" in flags:
            flags.add("SECONDBUTTON")
        pi.pointerFlags = sum(self.FLAG[n] for n in flags)
        pi.ptPixelLocation.x, pi.ptPixelLocation.y = f.x, f.y
        pi.ptPixelLocationRaw.x, pi.ptPixelLocationRaw.y = f.x, f.y
        pi.ButtonChangeType = self.CHANGE[f.button_change]
        pen.penFlags = ((self.PEN_FLAG_INVERTED if f.eraser else 0)
                        | (self.PEN_FLAG_ERASER if f.eraser and "INCONTACT" in flags else 0)
                        | (self.PEN_FLAG_BARREL if f.barrel else 0))
        pen.penMask = self.PEN_MASK_PRESSURE | self.PEN_MASK_TILT_X | self.PEN_MASK_TILT_Y
        pen.pressure = f.pressure
        pen.tiltX, pen.tiltY = f.tilt_x, f.tilt_y
        if not self.user32.InjectSyntheticPointerInput(self.dev, ctypes.byref(info), 1):
            err = ctypes.get_last_error()
            if err == 87 and self.pointer_id == 0 and self.frame_id == 1:
                self.pointer_id = 1  # some builds reject pointerId 0; retry once with 1
                self.frame_id = 0
                return self.send(f)
            print(f"InjectSyntheticPointerInput failed: error {err} flags={sorted(f.flags)}", file=sys.stderr)

    def close(self):
        if self.dev:
            self.user32.DestroySyntheticPointerDevice(self.dev)
            self.dev = None


class WindowsTouch:
    """Synthetic touchscreen (CreateSyntheticPointerDevice, PT_TOUCH): apps get real touch, so
    Windows does its own panning with inertia, pinch zoom and so on. update() takes every contact
    that is down, as {id: (x, y)} in screen pixels; contacts missing from it are lifted."""

    PT_TOUCH = 2
    MAX = 10

    def __init__(self):
        self.user32, self.POINTER_TYPE_INFO = _pointer_api()
        self.dev = self.user32.CreateSyntheticPointerDevice(self.PT_TOUCH, self.MAX, 1)
        if not self.dev:
            raise OSError(f"CreateSyntheticPointerDevice(touch) failed: {ctypes.get_last_error()}")
        self.slots = {}  # contact id -> (pointer id, x, y)
        self.frame = 0
        self.lock = threading.Lock()
        self.last = 0.0
        threading.Thread(target=self._keepalive, daemon=True).start()

    def _inject(self, items):
        arr = (self.POINTER_TYPE_INFO * len(items))()
        self.frame += 1
        for k, (pid, x, y, flags) in enumerate(items):
            info = arr[k]
            info.type = self.PT_TOUCH
            t = info.touchInfo
            pi = t.pointerInfo
            pi.pointerType = self.PT_TOUCH
            pi.pointerId = pid
            pi.frameId = self.frame
            pi.pointerFlags = flags | (WindowsPen.FLAG["PRIMARY"] if k == 0 and flags & 0x4 else 0)
            pi.ptPixelLocation.x = pi.ptPixelLocationRaw.x = x
            pi.ptPixelLocation.y = pi.ptPixelLocationRaw.y = y
            t.touchMask = 0x1  # TOUCH_MASK_CONTACTAREA
            t.rcContact.left, t.rcContact.right = x - 4, x + 4
            t.rcContact.top, t.rcContact.bottom = y - 4, y + 4
        if not self.user32.InjectSyntheticPointerInput(self.dev, arr, len(items)):
            print(f"touch inject failed: error {ctypes.get_last_error()}", file=sys.stderr)
        self.last = time.monotonic()

    def update(self, contacts):
        F = WindowsPen.FLAG
        held = F["INRANGE"] | F["INCONTACT"]
        with self.lock:
            items, new_slots = [], {}
            used = {v[0] for k, v in self.slots.items() if k in contacts}
            for cid, (x, y) in list(contacts.items())[:self.MAX]:
                if cid in self.slots:
                    pid = self.slots[cid][0]
                    items.append((pid, x, y, F["UPDATE"] | held))
                else:
                    pid = next(i for i in range(self.MAX) if i not in used)
                    used.add(pid)
                    items.append((pid, x, y, F["DOWN"] | held))
                new_slots[cid] = (pid, x, y)
            for cid, (pid, x, y) in self.slots.items():
                if cid not in new_slots:
                    items.append((pid, x, y, F["UP"]))
            self.slots = new_slots
            if items:
                self._inject(items)

    def _keepalive(self):
        # a contact that stops moving still has to be reported, or Windows cancels it
        F = WindowsPen.FLAG
        while self.dev:
            time.sleep(0.05)
            with self.lock:
                if self.slots and time.monotonic() - self.last > 0.08:
                    self._inject([(pid, x, y, F["UPDATE"] | F["INRANGE"] | F["INCONTACT"])
                                  for pid, x, y in self.slots.values()])

    def lift_all(self):
        self.update({})

    def close(self):
        if self.dev:
            self.lift_all()
            dev, self.dev = self.dev, None
            self.user32.DestroySyntheticPointerDevice(dev)


class PrintPen:
    """--dry-run backend: prints frames instead of injecting."""
    def send(self, f):
        print(f"{'+'.join(sorted(f.flags)):<28} {f.x:>5},{f.y:<5} p={f.pressure:<4} "
              f"tilt={f.tilt_x:>3},{f.tilt_y:<3}{' ERASER' if f.eraser else ''}")

    def close(self):
        pass


def monitors():
    """Monitor rectangles in physical pixels, primary first."""
    if sys.platform != "win32":
        return [(0, 0, 1920, 1080)]
    from ctypes import wintypes as wt
    user32 = ctypes.windll.user32
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # per-monitor v2
    except AttributeError:
        pass

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT), ("dwFlags", wt.DWORD)]

    found = []
    PROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HMONITOR, wt.HDC, ctypes.POINTER(wt.RECT), wt.LPARAM)

    def cb(hmon, hdc, rect, lparam):
        mi = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
        user32.GetMonitorInfoW(hmon, ctypes.byref(mi))
        r = mi.rcMonitor
        found.append((not (mi.dwFlags & 1), r.left, (r.left, r.top, r.right, r.bottom)))
        return True

    user32.EnumDisplayMonitors(None, None, PROC(cb), 0)
    return [m for _, _, m in sorted(found)]


# --------------------------------------------------------------------------- commands

def load_config():
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_config(cfg):
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)


def connect(args):
    import paramiko
    password = args.password or os.environ.get("RM_PASSWORD")
    try:
        # with no password given, try --key, ~/.ssh/id_* and the SSH agent first
        tab = Tablet(args.host, args.user, password, keyfile=args.key)
    except paramiko.AuthenticationException:
        if password is not None or args.key:
            raise
        password = getpass.getpass(f"root password for {args.host} (Settings > General > Help > "
                                   f"About > Copyrights and licenses): ")
        tab = Tablet(args.host, args.user, password)
    info = tab.install_helper()
    return tab, info


def cmd_info(args):
    tab, info = connect(args)
    print(json.dumps(info, indent=2))
    pen = pick_pen(info, args.device)
    print(f"\npen digitizer: {pen['path']} ({pen['name']})")
    for axis in ("X", "Y", "PRESSURE", "DISTANCE", "TILT_X", "TILT_Y"):
        print(f"  {axis:<9} {pen['abs'].get(axis, 'NOT REPORTED')}")
    print(f"  tools     {pen['keys']}")
    tab.close()


def cmd_watch(args):
    tab, info = connect(args)
    pen = pick_pen(info, args.device)
    print(f"watching {pen['path']}; hover, tilt and flip the pen; Ctrl+C to stop")
    chan = tab.client.get_transport().open_session()
    chan.exec_command(f"{tab.helper} watch {pen['path']}")
    try:
        for chunk in iter(lambda: chan.recv(4096), b""):
            sys.stdout.write(chunk.decode(errors="replace"))
            sys.stdout.flush()
    except KeyboardInterrupt:
        pass
    tab.close()


def wait_for_tap(parser, chan, label):
    print(f"  Tap the {label} of the screen with the pen tip...")
    while True:
        data = chan.recv(4096)
        if not data:
            raise RuntimeError("stream closed")
        for s in parser.feed(data):
            if s.touch or s.pressure > 0:
                xy = (s.x, s.y)
                while True:  # wait for lift so one tap counts once
                    for s2 in parser.feed(chan.recv(4096)):
                        if not (s2.touch or s2.pressure > 0):
                            print(f"    raw = {xy}")
                            return xy


def cmd_calibrate(args):
    tab, info = connect(args)
    pen = pick_pen(info, args.device)
    print("Put the tablet exactly where and how you will draw on it (any rotation is fine).")
    print("Tap as close to the screen corners as you can, as seen from where you sit:")
    chan = tab.stream(pen["path"], grab=True)  # grab so the taps don't hit xochitl buttons
    parser = EvdevParser(event_format(info))
    tl = wait_for_tap(parser, chan, "TOP-LEFT corner")
    tr = wait_for_tap(parser, chan, "TOP-RIGHT corner")
    tab.close()
    ax, ay = pen["abs"]["X"], pen["abs"]["Y"]
    nx = lambda v: (v - ax["min"]) / (ax["max"] - ax["min"])
    ny = lambda v: (v - ay["min"]) / (ay["max"] - ay["min"])
    dx, dy = nx(tr[0]) - nx(tl[0]), ny(tr[1]) - ny(tl[1])
    swap = abs(dy) > abs(dx)  # screen-horizontal travel happened on raw Y
    if swap:
        flip_u, flip_v = dy < 0, nx(tl[0]) > 0.5
    else:
        flip_u, flip_v = dx < 0, ny(tl[1]) > 0.5
    travel = max(abs(dx), abs(dy))
    if travel < 0.5:
        print(f"Warning: only {travel:.0%} of the axis range between the taps; corners may be off.")
    cfg = load_config()
    cfg["calibration"] = {"swap": swap, "flip_u": flip_u, "flip_v": flip_v,
                          "taps": {"top_left": tl, "top_right": tr}, "device": pen["name"]}
    save_config(cfg)
    print(f"Saved to {CONFIG_PATH}: swap={swap} flip_u={flip_u} flip_v={flip_v}")


def build_mapper(args, pen):
    cal = load_config().get("calibration")
    if cal is None:
        print("No calibration saved; assuming raw X=right, Y=down. Run `calibrate` if the "
              "cursor moves the wrong way.", file=sys.stderr)
        cal = {}
    mons = monitors()
    if args.monitor >= len(mons):
        raise SystemExit(f"monitor {args.monitor} not found ({len(mons)} available)")
    area = tuple(float(v) for v in args.area.split(",")) if args.area else (0.0, 0.0, 1.0, 1.0)
    return Mapper(absinfo=pen["abs"], monitor=mons[args.monitor], orientation=args.orientation,
                  fit=args.fit, swap=cal.get("swap", False), flip_u=cal.get("flip_u", False),
                  flip_v=cal.get("flip_v", False), homography=cal.get("homography"),
                  correction=cal.get("correction"),
                  aspect=cal.get("aspect"), pressure_gamma=args.pressure_gamma,
                  pressure_threshold=args.pressure_threshold, area=area)


def cmd_run(args):
    out = PrintPen() if args.dry_run else WindowsPen(feedback=args.feedback)
    delay = 1
    try:
        while True:
            translator = None
            try:
                tab, info = connect(args)
                pen = pick_pen(info, args.device)
                mapper = build_mapper(args, pen)
                has_tilt = "TILT_X" in pen["abs"] and not args.no_tilt
                translator = PenTranslator(mapper, has_tilt, has_touch_key="TOUCH" in pen["keys"])
                print(f"connected: {pen['path']} ({pen['name']}) -> monitor {mapper.monitor}, "
                      f"{args.orientation}, tilt={'on' if has_tilt else 'off'}, "
                      f"tablet UI {'blocked (grab)' if args.grab else 'still active'}")
                chan = tab.stream(pen["path"], grab=args.grab)
                parser = EvdevParser(event_format(info))
                delay = 1
                for chunk in iter(lambda: chan.recv(65536), b""):
                    for state in parser.feed(chunk):
                        for frame in translator.step(state):
                            out.send(frame)
                print("stream closed by tablet")
            except KeyboardInterrupt:
                raise
            except Exception as e:  # network drop, tablet sleep, auth error
                print(f"error: {e}", file=sys.stderr)
            if translator:
                for frame in translator.leave():
                    out.send(frame)
            print(f"reconnecting in {delay}s (Ctrl+C to quit)")
            time.sleep(delay)
            delay = min(delay * 2, 30)
    except KeyboardInterrupt:
        if translator:
            for frame in translator.leave():
                out.send(frame)
    finally:
        out.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=["run", "info", "watch", "calibrate"])
    ap.add_argument("--host", default="10.11.99.1", help="tablet address (USB default 10.11.99.1)")
    ap.add_argument("--user", default="root")
    ap.add_argument("--password", help="or set RM_PASSWORD; prompted if neither is given")
    ap.add_argument("--key", help="SSH private key file instead of password")
    ap.add_argument("--device", help="force an input device, e.g. /dev/input/event2")
    ap.add_argument("--monitor", type=int, default=0, help="0 = primary")
    ap.add_argument("--orientation", default="as-calibrated", choices=ORIENTATIONS,
                    help="extra rotation on top of calibration; leave as-calibrated if you "
                         "calibrated in the drawing position")
    ap.add_argument("--fit", default="crop", choices=["crop", "letterbox", "stretch"])
    ap.add_argument("--area", help="u0,v0,u1,v1 fractions of the tablet to use, e.g. 0.1,0.1,0.9,0.9")
    ap.add_argument("--pressure-gamma", type=float, default=1.0, help="<1 softer, >1 firmer")
    ap.add_argument("--pressure-threshold", type=float, default=0.0, help="fraction ignored as no contact")
    ap.add_argument("--no-tilt", action="store_true")
    ap.add_argument("--grab", action="store_true",
                    help="block the tablet's own UI from the pen (no ink on the e-ink screen)")
    ap.add_argument("--feedback", default="indirect", choices=["indirect", "default", "none"],
                    help="Windows pen visual feedback mode")
    ap.add_argument("--dry-run", action="store_true", help="print frames instead of injecting")
    args = ap.parse_args(argv)
    {"run": cmd_run, "info": cmd_info, "watch": cmd_watch, "calibrate": cmd_calibrate}[args.command](args)


if __name__ == "__main__":
    main()
