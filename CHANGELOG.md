# Changelog

All notable changes to OpenPhotoAgent are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project follows [Semantic Versioning](https://semver.org/). See [VERSIONING.md](VERSIONING.md) for the full policy.

## [0.0.2](https://github.com/romanticamaj/openphotoagent/compare/v0.0.1...v0.0.2) (2026-11-01)


### 📚 Documentation

* initial README, design specs, license and community files ([57e19d4](https://github.com/romanticamaj/openphotoagent/commit/57e19d49943c0c8cb41a61db666819365bac9460))

## [0.0.1] - 2026-11-01

### 🎞 Design Preview

The first public snapshot of OpenPhotoAgent. **No runnable code yet.** This release publishes the complete design so the community can review it before the MVP (`v0.1.0`) is built.

### ✨ Highlights

- **Product definition:** an open-source, self-hosted photo AI that runs **Caption / Tagging / Classification** on your own GPU, organizes photos into folders safely and reversibly, offers hybrid AI search, and exposes everything to third-party agents through an **MCP server**.
- **16 GB GPU default stack:** SigLIP 2 for zero-shot classification and embeddings, plus Qwen3-VL-8B-Instruct FP8 as the main VLM, with Qwen3.6-35B-A3B as an optional reviewer for hard cases.
- **Privacy by design:** originals never leave the worker, four egress levels per workspace, and MCP agents can only *propose* file changes. A human approves them in the browser.

### 📚 Documentation

- `docs/00-master-plan.md`: positioning, requirement mapping, cross-spec decisions, roadmap
- `docs/01-prior-art-and-naming.md`: survey of about 30 related projects (Immich, PhotoPrism, TagGUI, Label Studio …), gap matrix, build-vs-fork decision, name availability
- `docs/02-system-architecture.md`: all-in-one and control plane + edge worker deployment, Postgres + pgvector with a `SKIP LOCKED` queue, data model, job lifecycle, REST and worker protocol, path sandbox, plan → approve → journal → execute file operations, monorepo layout
- `docs/03-inference-layer.md`: YAML model registry, `InferenceService` / adapter interfaces, task strategies (fused JSON, controlled-vocabulary tagging, cascade classification), VRAM management on Blackwell, built-in mini benchmark
- `docs/04-control-panel-ux.md`: user journeys, wireframes for 14 pages, "lightbox in a darkroom" design system, React 19 + Vite + shadcn/ui stack
- `docs/05-search-and-mcp.md`: hybrid search (image vectors + Traditional Chinese full-text + tags, fused with RRF), MCP server with 21 tools, OAuth 2.1 / PAT scopes, approval flow, prompt-injection threat model

### 🧰 Project

- README in English and Traditional Chinese, with light and dark banners
- Apache-2.0 license, `CONTRIBUTING.md`, `SECURITY.md`, issue templates (benchmark report, feature suggestion)
- Versioning policy (`VERSIONING.md`), this changelog, and release automation (release-please, plus a workflow that publishes a release on tag push)

### ⚠️ Known limitations

- All VRAM and throughput figures are **estimates** and will be measured in the M0 spike.
- The specs are written in Traditional Chinese. English translations are welcome.

[0.0.1]: https://github.com/romanticamaj/openphotoagent/releases/tag/v0.0.1
