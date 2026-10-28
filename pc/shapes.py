"""Hold-to-snap shapes: draw, keep the pen still, and the stroke is replaced by a clean shape.

recognize() works on a stroke in millimetres on the tablet and returns a clean outline (line,
arc, polyline, triangle, rectangle/square, polygon, ellipse/circle) or None. Snapper sits
between the pen translator and the Windows pen: strokes pass through untouched, and when the
pen rests at the end of a recognised stroke it lifts the pen, undoes the stroke in the drawing
app (Ctrl+Z) and draws the clean shape as a new pen stroke with the stroke's own pressure.
No numpy: strokes are a few hundred points.
"""
import math
import sys
import time

from rmtablet import Frame, PANEL_MM, _solve


# --------------------------------------------------------------------------- geometry helpers

def _dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _length(pts):
    return sum(_dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))


def resample(pts, step):
    """Points every `step` along the path (keeps both ends)."""
    if len(pts) < 2:
        return list(pts)
    out = [pts[0]]
    carry = 0.0
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        d = _dist(a, b)
        if d == 0:
            continue
        t = step - carry
        while t <= d:
            out.append((a[0] + (b[0] - a[0]) * t / d, a[1] + (b[1] - a[1]) * t / d))
            t += step
        carry = (carry + d) % step
    if _dist(out[-1], pts[-1]) > step * 0.25:
        out.append(pts[-1])
    return out


def smooth(pts, k):
    """Moving average over 2k+1 points; tames sensor jitter that would add path length."""
    n = len(pts)
    if n < 2 * k + 3:
        return list(pts)
    out = [pts[0]]
    for i in range(1, n - 1):
        lo, hi = max(0, i - k), min(n, i + k + 1)
        w = pts[lo:hi]
        out.append((sum(p[0] for p in w) / len(w), sum(p[1] for p in w) / len(w)))
    return out + [pts[-1]]


def _seg_dist(p, a, b):
    vx, vy = b[0] - a[0], b[1] - a[1]
    L = vx * vx + vy * vy
    if L == 0:
        return _dist(p, a)
    t = max(0.0, min(1.0, ((p[0] - a[0]) * vx + (p[1] - a[1]) * vy) / L))
    return math.hypot(p[0] - a[0] - t * vx, p[1] - a[1] - t * vy)


def _poly_dist(p, poly):
    return min(_seg_dist(p, poly[i], poly[i + 1]) for i in range(len(poly) - 1))


def _mean_err(pts, outline):
    return sum(_poly_dist(p, outline) for p in pts) / len(pts)


def _rdp(pts, eps):
    """Ramer-Douglas-Peucker: indices of the kept points."""
    keep = {0, len(pts) - 1}
    stack = [(0, len(pts) - 1)]
    while stack:
        i, j = stack.pop()
        best, k = 0.0, None
        for m in range(i + 1, j):
            d = _seg_dist(pts[m], pts[i], pts[j])
            if d > best:
                best, k = d, m
        if k is not None and best > eps:
            keep.add(k)
            stack += [(i, k), (k, j)]
    return sorted(keep)


def _fit_line(pts):
    """Total least squares: (centroid, unit direction)."""
    n = len(pts)
    cx = sum(p[0] for p in pts) / n
    cy = sum(p[1] for p in pts) / n
    sxx = sum((p[0] - cx) ** 2 for p in pts)
    syy = sum((p[1] - cy) ** 2 for p in pts)
    sxy = sum((p[0] - cx) * (p[1] - cy) for p in pts)
    a = 0.5 * math.atan2(2 * sxy, sxx - syy)
    return (cx, cy), (math.cos(a), math.sin(a))


def _intersect(l1, l2):
    (p, d), (q, e) = l1, l2
    den = d[0] * e[1] - d[1] * e[0]
    if abs(den) < 1e-9:
        return None
    t = ((q[0] - p[0]) * e[1] - (q[1] - p[1]) * e[0]) / den
    return (p[0] + t * d[0], p[1] + t * d[1])


def _turn(a, b, c):
    """Absolute turning angle at b in degrees (0 = straight on)."""
    a1 = math.atan2(b[1] - a[1], b[0] - a[0])
    a2 = math.atan2(c[1] - b[1], c[0] - b[0])
    d = math.degrees(a2 - a1)
    return abs((d + 180) % 360 - 180)


def _sharp(pts, i, turn, closed=False, reach=4):
    """Is pts[i] a real corner and not a bend of a smooth curve? Most of the turn RDP found
    (`turn` degrees) has to happen within a few mm of it. Points are ~1 mm apart."""
    n = len(pts) - 1 if closed else len(pts)

    def at(j):
        return pts[j % n] if closed else pts[max(0, min(n - 1, j))]
    local = max(_turn(at(j - reach), at(j), at(j + reach)) for j in range(i - 5, i + 6))
    return local > max(25, 0.55 * turn)


def _signed_area(poly):
    return 0.5 * sum(poly[i][0] * poly[i + 1][1] - poly[i + 1][0] * poly[i][1]
                     for i in range(len(poly) - 1))


def _snap_angle(a, tol):
    """Snap an angle (radians) to the nearest multiple of 90 degrees within tol degrees."""
    q = round(a / (math.pi / 2)) * (math.pi / 2)
    return q if abs(math.degrees(a - q)) <= tol else a


# --------------------------------------------------------------------------- shape fits

def fit_ellipse(pts):
    """Algebraic conic fit A x^2 + B xy + C y^2 + D x + E y = 1 on normalised points.
    Returns (cx, cy, a, b, angle) or None if the points don't make an ellipse."""
    n = len(pts)
    mx = sum(p[0] for p in pts) / n
    my = sum(p[1] for p in pts) / n
    s = max(1e-9, math.sqrt(sum((p[0] - mx) ** 2 + (p[1] - my) ** 2 for p in pts) / n))
    rows = []
    for p in pts:
        x, y = (p[0] - mx) / s, (p[1] - my) / s
        rows.append((x * x, x * y, y * y, x, y))
    ata = [[sum(r[i] * r[j] for r in rows) for j in range(5)] for i in range(5)]
    atb = [sum(r[i] for r in rows) for i in range(5)]
    try:
        A, B, C, D, E = _solve(ata, atb)
    except ValueError:
        return None
    if B * B - 4 * A * C >= 0:
        return None
    det = 4 * A * C - B * B
    x0 = (B * E - 2 * C * D) / det
    y0 = (B * D - 2 * A * E) / det
    f = A * x0 * x0 + B * x0 * y0 + C * y0 * y0 + D * x0 + E * y0 - 1  # centred: Ax²+Bxy+Cy² = -f
    th = 0.5 * math.atan2(B, A - C)
    c, si = math.cos(th), math.sin(th)
    l1 = A * c * c + B * c * si + C * si * si
    l2 = A * si * si - B * c * si + C * c * c
    if l1 <= 0 or l2 <= 0 or f >= 0:
        return None
    a, b = math.sqrt(-f / l1) * s, math.sqrt(-f / l2) * s
    return mx + x0 * s, my + y0 * s, a, b, th


def fit_circle(pts):
    """Kasa fit x^2 + y^2 + D x + E y + F = 0. Returns (cx, cy, r) or None."""
    rows = [(p[0], p[1], 1.0) for p in pts]
    rhs = [-(p[0] ** 2 + p[1] ** 2) for p in pts]
    ata = [[sum(r[i] * r[j] for r in rows) for j in range(3)] for i in range(3)]
    atb = [sum(r[i] * v for r, v in zip(rows, rhs)) for i in range(3)]
    try:
        D, E, F = _solve(ata, atb)
    except ValueError:
        return None
    cx, cy = -D / 2, -E / 2
    r2 = cx * cx + cy * cy - F
    return (cx, cy, math.sqrt(r2)) if r2 > 0 else None


def ellipse_outline(cx, cy, a, b, th, start=0.0, sweep=2 * math.pi, step=1.0):
    n = max(12, int(abs(sweep) * max(a, b) / step))
    c, s = math.cos(th), math.sin(th)
    out = []
    for i in range(n + 1):
        t = start + sweep * i / n
        x, y = a * math.cos(t), b * math.sin(t)
        out.append((cx + x * c - y * s, cy + x * s + y * c))
    return out


def _ellipse_param(cx, cy, th, a, b, p):
    c, s = math.cos(th), math.sin(th)
    dx, dy = p[0] - cx, p[1] - cy
    return math.atan2((-dx * s + dy * c) / b, (dx * c + dy * s) / a)


def _closed_polygon(R, D):
    """Corners of a closed stroke, refined by fitting a line to each side. List of vertices."""
    # start the loop at the point farthest from the centroid so it begins at a corner
    n = len(R)
    cx = sum(p[0] for p in R) / n
    cy = sum(p[1] for p in R) / n
    k0 = max(range(n), key=lambda i: (R[i][0] - cx) ** 2 + (R[i][1] - cy) ** 2)
    loop = R[k0:] + R[:k0] + [R[k0]]
    idx = _rdp(loop, 0.06 * D)
    # drop corners that barely turn
    changed = True
    while changed and len(idx) > 4:
        changed = False
        for j in range(1, len(idx) - 1):
            if _turn(loop[idx[j - 1]], loop[idx[j]], loop[idx[j + 1]]) < 28:
                del idx[j]
                changed = True
                break
    # and corners left by a short wobble (a side shorter than a tenth of the shape)
    changed = True
    while changed and len(idx) > 4:
        changed = False
        for j in range(1, len(idx) - 1):
            if min(_dist(loop[idx[j - 1]], loop[idx[j]]), _dist(loop[idx[j]], loop[idx[j + 1]])) < 0.1 * D:
                del idx[j]
                changed = True
                break
    k = len(idx) - 1
    if not 3 <= k <= 6:
        return None
    ring = [loop[i] for i in idx]
    for j in range(k):
        if not _sharp(loop, idx[j], _turn(ring[j - 1] if j else ring[-2], ring[j], ring[j + 1]), closed=True):
            return None
    lines = []
    for j in range(k):
        i0, i1 = idx[j], idx[j + 1]
        span = i1 - i0
        cut = max(1, span // 6)
        seg = loop[i0 + cut:i1 - cut + 1] if span > 4 else loop[i0:i1 + 1]
        if len(seg) < 2:
            seg = loop[i0:i1 + 1]
        lines.append(_fit_line(seg))
    verts = []
    for j in range(k):
        v = _intersect(lines[j - 1], lines[j])
        if v is None or _dist(v, loop[idx[j]]) > 0.35 * D:
            v = loop[idx[j]]
        verts.append(v)
    return verts


def _rectangle(verts, D):
    """Square up a quadrilateral whose corners are all close to 90 degrees, else None."""
    loop = verts + verts[:1]
    for j in range(4):
        if abs(_turn(loop[j - 1] if j else verts[-1], verts[j], loop[j + 1]) - 90) > 16:
            return None
    # mean direction of the sides, folded onto 0..90 degrees
    sx = sy = 0.0
    for j in range(4):
        a = math.atan2(loop[j + 1][1] - loop[j][1], loop[j + 1][0] - loop[j][0])
        sx += math.cos(4 * a)
        sy += math.sin(4 * a)
    th = _snap_angle(math.atan2(sy, sx) / 4, 5)
    c, s = math.cos(th), math.sin(th)
    us = [v[0] * c + v[1] * s for v in verts]
    vs = [-v[0] * s + v[1] * c for v in verts]
    # opposite sides: average the two extreme coordinates on each axis
    us_sorted, vs_sorted = sorted(us), sorted(vs)
    u0, u1 = sum(us_sorted[:2]) / 2, sum(us_sorted[2:]) / 2
    v0, v1 = sum(vs_sorted[:2]) / 2, sum(vs_sorted[2:]) / 2
    w, h = u1 - u0, v1 - v0
    kind = "rectangle"
    if abs(w - h) <= 0.1 * max(w, h):
        m, uc, vc = (w + h) / 2, (u0 + u1) / 2, (v0 + v1) / 2
        u0, u1, v0, v1 = uc - m / 2, uc + m / 2, vc - m / 2, vc + m / 2
        kind = "square"
    corners = [(u0, v0), (u1, v0), (u1, v1), (u0, v1)]
    return kind, [(u * c - v * s, u * s + v * c) for u, v in corners]


def _start_like(outline, stroke, closed):
    """Begin the outline nearest where the stroke began and run the same way round."""
    if not closed:
        return outline
    ring = outline[:-1]
    if (_signed_area(ring + ring[:1]) > 0) != (_signed_area(stroke + stroke[:1]) > 0):
        ring = ring[::-1]
    k = min(range(len(ring)), key=lambda i: _dist(ring[i], stroke[0]))
    ring = ring[k:] + ring[:k]
    return ring + ring[:1]


def recognize(stroke, step=1.0):
    """stroke: [(x, y)] in mm. Returns {"kind", "outline" (mm points), "closed", "error"} or None."""
    R = resample(smooth(resample(stroke, step / 2), 2), step)
    if len(R) < 6:
        return None
    L = _length(R)
    xs, ys = [p[0] for p in R], [p[1] for p in R]
    D = math.hypot(max(xs) - min(xs), max(ys) - min(ys))
    if L < 8 or D < 5:
        return None
    gap = _dist(R[0], R[-1])

    # straight line
    if gap > 0.8 * L:
        dev = max(_seg_dist(p, R[0], R[-1]) for p in R)
        if dev < max(0.6, 0.045 * gap):
            (cx, cy), (dx, dy) = _fit_line(R)
            a = _snap_angle(math.atan2(dy, dx), 4)
            dx, dy = math.cos(a), math.sin(a)
            t0 = (R[0][0] - cx) * dx + (R[0][1] - cy) * dy
            t1 = (R[-1][0] - cx) * dx + (R[-1][1] - cy) * dy
            out = [(cx + t0 * dx, cy + t0 * dy), (cx + t1 * dx, cy + t1 * dy)]
            return {"kind": "line", "outline": out, "closed": False, "error": dev / gap}

    closed = gap < max(4.0, 0.22 * D) and L > 1.5 * D
    if closed:
        # trim an overshoot past the start so it doesn't skew the fits
        q = len(R) * 3 // 4
        cut = min(range(q, len(R)), key=lambda i: _dist(R[i], R[0]))
        R = R[:cut + 1]
        cands = []
        el = fit_ellipse(R)
        if el:
            cx, cy, a, b, th = el
            if min(a, b) > 0.15 * max(a, b) and max(a, b) < 1.5 * D:
                kind = "ellipse"
                th = _snap_angle(th, 7)
                if min(a, b) / max(a, b) > 0.88:
                    a = b = (a + b) / 2
                    th, kind = 0.0, "circle"
                out = ellipse_outline(cx, cy, a, b, th, step=step)
                cands.append((kind, out, _mean_err(R, out) / D))
        verts = _closed_polygon(R, D)
        if verts:
            k = len(verts)
            kind = {3: "triangle", 4: "quadrilateral"}.get(k, "polygon")
            poly = verts
            if k == 4:
                rect = _rectangle(verts, D)
                if rect:
                    kind, poly = rect
            out = poly + poly[:1]
            err = _mean_err(R, out) / D
            # corners present: prefer the polygon unless the ellipse is clearly better
            cands.append((kind, out, err * (0.7 if k <= 4 else 0.9)))
        cands = [c for c in cands if c[2] < 0.04]
        if not cands:
            return None
        kind, out, err = min(cands, key=lambda c: c[2])
        return {"kind": kind, "outline": _start_like(out, R, True), "closed": True, "error": err}

    # open curve: an arc of a circle, or a few straight segments
    cands = []
    ci = fit_circle(R)
    if ci:
        cx, cy, r = ci
        err = sum(abs(_dist(p, (cx, cy)) - r) for p in R) / len(R) / D
        a0 = math.atan2(R[0][1] - cy, R[0][0] - cx)
        am = math.atan2(R[len(R) // 2][1] - cy, R[len(R) // 2][0] - cx)
        a1 = math.atan2(R[-1][1] - cy, R[-1][0] - cx)
        tau = 2 * math.pi
        to_mid, to_end = (am - a0) % tau, (a1 - a0) % tau
        sweep = to_end if to_mid < to_end else to_end - tau  # the way that passes the middle
        if r < 3 * D and math.radians(25) < abs(sweep) < math.radians(330):
            out = ellipse_outline(cx, cy, r, r, 0.0, a0, sweep, step)
            cands.append(("arc", out, err))
    idx = _rdp(R, 0.05 * D)
    if 3 <= len(idx) <= 6 and all(_turn(R[idx[j - 1]], R[idx[j]], R[idx[j + 1]]) > 35
                                  and _sharp(R, idx[j], _turn(R[idx[j - 1]], R[idx[j]], R[idx[j + 1]]))
                                  for j in range(1, len(idx) - 1)):
        out = [R[i] for i in idx]
        cands.append(("polyline", out, _mean_err(R, out) / D * 0.8))
    cands = [c for c in cands if c[2] < 0.03]
    if not cands:
        return None
    kind, out, err = min(cands, key=lambda c: c[2])
    return {"kind": kind, "outline": out, "closed": False, "error": err}


# --------------------------------------------------------------------------- live snapping

DRAWING_APPS = ("photoshop", "krita", "clipstudiopaint", "illustrator", "mspaint", "sai", "gimp",
                "inkscape", "firealpaca", "medibang", "artrage", "rebelle", "affinity", "photo",
                "designer", "paintdotnet", "onenote", "xournal", "openboard", "fresco", "aseprite",
                "procreate", "infinitepainter", "corelpainter", "painter", "blender", "zbrush",
                "mypaint", "rnote", "pinta", "kolourpaint", "opentoonz", "azpainter", "drawing")


def foreground_app():
    """Executable name (lower case, no .exe) of the foreground window, or "" if unknown.
    Linux: the focused window's app class from the compositor (e.g. "krita", "gimp")."""
    if sys.platform.startswith("linux"):
        try:
            import linuxinput
            return linuxinput.active_window()[0]
        except Exception:
            return ""
    try:
        import ctypes
        from ctypes import wintypes as wt
        u, k = ctypes.windll.user32, ctypes.windll.kernel32
        hwnd = u.GetForegroundWindow()
        pid = wt.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        h = k.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(512)
            n = wt.DWORD(512)
            if not k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
                return ""
            name = buf.value.replace("/", "\\").rsplit("\\", 1)[-1].lower()
            return name[:-4] if name.endswith(".exe") else name
        finally:
            k.CloseHandle(h)
    except Exception:
        return ""


def app_allowed(name, apps):
    name = name.lower()
    return bool(name) and any(name.startswith(a.strip().lower()) for a in apps if a.strip())


def send_undo():
    """Ctrl+Z through SendInput (keybd_event is the same path and needs no structs)."""
    import ctypes
    u = ctypes.windll.user32
    VK_CONTROL, VK_Z, UP = 0x11, 0x5A, 0x2
    u.keybd_event(VK_CONTROL, 0, 0, 0)
    u.keybd_event(VK_Z, 0, 0, 0)
    u.keybd_event(VK_Z, 0, UP, 0)
    u.keybd_event(VK_CONTROL, 0, UP, 0)


def _median(v):
    v = sorted(v)
    return v[len(v) // 2] if v else 0


class Snapper:
    """Watches pen frames on their way to Windows and swaps a held stroke for its clean shape.

    feed(state, frames) -> frames to send now; tick() is called often (also without pen
    events) to notice the pen resting. Hooks: send(frame), undo(), app() -> exe name,
    allowed(name) -> bool, on_snap(info) for the tablet screen and the UI.
    """

    def __init__(self, mapper, send, hold_ms=600, undo=send_undo, app=foreground_app,
                 allowed=lambda name: app_allowed(name, DRAWING_APPS), on_snap=None,
                 clock=time.monotonic, sleep=time.sleep):
        self.m = mapper
        self.send = send
        self.hold_ms = hold_ms
        self.undo = undo
        self.app = app
        self.allowed = allowed
        self.on_snap = on_snap
        self.clock = clock
        self.sleep = sleep
        self.reset()
        self.last = None  # info about the last snap or skip, for the UI

    def reset(self):
        self.pts = []         # (t, x_mm, y_mm, raw_x, raw_y)
        self.frames = []      # contact frames of this stroke (pressure, tilt)
        self.tried = None     # where we last tried: one attempt per resting spot
        self.snapped = False  # stroke replaced: swallow the real pen until it lifts

    def mm(self, x, y):
        ax, ay = self.m.absinfo["X"], self.m.absinfo["Y"]
        return ((x - ax["min"]) / ((ax["max"] - ax["min"]) or 1) * PANEL_MM[0],
                (y - ay["min"]) / ((ay["max"] - ay["min"]) or 1) * PANEL_MM[1])

    def raw(self, x, y):
        ax, ay = self.m.absinfo["X"], self.m.absinfo["Y"]
        return (ax["min"] + x / PANEL_MM[0] * (ax["max"] - ax["min"]),
                ay["min"] + y / PANEL_MM[1] * (ay["max"] - ay["min"]))

    def feed(self, s, frames):
        out = []
        for f in frames:
            contact = "INCONTACT" in f.flags
            if self.snapped:
                # the shape is already drawn: keep the cursor with the pen, but as hover
                if "UP" in f.flags or contact:
                    out.append(Frame(flags={"INRANGE", "UPDATE"}, x=f.x, y=f.y))
                    if "UP" in f.flags:
                        self.reset()
                    continue
                if "INRANGE" not in f.flags:
                    self.reset()
                out.append(f)
                continue
            if contact and "DOWN" in f.flags:
                self.reset()
            if contact and not f.eraser:
                x, y = self.mm(s.x, s.y)
                if self.tried and _dist(self.tried, (x, y)) > 1.5:
                    self.tried = None  # moved on: the next rest may snap
                self.pts.append((self.clock(), x, y, s.x, s.y))
                self.frames.append(f)
            elif not contact:
                self.pts, self.frames = [], []
            out.append(f)
        return out

    def tick(self):
        if self.snapped or self.tried or len(self.pts) < 8:
            return
        now = self.clock()
        hold = self.hold_ms / 1000
        last = self.pts[-1]
        if now - self.pts[0][0] < hold + 0.15:
            return
        # resting: no sample in the last hold period strayed more than 1.2 mm from where it is now
        if any(_dist(p[1:3], last[1:3]) > 1.2 for p in self.pts if now - p[0] <= hold):
            return
        self.tried = last[1:3]
        # drop the resting tail, keep the drawn part
        k = len(self.pts)
        while k > 1 and _dist(self.pts[k - 1][1:3], last[1:3]) <= 1.2:
            k -= 1
        stroke = [(p[1], p[2]) for p in self.pts[:k]] + [(last[1], last[2])]
        shape = recognize(stroke)
        if not shape:
            self.last = {"ok": False, "text": "No shape recognised"}
            return
        name = self.app()
        if not self.allowed(name):
            self.last = {"ok": False, "kind": shape["kind"],
                         "text": f"Saw a {shape['kind']}, but {name or 'this app'} isn't on the drawing-apps list"}
            if self.on_snap:
                self.on_snap(self.last, None)
            return
        self.draw(shape, name)

    def draw(self, shape, app_name):
        fs = self.frames
        body = fs[len(fs) // 10: max(len(fs) // 10 + 1, len(fs) * 9 // 10)] or fs
        pressure = max(1, _median([f.pressure for f in body]))
        tx = _median([f.tilt_x for f in body])
        ty = _median([f.tilt_y for f in body])
        lx, ly = fs[-1].x, fs[-1].y
        # 1. lift the real stroke so the app commits it, 2. undo it, 3. draw the clean one
        self.send(Frame(flags={"INRANGE", "UP"}, x=lx, y=ly, button_change="up"))
        self.sleep(0.04)
        self.undo()
        self.sleep(0.06)
        outline = resample(shape["outline"], 1.0)
        raws = [self.raw(x, y) for x, y in outline]
        screen = []
        for rx, ry in raws:
            p = self.m.to_screen(rx, ry)
            if not screen or p != screen[-1]:
                screen.append(p)
        if len(screen) < 2:
            screen = screen * 2

        def frame(flags, xy, change=""):
            return Frame(flags=set(flags), x=xy[0], y=xy[1], pressure=pressure,
                         tilt_x=tx, tilt_y=ty, button_change=change)

        self.send(Frame(flags={"INRANGE", "UPDATE"}, x=screen[0][0], y=screen[0][1]))
        self.send(frame({"INRANGE", "INCONTACT", "DOWN"}, screen[0], "down"))
        for i, xy in enumerate(screen[1:]):
            self.send(frame({"INRANGE", "INCONTACT", "UPDATE"}, xy))
            if i % 4 == 3:
                self.sleep(0.002)  # let the app see a stroke, not one jump
        self.send(Frame(flags={"INRANGE", "UP"}, x=screen[-1][0], y=screen[-1][1], button_change="up"))
        self.send(Frame(flags={"INRANGE", "UPDATE"}, x=lx, y=ly))
        self.snapped = True
        self.last = {"ok": True, "kind": shape["kind"], "app": app_name, "pressure": pressure / 1024,
                     "text": f"Snapped to a {shape['kind']} in {app_name or 'the app'}"}
        if self.on_snap:
            self.on_snap(self.last, raws)
