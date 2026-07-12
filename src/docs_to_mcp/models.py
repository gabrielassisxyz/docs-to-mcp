"""Core data model shared across the crawl -> ingest -> index pipeline."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from urllib.parse import unquote, urlparse

# Metadata fields written to pages.jsonl, in order. The markdown body is stored
# separately in pages/<page_id>.md so the JSONL stays small and greppable.
JSONL_FIELDS = ("page_id", "source_url", "title", "section", "crawled_at", "content_hash")

# Frontmatter fields written into each pages/<page_id>.md. crawled_at is
# intentionally excluded so the .md files only diff when their content changes;
# the timestamp lives in pages.jsonl (a regenerated index file).
FRONTMATTER_FIELDS = ("page_id", "source_url", "title", "section", "content_hash")


@dataclass(frozen=True, slots=True)
class Page:
    """One normalized documentation page captured from a source URL.

    ``crawled_at`` is an ISO-8601 UTC timestamp supplied by the caller. The model
    never reads the wall clock itself so that ingestion stays deterministic and
    testable; the pipeline stamps the time once per run.
    """

    page_id: str
    source_url: str
    title: str
    section: str
    crawled_at: str
    content_hash: str
    markdown: str

    def jsonl_record(self) -> dict[str, str]:
        """Metadata-only view for pages.jsonl (excludes the markdown body)."""
        return {field: getattr(self, field) for field in JSONL_FIELDS}

    def frontmatter_meta(self) -> dict[str, str]:
        """Stable metadata view for the .md frontmatter (excludes crawled_at)."""
        return {field: getattr(self, field) for field in FRONTMATTER_FIELDS}


def compute_content_hash(markdown: str) -> str:
    """Stable content fingerprint used to skip rewrites and produce clean diffs."""
    return hashlib.sha256(markdown.encode("utf-8")).hexdigest()


def derive_page_id(source_url: str) -> str:
    """Derive a deterministic, filesystem-safe page id from a URL path.

    ``https://opencode.ai/docs/config/mcp`` -> ``config-mcp``. The docs root path
    itself collapses to ``index``. The path is URL-decoded and non-ASCII word
    characters are preserved, so wiki titles like ``ℕ𝕆_𝔽𝕌𝕋𝕌ℝ𝔼`` keep a
    distinct, meaningful id instead of collapsing to nothing.
    """
    path = unquote(urlparse(source_url).path).strip("/")
    # Drop a leading "docs" segment so ids read as sections, not "docs-docs-...".
    segments = [seg for seg in path.split("/") if seg and seg != "docs"]
    slug = "-".join(segments).replace("_", "-").lower()
    slug = re.sub(r"[^\w-]+", "-", slug).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return slug or "index"


def derive_section(source_url: str) -> str:
    """First meaningful path segment, used as a coarse grouping for list_docs."""
    path = unquote(urlparse(source_url).path).strip("/")
    segments = [seg for seg in path.split("/") if seg and seg != "docs"]
    return segments[0] if len(segments) > 1 else ""
