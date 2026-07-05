"""Research tool: search academic papers (arXiv, PubMed) and return titles +
abstracts + links, so the agent can summarize them or save them to the vault.

Free public APIs, no key, stdlib only. SAFE (read-only).
"""

from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any

from . import Tool, ToolContext

_UA = "local-agent/0.1 (personal research assistant)"


def _get(url: str, timeout: int = 25) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _arxiv(query: str, limit: int) -> str:
    params = urllib.parse.urlencode(
        {"search_query": f"all:{query}", "start": 0, "max_results": limit}
    )
    raw = _get(f"http://export.arxiv.org/api/query?{params}")
    ns = {"a": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(raw)
    out: list[str] = []
    for entry in root.findall("a:entry", ns):
        title = (entry.findtext("a:title", default="", namespaces=ns) or "").strip()
        summary = (entry.findtext("a:summary", default="", namespaces=ns) or "").strip()
        link = (entry.findtext("a:id", default="", namespaces=ns) or "").strip()
        summary = " ".join(summary.split())[:400]
        out.append(f"• {title}\n  {summary}\n  {link}")
    return "\n\n".join(out) if out else f"no arXiv results for '{query}'"


def _pubmed(query: str, limit: int) -> str:
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    sp = urllib.parse.urlencode(
        {"db": "pubmed", "term": query, "retmax": limit, "retmode": "json"}
    )
    import json

    ids = json.loads(_get(f"{base}/esearch.fcgi?{sp}")).get("esearchresult", {}).get("idlist", [])
    if not ids:
        return f"no PubMed results for '{query}'"
    fp = urllib.parse.urlencode({"db": "pubmed", "id": ",".join(ids), "retmode": "xml"})
    root = ET.fromstring(_get(f"{base}/efetch.fcgi?{fp}"))
    out: list[str] = []
    for art in root.findall(".//PubmedArticle"):
        title = (art.findtext(".//ArticleTitle") or "").strip()
        abstract = " ".join(
            (e.text or "") for e in art.findall(".//AbstractText")
        ).strip()
        pmid = (art.findtext(".//PMID") or "").strip()
        abstract = " ".join(abstract.split())[:400]
        link = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
        out.append(f"• {title}\n  {abstract}\n  {link}")
    return "\n\n".join(out) if out else f"no PubMed abstracts for '{query}'"


def research(args: dict[str, Any], ctx: ToolContext) -> str:
    """Search papers. {"query": str, "source": "arxiv"|"pubmed", "limit": int}."""
    query = str(args.get("query", "")).strip()
    source = str(args.get("source", "arxiv")).strip().lower()
    limit = int(args.get("limit", 5) or 5)
    if not query:
        return "error: 'query' is required"
    try:
        if source == "pubmed":
            return _pubmed(query, limit)
        return _arxiv(query, limit)
    except urllib.error.URLError as e:
        return f"research lookup failed (network): {e}"
    except Exception as e:  # noqa: BLE001
        return f"research error: {e}"


TOOLS = [
    Tool(
        name="research",
        tag="SAFE",
        fn=research,
        required=("query",),
        optional=("source", "limit"),
        description="Search academic papers (arXiv or PubMed); returns titles, abstracts, links.",
    ),
]
