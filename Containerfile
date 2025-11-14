# syntax=docker/dockerfile:1
FROM python:3.12-slim

# OCI labels
LABEL org.opencontainers.image.title="icinga-mcp" \
      org.opencontainers.image.description="MCP/MCPO + FastAPI bridge for Icinga Web 2 (Icinga DB Web)" \
      org.opencontainers.image.licenses="GPL-2.0-only"

# Env defaults
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    REST_HOST=0.0.0.0 \
    REST_PORT=8080

WORKDIR /app

# Install minimal OS deps for healthcheck and TLS
RUN apt-get update && apt-get install -y --no-install-recommends \
      curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install the app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --upgrade pip && pip install .

# Non-root user
RUN useradd -r -u 10001 -m appuser && chown -R appuser:appuser /app
USER appuser

# .env support: mount your .env to /app/.env or pass --env-file
# EXPOSE declares the REST port; map it with -p 8080:8080
EXPOSE 8080

# Healthcheck supports both unsecured and Bearer-protected setups
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD sh -c 'if [ -n "$REST_BEARER_TOKEN" ]; then curl -fsS -H "Authorization: Bearer $REST_BEARER_TOKEN" http://127.0.0.1:8080/health >/dev/null; else curl -fsS http://127.0.0.1:8080/health >/dev/null; fi'

# Start the REST server
ENTRYPOINT ["icinga-mcp-rest"]