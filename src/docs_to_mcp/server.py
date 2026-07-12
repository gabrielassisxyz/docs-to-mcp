"""A focused MCP server over one captured documentation corpus.

One generic server parameterized by ``slug`` rather than a hand-written server per
site: the tool surface is identical for every corpus, so duplicating it per slug
would only create drift. A site is selected at launch via `serve --slug`.
"""

from __future__ import annotations

from pathlib import Path

from fastmcp import FastMCP

from . import corpus, index, manifest, paths
from .pipeline import refresh


def build_server(slug: str, data_root: Path | str = paths.DEFAULT_DATA_ROOT) -> FastMCP:
    mcp = FastMCP(f"docs-{slug}")

    def _last_crawled() -> str | None:
        try:
            return manifest.read(slug, data_root).last_crawled_at
        except FileNotFoundError:
            return None

    @mcp.tool
    def search_docs(query: str, limit: int = 5) -> dict:
        """Search the captured docs and return ranked matching pages."""
        try:
            hits = index.search(slug, query, limit=limit, data_root=data_root)
        except index.StaleIndexError as exc:
            # Caught BEFORE the generic case on purpose. The generic advice is "run
            # refresh_docs", which re-crawls the entire site -- and an agent handed a
            # stale index would dutifully do exactly that: thousands of pages and a real
            # Firecrawl bill, to repair something local that takes two seconds. The pages
            # on disk are fine. Only the index needs rebuilding.
            return {
                "error": str(exc),
                "next_actions": [
                    f"Run `docs-to-mcp reindex --slug {slug}` in a shell. "
                    "Do NOT call refresh_docs — the captured pages are fine and re-crawling "
                    "would be a slow, expensive way to fix a local index."
                ],
            }
        except index.IndexError_ as exc:
            return {"error": str(exc), "next_actions": ["Run refresh_docs to build the corpus."]}
        return {
            "query": query,
            "corpus_last_crawled_at": _last_crawled(),
            "results": [
                {"page_id": h.page_id, "title": h.title, "section": h.section,
                 "source_url": h.source_url, "snippet": h.snippet}
                for h in hits
            ],
            "next_actions": ["Call get_doc(page_id) to read a full page."],
        }

    @mcp.tool
    def get_doc(page_id: str) -> dict:
        """Return one captured page: metadata, source URL, and full markdown."""
        record = corpus.load_records(slug, data_root).get(page_id)
        body = corpus.read_body(slug, page_id, data_root)
        if record is None or body is None:
            return {
                "error": f"unknown page_id '{page_id}'",
                "next_actions": ["Call list_docs() or search_docs() to find valid page ids."],
            }
        return {
            "page_id": page_id,
            "source_url": record["source_url"],
            "title": record["title"],
            "section": record["section"],
            "categories": record.get("categories", []),
            "crawled_at": record["crawled_at"],
            "markdown": body,
        }

    @mcp.tool
    def list_docs(section: str | None = None, category: str | None = None, limit: int = 50) -> dict:
        """List captured pages, optionally filtered to one section or category."""
        records = list(corpus.load_records(slug, data_root).values())
        sections = sorted({r["section"] for r in records if r["section"]})
        all_categories = sorted({c for r in records for c in r.get("categories", [])})
        if section:
            records = [r for r in records if r["section"] == section]
        if category:
            records = [r for r in records if category in r.get("categories", [])]
        records.sort(key=lambda r: r["page_id"])
        return {
            "count": len(records),
            "sections": sections,
            "categories": all_categories,
            "pages": [
                {"page_id": r["page_id"], "title": r["title"],
                 "categories": r.get("categories", []), "source_url": r["source_url"]}
                for r in records[:limit]
            ],
            "next_actions": ["Call get_doc(page_id) to read a page, or filter by category."],
        }

    @mcp.tool
    def refresh_docs(max_pages: int = 50) -> dict:
        """Re-crawl the source site and rebuild the local corpus and index."""
        try:
            source = manifest.read(slug, data_root)
        except FileNotFoundError as exc:
            return {"error": str(exc), "next_actions": ["Crawl the site once via the CLI first."]}
        result = refresh(source.root_url, slug, max_pages=max_pages,
                         locale=source.locale, sitemap_url=source.sitemap_url,
                         auto_sitemap=False, data_root=data_root)
        return {
            "discovered": result.discovered,
            "written": len(result.written),
            "removed": len(result.removed),
            "indexed": result.indexed,
            "failed": [{"url": u, "error": e} for u, e in result.failed],
            "next_actions": ["Call search_docs(query) against the refreshed corpus."],
        }

    return mcp
