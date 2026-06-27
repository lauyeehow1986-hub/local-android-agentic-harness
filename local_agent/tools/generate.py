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


def make_html_report(args: dict[str, Any], ctx: ToolContext) -> str:
    title = str(args.get("title", "Report")).strip()
    sections = args.get("sections") or []
    out_path = str(args.get("out_path", "report.html")).strip()
    if not isinstance(sections, list):
        return "error: 'sections' must be a list"
    body_parts: list[str] = [f"<h1>{html.escape(title)}</h1>"]
    for sec in sections:
        if isinstance(sec, dict):
            heading = html.escape(str(sec.get("heading", "")))
            text = html.escape(str(sec.get("body", sec.get("text", ""))))
            if heading:
                body_parts.append(f"<h2>{heading}</h2>")
            body_parts.append(f"<p>{text}</p>")
        else:
            body_parts.append(f"<p>{html.escape(str(sec))}</p>")
    doc = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{html.escape(title)}</title>"
        "<style>body{font-family:system-ui,sans-serif;max-width:48rem;"
        "margin:2rem auto;padding:0 1rem;line-height:1.5}</style></head><body>"
        + "".join(body_parts)
        + "</body></html>"
    )
    written = _write(out_path, doc)
    return f"wrote HTML report: {written} ({len(sections)} sections)"


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
