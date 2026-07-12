"""Navigation chrome is context-window budget spent on links nobody can click.

One captured MediaWiki wiki carried 14,473 "[edit]" markers and 1,085 "Jump to
navigation" links -- 1.8 MB, roughly 446k tokens, paid for on every query against that
corpus forever.

The tests that matter here are the ones proving we do NOT eat prose. Removing chrome is
worth a little; removing content is worth less than nothing, because it corrupts the
corpus silently and no reader can tell.
"""

from __future__ import annotations

from docs_to_mcp.ingester import strip_chrome


def test_removes_mediawiki_section_edit_links():
    md = (
        "## Weapons\n\n"
        "[edit](https://wiki.example/w/Page?action=edit&section=1 \"Edit section\")\n\n"
        "Garlic damages nearby enemies.\n"
    )
    out = strip_chrome(md)
    assert "action=edit" not in out
    assert "Garlic damages nearby enemies." in out
    assert "## Weapons" in out


def test_removes_edit_source_variants():
    md = "\\[[edit](https://w/x?action=edit) | [edit source](https://w/x?action=raw)\\]\n\nBody.\n"
    out = strip_chrome(md)
    assert "edit" not in out.lower().replace("body", "")
    assert "Body." in out


def test_removes_jump_and_skip_navigation():
    md = (
        "[Jump to navigation](https://wiki.example/w/Page#mw-head)\n"
        "[Skip to content](https://docs.example/page#_top)\n\n"
        "# Real Title\n\nReal content.\n"
    )
    out = strip_chrome(md)
    assert "Jump to navigation" not in out
    assert "Skip to content" not in out
    assert "# Real Title" in out
    assert "Real content." in out


def test_keeps_prose_that_merely_mentions_editing():
    """The word 'edit' in a sentence is content. Only the link chrome goes."""
    md = "To edit the config, open `config.json`. See [the guide](https://x/guide).\n"
    out = strip_chrome(md)
    assert "To edit the config" in out
    assert "[the guide](https://x/guide)" in out


def test_keeps_ordinary_links_and_code():
    md = (
        "Install with [uv](https://astral.sh/uv):\n\n"
        "```sh\nuv sync\n```\n\n"
        "See [the API reference](https://example.com/api).\n"
    )
    assert strip_chrome(md).strip() == md.strip()


def test_collapses_the_gaps_left_behind():
    """Stripping a line should not leave a crater of blank lines where it stood."""
    md = (
        "# Title\n\n"
        "[edit](https://w/x?action=edit)\n\n"
        "[edit](https://w/x?action=edit&section=2)\n\n"
        "Content.\n"
    )
    assert "\n\n\n" not in strip_chrome(md)
