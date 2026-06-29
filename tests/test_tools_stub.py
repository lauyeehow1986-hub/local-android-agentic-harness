"""Tool registry wiring + vault/generation tools against a temp vault.

These exercise the real tool fns (no Ollama needed) and confirm the registry
matches the model-facing contract names.
"""

import json
from pathlib import Path

import pytest

from local_agent.config import Config
from local_agent.tools import ToolContext, build_registry


# Names that MUST exist (kept in sync with prompts/agent_system.md).
CONTRACT_TOOLS = {
    "vault_search",
    "vault_semantic_search",
    "vault_read",
    "vault_list",
    "vault_write",
    "web_search",
    "web_scrape",
    "browser",
    "research",
    "analyze_data",
    "analyze_image",
    "analyze_pdf",
    "transcribe",
    "meeting_notes",
    "make_slides",
    "make_html_report",
    "rephrase",
    "shell",
    "git_sync",
    "open_app",
    "latest_file",
    "maps",
    "clipboard",
    "notify",
    "location",
    "speak",
    "request_approval",
}


@pytest.fixture
def reg():
    return build_registry()


@pytest.fixture
def ctx(tmp_path):
    cfg = Config()
    cfg.vault_path = tmp_path / "vault"
    (cfg.vault_path).mkdir()
    return ToolContext(config=cfg, client=None)


def test_registry_matches_contract(reg):
    assert set(reg.keys()) == CONTRACT_TOOLS


def test_tags_present(reg):
    for name, tool in reg.items():
        assert tool.tag in ("SAFE", "GUARDED"), name


def test_guarded_set(reg):
    guarded = {n for n, t in reg.items() if t.tag == "GUARDED"}
    assert guarded == {
        "vault_write",
        "browser",
        "make_slides",
        "make_html_report",
        "shell",
        "git_sync",
        "open_app",
    }


def test_vault_write_create_then_read(reg, ctx):
    write = reg["vault_write"].fn
    read = reg["vault_read"].fn
    out = write({"path": "Daily/2026-06-27.md", "content": "hello\n", "mode": "create"}, ctx)
    assert "created" in out
    got = read({"path": "Daily/2026-06-27.md"}, ctx)
    assert got == "hello\n"


def test_vault_write_create_refuses_existing(reg, ctx):
    write = reg["vault_write"].fn
    write({"path": "a.md", "content": "x", "mode": "create"}, ctx)
    out = write({"path": "a.md", "content": "y", "mode": "create"}, ctx)
    assert "refused" in out


def test_vault_write_append(reg, ctx):
    write = reg["vault_write"].fn
    read = reg["vault_read"].fn
    write({"path": "log.md", "content": "line1", "mode": "create"}, ctx)
    write({"path": "log.md", "content": "line2", "mode": "append"}, ctx)
    assert read({"path": "log.md"}, ctx) == "line1\nline2"


def test_vault_search(reg, ctx):
    write = reg["vault_write"].fn
    write({"path": "notes/omop.md", "content": "OMOP date mapping notes", "mode": "create"}, ctx)
    out = reg["vault_search"].fn({"query": "OMOP date", "limit": 5}, ctx)
    assert "omop.md" in out


def test_vault_search_ranks_by_relevance(reg, ctx):
    write = reg["vault_write"].fn
    # 'aaa.md' is alphabetically first but only mentions the term once;
    # 'zzz-recurrent.md' has a filename match + multiple hits → should rank first.
    write({"path": "aaa.md", "content": "a passing mention of recurrent here", "mode": "create"}, ctx)
    write(
        {
            "path": "zzz-recurrent.md",
            "content": "# Recurrent events\nrecurrent recurrent recurrent analysis",
            "mode": "create",
        },
        ctx,
    )
    out = reg["vault_search"].fn({"query": "recurrent", "limit": 5}, ctx)
    first_line = out.splitlines()[0]
    assert "zzz-recurrent.md" in first_line  # most relevant first, not alphabetical


def test_vault_search_no_match(reg, ctx):
    out = reg["vault_search"].fn({"query": "nonexistentxyz", "limit": 5}, ctx)
    assert "no matches" in out


def test_transcribe_writes_sidecar_and_preview(reg, tmp_path, monkeypatch):
    from local_agent.config import Config
    from local_agent.tools import ToolContext, data

    cfg = Config()
    cfg.vault_path = tmp_path / "vault"
    cfg.vault_path.mkdir()
    ctx2 = ToolContext(config=cfg, client=None)
    audio = tmp_path / "standup.m4a"
    audio.write_bytes(b"fake-audio")

    monkeypatch.setattr(
        data, "_transcribe_audio", lambda p, c, lang, translate=False: "We shipped v10 and agreed to fix the bug."
    )
    out = reg["transcribe"].fn({"path": str(audio)}, ctx2)
    assert "9 words transcribed" in out
    # Full transcript saved next to the audio.
    sidecar = audio.with_suffix(audio.suffix + ".transcript.txt")
    assert sidecar.exists()
    assert "shipped v10" in sidecar.read_text()
    assert "Preview:" in out


def test_transcribe_with_summary_task(reg, tmp_path, monkeypatch):
    from local_agent.config import Config
    from local_agent.tools import ToolContext, data

    cfg = Config()
    cfg.vault_path = tmp_path / "vault"
    cfg.vault_path.mkdir()
    client = _CapturingClient(reply="Decision: ship. Action: Bob fixes bug.")
    ctx2 = ToolContext(config=cfg, client=client)
    audio = tmp_path / "meeting.wav"
    audio.write_bytes(b"fake")

    monkeypatch.setattr(data, "_transcribe_audio", lambda p, c, lang, translate=False: "long transcript text")
    out = reg["transcribe"].fn(
        {"path": str(audio), "task": "summarize decisions and action items"}, ctx2
    )
    assert "Action: Bob fixes bug" in out


def test_transcribe_translate_flag(reg, tmp_path, monkeypatch):
    from local_agent.config import Config
    from local_agent.tools import ToolContext, data

    cfg = Config()
    cfg.vault_path = tmp_path / "vault"
    cfg.vault_path.mkdir()
    ctx2 = ToolContext(config=cfg, client=None)
    audio = tmp_path / "zh.m4a"
    audio.write_bytes(b"x")

    seen = {}

    def fake_audio_to_text(p, c, lang, *, diarize=False, translate=False):
        seen["translate"] = translate
        return ("Hello in English", None)

    monkeypatch.setattr(data, "_audio_to_text", fake_audio_to_text)
    out = reg["transcribe"].fn({"path": str(audio), "translate": True}, ctx2)
    assert seen["translate"] is True
    assert "English" in out


def test_resolve_whispercpp_short_name():
    from pathlib import Path

    from local_agent.tools import data

    resolved = data._resolve_whispercpp_model("small.en")
    assert resolved.endswith("/whisper.cpp/models/ggml-small.en.bin")
    # already-good name forms collapse to the same path
    assert data._resolve_whispercpp_model("ggml-small.en.bin").endswith("ggml-small.en.bin")


def test_transcribe_no_backend(reg, tmp_path, monkeypatch):
    from local_agent.config import Config
    from local_agent.tools import ToolContext, data

    cfg = Config()
    cfg.vault_path = tmp_path / "vault"
    cfg.vault_path.mkdir()
    ctx2 = ToolContext(config=cfg, client=None)
    audio = tmp_path / "rec.mp3"
    audio.write_bytes(b"x")

    def no_backend(p, c, lang, translate=False):
        raise RuntimeError("no-backend")

    monkeypatch.setattr(data, "_transcribe_audio", no_backend)
    out = reg["transcribe"].fn({"path": str(audio)}, ctx2)
    assert "no speech backend" in out


def test_transcribe_rejects_non_audio(reg, ctx, tmp_path):
    txt = tmp_path / "note.txt"
    txt.write_text("hi")
    out = reg["transcribe"].fn({"path": str(txt)}, ctx)
    assert "unrecognized audio type" in out


def test_meeting_notes_from_transcript(reg, tmp_path):
    from local_agent.config import Config
    from local_agent.tools import ToolContext

    cfg = Config()
    cfg.vault_path = tmp_path / "vault"
    cfg.vault_path.mkdir()
    client = _CapturingClient(
        reply="## Summary\nShipped v10.\n## Decisions\n- ship\n## Action Items\n| Owner | Action | Due |\n| Bob | fix bug | Fri |\n## Follow-ups\n- none"
    )
    ctx2 = ToolContext(config=cfg, client=client)
    tx = tmp_path / "standup.txt"
    tx.write_text("Alice: we shipped v10. Bob will fix the bug by Friday.")

    out = reg["meeting_notes"].fn({"path": str(tx), "title": "Standup"}, ctx2)
    assert out.startswith("---")                 # YAML frontmatter
    assert "type: meeting" in out
    assert "# Standup" in out
    assert "## Action Items" in out
    assert "Bob" in out


def test_meeting_notes_from_audio(reg, tmp_path, monkeypatch):
    from local_agent.config import Config
    from local_agent.tools import ToolContext, data

    cfg = Config()
    cfg.vault_path = tmp_path / "vault"
    cfg.vault_path.mkdir()
    client = _CapturingClient(reply="## Summary\nok\n## Decisions\n- none\n## Action Items\n| Owner | Action | Due |\n## Follow-ups\n- none")
    ctx2 = ToolContext(config=cfg, client=client)
    audio = tmp_path / "meeting.m4a"
    audio.write_bytes(b"fake")

    monkeypatch.setattr(data, "_audio_to_text", lambda p, c, lang, diarize=False, translate=False: ("transcript words", None))
    out = reg["meeting_notes"].fn({"path": str(audio)}, ctx2)
    assert "type: meeting" in out
    # raw transcript saved alongside the audio
    assert (audio.with_suffix(audio.suffix + ".transcript.txt")).exists()


def test_meeting_notes_needs_model(reg, ctx, tmp_path):
    tx = tmp_path / "t.txt"
    tx.write_text("hi")
    out = reg["meeting_notes"].fn({"path": str(tx)}, ctx)  # ctx.client is None
    assert "needs the local model" in out


def test_transcribe_diarize_unavailable_hint(reg, tmp_path, monkeypatch):
    from local_agent.config import Config
    from local_agent.tools import ToolContext, data

    cfg = Config()
    cfg.vault_path = tmp_path / "vault"
    cfg.vault_path.mkdir()
    ctx2 = ToolContext(config=cfg, client=None)
    audio = tmp_path / "rec.wav"
    audio.write_bytes(b"x")

    def needs_whisperx(p, c, lang):
        raise RuntimeError("diarize-unavailable")

    monkeypatch.setattr(data, "_transcribe_diarized", needs_whisperx)
    out = reg["transcribe"].fn({"path": str(audio), "diarize": True}, ctx2)
    assert "whisperx" in out.lower()


def test_summarize_long_map_reduce(monkeypatch):
    from local_agent.config import Config
    from local_agent.tools import ToolContext, data

    client = _CapturingClient(reply="partial/final summary")
    ctx2 = ToolContext(config=Config(), client=client)
    long_text = "word " * 4000  # ~20k chars → multiple chunks
    out = data._summarize_long(ctx2, long_text, "summarize", chunk_chars=3000, max_chunks=4)
    assert "summary" in out


def test_extract_pdf_prefers_pymupdf(monkeypatch):
    from local_agent.tools import data

    # PyMuPDF returns text → pypdf must NOT be consulted.
    monkeypatch.setattr(data, "_extract_with_pymupdf", lambda p, n: ("hello world", 3))

    def boom(p, n):
        raise AssertionError("pypdf should not be called when pymupdf succeeds")

    monkeypatch.setattr(data, "_extract_with_pypdf", boom)
    text, n_pages = data._extract_pdf_text(Path("/whatever.pdf"))
    assert text == "hello world" and n_pages == 3


def test_extract_pdf_falls_back_to_pypdf(monkeypatch):
    from local_agent.tools import data

    monkeypatch.setattr(data, "_extract_with_pymupdf", lambda p, n: (None, 0))
    monkeypatch.setattr(data, "_extract_with_pypdf", lambda p, n: ("from pypdf", 1))
    text, n_pages = data._extract_pdf_text(Path("/whatever.pdf"))
    assert text == "from pypdf" and n_pages == 1


def test_web_scrape_render_without_remote(reg, ctx):
    out = reg["web_scrape"].fn({"url": "https://example.com", "render": True}, ctx)
    assert "AGENT_BROWSER_REMOTE_URL" in out


def test_web_scrape_render_uses_remote(reg, tmp_path, monkeypatch):
    from local_agent.config import Config
    from local_agent.tools import ToolContext, web

    cfg = Config()
    cfg.browser_remote_url = "http://lanbox:3000"
    ctx2 = ToolContext(config=cfg, client=None)
    monkeypatch.setattr(
        web, "_remote_render", lambda url, ctx: "<html><body>Rendered JS content</body></html>"
    )
    out = reg["web_scrape"].fn({"url": "https://spa.example", "render": True}, ctx2)
    assert "Rendered JS content" in out


def test_analyze_pdf_missing_file(reg, ctx):
    out = reg["analyze_pdf"].fn({"path": "/nope/x.pdf"}, ctx)
    assert "not found" in out


def test_analyze_pdf_ocr_fallback_for_scanned(reg, tmp_path, monkeypatch):
    """A PDF with no extractable text should render pages and OCR via vision."""
    from local_agent.config import Config
    from local_agent.tools import ToolContext, data

    cfg = Config()
    cfg.vault_path = tmp_path / "vault"
    cfg.vault_path.mkdir()
    client = _CapturingClient(reply="INVOICE total 42.00")
    ctx2 = ToolContext(config=cfg, client=client)

    fake_pdf = tmp_path / "scan.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4 scanned")

    # No extractable text → triggers OCR path.
    monkeypatch.setattr(data, "_extract_pdf_text", lambda p, max_pages=50: ("", 2))
    # Pretend the renderer produced two page images.
    monkeypatch.setattr(data, "_render_pdf_pages", lambda p, n: [b"png1", b"png2"])

    out = reg["analyze_pdf"].fn({"path": str(fake_pdf), "task": "transcribe"}, ctx2)
    assert "OCR'd" in out
    assert "INVOICE total 42.00" in out
    # Vision model was used with an image payload.
    assert client.last["images"] and client.last["model"] == cfg.vision_model


def test_analyze_pdf_scanned_no_renderer(reg, tmp_path, monkeypatch):
    from local_agent.config import Config
    from local_agent.tools import ToolContext, data

    cfg = Config()
    cfg.vault_path = tmp_path / "vault"
    cfg.vault_path.mkdir()
    client = _CapturingClient()
    ctx2 = ToolContext(config=cfg, client=client)
    fake_pdf = tmp_path / "scan.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4 scanned")

    monkeypatch.setattr(data, "_extract_pdf_text", lambda p, max_pages=50: ("", 1))

    def no_backend(p, n):
        raise RuntimeError("no-backend")

    monkeypatch.setattr(data, "_render_pdf_pages", no_backend)
    out = reg["analyze_pdf"].fn({"path": str(fake_pdf)}, ctx2)
    assert "pymupdf" in out.lower()


def test_analyze_pdf_extract_failure_routes_to_ocr(reg, ctx, tmp_path, monkeypatch):
    """If text extraction fails (no backend, or encrypted/corrupt for the parser
    — the 'codec error' case), analyze_pdf should fall back to the OCR path
    rather than dead-ending."""
    from local_agent.tools import data

    fake = tmp_path / "doc.pdf"
    fake.write_bytes(b"%PDF-1.4 fake")

    def boom(p, max_pages=50):
        raise ValueError("codec error")  # simulate the parser choking

    monkeypatch.setattr(data, "_extract_pdf_text", boom)
    # ctx.client is None → OCR path reports graceful (no vision client) instead
    # of surfacing the raw codec error.
    out = reg["analyze_pdf"].fn({"path": str(fake)}, ctx)
    assert "vision client" in out or "OCR" in out or "scanned" in out


def test_analyze_pdf_no_backend_hint(reg, tmp_path, monkeypatch):
    """No text backend AND no renderer → a clear install hint (Termux-friendly)."""
    from local_agent.tools import data

    fake = tmp_path / "doc.pdf"
    fake.write_bytes(b"%PDF-1.4 fake")
    client = _CapturingClient()  # has a vision client so we reach the renderer step
    from local_agent.config import Config
    from local_agent.tools import ToolContext

    cfg = Config()
    cfg.vault_path = tmp_path / "vault"
    cfg.vault_path.mkdir()
    ctx3 = ToolContext(config=cfg, client=client)

    monkeypatch.setattr(data, "_extract_pdf_text", lambda p, max_pages=50: ("", 0))

    def no_render(p, n):
        raise RuntimeError("no-backend")

    monkeypatch.setattr(data, "_render_pdf_pages", no_render)
    out = reg["analyze_pdf"].fn({"path": str(fake)}, ctx3)
    assert "pdf2image" in out and "poppler" in out


class _CapturingClient:
    """Records the kwargs of the last generate() call; returns a canned reply."""

    def __init__(self, reply="a cat sitting on a mat"):
        self.reply = reply
        self.last = None

    def generate(self, prompt, **kwargs):
        self.last = {"prompt": prompt, **kwargs}
        return self.reply


def test_analyze_image_sends_image_and_unloads(reg, tmp_path):
    from local_agent.config import Config
    from local_agent.tools import ToolContext

    cfg = Config()
    cfg.vault_path = tmp_path / "vault"
    cfg.vault_path.mkdir()
    cfg.max_image_px = 0  # skip Pillow path for a deterministic test
    client = _CapturingClient()
    ctx2 = ToolContext(config=cfg, client=client)

    img = tmp_path / "pic.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\nfake-image-bytes")
    out = reg["analyze_image"].fn(
        {"path": str(img), "question": "What is in this image?"}, ctx2
    )
    assert out == "a cat sitting on a mat"
    # Image must go top-level (not in options), model = vision model, and the
    # model must be told to unload (keep_alive=0) so it doesn't sit by the 4B.
    assert client.last["images"] and isinstance(client.last["images"], list)
    assert client.last["model"] == cfg.vision_model
    assert client.last["keep_alive"] == cfg.vision_keep_alive


def test_analyze_image_ocr_uses_tesseract_not_vision(reg, tmp_path, monkeypatch):
    """A receipt question must read REAL text via Tesseract, not hallucinate via
    the vision model (the $6.99/$26.94 failure)."""
    from local_agent.config import Config
    from local_agent.tools import ToolContext, data

    cfg = Config()
    cfg.vault_path = tmp_path / "vault"
    cfg.vault_path.mkdir()
    client = _CapturingClient(reply="Egg Fried Rice is $7.30; SubTotal $16.14")
    ctx2 = ToolContext(config=cfg, client=client)
    img = tmp_path / "receipt.jpg"
    img.write_bytes(b"\xff\xd8\xff fake-jpeg")

    # Tesseract returns the REAL receipt text; vision must NOT be called.
    monkeypatch.setattr(
        data, "_tesseract_ocr",
        lambda raw, ctx: "Egg Fried Rice with Grilled Chicken 7.30\nSubTotal $16.14",
    )

    def no_vision(*a, **k):
        raise AssertionError("vision model must not be used when Tesseract has text")

    monkeypatch.setattr(data, "_vision_ocr", no_vision)
    out = reg["analyze_image"].fn(
        {"path": str(img), "question": "What is the total amount on this receipt?"}, ctx2
    )
    assert "16.14" in out
    # The LLM was asked to answer strictly over the OCR text.
    assert "7.30" in client.last["prompt"] or "16.14" in client.last["prompt"]


def test_analyze_image_transcribe_returns_raw_ocr(reg, tmp_path, monkeypatch):
    from local_agent.config import Config
    from local_agent.tools import ToolContext, data

    cfg = Config()
    cfg.vault_path = tmp_path / "vault"
    cfg.vault_path.mkdir()
    ctx2 = ToolContext(config=cfg, client=None)  # no model needed for raw transcribe
    img = tmp_path / "label.png"
    img.write_bytes(b"\x89PNG fake")
    monkeypatch.setattr(data, "_tesseract_ocr", lambda raw, ctx: "SERIAL ABC-123")
    out = reg["analyze_image"].fn(
        {"path": str(img), "question": "transcribe all text"}, ctx2
    )
    assert out == "SERIAL ABC-123"


def test_analyze_image_describe_uses_vision(reg, tmp_path, monkeypatch):
    """A non-text question should use the vision model, not OCR."""
    from local_agent.config import Config
    from local_agent.tools import ToolContext, data

    cfg = Config()
    cfg.vault_path = tmp_path / "vault"
    cfg.vault_path.mkdir()
    client = _CapturingClient(reply="a cat on a sofa")
    ctx2 = ToolContext(config=cfg, client=client)
    img = tmp_path / "photo.jpg"
    img.write_bytes(b"\xff\xd8\xff fake")

    def no_tess(raw, ctx):
        raise AssertionError("describe must not call tesseract")

    monkeypatch.setattr(data, "_tesseract_ocr", no_tess)
    monkeypatch.setattr(data, "_vision_ocr", lambda ctx, raw, q: "a cat on a sofa")
    out = reg["analyze_image"].fn(
        {"path": str(img), "question": "Describe what is in this photo"}, ctx2
    )
    assert "cat" in out


def test_analyze_image_missing_file(reg, ctx):
    out = reg["analyze_image"].fn({"path": "/nope/x.png"}, ctx)
    assert "not found" in out


def test_analyze_image_rejects_non_image(reg, ctx, tmp_path):
    txt = tmp_path / "note.txt"
    txt.write_text("hi")
    out = reg["analyze_image"].fn({"path": str(txt)}, ctx)
    assert "not a recognized image type" in out


def test_vault_list(reg, ctx):
    write = reg["vault_write"].fn
    write({"path": "folder/x.md", "content": "x", "mode": "create"}, ctx)
    out = reg["vault_list"].fn({"folder": "folder"}, ctx)
    assert "x.md" in out


def test_vault_path_escape_blocked(reg, ctx):
    out = reg["vault_read"].fn({"path": "../../etc/passwd"}, ctx)
    # Either escape error or not-found; must NOT leak host file contents.
    assert "root:" not in out


def test_validate_missing_required(reg):
    err = reg["vault_write"].validate({"path": "a.md"})  # missing content
    assert err and "content" in err


def test_make_html_report(reg, ctx, tmp_path):
    out_path = tmp_path / "r.html"
    out = reg["make_html_report"].fn(
        {
            "title": "T",
            "sections": [{"heading": "H", "body": "B"}],
            "out_path": str(out_path),
        },
        ctx,
    )
    assert out_path.exists()
    assert "<h1>T</h1>" in out_path.read_text()


def test_make_slides(reg, ctx, tmp_path):
    out_path = tmp_path / "s.md"
    reg["make_slides"].fn(
        {"title": "Deck", "outline": ["one", "two"], "out_path": str(out_path)}, ctx
    )
    text = out_path.read_text()
    assert "# Deck" in text and "## one" in text


def test_rephrase_without_client(reg, ctx):
    out = reg["rephrase"].fn({"text": "hi", "style": "formal"}, ctx)
    assert "unavailable" in out
