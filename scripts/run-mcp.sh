#!/usr/bin/env bash
set -euo pipefail
. .venv/bin/activate
exec python -m icinga_mcp.mcp_server
