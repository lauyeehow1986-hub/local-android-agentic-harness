"""Entrypoint: load config + agent_system.md, pick a frontend, run the loop.

    python -m local_agent.main
    AUTONOMY=full python -m local_agent.main

Runtime commands in the REPL:
    /autonomy hitl|full       toggle approval mode
    /route local|remote|auto  pick the model backend (remote = LAN box)
    /trace on|off             show/hide the ReAct trace
    /health                   check the Ollama connection(s)
    /quit                     exit
"""

from __future__ import annotations

import sys

from .config import load_config
from .frontends.terminal import TerminalFrontend
from .loop import Agent
from .ollama_client import OllamaClient


def build_agent(config) -> Agent:
    client = OllamaClient(
        base_url=config.ollama_base_url,
        model=config.model,
        num_ctx=config.num_ctx,
        temperature=config.temperature,
        timeout_s=config.request_timeout_s,
        num_predict=config.max_new_tokens,
    )
    remote_client = None
    if config.remote_base_url:
        remote_client = OllamaClient(
            base_url=config.remote_base_url,
            model=config.remote_model,
            num_ctx=config.num_ctx,
            temperature=config.temperature,
            timeout_s=config.request_timeout_s,
            num_predict=config.max_new_tokens,
        )
    return Agent(config=config, client=client, remote_client=remote_client)


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    config = load_config()
    agent = build_agent(config)
    frontend = TerminalFrontend()

    # One-shot mode: pass the task as args.
    if argv:
        task = " ".join(argv)
        agent.run_task(task, frontend)
        return 0

    frontend.on_info(
        f"local-agent ready · model={config.model} · autonomy="
        f"{config.normalized_autonomy()} · ollama={config.ollama_base_url}"
    )
    if not agent.client.health():
        frontend.on_info("WARNING: Ollama not reachable. Start it with `ollama serve`.")

    while True:
        task = frontend.read_task()
        if not task:
            continue
        if task in ("/quit", "/exit", "quit", "exit"):
            break
        if task.startswith("/autonomy"):
            parts = task.split()
            if len(parts) == 2 and parts[1] in ("hitl", "full"):
                config.autonomy = parts[1]
                frontend.on_info(f"autonomy = {config.autonomy}")
            else:
                frontend.on_info("usage: /autonomy hitl|full")
            continue
        if task.startswith("/trace"):
            parts = task.split()
            frontend.show_trace = not (len(parts) == 2 and parts[1] == "off")
            frontend.on_info(f"trace = {'on' if frontend.show_trace else 'off'}")
            continue
        if task == "/health":
            local_ok = agent.client.health()
            msg = "local ollama: " + ("ok" if local_ok else "unreachable")
            if agent.remote_client is not None:
                remote_ok = agent.remote_client.health()
                msg += " · remote: " + ("ok" if remote_ok else "unreachable")
            frontend.on_info(msg)
            continue
        if task.startswith("/route"):
            parts = task.split()
            if len(parts) == 2 and parts[1] in ("local", "remote", "auto"):
                if parts[1] != "local" and agent.remote_client is None:
                    frontend.on_info(
                        "no remote configured; set OLLAMA_REMOTE_URL to use remote/auto"
                    )
                else:
                    agent.route = parts[1]
                    frontend.on_info(f"route = {agent.route}")
            else:
                frontend.on_info("usage: /route local|remote|auto")
            continue

        agent.run_task(task, frontend)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
