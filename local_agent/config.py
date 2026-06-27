"""Central configuration. No hardcoded paths in logic — everything lives here.

Every value can be overridden by an environment variable so the same code runs
on the phone (Termux) and on a desktop dev box pointed at a desktop Ollama.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env_str(name: str, default: str) -> str:
    val = os.environ.get(name)
    return val if val is not None and val != "" else default


def _env_int(name: str, default: int) -> int:
    val = os.environ.get(name)
    if val is None or val == "":
        return default
    try:
        return int(val)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    val = os.environ.get(name)
    if val is None or val == "":
        return default
    try:
        return float(val)
    except ValueError:
        return default


# The Obsidian vault path has a SPACE in it. pathlib handles that fine; any
# shell/subprocess use MUST shlex.quote it (see tools/system.py).
DEFAULT_VAULT = "/storage/emulated/0/Download/Obsidian/Yh android"


@dataclass
class Config:
    """Runtime configuration for the harness."""

    # --- Model / Ollama ---
    ollama_base_url: str = field(
        default_factory=lambda: _env_str("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    )
    model: str = field(
        default_factory=lambda: _env_str("AGENT_MODEL", "qwen3:4b-instruct-2507-q4_K_M")
    )
    # Vision model is loaded ON DEMAND for analyze_image, then unloaded. Never
    # resident alongside the 4B (see Gotchas in CLAUDE.md).
    vision_model: str = field(
        default_factory=lambda: _env_str("AGENT_VISION_MODEL", "moondream")
    )
    # num_ctx: keep small or the KV cache OOM-kills on 8 GB. NOT the 256K max.
    num_ctx: int = field(default_factory=lambda: _env_int("AGENT_NUM_CTX", 6144))
    temperature: float = field(
        default_factory=lambda: _env_float("AGENT_TEMPERATURE", 0.2)
    )
    # Halves KV-cache RAM at negligible quality cost. Passed to ollama serve as
    # OLLAMA_KV_CACHE_TYPE; recorded here for documentation / health checks.
    kv_cache_type: str = field(
        default_factory=lambda: _env_str("OLLAMA_KV_CACHE_TYPE", "q8_0")
    )
    # Decode is ~3 tok/s CPU-only and throttles under load; don't assume
    # cold-start speed. Generous, and bumped for the worst case (cold prefill
    # of a large prompt plus a throttled decode).
    request_timeout_s: int = field(
        default_factory=lambda: _env_int("AGENT_REQUEST_TIMEOUT", 900)
    )
    # Hard cap on tokens generated per turn. One ReAct block is short; this
    # bounds worst-case decode time so the model can't run away.
    max_new_tokens: int = field(
        default_factory=lambda: _env_int("AGENT_MAX_NEW_TOKENS", 512)
    )

    # --- Vault ---
    vault_path: Path = field(
        default_factory=lambda: Path(_env_str("AGENT_VAULT", DEFAULT_VAULT))
    )

    # --- Loop / autonomy ---
    autonomy: str = field(default_factory=lambda: _env_str("AUTONOMY", "hitl"))
    # Cap the loop — small models drift in long chains.
    max_steps: int = field(default_factory=lambda: _env_int("AGENT_MAX_STEPS", 8))
    # On malformed JSON, re-prompt once before failing the step.
    max_json_retries: int = field(
        default_factory=lambda: _env_int("AGENT_MAX_JSON_RETRIES", 1)
    )
    # Truncate long tool observations before they re-enter context.
    max_observation_chars: int = field(
        default_factory=lambda: _env_int("AGENT_MAX_OBS_CHARS", 1500)
    )

    # --- Prompt / logging ---
    system_prompt_path: Path = field(
        default_factory=lambda: Path(
            _env_str(
                "AGENT_SYSTEM_PROMPT",
                str(Path(__file__).resolve().parent.parent / "prompts" / "agent_system.md"),
            )
        )
    )
    log_path: Path = field(
        default_factory=lambda: Path(
            _env_str("AGENT_LOG", str(Path.home() / ".local_agent" / "agent.log"))
        )
    )

    # --- Feature flags for heavy/optional tools ---
    enable_browser: bool = field(
        default_factory=lambda: _env_str("AGENT_ENABLE_BROWSER", "0") == "1"
    )

    def normalized_autonomy(self) -> str:
        """Coerce autonomy to a known value; default to the safe one."""
        return self.autonomy if self.autonomy in ("hitl", "full") else "hitl"

    def load_system_prompt(self) -> str:
        """Read the model-facing runtime contract from disk."""
        return self.system_prompt_path.read_text(encoding="utf-8")


def load_config() -> Config:
    """Build a Config from defaults + environment overrides."""
    return Config()
