import pytest

from icinga_mcp.config import Settings
from icinga_mcp.services import IcingaDBService


class DummyResponse:
    def __init__(
        self, status_code: int = 200, json_body: dict | None = None, has_content: bool = True
    ):
        self.status_code = status_code
        self._json_body = json_body or {}
        self.content = b"x" if has_content else b""

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise AssertionError(f"unexpected error status {self.status_code}")

    def json(self) -> dict:
        return self._json_body


class DummyClientList:
    """Dummy client for list_* style methods that call get_json()."""

    def __init__(self, payload):
        self.payload = payload
        self.calls: list[tuple[str, dict | None]] = []

    async def get_json(self, path: str, *, params: dict | None = None):
        self.calls.append((path, params or {}))
        return self.payload


class DummyClientActions:
    """Dummy client for action-style methods that call request()."""

    def __init__(self, response: DummyResponse | None = None):
        self.calls: list[tuple[str, str, dict | None, dict | None]] = []
        self.response = response or DummyResponse()

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        form_fields: dict | None = None,
    ):
        self.calls.append((method, path, params or {}, form_fields))
        return self.response


@pytest.mark.asyncio
async def test_list_hosts_uses_endpoint_and_sanitizes_params():
    settings = Settings()
    client = DummyClientList(payload=[{"name": "host1"}, {"name": "host2"}])
    service = IcingaDBService(client, settings)

    result = await service.list_hosts(page=2, limit=50, extra={"name~": "*vm*", "ignored": "x"})

    assert result == [{"name": "host1"}, {"name": "host2"}]

    assert len(client.calls) == 1
    path, params = client.calls[0]
    assert path == settings.endpoint("hosts")
    # sanitize_query stringifies pagination parameters
    assert params["page"] == "2"
    assert params["limit"] == "50"
    # operator filter is preserved
    assert params["name~"] == "*vm*"
    # unknown keys are dropped by sanitize_query
    assert "ignored" not in params


@pytest.mark.asyncio
async def test_list_hostgroups_uses_overview_endpoint_without_name():
    settings = Settings()
    client = DummyClientList(payload=[{"name": "linux"}])
    service = IcingaDBService(client, settings)

    await service.list_hostgroups(extra={"search": "linux"})

    assert len(client.calls) == 1
    path, params = client.calls[0]
    assert path == settings.endpoint("hostgroups")
    # unknown non-dotted filters like "search" are dropped by sanitize_query
    assert "search" not in params


@pytest.mark.asyncio
async def test_list_hostgroups_uses_detail_endpoint_with_name_filter():
    settings = Settings()
    client = DummyClientList(payload=[{"name": "linux"}])
    service = IcingaDBService(client, settings)

    await service.list_hostgroups(extra={"name": "linux"})

    assert len(client.calls) == 1
    path, params = client.calls[0]
    assert path == settings.endpoint("hostgroup")
    assert params["name"] == "linux"


@pytest.mark.asyncio
async def test_add_host_comment_sends_expected_request_and_wraps_action_result():
    settings = Settings()
    client = DummyClientActions(
        DummyResponse(status_code=201, json_body={"ok": True, "message": "created"})
    )
    service = IcingaDBService(client, settings)

    result = await service.add_host_comment(name="web01", comment="maintenance", expire="y")

    assert result.ok is True
    assert result.upstream_status == 201
    assert result.detail == {"ok": True, "message": "created"}

    assert len(client.calls) == 1
    method, path, params, form_fields = client.calls[0]
    assert method == "POST"
    assert path == settings.endpoint("host_add_comment")
    assert params == {"name": "web01"}
    assert form_fields == {"comment": "maintenance", "expire": "y"}


@pytest.mark.asyncio
async def test_acknowledge_service_sets_fixed_flags_and_wraps_response():
    settings = Settings()
    client = DummyClientActions(
        DummyResponse(status_code=200, json_body={"status": "acknowledged"})
    )
    service = IcingaDBService(client, settings)

    result = await service.acknowledge_service(
        name="PING",
        host_name="web01",
        comment="ich bin dran",
    )

    assert result.ok is True
    assert result.upstream_status == 200
    assert result.detail == {"status": "acknowledged"}

    assert len(client.calls) == 1
    method, path, params, form_fields = client.calls[0]
    assert method == "POST"
    assert path == settings.endpoint("service_acknowledge")
    assert params == {"name": "PING", "host.name": "web01"}
    assert form_fields == {
        "comment": "ich bin dran",
        "persistent": "n",
        "notify": "y",
        "sticky": "n",
        "expire": "n",
    }


@pytest.mark.asyncio
async def test_remove_comment_uses_name_directly_when_provided():
    settings = Settings()
    client = DummyClientActions(DummyResponse(status_code=200, json_body={"deleted": 1}))
    service = IcingaDBService(client, settings)

    result = await service.remove_comment({"name": "web01!PING!42"})

    assert result.ok is True
    assert result.upstream_status == 200
    assert result.detail == {"deleted": 1}

    assert len(client.calls) == 1
    method, path, params, form_fields = client.calls[0]
    assert method == "POST"
    assert path == settings.endpoint("comments_delete")
    assert params["comment.name"] == "web01!PING!42"
    assert form_fields is None


@pytest.mark.asyncio
async def test_remove_comment_builds_composite_name_from_host_and_id():
    settings = Settings()
    client = DummyClientActions(DummyResponse(status_code=200, json_body={"deleted": 1}))
    service = IcingaDBService(client, settings)

    await service.remove_comment({"host": "web01", "id": 7})

    assert len(client.calls) == 1
    _, _, params, _ = client.calls[0]
    # Without service, composite name is host!id
    assert params["comment.name"] == "web01!7"


@pytest.mark.asyncio
async def test_remove_comment_requires_minimal_identity_fields():
    settings = Settings()
    client = DummyClientActions()
    service = IcingaDBService(client, settings)

    with pytest.raises(ValueError):
        await service.remove_comment({"id": 1})  # missing host and name


@pytest.mark.asyncio
async def test_remove_downtime_uses_name_directly_when_provided():
    settings = Settings()
    client = DummyClientActions(DummyResponse(status_code=200, json_body={"deleted": 1}))
    service = IcingaDBService(client, settings)

    result = await service.remove_downtime({"name": "web01!PING!abc-uuid"})

    assert result.ok is True
    assert result.upstream_status == 200
    assert result.detail == {"deleted": 1}

    assert len(client.calls) == 1
    method, path, params, form_fields = client.calls[0]
    assert method == "POST"
    assert path == settings.endpoint("downtimes_delete")
    assert params["downtime.name"] == "web01!PING!abc-uuid"
    assert form_fields is None


@pytest.mark.asyncio
async def test_remove_downtime_builds_composite_name_from_host_and_id():
    settings = Settings()
    client = DummyClientActions(DummyResponse(status_code=200, json_body={"deleted": 1}))
    service = IcingaDBService(client, settings)

    await service.remove_downtime({"host": "db01", "service": "Disk", "id": "deadbeef"})

    assert len(client.calls) == 1
    _, _, params, _ = client.calls[0]
    # With service, composite name is host!service!id
    assert params["downtime.name"] == "db01!Disk!deadbeef"


@pytest.mark.asyncio
async def test_remove_downtime_requires_minimal_identity_fields():
    settings = Settings()
    client = DummyClientActions()
    service = IcingaDBService(client, settings)

    with pytest.raises(ValueError):
        await service.remove_downtime({"id": "x"})  # missing host and name
