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

規則：大型 PR 請先開 issue 討論；**絕對不要 commit 照片、模型權重或任何祕密**；凡是會搬移或刪除檔案的程式碼，都必須走「計畫 → 核准 → 日誌 → 執行」流程並附上測試；commit 訊息使用 Conventional Commits；貢獻內容一律以 Apache-2.0 授權。
