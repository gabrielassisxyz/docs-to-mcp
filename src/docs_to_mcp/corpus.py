"""Read-side helpers over a captured corpus, used by the MCP server."""

from __future__ import annotations

import json
from pathlib import Path

from . import frontmatter, paths


def load_records(slug: str, data_root: Path | str = paths.DEFAULT_DATA_ROOT) -> dict[str, dict]:
    """Return {page_id: metadata record} from pages.jsonl ({} if no corpus)."""
    path = paths.pages_jsonl(slug, data_root)
    if not path.exists():
        return {}
    records = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        records[record["page_id"]] = record
    return records


def read_body(slug: str, page_id: str, data_root: Path | str = paths.DEFAULT_DATA_ROOT) -> str | None:
    """Return the normalized markdown body for a page, or None if it is missing."""
    path = paths.pages_dir(slug, data_root) / f"{page_id}.md"
    if not path.exists():
        return None
    _, body = frontmatter.parse(path.read_text(encoding="utf-8"))
    return body
