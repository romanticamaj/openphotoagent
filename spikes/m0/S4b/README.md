# M0-S4b — 繁中 zero-shot（pending-hardware／需 HF 存取）

需要 `img:by-content-12-large`（600 張，manifest 於 nightly 下載）與 SigLIP2 權重。
```bash
uv run --with torch,transformers,pillow python spikes/m0/S4b/run.py --images <600 張 + labels.json>
```
`prompts.json` 是 12 類的 zh-Hant 與英文 prompt（英文版即「自動英譯」的人工校對版本）。通過：ensemble top-1 ≥ 0.80 且不低於英文版 3 pp 以上。結果決定 03 §3.3.1 的 prompt ensemble 預設。
