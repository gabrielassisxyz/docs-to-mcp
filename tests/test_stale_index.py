"""The failure mode that shipped: an index built by an older version of this tool.

Nothing tested a *stale* index, so nothing caught that querying one raises
`sqlite3.InterfaceError: column index out of range` -- a message that names no cause and
suggests no fix, and which reads like a corrupt corpus. The pages were always fine; only
the index was old, and rebuilding it from disk takes seconds. A real corpus of 2,281
pages sat broken behind that error.
"""

from __future__ import annotations

import sqlite3

import pytest

from docs_to_mcp import index, paths


def _corpus(tmp_path, slug="docs"):
    """A minimal on-disk corpus: pages.jsonl + one page file."""
    pages_dir = paths.pages_dir(slug, tmp_path)
    pages_dir.mkdir(parents=True)
    (pages_dir / "intro.md").write_text(
        "---\npage_id: intro\n---\n\n# Intro\n\nConfigure the widget here.\n",
        encoding="utf-8",
    )
    paths.pages_jsonl(slug, tmp_path).write_text(
        '{"page_id": "intro", "source_url": "https://x/intro", '
        '"title": "Intro", "section": "", "categories": []}\n',
        encoding="utf-8",
    )
    return slug


def test_fresh_index_is_searchable(tmp_path):
    slug = _corpus(tmp_path)
    assert index.build_index(slug, tmp_path) == 1

    hits = index.search(slug, "configure widget", limit=5, data_root=tmp_path)
    assert [h.page_id for h in hits] == ["intro"]


def test_stale_index_raises_an_actionable_error(tmp_path):
    """An index from an older schema must name the fix, not leak a SQLite error."""
    slug = _corpus(tmp_path)
    index.build_index(slug, tmp_path)

    # Simulate what an older version left on disk: the same FTS table, no version stamp.
    db = paths.index_db(slug, tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("DROP TABLE index_meta")
    conn.commit()
    conn.close()

    with pytest.raises(index.StaleIndexError) as exc:
        index.search(slug, "configure", data_root=tmp_path)

    message = str(exc.value)
    # The user must learn three things: what is stale, that their pages are safe, and the
    # exact command that fixes it. Losing any one of them sends them back to re-crawling.
    assert "older version" in message
    assert "pages are fine" in message
    assert f"docs-to-mcp reindex --slug {slug}" in message


def test_reindex_repairs_a_stale_index_without_recrawling(tmp_path):
    """The fix the error message promises has to actually work, offline."""
    slug = _corpus(tmp_path)
    index.build_index(slug, tmp_path)

    conn = sqlite3.connect(paths.index_db(slug, tmp_path))
    conn.execute("DROP TABLE index_meta")
    conn.commit()
    conn.close()

    with pytest.raises(index.StaleIndexError):
        index.search(slug, "configure", data_root=tmp_path)

    # Exactly what `docs-to-mcp reindex` calls -- no network, no crawler, no Firecrawl.
    assert index.build_index(slug, tmp_path) == 1

    hits = index.search(slug, "configure widget", limit=5, data_root=tmp_path)
    assert [h.page_id for h in hits] == ["intro"]


def test_version_bump_invalidates_an_existing_index(tmp_path, monkeypatch):
    """A future schema change must be caught by the check, not by a crash downstream."""
    slug = _corpus(tmp_path)
    index.build_index(slug, tmp_path)

    monkeypatch.setattr(index, "_SCHEMA_VERSION", index._SCHEMA_VERSION + 1)

    with pytest.raises(index.StaleIndexError):
        index.search(slug, "configure", data_root=tmp_path)
