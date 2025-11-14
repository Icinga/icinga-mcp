from __future__ import annotations
from pydantic import BaseModel, Field, ConfigDict
from typing import Any, Dict, List, Literal


class AnyRecord(BaseModel):
    model_config = ConfigDict(extra="allow")


class Host(AnyRecord): ...
class Service(AnyRecord): ...
class Downtime(AnyRecord): ...
class Acknowledgement(AnyRecord): ...
class Comment(AnyRecord): ...
class Notification(AnyRecord): ...
class Event(AnyRecord): ...
class HostGroup(AnyRecord): ...
class ServiceGroup(AnyRecord): ...


class ActionResult(BaseModel):
    ok: bool = True
    upstream_status: int | None = None
    detail: Dict[str, Any] | None = None
 
 
class CommentRemoveByName(BaseModel):
    """
    Minimal request body for POST /comment/remove.
    Provide the exact 'name' value from GET /comments; do not provide the ID.
    """
    name: str = Field(..., description="Exact 'name' value from GET /comments")
 
class DowntimeRemoveByName(BaseModel):
    """
    Minimal request body for POST /downtime/remove.
    Provide the exact 'name' value from GET /downtime; do not provide the ID.
    """
    name: str = Field(..., description="Exact 'name' value from GET /downtimes")
 
class CommentHostCreate(BaseModel):
    """
    Request body for POST /comment/host.
    Host name is provided via query parameter (?name=<host> or convenience ?host=<host>).
    Body contains only 'comment'. Expiration is not configurable via REST; backend sends expire='n'.
    """
    comment: str = Field(..., min_length=1, description="Non-empty comment text")


class CommentServiceCreate(BaseModel):
    """
    Request body for POST /comment/service.
    Body contains only 'comment'. Expiration is not configurable via REST; backend sends expire='n'.
    """
    comment: str = Field(..., min_length=1, description="Non-empty comment text")


class AcknowledgementHostCreate(BaseModel):
    """
    Request body for POST /acknowledgement/host.
    Host name must be provided via query (?name=<host> or convenience ?host=<host>).
    Only 'comment' is accepted. All other acknowledgement flags are fixed: persistent='n', notify='y', sticky='n', expire='n'.
    """
    comment: str = Field(..., min_length=1, description="Non-empty acknowledgement comment")


class AcknowledgementServiceCreate(BaseModel):
    """
    Request body for POST /acknowledgement/service.
    Service and host must be provided via query (?name=<service>&host.name=<host> or convenience ?service=&host=).
    Only 'comment' is accepted. All other acknowledgement flags are fixed: persistent='n', notify='y', sticky='n', expire='n'.
    """
    comment: str = Field(..., min_length=1, description="Non-empty acknowledgement comment")


class DowntimeHostCreate(BaseModel):
    """
    Request body for POST /downtime/host.
    Client supplies only 'comment' and a static 'window' selecting the duration.
    The server computes start and end timestamps and sends fixed upstream flags.
    """
    comment: str = Field(..., min_length=1, description="Non-empty downtime reason")
    window: Literal["hour", "day", "week"] = Field("hour", description="Duration window to schedule")


class DowntimeServiceCreate(BaseModel):
    """
    Request body for POST /downtime/service.
    Client supplies only 'comment' and a static 'window' selecting the duration.
    Requires service and host via query (?service=&host=) or legacy (?name=&host.name=).
    """
    comment: str = Field(..., min_length=1, description="Non-empty downtime reason")
    window: Literal["hour", "day", "week"] = Field("hour", description="Duration window to schedule")
