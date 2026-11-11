"""Checks the Linux virtual devices without a tablet: a synthetic Paper Pro pen stream goes
through the real parser and mapper into the uinput pen, then touch, keys and mouse, and libinput
(the input library of Hyprland, GNOME, KDE, Sway) reports what it sees.
Run:  python3 linux/check_devices.py   (needs /dev/uinput access and `libinput` from libinput-tools;
the pen hovers over your screen for a moment, keep hands off). Add --full to also draw (tip down
= a click), use the eraser, two-finger touch and a mouse drag: only over an empty desktop.
debug-events may need sudo or the input group; without it you get only the device list."""
import os, struct, subprocess, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "pc"))
import rmtablet as rt, linuxinput as li, keys
FULL = "--full" in sys.argv

EV = struct.Struct("<qqHHi")
EV_SYN, EV_KEY, EV_ABS = 0, 1, 3
def ev(t, c, v): return EV.pack(0, 0, t, c, v)
def syn(): return ev(EV_SYN, 0, 0)
ABS = {"X": {"min": 0, "max": 11180, "res": 2832}, "Y": {"min": 0, "max": 15340, "res": 2064},
       "PRESSURE": {"min": 0, "max": 4096}, "DISTANCE": {"min": 0, "max": 65535},
       "TILT_X": {"min": -9000, "max": 9000}, "TILT_Y": {"min": -9000, "max": 9000}}

pen = li.LinuxPen()
touch = li.LinuxTouch()
kb = keys.Keys()                      # Linux path -> virtual keyboard
mouse = keys.make_mouse()             # LinuxMouse -> virtual absolute mouse
li.devices().keyboard(); li.devices().pointer()
time.sleep(1.0)
subprocess.run(["udevadm", "settle"])
print("=== libinput list-devices (Paper Tablet only) ===", flush=True)
out = subprocess.run(["libinput", "list-devices"], capture_output=True, text=True).stdout
for block in out.split("\n\n"):
    if "Paper Tablet" in block:
        print("\n".join(l for l in block.splitlines() if l.split(":")[0].strip() in
              ("Device", "Kernel", "Capabilities", "Size", "Tap-to-click", "Calibration")), "\n", flush=True)

log = open("/tmp/li-events.txt", "w")
mon = subprocess.Popen(["libinput", "debug-events", "--show-keycodes"], stdout=log, stderr=subprocess.STDOUT)
time.sleep(2.0)

m = rt.Mapper(absinfo=ABS, monitor=(0, 0, 1920, 1080), orientation="landscape-ccw", fit="crop")
tr = rt.PenTranslator(m, has_tilt=True)
parser = rt.EvdevParser("<qqHHi")
def tablet(*events):
    data = b"".join(ev(*e) for e in events) + syn()
    for s in parser.feed(data):
        for f in tr.step(s):
            pen.send(f)
    time.sleep(0.01)

# hover in, at the panel centre, tilted
tablet((EV_KEY, 0x140, 1), (EV_ABS, 0, 5590), (EV_ABS, 1, 7670), (EV_ABS, 0x19, 400), (EV_ABS, 0x1a, 3000), (EV_ABS, 0x1b, -2000))
for i in range(5): tablet((EV_ABS, 0, 5590 + i * 50))
# tip down with rising pressure, a stroke, lift
if not FULL:
    tablet((EV_KEY, 0x140, 0))
    time.sleep(1.0)
    mon.terminate(); mon.wait(); log.close()
    print("=== libinput debug-events (hover only; --full for the rest) ===")
    print(open("/tmp/li-events.txt").read()); pen.close(); touch.close(); li.devices().close(); sys.exit()
tablet((EV_KEY, 0x14a, 1), (EV_ABS, 0x18, 600))
for i in range(20): tablet((EV_ABS, 0, 5840 + i * 100), (EV_ABS, 0x18, 600 + i * 170))
tablet((EV_KEY, 0x14a, 0), (EV_ABS, 0x18, 0))
for i in range(3): tablet((EV_ABS, 1, 7670 + i * 50))
tablet((EV_KEY, 0x140, 0))            # out of range
time.sleep(0.2)
# eraser end
tablet((EV_KEY, 0x141, 1), (EV_ABS, 0, 3000), (EV_ABS, 1, 3000))
tablet((EV_KEY, 0x14a, 1), (EV_ABS, 0x18, 2000))
for i in range(5): tablet((EV_ABS, 0, 3000 + i * 100))
tablet((EV_KEY, 0x14a, 0), (EV_ABS, 0x18, 0))
tablet((EV_KEY, 0x141, 0))
time.sleep(0.2)
# shortcut through the virtual keyboard
kb.combo("f24")  # a key no app uses
time.sleep(0.2)
# two-finger touch (Windows-touch / Linux-touch profile)
for i in range(6):
    touch.update({1: (800 + i * 10, 500), 2: (1100 - i * 10, 500)}); time.sleep(0.02)
touch.update({1: (860, 500)}); time.sleep(0.02)
touch.update({}); time.sleep(0.2)
# mouse drag (pan / rotate gestures use this)
mouse.begin_at(500, 500)
for i in range(5): mouse.goto(500 + i * 20, 500); time.sleep(0.02)
mouse.end()
time.sleep(1.0)
mon.terminate(); mon.wait(); log.close()
print("=== libinput debug-events ===")
print(open("/tmp/li-events.txt").read())
for d in (pen, touch): d.close()
li.devices().close()
