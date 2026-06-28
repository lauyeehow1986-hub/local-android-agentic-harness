"""Tool registry. The single source of truth for execution; the model-facing
contract lives in prompts/agent_system.md. Tool names and tags MUST match
between the two (see CLAUDE.md). If you change one, change both.

Each tool is a Tool(name, tag, arg_schema, fn). fn(args, ctx) -> str returns a
SHORT string observation. Long outputs are truncated by the loop before they
re-enter context (latency at ~3 tok/s is dominated by prefill size).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

# A tool function takes its parsed args and a context object (carrying config
# and the ollama client for tools that need the local model) and returns a
# short observation string.
ToolFn = Callable[[dict[str, Any], "ToolContext"], str]


@dataclass
class ToolContext:
    """Everything a tool may need at call time, injected by the loop."""

    config: Any
    client: Any = None  # OllamaClient, for tools that call the local model


@dataclass
class Tool:
    name: str
    tag: str  # "SAFE" | "GUARDED"
    fn: ToolFn
    required: tuple[str, ...] = ()
    optional: tuple[str, ...] = ()
    description: str = ""

    def validate(self, args: dict[str, Any]) -> Optional[str]:
        """Return an error string if required args are missing, else None."""
        if not isinstance(args, dict):
            return "INPUT must be a JSON object"
        missing = [k for k in self.required if k not in args]
        if missing:
            return f"missing required arg(s): {', '.join(missing)}"
        return None


# The registry is populated by register() calls in each tool module, wired up
# in build_registry() below to avoid import-order surprises.
def build_registry() -> dict[str, Tool]:
    from . import vault, web, data, generate, system, maps, device, research

    reg: dict[str, Tool] = {}

    def add(tool: Tool) -> None:
        reg[tool.name] = tool

    for mod in (vault, web, data, generate, system, maps, device, research):
        for tool in mod.TOOLS:
            add(tool)
    return reg


__all__ = ["Tool", "ToolContext", "ToolFn", "build_registry"]
