// Minimal local "browserless"-style HTTP bridge for Termux.
//
// Drives Termux's system Chromium with playwright-core (the JS driver — the
// Python Playwright wheel doesn't build on Termux, but the Node one + a real
// system Chromium does; approach per github.com/Jobians/playwright-termux).
//
// Exposes exactly the endpoint local_agent's web_scrape(render=true) expects:
//   POST /content  {"url": "https://..."}  ->  rendered HTML
//
// So on the phone:
//   node server.js &
//   export AGENT_BROWSER_REMOTE_URL=http://127.0.0.1:3000
// then ask the agent to scrape a JS page with rendering.

// Termux's recent Node (v26+) reports process.platform === 'android', which
// current playwright-core hard-rejects at registry init ("Unsupported platform:
// android") — before executablePath is ever consulted. Spoof it to 'linux'
// BEFORE requiring playwright-core; we provide our own Chromium via
// executablePath, so no browser download/registry lookup actually happens.
if (process.platform === 'android') {
  Object.defineProperty(process, 'platform', { value: 'linux' });
}

const http = require('http');
const { chromium } = require('playwright-core');

// Find your Chromium with `command -v chromium` or `command -v chromium-browser`.
const CHROMIUM_PATH =
  process.env.CHROMIUM_PATH ||
  '/data/data/com.termux/files/usr/bin/chromium-browser';
const PORT = parseInt(process.env.PORT || '3000', 10);
const HOST = process.env.HOST || '127.0.0.1';

// Let JS run after the DOM is ready before grabbing content (ms).
const SETTLE_MS = parseInt(process.env.RENDER_SETTLE_MS || '1500', 10);

let browserPromise = null;
function getBrowser() {
  if (!browserPromise) {
    browserPromise = chromium.launch({
      executablePath: CHROMIUM_PATH,
      headless: true,
      // Termux/Android headless Chromium needs single-process + no-zygote (the
      // multi-process model fails to fork here). --disable-dev-shm-usage avoids
      // /dev/shm limits; --disable-gpu since there's no usable GPU path.
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
  return browserPromise;
}

async function render(url) {
  const browser = await getBrowser();
  const page = await browser.newPage();
  try {
    // 'networkidle' is flaky on Termux Chromium (aborts); wait for the DOM,
    // then give JS a moment to render before reading the content.
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 45000 });
    if (SETTLE_MS > 0) await page.waitForTimeout(SETTLE_MS);
    return await page.content();
  } finally {
    await page.close();
  }
}

const server = http.createServer((req, res) => {
  const path = req.url.split('?')[0];
  if (req.method === 'POST' && path === '/content') {
    let body = '';
    req.on('data', (c) => (body += c));
    req.on('end', async () => {
      try {
        const { url } = JSON.parse(body || '{}');
        if (!url) {
          res.writeHead(400);
          return res.end('missing "url"');
        }
        const html = await render(url);
        res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
        res.end(html);
      } catch (e) {
        res.writeHead(500);
        res.end('render error: ' + (e && e.message ? e.message : e));
      }
    });
  } else if (req.method === 'GET' && path === '/health') {
    res.writeHead(200);
    res.end('ok');
  } else {
    res.writeHead(404);
    res.end('POST /content {"url":"..."}');
  }
});

server.listen(PORT, HOST, () =>
  console.log(`browser-bridge listening on http://${HOST}:${PORT} (chromium: ${CHROMIUM_PATH})`)
);
