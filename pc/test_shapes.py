"""Shape recognition, hold-to-snap and tablet shortcuts (no tablet or Windows needed)."""
import math
import random
import unittest

import keys
import rmtablet as rt
import shapes

ABS = {"X": {"min": 0, "max": 11180}, "Y": {"min": 0, "max": 15340}, "PRESSURE": {"min": 0, "max": 4096},
       "TILT_X": {"min": -9000, "max": 9000}, "TILT_Y": {"min": -9000, "max": 9000}}


def noisy(pts, n=0.4, seed=1):
    r = random.Random(seed)
    return [(x + r.gauss(0, n), y + r.gauss(0, n)) for x, y in pts]


def ellipse(cx, cy, a, b, th, n=200, sweep=2 * math.pi, start=0.3):
    c, s = math.cos(th), math.sin(th)
    return [(cx + a * math.cos(start + sweep * i / n) * c - b * math.sin(start + sweep * i / n) * s,
             cy + a * math.cos(start + sweep * i / n) * s + b * math.sin(start + sweep * i / n) * c)
            for i in range(n + 1)]


def polygon(vs, per=60, closed=True):
    L = vs + ([vs[0]] if closed else [])
    pts = []
    for a, b in zip(L, L[1:]):
        pts += [(a[0] + (b[0] - a[0]) * t / per, a[1] + (b[1] - a[1]) * t / per) for t in range(per)]
    return pts + [L[-1]]


class Recognize(unittest.TestCase):
    def kind(self, pts):
        r = shapes.recognize(pts)
        return r and r["kind"]

    def test_shapes(self):
        for seed in range(5):
            self.assertEqual(self.kind(noisy(ellipse(80, 80, 30, 30, 0), seed=seed)), "circle")
            self.assertEqual(self.kind(noisy(ellipse(80, 80, 40, 20, 0.5), seed=seed)), "ellipse")
            self.assertEqual(self.kind(noisy(polygon([(40, 60), (110, 62), (111, 101), (41, 99)]), seed=seed)), "rectangle")
            self.assertEqual(self.kind(noisy(polygon([(50, 100), (100, 100), (70, 50)]), seed=seed)), "triangle")
            self.assertEqual(self.kind(noisy(polygon([(10, 10), (90, 40)], closed=False), seed=seed)), "line")
            self.assertEqual(self.kind(noisy(ellipse(80, 80, 40, 40, 0, n=120, sweep=2.2), seed=seed)), "arc")
            self.assertEqual(self.kind(noisy(polygon([(10, 10), (40, 60), (70, 10), (100, 60)], closed=False), seed=seed)), "polyline")

    def test_not_shapes(self):
        self.assertIsNone(self.kind([(80 + 30 * math.sin(i / 9) * math.cos(i / 23), 80 + 25 * math.sin(i / 13 + 1)) for i in range(400)]))
        self.assertIsNone(self.kind([(10 + i * 0.4, 50 + 20 * math.sin(i / 30)) for i in range(250)]))
        self.assertIsNone(self.kind([(10, 10), (11, 11), (12, 10)]))

    def test_rectangle_is_squared(self):
        r = shapes.recognize(noisy(polygon([(40, 60), (110, 62), (111, 101), (41, 99)])))
        corners = r["outline"][:4]
        for i in range(4):
            a, b, c = corners[i - 1], corners[i], corners[(i + 1) % 4]
            self.assertAlmostEqual(shapes._turn(a, b, c), 90, delta=0.5)


class Snap(unittest.TestCase):
    def setUp(self):
        self.t = [0.0]
        self.sent, self.events = [], []
        m = rt.Mapper(absinfo=ABS, monitor=(0, 0, 1920, 1080), orientation="landscape-cw")
        self.tr = rt.PenTranslator(m, True)
        self.sn = shapes.Snapper(m, self.sent.append, hold_ms=600, undo=lambda: self.events.append("undo"),
                                 app=lambda: self.app, on_snap=lambda info, raws: self.events.append(info["ok"]),
                                 clock=lambda: self.t[0], sleep=lambda s: None)
        self.app = "photoshop"

    def pen(self, x, y, touch=True):
        s = rt.PenState()
        s.x, s.y = int(x / rt.PANEL_MM[0] * 11180), int(y / rt.PANEL_MM[1] * 15340)
        s.pressure, s.touch, s.pen = (2000 if touch else 0), touch, True
        self.t[0] += 0.008
        self.sent += self.sn.feed(s, self.tr.step(s))
        self.sn.tick()

    def draw_circle_and_rest(self, rest=100):
        pts = [(90 + 30 * math.cos(t / 40), 120 + 30 * math.sin(t / 40)) for t in range(260)]
        for p in pts:
            self.pen(*p)
        for _ in range(rest):
            self.pen(*pts[-1])
        return pts

    def test_snaps_after_rest(self):
        pts = self.draw_circle_and_rest()
        self.assertEqual(self.events, ["undo", True])
        self.pen(*pts[-1], touch=False)
        self.assertEqual(sum("DOWN" in f.flags for f in self.sent), 2)  # the real stroke + the shape
        self.assertEqual(sum("UP" in f.flags for f in self.sent), 2)    # real lift swallowed
        self.assertFalse(self.sn.snapped)

    def test_no_snap_without_rest(self):
        self.draw_circle_and_rest(rest=40)  # 0.32 s
        self.assertEqual(self.events, [])

    def test_only_in_drawing_apps(self):
        self.app = "explorer"
        self.draw_circle_and_rest()
        self.assertEqual(self.events, [False])  # recognised, but no Ctrl+Z sent

    def test_allowlist(self):
        self.assertTrue(shapes.app_allowed("Photoshop", ["photoshop"]))
        self.assertTrue(shapes.app_allowed("krita", [" krita "]))
        self.assertFalse(shapes.app_allowed("explorer", shapes.DRAWING_APPS))
        self.assertFalse(shapes.app_allowed("", shapes.DRAWING_APPS))


class Keys(unittest.TestCase):
    def test_combo_and_hold(self):
        log = []
        k = keys.Keys(send=lambda vk, up: log.append((vk, up)))
        k.combo("ctrl+shift+z")
        self.assertEqual(log, [(0x11, False), (0x10, False), (0x5A, False), (0x5A, True), (0x10, True), (0x11, True)])
        log.clear()
        k.down("ctrl")
        k.combo("ctrl+z")  # Ctrl already held: only Z is tapped
        self.assertEqual(log, [(0x11, False), (0x5A, False), (0x5A, True)])
        log.clear()
        k.down("alt")
        k.release_all()
        self.assertEqual(k.held, set())
        self.assertIn((0x12, True), log)
        self.assertIn((0x11, True), log)


if __name__ == "__main__":
    unittest.main()
