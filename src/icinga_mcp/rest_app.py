"""FastAPI REST application for icinga-mcp.

Author: Bernd Erk - Icinga GmbH
License: GPL-2.0-only

This module exposes REST endpoints that proxy Icinga Web 2 (Icinga DB Web module)
with normalized, summary-friendly responses and consistent error handling.
See docs/icinga-web-api.md for endpoint mapping and behavior.
"""
from __future__ import annotations

from fastapi import FastAPI, Depends, Query, HTTPException, Request, Body, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import JSONResponse
from httpx import RequestError, HTTPStatusError
from contextlib import asynccontextmanager
from .config import load_settings, Settings
from .http_client import IcingaWebClient
from .services import IcingaDBService
from .logging import setup_logging
from .models import Host, Service, HostGroup, ServiceGroup, Downtime, Comment, Notification, Event, ActionResult, CommentHostCreate, CommentServiceCreate, CommentRemoveByName, AcknowledgementHostCreate, AcknowledgementServiceCreate, DowntimeHostCreate, DowntimeServiceCreate, DowntimeRemoveByName
from typing import Any, Dict, Optional, Literal
from datetime import datetime, timedelta, timezone
import structlog

setup_logging()
log = structlog.get_logger(__name__)
# HTTP Bearer security scheme for global auth (visible in OpenAPI/Swagger)
_security_scheme = HTTPBearer(auto_error=False)

# Lightweight projection helpers to reduce payload (env-configurable defaults via Settings)
def _get_in(d: Any, path: str):
    cur = d
    for p in path.split("."):
        if isinstance(cur, dict) and p in cur:
            cur = cur[p]
        else:
            return None
    return cur

def _project_item(item: Any, fields: list[str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for f in fields:
        val = _get_in(item, f)
        if val is not None:
            out[f] = val
    return out

def _parse_fields(fields: Optional[str], default: list[str]) -> list[str]:
    if fields:
        return [f.strip() for f in fields.split(",") if f.strip()]
    return list(default)

def _project_list(items: list[Any], fields: list[str]) -> list[Dict[str, Any]]:
    return [_project_item(i, fields) for i in items]

# Timerange helpers for history endpoints
Timerange = Literal["hour", "day", "week", "month", "quarter", "year", "all"]

def _parse_event_time(value: Any) -> datetime | None:
    """
    Parse event time values from upstream entries.
    Supports:
      - ISO-8601 strings (with 'Z' or explicit offset)
      - epoch seconds (int/float or numeric string)
      - epoch milliseconds (int/float/numeric string; auto-detected)
      - returns timezone-aware UTC datetimes
    """
    if value is None:
        return None

    # Numeric epochs
    try:
        if isinstance(value, (int, float)):
            ts = float(value)
            # Heuristic: values larger than ~10^11 are likely ms since epoch
            if ts > 1e11:
                ts = ts / 1000.0
            return datetime.fromtimestamp(ts, tz=timezone.utc)
    except Exception:
        pass

    # Strings: numeric epoch or ISO-8601
    if isinstance(value, str):
        s = value.strip()

        # Try numeric epoch in string form
        try:
            num = float(s)
            if num > 1e11:
                num = num / 1000.0
            return datetime.fromtimestamp(num, tz=timezone.utc)
        except Exception:
            pass

        # Try ISO-8601 (tolerate trailing 'Z')
        try:
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None

    return None

def _timerange_since(tr: Timerange | None) -> datetime | None:
    """
    Map a logical timerange to a UTC 'since' timestamp.
    Approximations:
      - hour = 1 hour
      - month = 30 days
      - quarter = 90 days
      - year = 365 days
    """
    if not tr or tr == "all":
        return None
    now = datetime.now(timezone.utc)
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
    """Filter entries by event_time or common notification timestamps (send_time, history.event_time, etc)."""
    if since is None:
        return items
    out: list[Any] = []
    for it in items:
        ts_val = (
            _get_in(it, "event_time")
            or _get_in(it, "send_time")
            or _get_in(it, "time")
            or _get_in(it, "entry_time")
            or _get_in(it, "history.event_time")
            or _get_in(it, "notification.time")
            or _get_in(it, "notification.entry_time")
            or _get_in(it, "notification.send_time")
            or _get_in(it, "sent")
        )
        t = _parse_event_time(ts_val)
        if t is not None and t >= since:
            out.append(it)
    return out

# Extract members from group detail payload
def _extract_group_members(payload: Any, member_keys: list[str]) -> list[Any]:
    """
    Given a group detail response payload from Icinga DB Web (either dict or list),
    return the list of group members using the first matching key from member_keys.
    If the payload itself looks like a list of members, return it as-is.
    """
    # If upstream returned a list, check whether the first element wraps members
    if isinstance(payload, list):
        if not payload:
            return []
        first = payload[0]
        if isinstance(first, dict):
            for k in member_keys:
                v = first.get(k)
                if isinstance(v, list):
                    return v
        # Assume the list itself are the members
        return payload
    # If upstream returned an object, search for known member keys
    if isinstance(payload, dict):
        for k in member_keys:
            v = payload.get(k)
            if isinstance(v, list):
                return v
    return []
# Event normalization helpers for compact summaries
HOST_STATE_NAMES = {0: "UP", 1: "DOWN", 2: "UNREACHABLE"}
SERVICE_STATE_NAMES = {0: "OK", 1: "WARNING", 2: "CRITICAL", 3: "UNKNOWN"}


def _select_state_codes(item: Any, st: Any, *, use_event_type: bool = True) -> tuple[int | None, int | None]:
    """Return (current_code, previous_code) using event_type when available; fallback to state_type.
    use_event_type controls whether to consult the top-level event_type for hard/soft."""
    if not isinstance(st, dict):
        return (None, None)

    # Prefer item-level event_type to determine HARD vs SOFT events when requested
    ev_type = _get_in(item, "event_type") if use_event_type else None
    s_type: str | None = None
    if isinstance(ev_type, str):
        t = ev_type.upper()
        if "HARD" in t:
            s_type = "hard"
        elif "SOFT" in t:
            s_type = "soft"

    # Fallback to embedded state_type if event_type is not decisive
    if s_type is None:
        s_type = st.get("state_type")

    if s_type == "hard":
        return (st.get("hard_state"), st.get("previous_hard_state"))
    if s_type == "soft":
        return (st.get("soft_state"), st.get("previous_soft_state"))

    # Final fallback: prefer hard values if present, else soft
    cur = st.get("hard_state")
    prv = st.get("previous_hard_state")
    if cur is not None or prv is not None:
        return (cur, prv)
    return (st.get("soft_state"), st.get("previous_soft_state"))


def _name_for_state(code: int | None, *, scope: str) -> str | None:
    if code is None:
        return None
    return (HOST_STATE_NAMES if scope == "host" else SERVICE_STATE_NAMES).get(code)


def _derive_event_kind(item: Any) -> str:
    """Map implicit subelement presence to a coarse event type."""
    def present(*paths: str) -> bool:
        return any(_get_in(item, p) is not None for p in paths)

    if present("comment.comment_id", "comment.id"):
        return "comment"
    if present("acknowledgement.id"):
        return "acknowledgement"
    if present("downtime.downtime_id", "downtime.id"):
        return "downtime"
    if present("notification.id"):
        return "notification"
    if present("flapping.id"):
        return "flapping"
    if present("state.id"):
        return "state"
    # fall back to upstream detail if nothing matched
    t = _get_in(item, "event_type")
    return str(t) if t else "event"


def _normalize_event(item: Any, *, scope: str) -> Dict[str, Any]:
    """
    Produce a flattened summary-friendly dict for an event.
    scope = 'host' | 'service'
    """
    out: Dict[str, Any] = {}
    out["time"] = _get_in(item, "event_time")
    out["type"] = _derive_event_kind(item)
    out["host"] = _get_in(item, "host.name")
    svc_name = _get_in(item, "service.name")
    if svc_name is not None:
        out["service"] = svc_name

    ev_state = _get_in(item, "state")  # event's own state snapshot
    if scope == "host":
        code, prev = _select_state_codes(item, ev_state)
        out["state_code"] = code
        out["state_name"] = _name_for_state(code, scope="host")
        out["previous_state_code"] = prev
        out["previous_state_name"] = _name_for_state(prev, scope="host")
    else:
        # Service event: include both service and host state info
        scode, sprev = _select_state_codes(item, ev_state)
        out["service_state_code"] = scode
        out["service_state_name"] = _name_for_state(scode, scope="service")
        out["previous_state_code"] = sprev
        out["previous_state_name"] = _name_for_state(sprev, scope="service")
        host_state = _get_in(item, "host.state")
        hcode, _ = _select_state_codes(None, host_state if isinstance(host_state, dict) else {}, use_event_type=False)
        out["host_state_code"] = hcode
        out["host_state_name"] = _name_for_state(hcode, scope="host")

    out["output"] = _get_in(item, "state.output") or _get_in(item, "service.state.output") or _get_in(item, "host.state.output")

    kind = out["type"]
    if kind == "comment":
        out["author"] = _get_in(item, "comment.author")
        out["text"] = _get_in(item, "comment.comment")
    elif kind == "acknowledgement":
        out["author"] = _get_in(item, "acknowledgement.author")
        out["text"] = _get_in(item, "acknowledgement.comment")
    elif kind == "notification":
        out["author"] = _get_in(item, "notification.author")
        out["text"] = _get_in(item, "notification.text")
    elif kind == "downtime":
        out["author"] = _get_in(item, "downtime.author") or _get_in(item, "downtime.scheduled_by")
        out["text"] = _get_in(item, "downtime.comment")

    return out


def _derive_current_code(st: Any) -> int | None:
    """Pick current state code from a state dict honoring state_type."""
    if not isinstance(st, dict):
        return None
    s_type = st.get("state_type")
    if s_type == "hard":
        return st.get("hard_state")
    if s_type == "soft":
        return st.get("soft_state")
    # Fallback: prefer hard, then soft if state_type is missing
    return st.get("hard_state") if st.get("hard_state") is not None else st.get("soft_state")


def _normalize_summary_item(item: Any, *, scope: str) -> Dict[str, Any]:
    """
    Create a shallowly normalized object for list summaries:
    - state.code and state.name derived from hard/soft + state_type
    - acknowledged, in_downtime, last_check, last_state_change, output flattened at top-level
    scope: 'host' | 'service'
    """
    if not isinstance(item, dict):
        return item
    out: Dict[str, Any] = dict(item)
    st_src = item.get("state")
    st: Dict[str, Any] = dict(st_src) if isinstance(st_src, dict) else {}

    code = _derive_current_code(st)
    st["code"] = code
    st["name"] = _name_for_state(code, scope=("host" if scope == "host" else "service"))
    out["state"] = st

    # Flatten common summary fields expected by defaults
    out["acknowledged"] = st.get("is_acknowledged")
    out["in_downtime"] = st.get("in_downtime")
    out["last_check"] = st.get("last_update")
    out["last_state_change"] = st.get("last_state_change")
    out["output"] = st.get("output")
    return out


def _normalize_comment(item: Any) -> Dict[str, Any]:
    """
    Produce a compact summary for a comment ensuring we expose 'name' (composite)
    and never the internal 'id'.
    """
    if not isinstance(item, dict):
        return item
    out: Dict[str, Any] = {}
    # Time
    out["time"] = _get_in(item, "entry_time") or _get_in(item, "time")
    # Object context
    out["host"] = _get_in(item, "host.name")
    svc_name = _get_in(item, "service.name")
    if svc_name is not None:
        out["service"] = svc_name
    # Use upstream composite name instead of id
    out["name"] = _get_in(item, "name")
    # Content
    out["author"] = _get_in(item, "author") or _get_in(item, "comment.author")
    out["text"] = _get_in(item, "text") or _get_in(item, "comment") or _get_in(item, "comment.text")
    out["is_persistent"] = _get_in(item, "is_persistent")
    out["is_sticky"] = _get_in(item, "is_sticky")
    out["expire_time"] = _get_in(item, "expire_time")
    return out


def _normalize_downtime(item: Any) -> Dict[str, Any]:
    """
    Produce a compact summary for a downtime ensuring we expose 'name' (composite)
    suitable for deletion via /icingadb/downtimes/delete?downtime.name=...
    """
    if not isinstance(item, dict):
        return item
    out: Dict[str, Any] = {}
    # Prefer entry/start time for ordering
    out["time"] = (
        _get_in(item, "entry_time")
        or _get_in(item, "start_time")
        or _get_in(item, "start")
        or _get_in(item, "time")
    )
    # Object context
    out["host"] = _get_in(item, "host.name")
    svc_name = _get_in(item, "service.name")
    if svc_name is not None:
        out["service"] = svc_name
    # Composite name for deletion
    out["name"] = _get_in(item, "name")
    # Content
    out["author"] = _get_in(item, "author") or _get_in(item, "scheduled_by") or _get_in(item, "downtime.author")
    out["text"] = _get_in(item, "comment") or _get_in(item, "text") or _get_in(item, "downtime.comment")
    # Window and flags
    out["start_time"] = _get_in(item, "start_time") or _get_in(item, "start")
    out["end_time"] = _get_in(item, "end_time") or _get_in(item, "end")
    out["is_fixed"] = _get_in(item, "is_fixed")
    out["is_in_effect"] = _get_in(item, "is_in_effect") or _get_in(item, "active")
    return out


def _normalize_notification(item: Any) -> Dict[str, Any]:
    """
    Produce a compact summary for a notification suitable for LLM/UI consumption.
    Unlike comments/downtimes, notifications typically don't have a composite 'name';
    we expose the essential context and message.
    """
    if not isinstance(item, dict):
        return item
    out: Dict[str, Any] = {}
    # Time (prefer explicit send_time; also accept history.event_time and other common fields)
    out["time"] = (
        _get_in(item, "send_time")
        or _get_in(item, "entry_time")
        or _get_in(item, "time")
        or _get_in(item, "notification.time")
        or _get_in(item, "history.event_time")
    )
    # Object context
    out["host"] = _get_in(item, "host.name")
    svc_name = _get_in(item, "service.name")
    if svc_name is not None:
        out["service"] = svc_name
    # Additional attributes for summary view
    out["type"] = _get_in(item, "type") or _get_in(item, "notification.type")
    out["state"] = _get_in(item, "state") or _get_in(item, "notification.state")
    out["previous_hard_state"] = _get_in(item, "previous_hard_state") or _get_in(item, "notification.previous_hard_state")
    out["users_notified"] = _get_in(item, "users_notified") or _get_in(item, "notification.users_notified")
    # Content
    out["author"] = _get_in(item, "author") or _get_in(item, "notification.author")
    out["text"] = _get_in(item, "text") or _get_in(item, "notification.text")
    return out


def cfg(application: FastAPI | None = None) -> Settings:
    # Avoid referencing 'app' before it's defined by resolving at call time if needed
    if application is None:
        return globals()["app"].state.settings
    return application.state.settings

# Global auth dependency (Bearer only).
# Auth is ENABLED if REST_REQUIRE_API_KEY=true OR REST_BEARER_TOKEN is non-empty.
# Validation: Authorization: Bearer <token>
async def require_auth(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Security(_security_scheme),
) -> None:
    s = cfg()

    # Allow Swagger UI and schema to load without auth so "Authorize" can be used.
    path = request.url.path
    if path in ("/docs", "/openapi.json", "/redoc"):
        return

    # Load configured bearer token
    bearer_secret = None
    if getattr(s, "rest_bearer_token", None) is not None:
        try:
            bearer_secret = s.rest_bearer_token.get_secret_value().strip()
        except Exception:
            bearer_secret = None

    enabled = bool(s.rest_auth_required) or bool(bearer_secret)
    if not enabled:
        return

    if not bearer_secret:
        # Misconfiguration: auth required but no token provided
        raise HTTPException(status_code=500, detail="REST auth is required but no bearer token is configured")

    # Validate Authorization: Bearer <token>
    if credentials and isinstance(credentials, HTTPAuthorizationCredentials):
        if credentials.scheme.lower() == "bearer" and credentials.credentials == bearer_secret:
            return

    # Unauthorized
    raise HTTPException(
        status_code=401,
        detail="Invalid or missing bearer token",
        headers={"WWW-Authenticate": "Bearer"},
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_settings()
    client = IcingaWebClient(settings)
    service = IcingaDBService(client, settings)
    app.state.settings = settings
    app.state.service = service
    try:
        yield
    finally:
        await client.close()


tags_metadata = [
    {"name": "Health", "description": "Service health check"},
    {"name": "Hosts", "description": "Host listings and actions"},
    {"name": "Services", "description": "Service listings and actions"},
    {"name": "Problems", "description": "Problem-focused host and service listings"},
    {"name": "Groups", "description": "Host and service groups"},
    {"name": "Comments", "description": "List and manage comments"},
    {"name": "Acknowledgements", "description": "Manage acknowledgements"},
    {"name": "Downtimes", "description": "List and manage downtimes"},
    {"name": "Notifications", "description": "List notifications"},
    {"name": "History", "description": "Event histories for hosts and services"},
    {"name": "Search", "description": "Cross-object search for hosts, services, and groups"},
]

app = FastAPI(
    title="icinga-mcp REST",
    version="0.1.0",
    lifespan=lifespan,
    openapi_tags=tags_metadata,
    # Global auth via Bearer token configured at startup
    dependencies=[Depends(require_auth)],
)

# Exception handlers mapping upstream httpx errors to REST semantics
@app.exception_handler(HTTPStatusError)
async def _handle_status_error(request, exc: HTTPStatusError):
    resp = exc.response
    status = resp.status_code if resp is not None else 0

    # Normalize upstream redirects (e.g., login redirects or wrong endpoint mapping)
    if 300 <= status < 400:
        return JSONResponse(
            status_code=502,
            content={
                "detail": "Upstream redirect",
                "upstream_status": status,
                "location": resp.headers.get("location", "") if resp is not None else "",
                "body": resp.text if resp is not None else "",
            },
        )

    # Pass through other upstream statuses but include body for debugging
    return JSONResponse(
        status_code=status,
        content={
            "detail": "Upstream error",
            "upstream_status": status,
            "body": resp.text if resp is not None else "",
        },
    )

@app.exception_handler(RequestError)
async def _handle_request_error(request, exc: RequestError):
    return JSONResponse(
        status_code=502,
        content={
            "detail": "Upstream request failed",
            "error": str(exc),
            "error_type": exc.__class__.__name__,
        },
    )

# Catch-all to surface internal errors (helps diagnose 500s such as pagination issues)
@app.exception_handler(Exception)
async def _handle_unexpected_error(request, exc: Exception):
    try:
        path = str(request.url)
        method = getattr(request, "method", "UNKNOWN")
    except Exception:
        path = ""
        method = "UNKNOWN"
    # Log full stack trace
    log.exception("unhandled.exception", path=path, method=method)
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Internal server error",
            "error_type": exc.__class__.__name__,
            "message": str(exc),
            "path": path,
            "method": method,
        },
    )


def svc(app: FastAPI = app) -> IcingaDBService:
    return app.state.service


def qparams(
    page: Optional[int] = Query(default=None, ge=1),
    limit: Optional[int] = Query(default=None, ge=1, le=500),
    host: Optional[str] = Query(default=None, description="Equals host.name"),
    service: Optional[str] = Query(default=None, description="Equals service.name"),
):
    # Only validate pagination and expose convenience host/service.
    # Actual filtering is done via dotted params (e.g., host.name, service.name) passed through.
    return {"page": page, "limit": limit, "host": host, "service": service}


def qparams_hosts(
    page: Optional[int] = Query(default=None, ge=1),
    limit: Optional[int] = Query(default=None, ge=1, le=500),
    host: Optional[str] = Query(default=None, description="Equals host.name"),
):
    # Hosts endpoint should not expose service convenience parameter in OpenAPI
    return {"page": page, "limit": limit, "host": host}


def qparams_groups(
    page: Optional[int] = Query(default=None, ge=1),
    limit: Optional[int] = Query(default=None, ge=1, le=500),
    name: Optional[str] = Query(default=None, description="Equals group name"),
):
    # Groups endpoints expose only 'name' alongside pagination
    return {"page": page, "limit": limit, "name": name}

def qparams_basic(
    page: Optional[int] = Query(default=None, ge=1),
    limit: Optional[int] = Query(default=None, ge=1, le=500),
):
    # Basic pagination only (no convenience host/service)
    return {"page": page, "limit": limit}


@app.get(
    "/search",
    tags=["Search"],
    summary="Search hosts, services, and groups by name (case-insensitive)",
    description=(
        "Search hosts, services, hostgroups and servicegroups by name (case-insensitive). "
        "The provided name fragment is wrapped in '*' and sent to Icinga DB Web "
        "using the 'name_ci~' case-insensitive wildcard filter on each upstream endpoint."
    ),
)
async def search(
    name: str = Query(
        ...,
        min_length=1,
        description="Name fragment to search for; the server wraps this as *<name>* for upstream case-insensitive wildcard match via name_ci~",
    ),
):
    pattern = f"*{name}*"
    extra_filter: Dict[str, Any] = {"name_ci~": pattern}

    # Hosts (summary projection, same fields as /hosts)
    hosts_raw = await svc().list_hosts(page=None, limit=None, extra=extra_filter)
    host_fields = cfg().get_host_summary_fields()
    hosts_norm = [_normalize_summary_item(h, scope="host") for h in hosts_raw]
    hosts = _project_list(hosts_norm, host_fields)
    for item in hosts:
        item["type"] = "host"

    # Services (summary projection, same fields as /services)
    services_raw = await svc().list_services(page=None, limit=None, extra=extra_filter)
    service_fields = cfg().get_service_summary_fields()
    services_norm = [_normalize_summary_item(s, scope="service") for s in services_raw]
    services = _project_list(services_norm, service_fields)
    for item in services:
        item["type"] = "service"

    # Hostgroups (overview list; keep only important columns)
    hostgroups_raw = await svc().list_hostgroups(page=None, limit=None, extra=extra_filter)
    hostgroups: list[Dict[str, Any]] = []
    for g in hostgroups_raw:
        if not isinstance(g, dict):
            continue
        hostgroups.append(
            {
                "name": g.get("name"),
                "type": "hostgroup",
            }
        )

    # Servicegroups (overview list; keep only important columns)
    servicegroups_raw = await svc().list_servicegroups(page=None, limit=None, extra=extra_filter)
    servicegroups: list[Dict[str, Any]] = []
    for g in servicegroups_raw:
        if not isinstance(g, dict):
            continue
        servicegroups.append(
            {
                "name": g.get("name"),
                "type": "servicegroup",
            }
        )

    # Combined result; client distinguishes object kind via 'type'
    return hosts + services + hostgroups + servicegroups


@app.get(
    "/hosts",
    response_model=list[Host],
    tags=["Hosts"],
    summary="List hosts",
    description="List hosts. Supports host.* dotted filters. When 'summary' is true, returns a normalized projection; use 'fields' to control output fields.",
)
async def get_hosts(
    request: Request,
    params: Dict[str, Any] = Depends(qparams_hosts),
    fields: Optional[str] = Query(default=None, description="Comma-separated dotted fields to include when summary=true"),
    summary: Optional[bool] = Query(default=True, description="If true (default), return normalized summary projection; if false, return full upstream records"),
):
    # Pass through host.* dotted filters only; hosts endpoint does not support service.* filters
    extra: Dict[str, Any] = {k: v for k, v in request.query_params.items() if "." in k and not k.startswith("service.")}
    if params.get("host"):
        extra["host.name"] = params["host"]
    res = await svc().list_hosts(page=params.get("page"), limit=params.get("limit"), extra=extra)
    if summary is not False:
        f = _parse_fields(fields, cfg().get_host_summary_fields())
        norm = [_normalize_summary_item(r, scope="host") for r in res]
        res = _project_list(norm, f)
    return res


@app.get(
    "/services",
    response_model=list[Service],
    tags=["Services"],
    summary="List services",
    description="List services. Supports host.* and service.* dotted filters. When 'summary' is true, returns a normalized projection; use 'fields' to control output fields.",
)
async def get_services(
    request: Request,
    params: Dict[str, Any] = Depends(qparams),
    fields: Optional[str] = Query(default=None, description="Comma-separated dotted fields to include when summary=true"),
    summary: Optional[bool] = Query(default=True, description="If true (default), return normalized summary projection; if false, return full upstream records"),
):
    extra: Dict[str, Any] = {k: v for k, v in request.query_params.items() if "." in k}
    if params.get("host"):
        extra["host.name"] = params["host"]
    if params.get("service"):
        extra["service.name"] = params["service"]
    res = await svc().list_services(page=params.get("page"), limit=params.get("limit"), extra=extra)
    if summary is not False:
        f = _parse_fields(fields, cfg().get_service_summary_fields())
        norm = [_normalize_summary_item(r, scope="service") for r in res]
        res = _project_list(norm, f)
    return res



@app.get(
    "/problems/hosts",
    response_model=list[Host],
    tags=["Problems"],
    summary="List problem hosts",
    description="List hosts currently in a problem state (host.state.is_problem = y by default). Supports host.* dotted filters.",
)
async def get_problem_hosts(
    request: Request,
    params: Dict[str, Any] = Depends(qparams_hosts),
    fields: Optional[str] = Query(default=None, description="Comma-separated dotted fields to include when summary=true"),
    summary: Optional[bool] = Query(default=True, description="If true (default), return normalized summary projection; if false, return full upstream records"),
):
    # Forward host.* dotted filters only and enforce problem-only by default
    base_dotted: Dict[str, Any] = {
        k: v for k, v in request.query_params.items() if "." in k and not k.startswith("service.")
    }
    extra: Dict[str, Any] = dict(base_dotted)
    if params.get("host"):
        extra["host.name"] = params["host"]
    extra.setdefault("host.state.is_problem", "y")
    res = await svc().list_hosts(
        page=params.get("page"),
        limit=params.get("limit"),
        extra=extra,
    )
    if summary is not False:
        f = _parse_fields(fields, cfg().get_host_summary_fields())
        norm = [_normalize_summary_item(r, scope="host") for r in res]
        res = _project_list(norm, f)
    return res


@app.get(
    "/problems/services",
    response_model=list[Service],
    tags=["Problems"],
    summary="List problem services",
    description="List services currently in a problem state (service.state.is_problem = y by default). Supports service.* dotted filters (and host/service convenience params).",
)
async def get_problem_services(
    request: Request,
    params: Dict[str, Any] = Depends(qparams),
    fields: Optional[str] = Query(default=None, description="Comma-separated dotted fields to include when summary=true"),
    summary: Optional[bool] = Query(default=True, description="If true (default), return normalized summary projection; if false, return full upstream records"),
):
    # Forward service.* dotted filters only (plus convenience host/service) and enforce problem-only by default
    base_dotted: Dict[str, Any] = {
        k: v for k, v in request.query_params.items() if "." in k and not k.startswith("host.")
    }
    extra: Dict[str, Any] = dict(base_dotted)
    if params.get("host"):
        extra["host.name"] = params["host"]
    if params.get("service"):
        extra["service.name"] = params["service"]
    extra.setdefault("service.state.is_problem", "y")
    res = await svc().list_services(
        page=params.get("page"),
        limit=params.get("limit"),
        extra=extra,
    )
    if summary is not False:
        f = _parse_fields(fields, cfg().get_service_summary_fields())
        norm = [_normalize_summary_item(r, scope="service") for r in res]
        res = _project_list(norm, f)
    return res


@app.get(
    "/downtimes",
    response_model=list[Downtime],
    tags=["Downtimes"],
    summary="List downtimes",
    description="List downtimes. Supports dotted filters (e.g., host.* / service.*). When 'summary' is true, returns a compact projection similar to /comments; use 'fields' to control output fields.",
)
async def get_downtimes(
    request: Request,
    params: Dict[str, Any] = Depends(qparams_basic),
    host: Optional[str] = Query(default=None, description="Equals host.name"),
    service: Optional[str] = Query(default=None, description="Equals service.name"),
    fields: Optional[str] = Query(default=None, description="Comma-separated dotted fields to include when summary=true"),
    summary: Optional[bool] = Query(default=True, description="If true (default), return normalized summary projection; if false, return full upstream records"),
):
    extra: Dict[str, Any] = {k: v for k, v in request.query_params.items() if "." in k}
    if host:
        extra["host.name"] = host
    if service:
        extra["service.name"] = service

    res = await svc().list_downtimes(page=params.get("page"), limit=params.get("limit"), extra=extra)

    if summary is not False:
        f = _parse_fields(fields, cfg().get_downtime_summary_fields())
        norm = [_normalize_downtime(r) for r in res]
        res = _project_list(norm, f)
    return res


@app.get(
    "/notifications",
    response_model=list[Notification],
    tags=["Notifications"],
    summary="List notifications",
    description="List notifications. Supports dotted filters (e.g., host.* / service.*). When 'summary' is true, returns a compact projection; use 'fields' to control output fields. Supports 'timerange' to limit returned entries.",
)
async def get_notifications(
    request: Request,
    params: Dict[str, Any] = Depends(qparams_basic),
    host: Optional[str] = Query(default=None, description="Equals host.name"),
    service: Optional[str] = Query(default=None, description="Equals service.name"),
    fields: Optional[str] = Query(default=None, description="Comma-separated dotted fields to include when summary=true"),
    summary: Optional[bool] = Query(default=True, description="If true (default), return normalized summary projection; if false, return full upstream records"),
    timerange: Timerange = Query(default="all", description="Limit notifications to a time window: hour, day, week, month, quarter, year, or all"),
):
    extra: Dict[str, Any] = {k: v for k, v in request.query_params.items() if "." in k}
    if host:
        extra["host.name"] = host
    if service:
        extra["service.name"] = service

    res = await svc().list_notifications(page=params.get("page"), limit=params.get("limit"), extra=extra)

    # Apply local time-window filtering (same semantics as /history/*)
    since = _timerange_since(timerange)
    res = _filter_events_by_time(res, since)

    if summary is not False:
        f = _parse_fields(fields, cfg().get_notification_summary_fields())
        norm = [_normalize_notification(r) for r in res]
        res = _project_list(norm, f)
    return res


@app.post(
    "/downtime/remove",
    response_model=ActionResult,
    tags=["Downtimes"],
    summary="Remove a downtime",
    description="Delete a downtime by its composite 'name' as provided by GET /downtimes.",
)
async def post_remove_downtime(
    body: DowntimeRemoveByName = Body(
        ...,
        examples={
            "by_name": {
                "summary": "By 'name' from downtimes list",
                "description": "Send the exact 'name' value from GET /downtimes",
                "value": {"name": "sardine.vm.icinga.com!f09a0d5a-52f3-4361-a550-2aeecb1d0a3d"},
            }
        },
    )
):
    payload = body.model_dump(exclude_none=True)
    return await svc().remove_downtime_by_name(payload)


# Downtime window helpers (local time, formatted as YYYY-MM-DDTHH:MM:SS)
_WINDOW_DELTAS = {
    "hour": timedelta(hours=1),
    "day": timedelta(days=1),
    "week": timedelta(days=7),
}


def _start_end_from_window(window: Literal["hour", "day", "week"]) -> tuple[str, str]:
    now_local = datetime.now()  # naive local time (matches Icinga Web examples)
    end_local = now_local + _WINDOW_DELTAS[window]
    fmt = "%Y-%m-%dT%H:%M:%S"
    return now_local.strftime(fmt), end_local.strftime(fmt)


@app.post(
    "/downtime/host",
    response_model=ActionResult,
    tags=["Downtimes"],
    summary="Schedule host downtime",
    description="Schedule a downtime on a host. Provide host via query (?host= or legacy ?name=). Body contains 'comment' and a static 'window' (hour|day|week). Flags flexible/all_services/child_options are fixed upstream.",
)
async def post_downtime_host(
    request: Request,
    params: Dict[str, Any] = Depends(qparams_basic),
    host: Optional[str] = Query(default=None, description="Equals host.name"),
    body: DowntimeHostCreate = Body(
        ...,
        examples={
            "hour": {
                "summary": "1-hour host downtime",
                "value": {"comment": "nix", "window": "hour"},
            },
            "day": {
                "summary": "1-day host downtime",
                "value": {"comment": "maintenance window", "window": "day"},
            },
        },
    ),
):
    # Reject any service parameters for host downtime
    if "service.name" in request.query_params or "service" in request.query_params:
        raise HTTPException(status_code=422, detail="Host downtime does not accept any service parameters")

    # Resolve host name either from upstream-style ?name= or convenience ?host=
    name = request.query_params.get("name") or host
    if not name:
        raise HTTPException(status_code=422, detail="Host downtime requires 'name' (host) or convenience 'host'")

    start, end = _start_end_from_window(body.window)
    return await svc().schedule_downtime_host(name=name, comment=body.comment, start=start, end=end)


@app.post(
    "/downtime/service",
    response_model=ActionResult,
    tags=["Downtimes"],
    summary="Schedule service downtime",
    description="Schedule a downtime on a service. Requires service and host via query (?service=&host=) or legacy (?name=&host.name=). Body contains 'comment' and a static 'window' (hour|day|week). Flags flexible/child_options are fixed upstream.",
)
async def post_downtime_service(
    request: Request,
    params: Dict[str, Any] = Depends(qparams_basic),
    service: Optional[str] = Query(default=None, description="Equals service.name"),
    host: Optional[str] = Query(default=None, description="Equals host.name"),
    body: DowntimeServiceCreate = Body(
        ...,
        examples={
            "hour": {
                "summary": "1-hour service downtime",
                "value": {"comment": "hurra", "window": "hour"},
            },
            "week": {
                "summary": "1-week service downtime",
                "value": {"comment": "long maintenance", "window": "week"},
            },
        },
    ),
):
    # Resolve required upstream-style arguments: name=<service> and host.name=<host>
    extra: Dict[str, Any] = {}
    if "name" in request.query_params:
        extra["name"] = request.query_params["name"]
    elif service:
        extra["name"] = service

    if "host.name" in request.query_params:
        extra["host.name"] = request.query_params["host.name"]
    elif host:
        extra["host.name"] = host

    if "name" not in extra or "host.name" not in extra:
        raise HTTPException(
            status_code=422,
            detail="Service downtime requires both 'name' (service) and 'host.name' (host); convenience 'service' and 'host' are also accepted",
        )

    start, end = _start_end_from_window(body.window)
    return await svc().schedule_downtime_service(
        name=extra["name"],
        host_name=extra["host.name"],
        comment=body.comment,
        start=start,
        end=end,
    )


@app.get(
    "/comments",
    response_model=list[Comment],
    tags=["Comments"],
    summary="List comments",
    description="List comments with optional scoping by host and service. When 'summary' is true, returns a compact summary; use 'fields' to control output fields.",
)
async def get_comments(
    request: Request,
    params: Dict[str, Any] = Depends(qparams_basic),
    host: Optional[str] = Query(default=None, description="Equals host.name"),
    service: Optional[str] = Query(default=None, description="Equals service.name"),
    fields: Optional[str] = Query(default=None, description="Comma-separated dotted fields to include when summary=true"),
    summary: Optional[bool] = Query(default=True, description="If true (default), return normalized summary projection; if false, return full upstream records"),
):
    # Accept upstream-style dotted parameters only for scoping:
    # - No params: list all comments
    # - host.name: list host comments
    # - host.name + service.name: list service comments
    extra: Dict[str, Any] = {k: v for k, v in request.query_params.items() if "." in k}
    if host:
        extra["host.name"] = host
    if service:
        extra["service.name"] = service

    res = await svc().list_comments(
        page=params.get("page"),
        limit=params.get("limit"),
        extra=extra,
    )

    if summary is not False:
        # Project a safe summary that includes 'name' and omits internal 'id'
        f = _parse_fields(fields, cfg().get_comment_summary_fields())
        norm = [_normalize_comment(r) for r in res]
        res = _project_list(norm, f)

    return res


@app.post(
    "/comment/remove",
    response_model=ActionResult,
    tags=["Comments"],
    summary="Remove a comment",
    description="Delete a comment by its composite 'name' as provided by GET /comments.",
)
async def post_remove_comment(
    body: CommentRemoveByName = Body(
        ...,
        examples={
            "by_name": {
                "summary": "By 'name' from comments list",
                "description": "Send the exact 'name' value from GET /comments",
                "value": {"name": "answer.vm.icinga.com!f1cd84f1-583e-493f-b42b-86b9e1c6d325"},
            }
        },
    )
):
    payload = body.model_dump(exclude_none=True)
    return await svc().remove_comment(payload)



@app.post(
    "/comment/host",
    response_model=ActionResult,
    tags=["Comments"],
    summary="Create host comment",
    description="Create a comment on a host. Provide host via query (?host= or legacy ?name=). Body contains only 'comment'.",
)
async def post_comment_host(
    request: Request,
    params: Dict[str, Any] = Depends(qparams_basic),
    host: Optional[str] = Query(default=None, description="Equals host.name"),
    body: CommentHostCreate = Body(
        ...,
        examples={
            "basic": {
                "summary": "Create host comment",
                "value": {"comment": "Planned maintenance"}
            }
        },
    ),
):
    # Reject any service parameters
    if "service.name" in request.query_params or "service" in request.query_params:
        raise HTTPException(status_code=422, detail="Host comment does not accept any service parameters")

    # Resolve host name either from upstream-style ?name= or convenience ?host=
    name = request.query_params.get("name") or host
    if not name:
        raise HTTPException(status_code=422, detail="Host comment requires 'name' (host) or convenience 'host'")

    # Upstream target: /icingadb/host/add-comment?name=<host>
    return await svc().add_host_comment(name=name, comment=body.comment)


@app.post(
    "/comment/service",
    response_model=ActionResult,
    tags=["Comments"],
    summary="Create service comment",
    description="Create a comment on a service. Requires service and host via query (?service=&host=) or legacy (?name=&host.name=). Body contains only 'comment'.",
)
async def post_comment_service(
    request: Request,
    params: Dict[str, Any] = Depends(qparams_basic),
    service: Optional[str] = Query(default=None, description="Equals service.name"),
    host: Optional[str] = Query(default=None, description="Equals host.name"),
    body: CommentServiceCreate = Body(
        ...,
        examples={
            "basic": {
                "summary": "Create service comment",
                "value": {"comment": "Noted by on-call"},
            }
        },
    ),
):
    # Resolve required upstream-style arguments: name=<service> and host.name=<host>
    extra: Dict[str, Any] = {}
    # Accept explicit query params or convenience mapping (?service=&host=)
    if "name" in request.query_params:
        extra["name"] = request.query_params["name"]
    elif service:
        extra["name"] = service

    if "host.name" in request.query_params:
        extra["host.name"] = request.query_params["host.name"]
    elif host:
        extra["host.name"] = host

    if "name" not in extra or "host.name" not in extra:
        raise HTTPException(
            status_code=422,
            detail="Service comment requires both 'name' (service) and 'host.name' (host); convenience 'service' and 'host' are also accepted",
        )

    # Upstream target: /icingadb/service/add-comment?name=<service>&host.name=<host>
    return await svc().add_service_comment(
        name=extra["name"],
        host_name=extra["host.name"],
        comment=body.comment,
    )

@app.post(
    "/acknowledgement/host",
    response_model=ActionResult,
    tags=["Acknowledgements"],
    summary="Acknowledge host",
    description="Acknowledge a host problem. Provide host via query (?host= or legacy ?name=). Body contains only 'comment'; other flags are fixed upstream.",
)
async def post_acknowledgement_host(
    request: Request,
    params: Dict[str, Any] = Depends(qparams_basic),
    host: Optional[str] = Query(default=None, description="Equals host.name"),
    body: AcknowledgementHostCreate = Body(
        ...,
        examples={
            "basic": {
                "summary": "Acknowledge host problem",
                "value": {"comment": "ich bin dran"}
            }
        },
    ),
):
    # Reject any service parameters
    if "service.name" in request.query_params or "service" in request.query_params:
        raise HTTPException(status_code=422, detail="Host acknowledgement does not accept any service parameters")

    # Resolve host name either from upstream-style ?name= or convenience ?host=
    name = request.query_params.get("name") or host
    if not name:
        raise HTTPException(status_code=422, detail="Host acknowledgement requires 'name' (host) or convenience 'host'")

    # Upstream target: /icingadb/host/acknowledge?name=<host>
    return await svc().acknowledge_host(name=name, comment=body.comment)


@app.post(
    "/acknowledgement/service",
    response_model=ActionResult,
    tags=["Acknowledgements"],
    summary="Acknowledge service",
    description="Acknowledge a service problem. Requires service and host via query (?service=&host=) or legacy (?name=&host.name=). Body contains only 'comment'; other flags are fixed upstream.",
)
async def post_acknowledgement_service(
    request: Request,
    params: Dict[str, Any] = Depends(qparams_basic),
    service: Optional[str] = Query(default=None, description="Equals service.name"),
    host: Optional[str] = Query(default=None, description="Equals host.name"),
    body: AcknowledgementServiceCreate = Body(
        ...,
        examples={
            "basic": {
                "summary": "Acknowledge service problem",
                "value": {"comment": "ich bin dran"}
            }
        },
    ),
):
    # Resolve required upstream-style arguments: name=<service> and host.name=<host>
    extra: Dict[str, Any] = {}
    # Accept explicit query params or convenience mapping (?service=&host=)
    if "name" in request.query_params:
        extra["name"] = request.query_params["name"]
    elif service:
        extra["name"] = service

    if "host.name" in request.query_params:
        extra["host.name"] = request.query_params["host.name"]
    elif host:
        extra["host.name"] = host

    if "name" not in extra or "host.name" not in extra:
        raise HTTPException(
            status_code=422,
            detail="Service acknowledgement requires both 'name' (service) and 'host.name' (host); convenience 'service' and 'host' are also accepted",
        )

    # Upstream target: /icingadb/service/acknowledge?name=<service>&host.name=<host>
    return await svc().acknowledge_service(
        name=extra["name"],
        host_name=extra["host.name"],
        comment=body.comment,
    )

@app.post(
    "/acknowledgement/host/remove",
    response_model=ActionResult,
    tags=["Acknowledgements"],
    summary="Remove host acknowledgement",
    description="Remove an acknowledgement from a host. Provide host via query (?host= or legacy ?name=).",
)
async def post_remove_acknowledgement_host(
    request: Request,
    params: Dict[str, Any] = Depends(qparams_basic),
    host: Optional[str] = Query(default=None, description="Equals host.name"),
):
    # Reject any service parameters
    if "service.name" in request.query_params or "service" in request.query_params:
        raise HTTPException(status_code=422, detail="Host acknowledgement removal does not accept any service parameters")

    # Resolve host name either from upstream-style ?name= or convenience ?host=
    name = request.query_params.get("name") or host
    if not name:
        raise HTTPException(status_code=422, detail="Host acknowledgement removal requires 'name' (host) or convenience 'host'")

    # Upstream target: /icingadb/host/remove-acknowledgement?name=<host>
    return await svc().remove_acknowledgement_host(name=name)


@app.post(
    "/acknowledgement/service/remove",
    response_model=ActionResult,
    tags=["Acknowledgements"],
    summary="Remove service acknowledgement",
    description="Remove an acknowledgement from a service. Requires service and host via query (?service=&host=) or legacy (?name=&host.name=).",
)
async def post_remove_acknowledgement_service(
    request: Request,
    params: Dict[str, Any] = Depends(qparams_basic),
    service: Optional[str] = Query(default=None, description="Equals service.name"),
    host: Optional[str] = Query(default=None, description="Equals host.name"),
):
    # Resolve required upstream-style arguments: name=<service> and host.name=<host>
    extra: Dict[str, Any] = {}
    # Accept explicit query params or convenience mapping (?service=&host=)
    if "name" in request.query_params:
        extra["name"] = request.query_params["name"]
    elif service:
        extra["name"] = service

    if "host.name" in request.query_params:
        extra["host.name"] = request.query_params["host.name"]
    elif host:
        extra["host.name"] = host

    if "name" not in extra or "host.name" not in extra:
        raise HTTPException(
            status_code=422,
            detail="Service acknowledgement removal requires both 'name' (service) and 'host.name' (host); convenience 'service' and 'host' are also accepted",
        )

    # Upstream target: /icingadb/service/remove-acknowledgement?name=<service>&host.name=<host>
    return await svc().remove_acknowledgement_service(
        name=extra["name"],
        host_name=extra["host.name"],
    )


@app.post(
    "/checknow/host",
    response_model=ActionResult,
    tags=["Hosts"],
    summary="Trigger immediate host check",
    description="Trigger an immediate check for a host. Provide host via query (?host= or legacy ?name=).",
)
async def post_check_now_host(
    request: Request,
    params: Dict[str, Any] = Depends(qparams_basic),
    host: Optional[str] = Query(default=None, description="Equals host.name"),
):
    # Reject any service parameters
    if "service.name" in request.query_params or "service" in request.query_params:
        raise HTTPException(status_code=422, detail="Host check-now does not accept any service parameters")

    # Resolve host name either from upstream-style ?name= or convenience ?host=
    name = request.query_params.get("name") or host
    if not name:
        raise HTTPException(status_code=422, detail="Host check-now requires 'name' (host) or convenience 'host'")

    # Upstream target: /icingadb/host/check-now?name=<host>
    return await svc().check_now_host(name=name)


@app.post(
    "/checknow/service",
    response_model=ActionResult,
    tags=["Services"],
    summary="Trigger immediate service check",
    description="Trigger an immediate check for a service. Requires service and host via query (?service=&host=) or legacy (?name=&host.name=).",
)
async def post_check_now_service(
    request: Request,
    params: Dict[str, Any] = Depends(qparams_basic),
    service: Optional[str] = Query(default=None, description="Equals service.name"),
    host: Optional[str] = Query(default=None, description="Equals host.name"),
):
    # Resolve required upstream-style arguments: name=<service> and host.name=<host>
    extra: Dict[str, Any] = {}
    
    # Accept explicit query params or convenience mapping (?service=&host=)
    if "name" in request.query_params:
        extra["name"] = request.query_params["name"]
    elif service:
        extra["name"] = service

    if "host.name" in request.query_params:
        extra["host.name"] = request.query_params["host.name"]
    elif host:
        extra["host.name"] = host

    if "name" not in extra or "host.name" not in extra:
        raise HTTPException(
            status_code=422,
            detail="Service check-now requires both 'name' (service) and 'host.name' (host); convenience 'service' and 'host' are also accepted",
        )

    # Upstream target: /icingadb/service/check-now?name=<service>&host.name=<host>
    return await svc().check_now_service(
        name=extra["name"],
        host_name=extra["host.name"],
    )



@app.get(
    "/history/host",
    response_model=list[Event],
    tags=["History"],
    summary="Host history",
    description="History for a host. Requires host via query (?host= or legacy ?name=).",
)
async def get_host_history(
    request: Request,
    params: Dict[str, Any] = Depends(qparams_basic),
    host: Optional[str] = Query(default=None, description="Equals host.name"),
    fields: Optional[str] = Query(default=None, description="Comma-separated dotted fields to include when summary=true"),
    summary: Optional[bool] = Query(default=True, description="If true (default), return normalized event summary; if false, return full upstream records"),
    timerange: Timerange = Query(default="all", description="Limit events to a time window: hour, day, week, month, quarter, year, or all"),
):
    # Accept upstream-style ?name=...; also support convenience ?host=... mapped to name
    # Explicitly reject any service-related parameters for this endpoint
    if "service.name" in request.query_params:
        raise HTTPException(status_code=422, detail="Host history does not accept 'service.name' parameter")
    if "service" in request.query_params:
        raise HTTPException(status_code=422, detail="Host history does not accept 'service' parameter")

    extra: Dict[str, Any] = {}
    if "name" in request.query_params:
        extra["name"] = request.query_params["name"]
    elif host:
        extra["name"] = host
    else:
        raise HTTPException(status_code=422, detail="Host history requires 'name' (host) or convenience 'host'")

    res = await svc().list_host_history(page=params.get("page"), limit=params.get("limit"), extra=extra)

    # Apply local time-window filtering (upstream typically returns full history)
    since = _timerange_since(timerange)
    res = _filter_events_by_time(res, since)

    if summary is not False:
        f = _parse_fields(fields, cfg().get_host_event_summary_fields())
        flat = [_normalize_event(r, scope="host") for r in res]
        res = _project_list(flat, f)
    return res

@app.get(
    "/history/service",
    response_model=list[Event],
    tags=["History"],
    summary="Service history",
    description="History for a service. Requires both service and host via query (?service=&host=) or legacy (?name=&host.name=).",
)
async def get_service_history(
    request: Request,
    params: Dict[str, Any] = Depends(qparams_basic),
    service: Optional[str] = Query(default=None, description="Equals service.name"),
    host: Optional[str] = Query(default=None, description="Equals host.name"),
    fields: Optional[str] = Query(default=None, description="Comma-separated dotted fields to include when summary=true"),
    summary: Optional[bool] = Query(default=True, description="If true (default), return normalized event summary; if false, return full upstream records"),
    timerange: Timerange = Query(default="all", description="Limit events to a time window: hour, day, week, month, quarter, year, or all"),
):
    # Only allow 'name' (service) and 'host.name' (host); both are required
    extra: Dict[str, Any] = {}
    # Accept direct upstream-style params or convenience mapping
    if "name" in request.query_params:
        extra["name"] = request.query_params["name"]
    elif service:
        extra["name"] = service
    if "host.name" in request.query_params:
        extra["host.name"] = request.query_params["host.name"]
    elif host:
        extra["host.name"] = host
    # Enforce required upstream parameters
    if "name" not in extra or "host.name" not in extra:
        raise HTTPException(status_code=422, detail="Service history requires both 'name' (service) and 'host.name' (host)")

    res = await svc().list_service_history(page=params.get("page"), limit=params.get("limit"), extra=extra)

    # Apply local time-window filtering (upstream typically returns full history)
    since = _timerange_since(timerange)
    res = _filter_events_by_time(res, since)

    if summary is not False:
        f = _parse_fields(fields, cfg().get_service_event_summary_fields())
        flat = [_normalize_event(r, scope="service") for r in res]
        res = _project_list(flat, f)
    return res


@app.get(
    "/hostgroups",
    response_model=list[HostGroup],
    tags=["Groups"],
    summary="List host groups or members of a group",
    description="List host groups. When name is provided, returns members of that group (formatted like /hosts).",
)
async def get_hostgroups(
    request: Request,
    params: Dict[str, Any] = Depends(qparams_groups),
    fields: Optional[str] = Query(default=None, description="Comma-separated dotted fields to include when summary=true (applies when listing members)"),
    summary: Optional[bool] = Query(default=True, description="If true (default), return normalized summary for members; if false, return full upstream records"),
):
    # When 'name' is provided, upstream uses the singular detail endpoint.
    # In that case, present the members as a list of hosts just like /hosts, with the same summary projection.
    extra: Dict[str, Any] = {}
    name = params.get("name")
    if name:
        extra["name"] = name

    res = await svc().list_hostgroups(page=params.get("page"), limit=params.get("limit"), extra=extra)

    if name:
        # Extract members from group detail and format like /hosts
        members = _extract_group_members(res, ["hosts", "members"])
        if summary is not False:
            f = _parse_fields(fields, cfg().get_host_summary_fields())
            norm = [_normalize_summary_item(r, scope="host") for r in members]
            return _project_list(norm, f)
        return members

    # Overview (no name): pass-through upstream overview list
    return res


@app.get(
    "/servicegroups",
    response_model=list[ServiceGroup],
    tags=["Groups"],
    summary="List service groups or members of a group",
    description="List service groups. When name is provided, returns members of that group (formatted like /services).",
)
async def get_servicegroups(
    request: Request,
    params: Dict[str, Any] = Depends(qparams_groups),
    fields: Optional[str] = Query(default=None, description="Comma-separated dotted fields to include when summary=true (applies when listing members)"),
    summary: Optional[bool] = Query(default=True, description="If true (default), return normalized summary for members; if false, return full upstream records"),
):
    # When 'name' is provided, upstream uses the singular detail endpoint.
    # In that case, present the members as a list of services just like /services, with the same summary projection.
    extra: Dict[str, Any] = {}
    name = params.get("name")
    if name:
        extra["name"] = name

    res = await svc().list_servicegroups(page=params.get("page"), limit=params.get("limit"), extra=extra)

    if name:
        # Extract members from group detail and format like /services
        members = _extract_group_members(res, ["services", "members"])
        if summary is not False:
            f = _parse_fields(fields, cfg().get_service_summary_fields())
            norm = [_normalize_summary_item(r, scope="service") for r in members]
            return _project_list(norm, f)
        return members

    # Overview (no name): pass-through upstream overview list
    return res


@app.get(
    "/health",
    response_model=dict[str, str],
    tags=["Health"],
    summary="Health check",
    description="Simple health check endpoint.",
)
async def health() -> dict[str, str]:
    return {"status": "ok"}
