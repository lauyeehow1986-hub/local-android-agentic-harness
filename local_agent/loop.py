"""The ReAct controller: prompt -> generate -> parse -> (approval) -> dispatch
-> observe -> repeat, until FINAL or the step cap.

The harness compensates for a small, slow model:
- terse system prompt + transcript, small num_ctx,
- truncate tool observations before they re-enter context,
- re-prompt once on malformed JSON,
- cap the loop at config.max_steps,
- log every turn for debugging.

IO is delegated to a `frontend` object implementing:
    on_thought(text), on_action(name, args), on_observation(text),
    on_final(text), ask_approval(action, details) -> str, on_info(text)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from typing import Any, Optional, Protocol

from . import approval, parser
from .config import Config
from .ollama_client import OllamaClient, OllamaError
from .tools import ToolContext, build_registry


class Frontend(Protocol):
    def on_thought(self, text: str) -> None: ...
    def on_action(self, name: str, args: dict[str, Any]) -> None: ...
    def on_observation(self, text: str) -> None: ...
    def on_final(self, text: str) -> None: ...
    def on_info(self, text: str) -> None: ...
    def ask_approval(self, action: str, details: str) -> str: ...


def _make_logger(config: Config) -> logging.Logger:
    logger = logging.getLogger("local_agent.loop")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        config.log_path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            config.log_path, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    return logger


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n…[truncated {len(text) - limit} chars]"


@dataclass
class Agent:
    config: Config
    client: OllamaClient
    # Optional faster Ollama on a LAN box (e.g. 30B-A3B over Tailscale). Same
    # tool conventions → clean fallback. When set and reachable, `auto`/`remote`
    # routing sends generation there; we always fall back to local on failure.
    remote_client: Optional[OllamaClient] = None

    def __post_init__(self) -> None:
        self.registry = build_registry()
        self.logger = _make_logger(self.config)
        self.system_prompt = self.config.load_system_prompt()
        self.ctx = ToolContext(config=self.config, client=self.client)
        # local | remote | auto  (auto = remote if reachable, else local)
        self.route = getattr(self.config, "route", "local")

    def _active_client(self) -> OllamaClient:
        """Pick the client for this turn per the routing mode, with fallback."""
        if self.remote_client is None or self.route == "local":
            return self.client
        if self.route == "remote":
            return self.remote_client
        # auto: prefer remote when its server answers, else local.
        try:
            if self.remote_client.health():
                return self.remote_client
        except Exception:  # noqa: BLE001
            pass
        return self.client

    # -- prompt assembly --------------------------------------------------
    def _initial_transcript(self, user_task: str) -> str:
        import datetime

        autonomy = self.config.normalized_autonomy()
        # Inject the current date so "today"/"tomorrow" resolve and daily notes
        # land in Daily/<DATE>.md.
        today = datetime.date.today().isoformat()
        return f"AUTONOMY={autonomy}\nDATE={today}\nUSER: {user_task}\n"

    def _full_prompt(self, transcript: str) -> str:
        # The system prompt is passed via the system field; the transcript is
        # the running ReAct dialogue. We nudge the model to emit the next block.
        return transcript.rstrip() + "\n"

    # -- approval ---------------------------------------------------------
    def _gate(self, frontend: Frontend, tool, args: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
        """Return (allowed, possibly-edited-args)."""
        decision = approval.decide(
            autonomy=self.config.normalized_autonomy(),
            tool_name=tool.name,
            tool_tag=tool.tag,
            args=args,
        )
        if not decision.needs_prompt:
            return True, args
        details = json.dumps(args, ensure_ascii=False)
        raw = frontend.ask_approval(f"{tool.name} ({decision.reason})", details)
        verdict, payload = approval.parse_approval_response(raw)
        if verdict == "approved":
            return True, args
        if verdict == "edited" and payload:
            try:
                new_args = json.loads(payload)
                if isinstance(new_args, dict):
                    return True, new_args
            except json.JSONDecodeError:
                frontend.on_info("edited INPUT was not valid JSON; treating as denial")
        return False, args

    # -- dispatch ---------------------------------------------------------
    def _dispatch(self, frontend: Frontend, name: str, args: dict[str, Any]) -> str:
        tool = self.registry.get(name)
        if tool is None:
            return f"unknown tool '{name}'. Pick one from the contract."

        err = tool.validate(args)
        if err:
            return f"bad arguments for {name}: {err}"

        allowed, args = self._gate(frontend, tool, args)
        if not allowed:
            return f"DENIED by user: {name} was not run. Adapt or finish."

        try:
            return tool.fn(args, self.ctx)
        except Exception as e:  # noqa: BLE001 - never crash the loop on a tool
            self.logger.exception("tool %s raised", name)
            return f"tool '{name}' raised: {e}"

    # -- main loop --------------------------------------------------------
    def run_task(self, user_task: str, frontend: Frontend) -> str:
        transcript = self._initial_transcript(user_task)
        self.logger.info("TASK: %s", user_task)
        json_retries_used = 0

        for step in range(1, self.config.max_steps + 1):
            # Stream tokens to the frontend if it supports it and streaming is on.
            on_token = None
            streamed = False
            if self.config.stream and hasattr(frontend, "on_token"):
                on_token = frontend.on_token
            try:
                if on_token is not None:
                    if hasattr(frontend, "stream_begin"):
                        frontend.stream_begin()
                    raw = self._active_client().generate(
                        self._full_prompt(transcript),
                        system=self.system_prompt,
                        on_token=on_token,
                    )
                    streamed = True
                    if hasattr(frontend, "stream_end"):
                        frontend.stream_end()
                else:
                    raw = self._active_client().generate(
                        self._full_prompt(transcript), system=self.system_prompt
                    )
            except OllamaError as e:
                msg = f"model error: {e}"
                frontend.on_info(msg)
                self.logger.error(msg)
                return msg

            self.logger.info("STEP %d RAW: %s", step, raw)
            parsed = parser.parse(raw)

            # When streaming, the THOUGHT was already shown live; don't repeat it.
            if parsed.thought and not streamed:
                frontend.on_thought(parsed.thought)

            # FINAL — done.
            if parsed.kind == "final":
                final = parsed.final or "(no answer)"
                frontend.on_final(final)
                self.logger.info("FINAL: %s", final)
                return final

            # Hard parse error with no recoverable action.
            if parsed.kind == "error":
                if json_retries_used < self.config.max_json_retries:
                    json_retries_used += 1
                    transcript += (
                        "\nOBSERVATION: Your output was not a valid THOUGHT/ACTION/"
                        "INPUT or THOUGHT/FINAL block. Resend exactly one block.\n"
                    )
                    continue
                msg = f"could not parse model output: {parsed.error}"
                frontend.on_info(msg)
                return msg

            # ACTION path. Handle malformed/missing INPUT JSON with one re-prompt.
            if parsed.input_obj is None:
                if json_retries_used < self.config.max_json_retries:
                    json_retries_used += 1
                    transcript += (
                        f"\nTHOUGHT: {parsed.thought}\nACTION: {parsed.action}\n"
                        f"INPUT: {raw_input_hint(parsed)}\n"
                        "OBSERVATION: Your INPUT was not valid JSON. Resend the same "
                        "ACTION with valid single-line JSON.\n"
                    )
                    continue
                obs = f"INPUT parse failed twice: {parsed.error}. Skipping."
                frontend.on_observation(obs)
                transcript += (
                    f"\nTHOUGHT: {parsed.thought}\nACTION: {parsed.action}\n"
                    f"OBSERVATION: {obs}\n"
                )
                continue

            action = parsed.action or ""
            args = parsed.input_obj
            frontend.on_action(action, args)

            # request_approval is special: surface it to the frontend directly.
            if action == "request_approval":
                verdict_raw = frontend.ask_approval(
                    str(args.get("action", "(unspecified)")),
                    str(args.get("details", "")),
                )
                verdict, payload = approval.parse_approval_response(verdict_raw)
                obs = (
                    "approved"
                    if verdict == "approved"
                    else (f"edited:{payload}" if verdict == "edited" else "denied")
                )
            else:
                obs = self._dispatch(frontend, action, args)

            obs = _truncate(obs, self.config.max_observation_chars)
            frontend.on_observation(obs)
            self.logger.info("STEP %d OBS: %s", step, obs)

            transcript += (
                f"\nTHOUGHT: {parsed.thought}\nACTION: {action}\n"
                f"INPUT: {json.dumps(args, ensure_ascii=False)}\n"
                f"OBSERVATION: {obs}\n"
            )

        msg = (
            f"Reached the {self.config.max_steps}-step cap without finishing. "
            "Stopping to avoid drift."
        )
        frontend.on_info(msg)
        self.logger.info("STEP CAP HIT")
        return msg


def raw_input_hint(parsed: parser.ParsedStep) -> str:
    """A minimal echo of the args we wanted, for the re-prompt nudge."""
    if parsed.input_obj is not None:
        return json.dumps(parsed.input_obj, ensure_ascii=False)
    return "{}"
