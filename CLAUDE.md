# CLAUDE.md — Build Guide for Claude Code

> **This file is for Claude Code.** It is the developer-facing guide for building and
> maintaining this repository. It is NOT the agent's runtime prompt.
> The local agent's runtime system prompt lives at `prompts/agent_system.md` and is loaded
> into the Ollama loop at runtime — do not confuse the two. Never load this file into the agent.

---

## Project: `local-agent` — a terminal-first local agentic harness

A single-user personal agent that runs **fully on-device** on an Android phone (cph2637) inside
Termux. A small local LLM served by **Ollama** drives a **ReAct loop**; a Python harness parses the
model's output, dispatches tools, enforces an approval gate, and reads/writes an Obsidian vault.
No Claude Code and no cloud LLM are in the runtime loop — the harness is self-contained.

**Design priorities, in order:** (1) tool-calling reliability, (2) not corrupting the vault,
(3) staying within phone RAM, (4) usable latency. Build defensively; the local model is small and
slow, so the *harness* must compensate.

---

## Runtime environment (the deployment target)

- **Device:** cph2637, MediaTek Dimensity 6300 (2× Cortex-A76 @ 2.4 GHz + 6× A55, LPDDR4X ≈ 17 GB/s), 8 or 12 GB RAM, ColorOS 14 / Android 14.
- **Shell:** Termux (Python 3, `pkg`-installed). Not a normal Linux FS — see Gotchas.
- **LLM server:** Ollama running locally in Termux.
- **Vault:** Obsidian at `/storage/emulated/0/Download/Obsidian/Yh android` (note the **space** in the path).
- **Frontend (v1):** terminal / TUI over stdin/stdout. **Telegram is a later, optional adapter — do not couple the loop to it.**

You (Claude Code) are running on a dev machine, not the phone. Write code that *targets* the phone
but is testable on a desktop with a desktop Ollama instance.

---

## Model decision (verified — build against this exactly)

**Primary on-device model: `qwen3:4b-instruct-2507-q4_K_M`** (Qwen3-4B-Instruct-2507, Apache 2.0, ~2.5 GB).
Chosen over Qwen2.5 3B after benchmarking: newer (Aug 2025), purpose-tuned for tool calling
(BFCL v3 ~66; τ²-bench Telecom 27, beating much larger prior-gen models), fits 8 GB.

- Pull: `ollama pull qwen3:4b-instruct-2507-q4_K_M`
- **Use the Instruct (non-thinking) variant, never the Thinking variant** — chain-of-thought is unaffordable at this speed.
- Ollama options to set in the harness:
  - `num_ctx`: 4096–8192 (NOT the model's 256K max — KV cache must fit in RAM)
  - `temperature`: 0.1–0.3 (deterministic tool calls)
  - env `OLLAMA_KV_CACHE_TYPE=q8_0` (halves KV-cache RAM at negligible quality cost)
- **Fallback (speed):** `qwen2.5:3b-instruct-q4_K_M` or `llama3.2:3b` for trivial dispatch if 4B is too slow.
- **Vision (load on demand, not resident):** a Qwen2.5-VL / Qwen3-VL small or `moondream` for `analyze_image`.

**Performance reality to design around:** decode is ~2.5–4 tok/s on this chip, throttling to ~3 tok/s
sustained, CPU-only (Mali GPU offload is unreliable on this hardware — do not attempt Vulkan offload).
One ReAct step (~150 output tokens) ≈ 50 s. Therefore the harness and prompt must:
- keep generated tokens per step minimal (terse THOUGHT, single-line JSON),
- keep tool **outputs** short before they re-enter context (truncate/summarize long observations),
- cap context, and cap the loop (~8 tool calls/task).

**Hybrid escape hatch (later milestone):** route hard multi-step tasks to `qwen3:30b-a3b-instruct-2507`
on a 32 GB+ LAN box over Tailscale. Same family → same tool conventions → clean fallback. The 30B-A3B
MoE needs ~18 GB resident and **cannot** run on the phone (MoE saves compute, not memory). Design the
Ollama client with a configurable base URL so a remote endpoint is a config change, not a rewrite.

---

## Architecture

```
prompts/agent_system.md      # runtime system prompt (ReAct contract + tool spec) — loaded each turn
local_agent/
  __init__.py
  config.py                  # paths, model name, Ollama URL, AUTONOMY default, num_ctx, etc.
  ollama_client.py           # thin client; configurable base_url (local now, Tailscale later)
  loop.py                    # the ReAct controller: prompt -> generate -> parse -> dispatch -> observe
  parser.py                  # parse THOUGHT/ACTION/INPUT and THOUGHT/FINAL; validate single-line JSON
  approval.py                # SAFE/GUARDED logic, always-confirm set, hitl<->full toggle
  tools/
    __init__.py              # TOOL_REGISTRY: name -> callable, tag (SAFE/GUARDED), arg schema
    vault.py                 # vault_search/read/list/write
    web.py                   # web_search, web_scrape, browser (playwright)
    data.py                  # analyze_data, analyze_image, transcribe
    generate.py              # make_slides, make_html_report, rephrase
    system.py                # shell, git_sync, request_approval
  frontends/
    terminal.py              # v1 stdin/stdout REPL + approval prompts
    telegram.py              # later; same IO interface as terminal
  main.py                    # entrypoint: load config + agent_system.md, pick frontend, run loop
tests/
  test_parser.py             # parser must be bulletproof — most failures originate here
  test_approval.py
  test_tools_stub.py
requirements.txt
README.md
```

**The contract between harness and model is `prompts/agent_system.md`.** Tool names, tags, and the
ReAct format defined there MUST match `tools/__init__.py` exactly. If you change a tool name in one,
change it in both. Treat `agent_system.md` as the source of truth for the model-facing contract and
the registry as the source of truth for execution.

---

## ReAct protocol the harness must support

The model emits ONE of:

```
THOUGHT: <reasoning>
ACTION: <tool_name>
INPUT: <single-line JSON>
```
or
```
THOUGHT: <why done>
FINAL: <answer>
```

Parser requirements (`parser.py`):
- Extract exactly one block. Be tolerant of stray markdown fences and leading/trailing whitespace the small model will emit anyway — strip them.
- `INPUT` must parse as valid JSON. **On malformed JSON, re-prompt once** with a terse correction ("Your INPUT was not valid JSON. Resend the same ACTION with valid single-line JSON.") before failing the step.
- Never let the model fabricate an `OBSERVATION:` — strip anything after the first complete block.
- The harness appends `OBSERVATION: <result>` to the transcript and re-invokes the model.

---

## Approval / autonomy (`approval.py`)

- Runtime flag `AUTONOMY` ∈ {`hitl` (default), `full`}, injected into the system prompt as `AUTONOMY=<value>` each session, and toggleable at runtime via a frontend command (e.g. `/autonomy full`).
- Tools are tagged **SAFE** (reads, search, analysis, rephrase) or **GUARDED** (writes, deletes, sends, shell, git push).
- `hitl`: GUARDED tools require the model to first call `request_approval`; the harness blocks on a frontend prompt returning `approved` / `denied` / `edited:<json>`.
- `full`: GUARDED tools run directly **except the always-confirm set**, which prompts even in `full`:
  - `shell` containing `rm`, `mv` over existing files, `git push`, `curl|sh`, or overwriting `>` redirects
  - deleting/overwriting any vault note
  - any outbound send (future Telegram/email)
- The frontend owns the actual y/n/edit prompt; `approval.py` owns the policy. Keep them separate.

---

## Tool registry (`tools/`)

Each tool: `name`, `tag` (SAFE/GUARDED), `arg_schema`, `fn(args) -> str`. Tools return a **short string
observation**. Truncate large outputs (e.g. cap web_scrape/vault_read returns, summarize if needed) —
long observations destroy latency by bloating the next prefill.

Tool list (must match `agent_system.md`):
`vault_search` `vault_semantic_search` `vault_read` `vault_list` `vault_write`(G) · `web_search` `web_scrape` `browser`(G) `research` ·
`analyze_data` `check_data` `query_csv` `clean_data`(G) `analyze_image` `analyze_pdf` `transcribe` `meeting_notes` · `make_slides`(G) `make_html_report`(G) `report_csv`(G) `diagram`(G) `rephrase` ·
`maps` `open_app`(G) `clipboard` `notify` `location` `speak` · `screenshot` `find_on_screen` `tap`(G) `swipe`(G) `type_text`(G) `screen_automate`(G) · `latest_file` `shell`(G) `git_sync`(G) `request_approval`.

Implementation notes:
- **`vault_write`** modes: `create` / `overwrite` / `append`. One note per call (individually approvable). Preserve YAML frontmatter. Daily logs append to `Daily/YYYY-MM-DD.md`.
- **`browser`** uses Playwright; on Termux this is heavy — gate behind a feature flag and document install separately; it may run only on the LAN box.
- **`analyze_image`** loads the vision model on demand, then unloads — never keep it resident alongside the 4B.
- **`shell`** must `shlex.quote` every path. See Gotchas.

---

## Build order (do this, in this sequence)

1. **Skeleton + config + Ollama client.** Verify you can round-trip a prompt to local Ollama and get text back.
2. **Parser + tests.** `test_parser.py` first — feed it messy real-model outputs (fences, double blocks, bad JSON) and make it bulletproof. This is where most agent failures live.
3. **Loop with STUBBED tools.** Every tool returns a canned observation. Get the full cycle working end-to-end: prompt → parse → (approval) → stub dispatch → observation → next turn → FINAL.
4. **Approval + autonomy + terminal frontend.** Exercise hitl and full, including the always-confirm set.
5. **Real tools, one at a time, each with a test:** vault ops first (highest value, lowest risk), then web_search/web_scrape, then generation tools, then shell/git, then the heavy/optional ones (browser, vision, transcribe).
6. **Eval harness.** 20–50 of YH's representative agentic tasks scoring: valid-JSON rate, correct-tool-selection rate, multi-turn completion, tok/s and wall-clock per step. Trust this over public leaderboards.
7. **(Optional) Telegram adapter** as a second frontend implementing the same IO interface.
8. **(Optional) Hybrid routing** to the Tailscale 30B-A3B box for hard tasks.

Keep steps 1–4 fully testable on a desktop Ollama before touching the phone.

---

## Conventions

- Python 3, standard library first. Minimize dependencies (Termux/ARM wheels can be painful — prefer pure-Python; this mirrors YH's stdlib-only preference on other Termux projects). Keep `requirements.txt` lean.
- Type hints on public functions. Small, single-purpose modules.
- All config in `config.py` (and env overrides) — no hardcoded paths in logic.
- Log each turn (THOUGHT/ACTION/INPUT/OBSERVATION) to a rotating file for debugging the loop.
- Tests with `pytest`. Parser and approval logic must have coverage before real tools are wired.
- Terse output to the user in FINAL answers (YH prefers terse).

---

## Gotchas (read before coding)

- **Vault path has a space:** `/storage/emulated/0/Download/Obsidian/Yh android`. Fine for Python `pathlib`, but every `shell`/subprocess use MUST `shlex.quote`. Never build shell strings by concatenation.
- **Termux storage:** requires `termux-setup-storage` once; `/storage/emulated/0` is the shared Android storage. Document this in README.
- **No GPU offload:** Dimensity 6300 Mali driver is unreliable for Q4 inference — run Ollama CPU-only. Don't add Vulkan flags.
- **Thermal throttling:** sustained loops slow down; don't set aggressive timeouts that assume cold-start speed.
- **KV cache RAM:** large `num_ctx` will OOM-kill on 8 GB. Keep it small; use `q8_0` KV cache.
- **Don't keep two models resident:** unload the 4B before loading the vision model and vice versa.
- **Two CLAUDE.md files:** this one (Claude Code build guide, repo root) vs `prompts/agent_system.md` (runtime). Keep them distinct; keep tool names in sync.

---

## Run (target: Termux on device)

```bash
# one-time
pkg install python
pip install -r requirements.txt
termux-setup-storage
ollama serve &                       # or rely on Ollama's service
ollama pull qwen3:4b-instruct-2507-q4_K_M

# run
python -m local_agent.main            # terminal frontend, AUTONOMY=hitl by default
AUTONOMY=full python -m local_agent.main
```

Desktop dev: point `OLLAMA_BASE_URL` at the desktop Ollama and run the same command.
