from __future__ import annotations

DEFAULT_TOOLS = [
    "Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebFetch", "WebSearch",
]


def resolve_tools(
    allow: list[str] | None = None,
    deny: list[str] | None = None,
) -> list[str]:
    """Resolve final tool list from defaults + allow/deny overrides."""
    tools = set(DEFAULT_TOOLS)
    if allow:
        tools.update(allow)
    if deny:
        tools -= set(deny)
    return sorted(tools)
