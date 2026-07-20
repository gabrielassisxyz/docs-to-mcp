# Roadmap

What exists, what is missing, and what is deliberately out of scope. Derived from the current code and README — see `README.md` for usage and the full list of known limitations.

## Exists today

- **Capture pipeline**: Firecrawl-based crawl → normalization with frontmatter → SQLite FTS5 index, behind one `pipeline.refresh()` entry point shared by the CLI and the MCP `refresh_docs` tool.
- **CLI**: `crawl`, `categories`, `reindex`, `serve` — with concurrency, automatic resume of interrupted crawls, and `--incremental` top-ups.
- **Discovery**: sitemap-first (explicit flag → robots.txt → Firecrawl link map), with MediaWiki namespace handling built in.
- **MCP server** (stdio, FastMCP): `search_docs`, `get_doc`, `list_docs`, `refresh_docs`. Search and reads work fully offline.
- **Search**: BM25 with title/heading/category boosts, lead-only prose indexing to keep long pages from drowning their own matches, and an any-term fallback for natural-language queries.
- **MediaWiki extras**: category grouping for filtering and ranking; locale filtering with ISO 639-1 detection.
- Offline test suite; local CI gate (`bin/ci`) and versioned git hooks.

## Missing / natural next steps

These address the limitations the README already documents:

- **Full-text depth**: terms appearing only deep in a long page's prose are not indexed; only lead + headings are searchable.
- **Ranking signal**: relevance is text-only — no page-authority signal, so ambiguous one-word queries can surface similarly-titled pages above the canonical one.
- **Category noise**: MediaWiki maintenance/tracking categories are not distinguished from content categories, and dynamically-rendered category pages can come back empty.
- **Link rewriting**: reference-style Markdown links are not rewritten, only inline links and images.
- **Locale detection**: a real doc section named with a 2-letter ISO code is misread as a locale; only a manual `--locale` flag overrides it.
- **CI service**: `bin/ci` runs locally; a hosted CI workflow running the same script would gate PRs automatically.

## Deliberately out of scope

- **Crawling engines other than Firecrawl** — hosted or self-hosted Firecrawl is the one capture backend.
- **Committed corpora** — `data/` is regenerable output, per-user, and stays out of git.
- **A hosted or multi-user server** — the MCP server is a local stdio process serving one machine's captured corpora.
- **Semantic / embedding search** — ranking is deliberately plain FTS5 BM25 with field boosts.
