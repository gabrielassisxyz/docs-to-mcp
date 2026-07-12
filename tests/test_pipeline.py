"""Pipeline orchestration tests using a fake crawler (no network)."""

from __future__ import annotations

import json
import threading

import pytest

from docs_to_mcp import paths
from docs_to_mcp.crawler import CapturedPage, CrawlError
from docs_to_mcp.index import search
from docs_to_mcp.ingester import assign_page_ids, normalize, write_page
from docs_to_mcp.pipeline import refresh

STAMP = "2026-07-08T00:00:00Z"


class _FakeCrawler:
    """Serves canned pages; URLs listed in ``fail`` raise on capture.

    Records captured URLs (thread-safely) so tests can assert what was actually
    fetched vs skipped.
    """

    def __init__(self, pages: dict[str, tuple[str, str]], fail: set[str] | None = None):
        self._pages = pages
        self._fail = fail or set()
        self.captured: list[str] = []
        self._lock = threading.Lock()

    def discover(self, root_url, max_pages, locale=None, sitemap_url=None):
        return sorted(self._pages)[:max_pages]

    def capture(self, url):
        with self._lock:
            self.captured.append(url)
        if url in self._fail:
            raise CrawlError(f"boom {url}")
        title, body = self._pages[url]
        return CapturedPage(url, title, body)


def test_refresh_builds_corpus_and_searchable_index(tmp_path):
    crawler = _FakeCrawler({
        "https://x.ai/docs/intro": ("Intro", "Install the widget."),
        "https://x.ai/docs/auth": ("Auth", "Configure API keys."),
    })
    result = refresh("https://x.ai/docs", "site", data_root=tmp_path, crawler=crawler, crawled_at=STAMP)

    assert result.discovered == 2
    assert result.indexed == 2
    assert sorted(result.written) == ["auth", "intro"]
    assert search("site", "API keys", data_root=tmp_path)[0].page_id == "auth"


def test_refresh_records_failures_but_completes(tmp_path):
    crawler = _FakeCrawler(
        {"https://x.ai/docs/a": ("A", "alpha"), "https://x.ai/docs/b": ("B", "beta")},
        fail={"https://x.ai/docs/b"},
    )
    result = refresh("https://x.ai/docs", "site", data_root=tmp_path, crawler=crawler, crawled_at=STAMP)

    assert result.written == ["a"]
    assert len(result.failed) == 1
    assert result.failed[0][0] == "https://x.ai/docs/b"


def test_refresh_respects_max_pages(tmp_path):
    crawler = _FakeCrawler({f"https://x.ai/docs/p{i}": (f"P{i}", "x") for i in range(5)})
    result = refresh("https://x.ai/docs", "site", data_root=tmp_path, crawler=crawler, crawled_at=STAMP, max_pages=2)
    assert result.discovered == 2


def test_empty_discovery_raises_instead_of_wiping_corpus(tmp_path):
    crawler = _FakeCrawler({})
    with pytest.raises(CrawlError):
        refresh("https://x.ai/docs", "site", data_root=tmp_path, crawler=crawler, crawled_at=STAMP)


def _seed_pages(pages, urls, tmp_path):
    """Write .md for a subset of urls without finalizing (simulates interruption)."""
    id_map = assign_page_ids(sorted(pages))
    for url in urls:
        page = normalize(CapturedPage(url, *pages[url]), STAMP, id_map[url])
        write_page(page, "site", data_root=tmp_path)


def test_resume_captures_only_missing_pages(tmp_path):
    pages = {f"https://x.ai/docs/p{i}": (f"P{i}", f"body {i}") for i in range(4)}
    already = sorted(pages)[:2]
    _seed_pages(pages, already, tmp_path)
    assert not paths.pages_jsonl("site", tmp_path).exists()  # interrupted: no jsonl

    crawler = _FakeCrawler(pages)
    result = refresh("https://x.ai/docs", "site", data_root=tmp_path, crawler=crawler, crawled_at=STAMP)

    assert result.mode == "resume"
    assert result.skipped == 2
    assert len(result.written) == 2
    assert result.page_count == 4
    assert set(crawler.captured).isdisjoint(already)  # already-done not re-fetched


def test_incremental_only_captures_new_and_preserves_timestamps(tmp_path):
    base = {f"https://x.ai/docs/p{i}": (f"P{i}", f"body {i}") for i in range(3)}
    refresh("https://x.ai/docs", "site", data_root=tmp_path,
            crawler=_FakeCrawler(dict(base)), crawled_at=STAMP)  # fresh, finalized

    newer = {**base, "https://x.ai/docs/p9": ("P9", "brand new")}
    crawler = _FakeCrawler(newer)
    result = refresh("https://x.ai/docs", "site", data_root=tmp_path,
                     crawler=crawler, crawled_at="2026-08-01T00:00:00Z", incremental=True)

    assert result.mode == "incremental"
    assert result.skipped == 3
    assert len(result.written) == 1
    assert result.page_count == 4
    assert crawler.captured == ["https://x.ai/docs/p9"]

    records = {json.loads(l)["page_id"]: json.loads(l)
               for l in paths.pages_jsonl("site", tmp_path).read_text().splitlines()}
    assert records["p0"]["crawled_at"] == STAMP                    # untouched page keeps old stamp
    assert records["p9"]["crawled_at"] == "2026-08-01T00:00:00Z"   # new page gets new stamp


def test_concurrent_capture_writes_all_pages(tmp_path):
    pages = {f"https://x.ai/docs/p{i:02d}": (f"P{i}", f"body {i}") for i in range(30)}
    crawler = _FakeCrawler(pages)
    result = refresh("https://x.ai/docs", "site", data_root=tmp_path,
                     crawler=crawler, crawled_at=STAMP, concurrency=8)
    assert result.page_count == 30
    assert len(result.written) == 30
    assert result.indexed == 30
