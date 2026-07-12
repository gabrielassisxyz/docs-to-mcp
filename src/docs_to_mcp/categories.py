"""Build article -> category grouping for MediaWiki-style sites.

Category membership is not in the captured articles (the wiki's category footer is
stripped by main-content extraction), so we read it from the Category namespace:
each category page lists its member articles. We scrape those pages, parse the
member links, and invert to a page_id -> [category names] map.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import unquote, urlparse

from .crawler import CrawlError, FirecrawlCrawler
from .models import derive_page_id

# Category namespace in MediaWiki sitemaps.
CATEGORY_NAMESPACE = "14"
# Wiki article links inside a page, e.g. /w/Garlic or /w/Gemini_(I) (stop at #/?).
_WIKI_LINK_RE = re.compile(r"/w/([^\"')\s#?]+)")

# Substring patterns (lowercase) that mark MediaWiki maintenance/tracking
# categories rather than content groupings. These are normally "hidden categories"
# on the wiki, but scraping cannot see that flag, so we filter by name. This is a
# heuristic proxy: extend it as new wikis surface new patterns. The precise signal
# is the page property `hiddencat` via api.php (see the docs-to-mcp agent prompt) —
# used here only because this wiki blocks api.php in robots.txt.
_MAINTENANCE_PATTERNS = (
    "pages using", "pages with", "pages needing", "pages that need", "pages misusing",
    "under construction", "disambiguation", "incomplete", "needing", "documentation",
    "reference", "cleanup", "unarchived", "stub",
)


def _is_maintenance(category_name: str) -> bool:
    low = category_name.lower()
    return any(pattern in low for pattern in _MAINTENANCE_PATTERNS)


def build_category_map(
    crawler: FirecrawlCrawler,
    category_urls: list[str],
    article_ids: set[str],
    concurrency: int = 5,
    on_progress: Callable[[int, int], None] | None = None,
) -> tuple[dict[str, list[str]], int]:
    """Scrape category pages; return ({article page_id: category names}, failures).

    Membership is intersected with ``article_ids`` so only real captured articles
    count (subcategory and cross-namespace links are dropped automatically). The
    failure count is surfaced so a flaky, low-coverage run is visible rather than
    silently degrading the corpus.
    """
    grouped: dict[str, set[str]] = defaultdict(set)
    total = len(category_urls)
    failed = 0

    def work(url: str) -> tuple[str, set[str]]:
        name = _category_name(url)
        if _is_maintenance(name):
            return name, set()  # skip scraping maintenance categories entirely
        members = _parse_members(crawler.capture(url).markdown) & article_ids
        return name, members

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futures = {pool.submit(work, url): url for url in category_urls}
        for done, future in enumerate(as_completed(futures), start=1):
            try:
                name, members = future.result()
                for page_id in members:
                    grouped[page_id].add(name)
            except CrawlError:
                failed += 1  # a category page that fails to scrape contributes nothing
            if on_progress:
                on_progress(done, total)

    return {page_id: sorted(names) for page_id, names in grouped.items()}, failed


def _category_name(category_url: str) -> str:
    """`/w/Category:Base_Weapons` -> `Base Weapons`."""
    title = unquote(urlparse(category_url).path).rsplit("/", 1)[-1]
    _, _, name = title.partition(":")
    return (name or title).replace("_", " ").strip()


def _parse_members(markdown: str) -> set[str]:
    """Page ids of the wiki articles linked from a category page's content."""
    return {derive_page_id("/w/" + match) for match in _WIKI_LINK_RE.findall(markdown)}
