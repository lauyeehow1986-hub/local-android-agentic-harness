"""Generation tools: make_slides / make_html_report / rephrase.

make_* are GUARDED only because they write a file — harmless, approve once.
rephrase is SAFE and may call the local model internally.
"""

from __future__ import annotations

import html
from pathlib import Path
from typing import Any

from . import Tool, ToolContext


def _write(out_path: str, content: str) -> str:
    p = Path(out_path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return str(p)


def make_slides(args: dict[str, Any], ctx: ToolContext) -> str:
    title = str(args.get("title", "Untitled")).strip()
    outline = args.get("outline") or []
    out_path = str(args.get("out_path", "slides.md")).strip()
    if not isinstance(outline, list):
        return "error: 'outline' must be a list of strings"
    # Marp/Obsidian-friendly Markdown slides: '---' separates slides.
    parts = [f"---\nmarp: true\n---\n\n# {title}\n"]
    for item in outline:
        parts.append(f"\n---\n\n## {str(item)}\n")
    written = _write(out_path, "\n".join(parts))
    return f"wrote slide deck: {written} ({len(outline)} slides)"


def _svg_chart(chart: dict) -> str:
    """Render a bar or line chart as inline SVG — pure Python, no matplotlib.

    chart: {"type":"bar"|"line", "labels":[...], "values":[...], "title":str}
    or, to read REAL data instead of model-supplied numbers:
           {"type":..., "csv":path, "y":col, "x":col?, "title":str}
    """
    ctype = str(chart.get("type", "bar")).lower()
    title = str(chart.get("title", ""))
    labels = chart.get("labels")
    values = chart.get("values")

    # CSV-driven: compute from the file so numbers are real, not hallucinated.
    if chart.get("csv"):
        labels, values, err = _chart_data_from_csv(chart)
        if err:
            return f"<p><em>(chart unavailable: {html.escape(err)})</em></p>"

    try:
        values = [float(v) for v in (values or [])]
    except (TypeError, ValueError):
        return "<p><em>(chart unavailable: non-numeric values)</em></p>"
    if not values:
        return "<p><em>(chart unavailable: no data)</em></p>"
    labels = [str(x) for x in (labels or list(range(1, len(values) + 1)))]
    labels = (labels + [""] * len(values))[: len(values)]

    w, h, pad = 640, 300, 44
    vmax = max(values + [0.0])
    vmin = min(values + [0.0])
    span = (vmax - vmin) or 1.0
    parts = [
        f'<svg viewBox="0 0 {w} {h}" width="100%" style="max-width:{w}px;font-family:system-ui">',
        f'<text x="{w/2}" y="20" font-size="14" text-anchor="middle" font-weight="bold">{html.escape(title)}</text>' if title else "",
        f'<line x1="{pad}" y1="{h-pad}" x2="{w-pad}" y2="{h-pad}" stroke="#bbb"/>',
        f'<line x1="{pad}" y1="{pad}" x2="{pad}" y2="{h-pad}" stroke="#bbb"/>',
    ]
    n = len(values)
    plot_w = w - 2 * pad
    plot_h = h - 2 * pad

    def y_of(v):
        return h - pad - (v - vmin) / span * plot_h

    if ctype == "line":
        step = plot_w / max(1, n - 1)
        pts = " ".join(f"{pad + i*step:.1f},{y_of(v):.1f}" for i, v in enumerate(values))
        parts.append(f'<polyline fill="none" stroke="#3b82f6" stroke-width="2" points="{pts}"/>')
        # sparse x labels
        for i in range(0, n, max(1, n // 8)):
            x = pad + i * step
            parts.append(f'<text x="{x:.1f}" y="{h-pad+14}" font-size="9" text-anchor="middle">{html.escape(labels[i])[:8]}</text>')
    else:  # bar
        bw = plot_w / max(1, n)
        for i, v in enumerate(values):
            x = pad + i * bw
            y = y_of(v)
            bh = (h - pad) - y
            parts.append(f'<rect x="{x+bw*0.1:.1f}" y="{y:.1f}" width="{bw*0.8:.1f}" height="{max(0,bh):.1f}" fill="#3b82f6"/>')
            if n <= 25:
                parts.append(f'<text x="{x+bw*0.5:.1f}" y="{h-pad+14}" font-size="9" text-anchor="middle">{html.escape(labels[i])[:8]}</text>')
                parts.append(f'<text x="{x+bw*0.5:.1f}" y="{y-3:.1f}" font-size="8" text-anchor="middle">{v:.3g}</text>')
    parts.append(f'<text x="{pad-4}" y="{pad}" font-size="9" text-anchor="end">{vmax:.3g}</text>')
    parts.append(f'<text x="{pad-4}" y="{h-pad}" font-size="9" text-anchor="end">{vmin:.3g}</text>')
    parts.append("</svg>")
    return "".join(parts)


def _chart_data_from_csv(chart: dict):
    """Return (labels, values, error). Reads a numeric column from a CSV so the
    chart reflects real data. Caps at 60 points."""
    import csv as _csv

    path = Path(str(chart.get("csv", ""))).expanduser()
    if not path.exists():
        return None, None, f"csv not found: {path}"
    ycol = chart.get("y")
    xcol = chart.get("x")
    try:
        with path.open(newline="", encoding="utf-8", errors="ignore") as f:
            rows = list(_csv.reader(f))
    except OSError as e:
        return None, None, str(e)
    if len(rows) < 2:
        return None, None, "empty csv"
    header = rows[0]

    def idx(col):
        if col is None:
            return None
        if isinstance(col, int):
            return col
        return header.index(col) if col in header else None

    yi = idx(ycol)
    if yi is None:
        return None, None, f"column not found: {ycol}"
    xi = idx(xcol)
    values, labels = [], []
    for r in rows[1:]:
        if yi >= len(r):
            continue
        try:
            values.append(float(r[yi]))
        except ValueError:
            continue
        labels.append(r[xi] if (xi is not None and xi < len(r)) else "")
        if len(values) >= 60:
            break
    if not values:
        return None, None, f"no numeric data in column {ycol}"
    return labels, values, None


def _table_html(table) -> str:
    rows = table if isinstance(table, list) else [table]
    out = ["<table style='border-collapse:collapse' border='1' cellpadding='5'>"]
    for r in rows:
        cells = r if isinstance(r, (list, tuple)) else [r]
        out.append("<tr>" + "".join(f"<td>{html.escape(str(c))}</td>" for c in cells) + "</tr>")
    out.append("</table>")
    return "".join(out)


def make_html_report(args: dict[str, Any], ctx: ToolContext) -> str:
    """Build an HTML report. Each section may carry text, a table, and/or a chart.

    sections: list of either a string (a paragraph) or a dict:
      {"heading": str, "body": str,
       "table": [[...rows...]],
       "chart": {"type":"bar"|"line","labels":[...],"values":[...],"title":str}
               OR {"type":...,"csv":path,"y":col,"x":col?,"title":str}}
    Charts render as inline SVG (no dependencies). Use the csv form to plot real
    data instead of typing numbers.
    """
    title = str(args.get("title", "Report")).strip()
    sections = args.get("sections") or []
    out_path = str(args.get("out_path", "report.html")).strip()
    if not isinstance(sections, list):
        return "error: 'sections' must be a list"
    n_charts = 0
    body_parts: list[str] = [f"<h1>{html.escape(title)}</h1>"]
    for sec in sections:
        if isinstance(sec, dict):
            heading = html.escape(str(sec.get("heading", "")))
            text = str(sec.get("body", sec.get("text", "")))
            if heading:
                body_parts.append(f"<h2>{heading}</h2>")
            if text:
                body_parts.append(f"<p>{html.escape(text)}</p>")
            if sec.get("table"):
                body_parts.append(_table_html(sec["table"]))
            if sec.get("chart"):
                body_parts.append(_svg_chart(sec["chart"]))
                n_charts += 1
        else:
            body_parts.append(f"<p>{html.escape(str(sec))}</p>")
    doc = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{html.escape(title)}</title>"
        "<style>body{font-family:system-ui,sans-serif;max-width:52rem;"
        "margin:2rem auto;padding:0 1rem;line-height:1.5}"
        "td{font-size:0.9rem}svg{margin:1rem 0}</style></head><body>"
        + "".join(body_parts)
        + "</body></html>"
    )
    written = _write(out_path, doc)
    extra = f", {n_charts} chart(s)" if n_charts else " (no charts — pass a 'chart' in a section to add one)"
    return f"wrote HTML report: {written} ({len(sections)} sections{extra})"


def rephrase(args: dict[str, Any], ctx: ToolContext) -> str:
    text = str(args.get("text", "")).strip()
    style = str(args.get("style", "clear and concise")).strip()
    if not text:
        return "error: 'text' is required"
    if ctx.client is None:
        return "rephrase unavailable: no model client in context"
    prompt = (
        f"Rewrite the following text in a {style} style. Return only the "
        f"rewritten text, nothing else.\n\nTEXT:\n{text}"
    )
    try:
        out = ctx.client.generate(prompt, system="You are a careful copy editor.")
    except Exception as e:  # noqa: BLE001
        return f"rephrase error: {e}"
    return out.strip() or "(no output)"


TOOLS = [
    Tool(
        name="make_slides",
        tag="GUARDED",
        fn=make_slides,
        required=("title", "outline", "out_path"),
        description="Build a Markdown slide deck (writes a file).",
    ),
    Tool(
        name="make_html_report",
        tag="GUARDED",
        fn=make_html_report,
        required=("title", "sections", "out_path"),
        description="Build an HTML report (writes a file).",
    ),
    Tool(
        name="rephrase",
        tag="SAFE",
        fn=rephrase,
        required=("text",),
        optional=("style",),
        description="Rewrite text in a given style.",
    ),
]
