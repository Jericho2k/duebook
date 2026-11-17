"""Small charts for the results document, as plain SVG.

A line chart is all the results need, and drawing one by hand keeps the
benchmark free of a plotting dependency. Colours are fixed per series and
the chart has a white background, so it reads the same in any viewer.
"""

from __future__ import annotations

from collections.abc import Sequence
from html import escape

PALETTE = ["#2563eb", "#dc2626", "#16a34a", "#9333ea", "#ea580c"]


def line_chart(
    series: Sequence[tuple[str, Sequence[tuple[float, float]]]],
    *,
    title: str,
    x_label: str,
    y_label: str,
    width: int = 720,
    height: int = 380,
) -> str:
    left, right, top, bottom = 70, 20, 40, 55
    xs = [x for _, pts in series for x, _ in pts]
    ys = [y for _, pts in series for _, y in pts]
    x0, x1 = min(xs), max(xs)
    y0, y1 = 0.0, _nice(max(ys))
    plot_w, plot_h = width - left - right, height - top - bottom

    def px(x: float) -> float:
        return left + (x - x0) / ((x1 - x0) or 1) * plot_w

    def py(y: float) -> float:
        return top + plot_h - (y - y0) / ((y1 - y0) or 1) * plot_h

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" font-family="sans-serif" font-size="12">',
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        f'<text x="{width / 2}" y="22" text-anchor="middle" font-size="14" '
        f'font-weight="bold">{escape(title)}</text>',
    ]
    for i in range(6):
        y = y0 + (y1 - y0) * i / 5
        out.append(f'<line x1="{left}" x2="{width - right}" y1="{py(y):.1f}" y2="{py(y):.1f}" '
                   f'stroke="#e5e7eb"/>')  # fmt: skip
        out.append(f'<text x="{left - 6}" y="{py(y) + 4:.1f}" text-anchor="end" '
                   f'fill="#374151">{y:,.0f}</text>')  # fmt: skip
    for i in range(6):
        x = x0 + (x1 - x0) * i / 5
        out.append(f'<text x="{px(x):.1f}" y="{height - bottom + 18}" text-anchor="middle" '
                   f'fill="#374151">{x:,.0f}</text>')  # fmt: skip
    out.append(f'<text x="{left + plot_w / 2}" y="{height - 12}" text-anchor="middle" '
               f'fill="#111827">{escape(x_label)}</text>')  # fmt: skip
    mid = top + plot_h / 2
    out.append(f'<text x="16" y="{mid}" text-anchor="middle" fill="#111827" '
               f'transform="rotate(-90 16 {mid})">{escape(y_label)}</text>')  # fmt: skip
    for n, (name, pts) in enumerate(series):
        colour = PALETTE[n % len(PALETTE)]
        path = " ".join(f"{px(x):.1f},{py(y):.1f}" for x, y in pts)
        out.append(f'<polyline fill="none" stroke="{colour}" stroke-width="2" points="{path}"/>')
        ly = top + 8 + n * 18
        out.append(f'<line x1="{left + 12}" x2="{left + 36}" y1="{ly}" y2="{ly}" '
                   f'stroke="{colour}" stroke-width="3"/>')  # fmt: skip
        out.append(f'<text x="{left + 42}" y="{ly + 4}" fill="#111827">{escape(name)}</text>')
    out.append("</svg>")
    return "\n".join(out)


def _nice(value: float) -> float:
    """A round upper bound for an axis."""
    if value <= 0:
        return 1.0
    magnitude = 10 ** (len(str(int(value))) - 1)
    for step in (1, 2, 2.5, 5, 10):
        if value <= step * magnitude:
            return step * magnitude
    return 10 * magnitude
