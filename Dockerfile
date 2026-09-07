# Authored with Claude Code (Anthropic) under the direction of Anita Agasaveeran
# and Ameya Mathew, who specified the requirements and design and reviewed the
# implementation.
# See the "Credits and authorship" section of README.md.
# syntax=docker/dockerfile:1

# ---------------------------------------------------------------- build stage
# Dependencies are installed into a virtualenv here, then copied into a clean
# runtime image. Build tooling and pip's caches never reach the final layer.
FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy only what the install needs first, so the dependency layer is cached and
# a source-only change does not reinstall every package.
COPY pyproject.toml README.md ./
COPY app ./app

RUN pip install --upgrade pip && pip install .

# -------------------------------------------------------------- runtime stage
FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.title="issues-api-testbed" \
      org.opencontainers.image.description="FastAPI wrapper over the GitHub Issues REST API" \
      org.opencontainers.image.source="https://github.com/anita-agasaveeran/issues-api-testbed"

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000 \
    DATABASE_URL="sqlite:////data/events.db"

# Run as an unprivileged user. /data is a separate, writable directory so the
# SQLite event store survives container restarts when a volume is mounted.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /data \
    && chown -R appuser:appuser /data

COPY --from=builder /opt/venv /opt/venv

WORKDIR /srv
COPY --chown=appuser:appuser app ./app
COPY --chown=appuser:appuser openapi.yaml ./openapi.yaml

USER appuser
EXPOSE 8000
VOLUME ["/data"]

# python rather than curl: the slim image ships no curl, and adding one would
# grow the image and its attack surface for a liveness probe.
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import os,urllib.request,sys; \
url=f\"http://127.0.0.1:{os.environ.get('PORT','8000')}/healthz\"; \
sys.exit(0 if urllib.request.urlopen(url, timeout=2).status == 200 else 1)"

# Exec form, so SIGTERM reaches uvicorn rather than a wrapping shell: `exec`
# replaces the shell with uvicorn, which then runs as PID 1 and gets the signal.
# Without it `docker stop` kills the process before the lifespan handler can close
# the event store and HTTP client. Going through `sh -c` keeps ${PORT} expandable.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
