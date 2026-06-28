# Termux setup — from scratch (verified end-to-end)

A clean, ordered install of `local-agent` on the phone, incorporating every fix
found the hard way. Do the **Core** section to get a working agent; add the
**Optional capability** sections only for the features you want.

> Layout: you'll run **three things in three Termux tabs** — `ollama serve`, the
> agent REPL, and (optionally) the browser bridge. Open new tabs by swiping from
> the left edge → "New session".

---

## 0. Termux base (one time)

Install Termux **from F-Droid** (not Play Store). Then:

```bash
termux-setup-storage          # grant storage access (tap Allow)
pkg upgrade -y                # IMPORTANT: keeps packages in sync; prevents the
                              # broken-cmake "jsoncpp symbol" error later
pkg install -y python git
```

---

## 1. Core: Ollama + model + harness

### 1a. Ollama (run from $HOME — see note)

```bash
cd ~                                 # ALWAYS start ollama from a stable dir, or
                                     # llama-server later dies with "getcwd failed"
OLLAMA_KEEP_ALIVE=30m OLLAMA_KV_CACHE_TYPE=q8_0 ollama serve &
ollama pull qwen3:4b-instruct-2507-q4_K_M
```

### 1b. The harness

```bash
cd ~
git clone https://github.com/lauyeehow1986-hub/local-android-agentic-harness.git
cd local-android-agentic-harness
git checkout claude/build-from-markdown-tbl713
git pull
pip install -r requirements.txt      # pytest only; the runtime is stdlib
```

### 1c. Run it

```bash
python -m local_agent.main           # AUTONOMY=hitl by default
```
```
yh> what did I note about recurrent events?
```

That's a working agent (vault + web search/scrape). Everything below is optional.

---

## 2. Optional: PDF reading (text + scanned/encrypted OCR)

```bash
pip install pypdf                    # text extraction (pure-Python, installs fine)
pkg install -y poppler               # renderer for scanned/encrypted PDFs
pip install pdf2image                # pure-Python wrapper around poppler
ollama pull moondream                # vision model used for OCR
```
> Don't `pip install pymupdf` on the phone — it compiles MuPDF from source and fails.
> The poppler + pdf2image combo covers scanned/encrypted PDFs on-device.

```
yh> summarize the PDF at /storage/emulated/0/Download/<exact-name>.pdf
```

## 3. Optional: image analysis / OCR

```bash
pkg install -y tesseract             # accurate OCR for receipts/labels/documents
pip install Pillow                   # preprocessing (grayscale/contrast/upscale)
ollama pull moondream                # vision model, for describing scenes / VQA
```
`analyze_image` auto-picks **Tesseract** for reading text (a small vision model
*invents digits* on receipts) and the **vision model** for describing a scene. For
other languages: `AGENT_TESSERACT_LANG=eng+chi_sim` (install the lang data first).
```
yh> read all the text in /sdcard/Download/receipt.png
yh> what's the total on the receipt at /sdcard/Download/receipt.jpg
```

## 4. Optional: meeting transcription (whisper.cpp)

```bash
pkg install -y cmake clang make ffmpeg

cd ~
git clone https://github.com/ggml-org/whisper.cpp
cd whisper.cpp
cmake -B build && cmake --build build -j --config Release     # → build/bin/whisper-cli
sh ./models/download-ggml-model.sh base.en                    # ~142 MB

ln -sf "$PWD/build/bin/whisper-cli" "$PREFIX/bin/whisper-cli"
echo "export AGENT_WHISPER_CPP_MODEL=\"$PWD/models/ggml-base.en.bin\"" >> ~/.bashrc
export AGENT_WHISPER_CPP_MODEL="$PWD/models/ggml-base.en.bin"

# verify
whisper-cli -m "$AGENT_WHISPER_CPP_MODEL" -f samples/jfk.wav -otxt
```
> If `cmake` errors with a `jsoncpp` symbol, run `pkg upgrade -y` (your packages
> drifted) and rebuild.

```
yh> transcribe the meeting at /sdcard/Recordings/standup.m4a and list action items
yh> make meeting notes from /sdcard/Recordings/sync.m4a and save them under Meetings/
```

## 5. Optional: on-device JS-page rendering (browser bridge)

```bash
pkg install -y nodejs x11-repo
pkg install -y chromium

cd ~/local-android-agentic-harness/scripts/browser-bridge
npm install                          # pulls playwright-core (no browser download)

# run it (own tab; leave running)
CHROMIUM_PATH="$(command -v chromium-browser || command -v chromium)" node server.js
```
Test in another tab, then point the agent at it:
```bash
curl -s -X POST http://127.0.0.1:3000/content -H 'Content-Type: application/json' \
  -d '{"url":"https://example.com"}' | head -c 200      # should print HTML

echo 'export AGENT_BROWSER_REMOTE_URL=http://127.0.0.1:3000' >> ~/.bashrc
export AGENT_BROWSER_REMOTE_URL=http://127.0.0.1:3000
```
```
yh> scrape https://news.ycombinator.com with rendering and summarize the top stories
```
> RAM caveat: Chromium + the 4B is tight on 8 GB. If it thrashes, run a
> `browserless/chrome` container on a LAN box and set `AGENT_BROWSER_REMOTE_URL`
> to that box instead.

---

## 5b. Optional: maps & opening apps (Grab, navigation)

```bash
pkg install -y termux-api          # provides termux-open-url for open_app
```
- `maps` needs nothing extra (uses OpenStreetMap).
- `open_app` opens URLs/deeplinks on the phone. The agent can find a place and open
  the Grab app/site for you, but **cannot order or pay** — you finish in the app.

```
yh> directions from home to Marina Bay Sands
yh> open Grab to order chicken rice
```

## 5c. Optional: cron / batch jobs

```bash
pkg install -y cronie
crond                              # start the cron daemon (add to startup)
mkdir -p ~/jobs
printf 'summarize my notes from Daily/ for this week\n' > ~/jobs/weekly.txt
crontab -e
```
Example crontab line (Monday 8am weekly digest, allowing writes):
```
0 8 * * 1  cd ~/local-android-agentic-harness && AUTONOMY=full \
  python -m local_agent.batch --approve ~/jobs/weekly.txt >> ~/jobs/weekly.log 2>&1
```
Run a batch manually any time:
```bash
python -m local_agent.batch ~/jobs/weekly.txt            # guarded actions auto-denied
python -m local_agent.batch --approve ~/jobs/weekly.txt  # allow writes/sends
```

## 6. Make it persistent (~/.bashrc)

Add these so every new Termux session is configured (the whisper/browser lines
were already appended above if you did those steps):

```bash
# ~/.bashrc
export AGENT_WHISPER_CPP_MODEL="$HOME/whisper.cpp/models/ggml-base.en.bin"
export AGENT_BROWSER_REMOTE_URL=http://127.0.0.1:3000
# Optional: hybrid routing to a faster LAN box
# export OLLAMA_REMOTE_URL=http://<lan-ip>:11434
# export AGENT_ROUTE=auto
```

## 7. Daily startup (the three tabs)

```bash
# Tab 1 — Ollama (from home!)
cd ~ && OLLAMA_KEEP_ALIVE=30m OLLAMA_KV_CACHE_TYPE=q8_0 ollama serve

# Tab 2 — browser bridge (only if you use rendering)
cd ~/local-android-agentic-harness/scripts/browser-bridge && \
  CHROMIUM_PATH="$(command -v chromium-browser || command -v chromium)" node server.js

# Tab 3 — the agent
cd ~/local-android-agentic-harness && python -m local_agent.main
```

REPL commands: `/autonomy hitl|full` · `/route local|remote|auto` · `/trace on|off`
· `/health` · `/quit`

---

## Troubleshooting (things that actually happened)

| Symptom | Cause → fix |
|---|---|
| `HTTP 404 ... model not found` | model not pulled → `ollama pull qwen3:4b-instruct-2507-q4_K_M` |
| `getcwd failed: No such file or directory` | `ollama serve` started from a deleted dir → restart it from `cd ~` |
| `cmake ... cannot locate symbol _ZN4Json...` | package drift → `pkg upgrade -y`, rebuild |
| `pip install pymupdf` fails to build | expected on Termux → use `poppler` + `pdf2image` instead |
| `No matching distribution found for playwright` | expected → use the browser bridge (Node) or a LAN box |
| `Unsupported platform: android` (bridge) | handled by the bridge (spoofs platform); just `git pull` |
| `ERR_ABORTED ... networkidle` (bridge) | handled (single-process + domcontentloaded); `git pull` |
| PDF "not found" but opens in a viewer | give the **exact absolute path**; the model can't guess filenames |
| very slow (~0.3 tok/s) | thermal throttle / RAM swap → lower `AGENT_NUM_CTX`, use compact prompt, let it cool, or route to a LAN box |
