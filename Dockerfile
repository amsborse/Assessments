# syntax=docker/dockerfile:1.7
# Debian 12 (bookworm) is a Playwright-supported base for `playwright install --with-deps`.
ARG PYTHON_IMAGE=python:3.12-slim-bookworm

# ---------------------------------------------------------------- deps
# Resolve third-party dependencies only, so this layer is rebuilt only when uv.lock changes.
FROM ${PYTHON_IMAGE} AS deps
COPY --from=ghcr.io/astral-sh/uv:0.12.17 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-dev --no-install-project

# ---------------------------------------------------------------- runtime
FROM ${PYTHON_IMAGE} AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src \
    PATH=/app/.venv/bin:$PATH \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
WORKDIR /app

COPY --from=deps /app/.venv /app/.venv

# Chromium headless shell + required system libraries (root needed for apt).
# Depends only on the venv layer, so source changes don't re-download the browser.
RUN playwright install --with-deps --only-shell chromium \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/*

RUN useradd --system --uid 10001 --create-home --shell /usr/sbin/nologin app \
    && mkdir -p /app/data && chown app:app /app/data

COPY src ./src
COPY catalog ./catalog

USER app
ENV HOST=0.0.0.0 PORT=8000 DATA_DIR=/app/data CATALOG_DIR=/app/catalog
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/healthz', timeout=3)"]
ENTRYPOINT ["python", "-m", "assessments.cli"]
CMD ["serve"]
