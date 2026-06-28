"""Voice loop: speak to the agent, hear it answer — fully on-device.

  record (termux-microphone-record) → transcribe (whisper) → agent → speak (TTS)

    python -m local_agent.voice            # 8-second turns
    python -m local_agent.voice --seconds 12

Needs termux-api (`pkg install termux-api`) and a whisper backend (see
docs/TERMUX_SETUP.md). Press Enter to start each turn, Ctrl-C to quit.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional


def _have(binary: str) -> bool:
    return shutil.which(binary) is not None


def _record(seconds: int) -> Optional[Path]:
    if not _have("termux-microphone-record"):
        print("termux-microphone-record not found — `pkg install termux-api`.", file=sys.stderr)
        return None
    wav = Path(tempfile.mkstemp(suffix=".wav")[1])
    subprocess.run(
        ["termux-microphone-record", "-f", str(wav), "-l", str(seconds)],
        capture_output=True,
    )
    # Wait for the fixed-length recording to finish, then stop cleanly.
    try:
        import time

        time.sleep(seconds + 1)
    finally:
        subprocess.run(["termux-microphone-record", "-q"], capture_output=True)
    return wav if wav.exists() and wav.stat().st_size > 0 else None


def _speak(text: str) -> None:
    if _have("termux-tts-speak"):
        subprocess.run(["termux-tts-speak", text], capture_output=True)


class VoiceFrontend:
    """Minimal frontend: prints the trace, speaks the FINAL, auto-denies guarded."""

    def __init__(self) -> None:
        self.final = ""

    def on_thought(self, text): pass
    def on_action(self, name, args): print(f"  → {name} {args}", file=sys.stderr)
    def on_observation(self, text): pass
    def on_final(self, text): self.final = text
    def on_info(self, text): print(f"[info] {text}", file=sys.stderr)
    def ask_approval(self, action, details): return "n"  # voice mode: no unattended writes


def main(argv: Optional[list[str]] = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Voice loop for local-agent.")
    ap.add_argument("--seconds", type=int, default=8, help="recording length per turn")
    args = ap.parse_args(argv)

    from .config import load_config
    from .main import build_agent
    from .tools.data import _audio_to_text  # reuse the whisper backend detection

    config = load_config()
    config.stream = False
    agent = build_agent(config)
    print("Voice loop ready. Press Enter to speak, Ctrl-C to quit.")

    while True:
        try:
            input("\n[Enter to record] ")
        except (EOFError, KeyboardInterrupt):
            print("\nbye")
            return 0
        wav = _record(args.seconds)
        if not wav:
            continue
        text, err = _audio_to_text(wav, agent.ctx, getattr(config, "whisper_language", ""))
        wav.unlink(missing_ok=True)
        if err or not text:
            msg = err or "(heard nothing)"
            print(msg)
            _speak("Sorry, I didn't catch that.")
            continue
        print(f"you: {text}")
        fe = VoiceFrontend()
        answer = agent.run_task(text, fe)
        print(f"agent: {answer}")
        _speak(answer)


if __name__ == "__main__":
    raise SystemExit(main())
