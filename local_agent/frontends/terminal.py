"""Terminal frontend: a stdin/stdout REPL with approval prompts.

Implements the Frontend interface the loop expects. Output is terse (YH prefers
it). Thoughts/actions/observations are shown dimmed as a trace; the FINAL is
shown plainly.
"""

from __future__ import annotations

import sys
from typing import Any

# Tiny ANSI helpers — degrade to plain text if not a TTY.
_TTY = sys.stdout.isatty()


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _TTY else text


class TerminalFrontend:
    def __init__(self, *, show_trace: bool = True) -> None:
        self.show_trace = show_trace

    # -- loop callbacks ---------------------------------------------------
    def on_thought(self, text: str) -> None:
        if self.show_trace and text:
            print(_c("2", f"  · {text}"))

    def on_action(self, name: str, args: dict[str, Any]) -> None:
        if self.show_trace:
            print(_c("36", f"  → {name} {args}"))

    def on_observation(self, text: str) -> None:
        if self.show_trace:
            first = text.splitlines()[0] if text else ""
            more = "" if text.count("\n") == 0 else f" (+{text.count(chr(10))} lines)"
            print(_c("2", f"  ← {first}{more}"))

    def on_final(self, text: str) -> None:
        print(_c("1", text))

    def on_info(self, text: str) -> None:
        print(_c("33", f"[info] {text}"))

    def ask_approval(self, action: str, details: str) -> str:
        print(_c("35", f"\n[approval needed] {action}"))
        if details:
            print(_c("2", f"  {details}"))
        print(
            _c("2", "  reply: y(es) / n(o) / edited:<json>  ")
        )
        try:
            return input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            return "n"

    # -- REPL -------------------------------------------------------------
    def read_task(self) -> str:
        try:
            return input(_c("32", "\nyh> ")).strip()
        except (EOFError, KeyboardInterrupt):
            return "/quit"
