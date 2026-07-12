"""Thin wrapper over the Firecrawl v2 SDK: discover doc URLs and capture markdown.

We deliberately use ``map`` + per-page ``scrape`` instead of the bulk ``/crawl``
endpoint so the set of pages that enter the corpus is filtered deterministically
and every capture is independently testable and diffable.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from urllib.parse import urlparse

DEFAULT_API_URL = "http://localhost:3002"

# Path segments that mark non-documentation pages. Even under a /docs prefix a
# site may link out to these; we never want them in a docs corpus.
_NON_DOC_SEGMENTS = frozenset(
    {"blog", "changelog", "pricing", "careers", "about", "login",
     "signup", "terms", "privacy", "contact"}
)

# ISO 639-1 language codes, used to detect localized doc paths like /docs/de/...
# so a default crawl captures the canonical (unprefixed) docs instead of a
# translation. A real doc section named with a 2-letter code (e.g. /docs/is)
# could be misread as a locale; raise this with --locale if it ever bites.
_ISO_639_1 = frozenset(
    "aa ab ae af ak am an ar as av ay az ba be bg bh bi bm bn bo br bs ca ce ch "
    "co cr cs cu cv cy da de dv dz ee el en eo es et eu fa ff fi fj fo fr fy ga "
    "gd gl gn gu gv ha he hi ho hr ht hu hy hz ia id ie ig ii ik io is it iu ja "
    "jv ka kg ki kj kk kl km kn ko kr ks ku kv kw ky la lb lg li ln lo lt lu lv "
    "mg mh mi mk ml mn mr ms mt my na nb nd ne ng nl nn no nr nv ny oc oj om or "
    "os pa pi pl ps pt qu rm rn ro ru rw sa sc sd se sg si sk sl sm sn so sq sr "
    "ss st su sv sw ta te tg th ti tk tl tn to tr ts tt tw ty ug uk ur uz ve vi "
    "vo wa wo xh yi yo za zh zu".split()
)
# Region-tagged locales such as pt-br, zh-cn, zh-tw.
_REGION_LOCALE_RE = re.compile(r"^([a-z]{2})-[a-z]{2,4}$")

# Discovery budget: map is a single cheap call, so over-fetch links before
# filtering. The floor matters on heavily-localized sites where locale pages can
# be >90% of URLs; without it, the canonical docs may not survive the map cap.
_DISCOVERY_MULTIPLIER = 5
_DISCOVERY_FLOOR = 1000


class CrawlError(RuntimeError):
    """Raised when discovery or capture fails in a way the caller should surface."""


@dataclass(frozen=True, slots=True)
class CapturedPage:
    """Raw capture from Firecrawl, before normalization by the ingester."""

    source_url: str
    title: str
    markdown: str


def _is_locale_segment(segment: str) -> bool:
    """True if a path segment looks like a language/locale code (de, pt-br, ...)."""
    seg = segment.lower()
    if seg in _ISO_639_1:
        return True
    match = _REGION_LOCALE_RE.match(seg)
    return bool(match and match.group(1) in _ISO_639_1)


def _first_segment_after(prefix: str, path: str) -> str | None:
    """First path segment beyond the docs-root prefix, or None at the root."""
    trimmed = path.rstrip("/")
    rest = trimmed[len(prefix):] if prefix and trimmed.startswith(prefix) else trimmed
    parts = [seg for seg in rest.split("/") if seg]
    return parts[0] if parts else None


def filter_doc_urls(
    root_url: str, urls: list[str], max_pages: int, locale: str | None = None
) -> list[str]:
    """Keep same-host URLs under the docs root path, minus non-doc sections.

    Localization: with ``locale=None`` (default) localized paths (``/docs/de/...``)
    are dropped so the canonical docs are captured; pass ``locale='de'`` to keep
    only that language. If a site has *only* localized paths, the default keeps
    them rather than returning an empty corpus.

    Pure function (no network) so the filtering rules stay unit-testable. Results
    are de-duplicated and sorted for deterministic, diffable corpora.
    """
    root = urlparse(root_url)
    prefix = root.path.rstrip("/")
    want = locale.lower() if locale else None
    kept: set[str] = set()
    dropped_locale: set[str] = set()
    for raw in urls:
        parsed = urlparse(raw)
        if parsed.netloc != root.netloc:
            continue
        if prefix and not parsed.path.rstrip("/").startswith(prefix):
            continue
        segments = {seg for seg in parsed.path.lower().split("/") if seg}
        if segments & _NON_DOC_SEGMENTS:
            continue
        # Normalize away trailing slashes and fragments so /x and /x/ don't dupe.
        normalized = parsed._replace(fragment="", path=parsed.path.rstrip("/") or "/").geturl()
        first = _first_segment_after(prefix, parsed.path)
        if want is not None:
            if first == want:
                kept.add(normalized)
        elif first is not None and _is_locale_segment(first):
            dropped_locale.add(normalized)
        else:
            kept.add(normalized)
    # Fallback: a fully-localized site (no canonical pages) still yields a corpus.
    if want is None and not kept and dropped_locale:
        kept = dropped_locale
    return sorted(kept)[:max_pages]


class FirecrawlCrawler:
    """Discovers documentation URLs and captures each page as clean markdown.

    ``client`` is injectable so tests can pass a fake without a live Firecrawl.
    """

    def __init__(self, api_url: str | None = None, api_key: str | None = None, client=None):
        if client is not None:
            self._client = client
        else:
            from firecrawl import Firecrawl

            self._client = Firecrawl(
                api_url=api_url or os.environ.get("FIRECRAWL_API_URL", DEFAULT_API_URL),
                api_key=api_key or os.environ.get("FIRECRAWL_API_KEY") or "",
            )

    def discover(
        self,
        root_url: str,
        max_pages: int,
        locale: str | None = None,
        sitemap_url: str | None = None,
    ) -> list[str]:
        """Return up to ``max_pages`` documentation URLs.

        Discovery source is an explicit ``sitemap_url`` when given (deterministic,
        and lets MediaWiki callers target one namespace), otherwise Firecrawl's
        link map.
        """
        if sitemap_url:
            from . import sitemap

            urls = sitemap.fetch_sitemap_urls(sitemap_url)
        else:
            try:
                data = self._client.map(
                    root_url,
                    limit=max(max_pages * _DISCOVERY_MULTIPLIER, _DISCOVERY_FLOOR),
                    sitemap="include",
                )
            except Exception as exc:  # noqa: BLE001 - wrap SDK/transport errors uniformly
                raise CrawlError(f"map failed for {root_url}: {exc}") from exc
            urls = [link.url for link in data.links if getattr(link, "url", None)]
        return filter_doc_urls(root_url, urls, max_pages, locale)

    def capture(self, url: str) -> CapturedPage:
        """Scrape one URL to markdown, raising CrawlError on empty content."""
        try:
            doc = self._client.scrape(url, formats=["markdown"], only_main_content=True)
        except Exception as exc:  # noqa: BLE001 - wrap SDK/transport errors uniformly
            raise CrawlError(f"scrape failed for {url}: {exc}") from exc
        markdown = (doc.markdown or "").strip()
        if not markdown:
            raise CrawlError(f"empty markdown for {url}")
        metadata = getattr(doc, "metadata", None)
        title = (getattr(metadata, "title", None) if metadata else None) or url
        return CapturedPage(source_url=url, title=title, markdown=markdown)
