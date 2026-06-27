# browser-bridge — on-device JS rendering for Termux

Playwright's **Python** wheel doesn't build on Termux, but its **Node** driver
(`playwright-core`) works when pointed at Termux's **system Chromium**. This tiny
HTTP server wraps that into the same API `local_agent`'s `web_scrape(render=true)`
already speaks (`POST /content {"url":...}` → rendered HTML), so the phone can
render JS-heavy pages **fully on-device** — no LAN box required.

Approach based on the pattern in
[github.com/Jobians/playwright-termux](https://github.com/Jobians/playwright-termux).

## Setup (Termux)

```bash
# 1. Node + Termux's Chromium (from the x11 repo)
pkg install -y nodejs x11-repo
pkg install -y chromium

# 2. Find the Chromium binary path
command -v chromium-browser || command -v chromium

# 3. Install the bridge's one dependency
cd scripts/browser-bridge
npm install                       # pulls playwright-core (JS, no browser download)

# 4. Run it (set CHROMIUM_PATH if step 2 showed a different path)
CHROMIUM_PATH="$(command -v chromium-browser || command -v chromium)" node server.js &
```

## Point the agent at it

```bash
export AGENT_BROWSER_REMOTE_URL=http://127.0.0.1:3000
python -m local_agent.main
```

```
yh> scrape https://a-js-heavy-site.com with rendering and summarize the main points
```

The agent calls `web_scrape` with `render=true`, which POSTs to this bridge; the
bridge drives headless Chromium and returns the rendered HTML.

## Caveats (read before relying on this)

- **RAM.** Chromium + the resident 4B model on an 8 GB phone is tight — a heavy page
  plus Ollama can push into swap (the thing that already cripples decode speed). On the
  12 GB variant it's more comfortable. If it thrashes, prefer a **LAN-box browserless**
  (`AGENT_BROWSER_REMOTE_URL=http://<lan-ip>:3000`) instead.
- **Chromium install is large** (hundreds of MB) and the upstream Termux-Playwright
  pattern is early-stage; treat as experimental.
- This bridge does **render-a-URL** only (the common "read this dynamic page" case). For
  interactive click/fill flows, run the harness's `browser` tool on a desktop.
- Keep `--no-sandbox` — Android has no user namespaces for Chromium's sandbox.
- **`Unsupported platform: android`**: recent Termux Node (v26+) reports
  `process.platform === 'android'`, which current playwright-core rejects at startup.
  `server.js` works around this by spoofing `process.platform` to `'linux'` before
  requiring playwright-core (we supply Chromium via `executablePath`, so no browser
  download happens). If you adapt the script, keep that spoof at the very top.
