#!/usr/bin/env python3
"""Compose the detailed A/B/C/D wall chart (screen / zoom) from the rendered SVGs."""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SVG_DIR = ROOT / "docs" / "architecture" / "svg"
PNG_DIR = ROOT / "docs" / "architecture" / "png"

CANVAS_W = 2245
CANVAS_H = 1587
MARGIN = 60

TITLE = "FIxbot"
SUBTITLE = "Infrastructure at a glance \u2014 a per-Course AI study assistant on Kubernetes"

PANELS = [
    ("01-system-overview", 60, 175, 2125, 790),
    # bottom cells are laid out programmatically
    ("02-course-stack", 60, 995, None, 500),
    ("03-content-pipeline", None, 995, None, 500),
    ("04-data-observability", None, 995, None, 500),
]

GAP = 24

LEGEND = [
    ("solid", "request / data flow"),
    ("dashed", "optional or backup path"),
    ("dotted", "reconciliation / control"),
    ("cylinder", "database or persistent storage"),
]


def parse_svg(path):
    text = path.read_text()
    m = re.search(r"<svg[^>]*viewBox=\"([^\"]+)\"[^>]*>", text, re.S)
    if not m:
        raise SystemExit(f"no viewBox in {path}")
    view_box = m.group(1)
    inner = text[m.end():]
    inner = inner.rsplit("</svg>", 1)[0]
    parts = [float(x) for x in view_box.split()]
    if len(parts) != 4:
        raise SystemExit(f"bad viewBox in {path}: {view_box}")
    return view_box, parts[2], parts[3], inner


def fit(vbw, vbh, cell_x, cell_y, cell_w, cell_h):
    scale = min(cell_w / vbw, cell_h / vbh)
    w = vbw * scale
    h = vbh * scale
    x = cell_x + (cell_w - w) / 2
    y = cell_y + (cell_h - h) / 2
    return x, y, w, h


def panel(svg_name, cell_x, cell_y, cell_w, cell_h, index):
    view_box, vbw, vbh, inner = parse_svg(SVG_DIR / f"{svg_name}.svg")
    x, y, w, h = fit(vbw, vbh, cell_x, cell_y, cell_w, cell_h)
    return f"""
  <g id="panel-{index}">
    <rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="10"
          fill="#FFFFFF" stroke="#D8DEE6" stroke-width="1.5"/>
    <circle cx="{x + 18:.1f}" cy="{y + 18:.1f}" r="9" fill="#1F2933"/>
    <text x="{x + 18:.1f}" y="{y + 22:.1f}" text-anchor="middle"
          font-family="Noto Sans" font-size="10" font-weight="700" fill="#FFFFFF">{index}</text>
    <svg x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}"
         viewBox="{view_box}" preserveAspectRatio="xMidYMid meet">
{inner}
    </svg>
  </g>"""


def legend_markup():
    parts = []
    y = 1528
    x = MARGIN
    for kind, label in LEGEND:
        if kind == "solid":
            parts.append(f'<line x1="{x}" y1="{y}" x2="{x + 34}" y2="{y}" stroke="#5B6770" stroke-width="2.2"/>')
        elif kind == "dashed":
            parts.append(f'<line x1="{x}" y1="{y}" x2="{x + 34}" y2="{y}" stroke="#5B6770" stroke-width="2.2" stroke-dasharray="6 4"/>')
        elif kind == "dotted":
            parts.append(f'<line x1="{x}" y1="{y}" x2="{x + 34}" y2="{y}" stroke="#5B6770" stroke-width="2.2" stroke-dasharray="2 4"/>')
        else:
            parts.append(
                f'<path d="M {x} {y - 5} L {x} {y + 4} '
                f'A 17 6 0 0 0 {x + 34} {y + 4} L {x + 34} {y - 5} Z" '
                f'fill="#F3EAFB" stroke="#7B4FA6" stroke-width="1.8"/>'
                f'<ellipse cx="{x + 17}" cy="{y - 5}" rx="17" ry="6" '
                f'fill="#F3EAFB" stroke="#7B4FA6" stroke-width="1.8"/>'
            )
        parts.append(
            f'<text x="{x + 44}" y="{y + 5}" font-family="Noto Sans" '
            f'font-size="17" fill="#5B6770">{label}</text>'
        )
        x += 44 + len(label) * 9.4 + 46
    return "\n".join(parts)


def main():
    # Resolve bottom-row cells from the source aspect ratios.
    _, vbw2, vbh2, _ = parse_svg(SVG_DIR / "02-course-stack.svg")
    _, vbw3, vbh3, _ = parse_svg(SVG_DIR / "03-content-pipeline.svg")
    _, vbw4, vbh4, _ = parse_svg(SVG_DIR / "04-data-observability.svg")

    h = 500
    avail = CANVAS_W - 2 * MARGIN - 2 * GAP
    w2 = (vbw2 / vbh2) * h
    w3 = (vbw3 / vbh3) * h
    w4 = avail - w2 - w3
    if w4 < 300:
        scale = avail / (w2 + w3 + 300)
        w2, w3, w4 = w2 * scale, w3 * scale, 300
        h = h * scale

    x2 = MARGIN
    x3 = x2 + w2 + GAP
    x4 = x3 + w3 + GAP

    panels = [
        panel("01-system-overview", 60, 175, 2125, 790, "A"),
        panel("02-course-stack", x2, 995, w2, h, "B"),
        panel("03-content-pipeline", x3, 995, w3, h, "C"),
        panel("04-data-observability", x4, 995, w4, h, "D"),
    ]

    poster = f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="594mm" height="420mm"
     viewBox="0 0 {CANVAS_W} {CANVAS_H}">
  <rect x="0" y="0" width="{CANVAS_W}" height="{CANVAS_H}" fill="#FCFCFD"/>

  <text x="{MARGIN}" y="112" font-family="Noto Sans" font-size="64"
        font-weight="700" fill="#1F2933">{TITLE}</text>
  <text x="{MARGIN}" y="152" font-family="Noto Sans" font-size="27"
        fill="#4A5560">{SUBTITLE}</text>
  <line x1="{MARGIN}" y1="168" x2="{CANVAS_W - MARGIN}" y2="168"
        stroke="#D8DEE6" stroke-width="2"/>
{"".join(panels)}

  <line x1="{MARGIN}" y1="1500" x2="{CANVAS_W - MARGIN}" y2="1500"
        stroke="#D8DEE6" stroke-width="1"/>
  <text x="{MARGIN}" y="1518" font-family="Noto Sans" font-size="13"
        font-weight="700" fill="#7B8794">LEGEND</text>
  {legend_markup()}
  <text x="{CANVAS_W - MARGIN}" y="1560" text-anchor="end"
        font-family="Noto Sans" font-size="14" fill="#7B8794">
    Generic logical view, not a live snapshot \u00b7 regenerate: ./scripts/render-architecture.sh
  </text>
</svg>
"""
    out = SVG_DIR / "poster-detail.svg"
    out.write_text(poster)
    print(f"wrote {out.relative_to(ROOT)}")

    try:
        import cairosvg
    except ImportError:
        print("warning: cairosvg not installed; poster PNG not rendered", file=sys.stderr)
        print("hint: apk add --no-cache py3-cairosvg", file=sys.stderr)
        return
    png = PNG_DIR / "poster-detail.png"
    cairosvg.svg2png(
        bytestring=poster.encode(),
        write_to=str(png),
        output_width=4678,
        output_height=3307,
    )
    print(f"wrote {png.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
