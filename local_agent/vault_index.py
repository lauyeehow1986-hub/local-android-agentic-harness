"""Semantic index over the Obsidian vault for meaning-based search.

Embeds each note (chunked) with a local Ollama embedding model and stores the
vectors in a JSON file. `vault_semantic_search` embeds the query and returns the
nearest chunks by cosine similarity — so "competing risks" finds the note even
if it only says "Fine-Gray" / "cumulative incidence".

Build/refresh the index:
    python -m local_agent.vault_index            # incremental (changed notes only)
    python -m local_agent.vault_index --rebuild  # from scratch

Pure-stdlib math (no numpy) — fine for a personal vault of hundreds of notes.
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any, Optional


def _chunk(text: str, size: int = 800, overlap: int = 150) -> list[str]:
    """Split note text into overlapping chunks on paragraph-ish boundaries."""
    text = text.strip()
    if len(text) <= size:
        return [text] if text else []
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + size)
        # Prefer to break at a paragraph/newline near the end.
        if end < len(text):
            nl = text.rfind("\n", start + size - overlap, end)
            if nl != -1 and nl > start:
                end = nl
        chunks.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return [c for c in chunks if c]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def load_index(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"model": None, "entries": [], "mtimes": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def save_index(path: Path, index: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(index), encoding="utf-8")


def build_index(
    config,
    client,
    *,
    rebuild: bool = False,
    progress=None,
) -> dict[str, Any]:
    """Embed vault notes into config.vault_index_path. Incremental by default
    (only re-embeds notes whose mtime changed). Returns the index dict."""
    vault = Path(config.vault_path)
    index_path = Path(config.vault_index_path)
    model = config.embed_model

    index = {"model": None, "entries": [], "mtimes": {}} if rebuild else load_index(index_path)
    if index.get("model") not in (None, model):
        index = {"model": None, "entries": [], "mtimes": {}}  # model changed → rebuild
    index["model"] = model
    mtimes: dict[str, float] = index.get("mtimes", {})
    # Keep entries for unchanged files; drop the rest so we re-embed them.
    kept = []
    for e in index.get("entries", []):
        rel = e["path"]
        f = vault / rel
        if f.exists() and mtimes.get(rel) == f.stat().st_mtime:
            kept.append(e)
    kept_paths = {e["path"] for e in kept}

    entries = list(kept)
    for f in sorted(vault.rglob("*.md")):
        rel = str(f.relative_to(vault))
        mt = f.stat().st_mtime
        if rel in kept_paths and mtimes.get(rel) == mt:
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for i, chunk in enumerate(_chunk(text)):
            try:
                vec = client.embed(chunk, model)
            except Exception:  # noqa: BLE001 - skip a note that fails, keep going
                continue
            entries.append({"path": rel, "chunk": i, "text": chunk[:400], "vector": vec})
        mtimes[rel] = mt
        if progress:
            progress(rel)

    index["entries"] = entries
    index["mtimes"] = mtimes
    index["built_at"] = time.time()
    save_index(index_path, index)
    return index


def search(config, client, query: str, limit: int = 5) -> list[tuple[float, str, str]]:
    """Return [(score, path, snippet)] for the query. Raises FileNotFoundError if
    the index hasn't been built."""
    index_path = Path(config.vault_index_path)
    index = load_index(index_path)
    if not index.get("entries"):
        raise FileNotFoundError("no vault index — run: python -m local_agent.vault_index")
    qvec = client.embed(query, index.get("model") or config.embed_model)
    scored = [
        (_cosine(qvec, e["vector"]), e["path"], e["text"]) for e in index["entries"]
    ]
    scored.sort(key=lambda x: -x[0])
    # De-dup by path, keep best chunk per note.
    seen: set[str] = set()
    out: list[tuple[float, str, str]] = []
    for score, path, text in scored:
        if path in seen:
            continue
        seen.add(path)
        out.append((score, path, text))
        if len(out) >= limit:
            break
    return out


def main(argv: Optional[list[str]] = None) -> int:
    import argparse
    import sys

    ap = argparse.ArgumentParser(description="Build/refresh the vault semantic index.")
    ap.add_argument("--rebuild", action="store_true", help="rebuild from scratch")
    args = ap.parse_args(argv)

    from .config import load_config
    from .main import build_agent

    config = load_config()
    agent = build_agent(config)
    print(f"Indexing {config.vault_path} with {config.embed_model} …")
    n = {"count": 0}

    def prog(rel):
        n["count"] += 1
        print(f"  embedded: {rel}", file=sys.stderr)

    index = build_index(config, agent.client, rebuild=args.rebuild, progress=prog)
    print(f"done: {len(index['entries'])} chunks from {len(index['mtimes'])} notes "
          f"-> {config.vault_index_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
