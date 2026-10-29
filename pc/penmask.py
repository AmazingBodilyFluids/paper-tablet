"""The pen mask: N x N cells over the tablet screen as it sits (view orientation), one byte each,
0 = open .. 255 = the pen does nothing in Windows. Kept as a zlib file next to the settings; the
contrast setting is applied over it, so the stored mask never changes when the contrast does."""
import base64
import itertools
import math
import os
import struct
import threading
import zlib

N = 720           # cells across and down: about 0.25 x 0.33 mm each on the Paper Pro
FULL = 250        # from here up the pen leaves Windows altogether
_OLD_N = 72
THUMB = 120       # thumbnail cells across and down, for the tablet's mask strip
_versions = itertools.count(1)  # one sequence for every mask, so a version names one mask's state


def legacy(h):
    """An old hex mask from the settings file (72 x 72, either 1 bit or one 0-f level per cell) as
    N x N bytes, or None if there's nothing in it."""
    h = "".join(c for c in str(h or "").lower() if c in "0123456789abcdef")
    if not h.strip("0"):
        return None
    if len(h) == _OLD_N * _OLD_N // 4:
        lv = [15 if (int(c, 16) >> (3 - k)) & 1 else 0 for c in h for k in range(4)]
    else:
        h = h[:_OLD_N * _OLD_N].ljust(_OLD_N * _OLD_N, "0")
        lv = [int(c, 16) for c in h]
    f = N // _OLD_N
    out = bytearray(N * N)
    for r in range(N):
        src = lv[(r // f) * _OLD_N:(r // f + 1) * _OLD_N]
        out[r * N:(r + 1) * N] = bytes(v * 17 for v in src for _ in range(f))
    return out


def contrast_table(k):
    """Level -> level, pushed away from mid grey by k (below 1 towards it)."""
    k = float(k or 1)
    return bytes(max(0, min(255, math.floor(128 + (v - 127.5) * k))) for v in range(256))


def png_la(levels, n=N):
    """An n x n black image whose alpha is the level: what the tablet lays over the screen."""
    raw = bytearray()
    row = bytearray(2 * n)
    for r in range(n):
        row[1::2] = levels[r * n:(r + 1) * n]
        raw += b"\0" + row
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", n, n, 8, 4, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + chunk(b"IEND", b""))


class Mask:
    def __init__(self, path=None, contrast=1.0, invert=False):
        self.path = path
        self.lock = threading.Lock()
        self.raw = bytearray(N * N)
        self.ver = 0
        if path and os.path.exists(path):
            try:
                with open(path, "rb") as f:
                    data = zlib.decompress(f.read())
                if len(data) == N * N:
                    self.raw = bytearray(data)
            except (OSError, zlib.error):
                pass
        self.set_contrast(contrast, invert)

    # ---- what the pen and the tablet use
    def set_contrast(self, k, invert=False):
        """The contrast, and whether black and white swap (before the contrast), over the stored mask."""
        with self.lock:
            t = contrast_table(k)
            self.table = bytes(t[255 - v] for v in range(256)) if invert else t
            self.eff = bytes(self.raw).translate(self.table)
            self.cov = sum(self.eff) / (255 * N * N)
            self.ver = next(_versions)

    def level(self, p, q):
        c, r = min(N - 1, max(0, int(p * N))), min(N - 1, max(0, int(q * N)))
        return self.eff[r * N + c]

    def png(self):
        return png_la(self.eff)

    def thumb(self):
        """A small version for the tablet's mask strip (each thumbnail cell: the darkest it covers)."""
        f = N // THUMB
        rows = [self.eff[r * N:(r + 1) * N] for r in range(N)]
        out = bytearray(THUMB * THUMB)
        for tr in range(THUMB):
            block = rows[tr * f:(tr + 1) * f]
            for tc in range(THUMB):
                out[tr * THUMB + tc] = max(max(row[tc * f:(tc + 1) * f]) for row in block)
        return png_la(out, THUMB)

    def coverage(self):
        """How much of the screen is masked, 0..1 (as used, after the contrast)."""
        return self.cov

    # ---- changes
    def _changed(self):
        self.eff = bytes(self.raw).translate(self.table)
        self.cov = sum(self.eff) / (255 * N * N)
        self.ver = next(_versions)
        if self.path:
            tmp = self.path + ".tmp"
            with open(tmp, "wb") as f:
                f.write(zlib.compress(bytes(self.raw), 6))
            os.replace(tmp, self.path)

    def set_raw(self, data):
        if len(data) != N * N:
            raise ValueError(f"mask must be {N} x {N} bytes")
        with self.lock:
            self.raw = bytearray(data)
            self._changed()

    def set_b64(self, b64):
        self.set_raw(base64.b64decode(b64))

    def b64(self):
        return base64.b64encode(bytes(self.raw)).decode("ascii")

    def op(self, name):
        with self.lock:
            if name == "clear":
                self.raw = bytearray(N * N)
            elif name == "invert":
                self.raw = bytearray(255 - v for v in self.raw)
            else:
                raise ValueError(name)
            self._changed()
