# Deployment image for `aim serve`. Layout follows AI-Pipeline-V3's docker/Dockerfile.
FROM python:3.14-slim AS builder
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_CACHE_DIR=/tmp/uv-cache UV_LINK_MODE=copy
COPY --from=ghcr.io/astral-sh/uv:0.11.7@sha256:240fb85ab0f263ef12f492d8476aa3a2e4e1e333f7d67fbdd923d00a506a516a /uv /uvx /bin/
WORKDIR /app
# Dependency layer keyed only on pyproject/uv.lock.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/tmp/uv-cache uv sync --frozen --no-dev --no-install-project
COPY src/ src/
RUN --mount=type=cache,target=/tmp/uv-cache uv sync --frozen --no-dev

FROM python:3.14-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 aim
WORKDIR /app
COPY --from=builder /app /app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER aim
# Exec form: `aim` is PID 1 and gets SIGTERM from `docker stop` directly; the CLI's signal
# handler lets the current job finish before exiting. Settings come from the environment
# (compose `env_file`), so no --env-file here.
CMD ["aim", "serve"]
