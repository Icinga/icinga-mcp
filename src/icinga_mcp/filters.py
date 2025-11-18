from __future__ import annotations

from typing import Any

ALLOWED_QUERY_KEYS = {"page", "limit", "name", "name~", "name_ci~", "filter"}


def sanitize_query(params: dict[str, Any]) -> dict[str, str]:
    """Normalize query for Icinga DB Web:
    - Allow page, limit, name (and 'filter' when used by MCP tools) as-is (stringified).
    - Pass through any dotted filters like 'host.name', 'service.name', etc.
    - REST routes should avoid the 'filter' param and use dotted keys instead; MCP tools may set 'filter'.
    """
    out: dict[str, str] = {}
    for k, v in params.items():
        if v is None:
            continue
        if k in ALLOWED_QUERY_KEYS or "." in k:
            out[k] = str(v)
    return out
