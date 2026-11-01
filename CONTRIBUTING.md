# Contributing to OpenPhotoAgent

Thanks for your interest! OpenPhotoAgent is in its **design phase**, so this is the best time to shape it.

[繁體中文說明在下方](#繁體中文)

## Ways to help right now

| Area | How |
|---|---|
| 🧪 **Benchmarks** | Run the M0 spike (vLLM + Qwen3-VL-8B FP8, SigLIP 2) on your GPU and open an issue using the *Benchmark report* template |
| 💬 **Design review** | Read the specs in [`docs/`](docs/) and open a Discussion or Issue. Concrete suggestions are best: "§4.3 should…" |
| 🧠 **Model adapters** | Propose a model or runtime in an issue, with license, VRAM and structured-output support |
| 🎨 **UI mockups** | Figma or HTML mockups that follow [docs/04](docs/04-control-panel-ux.md) |
| 🌐 **Translation** | The specs are written in Traditional Chinese, and English translations are welcome |

## Ground rules

- **Discuss before large PRs.** Open an issue first so we can agree on the approach.
- **Never commit photos, model weights or secrets.** Test fixtures must be CC0 or your own images, and `.gitignore` already blocks the common cases.
- **Safety first for file operations.** Anything that moves or deletes files must go through the plan → approve → journal → execute flow ([docs/02 §6](docs/02-system-architecture.md)), and must come with tests.
- Use [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `docs:` …). PR titles become squash-merge commits, which drive versioning and the changelog (see [VERSIONING.md](VERSIONING.md)).
- By contributing, you agree that your contributions are licensed under [Apache-2.0](LICENSE).

## Test-driven development rules

OpenPhotoAgent is built with **strict TDD** (full rules: [docs/09 §1](docs/09-tdd-and-test-suite.md)). Every test carries a case ID from [09a](docs/09a-test-cases-v0.1.md) / [09b](docs/09b-test-cases-v0.2-plus.md): `@pytest.mark.tc("FS-012")` in Python, `it("[VIEW-005] …")` in TypeScript.

### Commit rules (docs/09 §1.3)

| Rule | Content |
|------|---------|
| Types | `test:`, `feat:`, `fix:`, `refactor:`, `perf:`, `docs:`, `build:`, `ci:`, `chore:`; scope = package (`core`, `fs-sandbox`, `worker`, `api`, `inference`, `web`, `viewer`, `desktop`, `mcp`, `packaging`) |
| Order | Within a PR, the `test:` commit must come **before** (or be the same commit as) the matching `feat:`/`fix:` commit. CI job `tdd-order` checks this heuristically |
| Footer | `Tests: FS-003, FS-004` (required for `test`/`feat`/`fix`); `Refs: 11#38` (capability number); golden changes add `Golden-Update: <reason>` |
| Bug fixes | Add a new regression test (new ID, marked "回歸"/regression) that reproduces the bug first, then fix |
| Squash | The PR title becomes the squash commit and drives the changelog |

### PR checklist (docs/09 §1.4)

The checklist lives in [`.github/pull_request_template.md`](.github/pull_request_template.md): red-before-green evidence, no skipped/weakened tests, golden files untouched, `tools/tc_status.py` re-run, plan → approve → journal → execute for file changes, error codes with i18n and fixes, untrusted text handling, no real network/time/models in PR tests, coverage and mutation thresholds.

### Never delete or skip tests to get green (docs/09 §1.5)

1. **Forbidden:** deleting tests, adding `skip`/`xfail`/`.only`/`test.fixme`, weakening assertions (e.g. `==` → `in`, loosening thresholds), mocking the module under test, or editing golden files to match wrong output. CI `test-integrity` compares against the base branch; such changes need the `test-change-approved` label (CODEOWNERS only).
2. **AI agents additionally:** if the spec says A, the test says A, but A is hard to implement — report and stop; never edit the test. If the test itself contradicts the spec, fix it in a separate `test:` commit citing the spec section.
3. **Legitimate removal:** a product decision removes the feature (docs first) → mark the case "廢止" (retired) in 09a/09b with the decision source and delete the test in the same change.

## Development setup

Coming with v0.1. The planned stack is Python with a **uv** workspace (ruff, mypy, pytest), TypeScript with **pnpm** (React 19, Vite, Vitest, Playwright), and Docker with NVIDIA Container Toolkit for GPU tests.

---

## 繁體中文

感謝你的興趣！專案目前在**設計階段**，現在加入最能影響它的方向。

- 🧪 **Benchmark：** 在你的 GPU 上跑 M0 spike，用 *Benchmark report* 範本開 issue 回報
- 💬 **設計審查：** 讀完 [`docs/`](docs/) 的規格後開 Discussion 或 Issue，建議越具體越好
- 🧠 **模型 adapter：** 開 issue 提議新模型或 runtime，請附上授權、VRAM 需求、是否支援 structured output
- 🎨 **UI mockup：** 依照 [docs/04](docs/04-control-panel-ux.md) 畫 Figma 或 HTML mockup
- 🌐 **翻譯：** 把規格翻成英文

TDD 規則（詳見 [docs/09 §1.3–§1.5](docs/09-tdd-and-test-suite.md)）：測試標註 09a／09b 的 ID；`test:` commit 必須早於或同於對應的 `feat:`／`fix:`，footer 必填 `Tests:` 與 `Refs:`；禁止刪除、跳過或弱化測試，禁止修改 golden 去配合錯誤輸出；PR 請依 `.github/pull_request_template.md` 檢查清單填寫。

規則：大型 PR 請先開 issue 討論；**絕對不要 commit 照片、模型權重或任何祕密**；凡是會搬移或刪除檔案的程式碼，都必須走「計畫 → 核准 → 日誌 → 執行」流程並附上測試；commit 訊息使用 Conventional Commits；貢獻內容一律以 Apache-2.0 授權。
