"""Eval harness: pure scoring + an end-to-end run with a scripted client."""

from pathlib import Path

from local_agent import eval as ev
from local_agent.config import Config
from local_agent.loop import Agent


def test_score_task_tool_and_keywords():
    task = {"id": "t", "task": "x", "expect_tool": "vault_search", "expect_keywords": ["paris"]}
    turns = [
        ev.TurnRecord(kind="action", action="vault_search", valid_json=True),
        ev.TurnRecord(kind="final"),
    ]
    r = ev.score_task(task, turns, "The answer is Paris.")
    assert r.reached_final
    assert r.tool_selected
    assert r.keywords_hit == 1 and r.keywords_total == 1


def test_score_task_missing_tool_and_bad_json():
    task = {"id": "t", "task": "x", "expect_tool": "vault_read", "expect_keywords": []}
    turns = [
        ev.TurnRecord(kind="action", action="vault_search", valid_json=False),
        ev.TurnRecord(kind="final"),
    ]
    r = ev.score_task(task, turns, "done")
    assert not r.tool_selected


def test_report_rates():
    report = ev.EvalReport()
    report.results = [
        ev.score_task(
            {"id": "a", "task": "x", "expect_tool": "vault_search", "expect_keywords": []},
            [ev.TurnRecord("action", "vault_search", True), ev.TurnRecord("final")],
            "ok",
        ),
        ev.score_task(
            {"id": "b", "task": "y", "expect_tool": None, "expect_keywords": ["132"]},
            [ev.TurnRecord("final")],
            "it is 132",
        ),
    ]
    assert report.completion_rate == 1.0
    assert report.tool_selection_rate == 1.0  # only the scoped task counts
    assert report.valid_json_rate == 1.0
    assert report.keyword_rate == 1.0


def _config(tmp_path):
    cfg = Config()
    cfg.vault_path = tmp_path / "vault"
    cfg.vault_path.mkdir()
    cfg.log_path = tmp_path / "agent.log"
    cfg.stream = False
    cfg.system_prompt_path = (
        Path(__file__).resolve().parent.parent / "prompts" / "agent_system.md"
    )
    return cfg


class ScriptedClient:
    def __init__(self, script):
        self.script = dict(script)  # task_text -> [outputs]

    def generate(self, prompt, system=None, model=None, options=None, on_token=None):
        for key, outs in self.script.items():
            if key in prompt and outs:
                return outs.pop(0)
        return "THOUGHT: done\nFINAL: fallback"

    def health(self):
        return True


def test_run_eval_end_to_end(tmp_path):
    cfg = _config(tmp_path)
    (cfg.vault_path / "a.md").write_text("recurrent events: Andersen-Gill model")
    client = ScriptedClient(
        {
            "recurrent": [
                'THOUGHT: search\nACTION: vault_search\nINPUT: {"query": "recurrent", "limit": 3}',
                "THOUGHT: found\nFINAL: Andersen-Gill model",
            ],
            "12 times 11": ["THOUGHT: math\nFINAL: 132"],
        }
    )
    agent = Agent(config=cfg, client=client)
    tasks = [
        {
            "id": "recurrent",
            "task": "What did I note about recurrent events?",
            "expect_tool": "vault_search",
            "expect_keywords": ["andersen"],
        },
        {
            "id": "math",
            "task": "What is 12 times 11?",
            "expect_tool": None,
            "expect_keywords": ["132"],
        },
    ]
    report = ev.run_eval(agent, tasks)
    assert report.completion_rate == 1.0
    assert report.tool_selection_rate == 1.0
    assert report.valid_json_rate == 1.0
    assert report.keyword_rate == 1.0


def test_run_models_ab_and_table(tmp_path):
    from local_agent import eval as ev

    cfg = _config(tmp_path)
    (cfg.vault_path / "a.md").write_text("recurrent events: Andersen-Gill model")
    tasks = [
        {
            "id": "recurrent",
            "task": "What did I note about recurrent events?",
            "expect_tool": "vault_search",
            "expect_keywords": ["andersen"],
        },
        {"id": "math", "task": "What is 12 times 11?", "expect_tool": None, "expect_keywords": ["132"]},
    ]

    # "good" model completes both with a valid tool call; "bad" emits invalid JSON.
    def factory(model):
        from local_agent.loop import Agent

        if model == "good":
            script = {
                "recurrent": [
                    'THOUGHT: s\nACTION: vault_search\nINPUT: {"query": "recurrent", "limit": 3}',
                    "THOUGHT: f\nFINAL: Andersen-Gill model",
                ],
                "12 times 11": ["THOUGHT: m\nFINAL: 132"],
            }
        else:
            script = {
                "recurrent": ["THOUGHT: s\nACTION: vault_search\nINPUT: {bad json}"] * 3,
                "12 times 11": ["THOUGHT: m\nFINAL: maybe 100"],
            }
        return Agent(config=cfg, client=ScriptedClient(script))

    reports = ev.run_models(tasks, ["good", "bad"], factory)
    assert set(reports.keys()) == {"good", "bad"}
    assert reports["good"].tool_selection_rate == 1.0
    assert reports["good"].keyword_rate == 1.0
    assert reports["bad"].keyword_rate < 1.0

    table = ev.format_comparison(reports)
    assert "good" in table and "bad" in table
    assert "valid-JSON" in table and "tool-select" in table


def test_bundled_tasks_file_is_valid():
    tasks = ev.load_tasks(
        Path(__file__).resolve().parent.parent / "local_agent" / "eval_tasks.json"
    )
    assert len(tasks) >= 5
    for t in tasks:
        assert "task" in t and "expect_tool" in t and "expect_keywords" in t
