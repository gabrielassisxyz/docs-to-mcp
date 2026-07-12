"""Tests for category extraction and the enrich_categories flow (no network)."""

from __future__ import annotations

import json

from docs_to_mcp import paths, sitemap
from docs_to_mcp.categories import _category_name, _parse_members, build_category_map
from docs_to_mcp.crawler import CapturedPage
from docs_to_mcp.index import search
from docs_to_mcp.pipeline import _merge_categories, enrich_categories, refresh

STAMP = "2026-07-08T00:00:00Z"


class _Fake:
    """Serves canned pages; discover returns only the article URLs."""

    def __init__(self, pages: dict[str, tuple[str, str]], article_urls: list[str]):
        self._pages = pages
        self._articles = article_urls

    def discover(self, root_url, max_pages, locale=None, sitemap_url=None):
        return sorted(self._articles)[:max_pages]

    def capture(self, url):
        title, markdown = self._pages[url]
        return CapturedPage(url, title, markdown)


def test_category_name_strips_namespace_and_underscores():
    assert _category_name("https://w.ai/w/Category:Base_Weapons") == "Base Weapons"


def test_parse_members_extracts_wiki_links():
    md = "[Garlic](/w/Garlic) and [Whip](/w/Whip#evolution) plus [Sub](/w/Category:Sub)"
    assert _parse_members(md) == {"w-garlic", "w-whip", "w-category-sub"}


def test_build_category_map_inverts_and_intersects():
    crawler = _Fake({"https://w.ai/w/Category:Weapons": ("C", "[G](/w/Garlic) [W](/w/Whip) [S](/w/Category:Sub)")}, [])
    mapping, _failed = build_category_map(
        crawler, ["https://w.ai/w/Category:Weapons"], article_ids={"w-garlic", "w-whip"}, concurrency=2
    )
    # Category:Sub is excluded because it is not one of our captured article ids.
    assert mapping == {"w-garlic": ["Weapons"], "w-whip": ["Weapons"]}


def test_build_category_map_drops_maintenance_categories():
    crawler = _Fake({
        "https://w.ai/w/Category:Weapons": ("C", "[G](/w/Garlic)"),
        "https://w.ai/w/Category:Incomplete_articles": ("C", "[G](/w/Garlic)"),
        "https://w.ai/w/Category:Pages_with_script_errors": ("C", "[G](/w/Garlic)"),
    }, [])
    mapping, _failed = build_category_map(crawler, [
        "https://w.ai/w/Category:Weapons",
        "https://w.ai/w/Category:Incomplete_articles",
        "https://w.ai/w/Category:Pages_with_script_errors",
    ], article_ids={"w-garlic"}, concurrency=2)
    assert mapping == {"w-garlic": ["Weapons"]}  # maintenance categories filtered out


def test_enrich_categories_tags_pages_and_makes_them_searchable(tmp_path, monkeypatch):
    pages = {
        "https://w.ai/w/Garlic": ("Garlic", "A weapon."),
        "https://w.ai/w/Whip": ("Whip", "A weapon."),
        "https://w.ai/w/Category:Weapons": ("Category:Weapons", "[Garlic](/w/Garlic) [Whip](/w/Whip)"),
    }
    articles = ["https://w.ai/w/Garlic", "https://w.ai/w/Whip"]
    crawler = _Fake(pages, articles)

    refresh("https://w.ai/", "site", data_root=tmp_path, crawler=crawler, crawled_at=STAMP,
            auto_sitemap=False, sitemap_url="https://w.ai/sitemaps/index.xml")

    monkeypatch.setattr(sitemap, "fetch_sitemap_urls",
                        lambda url, namespace=None: ["https://w.ai/w/Category:Weapons"])

    result = enrich_categories("site", data_root=tmp_path, crawler=crawler)
    assert result.tagged_pages == 2
    assert result.categories == 1

    records = {json.loads(l)["page_id"]: json.loads(l)
               for l in paths.pages_jsonl("site", tmp_path).read_text().splitlines()}
    assert records["w-garlic"]["categories"] == ["Weapons"]

    # The category name is now a searchable signal for its member pages.
    hits = {h.page_id for h in search("site", "weapons", data_root=tmp_path)}
    assert {"w-garlic", "w-whip"} <= hits


def test_merge_categories_unions_across_runs(tmp_path):
    path = tmp_path / "categories.json"
    path.write_text(json.dumps({"a": ["Weapons"], "b": ["Stages"]}))
    # A flaky re-run finds "a" in a new category and recovers "c"; nothing is lost.
    merged = _merge_categories(path, {"a": ["Evolutions"], "c": ["Characters"]})
    assert merged == {"a": ["Evolutions", "Weapons"], "b": ["Stages"], "c": ["Characters"]}


def test_enrich_categories_preserves_crawled_at(tmp_path, monkeypatch):
    pages = {
        "https://w.ai/w/Garlic": ("Garlic", "A weapon."),
        "https://w.ai/w/Category:Weapons": ("Category:Weapons", "[Garlic](/w/Garlic)"),
    }
    crawler = _Fake(pages, ["https://w.ai/w/Garlic"])
    refresh("https://w.ai/", "site", data_root=tmp_path, crawler=crawler, crawled_at=STAMP,
            auto_sitemap=False, sitemap_url="https://w.ai/sitemaps/index.xml")
    monkeypatch.setattr(sitemap, "fetch_sitemap_urls",
                        lambda url, namespace=None: ["https://w.ai/w/Category:Weapons"])
    enrich_categories("site", data_root=tmp_path, crawler=crawler)

    record = json.loads(paths.pages_jsonl("site", tmp_path).read_text().splitlines()[0])
    assert record["crawled_at"] == STAMP  # enrichment must not bump the crawl timestamp
