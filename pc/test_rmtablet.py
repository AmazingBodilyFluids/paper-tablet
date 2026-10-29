"""Offline tests: synthetic evdev streams through parser, translator and mapper (no tablet, no Windows)."""
import math
import struct
import unittest

import rmtablet as rt

ABS = {"X": {"min": 0, "max": 11180, "res": 0}, "Y": {"min": 0, "max": 15340, "res": 0},
       "PRESSURE": {"min": 0, "max": 4095, "res": 0},
       "TILT_X": {"min": -9000, "max": 9000, "res": 0}, "TILT_Y": {"min": -9000, "max": 9000, "res": 0}}


def ev(t, c, v, fmt="<qqHHi"):
    return struct.pack(fmt, 0, 0, t, c, v)


def syn(fmt="<qqHHi"):
    return ev(rt.EV_SYN, rt.SYN_REPORT, 0, fmt)


def mapper(**kw):
    kw.setdefault("monitor", (0, 0, 1920, 1080))
    kw.setdefault("orientation", "portrait")
    kw.setdefault("fit", "stretch")
    return rt.Mapper(absinfo=ABS, **kw)


class Parser(unittest.TestCase):
    def test_split_records_and_both_layouts(self):
        for fmt in ("<qqHHi", "<llHHi"):
            p = rt.EvdevParser(fmt)
            data = ev(rt.EV_KEY, rt.BTN_TOOL_PEN, 1, fmt) + ev(rt.EV_ABS, rt.ABS_X, 100, fmt) + syn(fmt)
            out = list(p.feed(data[:7])) + list(p.feed(data[7:]))  # split mid-record
            self.assertEqual(len(out), 1)
            self.assertTrue(out[0].pen)
            self.assertEqual(out[0].x, 100)

    def test_syn_dropped_discards_partial_report(self):
        p = rt.EvdevParser("<qqHHi")
        data = ev(rt.EV_ABS, rt.ABS_X, 5) + ev(rt.EV_SYN, rt.SYN_DROPPED, 0) + syn() + ev(rt.EV_ABS, rt.ABS_X, 6) + syn()
        self.assertEqual([s.x for s in p.feed(data)], [6])


class Translator(unittest.TestCase):
    def run_states(self, states, m=None):
        tr = rt.PenTranslator(m or mapper(), has_tilt=True)
        frames = []
        for s in states:
            frames += tr.step(s)
        return frames, tr

    def test_hover_down_drag_up_leave(self):
        S = rt.PenState
        frames, tr = self.run_states([
            S(x=0, y=0, pen=True),
            S(x=5590, y=7670, pen=True, touch=True, pressure=4095),
            S(x=11180, y=15340, pen=True, touch=True, pressure=1024),
            S(x=11180, y=15340, pen=True),
            S(),
        ])
        self.assertEqual([sorted(f.flags) for f in frames], [
            ["INRANGE", "UPDATE"], ["DOWN", "INCONTACT", "INRANGE"], ["INCONTACT", "INRANGE", "UPDATE"],
            ["INRANGE", "UP"], ["UPDATE"]])
        self.assertEqual(frames[1].pressure, 1024)
        self.assertEqual((frames[0].x, frames[0].y), (0, 0))
        self.assertEqual((frames[2].x, frames[2].y), (1919, 1079))
        self.assertEqual(frames[1].button_change, "down")
        self.assertEqual(frames[3].button_change, "up")

    def test_leaving_range_while_touching_sends_up_first(self):
        S = rt.PenState
        frames, _ = self.run_states([S(pen=True, touch=True, pressure=2000), S()])
        self.assertEqual([sorted(f.flags) for f in frames][1:], [["INRANGE", "UP"], ["UPDATE"]])

    def test_eraser_switch_leaves_range(self):
        S = rt.PenState
        frames, _ = self.run_states([S(pen=True), S(rubber=True)])
        self.assertEqual([sorted(f.flags) for f in frames], [["INRANGE", "UPDATE"], ["UPDATE"], ["INRANGE", "UPDATE"]])
        self.assertTrue(frames[-1].eraser)

    def test_pressure_threshold_is_hover(self):
        S = rt.PenState
        frames, _ = self.run_states([S(pen=True, touch=True, pressure=10)], mapper(pressure_threshold=0.05))
        self.assertEqual(sorted(frames[0].flags), ["INRANGE", "UPDATE"])

    def test_no_touch_key_uses_pressure(self):
        S = rt.PenState
        tr = rt.PenTranslator(mapper(), has_tilt=False, has_touch_key=False)
        f = tr.step(S(pen=True, pressure=0)) + tr.step(S(pen=True, pressure=900)) + tr.step(S(pen=True, pressure=0))
        self.assertEqual([sorted(x.flags) for x in f], [["INRANGE", "UPDATE"], ["DOWN", "INCONTACT", "INRANGE"], ["INRANGE", "UP"]])

    def test_disconnect_cleanup(self):
        S = rt.PenState
        _, tr = self.run_states([S(pen=True, touch=True, pressure=2000)])
        self.assertEqual([sorted(f.flags) for f in tr.leave()], [["INRANGE", "UP"], ["UPDATE"]])
        self.assertEqual(tr.leave(), [])


class Mapping(unittest.TestCase):
    def test_orientations_move_top_left(self):
        corners = {"portrait": (0, 0), "landscape-cw": (1919, 0), "landscape-ccw": (0, 1079), "portrait-flipped": (1919, 1079)}
        for o, want in corners.items():
            self.assertEqual(mapper(orientation=o).to_screen(0, 0), want, o)

    def test_calibration_swap_flip(self):
        m = mapper(swap=True, flip_u=True)
        # raw Y is the horizontal axis, reversed: raw (0, max Y) is upright top-left
        self.assertEqual(m.to_screen(0, 15340), (0, 0))

    def test_landscape_left_side_up_via_calibration(self):
        # A real desk setup: device turned so its native left edge is on top. Calibration taps:
        # desk top-left = native bottom-left (0, 15340), desk top-right = native top-left (0, 0)
        cfg = {}
        tl, tr = (0, 15340), (0, 0)
        nx = lambda v: v / 11180
        ny = lambda v: v / 15340
        dx, dy = nx(tr[0]) - nx(tl[0]), ny(tr[1]) - ny(tl[1])
        swap = abs(dy) > abs(dx)
        flip_u, flip_v = (dy < 0, nx(tl[0]) > 0.5) if swap else (dx < 0, ny(tl[1]) > 0.5)
        m = mapper(orientation="as-calibrated", fit="stretch", swap=swap, flip_u=flip_u, flip_v=flip_v)
        self.assertEqual(m.to_screen(*tl), (0, 0))
        self.assertEqual(m.to_screen(*tr), (1919, 0))
        self.assertEqual(m.to_screen(11180, 0), (1919, 1079))  # native top-right = desk bottom-right
        self.assertAlmostEqual(m.tablet_aspect(), 4 / 3)  # landscape 4:3
        m.fit = "crop"
        self.assertEqual(m.to_screen(5590, 7670), (960, 540))

    def test_corner_calibration_landscape_with_offsets(self):
        # left-side-up landscape, taps a little inside the true corners
        tl, tr, br, bl = (300, 15000), (250, 400), (10900, 350), (10950, 15050)
        cal = rt.corner_calibration(ABS, tl, tr, br, bl)
        self.assertTrue(cal["swap"])
        m = mapper(orientation="as-calibrated", fit="stretch", homography=cal["homography"], aspect=cal["aspect"],
                   swap=cal["swap"], flip_u=cal["flip_u"], flip_v=cal["flip_v"])
        for raw, want in ((tl, (0, 0)), (tr, (1919, 0)), (br, (1919, 1079)), (bl, (0, 1079))):
            self.assertEqual(m.to_screen(*raw), want)
        self.assertAlmostEqual(cal["aspect"], 4 / 3, delta=0.05)
        # Observed on a Paper Pro, 2026-10-27: top of the pen leaned right must give +tiltX, toward the
        # laptop -tiltY. Raw TILT_Y is inverted against raw Y; TILT_X follows raw X.
        self.assertGreater(m.tilt_xy(0, 3000)[0], 0)
        self.assertLess(m.tilt_xy(-3000, 0)[1], 0)

    def test_sheet_calibration_inset(self):
        # crosshairs at 10% insets, tablet upright, perfect taps
        P = lambda fx, fy: (fx * 11180, fy * 15340)
        cal = rt.corner_calibration(ABS, P(.1, .1), P(.9, .1), P(.9, .9), P(.1, .9), inset=0.1)
        m = mapper(fit="stretch", homography=cal["homography"], aspect=cal["aspect"])
        self.assertEqual(m.to_screen(0, 0), (0, 0))
        self.assertEqual(m.to_screen(11180, 15340), (1919, 1079))
        self.assertAlmostEqual(cal["aspect"], 0.75, delta=0.02)

    def test_nine_point_grid_exact_with_nonlinear_digitizer(self):
        # landscape, left side up, plus a bulge in the middle the homography can't model
        def raw(u, v):
            bulge = 0.01 * math.sin(math.pi * u) * math.sin(math.pi * v)
            return ((v + bulge) * 11180, (1 - u) * 15340)
        targets = rt.grid_targets(9, 0.1)
        taps = [raw(u, v) for u, v in targets]
        cal = rt.grid_calibration(ABS, taps, targets)
        self.assertTrue(cal["swap"])
        self.assertEqual(len(cal["residual_mm"]), 9)
        self.assertGreater(max(cal["residual_mm"]), 0.3)  # the bulge shows up as residual
        m = mapper(fit="stretch", homography=cal["homography"], correction=cal["correction"], aspect=cal["aspect"])
        for (u, v), tap in zip(targets, taps):
            pu, pv = m.uv(*tap)
            self.assertAlmostEqual(pu, u, places=6)
            self.assertAlmostEqual(pv, v, places=6)
        self.assertAlmostEqual(cal["aspect"], 4 / 3, delta=0.05)

    def test_degenerate_taps_rejected(self):
        with self.assertRaises(ValueError):
            rt.corner_calibration(ABS, (0, 0), (0, 0), (0, 0), (0, 0))

    def test_used_region(self):
        m = mapper(fit="crop", swap=True)  # 4:3 landscape on 16:9 -> horizontal band
        u0, v0, u1, v1 = m.used_region()
        self.assertEqual((u0, u1), (0.0, 1.0))
        self.assertAlmostEqual(v1 - v0, 0.75, places=3)
        m.fit = "letterbox"
        self.assertEqual(m.used_region(), (0.0, 0.0, 1.0, 1.0))

    def test_crop_reaches_whole_monitor_and_keeps_aspect(self):
        m = mapper(orientation="landscape-cw", fit="crop")
        # 4:3 landscape tablet on 16:9 monitor: vertical slice of tablet used, corners still reachable
        self.assertEqual(m.to_screen(11180, 0), (1919, 1079))  # upright top-right -> bottom-right
        l, t = m.to_screen(0, 15340)  # upright bottom-left -> landscape-cw top-left
        self.assertEqual((l, t), (0, 0))
        # centre maps to centre
        self.assertEqual(m.to_screen(5590, 7670), (960, 540))

    def test_edge_lift(self):
        # portrait tablet on a 16:9 monitor, crop: only a middle band of the tablet's height is mapped
        S = rt.PenState
        stroke = [S(x=5590, y=7670, pen=True, touch=True, pressure=2000),   # middle: inside
                  S(x=5590, y=300, pen=True, touch=True, pressure=2000),    # top: off the band
                  S(x=5590, y=200, pen=True, touch=True, pressure=2000),
                  S(x=5590, y=7000, pen=True, touch=True, pressure=2000)]   # back in
        m = mapper(fit="crop")
        self.assertTrue(m.inside(5590, 7670))
        self.assertFalse(m.inside(5590, 300))
        tr = rt.PenTranslator(m, has_tilt=False)
        kinds = [tuple(sorted(f.flags)) for s in stroke for f in tr.step(s)]
        self.assertEqual(kinds, [("DOWN", "INCONTACT", "INRANGE"), ("INRANGE", "UP"),
                                 ("INRANGE", "UPDATE"), ("DOWN", "INCONTACT", "INRANGE")])
        m = mapper(fit="crop", edge_lift=False)  # old behaviour: keeps drawing, pinned to the edge
        tr = rt.PenTranslator(m, has_tilt=False)
        frames = [f for s in stroke for f in tr.step(s)]
        self.assertTrue(all("INCONTACT" in f.flags for f in frames))
        self.assertEqual(frames[1].y, 0)

    def test_letterbox_shrinks(self):
        m = mapper(orientation="landscape-cw", fit="letterbox")
        x, y = m.to_screen(0, 15340)
        self.assertEqual(y, 0)
        self.assertGreater(x, 100)  # padded left and right

    def test_tilt_units_and_rotation(self):
        m = mapper(orientation="landscape-cw")
        self.assertEqual(m.tilt("TILT_X", 4500), 45)
        self.assertEqual(m.tilt_xy(3000, 0), (0, 30))
        m2 = mapper()
        m2.absinfo = dict(ABS, TILT_X={"min": -1000, "max": 1000, "res": 1146})  # units/radian
        self.assertEqual(m2.tilt("TILT_X", 573), 29)

    def test_paper_pro_bogus_resolution_ignored(self):
        # values read from a real Paper Pro (kernel 6.12.49 imx8mm-ferrari) on 2026-10-27
        m = mapper()
        m.absinfo = dict(ABS, X={"min": 0, "max": 11180, "res": 2832}, Y={"min": 0, "max": 15340, "res": 2064})
        self.assertAlmostEqual(m.tablet_aspect(), 0.75)

    def test_pressure_curve(self):
        m = mapper(pressure_gamma=2.0)
        self.assertEqual(m.pressure(4095), 1024)
        self.assertEqual(m.pressure(2047), 256)
        self.assertEqual(m.pressure(1), 1)  # touching is never zero pressure
        self.assertEqual(m.pressure(0), 0)


if __name__ == "__main__":
    unittest.main()


class OlderModels(unittest.TestCase):
    """reMarkable 1 / 2: Wacom digitizer turned 270 degrees against the panel."""
    WACOM = {"path": "/dev/input/event1", "name": "Wacom I2C Digitizer", "keys": ["TOOL_PEN", "TOOL_RUBBER", "TOUCH"],
             "abs": {"X": {"min": 0, "max": 20967}, "Y": {"min": 0, "max": 15725}, "PRESSURE": {"min": 0, "max": 4095},
                     "TILT_X": {"min": -9000, "max": 9000}, "TILT_Y": {"min": -9000, "max": 9000}}}

    def info(self, model=None, machine="armv7l", devs=None):
        return {"machine": machine, "model": model, "input_event_size": 16, "devices": devs or [self.WACOM]}

    def test_detects_the_model(self):
        import devices
        self.assertEqual(devices.detect(self.info("reMarkable 2.0")).key, "rm2")
        self.assertEqual(devices.detect(self.info("reMarkable 1.0")).key, "rm1")
        self.assertEqual(devices.detect(self.info(None, "aarch64", [])).key, "ferrari")  # older helper
        self.assertEqual(devices.detect({"machine": "armv7l", "devices": [self.WACOM, {"name": "pt_mt"}]}).key, "rm2")
        self.assertEqual(rt.event_format(self.info()), "<llHHi")

    def test_wacom_axes_turn_to_run_with_the_panel(self):
        pen = rt.pick_pen(self.info("reMarkable 2.0"))
        self.assertEqual(pen["rot"], 270)
        self.assertFalse(pen["invert_tilt_y"])
        self.assertEqual((pen["abs"]["X"]["max"], pen["abs"]["Y"]["max"]), (15725, 20967))  # short side across
        p = rt.EvdevParser("<llHHi", pen)
        ev = lambda t, c, v: struct.pack("<llHHi", 0, 0, t, c, v)
        # raw X near its max = near the top of the panel; raw Y near 0 = left edge
        data = (ev(1, rt.BTN_TOOL_PEN, 1) + ev(3, rt.ABS_X, 20000) + ev(3, rt.ABS_Y, 1000)
                + ev(3, rt.ABS_TILT_X, 3000) + ev(3, rt.ABS_TILT_Y, -2000) + ev(0, 0, 0))
        s = list(p.feed(data))[-1]
        self.assertEqual((s.x, s.y), (1000, 967))
        self.assertEqual((s.tilt_x, s.tilt_y), (-2000, -3000))
        m = rt.Mapper(absinfo=pen["abs"], monitor=(0, 0, 1000, 1000), fit="stretch", invert_tilt_y=False)
        u, v = m.uv(s.x, s.y)
        self.assertLess(u, 0.1)
        self.assertLess(v, 0.1)

    def test_paper_pro_pen_is_unchanged(self):
        elan = dict(self.WACOM, name="Elan marker input")
        pen = rt.pick_pen(self.info(None, "aarch64", [elan]))
        self.assertEqual((pen["rot"], pen["invert_tilt_y"]), (0, True))
        self.assertEqual(pen["abs"]["X"]["max"], 20967)

