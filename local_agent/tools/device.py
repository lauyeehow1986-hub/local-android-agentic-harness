"""Device-glue tools via termux-api: clipboard, notifications, location, TTS.

All require the `termux-api` package (`pkg install termux-api`) and the Termux:API
app. They degrade with a clear message when the helper binary is absent. These are
low-risk, local-only conveniences.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import Any

from . import Tool, ToolContext


def _run(cmd: list[str], *, input_text: str | None = None, timeout: int = 30) -> tuple[int, str, str]:
    proc = subprocess.run(
        cmd, input=input_text, capture_output=True, text=True, timeout=timeout
    )
    return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()


def _need(binary: str) -> str | None:
    return None if shutil.which(binary) else (
        f"{binary} not found — install termux-api (`pkg install termux-api`) and the "
        "Termux:API app."
    )


def clipboard(args: dict[str, Any], ctx: ToolContext) -> str:
    mode = str(args.get("mode", "read")).strip().lower()
    if mode == "read":
        err = _need("termux-clipboard-get")
        if err:
            return err
        _, out, _ = _run(["termux-clipboard-get"])
        return out or "(clipboard empty)"
    if mode == "write":
        err = _need("termux-clipboard-set")
        if err:
            return err
        text = str(args.get("text", ""))
        _run(["termux-clipboard-set"], input_text=text)
        return f"clipboard set ({len(text)} chars)"
    return "error: mode must be 'read' or 'write'"


def notify(args: dict[str, Any], ctx: ToolContext) -> str:
    err = _need("termux-notification")
    if err:
        return err
    title = str(args.get("title", "local-agent"))
    content = str(args.get("content", ""))
    rc, _, e = _run(["termux-notification", "--title", title, "--content", content])
    return "notification sent" if rc == 0 else f"notify failed: {e}"


def location(args: dict[str, Any], ctx: ToolContext) -> str:
    err = _need("termux-location")
    if err:
        return err
    provider = str(args.get("provider", "network"))  # network is fast; gps is precise
    rc, out, e = _run(["termux-location", "-p", provider], timeout=60)
    if rc != 0 or not out:
        return f"location unavailable: {e or 'no fix'} (try provider=gps outdoors)"
    return out  # JSON with latitude/longitude/accuracy


def speak(args: dict[str, Any], ctx: ToolContext) -> str:
    err = _need("termux-tts-speak")
    if err:
        return err
    text = str(args.get("text", "")).strip()
    if not text:
        return "error: 'text' is required"
    rc, _, e = _run(["termux-tts-speak", text], timeout=120)
    return "spoke" if rc == 0 else f"tts failed: {e}"


TOOLS = [
    Tool(
        name="clipboard",
        tag="SAFE",
        fn=clipboard,
        optional=("mode", "text"),
        description="Read or write the Android clipboard. {\"mode\":\"read\"|\"write\",\"text\":...}",
    ),
    Tool(
        name="notify",
        tag="SAFE",
        fn=notify,
        optional=("title", "content"),
        description="Push an Android notification.",
    ),
    Tool(
        name="location",
        tag="SAFE",
        fn=location,
        optional=("provider",),
        description="Get the phone's GPS/network location (JSON).",
    ),
    Tool(
        name="speak",
        tag="SAFE",
        fn=speak,
        required=("text",),
        description="Speak text aloud via Android TTS.",
    ),
]
