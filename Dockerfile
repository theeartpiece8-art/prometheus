# PROMETHEUS Quant Lab — Backend Dockerfile
# Multi-stage build: install dependencies in a builder layer, copy only
# what's needed into a slim runtime image.

FROM python:3.12-slim AS builder

WORKDIR /build

# System deps needed to build psycopg2 and other C-extension packages.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


FROM python:3.12-slim AS runtime

# Runtime-only system deps (libpq for psycopg2, curl for the healthcheck).
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 appuser

COPY --from=builder /install /usr/local

WORKDIR /app
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini .

RUN chown -R appuser:appuser /app
USER appuser

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/api/v1/health || exit 1

# Runs migrations, then starts the API. In production, prefer running
# migrations as a separate one-off step/init-container rather than on
# every container start; kept inline here for Sprint 1 simplicity so
# `docker compose up` works out of the box.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
