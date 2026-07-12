"""In-memory smoke test: build a corpus, then drive the MCP server via a client.

Exercises every tool over the FastMCP in-memory transport (no network, no stdio).
"""

from __future__ import annotations

import asyncio

from fastmcp import Client

from docs_to_mcp.crawler import CapturedPage, CrawlError
from docs_to_mcp.pipeline import refresh
from docs_to_mcp.server import build_server

STAMP = "2026-07-08T00:00:00Z"


class _FakeCrawler:
    def __init__(self, pages):
        self._pages = pages

    def discover(self, root_url, max_pages, locale=None, sitemap_url=None):
        return sorted(self._pages)[:max_pages]

    def capture(self, url):
        title, body = self._pages[url]
        return CapturedPage(url, title, body)


def _build_corpus(tmp_path):
    crawler = _FakeCrawler({
        "https://x.ai/docs/intro": ("Intro", "Install the widget to begin."),
        "https://x.ai/docs/auth": ("Auth", "Configure API keys and tokens."),
    })
    refresh("https://x.ai/docs", "site", data_root=tmp_path, crawler=crawler, crawled_at=STAMP)


def _run(coro):
    return asyncio.run(coro)


def test_server_tools_are_registered(tmp_path):
    _build_corpus(tmp_path)
    server = build_server("site", data_root=tmp_path)

    async def go():
        async with Client(server) as client:
            return {t.name for t in await client.list_tools()}

    assert _run(go()) == {"search_docs", "get_doc", "list_docs", "refresh_docs"}


def test_search_then_get_doc_roundtrip(tmp_path):
    _build_corpus(tmp_path)
    server = build_server("site", data_root=tmp_path)

    async def go():
        async with Client(server) as client:
            found = (await client.call_tool("search_docs", {"query": "API keys"})).data
            page_id = found["results"][0]["page_id"]
            doc = (await client.call_tool("get_doc", {"page_id": page_id})).data
            return found, page_id, doc

    found, page_id, doc = _run(go())
    assert page_id == "auth"
    assert found["corpus_last_crawled_at"] == STAMP
    assert doc["source_url"] == "https://x.ai/docs/auth"
    assert "API keys" in doc["markdown"]


def test_list_docs_reports_sections(tmp_path):
    _build_corpus(tmp_path)
    server = build_server("site", data_root=tmp_path)

    async def go():
        async with Client(server) as client:
            return (await client.call_tool("list_docs", {})).data

    listing = _run(go())
    assert listing["count"] == 2
    assert {p["page_id"] for p in listing["pages"]} == {"intro", "auth"}


def test_get_doc_unknown_id_returns_actionable_error(tmp_path):
    _build_corpus(tmp_path)
    server = build_server("site", data_root=tmp_path)

    async def go():
        async with Client(server) as client:
            return (await client.call_tool("get_doc", {"page_id": "nope"})).data

    result = _run(go())
    assert "error" in result and result["next_actions"]


def test_refresh_docs_uses_manifest_root_url(tmp_path):
    _build_corpus(tmp_path)
    # Point the server at a fresh crawler by monkeypatching the pipeline's default
    # is out of scope here; refresh_docs will hit a real FirecrawlCrawler, so we
    # only assert the manifest wiring: a missing manifest yields an actionable error.
    server = build_server("missing-slug", data_root=tmp_path)

    async def go():
        async with Client(server) as client:
            return (await client.call_tool("refresh_docs", {})).data

    result = _run(go())
    assert "error" in result and result["next_actions"]
