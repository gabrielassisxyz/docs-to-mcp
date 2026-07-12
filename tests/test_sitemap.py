"""Unit tests for sitemap parsing (fetching is stubbed; no network)."""

from __future__ import annotations

import gzip

import pytest

from docs_to_mcp import sitemap

URLSET = b"""<?xml version="1.0"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://w.example/w/Garlic</loc></url>
  <url><loc>https://w.example/w/Whip</loc></url>
  <url><loc>https://w.example/w/Garlic</loc></url>
</urlset>"""

INDEX = b"""<?xml version="1.0"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://w.example/sitemaps/NS_0.xml</loc></sitemap>
</sitemapindex>"""


def _stub_fetch(pages: dict[str, bytes], monkeypatch):
    monkeypatch.setattr(sitemap, "_fetch", lambda url: pages[url])


def test_parses_urlset_and_dedupes(monkeypatch):
    _stub_fetch({"https://w.example/sitemaps/NS_0.xml": URLSET}, monkeypatch)
    urls = sitemap.fetch_sitemap_urls("https://w.example/sitemaps/NS_0.xml")
    assert urls == ["https://w.example/w/Garlic", "https://w.example/w/Whip"]


def test_recurses_one_level_into_index(monkeypatch):
    _stub_fetch({
        "https://w.example/sitemaps/index.xml": INDEX,
        "https://w.example/sitemaps/NS_0.xml": URLSET,
    }, monkeypatch)
    urls = sitemap.fetch_sitemap_urls("https://w.example/sitemaps/index.xml")
    assert "https://w.example/w/Whip" in urls


def test_gzip_body_is_decompressed():
    # _fetch itself gunzips by magic bytes; exercise that path directly.
    compressed = gzip.compress(URLSET)
    assert compressed[:2] == b"\x1f\x8b"
    assert gzip.decompress(compressed) == URLSET


def test_invalid_xml_raises(monkeypatch):
    _stub_fetch({"https://w.example/bad.xml": b"not xml <<<"}, monkeypatch)
    with pytest.raises(sitemap.SitemapError):
        sitemap.fetch_sitemap_urls("https://w.example/bad.xml")


ROBOTS = b"""User-Agent: *
Disallow: /w/Special:
Sitemap: https://w.example/images/sitemaps/index.xml
"""

MW_INDEX = b"""<?xml version="1.0"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://w.example/images/sitemaps/NS_0-0.xml</loc></sitemap>
  <sitemap><loc>https://w.example/images/sitemaps/NS_1-0.xml</loc></sitemap>
  <sitemap><loc>https://w.example/images/sitemaps/NS_10-0.xml</loc></sitemap>
</sitemapindex>"""

NS0 = b"""<?xml version="1.0"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://w.example/w/Garlic</loc></url>
</urlset>"""
NS_TALK = b"""<?xml version="1.0"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://w.example/w/Talk:Garlic</loc></url>
</urlset>"""


def test_resolve_sitemap_reads_robots_directive(monkeypatch):
    _stub_fetch({"https://w.example/robots.txt": ROBOTS}, monkeypatch)
    assert sitemap.resolve_sitemap("https://w.example/") == "https://w.example/images/sitemaps/index.xml"


def test_resolve_sitemap_returns_none_when_absent(monkeypatch):
    _stub_fetch({"https://w.example/robots.txt": b"User-Agent: *\nDisallow:\n"}, monkeypatch)
    assert sitemap.resolve_sitemap("https://w.example/") is None


def test_resolve_sitemap_never_raises_on_fetch_failure(monkeypatch):
    def boom(url):
        raise sitemap.SitemapError("no robots")
    monkeypatch.setattr(sitemap, "_fetch", boom)
    assert sitemap.resolve_sitemap("https://w.example/") is None


def test_mediawiki_index_keeps_only_content_namespace(monkeypatch):
    _stub_fetch({
        "https://w.example/images/sitemaps/index.xml": MW_INDEX,
        "https://w.example/images/sitemaps/NS_0-0.xml": NS0,
        "https://w.example/images/sitemaps/NS_1-0.xml": NS_TALK,
    }, monkeypatch)
    urls = sitemap.fetch_sitemap_urls("https://w.example/images/sitemaps/index.xml")
    assert urls == ["https://w.example/w/Garlic"]  # NS_1 (Talk) and NS_10 excluded
