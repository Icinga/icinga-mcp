from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from mcp.server import Server

# Minimal MCP stdio server using mcp library
from mcp.server.stdio import stdio_server
from mcp.types import TextContent

from .config import load_settings
from .http_client import IcingaWebClient
from .logging import setup_logging
from .services import IcingaDBService

# Lightweight projection helpers to reduce payload sent to the LLM
HOST_SUMMARY_FIELDS = [
    "name",
    "state.code",
    "state.name",
    "state.is_problem",
    "last_check",
    "last_state_change",
    "acknowledged",
    "in_downtime",
    "output",
]
SERVICE_SUMMARY_FIELDS = [
    "host.name",
    "name",
    "state.code",
    "state.name",
    "state.is_problem",
    "last_check",
    "last_state_change",
    "acknowledged",
    "in_downtime",
    "output",
]
EVENT_SUMMARY_FIELDS = [
    "time",
    "type",
    "state.code",
    "state.name",
    "author",
    "text",
    "output",
]


def _get_in(d: Any, path: str):
    cur = d
    for p in path.split("."):
        if isinstance(cur, dict) and p in cur:
            cur = cur[p]
        else:
            return None
    return cur


def _project_item(item: Any, fields: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for f in fields:
        val = _get_in(item, f)
        if val is not None:
            out[f] = val
    return out


def _parse_fields(fields: str | None, default: list[str]) -> list[str]:
    if fields:
        return [f.strip() for f in fields.split(",") if f.strip()]
    return list(default)


def _project_list(items: list[Any], fields: list[str]) -> list[dict[str, Any]]:
    return [_project_item(i, fields) for i in items]


# Timerange helpers for history tools (LLM payload reduction)
Timerange = Literal["hour", "day", "week", "month", "quarter", "year", "all"]


def _parse_event_time(value: Any) -> datetime | None:
    """
    Parse event time values from upstream history entries.
    Supports ISO-8601 strings (with 'Z' or explicit offset) and epoch seconds.
    Returns timezone-aware UTC datetimes or None when parsing fails.
    """
    if value is None:
        return None
    try:
        if isinstance(value, int | float):
            return datetime.fromtimestamp(float(value), tz=UTC)
        if isinstance(value, str):
            s = value.strip()
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt.astimezone(UTC)
    except Exception:
        return None
    return None


def _timerange_since(tr: Timerange | None) -> datetime | None:
    """
    Map a timerange to a UTC 'since' timestamp.
    Approximations:
      - hour = 1 hour
      - month = 30 days, quarter = 90 days, year = 365 days
    """
    if not tr or tr == "all":
        return None
    now = datetime.now(UTC)
    if tr == "hour":
        delta = timedelta(hours=1)
    elif tr == "day":
        delta = timedelta(days=1)
    elif tr == "week":
        delta = timedelta(days=7)
    elif tr == "month":
        delta = timedelta(days=30)
    elif tr == "quarter":
        delta = timedelta(days=90)
    elif tr == "year":
        delta = timedelta(days=365)
    else:
        return None
    return now - delta


def _filter_events_by_time(items: list[Any], since: datetime | None) -> list[Any]:
    """Filter history entries by event_time (or fallback 'time') according to 'since'."""
    if since is None:
        return items
    out: list[Any] = []
    for it in items:
        t = _parse_event_time(_get_in(it, "event_time") or _get_in(it, "time"))
        if t is not None and t >= since:
            out.append(it)
    return out


async def run_stdio() -> None:
    setup_logging()
    settings = load_settings()
    client = IcingaWebClient(settings)
    service = IcingaDBService(client, settings)

    server = Server("icinga-mcp")

    async def _json(result: Any) -> list[TextContent]:
        return [TextContent(type="text", text=json.dumps(result))]

    @server.tool()
    async def list_hosts(
        filter_expr: str | None = None,
        page: int | None = None,
        limit: int | None = None,
        fields: str | None = None,
        summary: bool | None = True,
    ):
        extra = {"filter": filter_expr} if filter_expr else None
        res = await service.list_hosts(page=page, limit=limit, extra=extra)
        if summary is not False:
            f = _parse_fields(fields, settings.get_host_summary_fields())
            projected = _project_list(res, f)
            return await _json(projected)
        return await _json(res)

    @server.tool()
    async def list_services(
        filter_expr: str | None = None,
        page: int | None = None,
        limit: int | None = None,
        fields: str | None = None,
        summary: bool | None = True,
    ):
        extra = {"filter": filter_expr} if filter_expr else None
        res = await service.list_services(page=page, limit=limit, extra=extra)
        if summary is not False:
            f = _parse_fields(fields, settings.get_service_summary_fields())
            projected = _project_list(res, f)
            return await _json(projected)
        return await _json(res)

    @server.tool()
    async def list_downtimes(
        filter_expr: str | None = None, page: int | None = None, limit: int | None = None
    ):
        extra = {"filter": filter_expr} if filter_expr else None
        res = await service.list_downtimes(page=page, limit=limit, extra=extra)
        return await _json(res)

    @server.tool()
    async def list_comments(
        filter_expr: str | None = None, page: int | None = None, limit: int | None = None
    ):
        extra = {"filter": filter_expr} if filter_expr else None
        res = await service.list_comments(page=page, limit=limit, extra=extra)
        return await _json(res)

    @server.tool()
    async def list_host_history(
        name: str,
        filter_expr: str | None = None,
        page: int | None = None,
        limit: int | None = None,
        fields: str | None = None,
        summary: bool | None = True,
        timerange: Timerange = "all",
    ):
        extra: dict[str, Any] = {"name": name}
        if filter_expr:
            extra["filter"] = filter_expr
        res = await service.list_host_history(page=page, limit=limit, extra=extra)

        # Apply local time-window filtering for LLM clients
        since = _timerange_since(timerange)
        res = _filter_events_by_time(res, since)

        if summary is not False:
            f = _parse_fields(fields, settings.get_event_summary_fields())
            projected = _project_list(res, f)
            return await _json(projected)
        return await _json(res)

    @server.tool()
    async def list_service_history(
        name: str,
        host: str,
        filter_expr: str | None = None,
        page: int | None = None,
        limit: int | None = None,
        fields: str | None = None,
        summary: bool | None = True,
        timerange: Timerange = "all",
    ):
        extra: dict[str, Any] = {"name": name, "host.name": host}
        if filter_expr:
            extra["filter"] = filter_expr
        res = await service.list_service_history(page=page, limit=limit, extra=extra)

        # Apply local time-window filtering for LLM clients
        since = _timerange_since(timerange)
        res = _filter_events_by_time(res, since)

        if summary is not False:
            f = _parse_fields(fields, settings.get_event_summary_fields())
            projected = _project_list(res, f)
            return await _json(projected)
        return await _json(res)

    @server.tool()
    async def list_host_problems(
        host: str | None = None,
        page: int | None = None,
        limit: int | None = None,
        fields: str | None = None,
        summary: bool | None = True,
    ):
        """
        List hosts with problems only.
        - Convenience host maps to host.name.
        - summary=true by default returns a compact projection; set summary=false for full objects.
        - fields allows overriding the summary projection with comma-separated dotted paths.
        """
        extra: dict[str, Any] = {}
        if host:
            extra["host.name"] = host
        extra.setdefault("host.state.is_problem", "y")
        res = await service.list_hosts(page=page, limit=limit, extra=extra)
        if summary is not False:
            f = _parse_fields(fields, settings.get_host_summary_fields())
            projected = _project_list(res, f)
            return await _json(projected)
        return await _json(res)

    @server.tool()
    async def list_service_problems(
        host: str | None = None,
        service_name: str | None = None,
        page: int | None = None,
        limit: int | None = None,
        fields: str | None = None,
        summary: bool | None = True,
    ):
        """
        List services with problems only.
        - Convenience host maps to host.name; service maps to service.name.
        - summary=true by default returns a compact projection; set summary=false for full objects.
        - fields allows overriding the summary projection with comma-separated dotted paths.
        """
        extra: dict[str, Any] = {}
        if host:
            extra["host.name"] = host
        if service_name:
            extra["service.name"] = service_name
        extra.setdefault("service.state.is_problem", "y")
        res = await service.list_services(page=page, limit=limit, extra=extra)
        if summary is not False:
            f = _parse_fields(fields, settings.get_service_summary_fields())
            projected = _project_list(res, f)
            return await _json(projected)
        return await _json(res)

    @server.tool()
    async def get_host_detail(
        name: str,
        fields: str | None = None,
        summary: bool | None = False,
    ):
        """
        Fetch a single host by exact name. Returns full object by default.
        Use summary=true or fields="a,b,c" to reduce payload.
        """
        extra: dict[str, Any] = {"name": name}
        res = await service.list_hosts(limit=1, extra=extra)
        item: Any = res[0] if res else {}
        if summary:
            f = _parse_fields(fields, settings.get_host_summary_fields())
            item = _project_item(item, f)
        return await _json(item)

    @server.tool()
    async def get_service_detail(
        name: str,
        host: str,
        fields: str | None = None,
        summary: bool | None = False,
    ):
        """
        Fetch a single service by exact service name and host.
        Returns full object by default. Use summary=true or fields="a,b,c" to reduce payload.
        """
        extra: dict[str, Any] = {"host.name": host, "service.name": name}
        res = await service.list_services(limit=1, extra=extra)
        item: Any = res[0] if res else {}
        if summary:
            f = _parse_fields(fields, settings.get_service_summary_fields())
            item = _project_item(item, f)
        return await _json(item)

    @server.tool()
    async def list_hostgroups(
        name: str | None = None, page: int | None = None, limit: int | None = None
    ):
        """
        List hostgroups. Only 'name' filter is supported for exact group name.
        """
        extra: dict[str, Any] = {}
        if name:
            extra["name"] = name
        res = await service.list_hostgroups(page=page, limit=limit, extra=extra or None)
        return await _json(res)

    @server.tool()
    async def list_servicegroups(
        name: str | None = None, page: int | None = None, limit: int | None = None
    ):
        """
        List servicegroups. Only 'name' filter is supported for exact group name.
        """
        extra: dict[str, Any] = {}
        if name:
            extra["name"] = name
        res = await service.list_servicegroups(page=page, limit=limit, extra=extra or None)
        return await _json(res)

    # Actions
    @server.tool()
    async def acknowledge(payload_json: str):
        """
        Deprecated placeholder tool.

        Generic acknowledgement via MCP is not currently implemented in the
        service layer. Use the REST API endpoints instead:
        - /acknowledgement/host
        - /acknowledgement/service
        """
        raise NotImplementedError(
            "Generic 'acknowledge' MCP tool is deprecated; use REST /acknowledgement/{host,service} instead."
        )

    @server.tool()
    async def unacknowledge(payload_json: str):
        """
        Deprecated placeholder tool.

        Generic unacknowledgement via MCP is not currently implemented in the
        service layer. Use the REST API endpoints instead:
        - /acknowledgement/host/remove
        - /acknowledgement/service/remove
        """
        raise NotImplementedError(
            "Generic 'unacknowledge' MCP tool is deprecated; use REST /acknowledgement/{host,service}/remove instead."
        )

    @server.tool()
    async def schedule_downtime(payload_json: str):
        """
        Deprecated placeholder tool.

        Generic downtime scheduling via MCP is not currently implemented in the
        service layer. Use the REST API endpoints instead:
        - /downtime/host
        - /downtime/service
        """
        raise NotImplementedError(
            "Generic 'schedule_downtime' MCP tool is deprecated; use REST /downtime/{host,service} instead."
        )

    @server.tool()
    async def remove_downtime(payload_json: str):
        payload = json.loads(payload_json)
        res = await service.remove_downtime(payload)
        return await _json(res.model_dump())

    @server.tool()
    async def reschedule_check(payload_json: str):
        """
        Deprecated placeholder tool.

        Generic reschedule-check via MCP is not currently implemented in the
        service layer. Use the REST API endpoints instead:
        - /checknow/host
        - /checknow/service
        """
        raise NotImplementedError(
            "Generic 'reschedule_check' MCP tool is deprecated; use REST /checknow/{host,service} instead."
        )

    # Comments
    @server.tool()
    async def add_host_comment(name: str, comment: str, expire: str):
        """
        Create a host comment.
        - name: exact host name (maps to upstream ?name=<host>)
        - comment: text
        - expire: 'y' or 'n'
        """
        res = await service.add_host_comment(name=name, comment=comment, expire=expire.lower())
        return await _json(res.model_dump())

    @server.tool()
    async def add_service_comment(name: str, host: str, comment: str, expire: str):
        """
        Create a service comment.
        - name: exact service name (maps to upstream ?name=<service>)
        - host: exact host name (maps to upstream &host.name=<host>)
        - comment: text
        - expire: 'y' or 'n'
        """
        res = await service.add_service_comment(
            name=name, host_name=host, comment=comment, expire=expire.lower()
        )
        return await _json(res.model_dump())

    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream)
