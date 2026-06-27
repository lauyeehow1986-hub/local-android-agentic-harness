"""Parser must be bulletproof — feed it the mess a small model emits."""

from local_agent import parser


def test_clean_action():
    text = (
        "THOUGHT: Search the vault.\n"
        "ACTION: vault_search\n"
        'INPUT: {"query": "OMOP", "limit": 5}'
    )
    step = parser.parse(text)
    assert step.kind == "action"
    assert step.action == "vault_search"
    assert step.input_obj == {"query": "OMOP", "limit": 5}
    assert "Search the vault" in step.thought


def test_clean_final():
    text = "THOUGHT: I have the answer.\nFINAL: The answer is 42."
    step = parser.parse(text)
    assert step.kind == "final"
    assert step.final == "The answer is 42."


def test_strips_code_fences():
    text = (
        "```\n"
        "THOUGHT: do it\n"
        "ACTION: vault_read\n"
        'INPUT: {"path": "a.md"}\n'
        "```"
    )
    step = parser.parse(text)
    assert step.kind == "action"
    assert step.action == "vault_read"
    assert step.input_obj == {"path": "a.md"}


def test_strips_json_fence_label():
    text = (
        "THOUGHT: x\n"
        "ACTION: web_search\n"
        "INPUT:\n```json\n"
        '{"query": "weather"}\n'
        "```"
    )
    step = parser.parse(text)
    assert step.input_obj == {"query": "weather"}


def test_ignores_fabricated_observation():
    text = (
        "THOUGHT: read it\n"
        "ACTION: vault_read\n"
        'INPUT: {"path": "a.md"}\n'
        "OBSERVATION: I made this up\n"
        "THOUGHT: now I am done\n"
        "FINAL: leaked"
    )
    step = parser.parse(text)
    # The first complete block is the ACTION; the fabricated rest is discarded.
    assert step.kind == "action"
    assert step.action == "vault_read"


def test_final_with_fabricated_observation_after():
    text = (
        "THOUGHT: done\n"
        "FINAL: real answer\n"
        "OBSERVATION: fabricated\n"
        "THOUGHT: more"
    )
    step = parser.parse(text)
    assert step.kind == "final"
    assert step.final == "real answer"


def test_multiline_final():
    text = "THOUGHT: done\nFINAL: line one\nline two\nline three"
    step = parser.parse(text)
    assert step.kind == "final"
    assert step.final == "line one\nline two\nline three"


def test_bad_json_flagged_for_reprompt():
    text = "THOUGHT: x\nACTION: vault_read\nINPUT: {path: a.md}"  # unquoted
    step = parser.parse(text)
    assert step.kind == "action"
    assert step.input_obj is None
    assert step.error and "JSON" in step.error


def test_json_with_trailing_garbage():
    text = (
        "THOUGHT: x\n"
        "ACTION: vault_search\n"
        'INPUT: {"query": "hi"}  // trailing comment'
    )
    step = parser.parse(text)
    assert step.input_obj == {"query": "hi"}


def test_action_name_with_trailing_prose():
    text = "THOUGHT: x\nACTION: vault_list now\nINPUT: {}"
    step = parser.parse(text)
    assert step.action == "vault_list"


def test_lowercase_labels_tolerated():
    text = "thought: x\naction: vault_list\ninput: {}"
    step = parser.parse(text)
    assert step.kind == "action"
    assert step.action == "vault_list"
    assert step.input_obj == {}


def test_final_before_action_wins():
    text = (
        "THOUGHT: done\n"
        "FINAL: the answer\n"
        "ACTION: vault_read\n"
        'INPUT: {"path": "x"}'
    )
    step = parser.parse(text)
    assert step.kind == "final"


def test_action_before_final_wins():
    text = (
        "THOUGHT: act first\n"
        "ACTION: vault_read\n"
        'INPUT: {"path": "x"}\n'
        "FINAL: should not win"
    )
    step = parser.parse(text)
    assert step.kind == "action"


def test_empty_input_returns_error_only():
    step = parser.parse("")
    assert step.kind == "error"


def test_thought_only_becomes_final():
    step = parser.parse("THOUGHT: I think the sky is blue.")
    assert step.kind == "final"
    assert "sky is blue" in step.final


def test_missing_input_line():
    text = "THOUGHT: x\nACTION: vault_list"
    step = parser.parse(text)
    assert step.kind == "action"
    assert step.action == "vault_list"
    assert step.input_obj is None
    assert step.error


def test_multiline_json_input():
    text = (
        "THOUGHT: x\n"
        "ACTION: make_slides\n"
        "INPUT: {\n"
        '  "title": "Deck",\n'
        '  "outline": ["a", "b"],\n'
        '  "out_path": "d.md"\n'
        "}"
    )
    step = parser.parse(text)
    assert step.input_obj == {"title": "Deck", "outline": ["a", "b"], "out_path": "d.md"}


def test_input_not_object():
    text = 'THOUGHT: x\nACTION: vault_read\nINPUT: "just a string"'
    step = parser.parse(text)
    assert step.kind == "action"
    assert step.input_obj is None
    assert step.error
