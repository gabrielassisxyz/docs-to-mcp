"""Command-line entry point: crawl a docs site or serve a captured corpus."""

from __future__ import annotations

import argparse
import sys

from . import paths
from .crawler import CrawlError
from .index import IndexError_, build_index
from .pipeline import DEFAULT_CONCURRENCY, enrich_categories, refresh
from .sitemap import SitemapError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="docs-to-mcp", description=__doc__)
    parser.add_argument("--data-root", default=str(paths.DEFAULT_DATA_ROOT),
                        help="Base directory for captured corpora (default: data/docs).")
    sub = parser.add_subparsers(dest="command", required=True)

    crawl = sub.add_parser("crawl", help="Crawl a docs site into a local corpus + index.")
    crawl.add_argument("root_url", help="Documentation root URL, e.g. https://opencode.ai/docs")
    crawl.add_argument("--slug", required=True, help="Corpus slug, e.g. opencode-docs")
    crawl.add_argument("--max-pages", type=int, default=50, help="Max documentation pages to capture.")
    crawl.add_argument("--locale", default=None,
                       help="Keep only this locale's pages (e.g. de). Default: canonical/unprefixed docs.")
    crawl.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY,
                       help=f"Parallel page captures (default: {DEFAULT_CONCURRENCY}).")
    crawl.add_argument("--incremental", action="store_true",
                       help="Capture only pages not already in the corpus (cheap top-up of a big crawl).")
    crawl.add_argument("--sitemap", default=None,
                       help="Discover URLs from this explicit sitemap URL. By default a sitemap is "
                            "auto-resolved from robots.txt (MediaWiki indexes are narrowed to NS_0).")
    crawl.add_argument("--no-sitemap", action="store_true",
                       help="Skip sitemap discovery; use Firecrawl's link map.")

    cats = sub.add_parser("categories",
                          help="Tag captured articles with categories (MediaWiki Category namespace).")
    cats.add_argument("--slug", required=True, help="Corpus slug to enrich.")
    cats.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY,
                      help=f"Parallel category-page captures (default: {DEFAULT_CONCURRENCY}).")

    # Rebuilding the index is local work over pages already on disk — seconds, no network.
    # It had no command, so the only visible way out of a stale index was to re-crawl the
    # whole site: thousands of pages and a lot of someone's Firecrawl budget, to fix
    # something that never needed the network at all.
    reindex = sub.add_parser("reindex",
                             help="Rebuild the search index from the captured pages. No re-crawl.")
    reindex.add_argument("--slug", required=True, help="Corpus slug to reindex.")

    serve = sub.add_parser("serve", help="Serve a captured corpus over MCP (stdio).")
    serve.add_argument("--slug", required=True, help="Corpus slug to serve.")

    args = parser.parse_args(argv)

    if args.command == "crawl":
        return _run_crawl(args)
    if args.command == "categories":
        return _run_categories(args)
    if args.command == "reindex":
        return _run_reindex(args)
    if args.command == "serve":
        return _run_serve(args)
    return 2


def _run_reindex(args: argparse.Namespace) -> int:
    try:
        rows = build_index(args.slug, args.data_root)
    except IndexError_ as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"reindexed {args.slug}: {rows} pages")
    return 0


def _run_crawl(args: argparse.Namespace) -> int:
    def progress(done: int, total: int) -> None:
        if done == total or done % 25 == 0:
            print(f"  captured {done}/{total}", file=sys.stderr)

    try:
        result = refresh(args.root_url, args.slug, max_pages=args.max_pages,
                         locale=args.locale, sitemap_url=args.sitemap,
                         auto_sitemap=not args.no_sitemap,
                         incremental=args.incremental, concurrency=args.concurrency,
                         data_root=args.data_root, on_progress=progress)
    except SitemapError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except CrawlError as exc:
        print(f"error: {exc}", file=sys.stderr)
        print("hint: is the Firecrawl API running? check FIRECRAWL_API_URL "
              "(default http://localhost:3002).", file=sys.stderr)
        return 1

    print(f"crawled {args.slug} [{result.mode}] via {result.discovery}: "
          f"discovered {result.discovered}, captured {len(result.written)}, "
          f"skipped {result.skipped}, removed {len(result.removed)}, "
          f"corpus {result.page_count}, indexed {result.indexed}")
    for url, error in result.failed:
        print(f"  failed {url}: {error}", file=sys.stderr)
    print(f"corpus: {paths.corpus_dir(args.slug, args.data_root)}")
    return 0


def _run_categories(args: argparse.Namespace) -> int:
    def progress(done: int, total: int) -> None:
        if done == total or done % 25 == 0:
            print(f"  categories {done}/{total}", file=sys.stderr)

    try:
        result = enrich_categories(args.slug, concurrency=args.concurrency,
                                   data_root=args.data_root, on_progress=progress)
    except (CrawlError, SitemapError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"tagged {args.slug}: {result.tagged_pages} pages across {result.categories} categories "
          f"(from {result.category_pages} category pages, {result.failed} failed)")
    if result.failed:
        print(f"  {result.failed} category pages failed to scrape; re-run to recover them "
              f"(lower --concurrency if antibot failures persist).", file=sys.stderr)
    return 0


def _run_serve(args: argparse.Namespace) -> int:
    from .server import build_server

    build_server(args.slug, data_root=args.data_root).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
