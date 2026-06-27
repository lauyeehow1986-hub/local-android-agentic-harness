"""Web tools: search / scrape / browser.

web_search and web_scrape use the stdlib (urllib + a tiny HTML-to-text pass) to
avoid heavy ARM wheels on Termux. browser is GUARDED, gated behind a feature
flag, and may only run on the LAN box — Playwright is heavy in Termux.

Observations are kept short; the loop truncates further before re-prefill.
"""

from __future__ import annotations

import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from . import Tool, ToolContext

_UA = "Mozilla/5.0 (Linux; Android 14) local-agent/0.1"
_SCRIPT_STYLE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
_TAGS = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t\r\f\v]+")
_BLANKLINES = re.compile(r"\n\s*\n\s*\n+")


def _fetch(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="ignore")


def _html_to_text(raw: str) -> str:
    raw = _SCRIPT_STYLE.sub(" ", raw)
    raw = re.sub(r"</(p|div|h[1-6]|li|br|tr)>", "\n", raw, flags=re.IGNORECASE)
    raw = _TAGS.sub(" ", raw)
    raw = html.unescape(raw)
    raw = _WS.sub(" ", raw)
    raw = _BLANKLINES.sub("\n\n", raw)
    return raw.strip()


def _remote_render(target_url: str, ctx: ToolContext) -> str:
    """Fetch a JS-rendered page via a remote headless Chrome (browserless).

    POSTs to <AGENT_BROWSER_REMOTE_URL>/content and returns the rendered HTML.
    Pure stdlib — the heavy browser runs on the LAN box, the phone just drives it.
    """
    base = getattr(ctx.config, "browser_remote_url", "").rstrip("/")
    token = getattr(ctx.config, "browser_remote_token", "")
    endpoint = f"{base}/content" + (f"?token={token}" if token else "")
    body = json.dumps({"url": target_url}).encode("utf-8")
    req = urllib.request.Request(
        endpoint, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def web_scrape(args: dict[str, Any], ctx: ToolContext) -> str:
    url = str(args.get("url", "")).strip()
    render = bool(args.get("render", False))
    if not url:
        return "error: 'url' is required"
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    if render:
        # JS-rendered fetch via a remote headless Chrome (LAN box).
        if not getattr(ctx.config, "browser_remote_url", ""):
            return (
                "render requested but no remote browser configured. Set "
                "AGENT_BROWSER_REMOTE_URL to a browserless endpoint "
                "(e.g. http://<lan-ip>:3000) running headless Chrome, or drop "
                "render to fetch the static HTML."
            )
        try:
            raw = _remote_render(url, ctx)
        except urllib.error.URLError as e:
            return f"remote render failed: {e} (is the browserless box reachable?)"
        except Exception as e:  # noqa: BLE001
            return f"remote render error: {e}"
    else:
        try:
            raw = _fetch(url)
        except urllib.error.URLError as e:
            return f"fetch failed: {e}"
        except Exception as e:  # noqa: BLE001 - report, don't crash the loop
            return f"fetch error: {e}"

    text = _html_to_text(raw)
    # Cap here too; the loop truncates again but this saves memory.
    return text[:4000]


def web_search(args: dict[str, Any], ctx: ToolContext) -> str:
    query = str(args.get("query", "")).strip()
    if not query:
        return "error: 'query' is required"
    # DuckDuckGo Instant Answer API — no key, returns JSON. Good enough for a
    # dispatch-grade signal; full results would need an HTML scrape.
    params = urllib.parse.urlencode(
        {"q": query, "format": "json", "no_html": "1", "no_redirect": "1"}
    )
    url = f"https://api.duckduckgo.com/?{params}"
    try:
        raw = _fetch(url, timeout=20)
        data = json.loads(raw)
    except (urllib.error.URLError, json.JSONDecodeError) as e:
        return f"search failed: {e}"
    except Exception as e:  # noqa: BLE001
        return f"search error: {e}"

    lines: list[str] = []
    if data.get("AbstractText"):
        src = data.get("AbstractSource", "")
        lines.append(f"{data['AbstractText']} ({src}: {data.get('AbstractURL', '')})")
    for topic in data.get("RelatedTopics", []):
        if len(lines) >= 6:
            break
        if isinstance(topic, dict) and topic.get("Text"):
            lines.append(f"- {topic['Text']} {topic.get('FirstURL', '')}".strip())
    if not lines:
        return f"no instant answer for '{query}'. Try web_scrape on a specific URL."
    return "\n".join(lines)


def browser(args: dict[str, Any], ctx: ToolContext) -> str:
    if not getattr(ctx.config, "enable_browser", False):
        return (
            "browser disabled: Playwright won't install on Termux. For JS-rendered "
            "pages use web_scrape with render=true (drives a remote headless Chrome via "
            "AGENT_BROWSER_REMOTE_URL). For full click/fill automation, run the harness "
            "on a desktop/LAN box with AGENT_ENABLE_BROWSER=1 and Playwright installed."
        )
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        return "browser unavailable: Playwright not installed."
    steps = args.get("steps")
    if not isinstance(steps, list) or not steps:
        return "error: 'steps' must be a non-empty list of actions"
    # Minimal driver: navigate / click / fill / screenshot.
    try:
        from playwright.sync_api import sync_playwright

        out: list[str] = []
        with sync_playwright() as p:
            browser_obj = p.chromium.launch(headless=True)
            page = browser_obj.new_page()
            for step in steps:
                action = step.get("action")
                if action == "navigate":
                    page.goto(step["url"])
                    out.append(f"navigated {step['url']}")
                elif action == "click":
                    page.click(step["selector"])
                    out.append(f"clicked {step['selector']}")
                elif action == "fill":
                    page.fill(step["selector"], step.get("text", ""))
                    out.append(f"filled {step['selector']}")
                elif action == "screenshot":
                    path = step.get("path", "screenshot.png")
                    page.screenshot(path=path)
                    out.append(f"screenshot -> {path}")
                else:
                    out.append(f"unknown action: {action}")
            browser_obj.close()
        return "\n".join(out)
    except Exception as e:  # noqa: BLE001
        return f"browser error: {e}"


TOOLS = [
    Tool(
        name="web_search",
        tag="SAFE",
        fn=web_search,
        required=("query",),
        description="Search engine query (DuckDuckGo instant answer).",
    ),
    Tool(
        name="web_scrape",
        tag="SAFE",
        fn=web_scrape,
        required=("url",),
        optional=("render",),
        description="Fetch + extract a URL as text (render=true uses a remote headless Chrome for JS pages).",
    ),
    Tool(
        name="browser",
        tag="GUARDED",
        fn=browser,
        required=("steps",),
        description="Playwright actions (feature-flagged; heavy on Termux).",
    ),
]
