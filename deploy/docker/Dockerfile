# OpenPhotoAgent all-in-one (08 §0 delivery: Docker self-host). PG 17 + pgvector from the official pgvector image,
# the same web build as the desktop bundle (DOCK-002), FakeBackend-free runtime (real adapters arrive with packs).
# syntax=docker/dockerfile:1.7

FROM node:22-bookworm-slim AS web
WORKDIR /src
RUN corepack enable
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/web/package.json apps/web/
COPY packages/ packages/
COPY tools/tc-reporters/ tools/tc-reporters/
RUN pnpm install --frozen-lockfile --filter web...
COPY apps/web/ apps/web/
RUN pnpm --filter web build && find apps/web/dist -type f -print0 | sort -z | xargs -0 sha256sum > /web-dist.sha256

FROM pgvector/pgvector:pg17 AS app
ENV UV_PYTHON_INSTALL_DIR=/opt/python UV_LINK_MODE=copy OPAGENT_PG_BIN=/usr/lib/postgresql/17/bin \
    OPAGENT_HOME=/data OPAGENT_WEB_DIST=/app/web OPAGENT_BIND_HOST=0.0.0.0 \
    OPAGENT_ROOTS="photos=/photos:ro,sorted=/sorted:rw"
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages/ packages/
COPY apps/api apps/api
COPY apps/worker apps/worker
COPY apps/cli apps/cli
# data the app reads at runtime from the repo root (DOCK-004): model catalog, built-in presets, hw probe
# profiles, and the FakeBackend vectors used by `opagent dev` until real adapters arrive with packs
COPY fixtures/hw/ fixtures/hw/
COPY fixtures/fake_vectors.yaml fixtures/fake_vectors.yaml
RUN uv sync --frozen --no-dev --package opagent-cli && rm -rf /root/.cache
COPY --from=web /src/apps/web/dist /app/web
COPY --from=web /web-dist.sha256 /app/web-dist.sha256
RUN mkdir -p /data /photos /sorted
VOLUME ["/data"]
EXPOSE 47800
HEALTHCHECK --interval=10s --timeout=3s --start-period=30s CMD ["/app/.venv/bin/python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:47800/healthz', timeout=2).status == 200 else 1)"]
ENTRYPOINT ["/app/.venv/bin/opagent", "dev", "--port", "47800"]
