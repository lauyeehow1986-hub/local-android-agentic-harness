"""Obsidian vault tools: search / read / list / write.

All paths are relative to the vault root (config.vault_path), which contains a
space. We use pathlib throughout — never build shell strings here. Writes are
one note per call so each is individually approvable, and frontmatter is
preserved on append.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import Tool, ToolContext


def _vault_root(ctx: ToolContext) -> Path:
    return Path(ctx.config.vault_path)


def _safe_join(root: Path, rel: str) -> Path:
    """Resolve rel under root, refusing to escape the vault."""
    rel = rel.lstrip("/")
    target = (root / rel).resolve()
    root_res = root.resolve()
    if root_res not in target.parents and target != root_res:
        raise ValueError(f"path escapes vault: {rel}")
    return target


def vault_list(args: dict[str, Any], ctx: ToolContext) -> str:
    root = _vault_root(ctx)
    folder = str(args.get("folder", "")).strip()
    try:
        base = _safe_join(root, folder) if folder else root
    except ValueError as e:
        return f"error: {e}"
    if not base.exists():
        return f"folder not found: {folder or '.'}"
    if not base.is_dir():
        return f"not a folder: {folder}"
    entries = []
    for p in sorted(base.iterdir()):
        rel = p.relative_to(root)
        entries.append(f"{rel}/" if p.is_dir() else str(rel))
    if not entries:
        return f"(empty) {folder or '.'}"
    return "\n".join(entries)


def vault_read(args: dict[str, Any], ctx: ToolContext) -> str:
    root = _vault_root(ctx)
    rel = str(args.get("path", "")).strip()
    if not rel:
        return "error: 'path' is required"
    try:
        target = _safe_join(root, rel)
    except ValueError as e:
        return f"error: {e}"
    if not target.exists():
        return f"note not found: {rel}"
    if target.is_dir():
        return f"'{rel}' is a folder, not a note"
    return target.read_text(encoding="utf-8")


def vault_search(args: dict[str, Any], ctx: ToolContext) -> str:
    root = _vault_root(ctx)
    query = str(args.get("query", "")).strip()
    limit = int(args.get("limit", 5) or 5)
    if not query:
        return "error: 'query' is required"
    if not root.exists():
        return f"vault not found at {root}"
    q = query.lower()
    hits: list[str] = []
    for p in sorted(root.rglob("*.md")):
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        low = text.lower()
        if q in low or q in p.name.lower():
            # Grab a short snippet around the first match for context.
            idx = low.find(q)
            snippet = ""
            if idx != -1:
                start = max(0, idx - 40)
                end = min(len(text), idx + 60)
                snippet = text[start:end].replace("\n", " ").strip()
            rel = p.relative_to(root)
            hits.append(f"{rel}: …{snippet}…" if snippet else str(rel))
            if len(hits) >= limit:
                break
    if not hits:
        return f"no matches for '{query}'"
    return "\n".join(hits)


def vault_write(args: dict[str, Any], ctx: ToolContext) -> str:
    root = _vault_root(ctx)
    rel = str(args.get("path", "")).strip()
    content = args.get("content", "")
    mode = str(args.get("mode", "create")).strip().lower()
    if not rel:
        return "error: 'path' is required"
    if mode not in ("create", "overwrite", "append"):
        return f"error: invalid mode '{mode}' (use create|overwrite|append)"
    try:
        target = _safe_join(root, rel)
    except ValueError as e:
        return f"error: {e}"
    target.parent.mkdir(parents=True, exist_ok=True)

    if mode == "create" and target.exists():
        return f"refused: {rel} already exists (use append or overwrite)"

    if mode == "append" and target.exists():
        existing = target.read_text(encoding="utf-8")
        # Ensure we don't glue onto a frontmatter block or a line without a
        # newline boundary.
        sep = "" if existing.endswith("\n") or existing == "" else "\n"
        target.write_text(existing + sep + str(content), encoding="utf-8")
        return f"appended to {rel} ({len(str(content))} chars)"

    # create (new) or overwrite
    target.write_text(str(content), encoding="utf-8")
    verb = "overwrote" if mode == "overwrite" else "created"
    return f"{verb} {rel} ({len(str(content))} chars)"


TOOLS = [
    Tool(
        name="vault_search",
        tag="SAFE",
        fn=vault_search,
        required=("query",),
        optional=("limit",),
        description="Full-text search the vault.",
    ),
    Tool(
        name="vault_read",
        tag="SAFE",
        fn=vault_read,
        required=("path",),
        description="Read a note (path relative to vault root).",
    ),
    Tool(
        name="vault_list",
        tag="SAFE",
        fn=vault_list,
        optional=("folder",),
        description="List notes under a folder.",
    ),
    Tool(
        name="vault_write",
        tag="GUARDED",
        fn=vault_write,
        required=("path", "content"),
        optional=("mode",),
        description="Create/overwrite/append a note. One note per call.",
    ),
]
