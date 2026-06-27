"""Approval / autonomy policy. Owns the SAFE/GUARDED decision and the
always-confirm set. It does NOT prompt the user — the frontend owns the actual
y/n/edit interaction. Keep policy and IO separate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional

# Shell patterns that require confirmation even in `full` mode. Matched against
# the `cmd` argument of a `shell` tool call.
_ALWAYS_CONFIRM_SHELL = [
    re.compile(r"\brm\b"),
    re.compile(r"\bmv\b"),
    re.compile(r"\bgit\s+push\b"),
    re.compile(r"curl[^\n|]*\|\s*sh\b"),
    re.compile(r">"),  # any output redirect that can overwrite
]


@dataclass
class ApprovalDecision:
    """Outcome of the approval gate before a tool runs."""

    needs_prompt: bool
    reason: str = ""
    # For GUARDED tools in hitl mode the model is expected to call
    # request_approval itself; the harness still double-checks here.


def shell_needs_confirm(cmd: str) -> bool:
    """True if a shell command hits the always-confirm set."""
    return any(p.search(cmd) for p in _ALWAYS_CONFIRM_SHELL)


def is_always_confirm(tool_name: str, tool_tag: str, args: dict[str, Any]) -> bool:
    """True if this call must prompt even in `full` mode.

    - shell commands containing rm / mv / git push / curl|sh / overwriting >
    - deleting or overwriting any vault note (vault_write overwrite mode)
    - any outbound send (reserved for future telegram/email adapters)
    """
    if tool_name == "shell":
        return shell_needs_confirm(str(args.get("cmd", "")))
    if tool_name == "vault_write":
        return str(args.get("mode", "")).lower() == "overwrite"
    if tool_name in ("send_telegram", "send_email"):  # future adapters
        return True
    return False


def decide(
    *,
    autonomy: str,
    tool_name: str,
    tool_tag: str,
    args: dict[str, Any],
) -> ApprovalDecision:
    """Decide whether a tool call must be confirmed by the user.

    SAFE tools never prompt. GUARDED tools always prompt in hitl. In full,
    GUARDED tools run directly EXCEPT the always-confirm set.
    """
    autonomy = autonomy if autonomy in ("hitl", "full") else "hitl"

    if tool_tag == "SAFE":
        return ApprovalDecision(needs_prompt=False)

    # GUARDED from here.
    if is_always_confirm(tool_name, tool_tag, args):
        return ApprovalDecision(
            needs_prompt=True, reason="always-confirm action (destructive/outbound)"
        )

    if autonomy == "full":
        return ApprovalDecision(needs_prompt=False, reason="full autonomy")

    # hitl: every GUARDED tool prompts.
    return ApprovalDecision(needs_prompt=True, reason="guarded tool in hitl mode")


def parse_approval_response(raw: str) -> tuple[str, Optional[str]]:
    """Normalize a frontend approval response.

    Returns (decision, payload) where decision in {approved, denied, edited}.
    For 'edited', payload is the new INPUT JSON string. Accepts y/n shortcuts.
    """
    s = (raw or "").strip()
    low = s.lower()
    if low in ("y", "yes", "approve", "approved", "a"):
        return "approved", None
    if low in ("n", "no", "deny", "denied", "d", ""):
        return "denied", None
    if low.startswith("edited:"):
        return "edited", s[len("edited:") :].strip()
    if low.startswith("e:"):
        return "edited", s[len("e:") :].strip()
    # Anything else is treated as a denial to stay safe.
    return "denied", None
