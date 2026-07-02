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

## Capabilities at a glance

- **Notes (Obsidian vault)** — keyword search, **semantic search** (embeddings), read, list,
  and approval-gated writes; "today" resolves to `Daily/<date>.md`.
- **Web & research** — search, scrape (with optional **JS rendering** via a headless Chrome),
  and **academic search** (arXiv / PubMed).
- **Documents & images** — **PDF** (text + scanned/encrypted **OCR**), **image OCR** via
  Tesseract (accurate on receipts — no hallucinated digits) and scene description via a vision model.
- **Speech** — **meeting transcription** (whisper.cpp) → structured **meeting notes**, and a
  hands-free **voice loop** (talk to the agent, it speaks back).
- **Maps & device** — directions/place search, open an app/deeplink (Maps, Grab), clipboard,
  notifications, GPS location, text-to-speech, "newest file" lookup.
- **Data** — CSV stats (quartiles, sd, correlations) + optional histogram.
- **Generation** — slide deck, HTML report, rephrase.
- **Automation & ops** — **batch/cron** jobs, an **eval harness** with **A/B model comparison**,
  **hybrid routing** to a LAN box, and token **streaming**.
- **Safe by design** — every write/send/shell action is approval-gated (`hitl`), with an
  always-confirm set even in `full`; nothing touches money or credentials.

## What's here

```
prompts/
  agent_system.md           # runtime system prompt (full ReAct contract + tool spec)
  agent_system_compact.md   # leaner prompt for speed (AGENT_SYSTEM_PROMPT)
local_agent/
  config.py                 # all settings (env-overridable)
  ollama_client.py          # stdlib (urllib) client: generate/stream/embed, configurable base_url
  parser.py                 # THOUGHT/ACTION/INPUT & THOUGHT/FINAL parser (tolerant)
  approval.py               # SAFE/GUARDED policy + always-confirm set
  loop.py                   # the ReAct controller (streaming, hybrid routing, DATE inject)
  tools/                    # vault, web, data, generate, system, maps, device, research + registry
  frontends/terminal.py     # stdin/stdout REPL + approval prompts
  main.py                   # entrypoint (REPL / one-shot)
  batch.py                  # non-interactive runner for cron / task lists
  eval.py                   # eval harness + A/B model comparison
  vault_index.py            # semantic search index (embeddings)
  voice.py                  # voice loop (mic → whisper → agent → TTS)
scripts/
  setup-termux.sh           # one-shot installer (idempotent)
  browser-bridge/           # Node + Termux-Chromium HTTP bridge for JS rendering
jobs/                       # example batch jobs (daily-digest, weekly-review)
docs/TERMUX_SETUP.md        # full step-by-step phone setup + troubleshooting
tests/                      # 118 tests — parser, approval, tools, loop, eval, capabilities …
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

Full step-by-step below. (Also in **[docs/TERMUX_SETUP.md](docs/TERMUX_SETUP.md)** with a
troubleshooting table.) Do **Step 0 + 1** for a working agent; add any optional step you want.

> **Shortcut — install everything at once:** after Step 0 + cloning the repo (Step 1b),
> run `bash scripts/setup-termux.sh` to do every step automatically (idempotent; ~4 GB
> download — use Wi-Fi). Skip heavy parts with `SKIP_WHISPER=1 SKIP_BROWSER=1 SKIP_MODELS=1`.
> Then jump to "Daily startup".

#### Step 0 — Termux base (once)
Install Termux **from F-Droid** (not the Play Store build), then:
```bash
termux-setup-storage          # tap Allow — grants access to /storage/emulated/0
pkg upgrade -y                # keep packages in sync (prevents the cmake/jsoncpp error)
pkg install -y python git
```

#### Step 1 — Core: Ollama + model + harness (required)
```bash
# 1a. Ollama — ALWAYS start from $HOME, or llama-server later dies with "getcwd failed"
cd ~
OLLAMA_KEEP_ALIVE=30m OLLAMA_KV_CACHE_TYPE=q8_0 ollama serve &
ollama pull qwen3:4b-instruct-2507-q4_K_M

# 1b. The harness
cd ~
git clone https://github.com/lauyeehow1986-hub/local-android-agentic-harness.git
cd local-android-agentic-harness && git checkout claude/build-from-markdown-tbl713 && git pull
pip install -r requirements.txt

# 1c. Run it
python -m local_agent.main                 # AUTONOMY=hitl by default
```
```
yh> what did I note about recurrent events?
```
That's a working agent (vault + web). One-shot mode: `python -m local_agent.main "your task"`.

#### Step 2 — PDF reading (optional)
```bash
pip install pypdf                          # text PDFs
pkg install -y poppler && pip install pdf2image   # scanned/encrypted PDFs (OCR)
```
*(Don't `pip install pymupdf` — it won't build on Termux.)*

#### Step 3 — Image / receipt OCR (optional, recommended)
```bash
pkg install -y tesseract                   # accurate OCR — no hallucinated digits
pip install Pillow                         # image preprocessing
ollama pull moondream                      # vision model, for describing scenes
```

#### Step 4 — Meeting transcription (optional)
```bash
pkg install -y cmake clang make ffmpeg
cd ~ && git clone https://github.com/ggml-org/whisper.cpp && cd whisper.cpp
cmake -B build && cmake --build build -j --config Release
sh ./models/download-ggml-model.sh base.en
ln -sf "$PWD/build/bin/whisper-cli" "$PREFIX/bin/whisper-cli"
echo "export AGENT_WHISPER_CPP_MODEL=\"$PWD/models/ggml-base.en.bin\"" >> ~/.bashrc
export AGENT_WHISPER_CPP_MODEL="$PWD/models/ggml-base.en.bin"
whisper-cli -m "$AGENT_WHISPER_CPP_MODEL" -f samples/jfk.wav -otxt   # verify
```

#### Step 5 — Maps & opening apps / Grab (optional)
```bash
pkg install -y termux-api                  # for open_app (launching Maps/Grab)
```

#### Step 6 — On-device browser / JS pages (optional)
```bash
pkg install -y nodejs x11-repo && pkg install -y chromium
cd ~/local-android-agentic-harness/scripts/browser-bridge && npm install
echo 'export AGENT_BROWSER_REMOTE_URL=http://127.0.0.1:3000' >> ~/.bashrc
export AGENT_BROWSER_REMOTE_URL=http://127.0.0.1:3000
# run the bridge in its own tab (see Daily startup)
```

#### Step 7 — Scheduled jobs (optional)
```bash
pkg install -y cronie
crond
crontab -e
# 0 7 * * *  cd ~/local-android-agentic-harness && AUTONOMY=full \
#   python -m local_agent.batch --approve jobs/daily-digest.txt >> ~/jobs/digest.log 2>&1
```

#### Step 8 — Extra capabilities (optional)
Semantic search, voice, device tools, research, stats — quick enable:
```bash
ollama pull nomic-embed-text && python -m local_agent.vault_index   # semantic search
pkg install -y termux-api                                           # voice + clipboard/notify/location
pip install matplotlib                                              # analyze_data plots
# research (arXiv/PubMed) needs nothing
```
Full usage for each is in **"Additional capabilities — step by step"** below.

#### Daily startup (three Termux tabs)
```bash
# Tab 1 — Ollama (from $HOME)
cd ~ && OLLAMA_KEEP_ALIVE=30m OLLAMA_KV_CACHE_TYPE=q8_0 ollama serve

# Tab 2 — browser bridge (only if you did Step 6)
cd ~/local-android-agentic-harness/scripts/browser-bridge && \
  CHROMIUM_PATH="$(command -v chromium-browser || command -v chromium)" node server.js

# Tab 3 — the agent
cd ~/local-android-agentic-harness && python -m local_agent.main
```

#### Quick troubleshooting
| Symptom | Fix |
|---|---|
| `HTTP 404 ... model not found` | `ollama pull qwen3:4b-instruct-2507-q4_K_M` |
| `getcwd failed: No such file or directory` | restart `ollama serve` from `cd ~` |
| `cmake ... cannot locate symbol _ZN4Json` | `pkg upgrade -y`, then rebuild |
| `pip install pymupdf` fails | expected — use poppler + pdf2image (Step 2) |
| `playwright ... No matching distribution` | expected — use the browser bridge (Step 6) |
| PDF "not found" but opens in a viewer | give the **exact absolute path** |
| very slow (~0.3 tok/s) | let it cool; `AGENT_NUM_CTX=2048`; compact prompt; or LAN route |

### REPL commands
`/autonomy hitl|full` · `/route local|remote|auto` · `/whisper <name|path>` · `/trace on|off` · `/health` · `/quit`

`/whisper` switches the speech model on the fly by short name (`small.en`, `small`,
`medium`, …) — resolved to `~/whisper.cpp/models/ggml-<name>.bin`. For non-English audio,
use a multilingual model (drop `.en`) and `transcribe`/`meeting_notes` accept
`"translate": true` to output English from any language.

### Batch & cron (unattended runs)

Run a list of tasks with no human in the loop — for scheduled jobs:

```bash
python -m local_agent.batch tasks.txt            # one task per line (# = comment)
python -m local_agent.batch tasks.json           # JSON list of task strings
echo "summarize my day from Daily/" | python -m local_agent.batch -   # stdin
python -m local_agent.batch --approve tasks.txt  # allow GUARDED actions (default: auto-deny)
```

GUARDED actions are **auto-denied** by default (nobody's there to approve); pass
`--approve` (with `AUTONOMY=full`) only for task lists you trust. Schedule with cron:

```bash
pkg install -y cronie
crond                                              # start the daemon (add to startup)
crontab -e
# e.g. every morning at 7am, write a daily digest to the vault:
# 0 7 * * *  cd ~/local-android-agentic-harness && AUTONOMY=full \
#   python -m local_agent.batch --approve ~/jobs/morning.txt >> ~/jobs/morning.log 2>&1
```

(Alternatively use Android's scheduler via `termux-job-scheduler` from `termux-api`.)

### Maps & opening apps (incl. Grab)

- `maps` searches places and gives driving directions (OpenStreetMap, no API key) and
  returns a Google Maps link.
- `open_app` opens a URL/app deeplink on the phone (Maps navigation, the Grab app, …).

**On food delivery (Grab/foodpanda/etc.):** there is **no public ordering API**, so the
agent **cannot place orders or pay** — and won't pretend to. What it does: find the
place with `maps` and **open the Grab app/site** with `open_app` so *you* pick items and
pay. `open_app` needs `pkg install termux-api`. This keeps money and account actions in
your hands by design.

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
| `AGENT_EMBED_MODEL` | `nomic-embed-text` | embedding model for semantic search |
| `AGENT_VAULT_INDEX` | `~/.local_agent/vault_index.json` | semantic index location |
| `AGENT_VISION_KEEP_ALIVE` | `0` | unload vision model immediately (RAM-safe) |
| `AGENT_MAX_IMAGE_PX` | `1024` | downscale longest image side (needs Pillow; 0 = off) |
| `AGENT_PDF_OCR_MAX_PAGES` | `5` | pages to render + OCR for scanned PDFs |
| `AGENT_OCR_ENGINE` | `auto` | `auto`/`tesseract`/`vision` for reading text |
| `AGENT_TESSERACT_LANG` | `eng` | Tesseract language(s), e.g. `eng+chi_sim` |
| `AGENT_WHISPER_BACKEND` | `auto` | `auto`/`whisper`/`whispercpp`/`faster` |
| `AGENT_WHISPER_MODEL` | `base` | model for openai/faster-whisper (tiny…large) |
| `AGENT_WHISPER_CPP_MODEL` | _(unset)_ | path to a ggml model for whisper.cpp |
| `AGENT_WHISPER_LANG` | _(auto)_ | language hint, e.g. `en` |
| `AGENT_WHISPER_BEAM` | `5` | beam width (higher = more accurate, slower; 1 = greedy) |
| `AGENT_ENABLE_BROWSER` | `0` | gate the local Playwright browser tool (desktop only) |
| `AGENT_BROWSER_REMOTE_URL` | _(unset)_ | remote headless Chrome (browserless) for `web_scrape render=true` |
| `AGENT_BROWSER_REMOTE_TOKEN` | _(unset)_ | token for the remote browser, if it requires one |

## Tools

`vault_search` `vault_semantic_search` `vault_read` `vault_list` `vault_write`*(G)* ·
`web_search` `web_scrape` `browser`*(G)* `research` ·
`analyze_data` `check_data` `query_csv` `clean_data`*(G)* `analyze_image` `analyze_pdf` `transcribe` `meeting_notes` ·
`make_slides`*(G)* `make_html_report`*(G)* `report_csv`*(G)* `diagram`*(G)* `rephrase` ·
`maps` `open_app`*(G)* `clipboard` `notify` `location` `speak` ·
`screenshot` `find_on_screen` `tap`*(G)* `swipe`*(G)* `type_text`*(G)* `screen_automate`*(G)* ·
`latest_file` `shell`*(G)* `git_sync`*(G)* `request_approval`

**(G) = GUARDED** (writes/deletes/sends/shell). In `hitl` they require approval; in `full`
they run directly **except** the always-confirm set (shell `rm`/`mv`/`git push`/`curl|sh`/
overwriting `>`, overwriting a vault note, any outbound send).

Optional backends (the harness degrades gracefully without them):
- **Browsing JS-heavy pages** — Playwright **won't install on Termux**. Two options:
  - **`web_scrape` with `render=true`** (phone-friendly, recommended): drives a headless
    Chrome over HTTP and set `AGENT_BROWSER_REMOTE_URL`. Two ways to provide that Chrome:
    - **LAN box:** `docker run -p 3000:3000 ghcr.io/browserless/chromium`, then
      `AGENT_BROWSER_REMOTE_URL=http://<lan-ip>:3000` (lightest on the phone).
    - **On-device:** run `scripts/browser-bridge` (Node + Termux's system Chromium) and
      point at `http://127.0.0.1:3000` — no LAN box needed. See that folder's README;
      mind the RAM (Chromium + the 4B is tight on 8 GB).
  - **`browser`** (full click/fill automation) — only on a desktop/LAN box where the
    *harness itself* runs: `pip install playwright && playwright install chromium` and
    `AGENT_ENABLE_BROWSER=1`. (Hybrid routing sends only the LLM to the LAN box, not tool
    execution, so the local browser tool can't be "routed" from the phone.)
  - For plain static pages, `web_scrape` with no render works on-device already.
- **`analyze_image`** — two engines, picked automatically:
  - **Reading text (receipts, labels, documents): Tesseract OCR** — `pkg install tesseract`
    (+ `pip install Pillow` for grayscale/contrast/upscale preprocessing). This is **much**
    more accurate than a small vision model, which *invents digits* on receipts. The LLM
    then answers your question over the real OCR text. Tune with `AGENT_OCR_ENGINE`
    (`auto`|`tesseract`|`vision`), `AGENT_TESSERACT_LANG` (e.g. `eng`), `AGENT_TESSERACT_PSM`.
  - **Describing a scene / visual Q&A: the vision model** — `ollama pull moondream` (or a
    small Qwen2.5-VL/Qwen3-VL). Loaded on demand, unloaded right after (`keep_alive=0`) so
    it never sits in RAM beside the 4B; set `OLLAMA_MAX_LOADED_MODELS=1` to be sure.
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

## Tool reference (every tool)

You don't call tools directly — you ask in plain language and the agent picks them.
Below: each tool, its tag, arguments (**bold** = required), what it does, an example ask,
and any one-time setup. **(G) = GUARDED** (approval-gated).

### Vault (notes)
| Tool | Args | What it does · example |
|---|---|---|
| `vault_search` | **query**, limit | Keyword search. *"search my notes for OMOP"* |
| `vault_semantic_search` | **query**, limit | Meaning-based search (build the index first: `python -m local_agent.vault_index`). *"(semantic) what did I conclude about competing risks?"* |
| `vault_read` | **path** | Read a note. *"read Research/omop.md"* |
| `vault_list` | folder | List notes in a folder. *"list my Daily notes"* |
| `vault_write` *(G)* | **path**, **content**, mode | Create/append/overwrite a note (one per call). *"add a note that I deployed v10.13 to today's log"* |

### Web & research
| Tool | Args | What it does · example |
|---|---|---|
| `web_search` | **query** | DuckDuckGo instant answer. *"web search the OMOP CDM spec"* |
| `web_scrape` | **url**, render | Fetch a page as text; `render=true` runs JS via a headless Chrome (set `AGENT_BROWSER_REMOTE_URL`; see *Optional backends → Browsing JS-heavy pages*). *"summarize https://… with rendering"* |
| `research` | **query**, source, limit | arXiv / PubMed papers (titles, abstracts, links). *"find arXiv papers on recurrent-event survival"* |
| `browser` *(G)* | **steps** | Interactive web automation (navigate/read/fill/click/select/screenshot) via the browser bridge with a persistent login. *"on my throwaway foodpanda account, add chicken rice to the cart and pick cash on delivery"* — see **Web automation** below. |

### Documents, images, data
| Tool | Args | What it does · example · setup |
|---|---|---|
| `analyze_pdf` | **path**, task | PDF text → summary; auto-OCR for scanned PDFs. *"summarize the PDF at /sdcard/Download/x.pdf"* · `pip install pypdf` (+ poppler/pdf2image for scans) |
| `analyze_image` | **path**, question, ocr | OCR text (Tesseract — accurate on receipts) or describe a scene (vision model). *"what's the total on the receipt at …"* · `pkg install tesseract`; `ollama pull moondream` |
| `analyze_data` | **path**, task, plot | CSV stats + correlations, optional histogram. *"analyze cohort.csv, plot to age.png"* · `pip install matplotlib` for plots |
| `check_data` | **path** | Data-quality check: missing values, duplicates, type inconsistencies, outliers, constant/empty/ID columns. *"check cohort.csv for data quality issues"* |
| `query_csv` | **path**, **sql**, limit | Run SQL `SELECT` over a CSV (in-memory SQLite; table `data`, columns sanitized). *"on cohort.csv, count patients by age group"* |
| `clean_data` *(G)* | **path**, **out_path**, drop_duplicates, trim, na_normalize | Write a cleaned CSV (de-dupe, trim, normalize NA). *"clean cohort.csv to cohort_clean.csv"* |
| `report_csv` *(G)* | **csv**, **out_path**, title, columns, max_charts | **One-shot CSV → full HTML report** (stats + correlations + charts). *"build a report of cohort.csv with charts"* |

### Speech (needs whisper.cpp — on-phone Setup Step 4)
| Tool | Args | What it does · example |
|---|---|---|
| `transcribe` | **path**, task, language, diarize, translate | Audio → text; `task` summarizes; `translate=true` → English. *"transcribe the meeting at … and list action items"* |
| `meeting_notes` | **path**, title, context, language, diarize, translate | Audio/transcript → structured note (Summary/Decisions/Action Items). *"make meeting notes from sync.m4a"* |

### Generation
| Tool | Args | What it does · example |
|---|---|---|
| `make_slides` *(G)* | **title**, **outline**, **out_path** | Markdown (Marp) slide deck. *"make slides on X to deck.md"* |
| `make_html_report` *(G)* | **title**, **sections**, **out_path** | HTML report; sections can carry text, tables, and inline-SVG charts. |
| `report_csv` *(G)* | (see above) | The easy path for data reports. |
| `diagram` *(G)* | **spec**, **out_path**, engine | Text → diagram image (Graphviz DOT or Mermaid). On-device "image generation" for flowcharts/ER/org charts. *"draw an ER diagram of these tables"* · `pkg install graphviz` |
| `rephrase` | **text**, style | Rewrite text. *"rephrase what I copied to be polite"* |

### Maps & device (`pkg install termux-api`, except `maps`)
| Tool | Args | What it does · example |
|---|---|---|
| `maps` | query OR origin+destination | Place search / driving directions (returns a Maps link). *"directions from home to Marina Bay Sands"* |
| `open_app` *(G)* | **target** | Open a URL/app deeplink (Maps nav, Grab). Cannot order/pay. *"open Grab to order chicken rice"* |
| `clipboard` | mode, text | Read/write the clipboard. *"rephrase what I just copied"* |
| `notify` | title, content | Push a notification (good for cron). |
| `location` | provider | GPS/network location as JSON. *"what's near me?"* |
| `speak` | **text** | Text-to-speech aloud. |

### Screen interaction (Android via ADB — see *Screen automation* below)
| Tool | Args | What it does · example |
|---|---|---|
| `screenshot` | path | Capture the screen to a PNG. |
| `find_on_screen` | **text** | Locate on-screen text + its tap coordinates (screenshot + OCR). |
| `tap` *(G)* | x,y OR text | Tap at coordinates or on on-screen text. |
| `swipe` *(G)* | **x1,y1,x2,y2**, ms | Swipe/scroll. |
| `type_text` *(G)* | **text** | Type into the focused field (dictation-into-any-app, Wispr-style). |
| `screen_automate` *(G)* | **steps** | Run a screen-action sequence with a killswitch. *"open my banking app and read the balance"* |

### System / files / control
| Tool | Args | What it does · example |
|---|---|---|
| `latest_file` | folder, type, recursive | Newest file in a folder (so you needn't dictate a path). *"analyse the latest screenshot"* |
| `shell` *(G)* | **cmd** | Run a Termux command (destructive forms always confirm). |
| `git_sync` *(G)* | **repo_path**, message | Commit & push a repo/vault. *"sync my vault"* |
| `request_approval` | **action**, details | The model's way to ask before a guarded action (handled by the frontend). |

## Example prompts (what you can ask at `yh>`)

| Ask | Tool(s) the agent uses |
|---|---|
| "What did I note about recurrent events?" | `vault_search` → `vault_read` |
| "What did I conclude about competing risks?" (by meaning) | `vault_semantic_search` |
| "Find recent arXiv papers on recurrent-event survival" | `research` (arxiv) |
| "Analyze /sdcard/data.csv and plot it to /sdcard/hist.png" | `analyze_data` (stats + plot) |
| "Rephrase what I just copied" | `clipboard` (read) → `rephrase` |
| "Remind me at 3pm" (in a cron job) | `notify` |
| "List the notes in my Daily folder" | `vault_list` |
| "Save a daily note that I deployed v10.13" | `request_approval` → `vault_write` (append) |
| "Rephrase this politely: 'send me the file now'" | `rephrase` |
| "Search the web for the OMOP CDM standard" | `web_search` |
| "Summarize https://example.com/article" | `web_scrape` |
| "Analyze /sdcard/Download/data.csv — how many rows?" | `analyze_data` |
| "Check my cohort.csv for data quality issues" | `check_data` |
| "Describe /sdcard/DCIM/Camera/IMG_2026.jpg" | `analyze_image` |
| "Read all the text in /sdcard/Download/receipt.png" | `analyze_image` (OCR) |
| "Summarize the PDF at /sdcard/Download/paper.pdf" | `analyze_pdf` (text, or OCR if scanned) |
| "Transcribe the meeting at /sdcard/Recordings/standup.m4a and list action items" | `transcribe` (task=action items) |
| "Transcribe …/meeting.m4a and save the notes to my vault" | `transcribe` → `request_approval` → `vault_write` |
| "Make meeting notes from …/sync.m4a and file them under Meetings/" | `meeting_notes` → `request_approval` → `vault_write` |
| "Directions from my office to Marina Bay Sands" | `maps` (origin/destination) |
| "Find the nearest pharmacy and open it in Maps" | `maps` → `request_approval` → `open_app` |
| "Open Grab to order chicken rice" | `open_app` (opens the Grab app; **you** pick + pay) |
| "Analyse the latest screenshot and add it to today's notes" | `latest_file` → `analyze_image` → `vault_write` |
| "Transcribe the latest recording in Downloads" | `latest_file` (audio) → `transcribe` |
| "Make an HTML report titled 'Weekly' at …/weekly.html" | `request_approval` → `make_html_report` |
| "Build a report of /sdcard/cohort.csv with charts" | `request_approval` → `report_csv` (stats + correlations + histograms) |
| "Build slides on X to …/deck.md" | `request_approval` → `make_slides` |
| "Commit and push my vault" | `request_approval` → `git_sync` |

Read-only/analysis tools (SAFE) run immediately. Anything that writes/sends/executes
(GUARDED) asks first in `hitl` mode.

## Additional capabilities — step by step

Each is local and low-risk. Enable the ones you want.

### A. Semantic vault search (ask by meaning)
`vault_search` is keyword-based; `vault_semantic_search` finds notes by *concept*.
```bash
ollama pull nomic-embed-text                 # 1. embedding model (~274 MB)
python -m local_agent.vault_index            # 2. build the index (--rebuild to redo)
```
```
yh> (semantic) what did I conclude about competing risks?
```
3. Re-run `python -m local_agent.vault_index` after big edits — it only re-embeds
   changed notes. Index lives at `AGENT_VAULT_INDEX`.

### B. Voice loop (talk to it) — record → whisper → agent → speak
```bash
pkg install -y termux-api                     # 1. mic + TTS (and the Termux:API app)
# 2. needs a whisper backend (see Step 4 above)
python -m local_agent.voice                   # 3. start it
```
**Controls:** `Enter` = **start** recording → `Enter` again = **stop** (it then
transcribes, answers, and speaks) → `Ctrl-C` = quit. `--max-seconds N` caps a
recording. Guarded actions are auto-denied in voice mode.

**Two modes:**
- *Command* (default) — your speech is a task for the agent (it acts + answers + speaks).
- *Dictation* (`--dictate`) — your speech is **appended verbatim (timestamped) to a note**,
  no agent in the loop — fast, reliable voice memos. It **reads the transcript back** so you
  can catch mis-hears (`Enter` = save · `r` = redo · `n` = discard); `--no-confirm` to skip.
  ```bash
  python -m local_agent.voice --dictate                 # → today's Daily/<date>.md
  python -m local_agent.voice --dictate --note "Notes/ideas.md"
  ```

By default it **speaks a 1–2 sentence summary** (the full answer still prints) so TTS
doesn't read a wall of text; use `--full-speech` to speak everything.

**Accuracy:** `base.en` is fast but slips on connected speech; for dictation use
`small.en` (`sh ~/whisper.cpp/models/download-ggml-model.sh small.en`, then point
`AGENT_WHISPER_CPP_MODEL` at it). Beam search is on by default (`AGENT_WHISPER_BEAM=5`),
which improves accuracy on any model — lower it to `1` for max speed.

### C. Device tools (clipboard / notify / location / speak)
```bash
pkg install -y termux-api                     # one-time
```
```
yh> rephrase what I just copied                       # clipboard(read) → rephrase
yh> remind me to call the lab                          # notify
yh> what's near me right now?                          # location → maps
```

### D. Research (arXiv / PubMed)
No install needed.
```
yh> find recent arXiv papers on recurrent-event survival and save a summary to Research/
yh> search PubMed for OMOP CDM validation studies
```

### E. Data & stats (CSV)
```bash
pip install matplotlib                         # optional — only for histograms
```
```
yh> analyze /sdcard/Download/cohort.csv — give me the stats and correlations
yh> analyze /sdcard/Download/cohort.csv and save a histogram to /sdcard/Download/age.png
```
`analyze_data` reports per-column quartiles/sd and pairwise correlations always; the
plot needs matplotlib.

## Screen automation & type-anywhere (Wispr-style) — step by step

The agent can **see the screen and tap through any app**, and **type dictation into any
focused field** — via ADB on the device. Follow these steps in order.

#### Step 1 — install adb + OCR
```bash
pkg install -y android-tools tesseract           # adb + on-screen OCR
```
(There is **no** `pkg install termux-widget` — that's a separate app; see Step 5.)

#### Step 2 — enable Wireless Debugging & pair adb (grant permission)
1. Android **Settings → Developer options → Wireless debugging → ON**.
2. Tap **"Pair device with a pairing code"** — note the IP:PORT and 6-digit code.
3. In Termux:
```bash
adb pair 127.0.0.1:<pairing-port>                 # enter the 6-digit code when asked
adb connect 127.0.0.1:<connect-port>              # the port shown on the Wireless debugging screen
adb devices                                        # must show a line ending in 'device'
```
If the serial isn't the default, set `AGENT_ADB_SERIAL=127.0.0.1:<connect-port>`.
> Wireless debugging can reset its port after a reboot — re-run `adb connect` if `adb devices`
> is empty. This grants the agent input control; revoke anytime by turning Wireless debugging off.

#### Step 3 — test it works (safest first test)
Open a notes app, tap into a text field, then:
```bash
python -m local_agent.voice --type                # Enter → speak → Enter; it types into the field
```
If your words appear in the field, ADB input works. (Plain words type reliably; heavy
punctuation / non-ASCII may not round-trip.)

#### Step 4 — use it
- **Type-anywhere dictation:** `python -m local_agent.voice --type`
- **Automate an app** (agent sees → taps):
  ```
  yh> take a screenshot, find the "Transfer" button, and tap it
  yh> open my notes app and type today's summary
  ```
  It uses `screenshot`/`find_on_screen` to see, then `tap`/`type_text`/`swipe`.

#### Step 5 — home-screen buttons + KILLSWITCH (do this before automating)
Install the **Termux:Widget app from F-Droid** (it's an app, not a `pkg`), then:
```bash
mkdir -p ~/.shortcuts && cp scripts/termux-widgets/* ~/.shortcuts/ && \
  chmod +x ~/.shortcuts/* && rm ~/.shortcuts/README.md
```
Long-press home screen → **Widgets → Termux:Widget** → place one per script:
| Button | Does |
|---|---|
| 🔴 `stop-agent` | **Killswitch** — halts automation instantly |
| 🟢 `resume-agent` | Clears the killswitch |
| `dictate-type` | Speak → types into the focused app field |
| `dictate-note` | Speak → appends to today's daily note |

The killswitch is just a file: every `tap`/`type`/step checks `~/.local_agent/STOP` first
(`AGENT_KILLSWITCH`). `touch` it to halt, `rm` to resume. `screen_automate` also caps steps
(`AGENT_SCREEN_MAX_STEPS`, 20). **Keep the 🔴 button on your home screen before running any
tap-through automation.**

> ⚠️ Screen automation acts on your *whole phone*. It's `GUARDED` (asks in `hitl`), OCR-based
> tapping can misfire on unusual layouts, and it's untested against every app — keep the
> killswitch handy and start with low-stakes flows.

## Web automation (browse, fill, order — with a payment guardrail)

The `browser` tool drives a **persistent** Chromium session through the bridge
(on-device or LAN), so it can navigate, read the page, fill forms, click, and select —
across any web-order or form site, within a logged-in session.

Setup: run `scripts/browser-bridge` and `export AGENT_BROWSER_REMOTE_URL=http://127.0.0.1:3000`
(the bridge's README covers Termux + the persistent profile). Log in once with a
**throwaway account** (the agent can fill the login form; never use your main credentials).

```
yh> on foodpanda, add 1 chicken rice to the cart, pick cash on delivery, and show me the total
```

**Payment guardrail (built in, not optional):**
- The agent browses → adds to cart → selects **cash-on-delivery** if available.
- The **final "place order / pay / checkout" step always requires your approval**, even in
  `AUTONOMY=full` (it shows the cart + total for a one-tap y/n). The model can't silently
  place an order.
- If only **card/online payment** is offered, the agent **stops and hands back to you**.
- Use a throwaway account with **no saved card**, so the worst case is "wrong items in a
  cart." See `local_agent/approval.py` (`web_steps_need_confirm`) for the exact policy.

> ⚠️ **Run this on a LAN box, not the phone.** Web automation is the heaviest task type
> (multi-step ReAct + Chromium), and running Chromium alongside the 4B on the phone causes
> RAM thrashing — the model can time out before finishing a single step. Put **both** Ollama
> and the bridge on a laptop/desktop and make the phone a thin client:
> ```bash
> # LAN box:  OLLAMA_HOST=0.0.0.0 ollama serve   +   node scripts/browser-bridge/server.js
> # phone:
> OLLAMA_REMOTE_URL=http://<lan-ip>:11434 AGENT_ROUTE=auto \
>   AGENT_BROWSER_REMOTE_URL=http://<lan-ip>:3000 python -m local_agent.main
> ```
> On-device is only realistic for a step or two with `AGENT_NUM_CTX=2048`, the compact
> prompt, `AGENT_REQUEST_TIMEOUT=1800`, and other apps closed — and even then it's slow.
> Also fragile: anti-bot/CAPTCHA can block automation. This drives *your own* account on
> *your own* device.

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

**A/B compare models** — pick the best brain for *your* tasks on data, not datasheets:

```bash
python -m local_agent.eval --models qwen3:4b-instruct-2507-q4_K_M,gemma4:e2b
```

Runs the task set against each model and prints a side-by-side table. For a ReAct
agent, **tool-selection** and **valid-JSON** rates usually matter more than raw
multimodal ability — a model that reads images but emits 70% valid JSON feels worse
than a text model at 95%. (At ~3 tok/s an A/B over the full set is a long run.)

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

## Optional backends: what installs on Termux vs desktop

| Capability | Termux (phone) | Desktop / LAN box |
|---|---|---|
| Core agent, vault, web_search/scrape | ✅ stdlib | ✅ |
| Text PDFs | `pip install pypdf` | `pip install pymupdf` |
| Scanned/encrypted PDF OCR | `pkg install poppler && pip install pdf2image` + `ollama pull moondream` | `pip install pymupdf` + moondream |
| Image analysis / OCR | `ollama pull moondream` (+ `pip install Pillow`) | same |
| Meeting transcription | **whisper.cpp** (build below) + `pkg install ffmpeg` | `pip install openai-whisper` |
| Speaker diarization | ✗ (too heavy) | `pip install whisperx` + `HF_TOKEN` |
| JS-page rendering | `web_scrape render=true` → remote browserless | local Playwright |
| Full browser automation | ✗ | `pip install playwright` + `AGENT_ENABLE_BROWSER=1` |
| PyMuPDF / Playwright / whisperx | ✗ won't build | ✅ |

### Speech-to-text on Termux (whisper.cpp) — verified recipe

```bash
# toolchain (if cmake errors with a jsoncpp symbol, your packages drifted —
# `pkg upgrade` resyncs them, which fixes the broken cmake binary):
pkg upgrade -y
pkg install -y git cmake clang make ffmpeg

# build
git clone https://github.com/ggml-org/whisper.cpp && cd whisper.cpp
cmake -B build && cmake --build build -j --config Release   # → build/bin/whisper-cli

# model (base.en ≈ 142 MB, good speed/quality; tiny.en for more speed)
sh ./models/download-ggml-model.sh base.en

# wire it up so the harness finds it
ln -sf "$PWD/build/bin/whisper-cli" "$PREFIX/bin/whisper-cli"
echo "export AGENT_WHISPER_CPP_MODEL=\"$PWD/models/ggml-base.en.bin\"" >> ~/.bashrc
export AGENT_WHISPER_CPP_MODEL="$PWD/models/ggml-base.en.bin"

# verify
whisper-cli -m "$AGENT_WHISPER_CPP_MODEL" -f samples/jfk.wav -otxt
```

The harness needs all three: the `whisper-cli` binary on PATH, `AGENT_WHISPER_CPP_MODEL`
pointing at the `.bin`, and `ffmpeg` (it auto-converts m4a/opus/mp3 → 16 kHz WAV).

## Gotchas
- The vault path **has a space**. pathlib handles it; every shell use is `shlex.quote`d.
- Run Ollama **CPU-only** on the Dimensity 6300 (Mali Vulkan offload is unreliable).
- Don't keep two models resident — the vision model is swapped in on demand.
- Thermal throttling slows sustained loops; timeouts are generous on purpose.

---

> **Full clean install from scratch:** see **[docs/TERMUX_SETUP.md](docs/TERMUX_SETUP.md)** —
> an ordered, verified step-by-step (core + each optional capability + a troubleshooting table).

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
