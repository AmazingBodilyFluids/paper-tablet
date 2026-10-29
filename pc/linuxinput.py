"""Linux back end: the pen, keys and mouse as virtual input devices (uinput), and the monitor
layout and focused window from the compositor.

Wayland has no API for injecting input, so Paper Tablet creates kernel input devices through
/dev/uinput, exactly as a USB tablet would appear: libinput and the compositor (Hyprland,
GNOME, KDE, Sway...) then treat them like real hardware. That needs write access to
/dev/uinput (see linux/README-linux.md: a udev rule, once).

Coordinates: monitors() returns rectangles in the compositor's logical layout. The pen and the
mouse report absolute positions over the bounding box of all monitors, and the compositor maps
an absolute device across the whole layout unless told otherwise, so a position here lands on
the same pixel there.
"""
import fcntl
import json
import os
import shutil
import struct
import subprocess

# ioctls (linux/uinput.h)
UI_DEV_CREATE, UI_DEV_DESTROY = 0x5501, 0x5502
UI_DEV_SETUP = 0x405C5503      # _IOW('U', 3, struct uinput_setup), 92 bytes
UI_ABS_SETUP = 0x401C5504      # _IOW('U', 4, struct uinput_abs_setup), 28 bytes
UI_SET_EVBIT, UI_SET_KEYBIT, UI_SET_RELBIT, UI_SET_ABSBIT = 0x40045564, 0x40045565, 0x40045566, 0x40045567
UI_SET_PROPBIT = 0x4004556E

EV_SYN, EV_KEY, EV_REL, EV_ABS = 0, 1, 2, 3
SYN_REPORT = 0
ABS_X, ABS_Y, ABS_PRESSURE, ABS_TILT_X, ABS_TILT_Y = 0x00, 0x01, 0x18, 0x1A, 0x1B
BTN_LEFT, BTN_RIGHT, BTN_MIDDLE = 0x110, 0x111, 0x112
BTN_TOOL_PEN, BTN_TOOL_RUBBER, BTN_TOUCH, BTN_STYLUS, BTN_STYLUS2 = 0x140, 0x141, 0x14A, 0x14B, 0x14C
ABS_MT_SLOT, ABS_MT_POSITION_X, ABS_MT_POSITION_Y, ABS_MT_TRACKING_ID = 0x2F, 0x35, 0x36, 0x39
BTN_TOOL_FINGER = 0x145
INPUT_PROP_DIRECT = 0x01
REL_WHEEL = 0x08
BUS_VIRTUAL = 0x06
EVENT = struct.Struct("llHHi")  # struct input_event: timeval, type, code, value

# Key names as core/keys.py knows them -> Linux key codes (linux/input-event-codes.h)
KEY = {"esc": 1, "1": 2, "2": 3, "3": 4, "4": 5, "5": 6, "6": 7, "7": 8, "8": 9, "9": 10, "0": 11,
       "-": 12, "=": 13, "backspace": 14, "tab": 15, "q": 16, "w": 17, "e": 18, "r": 19, "t": 20,
       "y": 21, "u": 22, "i": 23, "o": 24, "p": 25, "[": 26, "]": 27, "enter": 28, "ctrl": 29,
       "a": 30, "s": 31, "d": 32, "f": 33, "g": 34, "h": 35, "j": 36, "k": 37, "l": 38, ";": 39,
       "'": 40, "`": 41, "shift": 42, "\\": 43, "z": 44, "x": 45, "c": 46, "v": 47, "b": 48, "n": 49,
       "m": 50, ",": 51, ".": 52, "/": 53, "alt": 56, "space": 57,
       "home": 102, "up": 103, "pageup": 104, "left": 105, "right": 106, "end": 107, "down": 108,
       "pagedown": 109, "insert": 110, "delete": 111, "win": 125}
KEY.update({f"f{n}": 58 + n for n in range(1, 11)})       # F1..F10 = 59..68
KEY.update({"f11": 87, "f12": 88})
KEY.update({f"f{n}": 183 + n - 13 for n in range(13, 25)})  # F13..F24 = 183..194


def available():
    return os.access("/dev/uinput", os.W_OK)


class UInput:
    """One virtual input device."""

    def __init__(self, name, keys=(), abs_axes=None, rels=(), props=(), vendor=0x1, product=0x1):
        self.fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
        try:
            if keys:
                fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_KEY)
                for k in keys:
                    fcntl.ioctl(self.fd, UI_SET_KEYBIT, k)
            if rels:
                fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_REL)
                for r in rels:
                    fcntl.ioctl(self.fd, UI_SET_RELBIT, r)
            if abs_axes:
                fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_ABS)
                for code, (lo, hi, res) in abs_axes.items():
                    fcntl.ioctl(self.fd, UI_SET_ABSBIT, code)
                    # struct uinput_abs_setup: __u16 code; struct input_absinfo (value min max fuzz flat res)
                    fcntl.ioctl(self.fd, UI_ABS_SETUP, struct.pack("<Hxxiiiiii", code, lo, lo, hi, 0, 0, res))
            for p in props:
                fcntl.ioctl(self.fd, UI_SET_PROPBIT, p)
            # struct uinput_setup: struct input_id (bustype vendor product version), name[80], ff_effects_max
            setup = struct.pack("<HHHH80sI", BUS_VIRTUAL, vendor, product, 1, name.encode()[:79], 0)
            fcntl.ioctl(self.fd, UI_DEV_SETUP, setup)
            fcntl.ioctl(self.fd, UI_DEV_CREATE)
        except Exception:
            os.close(self.fd)
            raise

    def emit(self, events):
        """events: (type, code, value) tuples; a SYN_REPORT is appended."""
        data = b"".join(EVENT.pack(0, 0, t, c, v) for t, c, v in events)
        os.write(self.fd, data + EVENT.pack(0, 0, EV_SYN, SYN_REPORT, 0))

    def close(self):
        if self.fd is not None:
            try:
                fcntl.ioctl(self.fd, UI_DEV_DESTROY)
            finally:
                os.close(self.fd)
                self.fd = None


def layout_box(mons=None):
    mons = mons or monitors()
    return (min(m[0] for m in mons), min(m[1] for m in mons), max(m[2] for m in mons), max(m[3] for m in mons))


class LinuxPen:
    """Virtual pen tablet: hover, tip, pressure, tilt and the eraser end, like a USB tablet.
    Takes the same Frames as rmtablet.WindowsPen."""

    PRESSURE_MAX = 1024  # Frame pressure is 0..1024

    def __init__(self, box=None, make=UInput):
        self.box = box or layout_box()
        l, t, r, b = self.box
        res = 10  # units per mm; libinput wants a resolution for tablets, any plausible one works
        self.dev = make("Paper Tablet Pen", keys=(BTN_TOOL_PEN, BTN_TOOL_RUBBER, BTN_TOUCH, BTN_STYLUS, BTN_STYLUS2),
                        abs_axes={ABS_X: (0, max(1, r - l - 1), res), ABS_Y: (0, max(1, b - t - 1), res),
                                  ABS_PRESSURE: (0, self.PRESSURE_MAX, 0),
                                  ABS_TILT_X: (-90, 90, 57), ABS_TILT_Y: (-90, 90, 57)},  # 57 units/rad = degrees
                        vendor=0x2D1F, product=0x0001)
        self.tool = None   # BTN_TOOL_PEN / BTN_TOOL_RUBBER while in range
        self.touch = False

    def send(self, f):
        l, t, r, b = self.box
        x, y = min(max(f.x - l, 0), r - l - 1), min(max(f.y - t, 0), b - t - 1)
        ev = []
        if "INRANGE" not in f.flags:  # leaving range
            if self.tool is not None:
                if self.touch:
                    ev += [(EV_KEY, BTN_TOUCH, 0), (EV_ABS, ABS_PRESSURE, 0)]
                ev += [(EV_ABS, ABS_X, x), (EV_ABS, ABS_Y, y), (EV_KEY, self.tool, 0)]
                self.dev.emit(ev)
            self.tool, self.touch = None, False
            return
        tool = BTN_TOOL_RUBBER if f.eraser else BTN_TOOL_PEN
        if self.tool is not None and tool != self.tool:
            self.dev.emit([(EV_KEY, BTN_TOUCH, 0), (EV_ABS, ABS_PRESSURE, 0), (EV_KEY, self.tool, 0)]
                          if self.touch else [(EV_KEY, self.tool, 0)])
            self.tool, self.touch = None, False
        ev += [(EV_ABS, ABS_X, x), (EV_ABS, ABS_Y, y),
               (EV_ABS, ABS_TILT_X, max(-90, min(90, f.tilt_x))), (EV_ABS, ABS_TILT_Y, max(-90, min(90, f.tilt_y)))]
        touching = "INCONTACT" in f.flags
        ev.append((EV_ABS, ABS_PRESSURE, min(self.PRESSURE_MAX, f.pressure) if touching else 0))
        if self.tool is None:
            ev.append((EV_KEY, tool, 1))
            self.tool = tool
        if touching != self.touch:
            ev.append((EV_KEY, BTN_TOUCH, int(touching)))
            self.touch = touching
        ev.append((EV_KEY, BTN_STYLUS, int(bool(f.barrel))))
        self.dev.emit(ev)

    def close(self):
        if self.dev:
            self.dev.close()
            self.dev = None


class LinuxTouch:
    """Virtual touchscreen (multitouch protocol B), for profiles that hand fingers to the desktop:
    apps then scroll with their own inertia and pinch-zoom. Same update() as rmtablet.WindowsTouch:
    every contact that is down, {id: (x, y)} in layout pixels; missing ones are lifted."""

    MAX = 10

    def __init__(self, box=None, make=UInput):
        self.box = box or layout_box()
        l, t, r, b = self.box
        w, h = max(1, r - l - 1), max(1, b - t - 1)
        self.dev = make("Paper Tablet Touch", keys=(BTN_TOUCH, BTN_TOOL_FINGER),
                        abs_axes={ABS_X: (0, w, 10), ABS_Y: (0, h, 10), ABS_MT_SLOT: (0, self.MAX - 1, 0),
                                  ABS_MT_TRACKING_ID: (0, 65535, 0),
                                  ABS_MT_POSITION_X: (0, w, 10), ABS_MT_POSITION_Y: (0, h, 10)},
                        props=(INPUT_PROP_DIRECT,), vendor=0x2D1F, product=0x0003)
        self.slots = {}  # contact id -> slot
        self.next_id = 1

    def update(self, contacts):
        l, t, r, b = self.box
        ev, keep = [], {}
        for cid, slot in self.slots.items():
            if cid not in contacts:
                ev += [(EV_ABS, ABS_MT_SLOT, slot), (EV_ABS, ABS_MT_TRACKING_ID, -1)]
        first = None
        for cid, (x, y) in list(contacts.items())[:self.MAX]:
            x, y = min(max(x - l, 0), r - l - 1), min(max(y - t, 0), b - t - 1)
            slot = self.slots.get(cid)
            ev.append((EV_ABS, ABS_MT_SLOT, slot if slot is not None else 0))
            if slot is None:
                slot = next(i for i in range(self.MAX) if i not in keep.values() and i not in self.slots.values())
                ev[-1] = (EV_ABS, ABS_MT_SLOT, slot)
                ev.append((EV_ABS, ABS_MT_TRACKING_ID, self.next_id))
                self.next_id = self.next_id % 65535 + 1
            ev += [(EV_ABS, ABS_MT_POSITION_X, x), (EV_ABS, ABS_MT_POSITION_Y, y)]
            keep[cid] = slot
            first = first or (x, y)
        down = bool(keep)
        if down != bool(self.slots):
            ev += [(EV_KEY, BTN_TOUCH, int(down)), (EV_KEY, BTN_TOOL_FINGER, int(down))]
        if first:
            ev += [(EV_ABS, ABS_X, first[0]), (EV_ABS, ABS_Y, first[1])]
        self.slots = keep
        if ev:
            self.dev.emit(ev)

    def lift_all(self):
        self.update({})

    def close(self):
        if self.dev:
            self.lift_all()
            self.dev.close()
            self.dev = None


class LinuxDevices:
    """A virtual keyboard and an absolute mouse, shared by keys.Keys and keys.Mouse."""

    def __init__(self, make=UInput):
        self.make = make
        self.kbd = self.mouse = None
        self.box = None

    def keyboard(self):
        if self.kbd is None:
            self.kbd = self.make("Paper Tablet Keys", keys=sorted(set(KEY.values())), vendor=0x2D1F, product=0x0002)
        return self.kbd

    def pointer(self):
        if self.mouse is None:
            self.box = layout_box()
            l, t, r, b = self.box
            self.mouse = self.make("Paper Tablet Mouse", keys=(BTN_LEFT, BTN_RIGHT, BTN_MIDDLE), rels=(REL_WHEEL,),
                                   abs_axes={ABS_X: (0, max(1, r - l - 1), 0), ABS_Y: (0, max(1, b - t - 1), 0)},
                                   vendor=0x2D1F, product=0x0003)
        return self.mouse

    def key(self, name, up):
        self.keyboard().emit([(EV_KEY, KEY[name], 0 if up else 1)])

    def move(self, x, y):
        dev = self.pointer()
        l, t, r, b = self.box
        dev.emit([(EV_ABS, ABS_X, min(max(int(x) - l, 0), r - l - 1)), (EV_ABS, ABS_Y, min(max(int(y) - t, 0), b - t - 1))])

    def button(self, down):
        self.pointer().emit([(EV_KEY, BTN_LEFT, int(down))])

    def close(self):
        for d in (self.kbd, self.mouse):
            if d:
                d.close()
        self.kbd = self.mouse = None


_devices = None


def devices():
    global _devices
    if _devices is None:
        _devices = LinuxDevices()
    return _devices


# --------------------------------------------------------------------------- compositor queries

def _run_json(cmd):
    if not shutil.which(cmd[0]):
        return None
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=2).stdout
        return json.loads(out) if out.strip() else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def hypr_monitors(data):
    """hyprctl monitors -j -> logical rectangles, focused monitor first."""
    out = []
    for m in data or []:
        if m.get("disabled"):
            continue
        w, h, s = m["width"], m["height"], m.get("scale") or 1
        if m.get("transform", 0) % 2:  # 90 / 270 degrees: the output is turned
            w, h = h, w
        lw, lh = round(w / s), round(h / s)
        out.append((not m.get("focused"), m["x"], (m["x"], m["y"], m["x"] + lw, m["y"] + lh)))
    return [r for _, _, r in sorted(out)]


def xrandr_monitors(text):
    """`xrandr --listmonitors` (X11 / XWayland): ' 0: +*DP-1 2560/597x1440/336+0+0  DP-1'
    -> rectangles, primary first."""
    import re
    out = []
    for line in text.splitlines()[1:]:
        m = re.search(r"(\S+)\s+(\d+)/\d+x(\d+)/\d+\+(-?\d+)\+(-?\d+)", line)
        if not m:
            continue
        name, w, h, x, y = m.group(1), *map(int, m.groups()[1:])
        out.append(("*" not in name, x, (x, y, x + w, y + h)))
    return [r for _, _, r in sorted(out)]


def monitors():
    mons = hypr_monitors(_run_json(["hyprctl", "monitors", "-j"]))
    if not mons and shutil.which("xrandr"):
        try:
            mons = xrandr_monitors(subprocess.run(["xrandr", "--listmonitors"], capture_output=True,
                                                  text=True, timeout=2).stdout)
        except (OSError, subprocess.SubprocessError):
            mons = []
    return mons or [(0, 0, 1920, 1080)]


def active_window():
    """(app name, (centre x, centre y)) of the focused window, or ("", None)."""
    w = _run_json(["hyprctl", "activewindow", "-j"])
    if not w:
        return "", None
    name = str(w.get("class") or w.get("initialClass") or "").lower()
    name = name.rsplit(".", 1)[-1]  # "org.kde.krita" -> "krita"
    try:
        (x, y), (ww, hh) = w["at"], w["size"]
        return name, (x + ww // 2, y + hh // 2)
    except (KeyError, ValueError, TypeError):
        return name, None


def cursor_pos():
    try:
        out = subprocess.run(["hyprctl", "cursorpos"], capture_output=True, text=True, timeout=2).stdout
        x, y = out.replace(",", " ").split()[:2]
        return int(float(x)), int(float(y))
    except (OSError, ValueError, subprocess.SubprocessError):
        l, t, r, b = layout_box()
        return (l + r) // 2, (t + b) // 2
