# M0-T1 — 測試基建可行性

| 子項 | 怎麼跑 | 狀態 |
|---|---|---|
| (a) template clone < 200 ms | `uv run python spikes/m0/T1/run.py`（三平台 runner 各一次，`m0-spike.yml`） | Linux 實測；macOS／Windows 待 CI |
| (b) tiny GGUF／ONNX | 候選（需在可連 HF 的機器確認大小與 CPU 延遲）：llama.cpp 的 `ggml-org/models` 內 tiny 測試模型、SmolVLM-256M GGUF、`Xenova/siglip-base-patch16-224` 量化 ONNX、隨機初始化的 tiny SigLIP（自行匯出，僅測協定）。若都超過 50 MB，依 Q-T2 只測 FakeRuntime 協定 | 雲端被擋（HF 403） |
| (c) Windows junction／subst／佔位檔 | `pwsh spikes/m0/T1/c_windows.ps1` | pending-hardware |
| (d) Playwright desktop build | 切片 1／10 的 e2e-smoke job | 待實作 |
