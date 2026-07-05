"""Parse the model's ReAct output. This is where most agent failures live, so
be tolerant of the mess a small model emits: stray ``` fences, leading/trailing
whitespace, duplicated blocks, and an OBSERVATION the model tries to fabricate.

The model emits exactly ONE of:

    THOUGHT: <reasoning>
    ACTION: <tool_name>
    INPUT: <single-line JSON>

or

    THOUGHT: <why done>
    FINAL: <answer>

We extract the FIRST complete block and discard anything after it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class ParsedStep:
    """Result of parsing one model turn.

    Exactly one of (final) or (action+input) is meaningful, indicated by kind.
    On a JSON failure, kind == "action" but action is set and error is set and
    input_obj is None — the loop should re-prompt once.
    """

    kind: str  # "action" | "final" | "error"
    thought: str = ""
    action: Optional[str] = None
    input_obj: Optional[dict[str, Any]] = None
    final: Optional[str] = None
    error: Optional[str] = None


# Strip markdown code fences anywhere in the text. Small models love to wrap
# their whole answer in ```...``` or ```json...```.
_FENCE_RE = re.compile(r"^\s*```[a-zA-Z0-9_-]*\s*$", re.MULTILINE)


def _strip_fences(text: str) -> str:
    return _FENCE_RE.sub("", text)


def _find_label(lines: list[str], label: str) -> Optional[int]:
    """Index of the first line whose stripped form starts with LABEL:."""
    pat = label.upper() + ":"
    for i, line in enumerate(lines):
        if line.strip().upper().startswith(pat):
            return i
    return None


def _value_after_label(line: str, label: str) -> str:
    """Everything after 'LABEL:' on a line, case-insensitive on the label."""
    idx = line.upper().find(label.upper() + ":")
    if idx == -1:
        return line.strip()
    return line[idx + len(label) + 1 :].strip()


def _extract_json_object(raw: str) -> str:
    """Best-effort isolate a single JSON object from a noisy INPUT value.

    Handles a trailing comment or stray fence the model tacked on. Returns the
    substring from the first '{' to its matching '}' (brace-depth aware, string
    aware). Falls back to the stripped raw if no balanced object is found.
    """
    s = raw.strip()
    start = s.find("{")
    if start == -1:
        return s
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(s)):
        c = s[i]
        if in_str:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return s[start : i + 1]
    return s[start:]


def parse(text: str) -> ParsedStep:
    """Parse one model turn into a ParsedStep.

    Tolerant by design. Always returns a ParsedStep; never raises.
    """
    if text is None:
        return ParsedStep(kind="error", error="empty model output")

    cleaned = _strip_fences(text)
    lines = cleaned.splitlines()

    t_idx = _find_label(lines, "THOUGHT")
    thought = _value_after_label(lines[t_idx], "THOUGHT") if t_idx is not None else ""

    final_idx = _find_label(lines, "FINAL")
    action_idx = _find_label(lines, "ACTION")

    # FINAL wins if it appears before ACTION (or ACTION is absent). FINAL may
    # span multiple lines — take everything from the label to the end (we've
    # already discarded any fabricated OBSERVATION via the model stop token,
    # but guard again here).
    if final_idx is not None and (action_idx is None or final_idx < action_idx):
        first = _value_after_label(lines[final_idx], "FINAL")
        rest = lines[final_idx + 1 :]
        # Stop at a fabricated OBSERVATION or a new block if present.
        body: list[str] = [first] if first else []
        for ln in rest:
            up = ln.strip().upper()
            if up.startswith("OBSERVATION:") or up.startswith("THOUGHT:"):
                break
            body.append(ln)
        final_text = "\n".join(body).strip()
        return ParsedStep(kind="final", thought=thought, final=final_text)

    if action_idx is None:
        # No ACTION and no FINAL. If there's a THOUGHT only, treat the whole
        # thing as a FINAL so the loop can surface it rather than hang.
        stripped = cleaned.strip()
        if stripped:
            return ParsedStep(kind="final", thought=thought, final=thought or stripped)
        return ParsedStep(kind="error", error="no ACTION or FINAL found")

    action = _value_after_label(lines[action_idx], "ACTION").strip()
    # A tool name only — drop anything trailing (the model sometimes adds prose).
    action = action.split()[0] if action else action

    input_idx = _find_label(lines, "INPUT")
    if input_idx is None:
        # No INPUT line. Some tools take no args; treat as empty object so the
        # schema validator can decide. But flag so the loop can re-prompt.
        return ParsedStep(
            kind="action",
            thought=thought,
            action=action,
            input_obj=None,
            error="missing INPUT line",
        )

    # INPUT value may be on the same line and/or continue on following lines
    # until a new block label. Gather it.
    raw_first = _value_after_label(lines[input_idx], "INPUT")
    collected = [raw_first] if raw_first else []
    for ln in lines[input_idx + 1 :]:
        up = ln.strip().upper()
        if up.startswith(("OBSERVATION:", "THOUGHT:", "ACTION:", "FINAL:")):
            break
        collected.append(ln)
    raw_input = "\n".join(collected).strip()

    candidate = _extract_json_object(raw_input)
    try:
        obj = json.loads(candidate)
        if not isinstance(obj, dict):
            return ParsedStep(
                kind="action",
                thought=thought,
                action=action,
                input_obj=None,
                error="INPUT JSON was not an object",
            )
        return ParsedStep(kind="action", thought=thought, action=action, input_obj=obj)
    except json.JSONDecodeError as e:
        return ParsedStep(
            kind="action",
            thought=thought,
            action=action,
            input_obj=None,
            error=f"INPUT was not valid JSON: {e}",
        )
