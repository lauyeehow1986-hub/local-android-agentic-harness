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


_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}


def _downscale_bytes(raw: bytes, max_px: int) -> bytes:
    """Downscale image bytes so the longest side <= max_px, if Pillow is
    available. Returns raw unchanged if Pillow isn't installed, the image is
    already small, or anything goes wrong — vision still works, just slower.
    """
    if max_px <= 0:
        return raw
    try:
        import io

        from PIL import Image  # optional dep
    except ImportError:
        return raw
    try:
        with Image.open(io.BytesIO(raw)) as im:
            longest = max(im.size)
            if longest <= max_px:
                return raw
            scale = max_px / longest
            new_size = (max(1, int(im.width * scale)), max(1, int(im.height * scale)))
            im = im.convert("RGB").resize(new_size)
            out = io.BytesIO()
            im.save(out, format="JPEG", quality=85)
            return out.getvalue()
    except Exception:  # noqa: BLE001 - any decode/resize failure → use raw
        return raw


def _image_bytes(p: Path, max_px: int) -> bytes:
    """Read an image file and downscale it (see _downscale_bytes)."""
    return _downscale_bytes(p.read_bytes(), max_px)


def _vision_ocr(ctx: ToolContext, img_bytes: bytes, question: str) -> str:
    """Run the vision model on raw image bytes; load-on-demand, unload after."""
    import base64 as _b64

    max_px = getattr(ctx.config, "max_image_px", 1024)
    b64 = _b64.b64encode(_downscale_bytes(img_bytes, max_px)).decode("ascii")
    vision_model = getattr(ctx.config, "vision_model", "moondream")
    keep_alive = getattr(ctx.config, "vision_keep_alive", "0")
    return ctx.client.generate(
        question, model=vision_model, images=[b64], keep_alive=keep_alive
    ).strip()


def _render_pdf_pages(path: Path, max_pages: int) -> list[bytes]:
    """Render the first max_pages of a PDF to PNG bytes.

    Tries PyMuPDF (`fitz`, no external binary) first, then pdf2image (needs the
    poppler `pdftoppm` binary). Raises RuntimeError('no-backend') if neither is
    available so the caller can give an install hint.
    """
    # Backend 1: PyMuPDF.
    try:
        import fitz  # PyMuPDF

        out: list[bytes] = []
        with fitz.open(str(path)) as doc:
            for i, page in enumerate(doc):
                if i >= max_pages:
                    break
                pix = page.get_pixmap(dpi=150)
                out.append(pix.tobytes("png"))
        return out
    except ImportError:
        pass

    # Backend 2: pdf2image + poppler.
    try:
        import io

        from pdf2image import convert_from_path

        images = convert_from_path(str(path), dpi=150, first_page=1, last_page=max_pages)
        out = []
        for im in images:
            buf = io.BytesIO()
            im.save(buf, format="PNG")
            out.append(buf.getvalue())
        return out
    except ImportError:
        raise RuntimeError("no-backend")


def analyze_image(args: dict[str, Any], ctx: ToolContext) -> str:
    """Run a vision model on an image to describe it, answer a question about it,
    or read/transcribe text from it (OCR).

    The vision model is loaded ON DEMAND and unloaded right after (keep_alive=0)
    so it never sits in RAM next to the 4B. Ask for OCR with a question like
    "Transcribe all text in this image."
    """
    path = str(args.get("path", "")).strip()
    question = str(args.get("question", "Describe this image in detail.")).strip()
    if not path:
        return "error: 'path' is required"
    p = Path(path)
    if not p.exists():
        return f"image not found: {path}"
    if p.suffix.lower() not in _IMAGE_EXTS:
        return f"not a recognized image type: {p.suffix} (expected {', '.join(sorted(_IMAGE_EXTS))})"
    if ctx.client is None:
        return "vision unavailable: no model client in context"

    try:
        raw = p.read_bytes()
    except OSError as e:
        return f"read error: {e}"
    vision_model = getattr(ctx.config, "vision_model", "moondream")
    try:
        result = _vision_ocr(ctx, raw, question)
    except Exception as e:  # noqa: BLE001
        return f"vision error: {e} (is '{vision_model}' pulled? try: ollama pull {vision_model})"
    return result or "(no description returned)"


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
        # Scanned/image-only PDF: fall back to rendering pages and OCR'ing them
        # with the vision model.
        return _analyze_pdf_ocr(p, n_pages, task, ctx)

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


def _analyze_pdf_ocr(p: Path, n_pages: int, task: str, ctx: ToolContext) -> str:
    """OCR fallback for image-only PDFs: render pages → vision model → text."""
    if ctx.client is None:
        return (
            f"no extractable text in {p.name} ({n_pages} pages) — looks scanned, and "
            "no vision client is available to OCR it."
        )
    max_pages = getattr(ctx.config, "pdf_ocr_max_pages", 5)
    try:
        pages = _render_pdf_pages(p, max_pages)
    except RuntimeError:
        return (
            f"no extractable text in {p.name} ({n_pages} pages) — it's scanned. To OCR "
            "it, install a PDF renderer: `pip install pymupdf` (preferred) or "
            "`pip install pdf2image` + the poppler binary (`pkg install poppler`)."
        )
    except Exception as e:  # noqa: BLE001
        return f"pdf render error: {e}"
    if not pages:
        return f"could not render any pages from {p.name}"

    vision_model = getattr(ctx.config, "vision_model", "moondream")
    ocr_parts: list[str] = []
    for i, img in enumerate(pages, 1):
        try:
            txt = _vision_ocr(ctx, img, "Transcribe all text in this image verbatim.")
        except Exception as e:  # noqa: BLE001
            return f"vision OCR error on page {i}: {e} (is '{vision_model}' pulled?)"
        if txt:
            ocr_parts.append(f"[page {i}]\n{txt}")
    ocr_text = "\n\n".join(ocr_parts).strip()
    if not ocr_text:
        return f"OCR produced no text for {p.name} ({n_pages} pages)."

    note = (
        f"{p.name} ({n_pages} pages, OCR'd first {len(pages)} via {vision_model})"
    )
    # Optionally run the task (e.g. summarize) over the OCR'd text with the text
    # model. This swaps the vision model out for the 4B again.
    excerpt = ocr_text[:6000]
    if task and task.lower() not in ("transcribe", "ocr", "extract text"):
        try:
            answer = ctx.client.generate(
                f"Document text (OCR'd, may be imperfect):\n{excerpt}\n\n"
                f"Task: {task}\nAnswer concisely.",
                system="You summarize documents tersely.",
            )
            return f"{note}:\n{answer.strip()}"
        except Exception:  # noqa: BLE001 - fall back to raw OCR text
            pass
    return f"{note}:\n{excerpt}"


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
