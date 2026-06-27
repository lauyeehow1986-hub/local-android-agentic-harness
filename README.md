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
`/autonomy hitl|full` · `/route local|remote|auto` · `/trace on|off` · `/health` · `/quit`

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
| `AGENT_MAX_NEW_TOKENS` | `512` | per-turn decode cap (bounds worst-case latency) |
| `AGENT_REQUEST_TIMEOUT` | `900` | seconds; generous for cold prefill + throttling |
| `AGENT_STREAM` | `1` | stream tokens live to the terminal |
| `OLLAMA_REMOTE_URL` | _(unset)_ | optional faster Ollama for hybrid routing |
| `AGENT_REMOTE_MODEL` | `qwen3:30b-a3b-instruct-2507` | model on the remote box |
| `AGENT_ROUTE` | `local` | `local` / `remote` / `auto` |
| `AGENT_VISION_MODEL` | `moondream` | vision model for `analyze_image` |
| `AGENT_VISION_KEEP_ALIVE` | `0` | unload vision model immediately (RAM-safe) |
| `AGENT_MAX_IMAGE_PX` | `1024` | downscale longest image side (needs Pillow; 0 = off) |
| `AGENT_PDF_OCR_MAX_PAGES` | `5` | pages to render + OCR for scanned PDFs |
| `AGENT_WHISPER_BACKEND` | `auto` | `auto`/`whisper`/`whispercpp`/`faster` |
| `AGENT_WHISPER_MODEL` | `base` | model for openai/faster-whisper (tiny…large) |
| `AGENT_WHISPER_CPP_MODEL` | _(unset)_ | path to a ggml model for whisper.cpp |
| `AGENT_WHISPER_LANG` | _(auto)_ | language hint, e.g. `en` |
| `AGENT_ENABLE_BROWSER` | `0` | gate the heavy Playwright tool |

## Tools

`vault_search` `vault_read` `vault_list` `vault_write`*(G)* ·
`web_search` `web_scrape` `browser`*(G)* ·
`analyze_data` `analyze_image` `analyze_pdf` `transcribe` `meeting_notes` ·
`make_slides`*(G)* `make_html_report`*(G)* `rephrase` ·
`shell`*(G)* `git_sync`*(G)* `request_approval`

**(G) = GUARDED** (writes/deletes/sends/shell). In `hitl` they require approval; in `full`
they run directly **except** the always-confirm set (shell `rm`/`mv`/`git push`/`curl|sh`/
overwriting `>`, overwriting a vault note, any outbound send).

Optional backends (the harness degrades gracefully without them):
- **`browser`** — `pip install playwright && playwright install chromium` and set
  `AGENT_ENABLE_BROWSER=1`. Heavy on Termux; usually run only on a LAN box.
- **`analyze_image`** — `ollama pull moondream` (or a small Qwen2.5-VL/Qwen3-VL).
  Describes an image, answers a question about it, or **reads text from it (OCR)** —
  e.g. `{"path": "...", "question": "Transcribe all text in this image."}`. The vision
  model is loaded on demand and unloaded right after (`keep_alive=0`) so it never sits
  in RAM beside the 4B; also set `OLLAMA_MAX_LOADED_MODELS=1` on the server to be sure.
  Large photos are auto-downscaled to `AGENT_MAX_IMAGE_PX` (1024) if **Pillow** is
  installed (`pip install Pillow`), which cuts RAM and latency; without Pillow it sends
  the full image.
- **`analyze_pdf`** — text extraction tries **PyMuPDF** (most robust, handles
  encrypted/awkward PDFs) then **pypdf**. If extraction yields nothing *or fails* (e.g.
  a "codec error" on an encrypted/oddly-encoded PDF that still opens in a viewer), it
  **auto-falls-back to OCR**: render the pages and read them with the vision model.
  - **On Termux:** `pip install pypdf` for text; PyMuPDF **won't build on-device**, so
    for the OCR fallback use `pkg install poppler && pip install pdf2image` (+
    `ollama pull moondream`). That combination handles text PDFs *and* scanned/encrypted
    ones without compiling any C extensions.
  - **On desktop/LAN:** `pip install pymupdf` alone covers almost everything (text +
    rendering). **Scanned (image-only)
  PDFs are handled automatically**: when there's no extractable text it renders the
  first `AGENT_PDF_OCR_MAX_PAGES` pages and OCRs them with the vision model. The OCR
  fallback needs a PDF renderer — `pip install pymupdf` (preferred, no system binary)
  or `pip install pdf2image` + `pkg install poppler` — plus a vision model
  (`ollama pull moondream`).
- **`transcribe`** (meeting speech-to-text) — install a Whisper backend, tried in
  order: **whisper.cpp** (`whisper-cli`/`whisper-cpp`/`main` on PATH + set
  `AGENT_WHISPER_CPP_MODEL` to a ggml model — best for Termux), **openai-whisper**
  (`pip install openai-whisper`), or **faster-whisper** (`pip install faster-whisper`).
  Non-WAV audio (m4a/opus/mp3…) needs **ffmpeg** (`pkg install ffmpeg`) for whisper.cpp;
  openai-whisper handles formats itself. Saves the full transcript to
  `<audio>.transcript.txt` and, if you pass a `task`, summarizes / extracts action
  items with a chunked map-reduce so long meetings fit the small context window.
  Pass `"diarize": true` for **speaker labels** — this needs `whisperx` + a HuggingFace
  token (`HF_TOKEN`) and is heavy, so run it on the LAN/desktop box, not the phone.
- **`meeting_notes`** — one step up from `transcribe`: takes a recording **or** an existing
  transcript and returns a ready-to-save Markdown note with `## Summary / ## Decisions /
  ## Action Items` (owner table) `/ ## Follow-ups`, plus YAML frontmatter. The agent then
  saves it with `vault_write` (approved in `hitl`). Same Whisper backends as `transcribe`.

## Example prompts (what you can ask at `yh>`)

| Ask | Tool(s) the agent uses |
|---|---|
| "What did I note about recurrent events?" | `vault_search` → `vault_read` |
| "List the notes in my Daily folder" | `vault_list` |
| "Save a daily note that I deployed v10.13" | `request_approval` → `vault_write` (append) |
| "Rephrase this politely: 'send me the file now'" | `rephrase` |
| "Search the web for the OMOP CDM standard" | `web_search` |
| "Summarize https://example.com/article" | `web_scrape` |
| "Analyze /sdcard/Download/data.csv — how many rows?" | `analyze_data` |
| "Describe /sdcard/DCIM/Camera/IMG_2026.jpg" | `analyze_image` |
| "Read all the text in /sdcard/Download/receipt.png" | `analyze_image` (OCR) |
| "Summarize the PDF at /sdcard/Download/paper.pdf" | `analyze_pdf` (text, or OCR if scanned) |
| "Transcribe the meeting at /sdcard/Recordings/standup.m4a and list action items" | `transcribe` (task=action items) |
| "Transcribe …/meeting.m4a and save the notes to my vault" | `transcribe` → `request_approval` → `vault_write` |
| "Make meeting notes from …/sync.m4a and file them under Meetings/" | `meeting_notes` → `request_approval` → `vault_write` |
| "Make an HTML report titled 'Weekly' at …/weekly.html" | `request_approval` → `make_html_report` |
| "Build slides on X to …/deck.md" | `request_approval` → `make_slides` |
| "Commit and push my vault" | `request_approval` → `git_sync` |

Read-only/analysis tools (SAFE) run immediately. Anything that writes/sends/executes
(GUARDED) asks first in `hitl` mode.

## Hybrid routing (offload hard tasks to a LAN box)

The on-device 4B is fine for quick vault lookups but slow for multi-step tasks.
Point the harness at a faster Ollama (e.g. `qwen3:30b-a3b-instruct-2507` on a
32 GB+ box over Tailscale) — same model family, same tool conventions, clean
fallback:

```bash
OLLAMA_REMOTE_URL=http://<lan-ip>:11434 AGENT_ROUTE=auto python -m local_agent.main
```

- `AGENT_ROUTE=local` (default) — always the phone.
- `AGENT_ROUTE=remote` — always the LAN box.
- `AGENT_ROUTE=auto` — LAN box when its health check passes, else local.
- Switch at runtime with `/route local|remote|auto`; `/health` shows both backends.

## Eval harness (measure before you tune)

Score the agent on representative tasks — trust this over public leaderboards.

```bash
python -m local_agent.eval                       # uses local_agent/eval_tasks.json
python -m local_agent.eval --tasks my_tasks.json
```

Reports **completion rate**, **tool-selection rate**, **valid-JSON rate**,
**keyword-match rate**, and **mean tok/s**. Add your own tasks as JSON objects:
`{"id", "task", "expect_tool": <name|null>, "expect_keywords": [..]}`.

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
