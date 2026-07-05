"""Screen-interaction tools (Android, via ADB) — the on-device path for
"type into any app" (Wispr-style dictation) and "look at the screen and tap".

Requires ADB reachable ON the device: enable Wireless Debugging, pair, and
`adb connect 127.0.0.1:<port>` (or root). Then the agent can:
  - screenshot the screen,
  - find on-screen text and its x,y (Tesseract word boxes),
  - tap / swipe / type into the focused field,
  - run a sequence (screen_automate) with a KILLSWITCH.

KILLSWITCH: every tap/type/step checks `config.killswitch_path`. Create that file
(e.g. from a Termux:Widget button) to halt automation instantly.

Everything degrades with a clear message when adb/tesseract aren't available.
NOTE: coordinates come from the full-resolution screenshot, so they map 1:1 to
screen pixels — do NOT downscale screenshots used for tapping.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

from . import Tool, ToolContext


def _adb(config) -> Optional[list[str]]:
    exe = shutil.which("adb")
    if not exe:
        return None
    cmd = [exe]
    serial = getattr(config, "adb_serial", "")
    if serial:
        cmd += ["-s", serial]
    return cmd


_ADB_HINT = (
    "adb not available/connected. Enable Wireless Debugging, `pkg install "
    "android-tools`, pair, then `adb connect 127.0.0.1:<port>`. Check `adb devices`."
)


def _killswitch_active(config) -> bool:
    p = getattr(config, "killswitch_path", None)
    return bool(p and Path(p).expanduser().exists())


def _default_shot() -> Path:
    return Path(tempfile.gettempdir()) / "agent_screen.png"


def screenshot(args: dict[str, Any], ctx: ToolContext) -> str:
    """Capture the current screen to a PNG. Returns the file path. SAFE (read)."""
    adb = _adb(ctx.config)
    if not adb:
        return _ADB_HINT
    out = str(args.get("path", "")).strip() or str(_default_shot())
    try:
        proc = subprocess.run(adb + ["exec-out", "screencap", "-p"], capture_output=True, timeout=30)
    except Exception as e:  # noqa: BLE001
        return f"screenshot error: {e}"
    if proc.returncode != 0 or not proc.stdout:
        err = (proc.stderr or b"").decode("utf-8", "ignore")[:200]
        return f"screenshot failed: {err or 'no output'} ({_ADB_HINT})"
    try:
        Path(out).write_bytes(proc.stdout)
    except OSError as e:
        return f"write error: {e}"
    return out


def _tesseract_tsv(image_path: str, lang: str = "eng", min_conf: float = 40.0):
    """Return word boxes [{text,left,top,width,height,conf}] via Tesseract, or None
    if tesseract isn't installed. `lang` e.g. "eng" or "chi_sim+eng"."""
    exe = shutil.which("tesseract")
    if not exe:
        return None
    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / "o"
        try:
            subprocess.run(
                [exe, image_path, str(base), "-l", lang, "--psm", "11", "tsv"],
                capture_output=True, text=True, timeout=120,
            )
        except Exception:  # noqa: BLE001
            return []
        tsv = Path(str(base) + ".tsv")
        if not tsv.exists():
            return []
        rows = []
        for line in tsv.read_text(encoding="utf-8", errors="ignore").splitlines()[1:]:
            f = line.split("\t")
            if len(f) < 12:
                continue
            text = f[11].strip()
            if not text:
                continue
            try:
                conf = float(f[10])
            except ValueError:
                conf = -1.0
            if conf < min_conf:
                continue
            try:
                rows.append({
                    "text": text, "left": int(f[6]), "top": int(f[7]),
                    "width": int(f[8]), "height": int(f[9]), "conf": conf,
                })
            except ValueError:
                continue
        return rows


def _locate(ctx: ToolContext, target: str):
    """Screenshot + OCR; return (matches, error). matches: [(text, cx, cy)]."""
    shot = screenshot({"path": str(_default_shot())}, ctx)
    if not shot.endswith(".png"):
        return None, shot  # error string
    lang = getattr(ctx.config, "tesseract_lang", "eng")
    rows = _tesseract_tsv(shot, lang)
    if rows is None:
        return None, "tesseract not installed: `pkg install tesseract`"
    tl = target.lower()
    hits = []
    for r in rows:
        if tl in r["text"].lower():
            cx = r["left"] + r["width"] // 2
            cy = r["top"] + r["height"] // 2
            hits.append((r["text"], cx, cy))
    return hits, None


def find_on_screen(args: dict[str, Any], ctx: ToolContext) -> str:
    """Find on-screen text and its tap coordinates. SAFE. {"text": str}"""
    target = str(args.get("text", "")).strip()
    if not target:
        return "error: 'text' is required"
    hits, err = _locate(ctx, target)
    if err:
        return err
    if not hits:
        return f"'{target}' not found on screen"
    return "\n".join(f"'{t}' at ({x},{y})" for t, x, y in hits[:6])


def tap(args: dict[str, Any], ctx: ToolContext) -> str:
    """Tap at x,y (or find_and_tap via 'text'). GUARDED."""
    if _killswitch_active(ctx.config):
        return "KILLSWITCH active — automation halted."
    adb = _adb(ctx.config)
    if not adb:
        return _ADB_HINT
    text = str(args.get("text", "")).strip()
    if text:
        hits, err = _locate(ctx, text)
        if err:
            return err
        if not hits:
            return f"'{text}' not found on screen"
        _, x, y = hits[0]
    else:
        x, y = args.get("x"), args.get("y")
        if x is None or y is None:
            return "error: provide 'x'+'y' or 'text'"
    try:
        subprocess.run(adb + ["shell", "input", "tap", str(int(x)), str(int(y))],
                       capture_output=True, timeout=15)
    except Exception as e:  # noqa: BLE001
        return f"tap error: {e}"
    return f"tapped ({x},{y})" + (f" [{text}]" if text else "")


def swipe(args: dict[str, Any], ctx: ToolContext) -> str:
    """Swipe from x1,y1 to x2,y2 over 'ms'. GUARDED."""
    if _killswitch_active(ctx.config):
        return "KILLSWITCH active — automation halted."
    adb = _adb(ctx.config)
    if not adb:
        return _ADB_HINT
    try:
        x1, y1, x2, y2 = (int(args[k]) for k in ("x1", "y1", "x2", "y2"))
    except (KeyError, ValueError, TypeError):
        return "error: 'x1','y1','x2','y2' are required"
    ms = str(int(args.get("ms", 300)))
    try:
        subprocess.run(adb + ["shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), ms],
                       capture_output=True, timeout=15)
    except Exception as e:  # noqa: BLE001
        return f"swipe error: {e}"
    return f"swiped ({x1},{y1})→({x2},{y2})"


def _escape_input(text: str) -> str:
    """Best-effort escaping for `adb shell input text`. Spaces → %s; a few shell
    metacharacters backslash-escaped. Plain words/sentences work; some
    punctuation and non-ASCII may not round-trip perfectly."""
    out = text.replace(" ", "%s")
    for ch in ["\\", '"', "'", "`", "(", ")", "&", "<", ">", "|", ";", "*", "~", "?", "$"]:
        out = out.replace(ch, "\\" + ch)
    return out


def type_text(args: dict[str, Any], ctx: ToolContext) -> str:
    """Type text into the focused field (Wispr-style). GUARDED. {"text": str}"""
    if _killswitch_active(ctx.config):
        return "KILLSWITCH active — automation halted."
    adb = _adb(ctx.config)
    if not adb:
        return _ADB_HINT
    text = str(args.get("text", ""))
    if not text:
        return "error: 'text' is required"
    try:
        subprocess.run(adb + ["shell", "input", "text", _escape_input(text)],
                       capture_output=True, timeout=30)
    except Exception as e:  # noqa: BLE001
        return f"type error: {e}"
    return f"typed {len(text)} chars into the focused field"


def screen_automate(args: dict[str, Any], ctx: ToolContext) -> str:
    """Run a sequence of screen actions, checking the KILLSWITCH before each.
    GUARDED. steps: [{"action": "find_and_tap"|"tap"|"type"|"swipe"|"wait"|"key", ...}]
      - find_and_tap {"text": str}         · tap {"x","y"}
      - type {"text": str}                 · swipe {"x1","y1","x2","y2","ms"}
      - wait {"ms": int}                   · key {"keyevent": str}  (e.g. "66" = Enter)
    """
    steps = args.get("steps")
    if not isinstance(steps, list) or not steps:
        return "error: 'steps' must be a non-empty list"
    adb = _adb(ctx.config)
    if not adb:
        return _ADB_HINT
    cap = getattr(ctx.config, "screen_max_steps", 20)
    results: list[str] = []
    for i, s in enumerate(steps[:cap], 1):
        if _killswitch_active(ctx.config):
            results.append(f"[{i}] KILLSWITCH — stopped")
            break
        a = str(s.get("action", "")).lower()
        if a in ("find_and_tap", "tap_text"):
            results.append(f"[{i}] " + tap({"text": s.get("text", "")}, ctx))
        elif a == "tap":
            results.append(f"[{i}] " + tap({"x": s.get("x"), "y": s.get("y")}, ctx))
        elif a == "type":
            results.append(f"[{i}] " + type_text({"text": s.get("text", "")}, ctx))
        elif a == "swipe":
            results.append(f"[{i}] " + swipe(s, ctx))
        elif a == "wait":
            time.sleep(min(float(s.get("ms", 500)) / 1000.0, 10.0))
            results.append(f"[{i}] waited")
        elif a == "key":
            try:
                subprocess.run(adb + ["shell", "input", "keyevent", str(s.get("keyevent", "66"))],
                               capture_output=True, timeout=10)
                results.append(f"[{i}] keyevent {s.get('keyevent', '66')}")
            except Exception as e:  # noqa: BLE001
                results.append(f"[{i}] key error: {e}")
        else:
            results.append(f"[{i}] unknown action: {a}")
    if len(steps) > cap:
        results.append(f"(stopped at step cap {cap})")
    return "\n".join(results)


TOOLS = [
    Tool(
        name="screenshot",
        tag="SAFE",
        fn=screenshot,
        optional=("path",),
        description="Capture the phone screen to a PNG (ADB); returns the path.",
    ),
    Tool(
        name="find_on_screen",
        tag="SAFE",
        fn=find_on_screen,
        required=("text",),
        description="Find on-screen text and its tap coordinates (screenshot + Tesseract).",
    ),
    Tool(
        name="tap",
        tag="GUARDED",
        fn=tap,
        optional=("x", "y", "text"),
        description="Tap at x,y or on on-screen text (ADB). Honors the killswitch.",
    ),
    Tool(
        name="swipe",
        tag="GUARDED",
        fn=swipe,
        required=("x1", "y1", "x2", "y2"),
        optional=("ms",),
        description="Swipe/scroll the screen (ADB).",
    ),
    Tool(
        name="type_text",
        tag="GUARDED",
        fn=type_text,
        required=("text",),
        description="Type text into the focused field (Wispr-style dictation-into-any-app; ADB).",
    ),
    Tool(
        name="screen_automate",
        tag="GUARDED",
        fn=screen_automate,
        required=("steps",),
        description="Run a screen-action sequence (find_and_tap/tap/type/swipe/wait/key) with a killswitch.",
    ),
]
