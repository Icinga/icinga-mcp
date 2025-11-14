"""Service layer for interacting with Icinga DB Web endpoints.

Author: Bernd Erk - Icinga GmbH
License: GPL-2.0-only

This module contains the IcingaDBService which encapsulates upstream HTTP calls
and exposes high-level methods consumed by the REST app and the MCP server.
All methods either return pass-through JSON data (lists) or ActionResult wrappers for actions.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple
from .http_client import IcingaWebClient
from .config import Settings
from .filters import sanitize_query
from .models import Host, Service, Downtime, Acknowledgement, Comment, Event, HostGroup, ServiceGroup, ActionResult, Notification
import structlog

log = structlog.get_logger(__name__)


class IcingaDBService:
    def __init__(self, client: IcingaWebClient, settings: Settings):
        self.c = client
        self.s = settings

    # Listings
    async def list_hosts(self, *, page: int | None = None, limit: int | None = None, extra: Dict[str, Any] | None = None) -> List[Host]:
        base = {"page": page, "limit": limit}
        params = sanitize_query({**base, **(extra or {})})
        data = await self.c.get_json(self.s.endpoint("hosts"), params=params)
        # Accept passthrough shape
        return data

    async def list_services(self, *, page: int | None = None, limit: int | None = None, extra: Dict[str, Any] | None = None) -> List[Service]:
        base = {"page": page, "limit": limit}
        params = sanitize_query({**base, **(extra or {})})
        data = await self.c.get_json(self.s.endpoint("services"), params=params)
        return data

    async def list_downtimes(self, *, page: int | None = None, limit: int | None = None, extra: Dict[str, Any] | None = None) -> List[Downtime]:
        base = {"page": page, "limit": limit}
        params = sanitize_query({**base, **(extra or {})})
        data = await self.c.get_json(self.s.endpoint("downtimes"), params=params)
        return data

    async def list_notifications(self, *, page: int | None = None, limit: int | None = None, extra: Dict[str, Any] | None = None) -> List[Notification]:
        base = {"page": page, "limit": limit}
        params = sanitize_query({**base, **(extra or {})})
        data = await self.c.get_json(self.s.endpoint("notifications"), params=params)
        return data

    async def list_comments(self, *, page: int | None = None, limit: int | None = None, extra: Dict[str, Any] | None = None) -> List[Comment]:
        base = {"page": page, "limit": limit}
        params = sanitize_query({**base, **(extra or {})})
        data = await self.c.get_json(self.s.endpoint("comments"), params=params)
        return data

    async def list_hostgroups(self, *, page: int | None = None, limit: int | None = None, extra: Dict[str, Any] | None = None) -> List[HostGroup]:
        base = {"page": page, "limit": limit}
        extra = extra or {}
        use_detail = "name" in extra
        params = sanitize_query({**base, **extra})
        endpoint_key = "hostgroup" if use_detail else "hostgroups"
        data = await self.c.get_json(self.s.endpoint(endpoint_key), params=params)
        return data

    async def list_servicegroups(self, *, page: int | None = None, limit: int | None = None, extra: Dict[str, Any] | None = None) -> List[ServiceGroup]:
        base = {"page": page, "limit": limit}
        extra = extra or {}
        use_detail = "name" in extra
        params = sanitize_query({**base, **extra})
        endpoint_key = "servicegroup" if use_detail else "servicegroups"
        data = await self.c.get_json(self.s.endpoint(endpoint_key), params=params)
        return data

    async def list_host_history(self, *, page: int | None = None, limit: int | None = None, extra: Dict[str, Any] | None = None) -> List[Event]:
        base = {"page": page, "limit": limit}
        params = sanitize_query({**base, **(extra or {})})
        data = await self.c.get_json(self.s.endpoint("host_history"), params=params)
        return data

    async def list_service_history(self, *, page: int | None = None, limit: int | None = None, extra: Dict[str, Any] | None = None) -> List[Event]:
        base = {"page": page, "limit": limit}
        params = sanitize_query({**base, **(extra or {})})
        data = await self.c.get_json(self.s.endpoint("service_history"), params=params)
        return data

    async def add_host_comment(self, *, name: str, comment: str, expire: str = "n") -> ActionResult:
        params = {"name": name}
        form_fields = {"comment": comment, "expire": expire}
        resp = await self.c.request("POST", self.s.endpoint("host_add_comment"), params=params, form_fields=form_fields)
        resp.raise_for_status()
        detail = resp.json() if resp.content else {}
        return ActionResult(ok=True, upstream_status=resp.status_code, detail=detail)

    async def add_service_comment(self, *, name: str, host_name: str, comment: str, expire: str = "n") -> ActionResult:
        params = {"name": name, "host.name": host_name}
        form_fields = {"comment": comment, "expire": expire}
        resp = await self.c.request("POST", self.s.endpoint("service_add_comment"), params=params, form_fields=form_fields)
        resp.raise_for_status()
        detail = resp.json() if resp.content else {}
        return ActionResult(ok=True, upstream_status=resp.status_code, detail=detail)
 
    async def acknowledge_host(self, *, name: str, comment: str) -> ActionResult:
        params = {"name": name}
        form_fields = {
            "comment": comment,
            "persistent": "n",
            "notify": "y",
            "sticky": "n",
            "expire": "n",
        }
        resp = await self.c.request("POST", self.s.endpoint("host_acknowledge"), params=params, form_fields=form_fields)
        resp.raise_for_status()
        detail = resp.json() if resp.content else {}
        return ActionResult(ok=True, upstream_status=resp.status_code, detail=detail)
 
    async def acknowledge_service(self, *, name: str, host_name: str, comment: str) -> ActionResult:
        params = {"name": name, "host.name": host_name}
        form_fields = {
            "comment": comment,
            "persistent": "n",
            "notify": "y",
            "sticky": "n",
            "expire": "n",
        }
        resp = await self.c.request("POST", self.s.endpoint("service_acknowledge"), params=params, form_fields=form_fields)
        resp.raise_for_status()
        detail = resp.json() if resp.content else {}
        return ActionResult(ok=True, upstream_status=resp.status_code, detail=detail)

    async def remove_acknowledgement_host(self, *, name: str) -> ActionResult:
        params = {"name": name}
        resp = await self.c.request("POST", self.s.endpoint("host_remove_acknowledgement"), params=params)
        resp.raise_for_status()
        detail = resp.json() if resp.content else {}
        return ActionResult(ok=True, upstream_status=resp.status_code, detail=detail)

    async def remove_acknowledgement_service(self, *, name: str, host_name: str) -> ActionResult:
        params = {"name": name, "host.name": host_name}
        resp = await self.c.request("POST", self.s.endpoint("service_remove_acknowledgement"), params=params)
        resp.raise_for_status()
        detail = resp.json() if resp.content else {}
        return ActionResult(ok=True, upstream_status=resp.status_code, detail=detail)
 
    async def remove_comment(self, payload: Dict[str, Any]) -> ActionResult:
        # Prefer the 'name' field from the comments list; accept legacy keys too
        comment_name = payload.get("name") or payload.get("comment_name") or payload.get("comment.name")
        if not comment_name:
            host = payload.get("host") or payload.get("host.name")
            service = payload.get("service") or payload.get("service.name")
            comment_id = payload.get("id") or payload.get("comment_id") or payload.get("commentId")
            if not host or not comment_id:
                raise ValueError("remove_comment requires 'name' (from list) or 'comment.name'; alternatively provide 'host' and 'id' (optional 'service').")
            comment_name = f"{host}!{service}!{comment_id}" if service else f"{host}!{comment_id}"
 
        params = sanitize_query({"comment.name": comment_name})
        resp = await self.c.request("POST", self.s.endpoint("comments_delete"), params=params)
        resp.raise_for_status()
        detail = resp.json() if resp.content else {}
        return ActionResult(ok=True, upstream_status=resp.status_code, detail=detail)
    
    async def remove_downtime(self, payload: Dict[str, Any]) -> ActionResult:
        downtime_name = payload.get("name") or payload.get("downtime_name") or payload.get("downtime.name")
        if not downtime_name:
            host = payload.get("host") or payload.get("host.name")
            service = payload.get("service") or payload.get("service.name")
            downtime_id = payload.get("id") or payload.get("downtime_id") or payload.get("downtimeId")
            if not host or not downtime_id:
                raise ValueError("remove_downtime requires 'name' (from list) or 'downtime.name'; alternatively provide 'host' and 'id' (optional 'service').")
            downtime_name = f"{host}!{service}!{downtime_id}" if service else f"{host}!{downtime_id}"
 
        params = sanitize_query({"downtime.name": downtime_name})
        resp = await self.c.request("POST", self.s.endpoint("downtimes_delete"), params=params)
        resp.raise_for_status()
        detail = resp.json() if resp.content else {}
        return ActionResult(ok=True, upstream_status=resp.status_code, detail=detail)

    async def remove_downtime(self, payload: Dict[str, Any]) -> ActionResult:
        data = await self.c.post_json(self.s.endpoint("action_remove_downtime"), payload=payload)
        return ActionResult(ok=True, detail=data)

    async def check_now_host(self, *, name: str) -> ActionResult:
        params = {"name": name}
        resp = await self.c.request("POST", self.s.endpoint("host_check_now"), params=params)
        resp.raise_for_status()
        detail = resp.json() if resp.content else {}
        return ActionResult(ok=True, upstream_status=resp.status_code, detail=detail)

    async def check_now_service(self, *, name: str, host_name: str) -> ActionResult:
        params = {"name": name, "host.name": host_name}
        resp = await self.c.request("POST", self.s.endpoint("service_check_now"), params=params)
        resp.raise_for_status()
        detail = resp.json() if resp.content else {}
        return ActionResult(ok=True, upstream_status=resp.status_code, detail=detail)


    async def schedule_downtime_host(self, *, name: str, comment: str, start: str, end: str) -> ActionResult:
        params = {"name": name}
        form_fields = {
            "comment": comment,
            "start": start,
            "end": end,
            "flexible": "n",
            "all_services": "n",
            "child_options": "0",
        }
        resp = await self.c.request("POST", self.s.endpoint("host_schedule_downtime"), params=params, form_fields=form_fields)
        resp.raise_for_status()
        detail = resp.json() if resp.content else {}
        return ActionResult(ok=True, upstream_status=resp.status_code, detail=detail)
    
    
    async def schedule_downtime_service(self, *, name: str, host_name: str, comment: str, start: str, end: str) -> ActionResult:
        params = {"name": name, "host.name": host_name}
        form_fields = {
            "comment": comment,
            "start": start,
            "end": end,
            "flexible": "n",
            "child_options": "0",
        }
        resp = await self.c.request("POST", self.s.endpoint("service_schedule_downtime"), params=params, form_fields=form_fields)
        resp.raise_for_status()
        detail = resp.json() if resp.content else {}
        return ActionResult(ok=True, upstream_status=resp.status_code, detail=detail)
