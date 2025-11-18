import httpx
import pytest

from icinga_mcp.config import Settings
from icinga_mcp.http_client import IcingaWebClient


@pytest.mark.asyncio
async def test_client_init_close():
    s = Settings(base_url="https://example.com/icingaweb2", username="u", password="p")  # type: ignore
    c = IcingaWebClient(s)
    await c.close()


class DummyAsyncClientEncode:
    """Dummy client to capture built requests and return a simple JSON response."""

    def __init__(self, *args, **kwargs):
        self.requests: list[httpx.Request] = []

    def build_request(self, method: str, url: httpx.URL, **kwargs) -> httpx.Request:
        return httpx.Request(method, url, **kwargs)

    async def send(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        return httpx.Response(200, request=req, json={"ok": True})

    async def aclose(self) -> None:
        # nothing to clean up in tests
        return None


@pytest.mark.asyncio
async def test_request_encodes_operator_filters_and_spaces(monkeypatch):
    # Patch AsyncClient constructor to our dummy that records requests.
    monkeypatch.setattr("icinga_mcp.http_client.httpx.AsyncClient", DummyAsyncClientEncode)

    s = Settings(
        base_url="https://example.com/icingaweb2",
        username="u",
        password="p",  # type: ignore
    )
    client = IcingaWebClient(s)

    resp = await client.request(
        "GET",
        "icingadb/hosts",
        params={"name~": "*vm host*", "page": 1},
    )

    assert resp.json() == {"ok": True}
    assert isinstance(client._client, DummyAsyncClientEncode)  # type: ignore[attr-defined]

    dummy: DummyAsyncClientEncode = client._client  # type: ignore[assignment,attr-defined]
    assert len(dummy.requests) == 1

    req = dummy.requests[0]
    url_str = str(req.url)
    # Operator filter has no '=' and spaces encoded as %20.
    assert "name~*vm%20host*" in url_str
    # Normal key/value pair encoded as usual.
    assert "page=1" in url_str


@pytest.mark.asyncio
async def test_request_rejects_both_json_and_form_fields(monkeypatch):
    # We don't care about transport; just need a dummy client.
    monkeypatch.setattr("icinga_mcp.http_client.httpx.AsyncClient", DummyAsyncClientEncode)

    s = Settings(
        base_url="https://example.com/icingaweb2",
        username="u",
        password="p",  # type: ignore
    )
    client = IcingaWebClient(s)

    with pytest.raises(ValueError):
        await client.request(
            "POST",
            "icingadb/hosts",
            json_body={"a": 1},
            form_fields={"b": "2"},
        )


class DummyAsyncClientRedirect:
    """Dummy client that always returns a redirect response."""

    def __init__(self, *args, **kwargs):
        self.requests: list[httpx.Request] = []

    def build_request(self, method: str, url: httpx.URL, **kwargs) -> httpx.Request:
        return httpx.Request(method, url, **kwargs)

    async def send(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        # 302 with Location header to trigger redirect handling.
        return httpx.Response(302, request=req, headers={"location": "https://example.com/login"})

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_request_raises_on_upstream_redirect(monkeypatch):
    monkeypatch.setattr("icinga_mcp.http_client.httpx.AsyncClient", DummyAsyncClientRedirect)

    s = Settings(
        base_url="https://example.com/icingaweb2",
        username="u",
        password="p",  # type: ignore
    )
    client = IcingaWebClient(s)

    with pytest.raises(httpx.HTTPStatusError):
        await client.request("GET", "icingadb/hosts")


class DummyAsyncClientRetry:
    """Dummy client that returns 500 once then 200 to exercise retry behavior."""

    def __init__(self, *args, **kwargs):
        self.requests: list[httpx.Request] = []
        self._seen = 0

    def build_request(self, method: str, url: httpx.URL, **kwargs) -> httpx.Request:
        return httpx.Request(method, url, **kwargs)

    async def send(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        self._seen += 1
        if self._seen == 1:
            # First attempt responds with 500 to trigger retry.
            return httpx.Response(500, request=req)
        # Second attempt succeeds.
        return httpx.Response(200, request=req, json={"ok": True})

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_request_retries_on_5xx_and_succeeds(monkeypatch):
    monkeypatch.setattr("icinga_mcp.http_client.httpx.AsyncClient", DummyAsyncClientRetry)

    # Use low retry/backoff settings to keep the test fast.
    s = Settings(
        base_url="https://example.com/icingaweb2",
        username="u",
        password="p",  # type: ignore
        max_retries=2,
        retry_backoff=0.0,
    )
    client = IcingaWebClient(s)

    resp = await client.request("GET", "icingadb/hosts")

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert isinstance(client._client, DummyAsyncClientRetry)  # type: ignore[attr-defined]

    dummy: DummyAsyncClientRetry = client._client  # type: ignore[assignment,attr-defined]
    # One failed + one successful attempt.
    assert len(dummy.requests) == 2
