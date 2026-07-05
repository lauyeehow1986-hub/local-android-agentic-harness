"""Streaming display + hybrid local/remote routing."""

from pathlib import Path

from local_agent.config import Config
from local_agent.loop import Agent


def _config(tmp_path):
    cfg = Config()
    cfg.vault_path = tmp_path / "vault"
    cfg.vault_path.mkdir()
    cfg.log_path = tmp_path / "agent.log"
    cfg.system_prompt_path = (
        Path(__file__).resolve().parent.parent / "prompts" / "agent_system.md"
    )
    return cfg


class StreamingClient:
    """Emits each canned output one token (word) at a time via on_token."""

    def __init__(self, outputs):
        self.outputs = list(outputs)

    def generate(self, prompt, system=None, model=None, options=None, on_token=None):
        text = self.outputs.pop(0) if self.outputs else "THOUGHT: x\nFINAL: done"
        if on_token is not None:
            for tok in text.split(" "):
                on_token(tok + " ")
        return text

    def health(self):
        return True


class StreamFrontend:
    def __init__(self):
        self.tokens = []
        self.began = self.ended = 0
        self.finals = []
        self.infos = []

    def stream_begin(self):
        self.began += 1

    def on_token(self, text):
        self.tokens.append(text)

    def stream_end(self):
        self.ended += 1

    def on_action(self, name, args):
        pass

    def on_observation(self, text):
        pass

    def on_final(self, text):
        self.finals.append(text)

    def on_info(self, text):
        self.infos.append(text)

    def ask_approval(self, action, details):
        return "n"


def test_streaming_forwards_tokens(tmp_path):
    cfg = _config(tmp_path)
    client = StreamingClient(["THOUGHT: done\nFINAL: the answer is here"])
    agent = Agent(config=cfg, client=client)
    fe = StreamFrontend()
    result = agent.run_task("q", fe)
    assert result == "the answer is here"
    assert fe.began == 1 and fe.ended == 1
    assert "".join(fe.tokens).strip() == "THOUGHT: done\nFINAL: the answer is here"


class RoutingClient:
    def __init__(self, name, healthy=True):
        self.name = name
        self.healthy = healthy

    def generate(self, *a, **k):
        return f"THOUGHT: x\nFINAL: {self.name}"

    def health(self):
        return self.healthy


def test_route_local_uses_local(tmp_path):
    cfg = _config(tmp_path)
    agent = Agent(
        config=cfg,
        client=RoutingClient("local"),
        remote_client=RoutingClient("remote"),
    )
    agent.route = "local"
    assert agent._active_client().name == "local"


def test_route_remote_uses_remote(tmp_path):
    cfg = _config(tmp_path)
    agent = Agent(
        config=cfg,
        client=RoutingClient("local"),
        remote_client=RoutingClient("remote"),
    )
    agent.route = "remote"
    assert agent._active_client().name == "remote"


def test_route_auto_prefers_remote_when_healthy(tmp_path):
    cfg = _config(tmp_path)
    agent = Agent(
        config=cfg,
        client=RoutingClient("local"),
        remote_client=RoutingClient("remote", healthy=True),
    )
    agent.route = "auto"
    assert agent._active_client().name == "remote"


def test_route_auto_falls_back_when_remote_down(tmp_path):
    cfg = _config(tmp_path)
    agent = Agent(
        config=cfg,
        client=RoutingClient("local"),
        remote_client=RoutingClient("remote", healthy=False),
    )
    agent.route = "auto"
    assert agent._active_client().name == "local"


def test_no_remote_always_local(tmp_path):
    cfg = _config(tmp_path)
    agent = Agent(config=cfg, client=RoutingClient("local"))
    agent.route = "auto"
    assert agent._active_client().name == "local"
