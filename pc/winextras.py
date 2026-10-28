"""Window extras for app.py, Windows only: the tray icon, edge resizing for the frameless window,
and a named event that lets a second launch bring this copy back from the tray.

The tray icon is a WinForms NotifyIcon: pywebview already runs WinForms (through pythonnet), so it
needs no extra package. WinForms objects live on the UI thread; everything here that touches them
is marshalled there with Invoke / BeginInvoke.
"""
import ctypes
import os
import threading
from ctypes import wintypes as wt

HERE = os.path.dirname(os.path.abspath(__file__))
ICON_ON = os.path.join(HERE, "icons", "paper-tablet.ico")
ICON_OFF = os.path.join(HERE, "icons", "paper-tablet-off.ico")
SHOW_EVENT = "Local\\PaperTablet.Show"

user32 = ctypes.windll.user32
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.CreateEventW.restype = wt.HANDLE
kernel32.OpenEventW.restype = wt.HANDLE
user32.GetDpiForWindow.restype = wt.UINT


# ------------------------------------------------------------------ bring back from the tray

def listen_for_show(callback):
    """Call callback() whenever another launch signals SHOW_EVENT (see signal_show)."""
    ev = kernel32.CreateEventW(None, False, False, SHOW_EVENT)
    if not ev:
        return

    def loop():
        while kernel32.WaitForSingleObject(ev, 0xFFFFFFFF) == 0:
            try:
                callback()
            except Exception as ex:
                print("show request failed:", repr(ex), flush=True)

    threading.Thread(target=loop, daemon=True, name="show-listener").start()


def signal_show():
    """From a second launch: ask the running copy to show its window. Returns False if none listens."""
    ev = kernel32.OpenEventW(0x0002, False, SHOW_EVENT)  # EVENT_MODIFY_STATE
    if not ev:
        return False
    user32.AllowSetForegroundWindow(wt.DWORD(-1).value)  # ASFW_ANY: let it come to the front
    kernel32.SetEvent(ev)
    kernel32.CloseHandle(ev)
    return True


# ------------------------------------------------------------------ edge resizing

class Resizer:
    """Resizes the frameless window from grips along its edges in the page. The page reports
    which edge was pressed; each move then reads the real cursor (physical pixels), so it's
    right at any display scale. min_w/min_h are in design pixels, scaled per display."""

    def __init__(self, min_w, min_h):
        self.hwnd = None
        self.min = (min_w, min_h)
        self.drag = None
        self.lock = threading.Lock()

    def start(self, edge):
        with self.lock:
            self.drag = None
            if not self.hwnd or user32.IsZoomed(self.hwnd):
                return
            r, p = wt.RECT(), wt.POINT()
            user32.GetWindowRect(self.hwnd, ctypes.byref(r))
            user32.GetCursorPos(ctypes.byref(p))
            scale = (user32.GetDpiForWindow(self.hwnd) or 96) / 96.0
            self.drag = (edge, r.left, r.top, r.right, r.bottom, p.x, p.y,
                         int(self.min[0] * scale), int(self.min[1] * scale))

    def move(self):
        with self.lock:
            if not self.drag:
                return
            edge, left, top, right, bottom, x0, y0, mw, mh = self.drag
            p = wt.POINT()
            user32.GetCursorPos(ctypes.byref(p))
            dx, dy = p.x - x0, p.y - y0
            if "w" in edge:
                left = min(left + dx, right - mw)
            if "e" in edge:
                right = max(right + dx, left + mw)
            if "n" in edge:
                top = min(top + dy, bottom - mh)
            if "s" in edge:
                bottom = max(bottom + dy, top + mh)
            user32.SetWindowPos(self.hwnd, None, left, top, right - left, bottom - top,
                                0x0004 | 0x0010)  # SWP_NOZORDER | SWP_NOACTIVATE

    def end(self):
        self.move()
        self.drag = None


# ------------------------------------------------------------------ tray icon

class Tray:
    """Tray icon with Open, Enable/Disable pen input and Exit. The icon shows whether the pen is on.
    on_open runs on the UI thread; on_toggle and on_exit run on their own threads."""

    def __init__(self, form, on_open, on_toggle, on_exit):
        from System import Action
        self.Action = Action
        self.form = form
        self.on_open, self.on_toggle, self.on_exit = on_open, on_toggle, on_exit
        self.state = None
        self.hwnd = None
        self.closed = False
        form.Invoke(Action(self._build))

    def _guard(self, fn):
        """fn wrapped for the UI thread: skipped once the tray is gone, and an error is logged rather
        than reaching WinForms, which would show its "Unhandled exception" dialog."""
        def run(*_):
            if self.closed:
                return
            try:
                fn()
            except Exception as ex:
                print("tray:", repr(ex), flush=True)
        return run

    def _later(self, fn):
        """Run fn on the UI thread without waiting (BeginInvoke)."""
        if not self.closed:
            try:
                self.form.BeginInvoke(self.Action(self._guard(fn)))
            except Exception:
                pass  # the window is already gone

    def _build(self):
        import System.Drawing as D
        import System.Windows.Forms as WF
        self.hwnd = self.form.Handle.ToInt64()
        small = WF.SystemInformation.SmallIconSize
        self.icons = {True: D.Icon(ICON_ON, small), False: D.Icon(ICON_OFF, small)}
        self.form.Icon = D.Icon(ICON_ON)  # the taskbar icon when run from source (pythonw's otherwise)

        def item(text, handler):
            it = WF.ToolStripMenuItem(text)
            it.Click += self._guard(handler)
            return it

        def later(fn):
            return lambda: threading.Thread(target=fn, daemon=True).start()

        self.open_item = item("Open Paper Tablet", self.on_open)
        self.open_item.Font = D.Font(self.open_item.Font, D.FontStyle.Bold)
        self.pen_item = item("Disable pen input", later(self.on_toggle))
        menu = WF.ContextMenuStrip()
        for it in (self.open_item, self.pen_item, WF.ToolStripSeparator(), item("Exit", later(self.on_exit))):
            menu.Items.Add(it)
        self.icon = WF.NotifyIcon()
        self.icon.ContextMenuStrip = menu
        self.icon.MouseClick += lambda s, e: self._guard(self.on_open)() if e.Button == WF.MouseButtons.Left else None
        self._apply((False, False))
        self.icon.Visible = True

    def _apply(self, state):
        connected, output = state
        self.state = state
        on = connected and output
        self.icon.Icon = self.icons[on]
        self.icon.Text = ("Paper Tablet: pen input on" if on else "Paper Tablet: pen input off" if connected
                          else "Paper Tablet: tablet not connected")
        self.pen_item.Text = "Disable pen input" if on else "Enable pen input"
        self.pen_item.Enabled = connected

    def update(self, connected, output):
        state = (bool(connected), bool(output))
        if state != self.state:
            self._later(lambda: self._apply(state))

    def balloon(self, title, text):
        import System.Windows.Forms as WF
        no_icon = getattr(WF.ToolTipIcon, "None")  # a Python keyword, so not WF.ToolTipIcon.None
        self._later(lambda: self.icon.ShowBalloonTip(4000, title, text, no_icon))

    def back_color(self, hex_color):
        """The window's own background, seen for a moment while it grows; follows the theme."""
        import System.Drawing as D
        self._later(lambda: setattr(self.form, "BackColor", D.ColorTranslator.FromHtml(hex_color)))

    def show_window(self):
        """Show, un-minimise and focus the window. UI thread only."""
        import System.Windows.Forms as WF
        self.form.Show()
        if self.form.WindowState == WF.FormWindowState.Minimized:
            self.form.WindowState = WF.FormWindowState.Normal
        self.form.Activate()
        user32.SetForegroundWindow(wt.HWND(self.hwnd))

    def call(self, fn):
        """Run fn on the UI thread and wait for it."""
        if not self.closed:
            self.form.Invoke(self.Action(self._guard(fn)))

    def dispose(self):
        """Remove the icon at once (otherwise it lingers until the mouse passes over it). UI thread.
        Anything still queued for the icon is skipped from here on."""
        if self.closed:
            return
        self.closed = True
        self.icon.Visible = False
        self.icon.Dispose()


def user_closing(form):
    """True if the window is closing because the user asked (close button, Alt+F4, taskbar), not
    because Windows is shutting down or signing out. WinForms keeps the reason in an internal
    property; if it can't be read, say False so the app closes normally."""
    try:
        import clr
        import System.Windows.Forms as WF
        from System.Reflection import BindingFlags
        prop = clr.GetClrType(WF.Form).GetProperty("CloseReason", BindingFlags.NonPublic | BindingFlags.Instance)
        return prop.GetValue(form) == WF.CloseReason.UserClosing
    except Exception as ex:
        print("close reason unknown:", repr(ex), flush=True)
        return False
