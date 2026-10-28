"""Keyboard shortcuts and the pan drag sent to the foreground app for the tablet-screen buttons
and finger gestures.

Keys go through keybd_event and the drag through mouse_event (both the SendInput path), so they
reach whatever window has focus, just like a keyboard and mouse would. Held keys are tracked so
they can always be released when the tablet screen closes or the cable is pulled; a stuck
modifier would make the whole PC act strangely.

Key names are lower case and layout-independent, as the desktop app records them from
KeyboardEvent.code: "a".."z", "0".."9", "f1".."f24", "space", "enter", "left", "[", "=", ...
A combination is names joined with "+", e.g. "ctrl+shift+z".
"""
import string
import sys
import threading

VK = {"ctrl": 0x11, "shift": 0x10, "alt": 0x12, "win": 0x5B,
      "space": 0x20, "tab": 0x09, "enter": 0x0D, "esc": 0x1B, "backspace": 0x08,
      "delete": 0x2E, "insert": 0x2D, "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
      "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
      ";": 0xBA, "=": 0xBB, ",": 0xBC, "-": 0xBD, ".": 0xBE, "/": 0xBF, "`": 0xC0,
      "[": 0xDB, "\\": 0xDC, "]": 0xDD, "'": 0xDE}
VK.update({c: ord(c.upper()) for c in string.ascii_lowercase})
VK.update({str(d): 0x30 + d for d in range(10)})
VK.update({f"f{n}": 0x6F + n for n in range(1, 25)})
# Keys that need KEYEVENTF_EXTENDEDKEY, or Windows reads them as the numeric keypad.
EXTENDED = {"delete", "insert", "home", "end", "pageup", "pagedown", "left", "up", "right", "down"}
MODIFIERS = ("ctrl", "shift", "alt", "win")
KEYUP, EXTKEY = 0x2, 0x1
LABEL = {"ctrl": "Ctrl", "shift": "Shift", "alt": "Alt", "win": "Win", "space": "Space", "tab": "Tab",
         "enter": "Enter", "esc": "Esc", "backspace": "Bksp", "delete": "Del", "insert": "Ins",
         "home": "Home", "end": "End", "pageup": "PgUp", "pagedown": "PgDn", "left": "Left",
         "up": "Up", "right": "Right", "down": "Down"}

# Gesture and built-in actions. Photoshop / Krita / Clip Studio defaults; redo is Ctrl+Shift+Z
# there (Clip Studio also accepts Ctrl+Y).
SHORTCUTS = {
    "undo": "ctrl+z", "redo": "ctrl+shift+z",
    "smaller": "[", "bigger": "]",
    "brush": "b", "eraser": "e",
    "zoomin": "ctrl+=", "zoomout": "ctrl+-", "fit": "ctrl+0",
}


def parse(spec):
    """Key names of a combination, or ValueError naming the unknown key."""
    names = [n.strip().lower() for n in str(spec).split("+") if n.strip()]
    if not names:
        raise ValueError("no key")
    for n in names:
        if n not in VK:
            raise ValueError(f"unknown key {n!r}")
    return names


def label(spec):
    """Short text for a button's second line: "ctrl+shift+z" -> "Ctrl Shift Z"."""
    try:
        return " ".join(LABEL.get(n, n.upper()) for n in parse(spec))
    except ValueError:
        return ""


class Keys:
    def __init__(self, send=None):
        self.held = set()
        self.lock = threading.Lock()
        self._send = send or self._keybd
        self._linux = send is None and sys.platform.startswith("linux")

    @staticmethod
    def _keybd(vk, up, ext=False):
        if sys.platform != "win32":
            return
        import ctypes
        ctypes.windll.user32.keybd_event(vk, 0, (KEYUP if up else 0) | (EXTKEY if ext else 0), 0)

    def _key(self, name, up):
        if self._linux:
            import linuxinput
            linuxinput.devices().key(name, up)
        elif name in EXTENDED:
            self._send(VK[name], up, True)
        else:
            self._send(VK[name], up)

    def combo(self, spec):
        """Tap a combination like "ctrl+shift+z" (keys already held stay held)."""
        names = parse(spec)
        with self.lock:
            pressed = []
            for n in names:
                if n not in self.held:
                    self._key(n, False)
                    pressed.append(n)
            for n in reversed(pressed):
                self._key(n, True)

    def down(self, spec):
        """Hold every key of a combination down until up()."""
        with self.lock:
            for n in parse(spec):
                if n not in self.held:
                    self._key(n, False)
                    self.held.add(n)

    def up(self, spec):
        with self.lock:
            for n in reversed(parse(spec)):
                if n in self.held:
                    if n in ("alt", "win"):
                        # Alt (or Win) pressed and released on its own opens the menu bar (Start
                        # menu); a Ctrl tap in between cancels that (the usual "mask key" trick).
                        self._key("ctrl", False)
                        self._key("ctrl", True)
                    self._key(n, True)
                    self.held.discard(n)

    def release_all(self):
        for n in list(self.held):
            self.up(n)


class Mouse:
    """Left-button drag in screen pixels, for the two-finger pan (Space + drag)."""

    MOVE, LDOWN, LUP, ABSOLUTE, VIRTUALDESK = 0x1, 0x2, 0x4, 0x8000, 0x4000

    def __init__(self, send=None, cursor=None, desk=None):
        self._send = send or self._mouse_event
        self._cursor = cursor or self._get_cursor
        self._desk = desk or self._virtual_desk
        self.start = None

    @staticmethod
    def _mouse_event(flags, x=0, y=0):
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.user32.mouse_event(flags, x, y, 0, 0)

    @staticmethod
    def _get_cursor():
        if sys.platform != "win32":
            return 0, 0
        import ctypes
        from ctypes import wintypes as wt
        p = wt.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(p))
        return p.x, p.y

    @staticmethod
    def _virtual_desk():
        if sys.platform != "win32":
            return 0, 0, 1920, 1080
        import ctypes
        g = ctypes.windll.user32.GetSystemMetrics
        return g(76), g(77), g(78), g(79)  # SM_X/YVIRTUALSCREEN, SM_CX/CYVIRTUALSCREEN

    def _abs(self, x, y):
        # absolute moves (0..65535 across the virtual desktop) ignore pointer acceleration
        l, t, w, h = self._desk()
        return (int((x - l) * 65535 / max(1, w - 1)), int((y - t) * 65535 / max(1, h - 1)))

    def begin(self):
        self.start = self._cursor()
        self._send(self.LDOWN)

    def goto(self, x, y):
        """Put the cursor at screen pixel (x, y) without pressing anything."""
        ax, ay = self._abs(x, y)
        self._send(self.MOVE | self.ABSOLUTE | self.VIRTUALDESK, ax, ay)

    def begin_at(self, x, y):
        """Press the left button at (x, y); move() is then relative to that point."""
        self.goto(x, y)
        self.start = (x, y)
        self._send(self.LDOWN)

    def move(self, dx, dy):
        if self.start is None:
            return
        x, y = self._abs(self.start[0] + dx, self.start[1] + dy)
        self._send(self.MOVE | self.ABSOLUTE | self.VIRTUALDESK, x, y)

    def end(self):
        if self.start is not None:
            self._send(self.LUP)
            self.start = None


class LinuxMouse(Mouse):
    """The same drags through the virtual absolute mouse (linuxinput)."""

    def __init__(self):
        import linuxinput
        self.dev = linuxinput.devices()
        super().__init__(send=lambda *a: None, cursor=linuxinput.cursor_pos)

    def begin(self):
        self.start = self._cursor()
        self.dev.move(*self.start)
        self.dev.button(True)

    def goto(self, x, y):
        self.dev.move(x, y)

    def begin_at(self, x, y):
        self.dev.move(x, y)
        self.start = (x, y)
        self.dev.button(True)

    def move(self, dx, dy):
        if self.start is not None:
            self.dev.move(self.start[0] + dx, self.start[1] + dy)

    def end(self):
        if self.start is not None:
            self.dev.button(False)
            self.start = None


def make_mouse():
    return LinuxMouse() if sys.platform.startswith("linux") else Mouse()


def foreground_center():
    """Centre of the focused window in screen pixels: the pivot for the rotate drag (drawing
    apps rotate the view around the middle of their window)."""
    if sys.platform.startswith("linux"):
        import linuxinput
        c = linuxinput.active_window()[1]
        if c:
            return c
        l, t, r, b = linuxinput.layout_box()
        return (l + r) // 2, (t + b) // 2
    if sys.platform != "win32":
        return 960, 540
    import ctypes
    from ctypes import wintypes as wt
    u = ctypes.windll.user32
    r = wt.RECT()
    if not u.GetWindowRect(u.GetForegroundWindow(), ctypes.byref(r)):
        return 960, 540
    return (r.left + r.right) // 2, (r.top + r.bottom) // 2
