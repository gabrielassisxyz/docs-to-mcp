"""Orchestrates crawl -> ingest -> index behind one entry point.

Capture is concurrent and streaming: each page is written as soon as it is
scraped, so a large crawl survives interruption and can resume. The .md files on
disk double as the resume ledger — no separate queue.

Modes (auto-detected, or forced via ``incremental``):
  - fresh:       (re)capture every discovered page, then prune stale ones.
  - resume:      a prior crawl left .md but no pages.jsonl (never finalized) ->
                 capture only the pages still missing.
  - incremental: capture only pages not already in the corpus (cheap top-up).
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import json
from urllib.parse import urlparse

from . import categories, corpus, index, manifest, paths, sitemap
from .crawler import CrawlError, FirecrawlCrawler
from .ingester import assign_page_ids, finalize, normalize, write_page

DEFAULT_CONCURRENCY = 5


@dataclass(frozen=True, slots=True)
class RefreshResult:
    slug: str
    mode: str
    discovery: str
    discovered: int
    written: list[str]
    skipped: int
    removed: list[str]
    indexed: int
    page_count: int
    failed: list[tuple[str, str]] = field(default_factory=list)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def refresh(
    root_url: str,
    slug: str,
    *,
    max_pages: int = 50,
    locale: str | None = None,
    sitemap_url: str | None = None,
    auto_sitemap: bool = True,
    incremental: bool = False,
    concurrency: int = DEFAULT_CONCURRENCY,
    data_root: Path | str = paths.DEFAULT_DATA_ROOT,
    crawler: FirecrawlCrawler | None = None,
    crawled_at: str | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> RefreshResult:
    """Discover, capture (concurrently), persist, and index a documentation site."""
    crawler = crawler or FirecrawlCrawler()
    stamp = crawled_at or _utc_now_iso()

    # Deterministic discovery: use an explicit sitemap, else auto-resolve one from
    # robots.txt (a MediaWiki index is narrowed to its content namespace), else
    # fall back to Firecrawl's link map. This embeds the "how do I crawl this site"
    # recon in the tool instead of leaving it to per-run trial and error.
    if sitemap_url is None and auto_sitemap:
        sitemap_url = sitemap.resolve_sitemap(root_url)
    discovery = f"sitemap:{sitemap_url}" if sitemap_url else "link-map"

    urls = crawler.discover(root_url, max_pages, locale, sitemap_url)
    if not urls:
        # Never finalize on an empty discovery: it would prune the whole corpus.
        raise CrawlError(f"no documentation URLs discovered under {root_url}")
    id_map = assign_page_ids(urls)
    discovered_ids = set(id_map.values())

    page_dir = paths.pages_dir(slug, data_root)
    present_ids = {p.stem for p in page_dir.glob("*.md")} if page_dir.exists() else set()
    finalized = paths.pages_jsonl(slug, data_root).exists()

    mode, to_capture = _plan(urls, id_map, present_ids, finalized, incremental)

    captured_ids, failed = _capture_all(crawler, to_capture, id_map, stamp, slug, data_root,
                                        concurrency, on_progress)

    ingest = finalize(slug, stamp, set(captured_ids), discovered_ids, data_root)
    indexed = index.build_index(slug, data_root) if paths.pages_jsonl(slug, data_root).exists() else 0
    page_count = len(list(page_dir.glob("*.md"))) if page_dir.exists() else 0

    manifest.write(
        manifest.Manifest(root_url=root_url, slug=slug, last_crawled_at=stamp,
                          page_count=page_count, locale=locale, sitemap_url=sitemap_url),
        data_root,
    )

    return RefreshResult(
        slug=slug, mode=mode, discovery=discovery, discovered=len(urls), written=ingest.written,
        skipped=len(urls) - len(to_capture), removed=ingest.removed,
        indexed=indexed, page_count=page_count, failed=failed,
    )


@dataclass(frozen=True, slots=True)
class CategoryResult:
    categories: int
    tagged_pages: int
    category_pages: int
    failed: int


def enrich_categories(
    slug: str,
    *,
    concurrency: int = DEFAULT_CONCURRENCY,
    data_root: Path | str = paths.DEFAULT_DATA_ROOT,
    crawler: FirecrawlCrawler | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> CategoryResult:
    """Crawl the Category namespace and tag existing articles with their categories.

    Reads the corpus built by ``refresh``, scrapes category pages, writes
    categories.json, then rebuilds pages.jsonl (merging categories) and the index.
    """
    source = manifest.read(slug, data_root)
    index_sitemap = source.sitemap_url or sitemap.resolve_sitemap(source.root_url)
    if not index_sitemap:
        raise CrawlError(f"no sitemap for '{slug}'; category grouping needs a MediaWiki sitemap")

    category_urls = [
        url for url in sitemap.fetch_sitemap_urls(index_sitemap, namespace=categories.CATEGORY_NAMESPACE)
        if ":" in urlparse(url).path.rsplit("/", 1)[-1]  # only namespaced Category: pages
    ]
    if not category_urls:
        raise CrawlError(f"no Category namespace found for '{slug}' (not a MediaWiki site?)")

    article_ids = set(corpus.load_records(slug, data_root))
    crawler = crawler or FirecrawlCrawler()
    category_map, failed = categories.build_category_map(
        crawler, category_urls, article_ids, concurrency, on_progress)

    # Merge (union) with any prior run so this scrape-flaky step is monotonic:
    # re-running recovers pages that failed last time without losing good data.
    # Trade-off: a category removed on the wiki lingers until a clean re-crawl.
    category_map = _merge_categories(paths.categories_json(slug, data_root), category_map)
    paths.categories_json(slug, data_root).write_text(
        json.dumps(category_map, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    page_dir = paths.pages_dir(slug, data_root)
    present_ids = {p.stem for p in page_dir.glob("*.md")}
    finalize(slug, source.last_crawled_at, set(), present_ids, data_root)
    index.build_index(slug, data_root)

    return CategoryResult(
        categories=len({c for cs in category_map.values() for c in cs}),
        tagged_pages=len(category_map),
        category_pages=len(category_urls),
        failed=failed,
    )


def _merge_categories(path: Path, new_map: dict[str, list[str]]) -> dict[str, list[str]]:
    prior = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    return {
        page_id: sorted(set(prior.get(page_id, [])) | set(new_map.get(page_id, [])))
        for page_id in set(prior) | set(new_map)
    }


def _plan(urls, id_map, present_ids, finalized, incremental):
    """Decide the mode and which URLs to capture."""
    resuming = bool(present_ids) and not finalized
    if resuming:
        mode = "resume"
    elif incremental:
        mode = "incremental"
    else:
        mode = "fresh"
    if mode == "fresh":
        return mode, list(urls)
    return mode, [url for url in urls if id_map[url] not in present_ids]


def _capture_all(crawler, urls, id_map, stamp, slug, data_root, concurrency, on_progress):
    """Scrape and persist pages concurrently; return (captured_ids, failures)."""
    captured_ids: list[str] = []
    failed: list[tuple[str, str]] = []
    total = len(urls)
    if not total:
        return captured_ids, failed

    def work(url: str) -> str:
        page = normalize(crawler.capture(url), stamp, id_map[url])
        write_page(page, slug, data_root)
        return page.page_id

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futures = {pool.submit(work, url): url for url in urls}
        for done, future in enumerate(as_completed(futures), start=1):
            url = futures[future]
            try:
                captured_ids.append(future.result())
            except Exception as exc:  # noqa: BLE001 - one bad page must not kill the crawl
                failed.append((url, str(exc)))
            if on_progress:
                on_progress(done, total)
    return captured_ids, failed
