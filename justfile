# OpenPhotoAgent task runner (CLAUDE.md §6). Requires: uv, pnpm, PostgreSQL binaries (OPAGENT_PG_BIN or distro).
set shell := ["bash", "-uc"]
export UV_SYSTEM_CERTS := "1"

py_fast := "not integration and not e2e and not chaos and not perf and not real_model and not gpu and not release_gate and not nightly"

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
    uv run python tools/cargo_tc.py

# integration (real PostgreSQL) + chaos subset; then the Python perf budgets in a process of their own (ADR-0018)
test-int:
    OPAGENT_TC_RESULTS=.tc/py-int.json uv run pytest -q --timeout=900 --timeout-method=thread -m "(integration or chaos) and not perf and not nightly and not release_gate"
    just perf-api

# Python perf budgets (layer F: VIEW-009, PF-075) — never in the integration process, whose heap they would time
perf-api:
    OPAGENT_TC_RESULTS=.tc/py-perf.json uv run pytest -q -p no:randomly -m "perf and not nightly and not gpu and not real_model"

# Playwright against the web build + all-in-one backend with FakeBackend: every project, visual included.
# Visual baselines match this dev container and the pinned Playwright image only (ADR-0019).
e2e:
    pnpm --filter web build
    pnpm --filter web build-storybook
    pnpm --filter web e2e

# PR CI: everything except the pixel baselines (09 §5: visual is a nightly layer, in the pinned image)
e2e-pr:
    pnpm --filter web build
    pnpm --filter web build-storybook
    pnpm --filter web exec playwright test --project=chromium

# visual baselines only; CI runs this inside mcr.microsoft.com/playwright:v<@playwright/test>-noble (VIS-004)
visual *args:
    pnpm --filter web build
    pnpm --filter web build-storybook
    pnpm --filter web exec playwright test --project=visual {{args}}

# perf budgets (VIEW-002/003/010, PERF-001/002/003/006) on a 100k-photo library; not part of PR CI
perf:
    pnpm --filter web build
    OPAGENT_E2E_PERF=1 pnpm --filter web exec playwright test

# ruff + mypy + import-linter + eslint + tsc
lint:
    uv run ruff check .
    uv run ruff format --check .
    uv run mypy -p opagent_core -p opagent_fs -p opagent_schemas -p opagent_secure
    uv run mypy -p opagent_inference -p opagent_adapters -p opagent_registry -p opagent_doctor -p opagent_search -p opagent_geo -p opagent_db -p opagent_api -p opagent_worker -p opagent_cli -p opagent_testing -p opagent_mcp -p opagent_assistant
    uv run lint-imports
    uv run python tools/gen_error_i18n.py --check
    uv run python tools/gen_api_labels.py --check
    uv run python tools/gen_problem_stories.py --check
    uv run python tools/contracts.py
    uv run python tools/check_changelog.py
    pnpm -r --if-present lint
    pnpm -r --if-present typecheck
    cd apps/desktop/supervisor && cargo fmt --check && cargo clippy -q --all-targets -- -D warnings
    # the Windows-only code (job objects) must compile too; CI's Windows runner is the first place it would fail
    rustup target add x86_64-pc-windows-gnu >/dev/null 2>&1 || true
    cd apps/desktop/supervisor && cargo check -q --target x86_64-pc-windows-gnu --all-targets

# v1.0 interface contracts (VERSIONING §4): check that the code still matches contracts/*.json
contracts:
    uv run python tools/contracts.py

# rewrite contracts/*.json — only for additive changes (or a new major with a CHANGELOG migration note)
contracts-update:
    uv run python tools/contracts.py --update

# regenerate docs/generated/coverage-status.md
tc-status:
    uv run python tools/tc_status.py

# mutation testing on fs-sandbox / path_template changes (score >= 85%)
mutate-diff:
    uv run python tools/mutate_diff.py

# all-in-one dev mode (api + worker + PG + web)
dev:
    uv run opagent dev
