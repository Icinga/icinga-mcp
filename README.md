# icinga-mcp

MCP/MCPO and REST bridge for Icinga Web 2 (Icinga DB Web module).

Important: This repository is not an officially supported Icinga product. It is a proof of concept aimed at learning about usage profiles and requirements. Use for experiments and lab environments; production use is at your own risk. Feedback is very welcome via issues and PRs.

## About

icinga-mcp exposes two surfaces:
- FastAPI REST server with OpenAPI/Swagger UI.
- MCP server that exposes tools consumable by MCPO (Model Context Protocol Orchestrator).

What it does:
- Talks to the Icinga Web 2: Icinga DB Web module, not the Icinga 2 core API.
- Provides read and write operations: listing hosts/services/problems, groups, history; comments; acknowledgements; downtimes; notifications; rescheduling; processing results.
- Normalizes and summarizes upstream payloads for UI/LLM consumption when requested.

MCP vs REST/OpenAPI:
- MCP: a stdio server speaking the MCP protocol so MCPO can call monitoring actions as tools. Ideal for LLM tool-calling workflows.
- REST/OpenAPI: a developer-friendly HTTP facade with a documented OpenAPI schema and Swagger UI. It forwards requests to Icinga DB Web endpoints and applies optional projections.
- Both surfaces ultimately call the same service layer and upstream endpoints.

References: [src/icinga_mcp/rest_app.py](src/icinga_mcp/rest_app.py), [src/icinga_mcp/services.py](src/icinga_mcp/services.py), [docs/icinga-web-api.md](docs/icinga-web-api.md).

## Prerequisites
- Python 3.11 or newer (3.12 also works).
- Icinga Web 2 with the Icinga DB Web module reachable via HTTP/HTTPS.
- Network connectivity from this service to Icinga Web 2.

## Installation

Using make + virtualenv (recommended):

1) Create venv and install dev deps:
```bash
make venv
. .venv/bin/activate
make dev
```

2) Copy and edit environment:
```bash
cp .env.example .env
# Required upstream settings:
#   ICINGA_WEB_BASE_URL=https://icingaweb2.example.tld
#   ICINGA_WEB_USERNAME=...
#   ICINGA_WEB_PASSWORD=...
# TLS options:
#   ICINGA_WEB_VERIFY_TLS=true        # default: true
#   ICINGA_WEB_CA_BUNDLE=/path/to/ca.pem
# REST auth (optional but recommended):
#   REST_REQUIRE_API_KEY=true
#   REST_BEARER_TOKEN=super-secret
# REST bind:
#   REST_HOST=127.0.0.1
#   REST_PORT=8080
```

3) Run the REST server (for HTTP clients):
```bash
make run-rest
# or, after `pip install -e .`:
icinga-mcp-rest
```
Swagger UI will be at http://127.0.0.1:8080/docs

4) Run the MCP server (for MCPO):
```bash
make run-mcp
# or:
icinga-mcp-server
```
See MCPO setup notes in [docs/mcpo-setup.md](docs/mcpo-setup.md) and optional helpers in [scripts/](scripts).

5) Health check:
```bash
icinga-mcp-health
```

Make targets overview (see [Makefile](Makefile)):
- venv: create .venv with your chosen Python (override with PYTHON=python3.12).
- install: editable install of the package into the venv.
- dev: install dev deps and pre-commit hooks.
- fmt: black + ruff --fix on src and tests.
- lint: ruff check.
- type: mypy type-check.
- test: pytest.
- run-rest: launch uvicorn with reload.
- run-mcp: launch the stdio MCP server.
- ci: run the usual local CI chain.

## Configuration

Environment variables live in [.env](.env); an example is provided in [.env.example](.env.example). Key options:
- ICINGA_WEB_BASE_URL, ICINGA_WEB_USERNAME, ICINGA_WEB_PASSWORD
- ICINGA_WEB_VERIFY_TLS, ICINGA_WEB_CA_BUNDLE
- REST_REQUIRE_API_KEY, REST_BEARER_TOKEN
- REST_HOST, REST_PORT

Secrets are never logged; TLS verification is on by default. Use a least-privilege Icinga Web 2 account.

## Documentation

REST API
- OpenAPI/Swagger: GET /docs and /openapi.json when the REST server is running.
- Authentication (optional but recommended): set REST_REQUIRE_API_KEY=true and REST_BEARER_TOKEN. Then call endpoints with
  Authorization: Bearer <token>.
- Endpoint families (high level):
  - Hosts and services: list, problems, history.
  - Groups: hostgroups, servicegroups (overview and single-group detail).
  - Downtimes: list, schedule host/service, remove by composite name.
  - Comments: list, add host/service, remove by composite name.
  - Acknowledgements: add/remove for host/service.
  - Notifications: enable/disable host/service.
  - Checks: reschedule host/service, process check results.
- Filtering and pagination:
  - Use dotted filters like host.name or service.name; convenience params host and service are mapped for you.
  - Summary and fields parameters can return compact normalized shapes.

Complete, up-to-date route and parameter notes live in [docs/icinga-web-api.md](docs/icinga-web-api.md). Example requests are in [examples/http-examples.http](examples/http-examples.http).

Quick examples
```bash
# List services with problems (requires Bearer if enabled)
curl -H "Authorization: Bearer $REST_BEARER_TOKEN" \
  'http://127.0.0.1:8080/problems/services?page=1&limit=50&host=web01'

# Schedule a 1-hour host downtime
curl -X POST -H "Content-Type: application/json" \
  -H "Authorization: Bearer $REST_BEARER_TOKEN" \
  'http://127.0.0.1:8080/downtime/host?host=web01' \
  -d '{"comment":"maintenance","window":"hour"}'

# Add a service comment
curl -X POST -H "Content-Type: application/json" \
  -H "Authorization: Bearer $REST_BEARER_TOKEN" \
  'http://127.0.0.1:8080/comment/service?service=HTTP&host=web01' \
  -d '{"comment":"Investigating"}'
```

MCP tools
- Exposes tools analogous to the REST operations for MCPO orchestration (list_hosts, list_services, list_problems, schedule_downtime, acknowledge, comments, notifications, reschedule, process_result, etc.).
- Start the server with make run-mcp or the icinga-mcp-server console script, then configure MCPO to spawn/connect.

## License

Licensed under GPL v2 (SPDX: GPL-2.0-only). See [LICENSE](LICENSE) for the full text:
https://www.gnu.org/licenses/old-licenses/gpl-2.0.txt

## Feedback and support

This is experimental software and not supported by Icinga as a product. Issues and PRs are welcome to help shape future direction and capture real-world requirements.

## Notifications

- REST route: GET /notifications
- Upstream: GET /icingadb/notifications
- Supports dotted filters host.name and service.name (convenience host and service are mapped).
- Summary mode is enabled by default; control output via fields. Default summary fields: time, host, service, author, text.

Quick example
```bash
# List notifications (summary) for a host with explicit fields
curl -H "Authorization: Bearer $REST_BEARER_TOKEN" \
  'http://127.0.0.1:8080/notifications?host=web01&amp;fields=time,host,service,author,text'
```

Target Web URL
```bash
# Point to the requested upstream instance
export ICINGA_WEB_BASE_URL="https://test.icinga.com/icingaweb2"
```

## Container (Docker/Podman)

A ready-to-build container is provided via the root-level Containerfile. It runs the REST server by default, loads your .env, and exposes port 8080.

Prerequisites
- Docker or Podman installed
- Copy and edit environment: cp .env.example .env

Build the image
```bash
# Docker
docker build -t icinga-mcp:local -f Containerfile .

# Podman
podman build -t icinga-mcp:local -f Containerfile .
```

Run the REST API (port-forward + .env)
```bash
# Using --env-file to load your .env
docker run --rm --name icinga-mcp \
  -p 8080:8080 \
  --env-file ./.env \
  icinga-mcp:local

# Alternative: bind-mount .env into /app/.env (read-only)
docker run --rm --name icinga-mcp \
  -p 8080:8080 \
  -v "$PWD/.env":/app/.env:ro \
  icinga-mcp:local
```

Verify the service
```bash
# If REST auth is disabled
curl -fsS http://localhost:8080/health

# If REST auth is enabled (set REST_REQUIRE_API_KEY=true and REST_BEARER_TOKEN in .env)
curl -fsS -H "Authorization: Bearer $REST_BEARER_TOKEN" http://localhost:8080/health
```
Swagger UI: http://localhost:8080/docs

TLS CA bundle (optional)
```bash
# Mount a custom CA bundle and point ICINGA_WEB_CA_BUNDLE to it
docker run --rm --name icinga-mcp \
  -p 8080:8080 \
  --env-file ./.env \
  -v "$PWD/ca.pem":/app/ca.pem:ro \
  -e ICINGA_WEB_CA_BUNDLE=/app/ca.pem \
  icinga-mcp:local
```

Run the MCP stdio server (instead of REST)
```bash
# Override entrypoint to start the MCP server
docker run --rm --name icinga-mcp-stdio \
  --env-file ./.env \
  --entrypoint icinga-mcp-server \
  icinga-mcp:local
```

Notes
- REST_HOST defaults to 0.0.0.0 in the container; no change required for port mapping.
- Container EXPOSEs 8080; publish with -p 8080:8080 (or any host port you prefer).
- Healthcheck inside the image calls /health and will use REST_BEARER_TOKEN if set.
- The image runs as a non-root user (UID 10001); ensure any mounted files (e.g., .env, CA bundle) are world-readable or readable by that UID.
- Podman equivalents work the same: replace docker with podman in the commands above.
