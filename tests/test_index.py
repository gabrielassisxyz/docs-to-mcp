"""Unit tests for the FTS5 index build and search (no network)."""

from __future__ import annotations

import pytest

from docs_to_mcp.crawler import CapturedPage
from docs_to_mcp.index import build_index, search
from docs_to_mcp.ingester import normalize, write_corpus

STAMP = "2026-07-08T00:00:00Z"


def _corpus(tmp_path):
    pages = [
        normalize(CapturedPage("https://x.ai/docs/intro", "Getting Started", "How to install the widget."), STAMP),
        normalize(CapturedPage("https://x.ai/docs/auth", "Authentication", "Configure API keys and tokens."), STAMP),
    ]
    write_corpus(pages, "site", data_root=tmp_path)


def test_build_index_returns_row_count(tmp_path):
    _corpus(tmp_path)
    assert build_index("site", data_root=tmp_path) == 2


def test_search_ranks_matching_page_first(tmp_path):
    _corpus(tmp_path)
    build_index("site", data_root=tmp_path)
    hits = search("site", "API keys", data_root=tmp_path)
    assert hits[0].page_id == "auth"
    assert hits[0].source_url == "https://x.ai/docs/auth"


def test_search_matches_title(tmp_path):
    _corpus(tmp_path)
    build_index("site", data_root=tmp_path)
    hits = search("site", "authentication", data_root=tmp_path)
    assert hits and hits[0].page_id == "auth"


def test_search_handles_special_characters_without_crashing(tmp_path):
    _corpus(tmp_path)
    build_index("site", data_root=tmp_path)
    # FTS5 syntax chars must be neutralized, not injected: no exception, list back.
    assert isinstance(search("site", 'install "widget" AND (', data_root=tmp_path), list)
    # Punctuation around a real term still matches the page containing it.
    assert search("site", "(install)!", data_root=tmp_path)[0].page_id == "intro"


def test_search_empty_query_returns_nothing(tmp_path):
    _corpus(tmp_path)
    build_index("site", data_root=tmp_path)
    assert search("site", "   ", data_root=tmp_path) == []


def test_title_match_outranks_body_only_match(tmp_path):
    # One page merely mentions "garlic" many times in the body; the other has it
    # in the title. Title boost must rank the titled page first.
    pages = [
        normalize(CapturedPage("https://x.ai/docs/grimoire", "Grimoire",
                               "garlic garlic garlic combos and evolution notes " * 5), STAMP),
        normalize(CapturedPage("https://x.ai/docs/garlic", "Garlic", "A defensive weapon."), STAMP),
    ]
    write_corpus(pages, "site", data_root=tmp_path)
    build_index("site", data_root=tmp_path)
    assert search("site", "garlic", data_root=tmp_path)[0].page_id == "garlic"


def test_heading_match_is_weighted(tmp_path):
    pages = [
        normalize(CapturedPage("https://x.ai/docs/a", "A", "# Installation\nSteps here."), STAMP),
        normalize(CapturedPage("https://x.ai/docs/b", "B", "Body mentions installation once."), STAMP),
    ]
    write_corpus(pages, "site", data_root=tmp_path)
    build_index("site", data_root=tmp_path)
    assert search("site", "installation", data_root=tmp_path)[0].page_id == "a"


def test_title_match_beats_huge_body_only_match(tmp_path):
    # A giant page mentions the term deep in its prose; a small page has it in the
    # title. Body-length capping must stop the giant page from drowning the title
    # match, so the titled page still wins.
    filler = "lorem ipsum dolor sit amet " * 2000  # ~54k chars
    pages = [
        normalize(CapturedPage("https://x.ai/docs/big", "Big", filler + " tornado facts"), STAMP),
        normalize(CapturedPage("https://x.ai/docs/tornado", "Tornado", "A weapon."), STAMP),
    ]
    write_corpus(pages, "site", data_root=tmp_path)
    build_index("site", data_root=tmp_path)
    assert search("site", "tornado", data_root=tmp_path)[0].page_id == "tornado"


def test_and_query_falls_back_to_or_when_empty(tmp_path):
    # No page contains every term of a natural-language query, but one is clearly
    # the best partial match. AND returns nothing; the OR fallback surfaces it.
    pages = [
        normalize(CapturedPage("https://x.ai/docs/pumm", "Pummarola", "A healing weapon."), STAMP),
        normalize(CapturedPage("https://x.ai/docs/misc", "Misc", "Some other notes."), STAMP),
    ]
    write_corpus(pages, "site", data_root=tmp_path)
    build_index("site", data_root=tmp_path)
    # "best healing weapon pummarola": no page has all four words.
    hits = search("site", "best healing weapon pummarola", data_root=tmp_path)
    assert hits and hits[0].page_id == "pumm"


def test_build_is_deterministic(tmp_path):
    _corpus(tmp_path)
    first = build_index("site", data_root=tmp_path)
    second = build_index("site", data_root=tmp_path)
    assert first == second == 2
