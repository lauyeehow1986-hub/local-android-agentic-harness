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

const http = require('http');
const { chromium } = require('playwright-core');

// Find your Chromium with `command -v chromium` or `command -v chromium-browser`.
const CHROMIUM_PATH =
  process.env.CHROMIUM_PATH ||
  '/data/data/com.termux/files/usr/bin/chromium-browser';
const PORT = parseInt(process.env.PORT || '3000', 10);
const HOST = process.env.HOST || '127.0.0.1';

let browserPromise = null;
function getBrowser() {
  if (!browserPromise) {
    browserPromise = chromium.launch({
      executablePath: CHROMIUM_PATH,
      headless: true,
      // Required on Android/Termux; --disable-dev-shm-usage avoids /dev/shm limits.
      args: ['--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage'],
    });
  }
  return browserPromise;
}

async function render(url) {
  const browser = await getBrowser();
  const page = await browser.newPage();
  try {
    await page.goto(url, { waitUntil: 'networkidle', timeout: 45000 });
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
