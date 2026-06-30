"""Voice loop: speak to the agent, hear it answer — fully on-device.

  record (termux-microphone-record) → transcribe (whisper) → agent → speak (TTS)

    python -m local_agent.voice                 # command mode: speech → agent → answer
    python -m local_agent.voice --dictate       # dictation: speech → appended to today's note
    python -m local_agent.voice --dictate --note "Notes/ideas.md"

Needs termux-api (`pkg install termux-api`) and a whisper backend (see
docs/TERMUX_SETUP.md).

Two modes:
  - default       → the transcript is a task for the agent (it acts + answers + speaks).
  - --dictate     → the transcript is appended VERBATIM (timestamped) to a note, no agent.
                    It reads the transcript back first (Enter = save / r = redo /
                    n = discard); use --no-confirm to save immediately.

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


def _spoken_form(agent, text: str, *, full: bool, max_len: int = 240) -> str:
    """Return a short version of `text` for TTS (long answers are awkward aloud).

    Speaks the whole thing if --full-speech, or if it's already short. Otherwise
    asks the model for a 1-2 sentence spoken summary; falls back to a truncation.
    """
    if full or len(text) <= max_len:
        return text
    try:
        s = agent.client.generate(
            f"Condense this into one or two short, natural sentences to read aloud:\n{text}",
            system="You produce a brief spoken summary. No preamble.",
        ).strip()
        return s or text[:max_len]
    except Exception:  # noqa: BLE001
        return text[:max_len]


def _dictation_note(note: str) -> str:
    """Resolve the target note for dictation — default today's daily note."""
    if note:
        return note
    import datetime

    return f"Daily/{datetime.date.today().isoformat()}.md"


def _append_dictation(agent, text: str, note: str) -> str:
    """Append a transcribed line (with a timestamp) to a vault note, verbatim.
    Returns the tool's result string. Uses vault_write append — no LLM in the loop."""
    import datetime

    stamp = datetime.datetime.now().strftime("%H:%M")
    content = f"- [{stamp}] {text}\n"
    return agent.registry["vault_write"].fn(
        {"path": note, "content": content, "mode": "append"}, agent.ctx
    )


def main(argv: Optional[list[str]] = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Voice loop for local-agent.")
    ap.add_argument("--max-seconds", type=int, default=180, help="safety cap per recording")
    ap.add_argument(
        "--full-speech", action="store_true",
        help="speak the entire answer (default: speak a 1-2 sentence summary)",
    )
    ap.add_argument(
        "--dictate", action="store_true",
        help="dictation mode: append the transcript verbatim to a note (no agent)",
    )
    ap.add_argument(
        "--note", default="",
        help="dictation target note (default: today's Daily/<date>.md)",
    )
    ap.add_argument(
        "--no-confirm", action="store_true",
        help="dictation: save immediately without reading the transcript back",
    )
    args = ap.parse_args(argv)

    from .config import load_config
    from .main import build_agent
    from .tools.data import _audio_to_text  # reuse the whisper backend detection

    config = load_config()
    config.stream = False
    agent = build_agent(config)
    note = _dictation_note(args.note)
    if args.dictate:
        print(f"Dictation mode → {note}. Enter = start, Enter = stop, Ctrl-C = quit.")
    else:
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

        if args.dictate:
            # Read the transcript back so mis-hears can be caught before saving.
            if not args.no_confirm:
                _speak(f"I heard: {text[:400]}")
                try:
                    resp = input("[Enter = save · r = redo · n = discard] ").strip().lower()
                except (EOFError, KeyboardInterrupt):
                    resp = "n"
                if resp == "r":
                    print("(redo)")
                    continue
                if resp == "n":
                    print("(discarded)")
                    continue
            # Verbatim → note, no agent reasoning. Fast and reliable.
            result = _append_dictation(agent, text, note)
            print(result)
            _speak("Saved.")
            continue

        fe = VoiceFrontend()
        answer = agent.run_task(text, fe)
        print(f"agent: {answer}")
        _speak(_spoken_form(agent, answer, full=args.full_speech))


if __name__ == "__main__":
    raise SystemExit(main())
