"""Approval policy: SAFE/GUARDED, hitl vs full, always-confirm set."""

from local_agent import approval


def test_safe_never_prompts():
    d = approval.decide(autonomy="hitl", tool_name="vault_read", tool_tag="SAFE", args={})
    assert d.needs_prompt is False
    d = approval.decide(autonomy="full", tool_name="web_search", tool_tag="SAFE", args={})
    assert d.needs_prompt is False


def test_guarded_prompts_in_hitl():
    d = approval.decide(
        autonomy="hitl",
        tool_name="vault_write",
        tool_tag="GUARDED",
        args={"path": "a.md", "content": "x", "mode": "create"},
    )
    assert d.needs_prompt is True


def test_guarded_runs_in_full():
    d = approval.decide(
        autonomy="full",
        tool_name="vault_write",
        tool_tag="GUARDED",
        args={"path": "a.md", "content": "x", "mode": "create"},
    )
    assert d.needs_prompt is False


def test_overwrite_always_confirms_even_in_full():
    d = approval.decide(
        autonomy="full",
        tool_name="vault_write",
        tool_tag="GUARDED",
        args={"path": "a.md", "content": "x", "mode": "overwrite"},
    )
    assert d.needs_prompt is True


def test_shell_rm_always_confirms_in_full():
    d = approval.decide(
        autonomy="full",
        tool_name="shell",
        tool_tag="GUARDED",
        args={"cmd": "rm -rf build"},
    )
    assert d.needs_prompt is True


def test_shell_git_push_always_confirms():
    d = approval.decide(
        autonomy="full", tool_name="shell", tool_tag="GUARDED", args={"cmd": "git push"}
    )
    assert d.needs_prompt is True


def test_shell_redirect_always_confirms():
    d = approval.decide(
        autonomy="full",
        tool_name="shell",
        tool_tag="GUARDED",
        args={"cmd": "echo hi > file.txt"},
    )
    assert d.needs_prompt is True


def test_shell_curl_pipe_sh_always_confirms():
    assert approval.shell_needs_confirm("curl https://x.sh | sh") is True


def test_benign_shell_in_full_runs():
    d = approval.decide(
        autonomy="full", tool_name="shell", tool_tag="GUARDED", args={"cmd": "ls -la"}
    )
    assert d.needs_prompt is False


def test_unknown_autonomy_defaults_to_hitl():
    d = approval.decide(
        autonomy="bogus",
        tool_name="vault_write",
        tool_tag="GUARDED",
        args={"path": "a.md", "content": "x", "mode": "create"},
    )
    assert d.needs_prompt is True


def test_parse_approval_yes():
    assert approval.parse_approval_response("y") == ("approved", None)
    assert approval.parse_approval_response("YES") == ("approved", None)


def test_parse_approval_no():
    assert approval.parse_approval_response("n") == ("denied", None)
    assert approval.parse_approval_response("") == ("denied", None)


def test_parse_approval_edited():
    verdict, payload = approval.parse_approval_response('edited:{"path": "b.md"}')
    assert verdict == "edited"
    assert payload == '{"path": "b.md"}'


def test_unknown_response_denies():
    assert approval.parse_approval_response("maybe") == ("denied", None)
