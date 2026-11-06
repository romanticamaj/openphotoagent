# M0 spike（docs/09 §6）

每個實驗一個目錄：`run.py`（或說明文件）＋ `README.md`；結果寫到 `results/<id>.json`：

```json
{"id": "M0-S1a", "status": "measured|partial|pending-hardware|blocked", "machine": {...}, "metrics": {...}, "pass": true|false|null, "notes": "..."}
```

- `pass` 只有在通過標準被完整評估時才是 true／false；否則 null。
- 推估值只能放在 `metrics.estimates`，並標明「NOT measured」。**不得捏造數據。**
- 彙整：`python spikes/m0/summarize.py`；CI：`.github/workflows/m0-spike.yml`（手動觸發，三平台跑可在 runner 上跑的部分）。
- 給使用者的硬體執行步驟：[`docs/research/m0-results.md`](../../docs/research/m0-results.md)。

| ID | 目錄 | 雲端狀態 |
|---|---|---|
| M0-S1a | `S1a/` | Linux 實測（partial） |
| M0-S1b | `S1b/` | Linux postgres-only 實測（partial） |
| M0-S1c | `S1c/` | Windows 實測 |
| M0-S2 | `S2/` | Linux 組件實測 + 標明的估算（partial） |
| M0-S3／S3b | `S3/`、`S3b/` | 5070 Ti 實測；其他 GPU pending |
| M0-S4 | `S4/` | 真權重 CPU／DirectML 實測（`M0-S4`、`M0-S4-variants`）；CoreML pending |
| M0-S4b | `S4b/` | 代理資料實測（partial） |
| M0-S5 | `S5/` | 文件審查（partial；AuraFace 與 TAR 待驗證） |
| M0-S6 | `S6/` | blocked（使用者帳號與憑證） |
| M0-S7 | `S7/` | 8B 實測；35B pending |
| M0-V7 | `V7/` | pending-hardware（需切片 6） |
| M0-V8 | `V8/` | 實測（Ubuntu 24.04 WebKitGTK 2.52） |
| M0-V9 | `V9/` | pending（需切片 11 + Windows） |
| M0-T1 | `T1/` | (a) Linux 實測；(b) HF 被擋；(c) Windows pending；(d) 待 e2e |
| M0-T2 | `T2/` | 已錄 `cloud-linux-nogpu`；參考機 pending |
