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
    "vault_read",
    "vault_list",
    "vault_write",
    "web_search",
    "web_scrape",
    "browser",
    "analyze_data",
    "analyze_image",
    "analyze_pdf",
    "transcribe",
    "make_slides",
    "make_html_report",
    "rephrase",
    "shell",
    "git_sync",
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


def test_analyze_pdf_graceful_without_pypdf(reg, ctx, tmp_path, monkeypatch):
    # Simulate pypdf not installed: the import inside _extract_pdf_text raises.
    import builtins

    fake = tmp_path / "doc.pdf"
    fake.write_bytes(b"%PDF-1.4 fake")
    real_import = builtins.__import__

    def blocked(name, *a, **k):
        if name == "pypdf":
            raise ImportError("no pypdf")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", blocked)
    out = reg["analyze_pdf"].fn({"path": str(fake)}, ctx)
    assert "install pypdf" in out


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
