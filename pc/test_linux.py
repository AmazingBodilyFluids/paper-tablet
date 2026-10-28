"""Linux back end without a real /dev/uinput: device setup structs, pen event sequences,
compositor parsing."""
import struct
import unittest

import linuxinput as L
from rmtablet import Frame


class FakeDev:
    def __init__(self, name, **kw):
        self.name, self.kw, self.out = name, kw, []

    def emit(self, events):
        self.out.append(list(events))

    def close(self):
        pass


def ev(dev):
    return [e for batch in dev.out for e in batch]


class Structs(unittest.TestCase):
    def test_ioctl_sizes_match_the_kernel_headers(self):
        self.assertEqual(struct.calcsize("<HHHH80sI"), 92)   # struct uinput_setup
        self.assertEqual(struct.calcsize("<Hxxiiiiii"), 28)  # struct uinput_abs_setup
        self.assertEqual((L.UI_DEV_SETUP >> 16) & 0x3FFF, 92)
        self.assertEqual((L.UI_ABS_SETUP >> 16) & 0x3FFF, 28)

    def test_every_key_name_has_a_linux_code(self):
        import keys
        missing = [n for n in keys.VK if n not in L.KEY]
        self.assertEqual(missing, [])


class Pen(unittest.TestCase):
    def pen(self):
        return L.LinuxPen(box=(0, 0, 1920, 1080), make=FakeDev)

    def test_device_looks_like_a_tablet(self):
        p = self.pen()
        self.assertIn(L.BTN_TOOL_PEN, p.dev.kw["keys"])
        self.assertIn(L.BTN_TOOL_RUBBER, p.dev.kw["keys"])
        self.assertEqual(p.dev.kw["abs_axes"][L.ABS_X][:2], (0, 1919))
        self.assertEqual(p.dev.kw["abs_axes"][L.ABS_PRESSURE][:2], (0, 1024))

    def test_hover_down_drag_up_leave(self):
        p = self.pen()
        p.send(Frame(flags={"INRANGE", "UPDATE"}, x=100, y=50))
        p.send(Frame(flags={"INRANGE", "INCONTACT", "DOWN"}, x=101, y=51, pressure=300))
        p.send(Frame(flags={"INRANGE", "INCONTACT", "UPDATE"}, x=110, y=55, pressure=600, tilt_x=20, tilt_y=-10))
        p.send(Frame(flags={"INRANGE", "UP"}, x=110, y=55))
        p.send(Frame(flags={"UPDATE"}, x=110, y=55))
        b = p.dev.out
        self.assertIn((L.EV_KEY, L.BTN_TOOL_PEN, 1), b[0])
        self.assertIn((L.EV_ABS, L.ABS_PRESSURE, 0), b[0])
        self.assertIn((L.EV_KEY, L.BTN_TOUCH, 1), b[1])
        self.assertIn((L.EV_ABS, L.ABS_PRESSURE, 300), b[1])
        self.assertIn((L.EV_ABS, L.ABS_TILT_X, 20), b[2])
        self.assertNotIn((L.EV_KEY, L.BTN_TOUCH, 1), b[2])  # no repeat while touching
        self.assertIn((L.EV_KEY, L.BTN_TOUCH, 0), b[3])
        self.assertIn((L.EV_KEY, L.BTN_TOOL_PEN, 0), b[4])
        self.assertIsNone(p.tool)

    def test_eraser_swaps_tools(self):
        p = self.pen()
        p.send(Frame(flags={"INRANGE", "UPDATE"}, x=10, y=10))
        p.send(Frame(flags={"INRANGE", "UPDATE"}, x=10, y=10, eraser=True))
        e = ev(p.dev)
        self.assertLess(e.index((L.EV_KEY, L.BTN_TOOL_PEN, 0)), e.index((L.EV_KEY, L.BTN_TOOL_RUBBER, 1)))

    def test_positions_are_relative_to_the_layout_and_clamped(self):
        p = L.LinuxPen(box=(-1920, 0, 1920, 1080), make=FakeDev)
        p.send(Frame(flags={"INRANGE", "UPDATE"}, x=-1920, y=2000))
        self.assertIn((L.EV_ABS, L.ABS_X, 0), p.dev.out[0])
        self.assertIn((L.EV_ABS, L.ABS_Y, 1079), p.dev.out[0])


class Compositor(unittest.TestCase):
    def test_hyprland_monitors_are_logical_and_focused_first(self):
        mons = L.hypr_monitors([
            {"x": 0, "y": 0, "width": 2880, "height": 1800, "scale": 1.5, "transform": 0, "focused": False},
            {"x": 1920, "y": 0, "width": 1920, "height": 1080, "scale": 1, "transform": 1, "focused": True},
            {"x": 5000, "y": 0, "width": 800, "height": 600, "scale": 1, "disabled": True}])
        self.assertEqual(mons, [(1920, 0, 3000, 1920), (0, 0, 1920, 1200)])

    def test_xrandr_monitors(self):
        text = "Monitors: 2\n 0: +*DP-1 2560/597x1440/336+0+0  DP-1\n 1: +HDMI-1 1920/510x1080/290+2560+0  HDMI-1"
        self.assertEqual(L.xrandr_monitors(text), [(0, 0, 2560, 1440), (2560, 0, 4480, 1080)])


if __name__ == "__main__":
    unittest.main()
