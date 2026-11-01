# OpenPhotoAgent task runner (CLAUDE.md §6). Requires: uv, pnpm, PostgreSQL binaries (OPAGENT_PG_BIN or distro).
set shell := ["bash", "-uc"]
export UV_SYSTEM_CERTS := "1"

py_fast := "not integration and not e2e and not chaos and not perf and not real_model and not gpu"

default:
    @just --list

# uv sync + pnpm install (+ Playwright browser already provided in CI image)
setup:
    uv sync
    pnpm install

# unit + property + contract (fast; PR required)
test:
    OPAGENT_TC_RESULTS=.tc/py-fast.json uv run pytest -q -m "{{py_fast}}"
    pnpm -r --if-present test

# integration (real PostgreSQL) + chaos subset
test-int:
    OPAGENT_TC_RESULTS=.tc/py-int.json uv run pytest -q -m "(integration or chaos) and not nightly"

# Playwright against the web build + all-in-one backend with FakeBackend
e2e:
    pnpm --filter web build
    pnpm --filter web e2e

# ruff + mypy + import-linter + eslint + tsc
lint:
    uv run ruff check .
    uv run ruff format --check .
    uv run mypy -p opagent_core -p opagent_fs -p opagent_schemas
    uv run mypy -p opagent_inference -p opagent_adapters -p opagent_registry -p opagent_doctor -p opagent_db -p opagent_api -p opagent_worker -p opagent_cli -p opagent_testing
    uv run lint-imports
    pnpm -r --if-present lint
    pnpm -r --if-present typecheck

# regenerate docs/generated/coverage-status.md
tc-status:
    uv run python tools/tc_status.py

# mutation testing on fs-sandbox / path_template changes (score >= 85%)
mutate-diff:
    uv run python tools/mutate_diff.py

# all-in-one dev mode (api + worker + PG + web)
dev:
    uv run opagent dev
