"""Unit tests for normalization and corpus writing (no network)."""

from __future__ import annotations

import json

from docs_to_mcp import frontmatter, paths
from docs_to_mcp.crawler import CapturedPage
from docs_to_mcp.ingester import normalize, resolve_links, write_corpus

BASE = "https://opencode.ai/docs/config/mcp"
STAMP = "2026-07-08T00:00:00Z"


def test_resolve_links_makes_relative_absolute_and_leaves_absolute():
    md = "[a](../intro) [b](/docs/x) [c](https://x.com) [d](#anchor) ![i](img.png)"
    out = resolve_links(md, BASE)
    assert "https://opencode.ai/docs/intro" in out
    assert "https://opencode.ai/docs/x" in out
    assert "(https://x.com)" in out           # untouched
    assert "(#anchor)" in out                 # untouched
    assert "https://opencode.ai/docs/config/img.png" in out


def test_resolve_links_preserves_link_titles():
    out = resolve_links('[a](../intro "Intro Page")', BASE)
    assert out == '[a](https://opencode.ai/docs/intro "Intro Page")'


def test_normalize_derives_fields_and_hash():
    page = normalize(CapturedPage(BASE, "MCP", "# MCP\nbody"), STAMP)
    assert page.page_id == "config-mcp"
    assert page.section == "config"
    assert page.crawled_at == STAMP
    assert len(page.content_hash) == 64


def _capture(url, title, body):
    return normalize(CapturedPage(url, title, body), STAMP)


def test_write_corpus_creates_md_and_jsonl(tmp_path):
    pages = [
        _capture("https://opencode.ai/docs/intro", "Intro", "# Intro"),
        _capture("https://opencode.ai/docs/config/mcp", "MCP", "# MCP"),
    ]
    result = write_corpus(pages, "site", data_root=tmp_path)

    assert result.written == ["config-mcp", "intro"]
    md = (paths.pages_dir("site", tmp_path) / "intro.md").read_text()
    meta, body = frontmatter.parse(md)
    assert meta["page_id"] == "intro"
    assert meta["source_url"] == "https://opencode.ai/docs/intro"
    assert "crawled_at" not in meta          # deliberately excluded from .md
    assert body.strip() == "# Intro"

    records = [json.loads(line) for line in paths.pages_jsonl("site", tmp_path).read_text().splitlines()]
    assert {r["page_id"] for r in records} == {"intro", "config-mcp"}
    assert all(r["crawled_at"] == STAMP for r in records)  # timestamp lives here


def test_write_corpus_prunes_stale_pages(tmp_path):
    write_corpus([_capture("https://opencode.ai/docs/old", "Old", "# Old")], "site", data_root=tmp_path)
    result = write_corpus([_capture("https://opencode.ai/docs/new", "New", "# New")], "site", data_root=tmp_path)

    assert result.removed == ["old"]
    assert not (paths.pages_dir("site", tmp_path) / "old.md").exists()
    assert (paths.pages_dir("site", tmp_path) / "new.md").exists()


def test_write_corpus_is_deterministic_across_runs(tmp_path):
    pages = [_capture("https://opencode.ai/docs/intro", "Intro", "# Intro\n[x](../y)")]
    write_corpus(pages, "site", data_root=tmp_path)
    first = (paths.pages_dir("site", tmp_path) / "intro.md").read_text()
    write_corpus(pages, "site", data_root=tmp_path)
    assert (paths.pages_dir("site", tmp_path) / "intro.md").read_text() == first


def test_colliding_page_ids_are_disambiguated(tmp_path):
    # Same derived id ("index") from two distinct URLs must not clobber.
    pages = [
        normalize(CapturedPage("https://a.com/docs", "A", "# A"), STAMP),
        normalize(CapturedPage("https://a.com/docs/", "A2", "# A2"), STAMP),
    ]
    result = write_corpus(pages, "site", data_root=tmp_path)
    assert sorted(result.written) == ["index", "index-1"]
