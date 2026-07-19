# docs-to-mcp

Turn a documentation site into an MCP server your coding agent can search.

Crawl the docs once with [Firecrawl](https://github.com/firecrawl/firecrawl), normalize the pages into a local corpus, and serve them over [FastMCP](https://github.com/jlowin/fastmcp) — so the agent queries the *real* docs instead of recalling a version of them from training data.

## Pipeline

```
firecrawl map + scrape  ->  ingest (normalize + frontmatter)  ->  sqlite FTS5 index  ->  MCP server
      crawler.py                    ingester.py                      index.py            server.py
```

All three build steps run behind one `pipeline.refresh()` entry point, reused by the CLI (`docs-to-mcp crawl`) and the MCP `refresh_docs` tool.

## Requirements

Python 3.11+, [uv](https://docs.astral.sh/uv/), and **a Firecrawl API** — either one works:

- **Hosted** — an API key from [firecrawl.dev](https://firecrawl.dev) (has a free tier). Set `FIRECRAWL_API_URL=https://api.firecrawl.dev` and `FIRECRAWL_API_KEY=<key>`.
- **Self-hosted** — follow Firecrawl's [self-host guide](https://docs.firecrawl.dev/contributing/self-host). It defaults to `http://localhost:3002`, which is what this tool assumes when `FIRECRAWL_API_URL` is unset. Make sure the search backend (searxng) is up: without it Firecrawl can scrape a URL you hand it but cannot discover pages.

## Usage

```sh
uv sync

# Crawl a docs site into data/docs/<slug>/
uv run docs-to-mcp crawl https://opencode.ai/docs --slug opencode-docs --max-pages 20

# Serve the captured corpus over MCP (stdio)
uv run docs-to-mcp serve --slug opencode-docs

# Rebuild the search index from pages already captured — no network, no re-crawl
uv run docs-to-mcp reindex --slug opencode-docs
```

### When search says the index is stale

If a query fails with *"the index was built by an older version"*, your captured pages are fine — only the index predates a schema change. Run `reindex` (seconds, offline). **Do not re-crawl**: that would re-fetch every page and spend real Firecrawl budget to repair something purely local.

## Category grouping (MediaWiki)

For MediaWiki sites, tag captured articles with their categories so `list_docs` can filter by group (Weapons, Characters, …) and category names feed search ranking. Run after a crawl; it scrapes the Category namespace and rebuilds the index:

```sh
uv run docs-to-mcp categories --slug vampire-survivors --concurrency 6
```

Category membership is read from the Category namespace because main-content extraction strips the per-article category footer.

## Discovery: sitemap first, link map as fallback

Discovery is resolved automatically and deterministically, so you don't have to figure out a site's shape yourself:

1. an explicit `--sitemap <url>` if you pass one;
2. else a sitemap auto-resolved from the site's `robots.txt` (`Sitemap:` directive);
3. else Firecrawl's link map.

**MediaWiki is handled automatically.** MediaWiki publishes a sitemap index split by namespace (`NS_0` = content, `NS_1` = Talk, `NS_10` = Template, `NS_828` = Module); the tool keeps only `NS_0`. So a wiki just works with no extra flags:

```sh
uv run docs-to-mcp crawl https://vampire.survivors.wiki/ --slug vampire-survivors \
  --max-pages 5000 --concurrency 6
```

Firecrawl's link map on a large MediaWiki is unreliable (a namespace-mixed, non-deterministic subset dominated by Templates/Modules), which is why sitemap discovery is preferred and automatic. Use `--no-sitemap` to force the link map.

## Large and interrupted crawls

Capture is concurrent and streaming: each page is written to disk the moment it is scraped, and the `pages/*.md` files double as the resume ledger.

- `--concurrency N` — parallel page captures (default 5; raise for big sites, but a self-hosted Firecrawl has limited render workers).
- **Resume** is automatic: if a crawl is interrupted (pages written but `pages.jsonl` never finalized), re-running the same command captures only the pages still missing.
- `--incremental` — capture only URLs not already in the corpus. A cheap top-up for a large corpus after new pages are published; untouched pages keep their original `crawled_at`.

```sh
# Big wiki: more parallelism, resume-safe if it dies partway
uv run docs-to-mcp crawl https://example.com/wiki --slug example-wiki --max-pages 5000 --concurrency 10

# Later: pull in only newly published pages
uv run docs-to-mcp crawl https://example.com/wiki --slug example-wiki --max-pages 5000 --incremental
```

## Layout

```
data/docs/<slug>/
  pages/<page_id>.md   # normalized markdown with frontmatter
  pages.jsonl          # per-page metadata + source URLs
  index.sqlite         # FTS5 search index
  manifest.json        # root_url + last_crawled_at, used by refresh_docs
```

## MCP tools

The server exposes a small, discovery-friendly surface:

- `search_docs(query, limit)` — ranked page matches (BM25) with snippets.
- `get_doc(page_id)` — one captured page with metadata and full markdown.
- `list_docs(section, limit)` — list captured pages, optionally by section.
- `refresh_docs(max_pages)` — re-crawl the source and rebuild corpus + index.

## Using the server from an MCP client

Register the captured corpus as a local stdio MCP server. Example OpenCode entry (one entry per slug; the same generic server is selected via `--slug`):

```jsonc
"opencode-docs": {
  "type": "local",
  "command": ["uv", "run", "--project", "docs-to-mcp",
              "docs-to-mcp", "serve", "--slug", "opencode-docs"],
  "environment": { "FIRECRAWL_API_URL": "http://localhost:3002" },
  "enabled": true
}
```

`FIRECRAWL_API_URL` is only needed by `refresh_docs`; plain search/read work offline.

## Localized sites

By default a crawl captures the canonical (unprefixed) docs and drops localized paths like `/docs/de/...` or `/docs/pt-br/...`, detected via ISO 639-1 codes. Pass `--locale de` to capture a specific language instead. A site that is *entirely* localized falls back to keeping its localized pages rather than an empty corpus.

## Search ranking

Results are ranked by BM25 with title, heading, and category matches weighted above body text. Because FTS5 normalizes relevance by whole-document length, only the lead (~6 KB) of each page's prose is indexed — otherwise a large page's title/heading match would be drowned out by its length. Headings are indexed from the full page, so structure stays searchable; only deep prose is excluded from search. Full page text is always available via `get_doc`.

A query first requires all terms (precise); if that returns nothing for a multi-word natural-language question, it falls back to matching any term and lets BM25 rank — so a query like "best weapons for Pasqualina" still returns results.

## Known limitations

- Search matches title, headings, and the lead of each page — a term that appears only deep in a long page's prose may not surface; open the page with `get_doc`.
- Ranking is text-relevance only (no page-authority signal), so for an ambiguous one-word query several similarly-titled pages may outrank the canonical one.
- Category grouping reads MediaWiki Category pages: maintenance/tracking categories (e.g. "Pages needing…") appear alongside content ones, and a category whose page renders its members dynamically may come back empty.
- On sites larger than `--max-pages`, discovery keeps the alphabetically-first URLs; raise `--max-pages` to capture the full corpus.
- A real doc section named with a 2-letter ISO code (e.g. `/docs/is`) would be misread as a locale and dropped by default; use `--locale` to override.
- Link rewriting handles inline markdown links/images, not reference-style links.
