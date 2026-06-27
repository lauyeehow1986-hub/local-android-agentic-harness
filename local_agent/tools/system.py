"""System / control tools: shell / git_sync / request_approval.

shell MUST shlex.quote every path and is GUARDED + part of the always-confirm
set for destructive patterns (handled in approval.py). request_approval is the
model's way of pausing for a user decision; the harness intercepts it specially
in the loop, but it's registered here so the registry is complete.
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any

from . import Tool, ToolContext


def shell(args: dict[str, Any], ctx: ToolContext) -> str:
    cmd = str(args.get("cmd", "")).strip()
    if not cmd:
        return "error: 'cmd' is required"
    # We run via the shell because the model emits full command lines, but the
    # approval gate (always-confirm set) has already vetted destructive forms.
    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return "shell timed out after 120s"
    except Exception as e:  # noqa: BLE001
        return f"shell error: {e}"
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    parts = [f"rc={proc.returncode}"]
    if out:
        parts.append(f"stdout:\n{out}")
    if err:
        parts.append(f"stderr:\n{err}")
    return "\n".join(parts)


def git_sync(args: dict[str, Any], ctx: ToolContext) -> str:
    repo_path = str(args.get("repo_path", "")).strip()
    message = str(args.get("message", "agent sync")).strip()
    if not repo_path:
        return "error: 'repo_path' is required"
    repo = Path(repo_path)
    if not (repo / ".git").exists():
        return f"not a git repo: {repo_path}"
    # Quote paths defensively (vault path has a space). Build the command list
    # for git but keep the chdir explicit via -C.
    rp = shlex.quote(str(repo))
    msg = shlex.quote(message)
    script = (
        f"git -C {rp} add -A && "
        f"(git -C {rp} diff --cached --quiet && echo 'nothing to commit' || "
        f"(git -C {rp} commit -m {msg} && git -C {rp} push))"
    )
    try:
        proc = subprocess.run(
            script, shell=True, capture_output=True, text=True, timeout=180
        )
    except subprocess.TimeoutExpired:
        return "git_sync timed out"
    except Exception as e:  # noqa: BLE001
        return f"git_sync error: {e}"
    out = ((proc.stdout or "") + (proc.stderr or "")).strip()
    return f"rc={proc.returncode}\n{out}"[:1500]


def open_app(args: dict[str, Any], ctx: ToolContext) -> str:
    """Open a URL or app deeplink on the phone (Termux).

    For things like launching Google Maps navigation or the Grab app to a search.
    It only OPENS the target — it cannot place orders or handle payment; the user
    completes those in the app. GUARDED (it launches an external app).
    """
    target = str(args.get("target", "")).strip()
    if not target:
        return "error: 'target' (a URL or deeplink) is required"
    opener = shutil.which("termux-open-url")
    try:
        if opener:
            subprocess.run([opener, target], timeout=20, capture_output=True, text=True)
            return f"opened: {target}"
        am = shutil.which("am")
        if am:
            subprocess.run(
                ["am", "start", "-a", "android.intent.action.VIEW", "-d", target],
                timeout=20,
                capture_output=True,
                text=True,
            )
            return f"opened via intent: {target}"
        return (
            "cannot open: no opener found. Install termux-api "
            "(`pkg install termux-api`) for termux-open-url."
        )
    except Exception as e:  # noqa: BLE001
        return f"open error: {e}"


def request_approval(args: dict[str, Any], ctx: ToolContext) -> str:
    # This should be intercepted by the loop/frontend. If a tool dispatch ever
    # reaches here, it means no frontend handled it — return a neutral note.
    action = str(args.get("action", "")).strip()
    return f"approval requested for: {action} (awaiting user via frontend)"


TOOLS = [
    Tool(
        name="shell",
        tag="GUARDED",
        fn=shell,
        required=("cmd",),
        description="Run a Termux shell command.",
    ),
    Tool(
        name="git_sync",
        tag="GUARDED",
        fn=git_sync,
        required=("repo_path",),
        optional=("message",),
        description="Commit & push a repo/vault.",
    ),
    Tool(
        name="open_app",
        tag="GUARDED",
        fn=open_app,
        required=("target",),
        description="Open a URL/app deeplink on the phone (e.g. Maps nav, Grab app). Cannot pay/order.",
    ),
    Tool(
        name="request_approval",
        tag="SAFE",
        fn=request_approval,
        required=("action",),
        optional=("details",),
        description="Pause for a user approval decision.",
    ),
]
