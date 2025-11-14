.PHONY: venv install dev fmt lint type test run-rest run-mcp ci check-python image image-podman run-docker run-podman

# Choose Python interpreter (override with: make PYTHON=python3.12)
PYTHON ?= python3

# Verify Python version is >= 3.11
check-python:
	@$(PYTHON) -c 'import sys; sys.exit(0 if sys.version_info>=(3,11) else 1)' >/dev/null 2>&1 || (echo "Python >= 3.11 required; found:" && $(PYTHON) -V 2>&1 && echo "Use: make PYTHON=python3.12 venv"; exit 1)

venv: check-python
	$(PYTHON) -m venv .venv

install:
	. .venv/bin/activate && pip install -U pip && pip install -e .

dev:
	. .venv/bin/activate && pip install -e ".[dev]" && pre-commit install

fmt:
	. .venv/bin/activate && black src tests && ruff check --fix src tests

lint:
	. .venv/bin/activate && ruff check src tests

type:
	. .venv/bin/activate && mypy src

test:
	. .venv/bin/activate && PYTHONPATH=./src:$$PYTHONPATH pytest -q

run-rest:
	. .venv/bin/activate && PYTHONPATH=./src:$${PYTHONPATH} uvicorn icinga_mcp.rest_app:app --host $${REST_HOST:-127.0.0.1} --port $${REST_PORT:-8080} --reload --reload-dir src --reload-exclude ".venv/*"

run-mcp:
	. .venv/bin/activate && PYTHONPATH=./src:$${PYTHONPATH} python -m icinga_mcp.mcp_server

ci:
	make venv && make dev && make fmt && make lint && make type && make test

# Container helpers
image:
	docker build -t icinga-mcp:local -f Containerfile .

image-podman:
	podman build -t icinga-mcp:local -f Containerfile .

run-docker:
	docker run --rm --name icinga-mcp \
		-p $${HOST_PORT:-8080}:$${REST_PORT:-8080} \
		--env-file $${ENV_FILE:-.env} \
		icinga-mcp:local

run-podman:
	podman run --rm --name icinga-mcp \
		-p $${HOST_PORT:-8080}:$${REST_PORT:-8080} \
		--env-file $${ENV_FILE:-.env} \
		icinga-mcp:local
