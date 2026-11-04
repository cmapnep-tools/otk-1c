"""Анимированный SVG терминала из настоящего прогона (демонстрация для README).

  python terminal_svg.py record сеанс.json "otk gate --base master" -- python -m otk_1c gate --base master
  python terminal_svg.py render демо.svg --title "..." --sub "D:\\repo\\=" сеанс1.json [сеанс2.json ...]

record запускает команду в текущем каталоге и пишет каждую строку вывода с отметкой времени; показываемая
команда — второй аргумент. render печатает сеансы друг за другом: команда набирается, строки появляются
в записанном порядке, паузы дольше --max-gap сокращаются (реальное время сеансов — в заголовке окна).
--sub СТАРОЕ=НОВОЕ убирает пути машины. Только стандартная библиотека.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from xml.sax.saxutils import escape

FONT = "Consolas, 'Cascadia Mono', 'DejaVu Sans Mono', 'Liberation Mono', monospace"
SIZE, LINE, CW, PAD, BAR = 13, 18, 7.4, 14, 30
COLORS = {"fg": "#d4d4d4", "dim": "#8b949e", "path": "#79c0ff", "ok": "#3fb950", "err": "#f85149",
          "warn": "#d29922", "bg": "#0d1117", "bar": "#161b22"}
CSS_CLASS = {"fg": "f", "dim": "d", "path": "p", "ok": "o", "err": "e", "warn": "w"}


def record(out, shown, cmd):
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    t0, lines = time.time(), []
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
    for raw in p.stdout:
        s = raw.decode("utf-8", "replace").rstrip("\r\n")
        lines.append([round(time.time() - t0, 2), s])
        print(s, flush=True)
    rc = p.wait()
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"shown": shown, "rc": rc, "total": round(time.time() - t0, 1), "lines": lines},
                  f, ensure_ascii=False, indent=0)
    return rc


def segments(line):
    """Строка вывода -> [(текст, цвет)]."""
    if line.startswith("# ОТК:"):
        return [(line, "err" if "ошибки" in line else "ok")]
    m = re.match(r"(.+?\(\d+,\d+\)): (error|warning) (\S+): (.*)$", line)
    if m:
        level = "err" if m.group(2) == "error" else "warn"
        return [(m.group(1) + ": ", "path"), (m.group(2) + " " + m.group(3), level), (": " + m.group(4), "fg")]
    if line.startswith(("сводка:", "  ")):
        return [(line, "dim")]
    return [(line, "fg")]


def wrap(segs, cols):
    """[(текст, цвет)] -> строки экрана, каждая [(текст, цвет)]; перенос по пробелам."""
    chars = [(ch, color) for text, color in segs for ch in text]
    rows = []
    while len(chars) > cols:
        cut = max((i + 1 for i in range(cols) if chars[i][0] == " "), default=cols)
        rows.append(chars[:cut])
        chars = chars[cut:]
    rows.append(chars)
    out = []
    for row in rows:
        merged = []
        for ch, color in row:
            if merged and merged[-1][1] == color:
                merged[-1] = (merged[-1][0] + ch, color)
            else:
                merged.append((ch, color))
        out.append(merged)
    return out


def render(out, sessions, title, subs, cols, max_gap, hold):
    events = []  # (момент, вид, строки экрана)
    t = 0.6
    for n, s in enumerate(sessions):
        if n:
            events.append((t, "out", ""))
        cmd = s["shown"]
        events.append((t, "cmd", cmd))
        t += 0.05 * len(cmd) + 0.5
        prev = 0.0
        for at, text in s["lines"]:
            for old, new in subs:
                text = text.replace(old, new)
            t += min(at - prev, max_gap)
            prev = at
            events.append((t, "out", text))
        t += 1.2
    total = t + hold
    rows = []  # (момент появления, [(текст, цвет)], длительность набора или 0)
    for at, kind, text in events:
        if kind == "cmd":
            rows.append((at, [("$ ", "ok"), (text, "fg")], 0.05 * len(text)))
        else:
            rows.extend((at, r, 0) for r in wrap(segments(text), cols))
    width = int(cols * CW + 2 * PAD)
    height = BAR + PAD + len(rows) * LINE + PAD

    def pct(x):
        return f"{100 * x / total:.3f}%"

    css = [f"text{{font-family:{FONT};font-size:{SIZE}px;white-space:pre}}"]
    css += [f".{CSS_CLASS[k]}{{fill:{COLORS[k]}}}" for k in CSS_CLASS]
    css.append(".b{font-weight:bold}")
    body = []
    for i, (at, row, typing) in enumerate(rows):
        y = BAR + PAD + (i + 1) * LINE - 5
        spans = "".join(f'<tspan class="{CSS_CLASS[c]}{" b" if c in ("err", "ok") and len(row) == 1 else ""}">'
                        f"{escape(txt)}</tspan>" for txt, c in row)
        body.append(f'<text x="{PAD}" y="{y}" class="r{i}" xml:space="preserve">{spans}</text>')
        if typing:
            # набор команды: шторка цвета фона уходит вправо по символу
            chars = sum(len(txt) for txt, _ in row) - 2
            css.append(f"@keyframes t{i}{{0%,{pct(at)}{{transform:translateX(0);animation-timing-function:"
                       f"steps({chars},end)}}{pct(at + typing)}{{transform:translateX({chars * CW:.1f}px)}}"
                       f"{pct(at + typing + 0.01)},100%{{transform:translateX({width}px)}}}}"
                       f".k{i}{{animation:t{i} {total:.2f}s infinite}}")
            body.append(f'<rect class="k{i}" x="{PAD + 2 * CW:.1f}" y="{y - LINE + 5}" width="{width}" '
                        f'height="{LINE}" fill="{COLORS["bg"]}"/>')
        css.append(f"@keyframes a{i}{{0%,{pct(at)}{{opacity:0}}{pct(at + 0.01)},100%{{opacity:1}}}}"
                   f".r{i}{{opacity:0;animation:a{i} {total:.2f}s step-end infinite}}")
    dots = "".join(f'<circle cx="{18 + 18 * k}" cy="{BAR / 2}" r="5.5" fill="{c}"/>'
                   for k, c in enumerate(("#ff5f56", "#ffbd2e", "#27c93f")))
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
           f'viewBox="0 0 {width} {height}" role="img" aria-label="{escape(title)}">'
           f"<title>{escape(title)}</title><style>{''.join(css)}</style>"
           f'<rect width="{width}" height="{height}" rx="8" fill="{COLORS["bg"]}"/>'
           f'<path d="M0 8a8 8 0 0 1 8-8h{width - 16}a8 8 0 0 1 8 8v{BAR - 8}h-{width}z" fill="{COLORS["bar"]}"/>'
           f'{dots}<text x="{width / 2}" y="{BAR / 2 + 4}" text-anchor="middle" class="d">{escape(title)}</text>'
           f"{''.join(body)}</svg>")
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(svg + "\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="mode", required=True)
    r = sub.add_parser("record")
    r.add_argument("out")
    r.add_argument("shown")
    r.add_argument("cmd", nargs=argparse.REMAINDER)
    v = sub.add_parser("render")
    v.add_argument("out")
    v.add_argument("sessions", nargs="+")
    v.add_argument("--title", default="")
    v.add_argument("--sub", action="append", default=[], help="СТАРОЕ=НОВОЕ")
    v.add_argument("--cols", type=int, default=118)
    v.add_argument("--max-gap", type=float, default=1.5)
    v.add_argument("--hold", type=float, default=6.0)
    a = ap.parse_args()
    if a.mode == "record":
        cmd = a.cmd[1:] if a.cmd[:1] == ["--"] else a.cmd
        return record(a.out, a.shown, cmd)
    sessions = [json.load(open(p, encoding="utf-8")) for p in a.sessions]
    subs = [tuple(s.split("=", 1)) for s in a.sub]
    render(a.out, sessions, a.title, subs, a.cols, a.max_gap, a.hold)
    return 0


if __name__ == "__main__":
    sys.exit(main())
