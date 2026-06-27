"""Data tools: analyze_data / analyze_image / transcribe.

These are SAFE (read-only analysis). They degrade gracefully when an optional
backend is missing rather than crashing the loop — the small model should get a
clear observation it can act on.

- analyze_data: stdlib CSV summary (rows, columns, simple stats), then an
  optional local-model pass to answer the task in plain language.
- analyze_image: loads a small vision model ON DEMAND via Ollama, then lets
  Ollama unload it. Never resident alongside the 4B.
- transcribe: speech-to-text. Requires an optional backend; reports cleanly if
  absent.
"""

from __future__ import annotations

import base64
import csv
import statistics
from pathlib import Path
from typing import Any

from . import Tool, ToolContext


def analyze_data(args: dict[str, Any], ctx: ToolContext) -> str:
    path = str(args.get("path", "")).strip()
    task = str(args.get("task", "")).strip()
    if not path:
        return "error: 'path' is required"
    p = Path(path)
    if not p.exists():
        return f"file not found: {path}"
    try:
        with p.open(newline="", encoding="utf-8", errors="ignore") as f:
            reader = csv.reader(f)
            rows = list(reader)
    except OSError as e:
        return f"read error: {e}"
    if not rows:
        return "empty CSV"

    header = rows[0]
    data_rows = rows[1:]
    summary = [f"{len(data_rows)} rows x {len(header)} cols", f"columns: {', '.join(header)}"]

    # Numeric column stats where possible.
    for ci, col in enumerate(header):
        nums: list[float] = []
        for r in data_rows:
            if ci < len(r):
                try:
                    nums.append(float(r[ci]))
                except ValueError:
                    pass
        if nums and len(nums) >= max(1, len(data_rows) // 2):
            summary.append(
                f"  {col}: min={min(nums):.3g} max={max(nums):.3g} "
                f"mean={statistics.fmean(nums):.3g}"
            )

    base = "\n".join(summary)

    # Optional: let the local model answer the natural-language task over the
    # summary. Kept short to respect latency.
    if task and ctx.client is not None:
        try:
            prompt = (
                f"Dataset summary:\n{base}\n\nTask: {task}\n"
                "Answer concisely in 1-3 sentences based only on the summary."
            )
            answer = ctx.client.generate(prompt, system="You are a terse data analyst.")
            return f"{base}\n\nAnswer: {answer.strip()}"
        except Exception:  # noqa: BLE001 - summary alone is still useful
            return base
    return base


def analyze_image(args: dict[str, Any], ctx: ToolContext) -> str:
    path = str(args.get("path", "")).strip()
    question = str(args.get("question", "Describe this image.")).strip()
    if not path:
        return "error: 'path' is required"
    p = Path(path)
    if not p.exists():
        return f"image not found: {path}"
    if ctx.client is None:
        return "vision unavailable: no model client in context"
    try:
        img_b64 = base64.b64encode(p.read_bytes()).decode("ascii")
    except OSError as e:
        return f"read error: {e}"
    # Load the vision model on demand. Ollama swaps models; we never hold two
    # resident in the harness ourselves.
    vision_model = getattr(ctx.config, "vision_model", "moondream")
    try:
        result = ctx.client.generate(
            question,
            model=vision_model,
            options={"images": [img_b64]},
        )
    except Exception as e:  # noqa: BLE001
        return f"vision error: {e} (is '{vision_model}' pulled?)"
    return result.strip() or "(no description returned)"


def _extract_pdf_text(path: Path, max_pages: int = 50) -> tuple[str, int]:
    """Return (text, n_pages). Pure-Python via pypdf; raises if unavailable."""
    from pypdf import PdfReader  # optional dep; caller handles ImportError

    reader = PdfReader(str(path))
    pages = reader.pages
    chunks: list[str] = []
    for i, page in enumerate(pages):
        if i >= max_pages:
            break
        try:
            chunks.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001 - skip a bad page, keep going
            continue
    return "\n".join(chunks).strip(), len(pages)


def analyze_pdf(args: dict[str, Any], ctx: ToolContext) -> str:
    """Extract text from a PDF and (optionally) summarize/answer a task over it.

    SAFE: read-only. Degrades clearly if pypdf isn't installed or the PDF has no
    extractable text (scanned image — suggest analyze_image/OCR instead).
    """
    path = str(args.get("path", "")).strip()
    task = str(args.get("task", "Summarize this document.")).strip()
    if not path:
        return "error: 'path' is required"
    p = Path(path)
    if not p.exists():
        return f"pdf not found: {path}"
    try:
        text, n_pages = _extract_pdf_text(p)
    except ImportError:
        return "pdf unavailable: install pypdf (`pip install pypdf`) to read PDFs."
    except Exception as e:  # noqa: BLE001
        return f"pdf read error: {e}"

    if not text:
        return (
            f"no extractable text in {p.name} ({n_pages} pages) — it's likely a "
            "scanned image. Try analyze_image on a page render, or OCR first."
        )

    # Cap the text fed to the small model; long context destroys latency.
    excerpt = text[:6000]
    if ctx.client is None:
        # No model available — return a useful extractive preview.
        head = " ".join(excerpt.split())[:1200]
        return f"{p.name} ({n_pages} pages). Text preview:\n{head}"

    prompt = (
        f"Document: {p.name} ({n_pages} pages). Content (may be truncated):\n"
        f"{excerpt}\n\nTask: {task}\nAnswer concisely."
    )
    try:
        answer = ctx.client.generate(prompt, system="You summarize documents tersely.")
    except Exception as e:  # noqa: BLE001
        return f"pdf summarize error: {e} (text extracted OK, {n_pages} pages)"
    return f"{p.name} ({n_pages} pages):\n{answer.strip()}"


def transcribe(args: dict[str, Any], ctx: ToolContext) -> str:
    path = str(args.get("path", "")).strip()
    if not path:
        return "error: 'path' is required"
    p = Path(path)
    if not p.exists():
        return f"audio not found: {path}"
    # Optional backend. Prefer a pure-CLI whisper if present; otherwise report.
    try:
        import shutil
        import subprocess

        exe = shutil.which("whisper") or shutil.which("whisper-cpp")
        if exe is None:
            return (
                "transcribe unavailable: no whisper backend found. Install "
                "whisper.cpp or openai-whisper to enable."
            )
        out = subprocess.run(
            [exe, str(p), "--output_format", "txt"],
            capture_output=True,
            text=True,
            timeout=600,
        )
        text = (out.stdout or "").strip()
        return text[:2000] if text else f"transcribe produced no text (rc={out.returncode})"
    except Exception as e:  # noqa: BLE001
        return f"transcribe error: {e}"


TOOLS = [
    Tool(
        name="analyze_data",
        tag="SAFE",
        fn=analyze_data,
        required=("path",),
        optional=("task",),
        description="Summarize/analyze a CSV dataset.",
    ),
    Tool(
        name="analyze_image",
        tag="SAFE",
        fn=analyze_image,
        required=("path",),
        optional=("question",),
        description="Vision model on an image (loaded on demand).",
    ),
    Tool(
        name="analyze_pdf",
        tag="SAFE",
        fn=analyze_pdf,
        required=("path",),
        optional=("task",),
        description="Extract text from a PDF and summarize/answer a task over it.",
    ),
    Tool(
        name="transcribe",
        tag="SAFE",
        fn=transcribe,
        required=("path",),
        description="Speech-to-text on an audio file.",
    ),
]
