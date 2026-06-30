// Local browser bridge for Termux (and LAN boxes): drives system Chromium with
// playwright-core. Two endpoints the harness uses:
//   POST /content  {"url":"..."}            -> rendered HTML (web_scrape render=true)
//   POST /actions  {"steps":[ ... ]}        -> interactive automation (browser tool)
//   GET  /health                            -> "ok"
//
// The session is PERSISTENT (a Chromium profile on disk) so a login survives
// across calls/restarts. Run once, log into your (throwaway) account, and the
// agent operates within that authenticated session.
//
//   node server.js &
//   export AGENT_BROWSER_REMOTE_URL=http://127.0.0.1:3000
//
// SAFETY: this bridge just executes the steps it's given. The harness's approval
// gate is what forces a confirmation before any "place order / pay / checkout"
// step (see local_agent/approval.py). Don't bypass that.

// Termux's recent Node (v26+) reports process.platform === 'android', which
// current playwright-core rejects at init. Spoof to 'linux' BEFORE requiring it.
if (process.platform === 'android') {
  Object.defineProperty(process, 'platform', { value: 'linux' });
}

const http = require('http');
const { chromium } = require('playwright-core');

const CHROMIUM_PATH =
  process.env.CHROMIUM_PATH ||
  '/data/data/com.termux/files/usr/bin/chromium-browser';
const PORT = parseInt(process.env.PORT || '3000', 10);
const HOST = process.env.HOST || '127.0.0.1';
const SETTLE_MS = parseInt(process.env.RENDER_SETTLE_MS || '1500', 10);
const PROFILE_DIR =
  process.env.BROWSER_PROFILE_DIR ||
  ((process.env.HOME || '.') + '/.local_agent/chromium-profile');
const TOKEN = process.env.BROWSER_TOKEN || '';

let ctxPromise = null;
function getContext() {
  if (!ctxPromise) {
    ctxPromise = chromium.launchPersistentContext(PROFILE_DIR, {
      executablePath: CHROMIUM_PATH,
      headless: process.env.BROWSER_HEADLESS !== '0',
      viewport: { width: 1024, height: 1600 },
      args: [
        '--no-sandbox',
        '--disable-gpu',
        '--disable-dev-shm-usage',
        '--single-process',
        '--no-zygote',
        '--disable-software-rasterizer',
      ],
    });
  }
  return ctxPromise;
}

let currentPage = null;
async function page() {
  const ctx = await getContext();
  if (!currentPage || currentPage.isClosed()) {
    const pages = ctx.pages();
    currentPage = pages.length ? pages[0] : await ctx.newPage();
  }
  return currentPage;
}

async function render(url) {
  const pg = await page();
  await pg.goto(url, { waitUntil: 'domcontentloaded', timeout: 45000 });
  if (SETTLE_MS > 0) await pg.waitForTimeout(SETTLE_MS);
  return await pg.content();
}

async function runSteps(steps) {
  const pg = await page();
  const results = [];
  for (const s of steps || []) {
    const a = String(s.action || '').toLowerCase();
    try {
      if (a === 'navigate' || a === 'goto') {
        await pg.goto(s.url, { waitUntil: 'domcontentloaded', timeout: 45000 });
        if (SETTLE_MS > 0) await pg.waitForTimeout(SETTLE_MS);
        results.push({ action: a, url: pg.url() });
      } else if (a === 'click' || a === 'tap') {
        await pg.click(s.selector, { timeout: 15000 });
        results.push({ action: a, selector: s.selector, ok: true });
      } else if (a === 'fill' || a === 'type') {
        await pg.fill(s.selector, s.text != null ? s.text : (s.value || ''));
        results.push({ action: a, selector: s.selector, ok: true });
      } else if (a === 'select') {
        await pg.selectOption(s.selector, s.value);
        results.push({ action: a, ok: true });
      } else if (a === 'press') {
        if (s.selector) await pg.press(s.selector, s.key);
        else await pg.keyboard.press(s.key);
        results.push({ action: a, ok: true });
      } else if (a === 'wait') {
        if (s.selector) await pg.waitForSelector(s.selector, { timeout: s.timeout || 15000 });
        else await pg.waitForTimeout(s.ms || 1000);
        results.push({ action: a, ok: true });
      } else if (a === 'read') {
        const t = s.selector
          ? await pg.locator(s.selector).first().innerText().catch(() => '')
          : await pg.evaluate(() => document.body.innerText);
        results.push({ action: a, text: String(t || '').slice(0, 2000) });
      } else if (a === 'screenshot') {
        const path = s.path || 'screenshot.png';
        await pg.screenshot({ path });
        results.push({ action: a, path });
      } else if (a === 'content') {
        results.push({ action: a, html: (await pg.content()).slice(0, 4000) });
      } else {
        results.push({ action: a, error: 'unknown action' });
      }
    } catch (e) {
      results.push({ action: a, error: e && e.message ? e.message : String(e) });
    }
  }
  results.push({ url: pg.url() });
  return results;
}

function authed(req) {
  if (!TOKEN) return true;
  return (req.url.split('?')[1] || '').includes('token=' + TOKEN);
}

const server = http.createServer((req, res) => {
  const path = req.url.split('?')[0];
  if (req.method === 'GET' && path === '/health') {
    res.writeHead(200);
    return res.end('ok');
  }
  if (req.method === 'POST' && (path === '/content' || path === '/actions')) {
    if (!authed(req)) {
      res.writeHead(403);
      return res.end('forbidden');
    }
    let body = '';
    req.on('data', (c) => (body += c));
    req.on('end', async () => {
      try {
        const payload = JSON.parse(body || '{}');
        if (path === '/content') {
          if (!payload.url) {
            res.writeHead(400);
            return res.end('missing "url"');
          }
          const html = await render(payload.url);
          res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
          return res.end(html);
        }
        const results = await runSteps(payload.steps);
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ results }));
      } catch (e) {
        res.writeHead(500);
        res.end('error: ' + (e && e.message ? e.message : e));
      }
    });
    return;
  }
  res.writeHead(404);
  res.end('POST /content {"url"} · POST /actions {"steps":[...]} · GET /health');
});

server.listen(PORT, HOST, () =>
  console.log(
    `browser-bridge on http://${HOST}:${PORT} (chromium: ${CHROMIUM_PATH}, profile: ${PROFILE_DIR})`
  )
);
