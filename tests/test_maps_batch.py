"""maps + open_app tools and the batch (cron) runner."""

from pathlib import Path

from local_agent.config import Config
from local_agent.tools import ToolContext, build_registry


def _reg():
    return build_registry()


def _ctx(tmp_path):
    cfg = Config()
    cfg.vault_path = tmp_path / "vault"
    cfg.vault_path.mkdir()
    return ToolContext(config=cfg, client=None)


def test_maps_search(monkeypatch, tmp_path):
    from local_agent.tools import maps as maps_mod

    monkeypatch.setattr(
        maps_mod,
        "_geocode",
        lambda q: {"name": "Marina Bay Sands, Singapore", "lat": 1.2834, "lon": 103.8607},
    )
    out = _reg()["maps"].fn({"query": "Marina Bay Sands"}, _ctx(tmp_path))
    assert "Marina Bay Sands" in out
    assert "google.com/maps/search" in out
    assert "1.28340" in out


def test_maps_directions(monkeypatch, tmp_path):
    from local_agent.tools import maps as maps_mod

    monkeypatch.setattr(
        maps_mod, "_geocode", lambda q: {"name": q, "lat": 1.30, "lon": 103.80}
    )
    monkeypatch.setattr(
        maps_mod,
        "_get_json",
        lambda url, timeout=20: {"routes": [{"distance": 5000, "duration": 600}]},
    )
    out = _reg()["maps"].fn({"origin": "A", "destination": "B"}, _ctx(tmp_path))
    assert "5.0 km" in out and "10 min" in out
    assert "google.com/maps/dir" in out


def test_maps_requires_args(tmp_path):
    out = _reg()["maps"].fn({}, _ctx(tmp_path))
    assert "provide" in out


def test_open_app_no_opener(monkeypatch, tmp_path):
    from local_agent.tools import system as sys_mod

    monkeypatch.setattr(sys_mod.shutil, "which", lambda name: None)
    out = _reg()["open_app"].fn({"target": "https://food.grab.com"}, _ctx(tmp_path))
    assert "termux-api" in out


def test_open_app_uses_opener(monkeypatch, tmp_path):
    from local_agent.tools import system as sys_mod

    calls = {}
    monkeypatch.setattr(sys_mod.shutil, "which", lambda name: "/usr/bin/termux-open-url" if name == "termux-open-url" else None)

    def fake_run(cmd, **kw):
        calls["cmd"] = cmd
        class R:  # minimal CompletedProcess stand-in
            returncode = 0
            stdout = ""
            stderr = ""
        return R()

    monkeypatch.setattr(sys_mod.subprocess, "run", fake_run)
    out = _reg()["open_app"].fn({"target": "https://food.grab.com/sg"}, _ctx(tmp_path))
    assert "opened" in out
    assert calls["cmd"][0].endswith("termux-open-url")


def test_open_app_requires_target(tmp_path):
    out = _reg()["open_app"].fn({}, _ctx(tmp_path))
    assert "required" in out


# --- batch runner ---------------------------------------------------------

class _FakeClient:
    def __init__(self, mapping):
        self.mapping = mapping

    def generate(self, prompt, system=None, model=None, options=None, on_token=None):
        for k, v in self.mapping.items():
            if k in prompt:
                return v
        return "THOUGHT: done\nFINAL: ok"

    def health(self):
        return True


def _agent(tmp_path):
    from local_agent.loop import Agent

    cfg = Config()
    cfg.vault_path = tmp_path / "vault"
    cfg.vault_path.mkdir()
    cfg.log_path = tmp_path / "agent.log"
    cfg.stream = False
    cfg.system_prompt_path = (
        Path(__file__).resolve().parent.parent / "prompts" / "agent_system.md"
    )
    client = _FakeClient(
        {
            "capital of France": "THOUGHT: known\nFINAL: Paris",
            "12 times 11": "THOUGHT: math\nFINAL: 132",
        }
    )
    return Agent(config=cfg, client=client)


def test_batch_runs_all_tasks(tmp_path):
    from local_agent import batch

    agent = _agent(tmp_path)
    results = batch.run_batch(
        agent, ["What is the capital of France?", "What is 12 times 11?"], auto="deny"
    )
    finals = {t: f for t, f in results}
    assert finals["What is the capital of France?"] == "Paris"
    assert finals["What is 12 times 11?"] == "132"


def test_batch_load_tasks_txt(tmp_path):
    from local_agent import batch

    f = tmp_path / "tasks.txt"
    f.write_text("# a comment\nfirst task\n\nsecond task\n")
    tasks = batch.load_tasks(str(f))
    assert tasks == ["first task", "second task"]


def test_batch_load_tasks_json(tmp_path):
    from local_agent import batch

    f = tmp_path / "tasks.json"
    f.write_text('["one", "two"]')
    assert batch.load_tasks(str(f)) == ["one", "two"]


def test_batch_frontend_auto_deny_then_approve():
    from local_agent.batch import BatchFrontend

    assert BatchFrontend(auto="deny").ask_approval("x", "y") == "n"
    assert BatchFrontend(auto="approve").ask_approval("x", "y") == "y"
