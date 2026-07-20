# AGENTS.md — read this first

docs-to-mcp turns a documentation site into a searchable local MCP server: Firecrawl captures the pages, an ingester normalizes them into a Markdown corpus with frontmatter, an SQLite FTS5 index makes them searchable, and a FastMCP stdio server exposes `search_docs` / `get_doc` / `list_docs` / `refresh_docs` to a coding agent. The point is that the agent answers from the *real* docs of the version in use, not from whatever version it memorized in training. `README.md` is the user-facing reference — commands, discovery rules, ranking rationale, known limitations; this file is only what an agent needs to work *on* the repo.

## Build / run / test

Python 3.11+, managed with [uv](https://docs.astral.sh/uv/). There is no build step beyond dependency sync.

```sh
uv sync                                    # install deps (incl. dev group: pytest)
uv run pytest                              # full test suite — offline, fast, no Firecrawl needed
uv run docs-to-mcp --help                  # CLI: crawl | categories | reindex | serve
bin/ci                                     # the exact pre-PR gate: gitleaks + shellcheck + pytest
```

- Tests are hermetic: they exercise ingestion, indexing, sitemap parsing, the pipeline, and a server smoke test against fixtures. A test that needs a live Firecrawl or network access does not belong in the suite.
- Only `crawl`, `categories`, and `refresh_docs` need a Firecrawl API (`FIRECRAWL_API_URL`, optionally `FIRECRAWL_API_KEY`); `serve`, `reindex`, and search work entirely offline against `data/docs/<slug>/`.
- `data/` is git-ignored regenerable output, never source. Do not commit captured corpora.

## Conventions

- Default branch: **`master`**. Never commit to it directly — branch, then PR.
- **Work in your own worktree, never in the main tree.** Before your first write, run `bin/worktree new <type>/<kebab-desc>` and do everything in the directory it prints. It branches off a fresh `origin/master` and keeps the worktree outside the repo. The main tree stays on `master` as a clean reference. Remove the worktree with `bin/worktree rm <task>` once the branch is merged.
- **After clone, run `bin/install-hooks` once.** It points git at `.githooks/`: a gitleaks pre-commit secret scan and a commit-msg gate that rejects assistant attribution.
- Commits follow [Conventional Commits](https://www.conventionalcommits.org) (`<type>(scope): <description>`); branches follow `<type>/<kebab-description>`. One logical change per commit.
- **No assistant attribution in commits or PRs** — no `Co-Authored-By:` naming an assistant, no "Generated with", no robot emoji. The commit-msg hook enforces it; do not bypass it.
- **`bin/ci` green before opening a PR.** It is the whole gate: "green locally" is the same claim as "green in CI".
- PR bodies explain **what + why**: a short summary of the change, then the decisions and trade-offs behind it. The diff already shows the what.
- Comments explain intent (why), not mechanics (what). Keep existing comments — they carry the reasoning behind non-obvious choices (chrome stripping, lead-only indexing, sitemap-first discovery).

## Common hurdles (append as discovered)

| hurdle | class | where the gate is |
|---|---|---|
