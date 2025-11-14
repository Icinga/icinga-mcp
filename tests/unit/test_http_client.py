import asyncio
import pytest
from icinga_mcp.config import Settings
from icinga_mcp.http_client import IcingaWebClient

@pytest.mark.asyncio
async def test_client_init_close():
    s = Settings(base_url="https://example.com/icingaweb2", username="u", password="p")  # type: ignore
    c = IcingaWebClient(s)
    await c.close()
