"""Dependency-free SVG bar charts for the release statistics."""

from __future__ import annotations

import math
from html import escape


WIDTH = 860
LABEL_WIDTH = 300
BAR_LEFT = LABEL_WIDTH + 10
BAR_WIDTH = WIDTH - BAR_LEFT - 150
ROW = 22
STYLE = """<style>
  text { font: 13px -apple-system, "Segoe UI", Helvetica, Arial, sans-serif; fill: #24292f; }
  .title { font-size: 15px; font-weight: 600; }
  .note { font-size: 11px; fill: #57606a; }
  .bar { fill: #3b82f6; }
  .slow { fill: #ef4444; }
  .fast { fill: #22c55e; }
  .missing { fill: #d0d7de; }
  .track { fill: #eaeef2; }
  .rule { stroke: #57606a; stroke-width: 1; stroke-dasharray: 4 3; }
  @media (prefers-color-scheme: dark) {
    text { fill: #e6edf3; }
    .note { fill: #8d96a0; }
    .track { fill: #30363d; }
    .missing { fill: #484f58; }
    .rule { stroke: #8d96a0; }
  }
</style>"""


def _document(height: int, body: list[str], title: str, note: str) -> str:
    head = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}" '
            f'viewBox="0 0 {WIDTH} {height}" role="img" aria-label="{escape(title)}">',
            STYLE,
            f'<text class="title" x="10" y="22">{escape(title)}</text>',
            f'<text class="note" x="10" y="{height - 8}">{escape(note)}</text>']
    return "\n".join(head + body + ["</svg>"]) + "\n"


def share_chart(title: str, rows: list[tuple[str, int, int]], note: str) -> str:
    """Horizontal bars of passed / total, 0-100 %."""
    body = []
    y = 40
    for label, passed, total in rows:
        share = passed / total if total else 0.0
        body.append(f'<text x="10" y="{y + 15}">{escape(label)}</text>')
        body.append(f'<rect class="track" x="{BAR_LEFT}" y="{y + 3}" width="{BAR_WIDTH}" height="16"/>')
        body.append(f'<rect class="bar" x="{BAR_LEFT}" y="{y + 3}" '
                    f'width="{max(1.0, BAR_WIDTH * share):.1f}" height="16"/>')
        percent = f"{100 * share:.2f}" if share < 0.1 else f"{100 * share:.1f}"
        body.append(f'<text x="{BAR_LEFT + BAR_WIDTH + 8}" y="{y + 15}">'
                    f'{passed:,} / {total:,} ({percent}%)</text>')
        y += ROW + 6
    return _document(y + 30, body, title, note)


def speedup_chart(title: str, rows: list[tuple[str, float | None, str]], note: str,
                  geometric_mean: float | None) -> str:
    """Speedup bars on a log scale around CPython (1.0); None marks a
    benchmark that did not run, with its status as the label text."""
    measured = [value for _label, value, _status in rows if value]
    low = min([0.1] + measured) / 1.5
    high = max([2.0] + measured) * 1.5
    span = math.log10(high) - math.log10(low)

    def x(value: float) -> float:
        return BAR_LEFT + BAR_WIDTH * (math.log10(value) - math.log10(low)) / span

    body = []
    y = 40
    one = x(1.0)
    for label, value, status in rows:
        body.append(f'<text x="10" y="{y + 13}">{escape(label)}</text>')
        if value:
            left, right = sorted((one, x(value)))
            kind = "fast" if value >= 1.0 else "slow"
            body.append(f'<rect class="{kind}" x="{left:.1f}" y="{y + 2}" '
                        f'width="{max(1.0, right - left):.1f}" height="14"/>')
            body.append(f'<text x="{BAR_LEFT + BAR_WIDTH + 8}" y="{y + 13}">{value:.2f}x</text>')
        else:
            body.append(f'<rect class="missing" x="{BAR_LEFT}" y="{y + 2}" '
                        f'width="{BAR_WIDTH}" height="14"/>')
            body.append(f'<text x="{BAR_LEFT + 6}" y="{y + 13}">{escape(status)}</text>')
        y += ROW - 2
    top, bottom = 34, y + 4
    body.append(f'<line class="rule" x1="{one:.1f}" y1="{top}" x2="{one:.1f}" y2="{bottom}"/>')
    body.append(f'<text class="note" x="{one + 4:.1f}" y="{bottom + 14}">CPython = 1.0x</text>')
    if geometric_mean:
        mean = x(geometric_mean)
        body.append(f'<line class="rule" x1="{mean:.1f}" y1="{top}" x2="{mean:.1f}" '
                    f'y2="{bottom}" style="stroke-dasharray: 1 3"/>')
        body.append(f'<text class="note" x="{mean + 4:.1f}" y="{bottom + 28}">'
                    f'geometric mean {geometric_mean:.2f}x</text>')
    return _document(bottom + 52, body, title, note)


def count_chart(title: str, rows: list[tuple[str, int]], total: int, note: str) -> str:
    """Horizontal bars of counts out of ``total``."""
    body = []
    y = 40
    for label, count in rows:
        share = count / total if total else 0.0
        body.append(f'<text x="10" y="{y + 15}">{escape(label)}</text>')
        body.append(f'<rect class="track" x="{BAR_LEFT}" y="{y + 3}" width="{BAR_WIDTH}" height="16"/>')
        body.append(f'<rect class="bar" x="{BAR_LEFT}" y="{y + 3}" '
                    f'width="{max(1.0, BAR_WIDTH * share):.1f}" height="16"/>')
        body.append(f'<text x="{BAR_LEFT + BAR_WIDTH + 8}" y="{y + 15}">{count} of {total}</text>')
        y += ROW + 6
    return _document(y + 30, body, title, note)
