#!/usr/bin/env python3
"""
rmtablet GUI: connect to the reMarkable Paper Pro, calibrate, tune and run the Windows pen.

    python rmtablet_gui.py          (or double-click gui.bat)

Uses only the standard library (tkinter) plus paramiko, like rmtablet.py.
Settings are saved next to this file in rmtablet.json. The password is never saved.
"""
import json
import queue
import socket
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, ttk

import rmtablet as rt

# calibration modes: (label, crosshair numbers on the sheet or None for screen corners, inset)
CAL_MODES = {
    "sheet9": ("9 crosshairs on the calibration sheet (most precise)", [1, 2, 3, 4, 5, 6, 7, 8, 9], 0.1),
    "sheet4": ("4 corner crosshairs on the sheet (1, 3, 9, 7)", [1, 3, 9, 7], 0.1),
    "corners": ("4 screen corners (no sheet needed)", None, 0.0),
}
POSITIONS = ["top-left", "top-middle", "top-right", "middle-left", "centre", "middle-right",
             "bottom-left", "bottom-middle", "bottom-right"]
CORNER_NAMES = ["TOP-LEFT", "TOP-RIGHT", "BOTTOM-RIGHT", "BOTTOM-LEFT"]
DEFAULTS = {
    "host": "10.11.99.1", "user": "root", "key": "", "monitor": 0, "fit": "crop",
    "orientation": "as-calibrated", "area": [0.0, 0.0, 1.0, 1.0], "gamma": 1.0, "threshold": 0.0,
    "tilt": True, "grab": False, "feedback": "indirect", "autoconnect": False,
}


if sys.stderr is None or sys.executable.lower().endswith("pythonw.exe"):
    # pythonw has no console: keep injection errors and tracebacks in a log next to the script
    sys.stderr = sys.stdout = open(rt.os.path.join(rt.HERE, "rmtablet-gui.log"), "a", buffering=1)
    print(f"--- started {time.strftime('%Y-%m-%d %H:%M:%S')}", file=sys.stderr)


class NullPen:
    def send(self, f):
        pass

    def close(self):
        pass


class Companion:
    """Optional tablet-side screen (tablet/companion). While it runs, xochitl is stopped and the
    tablet shows targets, status and an unsaved ink preview; closing it gives the screen back."""

    FILES = ("rmpp-companion", "run.sh")
    REMOTE = "/tmp/rmpp-companion"

    def __init__(self, app):
        self.app = app
        self.chan = None
        self.lines = queue.Queue()

    @property
    def running(self):
        return self.chan is not None and not self.chan.exit_status_ready()

    def start(self, tab):
        import os
        src = os.path.join(rt.HERE, "..", "tablet", "companion")
        tab.run(f"mkdir -p {self.REMOTE}")
        for name in self.FILES:
            with open(os.path.join(src, name), "rb") as f:
                tab.run(f"cat > {self.REMOTE}/{name} && chmod 755 {self.REMOTE}/{name}", f.read())
        self.chan = tab.client.get_transport().open_session()
        self.chan.exec_command(f"sh {self.REMOTE}/run.sh 2>&1")
        threading.Thread(target=self._read, args=(self.chan,), daemon=True).start()
        threading.Thread(target=self._keepalive, args=(self.chan,), daemon=True).start()

    def _keepalive(self, chan):
        # The app gives the screen back to xochitl if these stop (cable pulled, PC asleep).
        while not chan.closed and not chan.exit_status_ready():
            try:
                chan.sendall(b"ping\n")
            except Exception:
                return
            time.sleep(1)

    def _read(self, chan):
        buf = b""
        for chunk in iter(lambda: chan.recv(4096), b""):
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                self.lines.put(line.decode(errors="replace"))
        self.lines.put("companion: closed")

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


class Worker(threading.Thread):
    """Owns the SSH stream and the Windows pen. The GUI only reads `latest` and sets flags."""

    def __init__(self, app):
        super().__init__(daemon=True)
        self.app = app
        self.events = queue.Queue()  # (kind, payload) for the GUI
        self.latest = None
        self.stop_flag = threading.Event()
        self.output_on = False
        self.cal_taps = None  # list while calibrating: raw (x, y) per tap
        self.translator = None
        self.chan = None
        self.tab = None

    def log(self, kind, payload=None):
        self.events.put((kind, payload))

    def run(self):
        delay = 1
        while not self.stop_flag.is_set():
            pen_out = None
            try:
                self.log("status", f"connecting to {self.app.cfg['host']}...")
                self.tab, info = self.app.open_tablet()
                pen = rt.pick_pen(info, None)
                self.log("connected", (info, pen))
                self.translator = rt.PenTranslator(self.app.build_mapper(pen), self.app.cfg["tilt"] and "TILT_X" in pen["abs"],
                                                   has_touch_key="TOUCH" in pen["keys"])
                parser = rt.EvdevParser(rt.event_format(info))
                delay = 1
                was_down = False
                grabbed = None
                closed = False
                while not self.stop_flag.is_set() and not closed:
                    want = self.want_grab()
                    if want != grabbed:  # (re)open the stream with or without the exclusive grab
                        if self.chan:
                            self.chan.close()
                        self.chan = self.tab.stream(pen["path"], grab=want)
                        self.chan.settimeout(0.2)
                        parser = rt.EvdevParser(rt.event_format(info))
                        grabbed = want
                    try:
                        chunk = self.chan.recv(65536)
                    except socket.timeout:
                        chunk = None
                    if chunk == b"":
                        closed = True
                        break
                    if self.output_on and pen_out is None:
                        pen_out = self.make_pen()
                    if not self.output_on and pen_out is not None:
                        self.release(pen_out)
                        pen_out = None
                    if not chunk:
                        continue
                    for s in parser.feed(chunk):
                        self.latest = s
                        down = s.in_range and (s.touch or s.pressure > 0)
                        if self.cal_taps is not None:
                            if down and not was_down:
                                self.cal_taps.append((s.x, s.y))
                                self.log("tap", (s.x, s.y))
                        elif pen_out is not None:
                            for frame in self.translator.step(s):
                                pen_out.send(frame)
                        was_down = down
                if not self.stop_flag.is_set():
                    self.log("status", "tablet closed the stream (asleep or unplugged?)")
            except Exception as e:  # auth, network, helper problems
                self.log("error", str(e))
            if pen_out is not None:
                self.release(pen_out)
            self.close_tablet()
            self.log("disconnected")
            if self.stop_flag.wait(delay):
                break
            delay = min(delay * 2, 30)

    def want_grab(self):
        """Grab the pen away from xochitl when asked to, and always while calibrating (so taps
        don't open its menus), but never while the companion owns the screen: xochitl is
        stopped then, and a grab would starve the companion's own pen reader."""
        if self.app.companion.running:
            return False
        return bool(self.app.cfg["grab"]) or self.cal_taps is not None

    def make_pen(self):
        if sys.platform != "win32":
            self.log("status", "not on Windows: pen output simulated")
            return NullPen()
        try:
            return rt.WindowsPen(feedback=self.app.cfg["feedback"])
        except Exception as e:
            self.log("error", f"could not create Windows pen: {e}")
            self.output_on = False
            self.log("output", False)
            return None

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


class App:
    def __init__(self):
        self.monitors = rt.monitors()  # sets DPI awareness on Windows before Tk starts
        cfg = rt.load_config()
        self.cfg = dict(DEFAULTS, **cfg.get("gui", {}))
        self.calibration = cfg.get("calibration")
        if (self.calibration or {}).get("homography") and self.cfg["orientation"] != "as-calibrated":
            # point calibrations already encode how the tablet sits; an extra rotation on top
            # turns the cursor 90 degrees (seen with a leftover "landscape-cw" from older builds)
            self.cfg["orientation"] = "as-calibrated"
        self.info = self.pen = None
        self.worker = None
        self.companion = Companion(self)

        self.root = tk.Tk()
        self.root.title("reMarkable Paper Pro tablet")
        self.root.minsize(900, 600)
        self.root.protocol("WM_DELETE_WINDOW", self.quit)
        style = ttk.Style()
        if "vista" in style.theme_names():
            style.theme_use("vista")
        self.v = {}
        self.build()
        self.refresh_mapper()
        self.root.after(30, self.poll)
        if self.cfg["autoconnect"]:
            self.root.after(300, self.connect)

    # ------------------------------------------------------------------ layout

    def var(self, key, kind=tk.StringVar):
        v = kind(value=self.cfg[key])
        self.v[key] = v
        v.trace_add("write", lambda *_: self.on_setting(key))
        return v

    def build(self):
        main = ttk.Frame(self.root, padding=8)
        main.pack(fill="both", expand=True)
        main.columnconfigure(1, weight=1)
        main.rowconfigure(0, weight=1)

        nb = ttk.Notebook(main, width=360)
        nb.grid(row=0, column=0, sticky="nsw", padx=(0, 8))
        nb.add(self.build_connection(nb), text="Connection")
        nb.add(self.build_calibration(nb), text="Calibration")
        nb.add(self.build_mapping(nb), text="Mapping")
        nb.add(self.build_pressure(nb), text="Pressure")
        nb.add(self.build_companion(nb), text="Tablet screen")

        right = ttk.Frame(main)
        right.grid(row=0, column=1, sticky="nsew")
        right.rowconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)
        self.preview = tk.Canvas(right, background="#f4f4f2", highlightthickness=0)
        self.preview.grid(row=0, column=0, sticky="nsew")
        self.preview.bind("<Configure>", lambda e: self.draw_preview())
        self.preview.bind("<ButtonPress-1>", self.area_start)
        self.preview.bind("<B1-Motion>", self.area_drag)
        self.preview.bind("<ButtonRelease-1>", self.area_end)
        self.readout = ttk.Label(right, font=("Consolas", 10), text="pen: -")
        self.readout.grid(row=1, column=0, sticky="w", pady=(6, 0))
        bar = ttk.Frame(right)
        bar.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        self.out_btn = ttk.Button(bar, text="Start pen output", command=self.toggle_output, state="disabled")
        self.out_btn.pack(side="left")
        ttk.Label(bar, text="  Drag on the preview to set the active area.").pack(side="left")
        self.status = ttk.Label(main, text="not connected", anchor="w", relief="sunken", padding=(6, 2))
        self.status.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))

    def row(self, parent, r, label, widget):
        ttk.Label(parent, text=label).grid(row=r, column=0, sticky="w", pady=3)
        widget.grid(row=r, column=1, sticky="ew", pady=3)

    def build_connection(self, nb):
        f = ttk.Frame(nb, padding=10)
        f.columnconfigure(1, weight=1)
        self.row(f, 0, "Tablet address", ttk.Entry(f, textvariable=self.var("host")))
        self.row(f, 1, "User", ttk.Entry(f, textvariable=self.var("user")))
        kf = ttk.Frame(f)
        ttk.Entry(kf, textvariable=self.var("key")).pack(side="left", fill="x", expand=True)
        ttk.Button(kf, text="...", width=3, command=self.browse_key).pack(side="left")
        self.row(f, 2, "SSH key (optional)", kf)
        self.password = tk.StringVar()
        self.row(f, 3, "Password (optional)", ttk.Entry(f, textvariable=self.password, show="*"))
        ttk.Label(f, foreground="#666", wraplength=330,
                  text="Leave both empty to use ~/.ssh/id_ed25519. The password is never saved.").grid(
            row=4, column=0, columnspan=2, sticky="w")
        ttk.Checkbutton(f, text="Connect when the app starts", variable=self.var("autoconnect", tk.BooleanVar)).grid(
            row=5, column=0, columnspan=2, sticky="w", pady=(8, 0))
        b = ttk.Frame(f)
        b.grid(row=6, column=0, columnspan=2, sticky="w", pady=10)
        self.conn_btn = ttk.Button(b, text="Connect", command=self.connect)
        self.conn_btn.pack(side="left")
        ttk.Button(b, text="Disconnect", command=self.disconnect).pack(side="left", padx=6)
        self.dev_text = tk.Text(f, height=12, width=42, font=("Consolas", 9), relief="flat", background="#f7f7f7")
        self.dev_text.grid(row=7, column=0, columnspan=2, sticky="nsew")
        self.dev_text.insert("1.0", "No tablet connected.")
        self.dev_text.configure(state="disabled")
        return f

    def build_calibration(self, nb):
        f = ttk.Frame(nb, padding=10)
        ttk.Label(f, wraplength=330, justify="left", text=(
            "Put the tablet exactly where and how you draw on it (for example landscape, left side up). "
            "Then tap each corner of the tablet's screen, as you see it from your seat. Four taps correct "
            "rotation, mirroring and any offset at the edges.")).pack(anchor="w")
        self.cal_mode = tk.StringVar(value="sheet9")
        ttk.Label(f, wraplength=330, foreground="#444", text=(
            "Sheet: open tablet/calibration-landscape.pdf (or -portrait) full screen on the tablet.")).pack(
            anchor="w", pady=(8, 0))
        for key, (label, _, _) in CAL_MODES.items():
            ttk.Radiobutton(f, variable=self.cal_mode, value=key, text=label).pack(anchor="w")
        self.cal_btn = ttk.Button(f, text="Start calibration", command=self.start_calibration, state="disabled")
        self.cal_btn.pack(anchor="w", pady=10)
        self.cal_step = ttk.Label(f, font=("Segoe UI", 12, "bold"), text="")
        self.cal_step.pack(anchor="w")
        self.cal_info = ttk.Label(f, wraplength=330, justify="left", foreground="#444")
        self.cal_info.pack(anchor="w", pady=(10, 0))
        ttk.Button(f, text="Cancel calibration", command=self.cancel_calibration).pack(anchor="w", pady=(10, 0))
        ttk.Label(f, wraplength=330, foreground="#666", text=(
            "Check: hover over each tablet corner; the dot in the preview should reach the matching corner. "
            "Ink may appear on the tablet if its notebook is open; that's harmless.")).pack(anchor="w", pady=(14, 0))
        self.show_calibration()
        return f

    def build_mapping(self, nb):
        f = ttk.Frame(nb, padding=10)
        f.columnconfigure(1, weight=1)
        names = [f"{i}: {r - l} x {b - t} at ({l}, {t})" + (" primary" if i == 0 else "")
                 for i, (l, t, r, b) in enumerate(self.monitors)]
        self.mon_var = tk.StringVar(value=names[min(self.cfg["monitor"], len(names) - 1)])
        cb = ttk.Combobox(f, textvariable=self.mon_var, values=names, state="readonly")
        cb.bind("<<ComboboxSelected>>", lambda e: self.set_cfg("monitor", names.index(self.mon_var.get())))
        self.row(f, 0, "Monitor", cb)
        fit = ttk.Frame(f)
        for val, txt in (("crop", "Keep shape, fill monitor"), ("letterbox", "Keep shape, use whole tablet"),
                         ("stretch", "Stretch")):
            ttk.Radiobutton(fit, text=txt, value=val, variable=self.var("fit")).pack(anchor="w")
        self.row(f, 1, "Fit", fit)
        self.row(f, 2, "Extra rotation (normally as-calibrated)", ttk.Combobox(f, textvariable=self.var("orientation"),
                                                       values=rt.ORIENTATIONS, state="readonly"))
        ttk.Label(f, text="Active area (% of tablet)").grid(row=3, column=0, columnspan=2, sticky="w", pady=(10, 0))
        af = ttk.Frame(f)
        af.grid(row=4, column=0, columnspan=2, sticky="w")
        self.area_vars = []
        for i, lab in enumerate(("left", "top", "right", "bottom")):
            v = tk.DoubleVar(value=round(self.cfg["area"][i] * 100, 1))
            ttk.Label(af, text=lab).grid(row=0, column=i, padx=2)
            sb = ttk.Spinbox(af, from_=0, to=100, increment=1, width=6, textvariable=v, command=self.area_from_spin)
            sb.bind("<Return>", lambda e: self.area_from_spin())
            sb.bind("<FocusOut>", lambda e: self.area_from_spin())
            sb.grid(row=1, column=i, padx=2)
            self.area_vars.append(v)
        ttk.Button(f, text="Use whole tablet", command=lambda: self.set_area((0, 0, 1, 1))).grid(
            row=5, column=0, sticky="w", pady=6)
        ttk.Button(f, text="Crosshair box (10-90%)", command=lambda: self.set_area((0.1, 0.1, 0.9, 0.9))).grid(
            row=5, column=1, sticky="w", pady=6)
        ttk.Separator(f).grid(row=6, column=0, columnspan=2, sticky="ew", pady=8)
        ttk.Checkbutton(f, text="Send tilt", variable=self.var("tilt", tk.BooleanVar)).grid(row=7, column=0, sticky="w")
        ttk.Checkbutton(f, text="Block the tablet's own UI (no ink on e-ink)",
                        variable=self.var("grab", tk.BooleanVar)).grid(row=8, column=0, columnspan=2, sticky="w")
        self.row(f, 9, "Windows pen feedback", ttk.Combobox(f, textvariable=self.var("feedback"),
                                                             values=("indirect", "default", "none"), state="readonly"))
        ttk.Label(f, foreground="#666", wraplength=330,
                  text="Tilt, UI blocking and feedback apply on the next connect / output start.").grid(
            row=10, column=0, columnspan=2, sticky="w", pady=(6, 0))
        return f

    def build_pressure(self, nb):
        f = ttk.Frame(nb, padding=10)
        f.columnconfigure(1, weight=1)
        self.row(f, 0, "Curve (soft ↔ firm)", ttk.Scale(f, from_=0.3, to=3.0, variable=self.var("gamma", tk.DoubleVar),
                                                        command=lambda e: self.draw_curve()))
        self.row(f, 1, "Ignore light touch", ttk.Scale(f, from_=0.0, to=0.2, variable=self.var("threshold", tk.DoubleVar),
                                                       command=lambda e: self.draw_curve()))
        self.curve_label = ttk.Label(f, foreground="#444")
        self.curve_label.grid(row=2, column=0, columnspan=2, sticky="w")
        self.curve = tk.Canvas(f, width=320, height=220, background="white", highlightthickness=1,
                               highlightbackground="#ccc")
        self.curve.grid(row=3, column=0, columnspan=2, pady=8)
        ttk.Button(f, text="Reset", command=lambda: (self.v["gamma"].set(1.0), self.v["threshold"].set(0.0))).grid(
            row=4, column=0, sticky="w")
        self.peak = 0
        self.peak_label = ttk.Label(f, foreground="#444", text="Press hard once to check the full range is reachable.")
        self.peak_label.grid(row=5, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Button(f, text="Reset peak", command=self.reset_peak).grid(row=6, column=0, sticky="w")
        return f

    def build_companion(self, nb):
        f = ttk.Frame(nb, padding=10)
        ttk.Label(f, wraplength=330, justify="left", text=(
            "Optional. Shows calibration crosshairs, a status line, the active area and an ink preview "
            "on the tablet itself. Ink here is never saved.\n\n"
            "While it runs the notebook app is paused. It comes back when you press Stop, close this "
            "app, unplug the cable, or press the tablet's power button. Nothing is installed: the "
            "files go to /tmp and vanish on reboot.")).pack(anchor="w")
        self.rot_names = {"Match calibration": None, "Portrait": 0, "Landscape, left side up": 3,
                          "Landscape, right side up": 1, "Portrait, upside down": 2}
        self.cfg.setdefault("companion_rot", "Match calibration")
        ttk.Label(f, text="How the tablet sits").pack(anchor="w", pady=(10, 0))
        cb = ttk.Combobox(f, textvariable=self.var("companion_rot"), values=list(self.rot_names), state="readonly")
        cb.pack(anchor="w", fill="x")
        b = ttk.Frame(f)
        b.pack(anchor="w", pady=10)
        self.comp_btn = ttk.Button(b, text="Start tablet screen", command=self.toggle_companion, state="disabled")
        self.comp_btn.pack(side="left")
        ttk.Button(b, text="Clear ink", command=lambda: self.companion.send("clear")).pack(side="left", padx=6)
        self.comp_log = tk.Text(f, height=10, width=42, font=("Consolas", 8), relief="flat", background="#f7f7f7")
        self.comp_log.pack(anchor="w", fill="both", expand=True)
        return f

    def toggle_companion(self):
        # Each start or stop hands the screen between xochitl and the companion, which takes a
        # few seconds; clicks queued up meanwhile used to start overlapping sessions. So the
        # button stays disabled until the hand-over has settled.
        if str(self.comp_btn["state"]) == "disabled":
            return
        self.comp_btn.configure(state="disabled")
        self.root.after(4000, lambda: self.comp_btn.configure(state="normal") if self.worker and self.worker.tab else None)
        if self.companion.running:
            self.companion.stop()
            self.comp_btn.configure(text="Start tablet screen")
            return
        if not (self.worker and self.worker.tab):
            return
        try:
            self.companion.start(self.worker.tab)
        except Exception as e:
            self.set_status(f"tablet screen failed to start: {e}")
            return
        self.comp_btn.configure(text="Stop tablet screen")
        self.root.after(2500, self.sync_companion)

    def sync_companion(self):
        c = self.companion
        if not c.running:
            return
        c.send(f"rot {self.companion_rotation()}")
        c.send("area " + " ".join(f"{a:.4f}" for a in self.cfg["area"]))
        out = self.worker.output_on if self.worker else False
        c.send("status Connected to PC  -  pen output " + ("ON" if out else "off"))
        if self.worker and self.worker.cal_taps is not None:
            c.send("mode targets")
            c.send(f"next {self.cal_number(len(self.worker.cal_taps))}")
        else:
            c.send("mode ink")

    def companion_rotation(self):
        """Quarter turns for the tablet screen. 'Match calibration' reads it from how the
        calibrated frame sits on the digitizer (digitizer axes run with the panel's pixels)."""
        rot = self.rot_names.get(self.cfg.get("companion_rot"))
        if rot is not None:
            return rot
        c = self.calibration or {}
        key = (bool(c.get("swap")), bool(c.get("flip_u")), bool(c.get("flip_v")))
        return {(False, False, False): 0, (True, True, False): 3, (True, False, True): 1,
                (False, True, True): 2}.get(key, 0)

    def cal_number(self, i):
        _, numbers, _ = CAL_MODES[self.cal_mode.get()]
        if numbers and i < len(numbers):
            return numbers[i]
        return 0

    # ------------------------------------------------------------------ settings

    def on_setting(self, key):
        try:
            val = self.v[key].get()
        except tk.TclError:
            return  # half-typed number
        self.set_cfg(key, val)

    def set_cfg(self, key, val):
        self.cfg[key] = val
        self.refresh_mapper()
        self.save()
        if key in ("area", "companion_rot"):
            self.sync_companion()

    def save(self):
        cfg = rt.load_config()
        cfg["gui"] = {k: self.cfg[k] for k in list(DEFAULTS) + ["companion_rot"] if k in self.cfg}
        if self.calibration is not None:
            cfg["calibration"] = self.calibration
        rt.save_config(cfg)

    def browse_key(self):
        path = filedialog.askopenfilename(title="SSH private key")
        if path:
            self.v["key"].set(path)

    def build_mapper(self, pen):
        cal = self.calibration or {}
        mon = self.monitors[min(self.cfg["monitor"], len(self.monitors) - 1)]
        return rt.Mapper(absinfo=pen["abs"], monitor=mon, orientation=self.cfg["orientation"], fit=self.cfg["fit"],
                         swap=cal.get("swap", False), flip_u=cal.get("flip_u", False), flip_v=cal.get("flip_v", False),
                         homography=cal.get("homography"), aspect=cal.get("aspect"),
                         pressure_gamma=float(self.cfg["gamma"]), pressure_threshold=float(self.cfg["threshold"]),
                         area=tuple(self.cfg["area"]))

    def refresh_mapper(self):
        pen = self.pen or {"abs": {"X": {"min": 0, "max": 11180}, "Y": {"min": 0, "max": 15340},
                                   "PRESSURE": {"min": 0, "max": 4096}}}
        self.mapper = self.build_mapper(pen)
        if self.worker and self.worker.translator:
            self.worker.translator.m = self.mapper  # takes effect on the next pen report
        self.draw_curve()
        self.draw_preview()

    # ------------------------------------------------------------------ connection

    def open_tablet(self):
        import paramiko
        key = self.cfg["key"] or None
        password = self.password.get() or None
        try:
            tab = rt.Tablet(self.cfg["host"], self.cfg["user"], password, keyfile=key)
        except paramiko.AuthenticationException:
            raise RuntimeError("login refused: set up the SSH key (see README) or enter the password")
        return tab, tab.install_helper()

    def connect(self):
        if self.worker and self.worker.is_alive():
            return
        self.worker = Worker(self)
        self.worker.start()
        self.conn_btn.configure(state="disabled")

    def disconnect(self):
        self.companion.stop()
        if self.worker:
            self.worker.shutdown()
            self.worker = None
        self.conn_btn.configure(state="normal")
        self.out_btn.configure(state="disabled", text="Start pen output")
        self.cal_btn.configure(state="disabled")
        self.set_status("disconnected")

    def toggle_output(self):
        if not self.worker:
            return
        self.worker.output_on = not self.worker.output_on
        self.out_btn.configure(text="Stop pen output" if self.worker.output_on else "Start pen output")
        self.set_status("pen output ON: the tablet now drives the Windows pen" if self.worker.output_on
                        else "pen output off (preview only)")
        self.sync_companion()

    def set_status(self, text):
        self.status.configure(text=text)

    # ------------------------------------------------------------------ calibration

    def start_calibration(self):
        if not self.worker:
            return
        self.worker.cal_taps = []
        self.cal_step.configure(text=self.cal_prompt(0))
        self.sync_companion()

    def cal_prompt(self, i):
        _, numbers, _ = CAL_MODES[self.cal_mode.get()]
        if numbers:
            return f"Tap crosshair {numbers[i]} ({POSITIONS[numbers[i] - 1]})   [{i + 1}/{len(numbers)}]"
        return f"Tap the {CORNER_NAMES[i]} corner   [{i + 1}/4]"

    def cal_targets(self):
        _, numbers, inset = CAL_MODES[self.cal_mode.get()]
        if numbers is None:
            return rt.grid_targets(4, 0.0)
        grid = rt.grid_targets(9, inset)
        return [grid[n - 1] for n in numbers]

    def cancel_calibration(self):
        if self.worker:
            self.worker.cal_taps = None
        self.cal_step.configure(text="")
        self.sync_companion()

    def on_tap(self):
        taps = self.worker.cal_taps
        if taps is None:
            return
        targets = self.cal_targets()
        if len(taps) < len(targets):
            self.cal_step.configure(text=self.cal_prompt(len(taps)))
            self.companion.send(f"next {self.cal_number(len(taps))}")
            return
        self.worker.cal_taps = None
        self.sync_companion()
        try:
            cal = rt.grid_calibration(self.pen["abs"], taps[:len(targets)], targets)
        except ValueError as e:
            self.cal_step.configure(text=f"Failed: {e}. Try again.")
            return
        cal["device"] = self.pen["name"]
        cal["date"] = time.strftime("%Y-%m-%d %H:%M")
        self.calibration = cal
        self.v["orientation"].set("as-calibrated")  # the taps already capture how the tablet sits
        self.cal_step.configure(text="Calibrated ✓")
        self.save()
        self.show_calibration()
        self.refresh_mapper()
        self.sync_companion()

    def show_calibration(self):
        c = self.calibration
        if not c:
            self.cal_info.configure(text="No calibration saved yet: the cursor assumes the tablet is upright.")
            return
        if not c.get("homography"):
            kind = "2-corner (command line)"
        else:
            kind = f"{len(c.get('targets', [0] * 4))}-point" + (" sheet" if c.get("inset") else " corners")
        size = f", measured {c['size_mm'][0]} x {c['size_mm'][1]} mm" if c.get("size_mm") else ""
        text = f"Saved: {kind}{size}, {'landscape' if c.get('swap') else 'portrait'} frame. {c.get('date', '')}"
        res = c.get("residual_mm")
        if res and len(res) > 4:
            worst = max(range(len(res)), key=lambda i: res[i])
            text += ("\n\nHow far each tap sat from a perfect fit (mm), now corrected by the mesh:\n"
                     + "\n".join("  " + "  ".join(f"{r:5.2f}" for r in res[k:k + 3]) for k in range(0, 9, 3)))
            if res[worst] > 2.0:
                text += f"\n\nPoint {worst + 1} is {res[worst]:.1f} mm off: probably a mis-tap. Consider redoing it."
        self.cal_info.configure(text=text)

    # ------------------------------------------------------------------ active area

    def area_from_spin(self):
        try:
            vals = [min(100.0, max(0.0, float(v.get()))) / 100 for v in self.area_vars]
        except (tk.TclError, ValueError):
            return
        if vals[2] - vals[0] >= 0.05 and vals[3] - vals[1] >= 0.05:
            self.set_area(vals, from_spin=True)

    def set_area(self, area, from_spin=False):
        area = [round(a, 3) for a in area]
        if not from_spin:
            for v, a in zip(self.area_vars, area):
                v.set(round(a * 100, 1))
        self.set_cfg("area", area)

    def frame_rect(self):
        """Canvas rectangle of the tablet frame, drawn with its real proportions."""
        w, h = self.preview.winfo_width(), self.preview.winfo_height()
        aspect = self.mapper.oriented_aspect()
        pad = 24
        fw, fh = w - 2 * pad, h - 2 * pad
        if fw / max(fh, 1) > aspect:
            fw = fh * aspect
        else:
            fh = fw / aspect
        x0, y0 = (w - fw) / 2, (h - fh) / 2
        return x0, y0, x0 + fw, y0 + fh

    def to_frac(self, e):
        x0, y0, x1, y1 = self.frame_rect()
        return min(1, max(0, (e.x - x0) / (x1 - x0))), min(1, max(0, (e.y - y0) / (y1 - y0)))

    def area_start(self, e):
        self.drag0 = self.to_frac(e)

    def area_drag(self, e):
        p = self.to_frac(e)
        a = (min(self.drag0[0], p[0]), min(self.drag0[1], p[1]), max(self.drag0[0], p[0]), max(self.drag0[1], p[1]))
        self.preview.delete("drag")
        x0, y0, x1, y1 = self.frame_rect()
        self.preview.create_rectangle(x0 + a[0] * (x1 - x0), y0 + a[1] * (y1 - y0), x0 + a[2] * (x1 - x0),
                                      y0 + a[3] * (y1 - y0), outline="#d0532a", dash=(4, 2), width=2, tags="drag")

    def area_end(self, e):
        p = self.to_frac(e)
        a = (min(self.drag0[0], p[0]), min(self.drag0[1], p[1]), max(self.drag0[0], p[0]), max(self.drag0[1], p[1]))
        self.preview.delete("drag")
        if a[2] - a[0] >= 0.05 and a[3] - a[1] >= 0.05:
            self.set_area(a)

    # ------------------------------------------------------------------ drawing

    def draw_preview(self):
        c = self.preview
        c.delete("static")
        if c.winfo_width() < 50:
            return
        x0, y0, x1, y1 = self.frame_rect()
        fx = lambda u: x0 + u * (x1 - x0)
        fy = lambda v: y0 + v * (y1 - y0)
        c.create_rectangle(x0 - 10, y0 - 10, x1 + 10, y1 + 10, fill="#9a9a96", outline="", tags="static")
        c.create_rectangle(x0, y0, x1, y1, fill="#e9e8e2", outline="#555", tags="static")
        u0, v0, u1, v1 = self.mapper.used_region()
        c.create_rectangle(fx(max(0, u0)), fy(max(0, v0)), fx(min(1, u1)), fy(min(1, v1)), fill="#cfe3f5",
                           outline="#2a6fb0", width=2, tags="static")
        a = self.mapper.area
        c.create_rectangle(fx(a[0]), fy(a[1]), fx(a[2]), fy(a[3]), outline="#d0532a", width=2, dash=(6, 3),
                           tags="static")
        c.create_text(fx(0) + 6, fy(0) + 6, anchor="nw", fill="#555", tags="static",
                      text="tablet as it sits (after calibration)")
        c.create_text(fx(u0) + 6, fy(v1) - 6, anchor="sw", fill="#2a6fb0", tags="static", text="maps to monitor")
        c.tag_raise("pen")

    def draw_pen(self, s):
        c = self.preview
        c.delete("pen")
        if s is None or not s.in_range or self.pen is None:
            return
        x0, y0, x1, y1 = self.frame_rect()
        p, q = self.mapper.oriented(*self.mapper.uv(s.x, s.y))
        px, py = x0 + p * (x1 - x0), y0 + q * (y1 - y0)
        touching = s.touch or s.pressure > 0
        out = self.mapper.pressure(s.pressure) if touching else 0
        r = 4 + 10 * out / 1024
        color = "#e08a00" if s.rubber else ("#c62828" if touching else "#1e6fd9")
        if self.worker and self.worker.translator and self.worker.translator.has_tilt:
            tx, ty = self.mapper.tilt_xy(s.tilt_x, s.tilt_y)
            # Windows tilt points where the top of the pen leans (+Y = toward you), so the
            # line is drawn along the barrel: lean the pen toward the screen and it points up.
            c.create_line(px, py, px + tx * 0.8, py + ty * 0.8, fill="#777", width=2, tags="pen")
        c.create_oval(px - r, py - r, px + r, py + r, fill=color if touching else "", outline=color, width=2, tags="pen")
        sx, sy = self.mapper.to_screen(s.x, s.y)
        tilt = self.mapper.tilt_xy(s.tilt_x, s.tilt_y)
        self.readout.configure(text=(
            f"{'eraser' if s.rubber else 'pen'} {'DOWN' if touching else 'hover'}   raw {s.x:>5},{s.y:<5}  "
            f"screen {sx:>5},{sy:<5}  pressure {s.pressure:>4} -> {out:<4}  tilt {tilt[0]:>3},{tilt[1]:<3}"))
        if s.pressure > self.peak:
            self.peak = s.pressure
            pmax = self.pen["abs"]["PRESSURE"]["max"]
            self.peak_label.configure(text=f"Peak pressure: {self.peak} of {pmax} ({100 * self.peak / pmax:.0f}%)")
        if touching:
            self.draw_curve(marker=s.pressure)

    def reset_peak(self):
        self.peak = 0
        self.peak_label.configure(text="Press hard once to check the full range is reachable.")

    def draw_curve(self, marker=None):
        c = self.curve
        if not hasattr(self, "curve"):
            return
        c.delete("all")
        w, h, pad = 320, 220, 20
        c.create_line(pad, h - pad, w - pad, h - pad, fill="#999")
        c.create_line(pad, pad, pad, h - pad, fill="#999")
        c.create_line(pad, h - pad, w - pad, pad, fill="#ddd", dash=(3, 3))
        pmax = self.mapper.absinfo["PRESSURE"]["max"]
        pts = []
        for i in range(101):
            raw = pmax * i / 100
            pts += [pad + (w - 2 * pad) * i / 100, h - pad - (h - 2 * pad) * self.mapper.pressure(raw) / 1024]
        c.create_line(*pts, fill="#c62828", width=2, smooth=True)
        c.create_text(w / 2, h - 6, text="tablet pressure", fill="#666")
        c.create_text(8, h / 2, text="out", fill="#666", angle=90)
        if marker is not None:
            x = pad + (w - 2 * pad) * marker / pmax
            y = h - pad - (h - 2 * pad) * self.mapper.pressure(marker) / 1024
            c.create_oval(x - 4, y - 4, x + 4, y + 4, fill="#1e6fd9", outline="")
        self.curve_label.configure(text=f"curve {float(self.cfg['gamma']):.2f}, "
                                        f"ignore below {100 * float(self.cfg['threshold']):.0f}%")

    # ------------------------------------------------------------------ event loop

    def poll(self):
        w = self.worker
        if w:
            while True:
                try:
                    kind, payload = w.events.get_nowait()
                except queue.Empty:
                    break
                if kind == "status":
                    self.set_status(payload)
                elif kind == "error":
                    self.set_status(f"error: {payload}  (retrying)")
                elif kind == "connected":
                    self.info, self.pen = payload
                    self.on_connected()
                elif kind == "disconnected":
                    self.out_btn.configure(state="disabled")
                    self.cal_btn.configure(state="disabled")
                    self.comp_btn.configure(state="disabled", text="Start tablet screen")
                elif kind == "tap":
                    self.on_tap()
                elif kind == "output":
                    self.out_btn.configure(text="Start pen output")
            self.draw_pen(w.latest)
        while True:
            try:
                line = self.companion.lines.get_nowait()
            except queue.Empty:
                break
            print("tablet screen:", line, flush=True)
            self.comp_log.insert("end", line + "\n")
            self.comp_log.see("end")
            if line.startswith("companion: closed"):
                self.comp_btn.configure(text="Start tablet screen")
        self.root.after(16, self.poll)

    def on_connected(self):
        pen, info = self.pen, self.info
        lines = [f"{pen['name']}  ({pen['path']})", f"kernel {info['release']} {info['machine']}", ""]
        for axis in ("X", "Y", "PRESSURE", "DISTANCE", "TILT_X", "TILT_Y"):
            a = pen["abs"].get(axis)
            lines.append(f"{axis:<9} {a['min']} .. {a['max']}" if a else f"{axis:<9} not reported")
        lines += ["", "tools: " + ", ".join(pen["keys"])]
        self.dev_text.configure(state="normal")
        self.dev_text.delete("1.0", "end")
        self.dev_text.insert("1.0", "\n".join(lines))
        self.dev_text.configure(state="disabled")
        self.out_btn.configure(state="normal",
                               text="Stop pen output" if self.worker.output_on else "Start pen output")
        self.cal_btn.configure(state="normal")
        self.comp_btn.configure(state="normal")
        self.refresh_mapper()
        self.set_status(f"connected to {self.cfg['host']}: preview live"
                        + (", pen output ON" if self.worker.output_on else ". Press 'Start pen output' to draw in apps."))

    def quit(self):
        self.companion.stop()
        if self.worker:
            self.worker.shutdown()
            self.worker.join(timeout=2)
        self.root.destroy()

    def mainloop(self):
        self.root.mainloop()


if __name__ == "__main__":
    App().mainloop()
