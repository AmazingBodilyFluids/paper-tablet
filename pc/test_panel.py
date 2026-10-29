"""Panel buttons, key combinations and tablet actions (no Windows, no tablet needed)."""
import threading
import types
import base64
import unittest
import zlib

import core
import penmask
import rmtablet as rt
import keys


class KeyNames(unittest.TestCase):
    def test_parse_and_label(self):
        self.assertEqual(keys.parse("Ctrl + Shift + S"), ["ctrl", "shift", "s"])
        self.assertEqual(keys.label("ctrl+shift+z"), "Ctrl Shift Z")
        self.assertEqual(keys.label("ctrl+="), "Ctrl =")
        self.assertEqual(keys.label("pagedown"), "PgDn")
        self.assertEqual(keys.label("bogus"), "")
        for bad in ("", "+", "ctrl+numpadplus"):
            with self.assertRaises(ValueError):
                keys.parse(bad)

    def test_every_name_the_editor_records_is_known(self):
        # the names app.js makes from KeyboardEvent.code
        recorded = list("abcdefghijklmnopqrstuvwxyz0123456789") + [f"f{n}" for n in range(1, 25)] + [
            "space", "enter", "esc", "backspace", "tab", "delete", "insert", "home", "end", "pageup", "pagedown",
            "left", "right", "up", "down", "[", "]", "\\", ";", "'", ",", ".", "/", "`", "-", "=",
            "ctrl", "shift", "alt", "win"]
        for n in recorded:
            self.assertIn(n, keys.VK, n)

    def test_arrows_are_extended_and_holds_release(self):
        sent = []
        k = keys.Keys(send=lambda vk, up, ext=False: sent.append((vk, up, ext)))
        k.combo("ctrl+left")
        self.assertEqual(sent, [(0x11, False, False), (0x25, False, True), (0x25, True, True), (0x11, True, False)])
        sent.clear()
        k.down("ctrl+shift")
        self.assertEqual(k.held, {"ctrl", "shift"})
        k.release_all()
        self.assertEqual(k.held, set())
        self.assertEqual({s for s in sent if s[1]}, {(0x10, True, False), (0x11, True, False)})

    def test_alt_release_is_masked(self):
        sent = []
        k = keys.Keys(send=lambda vk, up, ext=False: sent.append((vk, up)))
        k.down("alt")
        k.up("alt")
        self.assertEqual(sent, [(0x12, False), (0x11, False), (0x11, True), (0x12, True)])


class CleanButtons(unittest.TestCase):
    def test_defaults_are_clean_and_sixteen(self):
        self.assertEqual(core.clean_buttons(core.DEFAULT_BUTTONS), core.DEFAULT_BUTTONS)
        self.assertEqual(len(core.DEFAULT_BUTTONS), core.PANEL_SLOTS)

    def test_pads_trims_and_validates(self):
        out = core.clean_buttons([
            {"t": "A very long title here", "kind": "shortcut", "keys": "Ctrl + S"},
            {"t": "Bad", "kind": "shortcut", "keys": "ctrl+nope"},
            {"t": "Weird", "kind": "launch-rockets", "keys": "x"},
            {"t": "Pen", "kind": "pen", "keys": "ctrl+z"},
            "not a dict",
        ] + [{}] * 20)
        self.assertEqual(len(out), core.PANEL_SLOTS)
        self.assertEqual(out[0], {"t": "A very long ti", "kind": "shortcut", "keys": "ctrl+s"})
        self.assertEqual(out[1]["kind"], "none")
        self.assertEqual(out[2]["kind"], "none")
        self.assertEqual(out[3], {"t": "Pen", "kind": "pen", "keys": ""})
        self.assertEqual(out[4], {"t": "", "kind": "none", "keys": ""})
        self.assertEqual(len(core.clean_buttons(None)), core.PANEL_SLOTS)


def bare_engine(cfg=None):
    """An Engine without its constructor (no config file, monitors or threads)."""
    e = object.__new__(core.Engine)
    e.cfg = dict(core.DEFAULTS, **(cfg or {}))
    e.model = core.devices.DEFAULT
    e.sent = []
    e.keys = keys.Keys(send=lambda vk, up, ext=False: e.sent.append((vk, up)))
    moves = e.moves = []
    e.mouse = keys.Mouse(send=lambda f, x=0, y=0: moves.append((f, x, y)), cursor=lambda: (500, 400),
                         desk=lambda: (0, 0, 1920, 1080))
    e.pan_space = False
    e.last_action = None
    e.mapper = None
    e.changed = lambda: None
    e.settings = []
    e.set = lambda k, v: e.settings.append((k, v))
    e.set_output = lambda on: e.settings.append(("output", on))
    e.companion = types.SimpleNamespace(running=True)
    e.cal = None
    e.companion_rotation = lambda: 3
    return e


class TabletActions(unittest.TestCase):
    def test_shortcut_button_taps_its_keys(self):
        e = bare_engine({"panel_buttons": [{"t": "Save", "kind": "shortcut", "keys": "ctrl+s"}]})
        e.tablet_action("press 0")
        self.assertEqual(e.sent, [(0x11, False), (0x53, False), (0x53, True), (0x11, True)])
        e.sent.clear()
        e.tablet_action("release 0")
        self.assertEqual(e.sent, [])
        self.assertEqual(e.last_action["action"], "press 0")  # a tap button ignores its release

    def test_hold_button_holds_until_release(self):
        e = bare_engine({"panel_buttons": [{}, {"t": "Rotate", "kind": "hold", "keys": "r"}]})
        e.tablet_action("press 1")
        self.assertEqual(e.keys.held, {"r"})
        e.tablet_action("release 1")
        self.assertEqual(e.keys.held, set())
        self.assertEqual(e.sent, [(0x52, False), (0x52, True)])

    def test_empty_and_out_of_range_do_nothing(self):
        e = bare_engine()
        e.tablet_action("press 99")
        e.tablet_action("press x")
        e.tablet_action("bogus")
        self.assertEqual((e.sent, e.last_action), ([], None))

    def test_gestures_send_shortcuts(self):
        e = bare_engine()
        e.tablet_action("undo")
        e.tablet_action("zoomin")
        self.assertEqual([vk for vk, up in e.sent if not up], [0x11, 0x5A, 0x11, 0xBB])

    def test_pan_holds_space_and_drags(self):
        e = bare_engine()
        e.pan_scale = lambda: (2.0, 1.0)
        e.tablet_action("pan begin")
        self.assertEqual(e.keys.held, {"space"})
        e.tablet_action("pan 10 -20")
        e.tablet_action("pan end")
        self.assertEqual(e.keys.held, set())
        flags = [m[0] for m in e.moves]
        self.assertEqual(flags[0], keys.Mouse.LDOWN)
        self.assertEqual(flags[-1], keys.Mouse.LUP)
        f, x, y = e.moves[1]
        self.assertEqual((x, y), e.mouse._abs(520, 380))
        self.assertEqual(e.last_action["action"], "pan end")

    def test_pan_keeps_a_space_already_held(self):
        e = bare_engine()
        e.pan_scale = lambda: (1.0, 1.0)
        e.keys.down("space")
        e.tablet_action("pan begin")
        e.tablet_action("pan end")
        self.assertEqual(e.keys.held, {"space"})

    def test_fold_pen_and_snap(self):
        e = bare_engine()
        e.tablet_action("panel 0")
        e.tablet_action("pen 1")
        e.tablet_action("snap 0")
        self.assertEqual(e.settings, [("panel_open", False), ("output", True), ("snap", False)])


class Profiles(unittest.TestCase):
    def engine(self, cfg=None):
        e = bare_engine(cfg)
        core.load_profiles(e.cfg)
        e.set = core.Engine.set.__get__(e)
        e.lock = threading.RLock()
        e.refresh_mapper = e.save = e.sync_companion = lambda: None
        e.worker = None
        e.companion = types.SimpleNamespace(running=True, lines=[])
        e.companion.send = e.companion.lines.append
        e.companion.light = lambda on: None
        return e

    def test_old_layout_becomes_first_profile(self):
        mine = core.clean_buttons([{"t": "Mine", "kind": "shortcut", "keys": "m"}])
        e = self.engine({"panel_buttons": mine})
        self.assertEqual(len(e.cfg["panel_profiles"]), 1)
        self.assertEqual(e.cfg["panel_profiles"][0]["buttons"], mine)
        self.assertEqual(e.buttons(), mine)

    def test_cycle_switches_buttons_and_wraps(self):
        a = core.clean_buttons([{"t": "A", "kind": "shortcut", "keys": "a"}])
        b = core.clean_buttons([{"t": "B", "kind": "hold", "keys": "b"}])
        e = self.engine({"panel_profiles": [{"name": "PS", "buttons": a}, {"name": "Krita", "buttons": b}]})
        e.tablet_action("press 0")
        self.assertEqual(e.sent[0], (0x41, False))  # A
        e.keys.down("shift")
        e.tablet_action("profile next")
        self.assertEqual(e.cfg["panel_profile"], 1)
        self.assertEqual(e.keys.held, set())  # held keys let go on a switch
        self.assertIn("toast Krita", e.companion.lines)
        e.tablet_action("press 0")
        self.assertEqual(e.keys.held, {"b"})
        e.tablet_action("profile next")
        self.assertEqual(e.cfg["panel_profile"], 0)  # wraps round
        e.tablet_action("profile prev")
        self.assertEqual(e.cfg["panel_profile"], 1)

    def test_editing_buttons_edits_the_active_profile(self):
        e = self.engine({"panel_profiles": [{"name": "One"}, {"name": "Two"}], "panel_profile": 1})
        e.set("panel_buttons", [{"t": "X", "kind": "shortcut", "keys": "x"}])
        self.assertEqual(e.cfg["panel_profiles"][1]["buttons"][0]["t"], "X")
        self.assertEqual(e.cfg["panel_profiles"][0]["buttons"], core.clean_buttons(None))

    def test_deleting_profiles_keeps_a_valid_selection(self):
        e = self.engine({"panel_profiles": [{"name": "One"}, {"name": "Two"}], "panel_profile": 1})
        e.set("panel_profiles", [{"name": "  Only   one ", "buttons": []}])
        self.assertEqual(e.cfg["panel_profile"], 0)
        self.assertEqual(e.cfg["panel_profiles"][0]["name"], "Only one")
        e.set("panel_profiles", [])
        self.assertEqual(len(e.cfg["panel_profiles"]), 1)  # never none


class PaperAndTrace(Profiles):
    def test_tablet_moves_the_image_and_buttons_toggle(self):
        e = self.engine()
        e.tablet_action("trace 120.5 -40 2.5")
        self.assertEqual((e.cfg["trace_x"], e.cfg["trace_y"], e.cfg["trace_scale"]), (120.5, -40.0, 2.5))
        e.tablet_action("traceadjust")
        e.tablet_action("light")
        self.assertTrue(e.cfg["trace_adjust"])
        self.assertTrue(e.cfg["light"])

    def test_values_are_clamped(self):
        e = self.engine()
        e.set("paper", "nonsense")
        e.set("paper_mm", 100)
        e.set("trace_opacity", 0)
        self.assertEqual((e.cfg["paper"], e.cfg["paper_mm"], e.cfg["trace_opacity"]), ("plain", 30.0, 0.05))

    def test_new_button_kinds_survive_cleaning(self):
        b = core.clean_buttons([{"t": "Image", "kind": "trace"}, {"t": "Light", "kind": "light"}])
        self.assertEqual([x["kind"] for x in b[:2]], ["trace", "light"])


class TraceMask(Profiles):
    N = penmask.N

    def state(self, x, y):
        return rt.PenState(x=x, y=y, pen=True)

    def masked_engine(self, **cfg):
        e = self.engine(cfg)
        e.mapper = rt.Mapper(absinfo={"X": {"min": 0, "max": 7199}, "Y": {"min": 0, "max": 7199},
                                      "PRESSURE": {"min": 0, "max": 4096}}, monitor=(0, 0, 1920, 1080), fit="stretch")
        e.cal = None
        e.cfg["masks"], e.masks, e.blank = [], {}, penmask.Mask()
        e.mask_path = lambda mid: None
        e.push_mask = lambda: e.companion.lines.append("maskimg")
        e.push_strip = lambda: None
        e.use_mask("")
        return e

    def cell(self, c, r):  # the middle of a cell, in digitizer units (7200 / N per cell)
        k = 7200 / self.N
        return self.state(int((c + 0.5) * k), int((r + 0.5) * k))

    def test_mask_from_the_page_and_hit_test(self):
        e = self.masked_engine()
        lv = bytearray(self.N * self.N)
        lv[0] = 255
        lv[100 * self.N + 200] = 255
        lv[100 * self.N + 220] = 128
        e.set_mask(base64.b64encode(bytes(lv)).decode())
        self.assertTrue(e.cfg["mask_on"])  # making a mask switches it on
        self.assertIn("maskimg", e.companion.lines)
        self.assertTrue(e.masked(self.cell(0, 0)))
        self.assertTrue(e.masked(self.cell(200, 100)))
        self.assertFalse(e.masked(self.cell(201, 100)))
        self.assertEqual(e.mask_level(self.cell(220, 100)), 128)
        e.set("mask_on", False)
        self.assertFalse(e.masked(self.cell(0, 0)))

    def test_grey_fades_or_gates_the_pressure(self):
        e = self.masked_engine()
        s = rt.PenState(x=1, y=1, pen=True, touch=True, pressure=2048)
        self.assertEqual(e.mask_pressure(s, 0).pressure, 2048)
        self.assertEqual(e.mask_pressure(s, 255).pressure, 0)
        self.assertEqual(e.mask_pressure(s, 85).pressure, round(2048 * 170 / 255))  # fade: a third lighter
        e.set("mask_mode", "gate")
        self.assertEqual(e.mask_pressure(s, 153).pressure, 0)     # below the 60% threshold: no ink
        self.assertEqual(e.mask_pressure(s, 51).pressure, round(4096 * (0.5 - 0.2) / 0.8))
        e.set("mask_mode", "bogus")
        self.assertEqual(e.cfg["mask_mode"], "fade")

    def test_contrast_applies_over_the_stored_mask(self):
        e = self.masked_engine(mask_on=True)
        lv = bytearray(self.N * self.N)
        lv[0], lv[1] = 64, 191
        e.pmask.set_raw(lv)
        e.set("mask_contrast", 2.0)
        self.assertEqual((e.pmask.eff[0], e.pmask.eff[1]), (1, 255))
        self.assertEqual(e.pmask.raw[:2], bytearray([64, 191]))   # stored as it was
        self.assertEqual(e.mask_level(self.cell(1, 0)), 255)

    def test_invert_toggles_over_the_stored_mask(self):
        e = self.masked_engine()
        e.tablet_action("maskinvert")                 # no mask yet: nothing happens
        self.assertFalse(e.cfg["mask_invert"])
        a = e.new_mask()
        self.assertFalse(e.masked(self.cell(0, 0)))
        e.tablet_action("maskinvert")                 # the strip's Invert tile
        self.assertTrue(e.cfg["mask_invert"])
        self.assertTrue(e.masked(self.cell(0, 0)))
        self.assertEqual(e.pmask.raw[0], 0)           # stored as it was
        b = e.new_mask()
        self.assertFalse(e.cfg["mask_invert"])        # each mask keeps its own
        e.pick_mask(a)
        self.assertTrue(e.cfg["mask_invert"])
        e.set("mask_invert", False)
        self.assertFalse(e.masked(self.cell(0, 0)))

    def test_a_library_of_masks(self):
        e = self.masked_engine()
        self.assertFalse(e.masked(self.cell(0, 0)))
        a = e.new_mask()
        e.mask_op("invert")                       # all black
        self.assertEqual(e.cfg["mask_active"], a)
        self.assertTrue(e.masked(self.cell(0, 0)))
        e.set("mask_contrast", 2.0)
        b = e.new_mask("Rails")
        self.assertEqual([m["name"] for m in e.cfg["masks"]], ["Mask 1", "Rails"])
        self.assertFalse(e.masked(self.cell(0, 0)))  # the new one is empty
        self.assertEqual(e.cfg["mask_contrast"], 1.0)
        e.tablet_action(f"maskpick {a}")
        self.assertTrue(e.masked(self.cell(0, 0)))
        self.assertEqual(e.cfg["mask_contrast"], 2.0)  # each mask keeps its own settings
        e.tablet_action("maskpick none")
        self.assertFalse(e.cfg["mask_on"])
        self.assertFalse(e.masked(self.cell(0, 0)))
        e.pick_mask(a)
        e.delete_mask(a)
        self.assertEqual(e.cfg["mask_active"], b)
        e.rename_mask(b, "  Ruler  ")
        self.assertEqual(e.cfg["masks"], [{"id": b, "name": "Ruler", "contrast": 1.0, "mode": "fade", "invert": False}])

    def test_old_hex_masks_carry_over(self):
        old = "8" + "0" * (72 * 72 // 4 - 1)  # 1-bit format: only the first cell set
        lv = penmask.legacy(old)
        f = self.N // 72
        self.assertEqual((lv[0], lv[f - 1], lv[f], lv[(f - 1) * self.N], lv[f * self.N]), (255, 255, 0, 255, 0))
        self.assertEqual(penmask.legacy("0" * 5184), None)
        self.assertEqual(penmask.legacy("8" * 5184)[0], 136)    # level format: 8 of 15

    def test_png_is_a_valid_image(self):
        png = penmask.png_la(bytes(range(16)) * 1, n=4)
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        raw = zlib.decompress(png[png.index(b"IDAT") + 4:png.index(b"IEND") - 8])
        self.assertEqual(raw[:9], bytes([0, 0, 0, 0, 1, 0, 2, 0, 3]))


class PanelGeometry(unittest.TestCase):
    def engine(self, **cfg):
        e = bare_engine(cfg)
        e.mapper = types.SimpleNamespace(oriented=lambda u, v: (u, v), uv=lambda x, y: (x, y))
        return e

    def pen(self, p, q):
        return types.SimpleNamespace(x=p, y=q, in_range=True)

    def test_open_panel_takes_its_strip(self):
        e = self.engine(panel="left", panel_open=True)
        f = core.PANEL_PX / 2160
        self.assertAlmostEqual(e.panel_frac(), f)
        self.assertTrue(e.over_panel(self.pen(f / 2, 0.9)))
        self.assertFalse(e.over_panel(self.pen(f * 1.5, 0.5)))
        self.assertAlmostEqual(e.effective_area()[0], f)

    def test_folded_panel_leaves_only_the_tab(self):
        e = self.engine(panel="left", panel_open=False)
        self.assertEqual(e.panel_frac(), 0)
        self.assertEqual(e.effective_area(), list(core.DEFAULTS["area"]))
        self.assertTrue(e.over_panel(self.pen(0.02, 0.02)))
        self.assertFalse(e.over_panel(self.pen(0.02, 0.5)))
        self.assertFalse(e.over_panel(self.pen(0.1, 0.02)))

    def test_right_panel_tab_is_top_right(self):
        e = self.engine(panel="right", panel_open=False)
        self.assertTrue(e.over_panel(self.pen(0.99, 0.02)))
        self.assertFalse(e.over_panel(self.pen(0.01, 0.02)))


class TapGestures(unittest.TestCase):
    def test_clean_taps(self):
        out = core.clean_taps({"1x3": {"t": "New layer", "keys": "Ctrl+Shift+Alt+N"}, "2x1": {"t": "X", "keys": "nope"},
                               "9x9": {"t": "?", "keys": "a"}})
        self.assertEqual(set(out), set(core.TAP_SLOTS))
        self.assertEqual(out["1x3"], {"t": "New layer", "keys": "ctrl+shift+alt+n"})
        self.assertEqual(out["2x1"], {"t": "", "keys": ""})
        self.assertEqual(core.clean_taps(core.DEFAULT_TAPS), core.DEFAULT_TAPS)

    def test_tap_sends_its_keys_and_toast(self):
        e = bare_engine()
        sent = []
        e.companion = types.SimpleNamespace(running=True, send=sent.append)
        e.tablet_action("tap 1 3")
        self.assertEqual([vk for vk, up in e.sent if not up], [0x11, 0x10, 0x12, 0x4E])
        self.assertEqual(sent, ["toast New layer"])
        e.sent.clear()
        e.tablet_action("tap 2 1")  # unassigned
        e.tablet_action("tap 7 7")
        self.assertEqual(e.sent, [])


class Rotate(unittest.TestCase):
    def patch_center(self):
        old = keys.foreground_center
        keys.foreground_center = lambda: (1000, 600)
        self.addCleanup(setattr, keys, "foreground_center", old)

    def test_twist_holds_key_and_drags_round_the_centre(self):
        e = bare_engine()
        self.patch_center()
        e.tablet_action("rotate begin")
        self.assertEqual(e.keys.held, {"r"})
        self.assertEqual(e.mouse.start, (1000, 300))  # straight above the centre
        e.tablet_action("rotate 90")
        f, x, y = e.moves[-1]
        self.assertEqual((x, y), e.mouse._abs(1300, 600))  # a quarter turn clockwise
        e.tablet_action("rotate end")
        self.assertEqual(e.keys.held, set())
        self.assertEqual(e.moves[-2][0], keys.Mouse.LUP)
        self.assertEqual(e.moves[-1][1:], e.mouse._abs(500, 400))  # cursor put back
        self.assertEqual(e.last_action["action"], "rotate end")

    def test_rotate_key_is_configurable_and_keeps_held_keys(self):
        e = bare_engine({"rotate_keys": "shift+space"})
        self.patch_center()
        e.keys.down("shift")
        e.tablet_action("rotate begin")
        e.tablet_action("rotate end")
        self.assertEqual(e.keys.held, {"shift"})

    def test_stray_rotate_moves_are_ignored(self):
        e = bare_engine()
        e.tablet_action("rotate 30")
        e.tablet_action("rotate end")
        self.assertEqual(e.moves, [])


class ReportRate(unittest.TestCase):
    def test_parser_keeps_kernel_timestamps(self):
        import struct as st
        import rmtablet as rt
        rec = lambda sec, usec, t, c, v: st.pack("<qqHHi", sec, usec, t, c, v)
        p = rt.EvdevParser("<qqHHi")
        out = list(p.feed(rec(10, 500000, 3, 0, 5) + rec(10, 500000, 0, 0, 0)))
        self.assertEqual(out[0].t, 10.5)

    def test_rate_meter_median_and_gaps(self):
        r = core.RateMeter()
        t = 0.0
        for k in range(200):
            t += 0.5 if k == 100 else 1 / 240  # one long pause in the middle
            r.add(t, t + 3 + (0.002 if k % 3 else 0))
        res = r.compute()
        self.assertEqual(res["tablet"], 240)
        self.assertAlmostEqual(res["pc"], 240, delta=8)
        self.assertIsNotNone(r.last)


if __name__ == "__main__":
    unittest.main()


class WiFi(unittest.TestCase):
    class Tab:
        def __init__(self, ip="192.168.0.100"):
            self.cmds, self.ip = [], ip

        def run(self, cmd):
            self.cmds.append(cmd)
            if cmd.startswith("ip -4"):
                return 0, (f"3: wlan0    inet {self.ip}/24 brd 192.168.0.255 scope global wlan0\n" if self.ip else "").encode(), b""
            return 0, b"", b""

        def server_key(self):
            return "ssh-ed25519 AAAAkey"

    def engine(self, via="usb", connected=True, **cfg):
        e = bare_engine(cfg)
        e.lock = threading.RLock()
        e.save = lambda: None
        e.via = via
        e.worker = types.SimpleNamespace(tab=self.Tab(), connected=connected)
        e.disconnect = lambda: None
        real, core.port_open = core.port_open, lambda *a, **k: True
        self.addCleanup(setattr, core, "port_open", real)
        return e

    def test_reads_the_wlan_address(self):
        self.assertEqual(core.Engine.wlan_ip(b"3: wlan0    inet 10.0.0.7/24 brd 10.0.0.255 scope global wlan0"), "10.0.0.7")
        self.assertEqual(core.Engine.wlan_ip(b""), "")

    def test_switching_on_needs_the_cable(self):
        e = self.engine(via="wifi")
        self.assertFalse(e.set_wifi(True)["ok"])
        e = self.engine(connected=False)
        self.assertFalse(e.set_wifi(True)["ok"])

    def test_switching_on_pins_the_key_and_learns_the_address(self):
        e = self.engine()
        r = e.set_wifi(True)
        self.assertTrue(r["ok"], r)
        self.assertEqual((e.cfg["wifi"], e.cfg["wifi_host"], e.cfg["host_key"]), (True, "192.168.0.100", "ssh-ed25519 AAAAkey"))
        self.assertIn("rm-ssh-over-wlan on", e.worker.tab.cmds[0])

    def test_no_wifi_on_the_tablet(self):
        e = self.engine()
        e.worker.tab.ip = ""
        self.assertFalse(e.set_wifi(True)["ok"])
        self.assertFalse(e.cfg["wifi"])

    def test_switching_off_while_away_waits_for_the_cable(self):
        e = self.engine(connected=False, wifi=True, wifi_host="192.168.0.100")
        e.set_wifi(False)
        self.assertEqual((e.cfg["wifi"], e.cfg["wifi_off_pending"]), (False, True))
        tab = self.Tab()
        e.cable_chores(tab)
        self.assertIn("rm-ssh-over-wlan off", tab.cmds[0])
        self.assertFalse(e.cfg["wifi_off_pending"])

    def test_cable_keeps_the_address_current(self):
        e = self.engine(wifi=True, wifi_host="192.168.0.50")
        e.cable_chores(self.Tab("192.168.0.77"))
        self.assertEqual(e.cfg["wifi_host"], "192.168.0.77")
        self.assertEqual(e.cfg["host_key"], "ssh-ed25519 AAAAkey")

    def test_wifi_login_needs_a_pinned_key(self):
        e = self.engine(via="wifi", connected=False, wifi=True, wifi_host="192.168.0.100")
        with self.assertRaises(PermissionError):
            e.open_tablet()


class ProfileByProgram(Profiles):
    """The profile follows the program in front, until a profile is picked by hand."""

    def test_apps_are_cleaned(self):
        p = core.clean_profiles([{"name": "PS", "apps": " Photoshop ,krita,, X" * 40}])
        self.assertTrue(p[0]["apps"].startswith("photoshop, krita, x"))
        self.assertLessEqual(len(p[0]["apps"]), 200)
        self.assertEqual(core.clean_profiles([{}])[0]["apps"], "")

    def test_switches_when_the_program_changes(self):
        e = self.engine({"panel_profiles": [{"name": "PS", "apps": "photoshop"}, {"name": "Krita", "apps": "krita, kritarunner"}]})
        e.fg_app, e.auto_app = "", None
        e.auto_profile("krita")
        self.assertEqual(e.cfg["panel_profile"], 1)
        e.auto_profile("explorer")          # no profile lists it: nothing changes
        self.assertEqual(e.cfg["panel_profile"], 1)
        e.auto_profile("photoshop")
        self.assertEqual(e.cfg["panel_profile"], 0)
        e.tablet_action("profile next")      # picked by hand: stays while Photoshop is in front
        self.assertEqual(e.cfg["panel_profile"], 1)
        e.auto_profile("photoshop")
        self.assertEqual(e.cfg["panel_profile"], 1)
        e.auto_profile("krita")              # the program changed: automatic again
        e.auto_profile("photoshop")
        self.assertEqual(e.cfg["panel_profile"], 0)
        self.assertEqual(e.fg_app, "photoshop")
        e.auto_profile("")                   # unknown window: remembered as nothing, no switch
        self.assertEqual(e.cfg["panel_profile"], 0)


class Battery(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(core.Engine.parse_battery(b"82 Charging\n"), {"pct": 82, "status": "charging"})
        self.assertEqual(core.Engine.parse_battery(b"\n100 Full\n"), {"pct": 100, "status": "full"})
        self.assertEqual(core.Engine.parse_battery(b"7\n"), {"pct": 7, "status": "unknown"})
        self.assertEqual(core.Engine.parse_battery(b"cat: no such file\n"), None)
        self.assertEqual(core.Engine.parse_battery(b""), None)


class RetapOneTarget(Profiles):
    ABS = {"X": {"min": 0, "max": 11180, "res": 0}, "Y": {"min": 0, "max": 15340, "res": 0},
           "PRESSURE": {"min": 0, "max": 4096, "res": 0}}

    def calibrated(self):
        import math
        e = self.engine()
        e.pen = {"name": "pen", "abs": self.ABS}
        e.worker = types.SimpleNamespace(connected=True)
        targets = rt.grid_targets(9, 0.1)
        raw = lambda u, v: (v * 11180, (1 - u) * 15340)
        taps = [raw(u, v) for u, v in targets]
        taps[4] = (taps[4][0] + 900, taps[4][1])  # a mis-tap on the middle target
        e.cal = {"mode": "sheet9", "taps": taps[:-1]}
        e.on_tap(taps[-1])
        return e, raw, targets

    def test_bad_tap_is_offered_for_a_retap(self):
        e, raw, targets = self.calibrated()
        self.assertTrue(e.cal_result["ok"])
        self.assertEqual(e.cal_result.get("retap"), 4)
        self.assertEqual(e.calibration["mode"], "sheet9")
        self.assertGreater(e.calibration["residual_mm"][4], 2.0)

    def test_retap_replaces_one_tap_and_refits(self):
        e, raw, targets = self.calibrated()
        self.assertTrue(e.retap_target(4))
        self.assertEqual(e.cal["redo"], 4)
        self.assertEqual(core.Engine.cal_index(e.cal), 4)
        self.assertIn("again", e.cal_prompt(e.cal))
        e.on_tap(raw(*targets[4]))
        self.assertIsNone(e.cal)
        self.assertNotIn("retap", e.cal_result)
        self.assertIn("Target 5 tapped again", e.cal_result["text"])
        self.assertLess(max(e.calibration["residual_mm"]), 0.5)
        self.assertEqual(len(e.calibration["taps"]), 9)

    def test_retap_needs_a_tablet_and_a_valid_target(self):
        e, _, _ = self.calibrated()
        self.assertFalse(e.retap_target(9))
        e.worker = None
        self.assertFalse(e.retap_target(0))
        self.assertEqual(core.Engine.cal_mode_of({"targets": [[0, 0]] * 4, "inset": 0.0}), "corners")
        self.assertEqual(core.Engine.cal_mode_of({"targets": [[0, 0]] * 4, "inset": 0.1}), "sheet4")
