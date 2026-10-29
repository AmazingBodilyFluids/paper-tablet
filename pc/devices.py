"""The reMarkable models the app knows, and how to tell which one is plugged in.

Everything else in the app works in panel terms: the pen helper's raw digitizer axes are turned
to run with the panel's pixels in portrait (X to the right, Y down) as they are read, so
calibration, the tablet screen, shapes and the mask don't need to know the model.

Sources: screen sizes and densities from reMarkable's product specifications; the input
devices and their orientation from reMarkable's own Qt guide (developer.remarkable.com,
touch rotation on rM1 / rM2) and libremarkable (MIT; rM1 / rM2 Wacom digitizer turned 270
degrees against the panel); the Paper Pro from measurements on a real tablet (2026-10-27).
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Model:
    key: str
    name: str
    screen: tuple          # panel pixels, portrait (width, height)
    panel_mm: tuple        # active area, portrait (width, height)
    helper: str            # pen helper binary in tablet/
    companion: str         # tablet-screen app in tablet/companion/
    pen_rot: int = 0       # quarter turns of the digitizer against the panel, in degrees (0 or 270)
    invert_tilt_y: bool = False  # the digitizer's TILT_Y runs against its Y axis
    frontlight: bool = False
    developer_mode: bool = False  # SSH needs developer mode (which resets the tablet)
    verified: bool = False  # tested on real hardware

    @property
    def px_per_mm(self):
        return self.screen[1] / self.panel_mm[1]


MODELS = {
    "ferrari": Model("ferrari", "reMarkable Paper Pro", (1620, 2160), (179.8, 239.8),
                     "rmpp-pen", "rmpp-companion", invert_tilt_y=True, frontlight=True,
                     developer_mode=True, verified=True),
    # 10.3", 1404 x 1872 at 226 ppi
    "rm2": Model("rm2", "reMarkable 2", (1404, 1872), (157.8, 210.4),
                 "rmpp-pen-armv7", "rmpp-companion-rm2", pen_rot=270),
    "rm1": Model("rm1", "reMarkable 1", (1404, 1872), (157.8, 210.4),
                 "rmpp-pen-armv7", "rmpp-companion-rm1", pen_rot=270),
}
DEFAULT = MODELS["ferrari"]


def helper_for_arch(machine):
    """The pen helper that runs on a CPU (`uname -m`)."""
    return "rmpp-pen" if machine.startswith("aarch64") else "rmpp-pen-armv7"


def detect(info):
    """The model from the pen helper's `info` (its "model" line is /sys/devices/soc0/machine or
    the device-tree model; older helpers didn't report one)."""
    text = " ".join(str(info.get(k) or "") for k in ("model", "hwrevision")).lower()
    if "remarkable 2" in text or "remarkable2" in text:
        return MODELS["rm2"]
    if "remarkable 1" in text or "remarkable1" in text or "prototype 1" in text:
        return MODELS["rm1"]
    names = " ".join(d.get("name", "") for d in info.get("devices", [])).lower()
    if not str(info.get("machine", "")).startswith("aarch64") and "wacom" in names:
        # a 32-bit reMarkable that didn't say which: the rM2's touch panel is "pt_mt"
        return MODELS["rm2"] if "pt_mt" in names else MODELS["rm1"]
    return DEFAULT
