# =============================================================================
# Invoice Intelligence Platform — Dockerfile
# Multi-stage build: frontend builder -> Python builder -> slim runtime.
# The runtime image serves the built React app AND the API from one
# process/origin (see app/main.py's FRONTEND_DIST static route) — no
# second hosting provider, no separate frontend container.
# =============================================================================

# ---------------------------------------------------------------------------
# Stage 1: Frontend builder — produces web/dist (static assets only;
# nothing from this stage ships in the runtime image except that output)
# ---------------------------------------------------------------------------
FROM node:22-slim AS frontend-builder

WORKDIR /build/web

COPY web/package.json web/package-lock.json ./
RUN npm ci

COPY web/ ./
RUN npm run build

# ---------------------------------------------------------------------------
# Stage 2: Python builder — installs all Python dependencies
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# Install system build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency files first (leverages Docker layer cache). README.md
# is required too: pyproject.toml declares it as the package readme, and
# hatchling's build backend validates the file exists during
# `pip install .`'s metadata step — without it the build fails before any
# dependency is even resolved.
COPY pyproject.toml README.md ./

# Create a virtual environment and install dependencies
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

RUN pip install --upgrade pip && pip install .

# ---------------------------------------------------------------------------
# Stage 3: Runtime — minimal production image
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# Install only runtime system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv

# Copy application source
COPY app/ ./app/
COPY alembic/ ./alembic/
COPY alembic.ini ./

# Copy the built frontend — served same-origin by app/main.py's
# FRONTEND_DIST static route (see that file for why this is one process,
# not a second hosting provider). Absent this directory, the app still
# runs as an API-only service (local backend-only dev, or if the
# frontend build stage is skipped).
COPY --from=frontend-builder /build/web/dist ./web/dist

# Create non-root user for security
RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser
RUN mkdir -p /app/uploads /app/logs && chown -R appuser:appgroup /app
USER appuser

# Render (and most PaaS free tiers) assign the listen port via $PORT at
# container start and route traffic to whatever that is — never assume
# 8000 in production. 8000 is only the local-Docker/docker-compose
# default, which never sets PORT.
ENV PORT=8000
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD sh -c 'curl -f http://localhost:${PORT}/api/v1/health || exit 1'

# Shell form so ${PORT} is expanded at container start, not baked in at
# build time. --workers 1: a single free-tier instance; do not raise
# this without also revisiting the DB pool size (DATABASE_POOL_SIZE)
# and OpenAI/Vision rate limits, which are sized for one worker.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1"]
