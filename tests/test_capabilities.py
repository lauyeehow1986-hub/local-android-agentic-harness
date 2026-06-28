"""Semantic search, device tools, research, and data/stats upgrades."""

from pathlib import Path

from local_agent.config import Config
from local_agent.tools import ToolContext, build_registry


def _reg():
    return build_registry()


def _ctx(tmp_path, client=None):
    cfg = Config()
    cfg.vault_path = tmp_path / "vault"
    cfg.vault_path.mkdir()
    return ToolContext(config=cfg, client=client)


# --- semantic vault search ------------------------------------------------

class _EmbedClient:
    """Deterministic toy embeddings: vector from word presence, for cosine math."""

    VOCAB = ["recurrent", "andersen", "gill", "omop", "date", "mapping", "bus", "deploy"]

    def embed(self, text, model):
        t = text.lower()
        return [1.0 if w in t else 0.0 for w in self.VOCAB]

    def generate(self, *a, **k):
        return "THOUGHT: x\nFINAL: ok"

    def health(self):
        return True


def test_semantic_search_end_to_end(tmp_path):
    from local_agent import vault_index

    client = _EmbedClient()
    ctx = _ctx(tmp_path, client=client)
    ctx.config.vault_index_path = tmp_path / "idx.json"
    (ctx.config.vault_path / "stats.md").write_text("Andersen-Gill model for recurrent events")
    (ctx.config.vault_path / "omop.md").write_text("OMOP date mapping from visit start")
    (ctx.config.vault_path / "bus.md").write_text("deploy bus tracker")

    vault_index.build_index(ctx.config, client, rebuild=True)
    out = _reg()["vault_semantic_search"].fn({"query": "recurrent events andersen", "limit": 2}, ctx)
    assert "stats.md" in out.splitlines()[0]   # most semantically similar first


def test_semantic_search_no_index(tmp_path):
    ctx = _ctx(tmp_path, client=_EmbedClient())
    ctx.config.vault_index_path = tmp_path / "missing.json"
    out = _reg()["vault_semantic_search"].fn({"query": "x"}, ctx)
    assert "vault_index" in out


def test_chunk_and_cosine():
    from local_agent import vault_index

    chunks = vault_index._chunk("a" * 2000, size=800, overlap=100)
    assert len(chunks) >= 2
    assert abs(vault_index._cosine([1, 0], [1, 0]) - 1.0) < 1e-9
    assert abs(vault_index._cosine([1, 0], [0, 1])) < 1e-9


# --- device tools (graceful without termux-api) ---------------------------

def test_clipboard_read_no_termux(tmp_path, monkeypatch):
    from local_agent.tools import device

    monkeypatch.setattr(device.shutil, "which", lambda n: None)
    out = _reg()["clipboard"].fn({"mode": "read"}, _ctx(tmp_path))
    assert "termux-api" in out


def test_notify_no_termux(tmp_path, monkeypatch):
    from local_agent.tools import device

    monkeypatch.setattr(device.shutil, "which", lambda n: None)
    out = _reg()["notify"].fn({"title": "hi", "content": "x"}, _ctx(tmp_path))
    assert "termux-api" in out


def test_speak_requires_text(tmp_path, monkeypatch):
    from local_agent.tools import device

    monkeypatch.setattr(device.shutil, "which", lambda n: "/usr/bin/termux-tts-speak")
    out = _reg()["speak"].fn({}, _ctx(tmp_path))
    assert "required" in out


def test_clipboard_write(tmp_path, monkeypatch):
    from local_agent.tools import device

    monkeypatch.setattr(device.shutil, "which", lambda n: "/usr/bin/" + n)
    monkeypatch.setattr(device, "_run", lambda cmd, **k: (0, "", ""))
    out = _reg()["clipboard"].fn({"mode": "write", "text": "hello"}, _ctx(tmp_path))
    assert "clipboard set" in out


# --- research -------------------------------------------------------------

def test_research_arxiv(tmp_path, monkeypatch):
    from local_agent.tools import research

    sample = b"""<?xml version="1.0"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry><title>Recurrent Events Survival</title>
        <summary>A method for recurrent event data.</summary>
        <id>http://arxiv.org/abs/1234.5678</id></entry>
    </feed>"""
    monkeypatch.setattr(research, "_get", lambda url, timeout=25: sample)
    out = _reg()["research"].fn({"query": "recurrent events", "source": "arxiv"}, _ctx(tmp_path))
    assert "Recurrent Events Survival" in out
    assert "arxiv.org/abs/1234.5678" in out


def test_research_requires_query(tmp_path):
    out = _reg()["research"].fn({}, _ctx(tmp_path))
    assert "required" in out


# --- data/stats upgrade ---------------------------------------------------

def test_analyze_data_richer_stats(tmp_path):
    csv_path = tmp_path / "d.csv"
    csv_path.write_text("a,b\n1,2\n2,4\n3,6\n4,8\n")
    out = _reg()["analyze_data"].fn({"path": str(csv_path)}, _ctx(tmp_path))
    assert "median" in out and "sd=" in out
    assert "corr(a,b)" in out          # perfectly correlated → ~1.00
    assert "1.00" in out


def test_analyze_data_plot_skips_without_matplotlib(tmp_path, monkeypatch):
    import builtins

    csv_path = tmp_path / "d.csv"
    csv_path.write_text("a\n1\n2\n3\n")
    real_import = builtins.__import__

    def blocked(name, *a, **k):
        if name == "matplotlib":
            raise ImportError("no matplotlib")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", blocked)
    out = _reg()["analyze_data"].fn(
        {"path": str(csv_path), "plot": str(tmp_path / "p.png")}, _ctx(tmp_path)
    )
    assert "matplotlib" in out
