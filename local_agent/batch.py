"""Batch / non-interactive runner — for cron jobs and scripted task lists.

Runs each task through the agent with no human in the loop. Because nobody is
there to approve, GUARDED actions are auto-DENIED by default (safe); pass
--approve (or auto="approve") to let them run unattended — combine that with
AUTONOMY=full only when you trust the task list.

Usage:
    python -m local_agent.batch tasks.txt           # one task per line (# = comment)
    python -m local_agent.batch tasks.json          # JSON list of task strings
    python -m local_agent.batch --approve tasks.txt # allow guarded actions
    echo "summarize my day" | python -m local_agent.batch -   # stdin

Schedule it with cron (see docs/TERMUX_SETUP.md / README).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


class BatchFrontend:
    """Non-interactive frontend. Records the FINAL of each task; resolves
    approval prompts automatically per `auto` ('deny' | 'approve')."""

    def __init__(self, auto: str = "deny", verbose: bool = False) -> None:
        self.auto = auto
        self.verbose = verbose
        self.final: str = ""
        self.infos: list[str] = []

    def on_thought(self, text: str) -> None:
        if self.verbose and text:
            print(f"  · {text}", file=sys.stderr)

    def on_action(self, name: str, args: dict[str, Any]) -> None:
        if self.verbose:
            print(f"  → {name} {args}", file=sys.stderr)

    def on_observation(self, text: str) -> None:
        if self.verbose:
            print(f"  ← {text.splitlines()[0] if text else ''}", file=sys.stderr)

    def on_final(self, text: str) -> None:
        self.final = text

    def on_info(self, text: str) -> None:
        self.infos.append(text)
        if self.verbose:
            print(f"  [info] {text}", file=sys.stderr)

    def ask_approval(self, action: str, details: str) -> str:
        # Unattended: approve or deny by policy.
        return "y" if self.auto == "approve" else "n"


def load_tasks(source: str) -> list[str]:
    """Load tasks from a .json list, a newline file (# comments ok), or stdin."""
    if source == "-":
        raw = sys.stdin.read()
        return [ln.strip() for ln in raw.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    p = Path(source)
    text = p.read_text(encoding="utf-8")
    if p.suffix.lower() == ".json":
        data = json.loads(text)
        return [str(t) for t in data]
    return [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]


def run_batch(agent, tasks: list[str], *, auto: str = "deny", verbose: bool = False):
    """Run each task; return a list of (task, final) tuples."""
    results: list[tuple[str, str]] = []
    for task in tasks:
        fe = BatchFrontend(auto=auto, verbose=verbose)
        final = agent.run_task(task, fe)
        results.append((task, final))
    return results


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run local-agent tasks non-interactively.")
    ap.add_argument("tasks", help="path to a .txt/.json task file, or - for stdin")
    ap.add_argument(
        "--approve",
        action="store_true",
        help="auto-approve GUARDED actions (default: auto-deny). Use with care.",
    )
    ap.add_argument("--verbose", action="store_true", help="print the ReAct trace to stderr")
    args = ap.parse_args(argv)

    from .config import load_config
    from .loop import Agent
    from .main import build_agent

    config = load_config()
    config.stream = False  # no live streaming in batch
    agent = build_agent(config)
    tasks = load_tasks(args.tasks)
    auto = "approve" if args.approve else "deny"

    results = run_batch(agent, tasks, auto=auto, verbose=args.verbose)
    for task, final in results:
        print(f"### {task}\n{final}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
