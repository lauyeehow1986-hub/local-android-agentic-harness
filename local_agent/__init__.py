"""local-agent: a terminal-first, on-device agentic harness.

A small local LLM (served by Ollama) drives a strict ReAct loop. This package
is the harness around that model: it builds the prompt, parses the model's
output, enforces an approval gate, dispatches tools, and feeds observations
back in. See the repo-root CLAUDE.md for the build guide and
prompts/agent_system.md for the model-facing runtime contract.
"""

__version__ = "0.1.0"
