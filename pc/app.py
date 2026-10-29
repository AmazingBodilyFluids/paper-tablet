#!/usr/bin/env python3
"""
Paper Tablet: the desktop app (pywebview + WebView2). Double-click "Paper Tablet.bat".

The window is an HTML interface in web/; everything it does goes through Api below to the
headless Engine in core.py. A small splash window shows while the engine starts.
Needs: Python 3.10+, `pip install -r requirements.txt` (paramiko, pywebview).
"""
import json
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import paths  # noqa: E402

if sys.stderr is None or sys.executable.lower().endswith("pythonw.exe"):
    # pythonw has no console: keep errors in a log (where: paths.log_dir)
    sys.stderr = sys.stdout = open(os.path.join(paths.log_dir(), "paper-tablet.log"), "a", buffering=1, encoding="utf-8")
    print(f"--- started {time.strftime('%Y-%m-%d %H:%M:%S')}")

import rmtablet as rt  # noqa: E402

# Per-monitor DPI awareness first (rt.monitors sets it): the Windows pen needs physical pixels,
# and it must be in place before any window exists.
MONITORS = rt.monitors()

import webview  # noqa: E402

import penmask  # noqa: E402
from core import Engine, use_profile_config  # noqa: E402
from version import __version__  # noqa: E402

CONFIG = use_profile_config()

if sys.platform == "win32":
    import winextras

BG = "#060e17"
# Window background per theme (web/themes.css), shown before the page has painted
THEME_BG = {"default": BG, "graphite": "#101215", "ledger": "#f4f1e8", "intaglio": "#0e1a15"}
SPLASH_MIN_S = 2.6
SPLASH_MAX_S = 7.0  # give up waiting for the splash animation after this
MAIN_TITLE = "Paper Tablet"
SPLASH_TITLE = "Paper Tablet starting"


def saved_theme():
    try:
        with open(CONFIG, encoding="utf-8") as f:
            theme = json.load(f).get("gui", {}).get("theme")
    except (OSError, ValueError):
        theme = None
    return theme if theme in THEME_BG else "intaglio"


def place_window(title, width, height):
    """Size and centre a window on the primary display's work area, in physical pixels.

    pywebview's own width/height/x/y are unreliable once the process is per-monitor DPI aware
    (needed for the pen): on a 1080p laptop the window came up too large and pushed
    off-screen. So the size is set natively: the design size times the display scale,
    clamped to 94% of the work area (screen minus taskbar)."""
    if sys.platform != "win32":
        return
    import ctypes
    from ctypes import wintypes as wt
    u = ctypes.windll.user32
    hwnd = u.FindWindowW(None, title)
    if not hwnd:
        return
    work = wt.RECT()
    u.SystemParametersInfoW(0x0030, 0, ctypes.byref(work), 0)  # SPI_GETWORKAREA
    try:
        scale = u.GetDpiForWindow(hwnd) / 96.0
    except Exception:
        scale = 1.0
    ww, wh = work.right - work.left, work.bottom - work.top
    w = min(int(width * scale), int(ww * 0.94))
    h = min(int(height * scale), int(wh * 0.94))
    x = work.left + (ww - w) // 2
    y = work.top + (wh - h) // 2
    u.SetWindowPos(hwnd, None, x, y, w, h, 0x0004 | 0x0010)  # SWP_NOZORDER | SWP_NOACTIVATE


class Api:
    """Everything the page may call (window.pywebview.api.*). Underscored members stay hidden."""

    def __init__(self):
        self._engine = None
        self._window = None
        self._tray = None
        self._resizer = winextras.Resizer(900, 600) if sys.platform == "win32" else None
        self._quitting = False
        self._closing = False
        self._told_tray = False
        self._ready = threading.Event()

    # ---- state
    def ready(self):
        return self._ready.is_set()

    def snapshot(self):
        return self._engine.snapshot() if self._engine else None

    def live(self):
        return self._engine.live() if self._engine else {"pen": None}

    def version(self):
        return __version__

    # ---- settings and actions
    def set(self, key, value):
        self._engine.set(key, value)
        if key == "theme" and self._tray:
            self._tray.back_color(THEME_BG.get(value, BG))
        return True

    def connect(self):
        self._engine.connect()

    def disconnect(self):
        self._engine.disconnect()

    def set_output(self, on):
        self._engine.set_output(on)

    def set_screen(self, on):
        self._engine.set_screen(on)

    def clear_ink(self):
        self._engine.companion.send("clear")

    def get_mask(self):
        """The pen mask as stored (before contrast): base64 of N x N bytes, and its version."""
        return {"b64": self._engine.pmask.b64(), "ver": self._engine.pmask.ver, "n": penmask.N}

    def set_mask(self, b64):
        return self._engine.set_mask(b64)

    def mask_op(self, name):
        self._engine.mask_op(name)

    def set_position(self, key):
        self._engine.set_position(key)

    def start_calibration(self, mode):
        self._engine.start_calibration(mode)

    def cancel_calibration(self):
        self._engine.cancel_calibration()

    def reset_peak(self):
        self._engine.reset_peak()

    def install_key(self, password):
        return self._engine.install_key(password)

    def finish_onboarding(self):
        self._engine.finish_onboarding()

    def restart_onboarding(self):
        self._engine.set("onboarded", False)

    def open_calibration_pdf(self):
        path = os.path.normpath(os.path.join(HERE, "..", "tablet", "calibration-landscape.pdf"))
        if sys.platform == "win32":
            os.startfile(os.path.dirname(path))  # noqa: S606 (opens the folder in Explorer)
        elif sys.platform.startswith("linux"):
            import subprocess
            subprocess.Popen(["xdg-open", os.path.dirname(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return path

    def pick_trace_image(self):
        """File dialog for the tracing image; the chosen path, or None."""
        kinds = ("Images (*.png;*.jpg;*.jpeg;*.bmp;*.gif;*.webp)", "All files (*.*)")
        got = self._window.create_file_dialog(webview.OPEN_DIALOG, file_types=kinds)
        if not got:
            return None
        path = got[0] if isinstance(got, (list, tuple)) else got
        self._engine.set("trace_file", path)
        return path

    def open_url(self, url):
        if url.startswith("https://"):
            import webbrowser
            webbrowser.open(url)

    # ---- window chrome (the window is frameless; the page draws its own title bar)
    def win_minimize(self):
        self._window.minimize()

    def win_toggle_max(self):
        w = self._window
        if getattr(self, "_maxed", False):
            w.restore()
            self._maxed = False
        else:
            w.maximize()
            self._maxed = True
        return self._maxed

    def win_close(self):
        self._window.destroy()  # on_closing decides between the tray and quitting

    # The window has no frame, so the page's edge grips resize it (winextras.Resizer)
    def resize_start(self, edge):
        if self._resizer:
            self._resizer.start(str(edge))

    def resize_move(self):
        if self._resizer:
            self._resizer.move()

    def resize_end(self):
        if self._resizer:
            self._resizer.end()

    # ---- tray
    def _toggle_pen(self):
        if self._engine:
            self._engine.set_output(not self._engine.output_on)

    def _exit(self):
        self._quitting = True
        self._window.destroy()

    def _to_tray(self):
        """Close button with "close to tray" on: hide instead of quitting. True if it did."""
        if (self._quitting or not self._tray or not self._engine or not self._engine.cfg.get("close_to_tray")
                or not winextras.user_closing(self._window.native)):
            return False
        self._window.hide()
        if not self._told_tray:
            self._told_tray = True
            self._tray.balloon("Paper Tablet is still running",
                               "It stays in the tray so the pen keeps working. Right-click the icon to exit.")
        return True


def already_running():
    """One Paper Tablet at a time: two would mean two synthetic pens fighting over the cursor.
    A named mutex says whether another copy owns it; if so, ask it to show its window (it may be
    hidden in the tray), or bring an older copy's window forward."""
    if sys.platform.startswith("linux"):
        import fcntl
        import paths
        main.lock = open(os.path.join(paths.data_dir(), "running.lock"), "w")  # held until exit
        try:
            fcntl.flock(main.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return False
        except OSError:
            return True
    if sys.platform != "win32":
        return False
    import ctypes
    k, u = ctypes.WinDLL("kernel32", use_last_error=True), ctypes.windll.user32
    k.CreateMutexW.restype = ctypes.c_void_p
    main.mutex = k.CreateMutexW(None, False, "Local\\PaperTablet.SingleInstance")  # held until exit
    if ctypes.get_last_error() != 183:  # ERROR_ALREADY_EXISTS
        return False
    if winextras.signal_show():
        return True
    hwnd = u.FindWindowW(None, MAIN_TITLE)
    if hwnd:
        u.ShowWindow(hwnd, 9)  # SW_RESTORE
        u.SetForegroundWindow(hwnd)
    return True


def main():
    if already_running():
        print("Paper Tablet is already running; brought it to the front.", flush=True)
        return
    api = Api()
    theme = saved_theme()
    # No splash on Linux: tiling compositors (Hyprland, Sway) would tile it like any window.
    use_splash = sys.platform == "win32"
    splash = None if not use_splash else webview.create_window(SPLASH_TITLE, os.path.join(HERE, "web", "splash.html"),
                                   width=560, height=340, frameless=True, easy_drag=False,
                                   on_top=True, background_color=THEME_BG[theme], resizable=False)
    window = webview.create_window(MAIN_TITLE, os.path.join(HERE, "web", "index.html"), js_api=api,
                                   width=1240, height=820, min_size=(900, 600),
                                   frameless=True, easy_drag=False, hidden=True, background_color=THEME_BG[theme])
    if splash:
        splash.events.shown += lambda: place_window(SPLASH_TITLE, 560, 340)
        splash.events.loaded += lambda: splash.evaluate_js(f"document.documentElement.dataset.theme = {json.dumps(theme)}")
    api._window = window
    t0 = time.time()

    def notify():
        if api._closing:
            return  # the page is going away; the engine's last "disconnected" has nowhere to go
        if api._tray and api._engine:
            api._tray.update(api._engine.connected, api._engine.output_on)
        try:
            window.evaluate_js("window.onEngineChange && window.onEngineChange()")
        except Exception:
            pass

    def start_tray():
        try:
            api._tray = winextras.Tray(window.native, on_open=lambda: api._tray.show_window(),
                                       on_toggle=api._toggle_pen, on_exit=api._exit)
            api._resizer.hwnd = api._tray.hwnd
            winextras.listen_for_show(lambda: api._tray.call(api._tray.show_window))
        except Exception as ex:  # the app still works without a tray icon; it just quits on close
            print("tray icon failed:", repr(ex), flush=True)
            import ctypes
            api._resizer.hwnd = ctypes.windll.user32.FindWindowW(None, MAIN_TITLE)

    def boot():
        try:
            api._engine = Engine(on_change=notify)
        except Exception as ex:  # show the problem instead of a silent exit
            print("engine failed to start:", repr(ex), flush=True)
            raise
        api._ready.set()
        # Let the splash animation play to its end (the page reports it), then fade it out
        # before the main window appears, instead of cutting it off at a fixed time.
        time.sleep(max(0.0, SPLASH_MIN_S - (time.time() - t0)) if splash else 0)
        while splash and time.time() - t0 < SPLASH_MAX_S:
            try:
                if splash.evaluate_js("window.splashDone === true"):
                    break
            except Exception:
                break
            time.sleep(0.05)
        if splash:
            time.sleep(0.2)  # a beat on the finished logo
            try:
                splash.evaluate_js("fadeOut()")
                time.sleep(0.4)
            except Exception:
                pass
        place_window(MAIN_TITLE, 1240, 820)  # while still hidden, so it never shows at the wrong size
        window.show()
        place_window(MAIN_TITLE, 1240, 820)
        if sys.platform == "win32":
            start_tray()
        notify()
        try:
            if splash:
                splash.destroy()
        except Exception:
            pass

    def on_closing():
        if api._to_tray():
            return False  # cancels the close
        api._closing = True
        tray, api._tray = api._tray, None
        if api._engine:
            api._engine.shutdown()
        if tray:
            tray.dispose()

    window.events.closing += on_closing
    webview.start(boot, debug="--debug" in sys.argv)


if __name__ == "__main__":
    main()
