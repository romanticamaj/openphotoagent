# M0 Spike 結果（雲端部分）與硬體實驗執行指南

> 日期：2026-11-01｜環境：Claude Code 雲端 session（Ubuntu 24.04 容器、4 vCPU、15 GB RAM、**無 GPU**）｜網路政策：Hugging Face、PGDG apt、GitHub release 下載被擋；PyPI、npm、crates.io、GitHub git 協定可用。
> 規則：**只記錄實測值**。推估值只出現在 `metrics.estimates` 並標明。原始結果：[`spikes/m0/results/`](../../spikes/m0/results/)。

## 1. 結論表

| ID | 驗證項 | 狀態 | 已得結果 | 通過標準 | 判定 |
|----|--------|------|----------|----------|------|
| M0-S1a | 嵌入式 PG 啟動 | partial | Linux（PG 16.15）50 次：冷啟動 p95 **0.71 s**、暖啟動 p95 **0.127 s**、**0 失敗** | 暖 p95 < 5 s、0 失敗、Windows 管理員可啟動 | Linux ✅；Windows／macOS ⏳ |
| M0-S1b | kill -9 恢復 | partial | Linux postgres 20 次 kill -9：恢復 p95 **1.56 s**（max 1.79 s）、已提交 1,000 列 **0 遺失** | ≤ 30 s、0 遺失 | postgres ✅；api／worker／llama-server ⏳（需切片 11 監管器） |
| M0-S1c | Windows AF_UNIX | pending-hardware | — | 10,000 次 0 錯誤 | ⏳；維持 127.0.0.1 + SCRAM 預設 |
| M0-S2 | 安裝包大小 | partial | Linux 實測（xz -6）：CPython 3.12 **21.7 MB**、ORT CPU **16.6 MB**、其他 Python 依賴 **56.7 MB**、PG16+pgvector **15.2 MB** → 實測小計 **110.2 MB**；加上未建置組件的 08 §4.1 上限估算（Tauri 15、SPA 5、llama-server 15、GeoNames 15）→ **預估 160 MB** | ≤ 200 MB（目標 150） | 預估在上限內、略超目標；權威量測待 desktop-build（DESK-030） |
| M0-S3／S3b | llama.cpp 結構化輸出、key 順序 | pending-hardware | — | 首次合法 ≥ 99% 等 | ⏳ |
| M0-S4 | SigLIP2 ONNX 一致性 | pending-hardware | 匯出／比對流程已以**隨機初始化 tiny SigLIP** 驗證：ORT CPU 影像／文字 cosine 最小 0.99999988（**不代表真權重**） | 每向量 cosine ≥ 0.999 | ⏳ |
| M0-S4b | 繁中 zero-shot | pending-hardware | prompt 集已備（`S4b/prompts.json`） | ensemble ≥ 0.80 | ⏳ |
| M0-S5 | 人臉授權 | partial | YuNet **MIT**、SFace **Apache-2.0**、eDifFIQA(T) **CC-BY-4.0**（需署名），皆可再散布；sha256 已記錄 | 授權允許 Apache-2.0 散布並商用 | YuNet／SFace／eDifFIQA ✅；AuraFace ⏳（HF 被擋）；TAR@FAR ⏳（Q-T1） |
| M0-S6 | 簽章流水線 | blocked | — | notarize 通過 | 需使用者帳號（BLOCKERS B-3、B-4） |
| M0-S7 | tool-calling | pending-hardware | — | ≥ 95% | ⏳；未通過時 v0.3 只開快速路徑 |
| M0-V7 | ETA 涵蓋率 | pending-hardware | 模擬涵蓋率由 ETA-016 單元測試負責 | ≥ 70% | ⏳ |
| M0-V8 | WebKitGTK 能力 | measured | WebKitGTK **2.52.6**（Ubuntu 24.04）：View Transitions ✅、`Intl.DurationFormat` ✅、`createImageBitmap`（含 resize）✅、fetch streaming ✅、EventSource ✅、OffscreenCanvas ✅、container queries ✅、`:has()` ✅；**AVIF 解碼 ❌** | 記錄 | 見 §2 |
| M0-V9 | tauri-driver | pending-hardware | — | 10/10 | ⏳（切片 11） |
| M0-T1 | 測試基建 | partial | (a) template clone p95 **39 ms**（含 psql 啟動，上界）；(b) HF 被擋，候選見 `T1/README.md`；(c) Windows 腳本已備；(d) 由 e2e-smoke 驗證 | (a) < 200 ms | (a) Linux ✅ |
| M0-T2 | 硬體探針錄製 | partial | 實錄 `fixtures/hw/cloud-linux-nogpu.yaml` | 參考機皆有實錄 | ⏳ |

## 2. 對規格與實作的影響

1. **嵌入式 PG（S1a／S1b、T1a）**：Linux 上的啟動與 template clone 遠低於門檻，`embedded_pg` fixture 的「session 層級 initdb + 每測試 template clone」策略可行。雲端 session 只能取得 PG 16（PGDG 被擋），所以本機開發用 PG 16 + pgvector 0.6 跑整合測試；CI（`ci.yml`）與 m0-spike 用 PG 17。程式碼不得使用 PG 17 專屬語法（PG-014 一致性測試在 CI 以 pg17 驗證）。
2. **安裝包（S2）**：Python 依賴壓縮後 56.7 MB，超出 08 §4.1 估算的 35–50 MB。主要來源：jieba（解壓 37 MB，含字典）、numpy（57 MB，含 OpenBLAS）、pillow-heif（26 MB）、cryptography（15 MB）。預估總量 ~160 MB 仍 ≤ 200 MB 硬上限，但超過 150 MB 目標。切片 11 打包時的優先處理順序：(a) jieba 字典改用精簡繁中字典或延後到 data pack；(b) 檢查 cryptography 是否真的需要；(c) 依 08 §4.1 先移出 GeoNames。
3. **WebKitGTK（V8）**：Ubuntu 24.04 的 WebKitGTK 2.52 支援 View Transitions 與 `Intl.DurationFormat`，所以 VIEW-007 的 FLIP 降級與 I18N-006 的時間格式降級在 24.04 上不會觸發；但 22.04 的 WebKitGTK 較舊，**降級路徑仍要實作並測試**（以 feature detection，不以 UA 判斷）。**AVIF 無法解碼** → 縮圖格式維持 WebP／JPEG，不使用 AVIF。
4. **人臉授權（S5）**：YuNet（MIT）與 eDifFIQA(T)（CC-BY-4.0）可隨安裝包散布；eDifFIQA 必須在 NOTICE 與 About 頁署名。SFace（Apache-2.0，38.7 MB）適合放在 face pack。AuraFace 待在可連 HF 的機器確認授權檔與 sha；確認前 FACE-* 只用 FakeFaceBackend（09 Q-T1）。
5. **tiny 模型（T1b、Q-T2）**：雲端無法下載 HF 模型，PR 的 llama.cpp 契約測試先依 Q-T2 的退路只驗證程序管理與 OpenAI 相容協定（FakeRuntime）；ORT 的契約測試以「隨機初始化並自行匯出的 tiny ONNX」執行（`S4/run.py --tiny-random` 的作法），不需要網路。

## 3. 給使用者：在 Windows 5070 Ti 與 Mac 上執行 pending 實驗

前置（兩台都做）：
```bash
git clone https://github.com/romanticamaj/openphotoagent && cd openphotoagent && git checkout spike/m0
# 安裝 uv：https://docs.astral.sh/uv/
```

### 3.1 Windows 11 + RTX 5070 Ti（參考機 1）

| 順序 | 實驗 | 指令 |
|---|---|---|
| 1 | T2 硬體探針 | `uv run python spikes/m0/T2/record.py --profile rtx5070ti-win11` |
| 2 | S1a（一般帳號） | 下載 PG 17 Windows zip（EDB binaries）解壓到 `C:\pg17`；`$env:OPAGENT_PG_BIN="C:\pg17\pgsql\bin"; $env:OPAGENT_M0_MACHINE="rtx5070ti-win11-user"; uv run python spikes/m0/S1a/run.py --runs 50` |
| 3 | S1a（系統管理員 PowerShell，Defender 保持開啟） | 同上，`OPAGENT_M0_MACHINE=rtx5070ti-win11-admin` |
| 4 | S1c | 以 `-c unix_socket_directories='C:/opagent/sock'` 啟動 PG，`uv run --with asyncpg,"psycopg[binary]" python spikes/m0/S1c/run.py --sockdir C:/opagent/sock` |
| 5 | T1(c) | `pwsh spikes/m0/T1/c_windows.ps1` |
| 6 | S4（CPU、DirectML） | `uv run --with torch,transformers,onnx,onnxruntime-directml,pillow python spikes/m0/S4/run.py --images <500 張> --texts spikes/m0/S4/texts.txt --eps CPU,DML` |
| 7 | S4b | `uv run --with torch,transformers,pillow python spikes/m0/S4b/run.py --images <600 張 + labels.json>` |
| 8 | S3 | 下載 llama.cpp 官方 Windows CUDA 13 版與 Qwen3-VL-8B GGUF + mmproj；`llama-server -m … --mmproj … --port 8080 -c 8192 -ngl 99 --parallel 2`；`uv run python spikes/m0/S3/run.py --images <1000 張>` |
| 9 | S3b | `uv run python spikes/m0/S3/run.py --images <同上> --key-order category_first --id M0-S3b-category_first` |
| 10 | S7 | `llama-server -m … --jinja --port 8080 -c 16384 -ngl 99`；`uv run python spikes/m0/S7/run.py --runs 20`，再 `--runs 100 --id M0-S7-ext` |
| 11 | V7、S1b（完整）、V9 | 需要切片 6／11 完成後的 app（見各 README） |

### 3.2 Mac（M2 16 GB，參考機 3）

| 順序 | 實驗 | 指令 |
|---|---|---|
| 1 | T2 | `uv run python spikes/m0/T2/record.py --profile m2-air-16` |
| 2 | S1a | `brew install postgresql@17`；`OPAGENT_PG_BIN=$(brew --prefix postgresql@17)/bin uv run python spikes/m0/S1a/run.py --runs 50` |
| 3 | S4（CPU、CoreML） | `uv run --with torch,transformers,onnx,onnxruntime,pillow python spikes/m0/S4/run.py --images <500 張> --texts spikes/m0/S4/texts.txt --eps CPU,COREML` |
| 4 | S3（Metal，4B） | llama.cpp macOS arm64 版 + Qwen3-VL-4B GGUF；同 3.1 第 8 步 |

### 3.3 回傳

把 `spikes/m0/results/*.json` 與 `fixtures/hw/*.yaml` commit 到 `spike/m0-hardware` 分支並開 PR；或直接在本機跑 GOALS.md 的 `M0-hardware` goal。未通過的項目依 09 §6「不通過時」欄位更新 03／06／08。
