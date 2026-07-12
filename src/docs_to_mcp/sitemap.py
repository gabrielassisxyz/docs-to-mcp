"""Discover URLs from a sitemap, an alternative to Firecrawl's link map.

More reliable and deterministic than crawling links on sites that publish a
sitemap — notably MediaWiki, whose per-namespace sitemaps let a caller target
just the content namespace (e.g. NS_0) instead of Templates/Modules/Talk pages.

Standard library only: fetch, gunzip if needed, parse <loc> elements, and recurse
one level when handed a <sitemapindex>.
"""

from __future__ import annotations

import gzip
import re
import urllib.request
import xml.etree.ElementTree as ET
from urllib.parse import urljoin

_USER_AGENT = "docs-to-mcp/0.1 (+https://github.com/; documentation crawler)"
_FETCH_TIMEOUT = 30
# Safety bound so pointing at a giant sitemap index cannot fan out unbounded.
_MAX_URLS = 100_000
# MediaWiki splits its sitemap by namespace: NS_0 = main content, NS_1 = Talk,
# NS_10 = Template, NS_828 = Module, etc. We keep only the content namespace.
_MW_NAMESPACE_RE = re.compile(r"/NS_(\d+)-\d+\.xml(?:\.gz)?$")
_MW_CONTENT_NAMESPACE = "0"


class SitemapError(RuntimeError):
    """Raised when a sitemap cannot be fetched or parsed."""


def resolve_sitemap(root_url: str) -> str | None:
    """Best-effort discovery of a site's sitemap via its robots.txt.

    Returns the first ``Sitemap:`` directive found (for MediaWiki this is the
    namespace index, which fetch_sitemap_urls then narrows to NS_0), or None so
    the caller can fall back to link-map discovery. Never raises.
    """
    robots_url = urljoin(root_url, "/robots.txt")
    try:
        body = _fetch(robots_url).decode("utf-8", "replace")
    except SitemapError:
        return None
    for line in body.splitlines():
        if line.lower().startswith("sitemap:"):
            return line.split(":", 1)[1].strip()
    return None


def fetch_sitemap_urls(sitemap_url: str, namespace: str = _MW_CONTENT_NAMESPACE) -> list[str]:
    """Return all page URLs listed in a sitemap (recursing one level for indexes).

    For a MediaWiki namespace-split index, only sitemaps for ``namespace`` are
    followed (default NS_0 content; pass e.g. "14" for the Category namespace).
    """
    urls: list[str] = []
    _collect(sitemap_url, urls, namespace, depth=0)
    # De-dupe while preserving first-seen order, then cap.
    seen: set[str] = set()
    deduped = [u for u in urls if not (u in seen or seen.add(u))]
    return deduped[:_MAX_URLS]


def _collect(sitemap_url: str, out: list[str], namespace: str, depth: int) -> None:
    if len(out) >= _MAX_URLS:
        return
    root = _parse(_fetch(sitemap_url))
    tag = _local_name(root.tag)
    if tag == "sitemapindex":
        if depth >= 2:
            return
        for loc in _select_namespace_sitemaps(_locs(root, "sitemap"), namespace):
            _collect(loc, out, namespace, depth + 1)
    else:  # urlset (or anything with <url><loc> children)
        out.extend(_locs(root, "url"))


def _select_namespace_sitemaps(sitemap_locs: list[str], namespace: str) -> list[str]:
    """On a MediaWiki namespace-split index keep only ``namespace``; else keep all."""
    namespaced = [(m.group(1), loc) for loc in sitemap_locs
                  if (m := _MW_NAMESPACE_RE.search(loc))]
    if not namespaced:
        return sitemap_locs
    return [loc for ns, loc in namespaced if ns == namespace]


def _fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=_FETCH_TIMEOUT) as response:
            data = response.read()
    except Exception as exc:  # noqa: BLE001 - normalize network/HTTP errors
        raise SitemapError(f"failed to fetch sitemap {url}: {exc}") from exc
    if data[:2] == b"\x1f\x8b":  # gzip magic
        data = gzip.decompress(data)
    return data


def _parse(data: bytes) -> ET.Element:
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise SitemapError(f"invalid sitemap XML: {exc}") from exc


def _locs(root: ET.Element, parent_tag: str) -> list[str]:
    """Extract <loc> text under each <parent_tag>, namespace-agnostic."""
    found: list[str] = []
    for parent in root:
        if _local_name(parent.tag) != parent_tag:
            continue
        for child in parent:
            if _local_name(child.tag) == "loc" and child.text:
                found.append(child.text.strip())
    return found


def _local_name(tag: str) -> str:
    """Strip the XML namespace: '{http://...}loc' -> 'loc'."""
    return tag.rsplit("}", 1)[-1]
