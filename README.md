# local-agent

A terminal-first, **fully on-device** personal agent for Android (Termux). A small
local LLM served by **Ollama** drives a strict **ReAct loop**; a Python harness parses
the model's output, enforces an approval gate, dispatches tools, and reads/writes an
Obsidian vault. **No Claude Code and no cloud LLM are in the runtime loop** — the
harness is self-contained.

> Two docs, don't confuse them:
> - **`CLAUDE.md`** (repo root) — the build guide for the developer (and for Claude Code).
> - **`prompts/agent_system.md`** — the runtime system prompt loaded into the model each
>   turn. It is the model-facing contract (ReAct format + tool spec).

## What's here

```
prompts/agent_system.md     # runtime system prompt (ReAct contract + tool spec)
local_agent/
  config.py                 # paths, model, Ollama URL, autonomy, num_ctx … (env-overridable)
  ollama_client.py          # thin stdlib (urllib) client; configurable base_url
  parser.py                 # THOUGHT/ACTION/INPUT & THOUGHT/FINAL parser (tolerant)
  approval.py               # SAFE/GUARDED policy + always-confirm set
  loop.py                   # the ReAct controller
  tools/                    # vault, web, data, generate, system tools + registry
  frontends/terminal.py     # v1 stdin/stdout REPL + approval prompts
  main.py                   # entrypoint
tests/                      # parser, approval, tools, full-loop (no Ollama needed)
```

## Design priorities (in order)
1. Tool-calling reliability  2. Not corrupting the vault  3. Staying within phone RAM
4. Usable latency. The model is small and slow (~3 tok/s CPU-only), so the **harness
compensates**: terse prompts, single-line JSON, truncated observations, a step cap, and a
one-shot JSON re-prompt.

---

## Quick start

### Desktop dev (recommended first — steps 1–4 are testable without the phone)

```bash
pip install -r requirements.txt          # only needs pytest; runtime is stdlib
pytest -q                                # 50+ tests, no Ollama required

# point at a desktop Ollama and run the REPL
ollama pull qwen3:4b-instruct-2507-q4_K_M
OLLAMA_BASE_URL=http://127.0.0.1:11434 python -m local_agent.main
```

### On the phone (Termux — the deployment target)

```bash
pkg install python
pip install -r requirements.txt
termux-setup-storage                     # one-time: grants access to /storage/emulated/0

# Ollama in Termux
OLLAMA_KV_CACHE_TYPE=q8_0 ollama serve &  # q8_0 halves KV-cache RAM
ollama pull qwen3:4b-instruct-2507-q4_K_M

# run (terminal frontend, AUTONOMY=hitl by default)
python -m local_agent.main
AUTONOMY=full python -m local_agent.main  # autonomous mode (still confirms destructive ops)
```

One-shot: `python -m local_agent.main "what did I note about OMOP date mapping?"`

### REPL commands
`/autonomy hitl|full` · `/trace on|off` · `/health` · `/quit`

---

## Configuration (all env-overridable — see `config.py`)

| Env var | Default | Notes |
|---|---|---|
| `OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | desktop Ollama, or a Tailscale LAN box later |
| `AGENT_MODEL` | `qwen3:4b-instruct-2507-q4_K_M` | **Instruct (non-thinking)** variant only |
| `AGENT_VAULT` | `/storage/emulated/0/Download/Obsidian/Yh android` | note the space in the path |
| `AUTONOMY` | `hitl` | `hitl` (approve guarded ops) or `full` |
| `AGENT_NUM_CTX` | `6144` | keep small; large ctx OOM-kills on 8 GB |
| `AGENT_TEMPERATURE` | `0.2` | deterministic tool calls |
| `OLLAMA_KV_CACHE_TYPE` | `q8_0` | set on `ollama serve`, not the harness |
| `AGENT_MAX_STEPS` | `8` | loop cap — small models drift in long chains |
| `AGENT_ENABLE_BROWSER` | `0` | gate the heavy Playwright tool |

## Tools

`vault_search` `vault_read` `vault_list` `vault_write`*(G)* ·
`web_search` `web_scrape` `browser`*(G)* ·
`analyze_data` `analyze_image` `analyze_pdf` `transcribe` ·
`make_slides`*(G)* `make_html_report`*(G)* `rephrase` ·
`shell`*(G)* `git_sync`*(G)* `request_approval`

**(G) = GUARDED** (writes/deletes/sends/shell). In `hitl` they require approval; in `full`
they run directly **except** the always-confirm set (shell `rm`/`mv`/`git push`/`curl|sh`/
overwriting `>`, overwriting a vault note, any outbound send).

Optional backends (the harness degrades gracefully without them):
- **`browser`** — `pip install playwright && playwright install chromium` and set
  `AGENT_ENABLE_BROWSER=1`. Heavy on Termux; usually run only on a LAN box.
- **`analyze_image`** — `ollama pull moondream` (or a small Qwen-VL). Loaded on demand,
  never resident alongside the 4B.
- **`analyze_pdf`** — `pip install pypdf` (pure-Python). Extracts text and, if the
  model client is available, summarizes/answers a task over it. Scanned (image-only)
  PDFs have no extractable text — use `analyze_image`/OCR for those.
- **`transcribe`** — a `whisper` / `whisper-cpp` binary on `PATH`.

## Latency tuning (this chip is slow — ~3 tok/s, prefill-bound)

The first turn of a session spends most of its time *reading* the prompt (prefill).
The system prompt dominates that. To make it usable:

- **Use the REPL, not one-shot.** `python -m local_agent.main` (no args) keeps the
  process alive so Ollama's KV cache of the system prompt is reused — turns after the
  first only prefill the new transcript tokens and are much faster. Each one-shot
  relaunch throws that warmth away.
- **Keep the model resident:** start the server with
  `OLLAMA_KEEP_ALIVE=30m OLLAMA_KV_CACHE_TYPE=q8_0 ollama serve` so it isn't reloaded
  between tasks.
- **Use the compact system prompt** to cut ~1070 tokens (~70%) off every turn's prompt
  overhead, at some cost to the model's in-context examples:
  ```bash
  AGENT_SYSTEM_PROMPT=prompts/agent_system_compact.md python -m local_agent.main
  ```
  The full `prompts/agent_system.md` stays the default (tool-calling reliability is
  priority #1); the compact variant is opt-in for speed. Both keep the tool contract
  identical.

## Gotchas
- The vault path **has a space**. pathlib handles it; every shell use is `shlex.quote`d.
- Run Ollama **CPU-only** on the Dimensity 6300 (Mali Vulkan offload is unreliable).
- Don't keep two models resident — the vision model is swapped in on demand.
- Thermal throttling slows sustained loops; timeouts are generous on purpose.

---

## What YOU need to do (setup checklist)

See the device-side steps above. The short version:

1. **Install Termux** (from F-Droid, not the outdated Play Store build) and run
   `pkg install python`, then `pip install -r requirements.txt`.
2. **`termux-setup-storage`** once, so Termux can read/write the Obsidian vault under
   `/storage/emulated/0`.
3. **Install Ollama in Termux**, `ollama serve` with `OLLAMA_KV_CACHE_TYPE=q8_0`, and
   `ollama pull qwen3:4b-instruct-2507-q4_K_M`.
4. **Confirm the vault path** matches `AGENT_VAULT` (default
   `/storage/emulated/0/Download/Obsidian/Yh android`). Override the env var if yours differs.
5. `python -m local_agent.main` and start asking.

### Obsidian community plugins

**None are required.** The harness works directly on the vault's Markdown files on disk,
so the agent functions whether or not Obsidian is even open. Obsidian auto-detects files
the agent writes externally — no plugin needed for that.

Optional, only if you want a nicer experience around what the agent produces:

- **Daily Notes** *(core plugin, not community — just enable it)*: the agent appends
  captures to `Daily/YYYY-MM-DD.md`; this makes those notes first-class.
- **Dataview** *(community)*: query/aggregate notes the agent tags or fills with frontmatter.
- **Templater** *(community)*: if you want richer note scaffolds than the agent's plain
  Markdown writes.

If you'd rather not install anything, skip all three — the agent doesn't depend on them.
