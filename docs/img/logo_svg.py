"""Логотип ОТК — штамп «ТЕХНИЧЕСКИЙ КОНТРОЛЬ · КОД 1С · ПРИНЯТ» — в SVG без шрифтов: буквы — контуры.

  python logo_svg.py ШРИФТ.ttf logo.svg [--flat]

ШРИФТ — Roboto Bold (Apache-2.0). Без --flat — оттиск с зернистостью чернил (фильтр SVG), с --flat — ровная
заливка для мелких размеров (значок вкладки). Нужен fontTools (`pip install fonttools`).
"""
import argparse
import math

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

RED = "#c62828"
C = 120  # центр, viewBox 240×240
TOP = ("ТЕХНИЧЕСКИЙ КОНТРОЛЬ", 14.5, 2.0, 76)  # текст, кегль, разрядка, радиус базовой линии
BOTTOM = ("КОД 1С · ПРИНЯТ", 15.0, 3.0, 92)
INK = """  <defs>
    <filter id="ink" x="-5%" y="-5%" width="110%" height="110%">
      <feTurbulence type="fractalNoise" baseFrequency="0.9" numOctaves="2" seed="7" result="noise"/>
      <feColorMatrix in="noise" type="matrix"
        values="0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 -1.4 1.75" result="holes"/>
      <feComposite in="SourceGraphic" in2="holes" operator="in" result="grain"/>
      <feTurbulence type="fractalNoise" baseFrequency="0.035" numOctaves="2" seed="3" result="warp"/>
      <feDisplacementMap in="grain" in2="warp" scale="2.5"/>
    </filter>
  </defs>
"""


class Font:
    def __init__(self, path):
        self.f = TTFont(path)
        self.cmap = self.f.getBestCmap()
        self.gs = self.f.getGlyphSet()
        self.upm = self.f["head"].unitsPerEm

    def advance(self, ch, size):
        return self.f["hmtx"][self.cmap[ord(ch)]][0] * size / self.upm

    def path(self, ch, size, x, y):
        """Контур буквы: начало базовой линии в (x, y) системы SVG (ось y вниз)."""
        k = size / self.upm
        pen = SVGPathPen(self.gs, ntos=lambda v: f"{v:.2f}".rstrip("0").rstrip("."))
        self.gs[self.cmap[ord(ch)]].draw(TransformPen(pen, (k, 0, 0, -k, x, y)))
        return pen.getCommands()

    def width(self, text, size, spacing):
        return sum(self.advance(ch, size) for ch in text) + spacing * (len(text) - 1)


def arc_text(font, text, size, spacing, radius, top):
    """Буквы по дуге с центром вверху (top) или внизу; вверху читаются по часовой, внизу — против."""
    out, s = [], -font.width(text, size, spacing) / 2
    for ch in text:
        a = font.advance(ch, size)
        if ch != " ":
            phi = (s + a / 2) / radius
            theta = -math.pi / 2 + phi if top else math.pi / 2 - phi
            x, y = C + radius * math.cos(theta), C + radius * math.sin(theta)
            turn = math.degrees(theta) + (90 if top else -90)
            d = font.path(ch, size, -a / 2, 0)
            out.append(f'<path transform="translate({x:.2f} {y:.2f}) rotate({turn:.2f})" d="{d}"/>')
        s += a + spacing
    return out


def star(cx, cy, r):
    pts = []
    for i in range(10):
        rr = r if i % 2 == 0 else r * 0.4
        t = -math.pi / 2 + i * math.pi / 5
        pts.append(f"{cx + rr * math.cos(t):.2f},{cy + rr * math.sin(t):.2f}")
    return f'<polygon points="{" ".join(pts)}"/>'


def build(font_path, flat):
    font = Font(font_path)
    size = 44
    w = font.width("ОТК", size, 1)
    x, glyphs = C - w / 2, []
    for ch in "ОТК":
        glyphs.append(f'<path d="{font.path(ch, size, x, 133)}"/>')
        x += font.advance(ch, size) + 1
    body = [
        f'<circle cx="{C}" cy="{C}" r="112" fill="none" stroke-width="7"/>',
        f'<circle cx="{C}" cy="{C}" r="100" fill="none" stroke-width="2.5"/>',
        f'<circle cx="{C}" cy="{C}" r="62" fill="none" stroke-width="2.5"/>',
        '<g stroke="none">',
        *arc_text(font, *TOP, top=True),
        *arc_text(font, *BOTTOM, top=False),
        star(24, 120, 7.5), star(216, 120, 7.5),
        *glyphs,
        "</g>",
        '<path d="M 103 146 l 11 10 l 22 -22" fill="none" stroke-width="6" stroke-linecap="round" '
        'stroke-linejoin="round"/>',
    ]
    ink = "" if flat else ' filter="url(#ink)"'
    lines = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 240" width="240" height="240">',
             "  <title>ОТК</title>", *([] if flat else [INK.rstrip("\n")]),
             f'  <g{ink} fill="{RED}" stroke="{RED}" transform="rotate(-12 {C} {C})">',
             *("    " + b for b in body), "  </g>", "</svg>", ""]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("font")
    ap.add_argument("out")
    ap.add_argument("--flat", action="store_true", help="без зернистости чернил")
    a = ap.parse_args()
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        f.write(build(a.font, a.flat))


if __name__ == "__main__":
    main()
