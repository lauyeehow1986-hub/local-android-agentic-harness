"""End-to-end loop with a scripted fake model client and a capturing frontend.
No Ollama, no network — exercises prompt -> parse -> dispatch -> observe -> FINAL,
the JSON re-prompt path, and the approval gate.
"""

from pathlib import Path

from local_agent.config import Config
from local_agent.loop import Agent


class FakeClient:
    """Returns canned model outputs in sequence, ignoring the prompt."""

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = 0

    def generate(self, prompt, system=None, model=None, options=None):
        self.calls += 1
        if self.outputs:
            return self.outputs.pop(0)
        return "THOUGHT: nothing left\nFINAL: done"

    def health(self):
        return True


class CapturingFrontend:
    def __init__(self, approvals=None):
        self.thoughts = []
        self.actions = []
        self.observations = []
        self.finals = []
        self.infos = []
        self.approvals = list(approvals or [])

    def on_thought(self, text):
        self.thoughts.append(text)

    def on_action(self, name, args):
        self.actions.append((name, args))

    def on_observation(self, text):
        self.observations.append(text)

    def on_final(self, text):
        self.finals.append(text)

    def on_info(self, text):
        self.infos.append(text)

    def ask_approval(self, action, details):
        return self.approvals.pop(0) if self.approvals else "n"


def _config(tmp_path):
    cfg = Config()
    cfg.vault_path = tmp_path / "vault"
    cfg.vault_path.mkdir()
    cfg.log_path = tmp_path / "agent.log"
    # Point the system prompt at the real one in the repo.
    cfg.system_prompt_path = Path(__file__).resolve().parent.parent / "prompts" / "agent_system.md"
    return cfg


def test_simple_read_then_final(tmp_path):
    cfg = _config(tmp_path)
    (cfg.vault_path / "a.md").write_text("the secret is 42")
    client = FakeClient(
        [
            'THOUGHT: read it\nACTION: vault_read\nINPUT: {"path": "a.md"}',
            "THOUGHT: got it\nFINAL: the secret is 42",
        ]
    )
    agent = Agent(config=cfg, client=client)
    fe = CapturingFrontend()
    result = agent.run_task("what is in a.md?", fe)
    assert result == "the secret is 42"
    assert fe.actions[0][0] == "vault_read"
    assert "the secret is 42" in fe.observations[0]


def test_guarded_write_denied(tmp_path):
    cfg = _config(tmp_path)
    cfg.autonomy = "hitl"
    client = FakeClient(
        [
            'THOUGHT: write\nACTION: vault_write\nINPUT: {"path": "n.md", "content": "x", "mode": "create"}',
            "THOUGHT: ok denied\nFINAL: not written",
        ]
    )
    agent = Agent(config=cfg, client=client)
    fe = CapturingFrontend(approvals=["n"])
    agent.run_task("write a note", fe)
    assert not (cfg.vault_path / "n.md").exists()
    assert any("DENIED" in o for o in fe.observations)


def test_guarded_write_approved(tmp_path):
    cfg = _config(tmp_path)
    cfg.autonomy = "hitl"
    client = FakeClient(
        [
            'THOUGHT: write\nACTION: vault_write\nINPUT: {"path": "n.md", "content": "hi", "mode": "create"}',
            "THOUGHT: done\nFINAL: written",
        ]
    )
    agent = Agent(config=cfg, client=client)
    fe = CapturingFrontend(approvals=["y"])
    agent.run_task("write a note", fe)
    assert (cfg.vault_path / "n.md").read_text() == "hi"


def test_full_autonomy_skips_prompt(tmp_path):
    cfg = _config(tmp_path)
    cfg.autonomy = "full"
    client = FakeClient(
        [
            'THOUGHT: write\nACTION: vault_write\nINPUT: {"path": "n.md", "content": "hi", "mode": "create"}',
            "THOUGHT: done\nFINAL: written",
        ]
    )
    agent = Agent(config=cfg, client=client)
    fe = CapturingFrontend()  # no approvals provided; should not be asked
    agent.run_task("write a note", fe)
    assert (cfg.vault_path / "n.md").read_text() == "hi"


def test_bad_json_reprompts_once(tmp_path):
    cfg = _config(tmp_path)
    (cfg.vault_path / "a.md").write_text("data")
    client = FakeClient(
        [
            "THOUGHT: x\nACTION: vault_read\nINPUT: {path: a.md}",  # invalid JSON
            'THOUGHT: retry\nACTION: vault_read\nINPUT: {"path": "a.md"}',
            "THOUGHT: done\nFINAL: data",
        ]
    )
    agent = Agent(config=cfg, client=client)
    fe = CapturingFrontend()
    result = agent.run_task("read a.md", fe)
    assert result == "data"
    assert client.calls == 3


def test_step_cap(tmp_path):
    cfg = _config(tmp_path)
    cfg.max_steps = 3
    # Always emits an action, never finishes.
    client = FakeClient(
        ['THOUGHT: loop\nACTION: vault_list\nINPUT: {}'] * 10
    )
    agent = Agent(config=cfg, client=client)
    fe = CapturingFrontend()
    result = agent.run_task("loop forever", fe)
    assert "cap" in result.lower()
    assert client.calls == 3
