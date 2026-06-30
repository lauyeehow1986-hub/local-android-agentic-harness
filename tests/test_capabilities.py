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


def test_latest_file_picks_newest(tmp_path):
    import os
    import time

    folder = tmp_path / "dl"
    folder.mkdir()
    old = folder / "old.png"
    old.write_bytes(b"x")
    time.sleep(0.01)
    new = folder / "new.png"
    new.write_bytes(b"y")
    # make 'new' definitively newer
    os.utime(new, (time.time() + 10, time.time() + 10))
    out = _reg()["latest_file"].fn({"folder": str(folder)}, _ctx(tmp_path))
    assert out == str(new)


def test_latest_file_filters_by_type(tmp_path):
    import os
    import time

    folder = tmp_path / "dl"
    folder.mkdir()
    img = folder / "a.png"
    img.write_bytes(b"x")
    pdf = folder / "b.pdf"
    pdf.write_bytes(b"y")
    os.utime(pdf, (time.time() + 10, time.time() + 10))  # pdf is newest overall
    # ask for the newest IMAGE → must skip the newer pdf
    out = _reg()["latest_file"].fn({"folder": str(folder), "type": "image"}, _ctx(tmp_path))
    assert out == str(img)


def test_latest_file_missing_folder(tmp_path):
    out = _reg()["latest_file"].fn({"folder": str(tmp_path / "nope")}, _ctx(tmp_path))
    assert "folder not found" in out


def test_voice_dictation_appends_to_note(tmp_path):
    from local_agent import voice
    from local_agent.loop import Agent

    cfg = Config()
    cfg.vault_path = tmp_path / "vault"
    cfg.vault_path.mkdir()
    cfg.log_path = tmp_path / "agent.log"
    cfg.system_prompt_path = (
        Path(__file__).resolve().parent.parent / "prompts" / "agent_system.md"
    )

    class _C:
        def generate(self, *a, **k):
            return ""

        def health(self):
            return True

    agent = Agent(config=cfg, client=_C())
    res = voice._append_dictation(agent, "remember to email the lab", "Notes/voice.md")
    assert "voice.md" in res                  # created on first dictation, appended after
    saved = (cfg.vault_path / "Notes" / "voice.md").read_text()
    assert "remember to email the lab" in saved
    assert saved.strip().startswith("- [")   # timestamped bullet


def test_voice_dictation_default_note_is_today():
    from local_agent import voice
    import datetime

    note = voice._dictation_note("")
    assert note == f"Daily/{datetime.date.today().isoformat()}.md"
    assert voice._dictation_note("X/y.md") == "X/y.md"


def test_voice_spoken_form_summarizes_long(tmp_path):
    from local_agent import voice
    from local_agent.loop import Agent

    cfg = Config()
    cfg.vault_path = tmp_path / "vault"
    cfg.vault_path.mkdir()
    cfg.log_path = tmp_path / "agent.log"
    cfg.system_prompt_path = (
        Path(__file__).resolve().parent.parent / "prompts" / "agent_system.md"
    )

    class _C:
        def generate(self, *a, **k):
            return "Short spoken version."

        def health(self):
            return True

    agent = Agent(config=cfg, client=_C())
    long = "word " * 200
    assert voice._spoken_form(agent, long, full=False) == "Short spoken version."
    # short answers and --full-speech are spoken verbatim
    assert voice._spoken_form(agent, "hi there", full=False) == "hi there"
    assert voice._spoken_form(agent, long, full=True) == long


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
