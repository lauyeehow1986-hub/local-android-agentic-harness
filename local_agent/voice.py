"""Voice loop: speak to the agent, hear it answer — fully on-device.

  record (termux-microphone-record) → transcribe (whisper) → agent → speak (TTS)

    python -m local_agent.voice                 # press Enter to start/stop each turn
    python -m local_agent.voice --max-seconds 180

Needs termux-api (`pkg install termux-api`) and a whisper backend (see
docs/TERMUX_SETUP.md).

Controls:
  - Enter  → START recording
  - Enter  → STOP recording (then it transcribes + answers + speaks)
  - Ctrl-C → quit
`--max-seconds` is just a safety cap so a forgotten recording can't run forever.
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


def _rec_path() -> Path:
    return Path(tempfile.gettempdir()) / "agent_voice_rec.m4a"


def _start_record(max_seconds: int) -> Optional[Path]:
    """Begin recording (returns immediately). Stops on _stop_record() or the cap.

    Records .m4a — termux-microphone-record's native format; our transcribe path
    converts it to WAV via ffmpeg for whisper.cpp. The output path must NOT exist
    beforehand (the recorder won't write into an existing file), so we delete it
    first; we also clear any stuck recording session.
    """
    if not _have("termux-microphone-record"):
        print("termux-microphone-record not found — `pkg install termux-api`.", file=sys.stderr)
        return None
    subprocess.run(["termux-microphone-record", "-q"], capture_output=True)  # clear stuck session
    rec = _rec_path()
    rec.unlink(missing_ok=True)                                              # fresh path
    proc = subprocess.run(
        ["termux-microphone-record", "-f", str(rec), "-l", str(max_seconds)],
        capture_output=True, text=True,
    )
    msg = ((proc.stdout or "") + (proc.stderr or "")).strip()
    if "started" not in msg.lower():
        print(f"recorder: {msg or 'failed to start (check Termux:API app + mic permission)'}",
              file=sys.stderr)
        return None
    return rec


def _stop_record() -> None:
    subprocess.run(["termux-microphone-record", "-q"], capture_output=True)
    import time

    time.sleep(1.0)  # let the recorder finalize/flush the file to disk


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
    ap.add_argument("--max-seconds", type=int, default=180, help="safety cap per recording")
    args = ap.parse_args(argv)

    from .config import load_config
    from .main import build_agent
    from .tools.data import _audio_to_text  # reuse the whisper backend detection

    config = load_config()
    config.stream = False
    agent = build_agent(config)
    print("Voice loop ready. Enter = start recording, Enter again = stop, Ctrl-C = quit.")

    while True:
        try:
            input("\n[Enter to START recording] ")
        except (EOFError, KeyboardInterrupt):
            print("\nbye")
            return 0
        wav = _start_record(args.max_seconds)
        if not wav:
            continue
        try:
            input("[recording… Enter to STOP] ")
        except (EOFError, KeyboardInterrupt):
            pass
        _stop_record()
        if not (wav.exists() and wav.stat().st_size > 0):
            print("(no audio captured)")
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
