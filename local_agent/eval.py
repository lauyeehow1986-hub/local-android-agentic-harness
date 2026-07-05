"""Eval harness (CLAUDE.md milestone 6).

Scores the agent on a set of representative tasks. Trust this over public
leaderboards — it measures what actually matters for this harness:

- valid-JSON rate: of the model's ACTION turns, how many had parseable INPUT
- tool-selection rate: did it call the expected tool at least once
- completion rate: did it reach a FINAL within the step cap
- latency: tok/s and wall-clock per step (when run against real Ollama)

A task is a dict:
    {"task": str, "expect_tool": str|null, "expect_keywords": [str], "id": str}

Run against real Ollama:
    python -m local_agent.eval --tasks local_agent/eval_tasks.json

The scoring logic (score_run) is pure and unit-tested; the runner wires it to a
real Agent + OllamaClient.
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


@dataclass
class TurnRecord:
    """One model turn observed during a task run."""

    kind: str  # "action" | "final" | "error"
    action: Optional[str] = None
    valid_json: bool = False  # only meaningful for action turns


@dataclass
class TaskResult:
    task_id: str
    reached_final: bool
    final_text: str
    turns: list[TurnRecord]
    expected_tool: Optional[str]
    tool_selected: bool
    keywords_hit: int
    keywords_total: int
    wall_clock_s: float = 0.0
    tokens: int = 0

    @property
    def tok_per_s(self) -> float:
        return self.tokens / self.wall_clock_s if self.wall_clock_s > 0 else 0.0


@dataclass
class EvalReport:
    results: list[TaskResult] = field(default_factory=list)

    def _rate(self, predicate) -> float:
        if not self.results:
            return 0.0
        return sum(1 for r in self.results if predicate(r)) / len(self.results)

    @property
    def completion_rate(self) -> float:
        return self._rate(lambda r: r.reached_final)

    @property
    def tool_selection_rate(self) -> float:
        # Only over tasks that expected a specific tool.
        scoped = [r for r in self.results if r.expected_tool]
        if not scoped:
            return 0.0
        return sum(1 for r in scoped if r.tool_selected) / len(scoped)

    @property
    def valid_json_rate(self) -> float:
        action_turns = [t for r in self.results for t in r.turns if t.kind == "action"]
        if not action_turns:
            return 1.0  # no action turns → vacuously fine
        return sum(1 for t in action_turns if t.valid_json) / len(action_turns)

    @property
    def keyword_rate(self) -> float:
        scoped = [r for r in self.results if r.keywords_total > 0]
        if not scoped:
            return 0.0
        return sum(r.keywords_hit / r.keywords_total for r in scoped) / len(scoped)

    @property
    def mean_tok_per_s(self) -> float:
        timed = [r.tok_per_s for r in self.results if r.wall_clock_s > 0]
        return sum(timed) / len(timed) if timed else 0.0

    def summary(self) -> str:
        lines = [
            f"tasks:            {len(self.results)}",
            f"completion rate:  {self.completion_rate:.0%}",
            f"tool-selection:   {self.tool_selection_rate:.0%}",
            f"valid-JSON rate:  {self.valid_json_rate:.0%}",
            f"keyword match:    {self.keyword_rate:.0%}",
        ]
        if self.mean_tok_per_s:
            lines.append(f"mean tok/s:       {self.mean_tok_per_s:.2f}")
        return "\n".join(lines)


def score_task(
    task: dict[str, Any],
    turns: list[TurnRecord],
    final_text: str,
    *,
    wall_clock_s: float = 0.0,
    tokens: int = 0,
) -> TaskResult:
    """Pure scoring of one task run. No I/O — unit-testable."""
    expected_tool = task.get("expect_tool")
    reached_final = any(t.kind == "final" for t in turns)
    tool_selected = bool(expected_tool) and any(
        t.action == expected_tool for t in turns if t.kind == "action"
    )
    keywords = [k.lower() for k in task.get("expect_keywords", [])]
    low_final = (final_text or "").lower()
    keywords_hit = sum(1 for k in keywords if k in low_final)
    return TaskResult(
        task_id=str(task.get("id", task.get("task", "?"))[:40]),
        reached_final=reached_final,
        final_text=final_text,
        turns=turns,
        expected_tool=expected_tool,
        tool_selected=tool_selected,
        keywords_hit=keywords_hit,
        keywords_total=len(keywords),
        wall_clock_s=wall_clock_s,
        tokens=tokens,
    )


class _RecordingFrontend:
    """Auto-approves guarded actions and records nothing user-facing.

    Used so eval runs don't block on prompts. Set autonomy=full in the config
    you pass to the agent if you want guarded tools to actually run; otherwise
    approvals here auto-approve so completion isn't gated by I/O.
    """

    def on_thought(self, text): pass
    def on_action(self, name, args): pass
    def on_observation(self, text): pass
    def on_final(self, text): pass
    def on_info(self, text): pass
    def ask_approval(self, action, details): return "y"


def run_task(agent, task: dict[str, Any]) -> TaskResult:
    """Run one task through a real Agent, instrumenting the model turns.

    We wrap the agent's active client so we can observe each generation's
    parsed kind/validity and token count without changing the loop.
    """
    from . import parser as _parser

    turns: list[TurnRecord] = []
    token_total = 0
    real_generate = agent.client.generate

    def wrapped(prompt, **kwargs):
        nonlocal token_total
        out = real_generate(prompt, **kwargs)
        token_total += max(1, len(out) // 4)  # rough token estimate
        p = _parser.parse(out)
        if p.kind == "final":
            turns.append(TurnRecord(kind="final"))
        elif p.kind == "action":
            turns.append(
                TurnRecord(kind="action", action=p.action, valid_json=p.input_obj is not None)
            )
        else:
            turns.append(TurnRecord(kind="error"))
        return out

    agent.client.generate = wrapped  # type: ignore[method-assign]
    start = time.time()
    try:
        final_text = agent.run_task(task["task"], _RecordingFrontend())
    finally:
        agent.client.generate = real_generate  # type: ignore[method-assign]
    elapsed = time.time() - start
    return score_task(task, turns, final_text, wall_clock_s=elapsed, tokens=token_total)


def run_eval(agent, tasks: list[dict[str, Any]]) -> EvalReport:
    report = EvalReport()
    for task in tasks:
        report.results.append(run_task(agent, task))
    return report


def load_tasks(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def run_models(tasks, models, agent_factory) -> dict[str, EvalReport]:
    """Run the same task set against each model. `agent_factory(model)` returns a
    fresh Agent configured for that model. Returns {model_name: EvalReport}."""
    reports: dict[str, EvalReport] = {}
    for model in models:
        agent = agent_factory(model)
        reports[model] = run_eval(agent, tasks)
    return reports


def format_comparison(reports: dict[str, EvalReport]) -> str:
    """Side-by-side table: one column per model, one row per metric."""
    models = list(reports.keys())
    rows = [
        ("completion", lambda r: f"{r.completion_rate:.0%}"),
        ("tool-select", lambda r: f"{r.tool_selection_rate:.0%}"),
        ("valid-JSON", lambda r: f"{r.valid_json_rate:.0%}"),
        ("keyword", lambda r: f"{r.keyword_rate:.0%}"),
        ("tok/s", lambda r: f"{r.mean_tok_per_s:.2f}" if r.mean_tok_per_s else "-"),
    ]
    w = max(12, *(len(m) for m in models))
    header = "metric".ljust(12) + "".join(m.ljust(w + 2) for m in models)
    lines = [header, "-" * len(header)]
    for label, fn in rows:
        line = label.ljust(12) + "".join(fn(reports[m]).ljust(w + 2) for m in models)
        lines.append(line)
    return "\n".join(lines)


def _make_agent_factory(config):
    """Return agent_factory(model) that builds an Agent on `model`, reusing config."""
    from .loop import Agent
    from .ollama_client import OllamaClient

    def factory(model: str):
        client = OllamaClient(
            base_url=config.ollama_base_url,
            model=model,
            num_ctx=config.num_ctx,
            temperature=config.temperature,
            timeout_s=config.request_timeout_s,
            num_predict=config.max_new_tokens,
        )
        return Agent(config=config, client=client)

    return factory


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Run the local-agent eval harness.")
    default_tasks = Path(__file__).resolve().parent / "eval_tasks.json"
    ap.add_argument("--tasks", default=str(default_tasks), help="path to tasks JSON")
    ap.add_argument(
        "--models",
        default="",
        help="comma-separated models for an A/B run, e.g. qwen3:4b-instruct-2507-q4_K_M,gemma4:e2b",
    )
    args = ap.parse_args(argv)

    from .config import load_config

    config = load_config()
    config.stream = False
    tasks = load_tasks(Path(args.tasks))
    factory = _make_agent_factory(config)

    # A/B mode: run each model and print a side-by-side comparison.
    if args.models.strip():
        models = [m.strip() for m in args.models.split(",") if m.strip()]
        print(f"A/B: {len(tasks)} tasks × {len(models)} models {models} …")
        print("(at ~3 tok/s this is a long run — let it cook)\n")
        reports: dict[str, EvalReport] = {}
        for model in models:
            print(f"--- {model} ---")
            reports[model] = run_eval(factory(model), tasks)
            print(f"  done: {reports[model].completion_rate:.0%} completed\n")
        print(format_comparison(reports))
        return 0

    # Single-model run.
    agent = factory(config.model)
    print(f"running {len(tasks)} tasks against {config.model} …\n")
    report = run_eval(agent, tasks)
    for r in report.results:
        status = "✓" if r.reached_final else "✗"
        tool = f" tool={'ok' if r.tool_selected else 'miss'}" if r.expected_tool else ""
        print(f"  {status} {r.task_id}{tool} ({r.wall_clock_s:.0f}s)")
    print("\n" + report.summary())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
