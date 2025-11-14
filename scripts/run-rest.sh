#!/usr/bin/env bash
set -euo pipefail
. .venv/bin/activate
export PYTHONPATH="./src:${PYTHONPATH:-}"
exec uvicorn icinga_mcp.rest_app:app --host "${REST_HOST:-127.0.0.1}" --port "${REST_PORT:-8080}" --reload --reload-dir src --reload-exclude ".venv"
