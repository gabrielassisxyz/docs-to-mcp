"""Per-corpus manifest: enough state to refresh a corpus without re-supplying args.

Stored as manifest.json so `refresh_docs` knows the root URL to re-crawl and the
server can report when the corpus was last captured.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from . import paths


@dataclass(frozen=True, slots=True)
class Manifest:
    root_url: str
    slug: str
    last_crawled_at: str
    page_count: int
    # Locale used at crawl time, so refresh_docs re-crawls the same language.
    # Defaulted for backward compatibility with manifests written before locales.
    locale: str | None = None
    # Sitemap discovery source, if the crawl used one instead of Firecrawl map.
    sitemap_url: str | None = None


def _path(slug: str, data_root: Path | str) -> Path:
    return paths.corpus_dir(slug, data_root) / "manifest.json"


def write(manifest: Manifest, data_root: Path | str = paths.DEFAULT_DATA_ROOT) -> None:
    path = _path(manifest.slug, data_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(manifest), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read(slug: str, data_root: Path | str = paths.DEFAULT_DATA_ROOT) -> Manifest:
    path = _path(slug, data_root)
    if not path.exists():
        raise FileNotFoundError(f"no manifest for '{slug}' at {path}; crawl it first")
    return Manifest(**json.loads(path.read_text(encoding="utf-8")))
