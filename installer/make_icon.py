"""Draws the app icons into pc/icons/: paper-tablet.ico, the title-bar logo (pc/web/index.html,
.tb-logo) in champagne on a navy rounded square, and paper-tablet-off.ico, the tray icon while
pen input is off (grey, no ink, struck through). Needs Pillow; only run when the logo changes."""
import os
from PIL import Image, ImageDraw

NAVY, CHAMPAGNE, GREY = (6, 14, 23, 255), (196, 168, 130, 255), (128, 136, 146, 255)
BIG = 1024  # draw once large, then scale down for each icon size
SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pc", "icons")


def bezier(p0, p1, p2, p3, n=64):
    return [tuple((1 - t) ** 3 * a + 3 * (1 - t) ** 2 * t * b + 3 * (1 - t) * t ** 2 * c + t ** 3 * d
                  for a, b, c, d in zip(p0, p1, p2, p3))
            for t in (i / n for i in range(n + 1))]


def draw(on=True):
    ink = CHAMPAGNE if on else GREY
    img = Image.new("RGBA", (BIG, BIG), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, BIG - 1, BIG - 1), radius=BIG * 0.2, fill=NAVY)
    s = BIG / 24 * 0.92  # the logo's 24-unit viewBox, with a little margin
    o = (BIG - 24 * s) / 2
    w = round(1.9 * s)  # a bit heavier than the title bar's 1.4 so it reads at 16 px

    def pt(x, y):
        return (o + x * s, o + y * s)

    def stroke(points):
        # Stamped as dense round dots: a smooth stroke with round caps (Pillow's wide lines are jagged)
        for cx, cy in (pt(*p) for p in points):
            d.ellipse((cx - w / 2, cy - w / 2, cx + w / 2, cy + w / 2), fill=ink)

    d.rounded_rectangle((*pt(2.5, 5), *pt(21.5, 19)), radius=2.2 * s, outline=ink, width=w)
    if on:
        # M6.5 14.5 c2.2-3.6 3.6-3.6 4.8-1.2 s2.6 2.4 5.2-2.6
        stroke(bezier((6.5, 14.5), (8.7, 10.9), (10.1, 10.9), (11.3, 13.3), 400) +
               bezier((11.3, 13.3), (12.5, 15.7), (13.9, 15.7), (16.5, 10.7), 400))
    else:
        stroke([(4 + 16 * t / 400, 20.5 - 17 * t / 400) for t in range(401)])
    return img


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    for name, on in (("paper-tablet.ico", True), ("paper-tablet-off.ico", False)):
        path = os.path.join(OUT, name)
        draw(on).save(path, sizes=[(n, n) for n in SIZES])
        print("wrote", path)
