"""Unit tests for the pure URL-filtering logic and the capture wrapper.

These run without a live Firecrawl: filtering is pure, and capture is exercised
through an injected fake client.
"""

from __future__ import annotations

import pytest

from docs_to_mcp.crawler import CrawlError, FirecrawlCrawler, filter_doc_urls

ROOT = "https://opencode.ai/docs"


def test_keeps_only_pages_under_docs_prefix():
    urls = [
        "https://opencode.ai/docs/config",
        "https://opencode.ai/docs/config/mcp",
        "https://opencode.ai/",              # above the docs prefix
        "https://opencode.ai/pricing",       # non-doc section
    ]
    assert filter_doc_urls(ROOT, urls, 10) == [
        "https://opencode.ai/docs/config",
        "https://opencode.ai/docs/config/mcp",
    ]


def test_drops_other_hosts_and_non_doc_segments():
    urls = [
        "https://opencode.ai/docs/intro",
        "https://external.com/docs/intro",   # different host
        "https://opencode.ai/docs/blog/post",  # blog under docs
    ]
    assert filter_doc_urls(ROOT, urls, 10) == ["https://opencode.ai/docs/intro"]


def test_dedupes_trailing_slash_and_fragments_and_sorts():
    urls = [
        "https://opencode.ai/docs/b",
        "https://opencode.ai/docs/b/",
        "https://opencode.ai/docs/b#section",
        "https://opencode.ai/docs/a",
    ]
    assert filter_doc_urls(ROOT, urls, 10) == [
        "https://opencode.ai/docs/a",
        "https://opencode.ai/docs/b",
    ]


def test_respects_max_pages_cap():
    urls = [f"https://opencode.ai/docs/p{i}" for i in range(5)]
    assert len(filter_doc_urls(ROOT, urls, 2)) == 2


def test_default_drops_locale_prefixed_pages():
    urls = [
        "https://opencode.ai/docs/tools",       # canonical
        "https://opencode.ai/docs/de/tools",     # German
        "https://opencode.ai/docs/pt-br/tools",  # region locale
        "https://opencode.ai/docs/zh-cn/tools",
    ]
    assert filter_doc_urls(ROOT, urls, 10) == ["https://opencode.ai/docs/tools"]


def test_two_letter_non_locale_sections_are_kept():
    # "go", "sdk", "ide" are real doc sections, not ISO 639-1 codes.
    urls = [
        "https://opencode.ai/docs/go",
        "https://opencode.ai/docs/sdk",
        "https://opencode.ai/docs/de/go",  # localized -> dropped
    ]
    assert filter_doc_urls(ROOT, urls, 10) == [
        "https://opencode.ai/docs/go",
        "https://opencode.ai/docs/sdk",
    ]


def test_explicit_locale_keeps_only_that_language():
    urls = [
        "https://opencode.ai/docs/tools",
        "https://opencode.ai/docs/de/tools",
        "https://opencode.ai/docs/es/tools",
    ]
    assert filter_doc_urls(ROOT, urls, 10, locale="de") == ["https://opencode.ai/docs/de/tools"]


def test_fully_localized_site_falls_back_to_locale_pages():
    # No canonical/unprefixed pages exist; default must not return empty.
    urls = [
        "https://opencode.ai/docs/de/tools",
        "https://opencode.ai/docs/es/tools",
    ]
    assert filter_doc_urls(ROOT, urls, 10) == urls


class _FakeDoc:
    def __init__(self, markdown, title=None):
        self.markdown = markdown
        self.metadata = type("M", (), {"title": title})()


class _FakeClient:
    def __init__(self, doc):
        self._doc = doc

    def scrape(self, url, **kwargs):
        return self._doc


def test_capture_returns_title_and_markdown():
    crawler = FirecrawlCrawler(client=_FakeClient(_FakeDoc("# Hello\nbody", title="Hello")))
    page = crawler.capture("https://opencode.ai/docs/x")
    assert page.title == "Hello"
    assert page.markdown == "# Hello\nbody"
    assert page.source_url == "https://opencode.ai/docs/x"


def test_capture_falls_back_to_url_when_no_title():
    crawler = FirecrawlCrawler(client=_FakeClient(_FakeDoc("body", title=None)))
    assert crawler.capture("https://opencode.ai/docs/x").title == "https://opencode.ai/docs/x"


def test_capture_raises_on_empty_markdown():
    crawler = FirecrawlCrawler(client=_FakeClient(_FakeDoc("   ")))
    with pytest.raises(CrawlError):
        crawler.capture("https://opencode.ai/docs/x")
