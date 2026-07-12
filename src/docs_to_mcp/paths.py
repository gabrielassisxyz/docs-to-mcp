"""Single source of truth for the on-disk corpus layout.

Referenced by the ingester (writes), the index builder (reads/writes the db), and
the MCP server (reads), so the layout lives in exactly one place.
"""

from __future__ import annotations

from pathlib import Path

DEFAULT_DATA_ROOT = Path("data/docs")


def corpus_dir(slug: str, data_root: Path | str = DEFAULT_DATA_ROOT) -> Path:
    return Path(data_root) / slug


def pages_dir(slug: str, data_root: Path | str = DEFAULT_DATA_ROOT) -> Path:
    return corpus_dir(slug, data_root) / "pages"


def pages_jsonl(slug: str, data_root: Path | str = DEFAULT_DATA_ROOT) -> Path:
    return corpus_dir(slug, data_root) / "pages.jsonl"


def index_db(slug: str, data_root: Path | str = DEFAULT_DATA_ROOT) -> Path:
    return corpus_dir(slug, data_root) / "index.sqlite"


def categories_json(slug: str, data_root: Path | str = DEFAULT_DATA_ROOT) -> Path:
    return corpus_dir(slug, data_root) / "categories.json"
