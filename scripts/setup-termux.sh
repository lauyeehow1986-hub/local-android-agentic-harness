#!/data/data/com.termux/files/usr/bin/bash
# One-shot Termux setup for local-agent: installs EVERYTHING (core + all optional
# capabilities). Safe to re-run — it skips work that's already done.
#
#   bash scripts/setup-termux.sh
#
# Heads up: this downloads a lot (~4 GB of models + Chromium). Use Wi-Fi. Optional
# sections continue on error so one failure doesn't abort the rest; a summary
# prints at the end. Skip the heavy bits with env flags:
#   SKIP_WHISPER=1 SKIP_BROWSER=1 SKIP_MODELS=1 bash scripts/setup-termux.sh

set -u
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BASHRC="$HOME/.bashrc"
note() { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓ %s\033[0m\n' "$*"; }
warn() { printf '  \033[33m! %s\033[0m\n' "$*"; }

add_env() {  # add_env VAR value  — append to ~/.bashrc once
  local line="export $1=\"$2\""
  grep -qsF "export $1=" "$BASHRC" || echo "$line" >> "$BASHRC"
  export "$1=$2"
}

note "1/7  System packages"
pkg upgrade -y || warn "pkg upgrade had issues (continuing)"
pkg install -y python git ffmpeg poppler tesseract termux-api cronie \
  cmake clang make || warn "some packages failed"
ok "core + tool packages"

note "2/7  Python dependencies"
cd "$REPO_DIR"
pip install -r requirements.txt || warn "requirements failed"
pip install pypdf Pillow pdf2image || warn "optional pip libs failed"
ok "python deps (pypdf, Pillow, pdf2image)"

note "2b/7  Device tools (termux-api) + optional plotting"
pkg install -y termux-api || warn "termux-api failed (clipboard/notify/location/voice need it)"
pip install matplotlib >/dev/null 2>&1 && ok "matplotlib (analyze_data plots)" || warn "matplotlib skipped"

note "3/7  Ollama models"
if [ "${SKIP_MODELS:-0}" = "1" ]; then
  warn "SKIP_MODELS=1 — skipping model pulls"
elif command -v ollama >/dev/null 2>&1; then
  curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1 || {
    warn "ollama server not responding — starting it from \$HOME"
    (cd "$HOME" && OLLAMA_KEEP_ALIVE=30m OLLAMA_KV_CACHE_TYPE=q8_0 ollama serve >/dev/null 2>&1 &)
    sleep 3
  }
  ollama pull qwen3:4b-instruct-2507-q4_K_M || warn "qwen pull failed"
  ollama pull moondream || warn "moondream pull failed"
  ollama pull nomic-embed-text || warn "embed model pull failed (semantic search)"
  ok "models pulled"
  echo "  tip: build the semantic index with: python -m local_agent.vault_index"
else
  warn "ollama not found — install it, then pull qwen3:4b-instruct-2507-q4_K_M moondream nomic-embed-text"
fi

note "4/7  Speech-to-text (whisper.cpp)"
if [ "${SKIP_WHISPER:-0}" = "1" ]; then
  warn "SKIP_WHISPER=1 — skipping"
else
  WC="$HOME/whisper.cpp"
  [ -d "$WC" ] || git clone https://github.com/ggml-org/whisper.cpp "$WC"
  if [ ! -x "$WC/build/bin/whisper-cli" ]; then
    (cd "$WC" && cmake -B build && cmake --build build -j --config Release) || warn "whisper build failed"
  fi
  [ -f "$WC/models/ggml-base.en.bin" ] || (cd "$WC" && sh ./models/download-ggml-model.sh base.en) || warn "model download failed"
  if [ -x "$WC/build/bin/whisper-cli" ]; then
    ln -sf "$WC/build/bin/whisper-cli" "$PREFIX/bin/whisper-cli"
    add_env AGENT_WHISPER_CPP_MODEL "$WC/models/ggml-base.en.bin"
    ok "whisper-cli ready"
  fi
fi

note "5/7  On-device browser bridge (Node + Chromium)"
if [ "${SKIP_BROWSER:-0}" = "1" ]; then
  warn "SKIP_BROWSER=1 — skipping"
else
  pkg install -y nodejs x11-repo || warn "nodejs/x11-repo failed"
  pkg install -y chromium || warn "chromium failed (browser rendering won't work)"
  (cd "$REPO_DIR/scripts/browser-bridge" && npm install) || warn "npm install failed"
  add_env AGENT_BROWSER_REMOTE_URL "http://127.0.0.1:3000"
  ok "browser-bridge installed (run it in its own tab — see below)"
fi

note "6/7  Scheduled jobs (cron)"
command -v crond >/dev/null 2>&1 && ok "cronie installed (run 'crond' to start the daemon)" || warn "cronie missing"

note "7/7  Done"
cat <<EOF

Setup complete (re-run anytime; it's idempotent). New shells pick up the env
vars added to ~/.bashrc. To start using it, open THREE Termux tabs:

  Tab 1 (Ollama):   cd ~ && OLLAMA_KEEP_ALIVE=30m OLLAMA_KV_CACHE_TYPE=q8_0 ollama serve
  Tab 2 (browser):  cd $REPO_DIR/scripts/browser-bridge && \\
                    CHROMIUM_PATH="\$(command -v chromium-browser || command -v chromium)" node server.js
  Tab 3 (agent):    cd $REPO_DIR && python -m local_agent.main

Try:  yh> what's the total on the receipt at /sdcard/Download/<file>.jpg
EOF
