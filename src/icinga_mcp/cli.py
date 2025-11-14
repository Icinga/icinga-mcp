"""Command-line entry points for icinga-mcp.

Author: Bernd Erk - Icinga GmbH
License: GPL-2.0-only
"""
from __future__ import annotations

import asyncio
from .logging import setup_logging
from .config import load_settings
import uvicorn


def run_rest() -> None:
    """Run the FastAPI REST server.

    Loads settings from environment and starts uvicorn bound to configured host/port.
    Logging is initialized via structlog.
    """
    setup_logging()
    from .rest_app import app
    s = load_settings()
    uvicorn.run(app, host=s.rest_host, port=s.rest_port, log_level="info")


def run_mcp() -> None:
    """Run the MCP stdio server.

    MCPO typically spawns this command; it serves tools over stdio using the MCP protocol.
    """
    setup_logging()
    # Runs stdio MCP server; MCPO will spawn this command.
    from .mcp_server import run_stdio
    asyncio.run(run_stdio())


def health() -> None:
    """Lightweight health check printing OK if settings load successfully."""
    # Basic self-check that config can load
    _ = load_settings()
    print("OK")
