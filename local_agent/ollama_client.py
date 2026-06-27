"""Thin Ollama client over the stdlib (urllib) — no `requests`/`ollama` dep.

Kept deliberately small. The base_url is configurable so the same client talks
to local Ollama now and a Tailscale LAN box (30B-A3B) later — a config change,
not a rewrite.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Optional


class OllamaError(RuntimeError):
    """Raised when the Ollama HTTP API cannot be reached or returns an error."""


class OllamaClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        num_ctx: int = 6144,
        temperature: float = 0.2,
        timeout_s: int = 600,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.num_ctx = num_ctx
        self.temperature = temperature
        self.timeout_s = timeout_s

    # -- internal ---------------------------------------------------------
    def _post(self, path: str, payload: dict) -> dict:
        url = f"{self.base_url}{path}"
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                body = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            # Server IS reachable but returned an error status. Surface its body
            # — a 404 here almost always means the model isn't pulled, and
            # Ollama puts {"error": "model 'X' not found"} in the response.
            detail = ""
            try:
                detail = e.read().decode("utf-8", errors="ignore")[:300]
            except Exception:  # noqa: BLE001
                pass
            hint = ""
            if e.code == 404 and "not found" in detail.lower():
                hint = (
                    f" — model '{payload.get('model', '?')}' is not pulled. "
                    f"Run: ollama pull {payload.get('model', '?')}"
                )
            raise OllamaError(
                f"Ollama returned HTTP {e.code} for {path}: {detail}{hint}"
            ) from e
        except urllib.error.URLError as e:
            raise OllamaError(f"cannot reach Ollama at {url}: {e}") from e
        try:
            return json.loads(body)
        except json.JSONDecodeError as e:
            raise OllamaError(f"bad JSON from Ollama: {e}: {body[:200]}") from e

    # -- public -----------------------------------------------------------
    def generate(
        self,
        prompt: str,
        *,
        system: Optional[str] = None,
        model: Optional[str] = None,
        options: Optional[dict[str, Any]] = None,
    ) -> str:
        """Single-shot completion. Non-streaming (we want the whole block).

        Uses /api/generate with the ReAct transcript as the prompt. The system
        prompt (agent_system.md) is passed separately.
        """
        opts = {
            "num_ctx": self.num_ctx,
            "temperature": self.temperature,
            # Stop as soon as the model starts hallucinating an OBSERVATION;
            # the parser also guards against this, but stopping early saves
            # precious tokens at ~3 tok/s.
            "stop": ["\nOBSERVATION:", "OBSERVATION:"],
        }
        if options:
            opts.update(options)
        payload: dict[str, Any] = {
            "model": model or self.model,
            "prompt": prompt,
            "stream": False,
            "options": opts,
        }
        if system:
            payload["system"] = system
        result = self._post("/api/generate", payload)
        return result.get("response", "")

    def health(self) -> bool:
        """True if the Ollama server answers and has the model available."""
        url = f"{self.base_url}/api/tags"
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                json.loads(resp.read().decode("utf-8"))
            return True
        except (urllib.error.URLError, json.JSONDecodeError):
            return False
