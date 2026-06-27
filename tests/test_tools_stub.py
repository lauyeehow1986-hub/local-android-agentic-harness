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
