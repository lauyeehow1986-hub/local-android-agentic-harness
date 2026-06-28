"""Thin Ollama client over the stdlib (urllib) — no `requests`/`ollama` dep.

Kept deliberately small. The base_url is configurable so the same client talks
to local Ollama now and a Tailscale LAN box (30B-A3B) later — a config change,
not a rewrite.
"""

from __future__ import annotations

import json
import socket
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
        num_predict: int = 512,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.num_ctx = num_ctx
        self.temperature = temperature
        self.timeout_s = timeout_s
        # Hard cap on tokens generated per turn. A single ReAct block is short;
        # this bounds worst-case decode time so a rambling generation can't burn
        # the whole timeout at ~3 tok/s.
        self.num_predict = num_predict

    # -- internal ---------------------------------------------------------
    def _request(self, path: str, payload: dict) -> urllib.request.Request:
        url = f"{self.base_url}{path}"
        data = json.dumps(payload).encode("utf-8")
        return urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}, method="POST"
        )

    def _translate_error(self, e: Exception, path: str, payload: dict) -> "OllamaError":
        """Map low-level urllib/socket failures to a clear OllamaError."""
        if isinstance(e, urllib.error.HTTPError):
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
            return OllamaError(f"Ollama returned HTTP {e.code} for {path}: {detail}{hint}")
        if isinstance(e, (TimeoutError, socket.timeout)):
            # The model is decoding slower than request_timeout_s allows (heavy
            # thermal throttling on this chip). Surface it cleanly.
            return OllamaError(
                f"request to Ollama timed out after {self.timeout_s}s — the model is "
                "decoding very slowly (thermal throttling / RAM pressure). Try a "
                "smaller model, a lower AGENT_NUM_CTX, the compact prompt, streaming, "
                "or raise AGENT_REQUEST_TIMEOUT."
            )
        if isinstance(e, urllib.error.URLError):
            reason = getattr(e, "reason", e)
            if isinstance(reason, (TimeoutError, socket.timeout)):
                return OllamaError(
                    f"request to Ollama timed out after {self.timeout_s}s "
                    "(thermal throttling / RAM pressure)."
                )
            return OllamaError(f"cannot reach Ollama at {self.base_url}{path}: {e}")
        if isinstance(e, OSError):
            return OllamaError(f"network error talking to Ollama at {self.base_url}{path}: {e}")
        return OllamaError(f"unexpected error talking to Ollama: {e}")

    def _post(self, path: str, payload: dict) -> dict:
        req = self._request(path, payload)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                body = resp.read().decode("utf-8")
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as e:
            raise self._translate_error(e, path, payload) from e
        try:
            return json.loads(body)
        except json.JSONDecodeError as e:
            raise OllamaError(f"bad JSON from Ollama: {e}: {body[:200]}") from e

    def _post_stream(self, path: str, payload: dict, on_token) -> str:
        """Stream NDJSON chunks from Ollama, forwarding each token to on_token.

        Returns the full accumulated text. Streaming also keeps the socket
        active, so a slow-but-steady decode won't trip the read timeout the way
        one long blocking request does.
        """
        req = self._request(path, payload)
        pieces: list[str] = []
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                for line in resp:
                    if not line.strip():
                        continue
                    try:
                        chunk = json.loads(line.decode("utf-8"))
                    except json.JSONDecodeError:
                        continue
                    if "error" in chunk:
                        raise OllamaError(f"Ollama stream error: {chunk['error']}")
                    tok = chunk.get("response", "")
                    if tok:
                        pieces.append(tok)
                        try:
                            on_token(tok)
                        except Exception:  # noqa: BLE001 - display must not kill gen
                            pass
                    if chunk.get("done"):
                        break
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as e:
            raise self._translate_error(e, path, payload) from e
        return "".join(pieces)

    # -- public -----------------------------------------------------------
    def generate(
        self,
        prompt: str,
        *,
        system: Optional[str] = None,
        model: Optional[str] = None,
        options: Optional[dict[str, Any]] = None,
        on_token=None,
        images: Optional[list[str]] = None,
        keep_alive: Optional[Any] = None,
    ) -> str:
        """Single-shot completion returning the full text.

        Uses /api/generate with the ReAct transcript as the prompt. The system
        prompt is passed separately. If on_token is given, streams chunks to it
        (the terminal renders them live) while still returning the full text.

        `images` is a list of base64-encoded images for a vision model — Ollama
        expects it at the top level of the request, NOT inside options.
        `keep_alive` controls how long the model stays resident (e.g. 0 to unload
        immediately after — used for the vision model so it never sits alongside
        the 4B in RAM).
        """
        opts = {
            "num_ctx": self.num_ctx,
            "temperature": self.temperature,
            "num_predict": self.num_predict,
            # Stop as soon as the model starts hallucinating an OBSERVATION;
            # the parser also guards against this, but stopping early saves
            # precious tokens at ~3 tok/s.
            "stop": ["\nOBSERVATION:", "OBSERVATION:"],
        }
        if options:
            opts.update(options)
        streaming = on_token is not None
        payload: dict[str, Any] = {
            "model": model or self.model,
            "prompt": prompt,
            "stream": streaming,
            "options": opts,
        }
        if system:
            payload["system"] = system
        if images:
            payload["images"] = images
        if keep_alive is not None:
            payload["keep_alive"] = keep_alive
        if streaming:
            return self._post_stream("/api/generate", payload, on_token)
        result = self._post("/api/generate", payload)
        return result.get("response", "")

    def embed(self, text: str, model: str) -> list[float]:
        """Return an embedding vector for `text` using an embedding model
        (e.g. nomic-embed-text). Used for semantic vault search."""
        result = self._post("/api/embeddings", {"model": model, "prompt": text})
        vec = result.get("embedding")
        if not isinstance(vec, list):
            raise OllamaError(f"no embedding returned (is '{model}' pulled?)")
        return vec

    def health(self) -> bool:
        """True if the Ollama server answers and has the model available."""
        url = f"{self.base_url}/api/tags"
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                json.loads(resp.read().decode("utf-8"))
            return True
        except (urllib.error.URLError, json.JSONDecodeError):
            return False
