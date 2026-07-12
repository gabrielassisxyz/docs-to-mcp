"""Normalize captured pages into a stable, diffable local corpus.

Turns raw ``CapturedPage`` markdown into ``Page`` records (absolute links, derived
ids, content hash) and writes them as ``pages/<page_id>.md`` plus ``pages.jsonl``.
Stale pages no longer present in a capture are pruned so a refresh mirrors the site.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import urljoin

from . import frontmatter, paths
from .crawler import CapturedPage
from .models import (
    Page,
    compute_content_hash,
    derive_page_id,
    derive_section,
)

# Markdown inline links/images: group 1 = "[text](" or "![text](", group 2 = the
# target (up to whitespace or ")"), group 3 = optional ' "title")' remainder.
_LINK_RE = re.compile(r'(!?\[[^\]]*\]\()([^)\s]+)(\s*[^)]*\))')
# A target is already absolute if it has a scheme, is protocol-relative, or is a
# pure in-page anchor.
_ABSOLUTE_RE = re.compile(r"^(?:[a-z][a-z0-9+.\-]*:|//|#)", re.IGNORECASE)

# Site chrome: navigation affordances that mean nothing once the page is a text file.
# This corpus exists to be read by a model, and every one of these lines is context
# window spent on a link nobody can click. One captured MediaWiki wiki carried 14,473
# "[edit]" markers and 1,085 "Jump to navigation" links across its pages -- paid for on
# every single query against it, forever.
#
# Deliberately conservative: each pattern is anchored to an unmistakable shape (a link
# whose target is the page's own #mw-head anchor, an [edit] link into a MediaWiki
# action=edit URL). Prose is never touched. Cutting real content to save tokens would be
# a far worse trade than leaving some chrome behind -- when in doubt, keep it.
_CHROME_RES = (
    # [Jump to navigation](…#mw-head), [Skip to content](…#_top), [Back to top](…)
    re.compile(r"^\s*\[(?:Jump to|Skip to|Back to)[^\]]*\]\([^)]*\)\s*$", re.MULTILINE | re.IGNORECASE),
    # MediaWiki section edit links: \[[edit](…action=edit…)] and | [edit source](…)
    re.compile(r"\\?\[\s*\[edit(?:\s+source)?\]\([^)]*\)\s*(?:\|\s*\[edit\s+source\]\([^)]*\)\s*)?\\?\]"),
    re.compile(r"\|\s*\[edit(?:\s+source)?\]\([^)]*\)"),
    re.compile(r"\[edit(?:\s+source)?\]\([^)]*\)"),
)
# Collapse the blank-line craters the removals leave behind.
_BLANK_RUN_RE = re.compile(r"\n{3,}")


@dataclass(frozen=True, slots=True)
class IngestResult:
    written: list[str]
    removed: list[str]

    @property
    def total(self) -> int:
        return len(self.written)


def resolve_links(markdown: str, base_url: str) -> str:
    """Rewrite relative markdown links/images to absolute URLs against base_url."""

    def _replace(match: re.Match[str]) -> str:
        target = match.group(2)
        if _ABSOLUTE_RE.match(target):
            return match.group(0)
        return match.group(1) + urljoin(base_url, target) + match.group(3)

    return _LINK_RE.sub(_replace, markdown)


def strip_chrome(markdown: str) -> str:
    """Remove navigation affordances that carry no meaning in a text corpus."""
    for pattern in _CHROME_RES:
        markdown = pattern.sub("", markdown)
    return _BLANK_RUN_RE.sub("\n\n", markdown).strip() + "\n"


def normalize(captured: CapturedPage, crawled_at: str, page_id: str | None = None) -> Page:
    """Convert a raw capture into a normalized Page (pure, no IO).

    ``page_id`` is passed in when the caller pre-assigned collision-free ids for a
    whole URL set (streaming path); it defaults to deriving from the URL.
    """
    markdown = strip_chrome(resolve_links(captured.markdown, captured.source_url))
    return Page(
        page_id=page_id or derive_page_id(captured.source_url),
        source_url=captured.source_url,
        title=captured.title,
        section=derive_section(captured.source_url),
        crawled_at=crawled_at,
        content_hash=compute_content_hash(markdown),
        markdown=markdown,
    )


def assign_page_ids(urls: list[str]) -> dict[str, str]:
    """Map each URL to a unique, deterministic page_id, disambiguating collisions.

    Computed over the whole URL set up front so the streaming capture path can
    write pages concurrently without racing on a shared id — and so a resumed
    crawl reproduces the exact same ids for already-written pages.
    """
    by_id: dict[str, list[str]] = {}
    for url in urls:
        by_id.setdefault(derive_page_id(url), []).append(url)
    mapping: dict[str, str] = {}
    for page_id, group in by_id.items():
        for offset, url in enumerate(sorted(group)):
            mapping[url] = page_id if offset == 0 else f"{page_id}-{offset}"
    return mapping


def _assign_unique_ids(pages: list[Page]) -> list[Page]:
    """Disambiguate any page_id collisions deterministically by source_url order."""
    by_id: dict[str, list[Page]] = {}
    for page in pages:
        by_id.setdefault(page.page_id, []).append(page)
    result: list[Page] = []
    for page_id, group in by_id.items():
        if len(group) == 1:
            result.append(group[0])
            continue
        for offset, page in enumerate(sorted(group, key=lambda p: p.source_url)):
            result.append(page if offset == 0 else replace(page, page_id=f"{page_id}-{offset}"))
    return result


def write_corpus(pages: list[Page], slug: str, data_root: Path | str = paths.DEFAULT_DATA_ROOT) -> IngestResult:
    """Write pages/*.md and pages.jsonl, pruning pages absent from this capture."""
    pages = _assign_unique_ids(pages)
    pages.sort(key=lambda p: p.page_id)

    page_dir = paths.pages_dir(slug, data_root)
    page_dir.mkdir(parents=True, exist_ok=True)

    keep = {page.page_id for page in pages}
    removed = _prune(page_dir, keep)

    for page in pages:
        _write_page_file(page, page_dir)

    _write_jsonl(paths.pages_jsonl(slug, data_root), pages)
    return IngestResult(written=[p.page_id for p in pages], removed=removed)


def write_page(page: Page, slug: str, data_root: Path | str = paths.DEFAULT_DATA_ROOT) -> None:
    """Persist a single page's .md immediately (streaming/resumable capture path).

    Safe to call concurrently: each page writes its own distinct file, and ids are
    pre-assigned to be unique, so there is no shared state to race on.
    """
    page_dir = paths.pages_dir(slug, data_root)
    page_dir.mkdir(parents=True, exist_ok=True)
    _write_page_file(page, page_dir)


def finalize(
    slug: str,
    stamp: str,
    captured_ids: set[str],
    keep_ids: set[str],
    data_root: Path | str = paths.DEFAULT_DATA_ROOT,
) -> IngestResult:
    """Rebuild pages.jsonl from the .md on disk and prune pages outside keep_ids.

    ``crawled_at`` is ``stamp`` for pages captured this run and preserved from the
    prior pages.jsonl for pages left untouched (so an incremental run doesn't
    falsely refresh timestamps of unchanged pages).
    """
    page_dir = paths.pages_dir(slug, data_root)
    prior = _read_prior_crawled_at(paths.pages_jsonl(slug, data_root))
    categories = _read_categories(paths.categories_json(slug, data_root))

    records: list[dict] = []
    for page_id in sorted(keep_ids):
        md_path = page_dir / f"{page_id}.md"
        if not md_path.exists():
            continue
        meta, _ = frontmatter.parse(md_path.read_text(encoding="utf-8"))
        crawled_at = stamp if page_id in captured_ids else prior.get(page_id, stamp)
        records.append({
            "page_id": page_id,
            "source_url": meta.get("source_url", ""),
            "title": meta.get("title", ""),
            "section": meta.get("section", ""),
            "categories": categories.get(page_id, []),
            "crawled_at": crawled_at,
            "content_hash": meta.get("content_hash", ""),
        })

    _write_jsonl_records(paths.pages_jsonl(slug, data_root), records)
    removed = _prune(page_dir, keep_ids)
    return IngestResult(written=sorted(captured_ids), removed=removed)


def _read_prior_crawled_at(jsonl_path: Path) -> dict[str, str]:
    if not jsonl_path.exists():
        return {}
    prior = {}
    for line in jsonl_path.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        prior[record["page_id"]] = record["crawled_at"]
    return prior


def _read_categories(categories_path: Path) -> dict[str, list[str]]:
    """Optional page_id -> [category] map produced by the category-enrichment step."""
    if not categories_path.exists():
        return {}
    return json.loads(categories_path.read_text(encoding="utf-8"))


def _write_page_file(page: Page, page_dir: Path) -> None:
    content = frontmatter.dump(page.frontmatter_meta(), page.markdown)
    (page_dir / f"{page.page_id}.md").write_text(content, encoding="utf-8")


def _prune(page_dir: Path, keep: set[str]) -> list[str]:
    removed: list[str] = []
    for existing in page_dir.glob("*.md"):
        if existing.stem not in keep:
            existing.unlink()
            removed.append(existing.stem)
    return sorted(removed)


def _write_jsonl(path: Path, pages: list[Page]) -> None:
    _write_jsonl_records(path, [page.jsonl_record() for page in pages])


def _write_jsonl_records(path: Path, records: list[dict[str, str]]) -> None:
    lines = [json.dumps(record, ensure_ascii=False, sort_keys=True) for record in records]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
