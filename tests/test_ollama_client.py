"""Client error handling: a slow/throttled model must surface a clean
OllamaError, never an unhandled traceback."""

from unittest import mock

import pytest

from local_agent.ollama_client import OllamaClient, OllamaError


def _client():
    return OllamaClient(base_url="http://127.0.0.1:11434", model="m", timeout_s=5)


def test_socket_timeout_becomes_ollama_error():
    c = _client()
    with mock.patch("urllib.request.urlopen", side_effect=TimeoutError("timed out")):
        with pytest.raises(OllamaError) as ei:
            c.generate("hi")
    assert "timed out" in str(ei.value).lower()


def test_oserror_becomes_ollama_error():
    c = _client()
    with mock.patch("urllib.request.urlopen", side_effect=OSError("boom")):
        with pytest.raises(OllamaError):
            c.generate("hi")


def test_num_predict_in_payload():
    c = OllamaClient(base_url="http://x", model="m", num_predict=256)
    captured = {}

    def fake_post(path, payload):
        captured.update(payload)
        return {"response": "ok"}

    with mock.patch.object(c, "_post", side_effect=fake_post):
        out = c.generate("hi")
    assert out == "ok"
    assert captured["options"]["num_predict"] == 256
