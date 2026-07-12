"""YAML frontmatter round-trip for captured page files.

We lean on PyYAML rather than hand-rolling key: value parsing because page titles
can contain colons, quotes, and unicode that need proper escaping to survive a
round trip.
"""

from __future__ import annotations

import yaml

_FENCE = "---"


def dump(meta: dict[str, str], body: str) -> str:
    """Render ``meta`` as a YAML frontmatter block above ``body``."""
    front = yaml.safe_dump(
        meta, sort_keys=False, allow_unicode=True, default_flow_style=False
    ).strip()
    return f"{_FENCE}\n{front}\n{_FENCE}\n\n{body.rstrip()}\n"


def parse(text: str) -> tuple[dict[str, str], str]:
    """Split frontmatter from body. Returns ({}, text) when no frontmatter."""
    if not text.startswith(_FENCE + "\n"):
        return {}, text
    rest = text[len(_FENCE) + 1:]
    close = rest.find("\n" + _FENCE)
    if close == -1:
        return {}, text
    meta = yaml.safe_load(rest[:close]) or {}
    after = rest[close + 1 + len(_FENCE):]
    newline = after.find("\n")
    body = after[newline + 1:] if newline != -1 else ""
    return meta, body
