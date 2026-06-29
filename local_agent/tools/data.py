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


def _numeric_columns(header, data_rows):
    """Return {col_name: [floats]} for columns that are mostly numeric."""
    cols: dict[str, list[float]] = {}
    for ci, col in enumerate(header):
        nums: list[float] = []
        for r in data_rows:
            if ci < len(r):
                try:
                    nums.append(float(r[ci]))
                except ValueError:
                    pass
        if nums and len(nums) >= max(1, len(data_rows) // 2):
            cols[col] = nums
    return cols


def _describe(nums: list[float]) -> str:
    n = len(nums)
    mean = statistics.fmean(nums)
    sd = statistics.pstdev(nums) if n > 1 else 0.0
    qs = statistics.quantiles(nums, n=4) if n >= 4 else [min(nums), statistics.median(nums), max(nums)]
    return (
        f"n={n} min={min(nums):.3g} q1={qs[0]:.3g} median={statistics.median(nums):.3g} "
        f"q3={qs[-1]:.3g} max={max(nums):.3g} mean={mean:.3g} sd={sd:.3g}"
    )


def _maybe_plot(numeric: dict, out_path: str) -> str:
    """Histogram of the first numeric column via matplotlib, if available."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return "(plot skipped: matplotlib not installed — `pip install matplotlib`)"
    try:
        col, vals = next(iter(numeric.items()))
        plt.figure()
        plt.hist(vals, bins=min(30, max(5, len(vals) // 5)))
        plt.title(f"{col} (n={len(vals)})")
        plt.xlabel(col)
        plt.ylabel("count")
        p = Path(out_path).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        plt.savefig(p, dpi=100, bbox_inches="tight")
        plt.close()
        return f"plot saved: {p}"
    except Exception as e:  # noqa: BLE001
        return f"(plot failed: {e})"


def analyze_data(args: dict[str, Any], ctx: ToolContext) -> str:
    path = str(args.get("path", "")).strip()
    task = str(args.get("task", "")).strip()
    plot_path = str(args.get("plot", "")).strip()
    if not path:
        return "error: 'path' is required"
    p = Path(path)
    if not p.exists():
        return f"file not found: {path}"
    try:
        with p.open(newline="", encoding="utf-8", errors="ignore") as f:
            rows = list(csv.reader(f))
    except OSError as e:
        return f"read error: {e}"
    if not rows:
        return "empty CSV"

    header, data_rows = rows[0], rows[1:]
    numeric = _numeric_columns(header, data_rows)
    summary = [f"{len(data_rows)} rows x {len(header)} cols", f"columns: {', '.join(header)}"]
    for col, nums in numeric.items():
        summary.append(f"  {col}: {_describe(nums)}")

    # Pairwise correlation between the first few numeric columns.
    cols = list(numeric.items())
    for i in range(len(cols)):
        for j in range(i + 1, min(len(cols), 4)):
            (n1, v1), (n2, v2) = cols[i], cols[j]
            m = min(len(v1), len(v2))
            if m >= 3:
                try:
                    r = statistics.correlation(v1[:m], v2[:m])
                    summary.append(f"  corr({n1},{n2}) = {r:.2f}")
                except (statistics.StatisticsError, ValueError):
                    pass

    base = "\n".join(summary)

    if plot_path and numeric:
        base += "\n" + _maybe_plot(numeric, plot_path)

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


# Words in a question that mean "read the text" (use real OCR, not a VLM that
# will plausibly invent digits — the receipt-hallucination failure mode).
_OCR_INTENT = (
    "text", "transcribe", "read", "ocr", "receipt", "invoice", "total", "subtotal",
    "price", "amount", "cost", "number", "digits", "says", "written", "writing",
    "label", "serial", "code", "date", "menu",
)


def _looks_like_ocr(question: str) -> bool:
    q = question.lower()
    return any(w in q for w in _OCR_INTENT)


def _is_transcribe_only(question: str) -> bool:
    q = question.lower()
    return any(w in q for w in ("transcribe", "ocr", "all text", "read all", "extract text"))


def _preprocess_for_ocr(raw: bytes) -> bytes:
    """Grayscale + autocontrast + upscale small images — big accuracy win for
    Tesseract on phone photos/receipts. No-op (returns raw) without Pillow."""
    try:
        import io

        from PIL import Image, ImageOps
    except ImportError:
        return raw
    try:
        im = Image.open(io.BytesIO(raw))
        im = ImageOps.exif_transpose(im)        # honor camera orientation
        im = ImageOps.grayscale(im)
        im = ImageOps.autocontrast(im)
        longest = max(im.size)
        if longest < 1600:                       # upscale small text for OCR
            scale = 1600 / longest
            im = im.resize((int(im.width * scale), int(im.height * scale)))
        out = io.BytesIO()
        im.save(out, format="PNG")
        return out.getvalue()
    except Exception:  # noqa: BLE001
        return raw


def _tesseract_ocr(raw: bytes, ctx: ToolContext) -> Optional[str]:
    """OCR image bytes with the Tesseract engine (accurate for printed text).
    Returns None if tesseract isn't installed or produced nothing."""
    import shutil
    import subprocess
    import tempfile

    exe = shutil.which("tesseract")
    if not exe:
        return None
    lang = getattr(ctx.config, "tesseract_lang", "eng")
    psm = str(getattr(ctx.config, "tesseract_psm", "6"))
    data = _preprocess_for_ocr(raw)
    with tempfile.TemporaryDirectory() as td:
        img_path = Path(td) / "in.png"
        img_path.write_bytes(data)
        out_base = Path(td) / "out"
        try:
            subprocess.run(
                [exe, str(img_path), str(out_base), "-l", lang, "--psm", psm],
                capture_output=True, text=True, timeout=120, check=True,
            )
        except Exception:  # noqa: BLE001 - treat any failure as "no tesseract result"
            return None
        txt = Path(str(out_base) + ".txt")
        if not txt.exists():
            return None
        text = txt.read_text(encoding="utf-8", errors="ignore").strip()
        return text or None


def _answer_over_text(ctx: ToolContext, text: str, question: str) -> str:
    """Have the LLM answer a question grounded ONLY in OCR'd text (no inventing)."""
    if ctx.client is None:
        return text
    prompt = (
        f"Text extracted from an image via OCR (may contain minor errors):\n{text}\n\n"
        f"Question: {question}\n"
        "Answer concisely using ONLY the text above. Do not invent numbers or items."
    )
    try:
        return ctx.client.generate(
            prompt,
            system="You answer strictly from the provided OCR text; never guess numbers.",
        ).strip()
    except Exception:  # noqa: BLE001
        return text


def analyze_image(args: dict[str, Any], ctx: ToolContext) -> str:
    """Read or analyze an image: describe it, answer a question, or OCR text.

    For reading printed text (receipts, labels, documents) it prefers the
    **Tesseract** OCR engine — a small vision model invents digits. The LLM then
    answers your question over the accurate OCR text. Pure description/VQA still
    uses the on-demand vision model. Force a path with `ocr` (true/false) or the
    AGENT_OCR_ENGINE config (auto|tesseract|vision).
    """
    path = str(args.get("path", "")).strip()
    question = str(args.get("question", "Describe this image in detail.")).strip()
    ocr_arg = args.get("ocr", None)
    if not path:
        return "error: 'path' is required"
    p = Path(path)
    if not p.exists():
        return f"image not found: {path}"
    if p.suffix.lower() not in _IMAGE_EXTS:
        return f"not a recognized image type: {p.suffix} (expected {', '.join(sorted(_IMAGE_EXTS))})"

    try:
        raw = p.read_bytes()
    except OSError as e:
        return f"read error: {e}"

    engine = getattr(ctx.config, "ocr_engine", "auto")
    want_ocr = bool(ocr_arg) if ocr_arg is not None else _looks_like_ocr(question)

    # OCR path: Tesseract first (accurate), then answer the question over it.
    if engine != "vision" and (want_ocr or engine == "tesseract"):
        text = _tesseract_ocr(raw, ctx)
        if text:
            if _is_transcribe_only(question):
                return text
            return _answer_over_text(ctx, text, question)
        if engine == "tesseract":
            return (
                "tesseract OCR found no text (install it: `pkg install tesseract`, "
                "or the image has no readable text)."
            )
        # auto: fall through to the vision model.

    if ctx.client is None:
        return "vision unavailable: no model client in context (and no tesseract text)"
    vision_model = getattr(ctx.config, "vision_model", "moondream")
    try:
        result = _vision_ocr(ctx, raw, question)
    except Exception as e:  # noqa: BLE001
        return f"vision error: {e} (is '{vision_model}' pulled? try: ollama pull {vision_model})"
    return result or "(no description returned)"


def _extract_with_pymupdf(path: Path, max_pages: int):
    """Text via PyMuPDF (fitz). Robust — handles many PDFs pypdf can't, including
    empty-password-encrypted ones. Returns (text, n_pages) or (None, 0) if fitz
    is unavailable or the document can't be opened."""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return None, 0
    try:
        doc = fitz.open(str(path))
    except Exception:  # noqa: BLE001 - let pypdf try next
        return None, 0
    try:
        if getattr(doc, "is_encrypted", False):
            doc.authenticate("")  # try the common empty user password
        n = doc.page_count
        parts: list[str] = []
        for i, page in enumerate(doc):
            if i >= max_pages:
                break
            try:
                parts.append(page.get_text() or "")
            except Exception:  # noqa: BLE001
                continue
        return "\n".join(parts).strip(), n
    except Exception:  # noqa: BLE001
        return None, 0
    finally:
        try:
            doc.close()
        except Exception:  # noqa: BLE001
            pass


def _extract_with_pypdf(path: Path, max_pages: int) -> tuple[str, int]:
    """Text via pypdf. Raises ImportError if pypdf isn't installed."""
    from pypdf import PdfReader  # optional dep; caller handles ImportError

    reader = PdfReader(str(path))
    if getattr(reader, "is_encrypted", False):
        try:
            reader.decrypt("")  # empty user password — common for "opens fine" PDFs
        except Exception:  # noqa: BLE001
            pass
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


def _extract_pdf_text(path: Path, max_pages: int = 50) -> tuple[str, int]:
    """Return (text, n_pages). Try PyMuPDF first (more robust), then pypdf.

    Raises ImportError only if NEITHER backend is available.
    """
    text, n = _extract_with_pymupdf(path, max_pages)
    if text is not None:
        return text, n
    return _extract_with_pypdf(path, max_pages)


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
        # No text-extraction backend installed. We may still be able to OCR if a
        # renderer is present, so route to the OCR fallback rather than dead-end.
        text, n_pages = "", 0
    except Exception:  # noqa: BLE001
        # The text parser failed (encrypted/corrupt for it, odd encoding, the
        # "codec error" case). The PDF may still render fine — fall back to OCR.
        text, n_pages = "", 0

    if not text:
        # No extractable text (scanned, or extraction failed): render pages and
        # OCR them with the vision model.
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
    """OCR fallback for image-only PDFs: render pages → OCR → text.

    Prefers the Tesseract engine per page (accurate for documents); falls back to
    the vision model only if Tesseract isn't installed.
    """
    engine = getattr(ctx.config, "ocr_engine", "auto")
    use_vision_fallback = ctx.client is not None and engine != "tesseract"
    has_tesseract = bool(__import__("shutil").which("tesseract")) and engine != "vision"
    if not has_tesseract and not use_vision_fallback:
        return (
            f"no extractable text in {p.name} ({n_pages} pages) — looks scanned, and no "
            "OCR engine available. Install Tesseract (`pkg install tesseract`) or a "
            "vision model."
        )
    max_pages = getattr(ctx.config, "pdf_ocr_max_pages", 5)
    try:
        pages = _render_pdf_pages(p, max_pages)
    except RuntimeError:
        return (
            f"couldn't read text from {p.name} and can't render it to OCR. On Termux: "
            "`pkg install poppler && pip install pdf2image` (the installable renderer; "
            "pymupdf won't build on-device). On desktop/LAN: `pip install pymupdf`. For "
            "text PDFs, `pip install pypdf` also helps."
        )
    except Exception as e:  # noqa: BLE001
        return f"pdf render error: {e}"
    if not pages:
        return f"could not render any pages from {p.name}"

    vision_model = getattr(ctx.config, "vision_model", "moondream")
    engine_used = "tesseract" if has_tesseract else vision_model
    ocr_parts: list[str] = []
    for i, img in enumerate(pages, 1):
        txt = _tesseract_ocr(img, ctx) if has_tesseract else None
        if not txt and use_vision_fallback:
            try:
                txt = _vision_ocr(ctx, img, "Transcribe all text in this image verbatim.")
            except Exception as e:  # noqa: BLE001
                return f"OCR error on page {i}: {e} (is '{vision_model}' pulled?)"
        if txt:
            ocr_parts.append(f"[page {i}]\n{txt}")
    ocr_text = "\n\n".join(ocr_parts).strip()
    if not ocr_text:
        return f"OCR produced no text for {p.name} ({n_pages} pages)."

    if ctx.client is None:
        return f"{p.name} ({n_pages} pages, OCR'd via {engine_used}):\n{ocr_text[:1500]}"

    note = (
        f"{p.name} ({n_pages} pages, OCR'd first {len(pages)} via {engine_used})"
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


_AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".flac", ".mp4", ".webm"}


def _summarize_long(
    ctx: ToolContext, text: str, task: str, *, chunk_chars: int = 3000, max_chunks: int = 12
) -> str:
    """Map-reduce summary so long transcripts fit a small context window.

    Summarize each chunk toward the goal, then summarize the summaries. Bounded
    by max_chunks so a multi-hour meeting can't spawn unlimited slow model calls.
    """
    chunks = [text[i : i + chunk_chars] for i in range(0, len(text), chunk_chars)]
    truncated = len(chunks) > max_chunks
    chunks = chunks[:max_chunks]
    system = "You write terse, structured meeting notes."

    if len(chunks) == 1:
        return ctx.client.generate(
            f"{task}\n\nTranscript:\n{chunks[0]}", system=system
        ).strip()

    partials: list[str] = []
    for i, c in enumerate(chunks, 1):
        s = ctx.client.generate(
            f"Summarize part {i} of a meeting transcript toward this goal: {task}\n\n{c}",
            system=system,
        )
        partials.append(s.strip())
    combined = "\n".join(partials)
    final = ctx.client.generate(
        f"{task}\n\nCombine these partial summaries into one set of notes:\n{combined}",
        system=system,
    ).strip()
    if truncated:
        final += f"\n[note: only the first {max_chunks} chunks were summarized]"
    return final


def _resolve_whispercpp_model(name: str) -> str:
    """Allow AGENT_WHISPER_CPP_MODEL to be a short name (e.g. 'small.en' or
    'small') instead of a full path. An existing file path is used as-is;
    otherwise we expand <name> -> ~/whisper.cpp/models/ggml-<name>.bin."""
    if not name:
        return name
    p = Path(name).expanduser()
    if p.exists():
        return str(p)
    short = name
    if short.startswith("ggml-"):
        short = short[len("ggml-"):]
    if short.endswith(".bin"):
        short = short[:-4]
    return str(Path.home() / "whisper.cpp" / "models" / f"ggml-{short}.bin")


def _transcribe_audio(p: Path, ctx: ToolContext, language: str, *, translate: bool = False) -> str:
    """Detect a Whisper backend and return the transcript text.

    Backends tried (auto): openai-whisper CLI → whisper.cpp → faster-whisper.
    `translate=True` outputs English regardless of the spoken language.
    Raises RuntimeError('no-backend') if none is available, or
    RuntimeError('ffmpeg-needed') if whisper.cpp needs WAV but ffmpeg is absent.
    """
    import shutil
    import subprocess
    import tempfile

    pref = getattr(ctx.config, "whisper_backend", "auto")
    lang_args_openai = (["--language", language] if language else [])

    # 1) openai-whisper / whisper CLI (handles m4a/etc directly via its own ffmpeg).
    if pref in ("auto", "whisper"):
        exe = shutil.which("whisper")
        if exe:
            model = getattr(ctx.config, "whisper_model", "base")
            bs = str(getattr(ctx.config, "whisper_beam_size", 5))
            task = "translate" if translate else "transcribe"
            with tempfile.TemporaryDirectory() as td:
                subprocess.run(
                    [exe, str(p), "--model", model, "--output_format", "txt",
                     "--output_dir", td, "--task", task, "--beam_size", bs,
                     *lang_args_openai],
                    capture_output=True, text=True, timeout=3600, check=True,
                )
                out_txt = Path(td) / (p.stem + ".txt")
                if out_txt.exists():
                    return out_txt.read_text(encoding="utf-8", errors="ignore").strip()
            return ""

    # 2) whisper.cpp — needs a ggml model and a 16k mono WAV.
    if pref in ("auto", "whispercpp"):
        exe = next((shutil.which(n) for n in ("whisper-cli", "whisper-cpp", "main") if shutil.which(n)), None)
        if exe:
            model = _resolve_whispercpp_model(getattr(ctx.config, "whisper_cpp_model", ""))
            if not model:
                raise RuntimeError("whispercpp-model-needed")
            wav, tmp_wav = _ensure_wav16k(p)
            try:
                with tempfile.TemporaryDirectory() as td:
                    of = Path(td) / "out"
                    bs = str(getattr(ctx.config, "whisper_beam_size", 5))
                    cmd = [exe, "-m", model, "-f", str(wav), "-otxt", "-of", str(of), "-bs", bs]
                    if language:
                        cmd += ["-l", language]
                    if translate:
                        cmd += ["-tr"]
                    subprocess.run(cmd, capture_output=True, text=True, timeout=3600, check=True)
                    txt = Path(str(of) + ".txt")
                    return txt.read_text(encoding="utf-8", errors="ignore").strip() if txt.exists() else ""
            finally:
                if tmp_wav and tmp_wav.exists():
                    tmp_wav.unlink()

    # 3) faster-whisper (Python).
    if pref in ("auto", "faster"):
        try:
            from faster_whisper import WhisperModel  # optional dep
        except ImportError:
            WhisperModel = None  # type: ignore[assignment]
        if WhisperModel is not None:
            model = getattr(ctx.config, "whisper_model", "base")
            wm = WhisperModel(model, device="cpu", compute_type="int8")
            segments, _ = wm.transcribe(
                str(p), language=language or None,
                task="translate" if translate else "transcribe",
            )
            return " ".join(seg.text.strip() for seg in segments).strip()

    raise RuntimeError("no-backend")


def _ensure_wav16k(p: Path):
    """Return (wav_path, temp_to_cleanup_or_None). Converts via ffmpeg if needed."""
    import shutil
    import subprocess
    import tempfile

    if p.suffix.lower() == ".wav":
        return p, None
    ff = shutil.which("ffmpeg")
    if not ff:
        raise RuntimeError("ffmpeg-needed")
    fd, tmp_name = tempfile.mkstemp(suffix=".wav")
    import os

    os.close(fd)
    tmp = Path(tmp_name)
    subprocess.run(
        [ff, "-y", "-i", str(p), "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(tmp)],
        capture_output=True, text=True, timeout=1800, check=True,
    )
    return tmp, tmp


def _whisper_hint(kind: str) -> str:
    """Map a backend RuntimeError code to an actionable user message."""
    if kind == "no-backend":
        return (
            "transcribe unavailable: no speech backend found. Install one: "
            "whisper.cpp (`pkg install whisper.cpp` or build it; set "
            "AGENT_WHISPER_CPP_MODEL to a ggml model), or `pip install "
            "openai-whisper`, or `pip install faster-whisper`. For non-WAV "
            "audio also install ffmpeg (`pkg install ffmpeg`)."
        )
    if kind == "ffmpeg-needed":
        return "transcribe needs ffmpeg to convert this audio to WAV: `pkg install ffmpeg`."
    if kind == "whispercpp-model-needed":
        return (
            "whisper.cpp found but no model set. Point AGENT_WHISPER_CPP_MODEL at a "
            "ggml model file (e.g. ggml-base.en.bin)."
        )
    if kind == "diarize-unavailable":
        return (
            "speaker diarization needs whisperx (`pip install whisperx`) and a "
            "HuggingFace token for pyannote — heavy; run it on the LAN/desktop box, "
            "not the phone. Falling back: re-run without diarization."
        )
    return f"transcribe error: {kind}"


def _audio_to_text(p: Path, ctx: ToolContext, language: str, *, diarize: bool = False, translate: bool = False):
    """Transcribe audio to text. Returns (text, error_message). Exactly one is set."""
    try:
        if diarize:
            return _transcribe_diarized(p, ctx, language), None
        return _transcribe_audio(p, ctx, language, translate=translate), None
    except RuntimeError as e:
        return None, _whisper_hint(str(e))
    except Exception as e:  # noqa: BLE001
        return None, f"transcribe error: {e}"


def _transcribe_diarized(p: Path, ctx: ToolContext, language: str) -> str:
    """Speaker-labeled transcript via whisperx (desktop/LAN-grade; heavy deps).

    Raises RuntimeError('diarize-unavailable') if whisperx isn't installed.
    Returns text with '[SPEAKER_xx] ...' lines. Errors are surfaced, not raised
    as tracebacks, so the loop stays alive.
    """
    try:
        import os

        import whisperx  # optional, heavy
    except ImportError:
        raise RuntimeError("diarize-unavailable")
    model_name = getattr(ctx.config, "whisper_model", "base")
    hf_token = os.environ.get("HF_TOKEN", "")
    audio = whisperx.load_audio(str(p))
    model = whisperx.load_model(model_name, device="cpu", compute_type="int8")
    result = model.transcribe(audio, language=language or None)
    align_model, meta = whisperx.load_align_model(
        language_code=result["language"], device="cpu"
    )
    result = whisperx.align(result["segments"], align_model, meta, audio, "cpu")
    diarize_model = whisperx.DiarizationPipeline(use_auth_token=hf_token, device="cpu")
    diarize_segments = diarize_model(audio)
    result = whisperx.assign_word_speakers(diarize_segments, result)
    lines: list[str] = []
    for seg in result.get("segments", []):
        spk = seg.get("speaker", "SPEAKER_?")
        lines.append(f"[{spk}] {seg.get('text', '').strip()}")
    return "\n".join(lines).strip()


def transcribe(args: dict[str, Any], ctx: ToolContext) -> str:
    """Speech-to-text for meetings: transcribe an audio/video file, save the full
    transcript next to it, and (optionally) summarize / extract action items.

    args: {"path": str, "task": str?, "language": str?, "diarize": bool?, "translate": bool?}
      - task: if given (e.g. "summarize decisions and action items"), runs a
        map-reduce summary over the transcript with the local model.
      - language: e.g. "en"; omit to auto-detect.
      - diarize: label speakers (needs whisperx; desktop/LAN-grade).
      - translate: output English even if the speech is another language.
    """
    path = str(args.get("path", "")).strip()
    task = str(args.get("task", "")).strip()
    language = str(args.get("language", "") or getattr(ctx.config, "whisper_language", "")).strip()
    diarize = bool(args.get("diarize", False))
    translate = bool(args.get("translate", False))
    if not path:
        return "error: 'path' is required"
    p = Path(path)
    if not p.exists():
        return f"audio not found: {path}"
    if p.suffix.lower() not in _AUDIO_EXTS:
        return f"unrecognized audio type: {p.suffix} (expected {', '.join(sorted(_AUDIO_EXTS))})"

    text, err = _audio_to_text(p, ctx, language, diarize=diarize, translate=translate)
    if err:
        return err

    if not text:
        return f"transcribe produced no text for {p.name} (silent or unsupported audio?)"

    # Save the full transcript next to the audio — meetings are long; we don't
    # want the whole thing flooding the model's context as an observation.
    transcript_path = p.with_suffix(p.suffix + ".transcript.txt")
    try:
        transcript_path.write_text(text, encoding="utf-8")
        saved = f"transcript saved: {transcript_path}"
    except OSError:
        saved = "(could not save transcript file)"
    n_words = len(text.split())
    header = f"{p.name}: {n_words} words transcribed. {saved}"

    if task and ctx.client is not None:
        try:
            summary = _summarize_long(ctx, text, task)
        except Exception as e:  # noqa: BLE001
            return f"{header}\n(summary failed: {e}; full transcript is saved)"
        return f"{header}\n\n{task}:\n{summary}"

    preview = " ".join(text.split())[:800]
    return f"{header}\nPreview: {preview}…"


_MEETING_TEMPLATE = (
    "Write structured meeting notes in Markdown from the transcript. Use EXACTLY "
    "these sections and nothing else:\n"
    "## Summary\n(2-4 sentences)\n"
    "## Decisions\n(- bullet list)\n"
    "## Action Items\n(a Markdown table: | Owner | Action | Due |)\n"
    "## Follow-ups\n(- bullet list)\n"
    "Be terse and factual; include only what's in the transcript. If a section has "
    "nothing, write '- none'."
)

_TRANSCRIPT_EXTS = {".txt", ".md", ".vtt", ".srt"}


def meeting_notes(args: dict[str, Any], ctx: ToolContext) -> str:
    """Turn a meeting recording (or an existing transcript) into a structured
    Markdown note: Summary / Decisions / Action Items (owner table) / Follow-ups.

    args: {"path": str, "title": str?, "context": str?, "language": str?, "diarize": bool?}
      - path: an audio file (transcribed first) or a transcript .txt/.md/.vtt/.srt.
      - context: optional hints (meeting title, attendees) folded into the prompt.

    Returns ready-to-save Markdown (with YAML frontmatter). SAFE: it produces
    text; the agent saves it with vault_write (approved in hitl).
    """
    import datetime

    path = str(args.get("path", "")).strip()
    title = str(args.get("title", "")).strip()
    context = str(args.get("context", "")).strip()
    language = str(args.get("language", "") or getattr(ctx.config, "whisper_language", "")).strip()
    diarize = bool(args.get("diarize", False))
    translate = bool(args.get("translate", False))
    if not path:
        return "error: 'path' is required"
    if ctx.client is None:
        return "meeting_notes needs the local model to summarize."
    p = Path(path)
    if not p.exists():
        return f"file not found: {path}"

    # Get the transcript text — either read it, or transcribe the audio first.
    suffix = p.suffix.lower()
    if suffix in _TRANSCRIPT_EXTS:
        text = p.read_text(encoding="utf-8", errors="ignore").strip()
        source = f"transcript {p.name}"
    elif suffix in _AUDIO_EXTS:
        text, err = _audio_to_text(p, ctx, language, diarize=diarize, translate=translate)
        if err:
            return err
        if not text:
            return f"no speech transcribed from {p.name}"
        # Save the raw transcript alongside the audio for the record.
        try:
            p.with_suffix(p.suffix + ".transcript.txt").write_text(text, encoding="utf-8")
        except OSError:
            pass
        source = f"audio {p.name}"
    else:
        return f"unsupported input: {p.suffix} (give an audio file or a transcript)"

    if not text:
        return f"empty transcript: {p.name}"

    task = _MEETING_TEMPLATE + (f"\nMeeting context: {context}" if context else "")
    try:
        body = _summarize_long(ctx, text, task)
    except Exception as e:  # noqa: BLE001
        return f"meeting_notes summary failed: {e}"

    today = datetime.date.today().isoformat()
    if not title:
        title = f"Meeting {today}"
    frontmatter = (
        f"---\ntitle: {title}\ndate: {today}\ntype: meeting\nsource: {source}\n---\n\n"
        f"# {title}\n\n"
    )
    return frontmatter + body.strip() + "\n"


TOOLS = [
    Tool(
        name="analyze_data",
        tag="SAFE",
        fn=analyze_data,
        required=("path",),
        optional=("task", "plot"),
        description="Analyze a CSV: per-column stats, correlations, optional histogram (plot=path).",
    ),
    Tool(
        name="analyze_image",
        tag="SAFE",
        fn=analyze_image,
        required=("path",),
        optional=("question", "ocr"),
        description="Read/analyze an image: OCR text (Tesseract) for receipts/labels, or describe via vision model.",
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
        optional=("task", "language", "diarize", "translate"),
        description="Transcribe meeting audio; optionally summarize / extract action items.",
    ),
    Tool(
        name="meeting_notes",
        tag="SAFE",
        fn=meeting_notes,
        required=("path",),
        optional=("title", "context", "language", "diarize", "translate"),
        description="Audio/transcript → structured meeting note (summary, decisions, action items).",
    ),
]
