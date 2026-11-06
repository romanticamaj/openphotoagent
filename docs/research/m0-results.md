# M0 Spike 結果與硬體實驗執行指南

> 日期：2026-11-01｜環境：Claude Code 雲端 session（Ubuntu 24.04 容器、4 vCPU、15 GB RAM、**無 GPU**）｜網路政策：Hugging Face、PGDG apt、GitHub release 下載被擋；PyPI、npm、crates.io、GitHub git 協定可用。
> 規則：**只記錄實測值**。推估值只出現在 `metrics.estimates` 並標明。原始結果：[`spikes/m0/results/`](../../spikes/m0/results/)。

## 1. 結論表

| ID | 驗證項 | 狀態 | 已得結果 | 通過標準 | 判定 |
|----|--------|------|----------|----------|------|
| M0-S1a | 嵌入式 PG 啟動 | partial | Linux（PG 16.15）：暖 p95 **0.127 s**、0 失敗。**Windows 11 一般帳號（PG 17.6，Defender 開啟，50 次）**：NVMe 上暖 p95 **0.74 s**、冷 p95 **2.80 s**（max 4.96 s）、**0 失敗**；5400 rpm HDD 且滿載時暖 p95 **6.74 s**（max 22.9 s）、冷 max 217 s、0 失敗。**管理員帳號未跑**（此帳號不是管理員）。只跑 pg_ctl，不含 Tauri 殼 | 暖 p95 < 5 s、0 失敗、Windows 管理員可啟動 | Linux ✅；Windows 一般帳號 NVMe ✅（HDD 滿載 ❌）；管理員 ⏳；macOS ⏳ |
| M0-S1b | kill -9 恢復 | partial | Linux postgres 20 次 kill -9：恢復 p95 **1.56 s**（max 1.79 s）、已提交 1,000 列 **0 遺失** | ≤ 30 s、0 遺失 | postgres ✅；api／worker／llama-server ⏳（未跑：需要可運作的桌面 app） |
| M0-S1c | Windows AF_UNIX | measured | PG 17.6 + **psycopg 10,000 次 0 錯誤**（20 次／秒）；**asyncpg 200 次全失敗**：Windows 不支援（磁碟代號路徑被當主機名、`/` 路徑 `NotImplementedError`）。產品只用 psycopg | 10,000 次 0 錯誤（asyncpg 與 psycopg） | 字面 ❌（asyncpg）；產品實際使用的 psycopg ✅。由 08 §1.3 決定，見 §2.1 |
| M0-S2 | 安裝包大小 | partial | Linux 實測（xz -6）：CPython 3.12 **21.7 MB**、ORT CPU **16.6 MB**、其他 Python 依賴 **56.7 MB**、PG16+pgvector **15.2 MB** → 實測小計 **110.2 MB**；加上未建置組件的 08 §4.1 上限估算（Tauri 15、SPA 5、llama-server 15、GeoNames 15）→ **預估 160 MB** | ≤ 200 MB（目標 150） | 預估在上限內、略超目標；權威量測待 desktop-build（DESK-030） |
| M0-S3／S3b | llama.cpp 結構化輸出、key 順序 | measured | b12121 CUDA 13.4、Qwen3VL-8B Q4_K_M、COCO 1,000 張（約 640×480）：**首次合法 100%**（1,000/1,000，三次皆同）、enum 違規 0、**category 首 token logprob 100%**、0.97 張／秒、p50 0.94 s、VRAM +7.5 GiB。產品提示下準確率（代理資料）caption→category **0.749**、category→caption **0.729**，差 2.0 pp | 首次合法 ≥ 99%、修復後 ≥ 99.9%、logprob 可取得；差 > 2 pp 才改順序 | ✅；維持 caption 優先。準確率為代理值，見 §2.1 |
| M0-S4 | SigLIP2 ONNX 一致性 | measured | 真權重 500 張＋200 條：自行匯出 fp32 在 CPU／DirectML 每向量 cosine ≥ **0.9999997**。公開 fp16：DirectML 影像最小 **0.99980**、45.9 張／秒；CPU 預設最佳化**載入失敗**，關閉最佳化才可用（0.97 張／秒）。**公開 int8 影像 cosine 最小 0.263，不合格** | 每向量 cosine ≥ 0.999 | fp32 ✅、fp16 ✅（CPU 有條件）、int8 ❌ |
| M0-S4b | 繁中 zero-shot | partial | **代理資料**（COCO 600 張、衍生標籤、12 類只有 9 類）：ensemble **0.605**、英文 **0.595**、zh-Hant **0.505**；ECE 0.41／0.37／0.33 | ensemble ≥ 0.80 且不低於英文 3 pp 以上 | 未完整評估（需 `img:by-content-12-large`）；此資料上 0.80 未達、3 pp 達成 |
| M0-S5 | 人臉授權 | partial | YuNet **MIT**、SFace **Apache-2.0**、eDifFIQA(T) **CC-BY-4.0**；AuraFace `glintr100.onnx` 標示 **Apache-2.0**（LICENSE 為未填寫範本）、sha256 `a7933ea5…5c60`、248.6 MiB float32。同 repo 其他 4 個 ONNX 與 InsightFace 非商用權重逐位元相同 | 授權允許 Apache-2.0 散布並商用 | YuNet／SFace／eDifFIQA ✅；AuraFace 只有 `glintr100.onnx` 可用，訓練資料待法律判斷；TAR@FAR ⏳（Q-T1） |
| M0-S6 | 簽章流水線 | blocked | — | notarize 通過 | 需使用者帳號（BLOCKERS B-3、B-4） |
| M0-S7 | tool-calling | measured | Qwen3VL-8B Q4_K_M、`--jinja`：**20/20**、擴充 **100/100**（解析、工具選擇、引數 JSON 皆 100%；10 個題目循環、單輪呼叫）。Qwen3.6-35B-A3B 未跑 | ≥ 95% | 8B ✅；35B ⏳ |
| M0-V7 | ETA 涵蓋率 | pending-hardware | 未跑：需要可運作的桌面 app（修復中） | ≥ 70% | ⏳ |
| M0-V8 | WebKitGTK 能力 | measured | WebKitGTK **2.52.6**（Ubuntu 24.04）：View Transitions ✅、`Intl.DurationFormat` ✅、`createImageBitmap`（含 resize）✅、fetch streaming ✅、EventSource ✅、OffscreenCanvas ✅、container queries ✅、`:has()` ✅；**AVIF 解碼 ❌** | 記錄 | 見 §2 |
| M0-V9 | tauri-driver | pending-hardware | 未跑：需要可運作的桌面 app（修復中） | 10/10 | ⏳ |
| M0-T1 | 測試基建 | partial | (a) template clone p95 **39 ms**（Linux）；(b) HF 候選未測；(c) **Windows 實測**：junction ✅、subst ✅、`FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS` ❌（SetFileAttributesW 回傳成功但屬性未生效）；(d) 由 e2e-smoke 驗證 | (a) < 200 ms；(c) 可行 | (a) Linux ✅；(c) junction／subst ✅，雲端佔位檔改 FakeFS + 手動 |
| M0-T2 | 硬體探針錄製 | partial | 實錄 `cloud-linux-nogpu`、**`rtx5070ti-win11`**（驅動 591.86、CUDA 13.1、cc 12.0、WDDM；已去識別） | 參考機皆有實錄 | 參考機 1 ✅；4060、M2 ⏳ |

## 2.1 參考機 1 實測（2026-11-05，Windows 11、RTX 5070 Ti 16 GB、驅動 591.86、Ryzen 9 9900X、64 GB）

條件：與 gate suite、使用者其他 GPU 程式同時執行；D: 是 5400 rpm SATA HDD，C: 是 NVMe。資料集為公開 COCO val2017，未使用任何個人照片。

1. **llama.cpp**：官方 b12121 `win-cuda-13.4-x64` + cudart 13.4 在驅動 591.86（回報 CUDA 13.1）上直接可用，37/37 層上 GPU，不需 Vulkan。關閉 CUDA 快取後首次請求仍 0.82 s，所以是原生 sm_120 kernel，沒有 JIT。不需要額外旗標；Windows 上 `-ngl 99 -c 8192 --parallel 2` 即可，tool calling 用 `--jinja -c 16384`。VRAM +7,515～7,519 MiB（catalog 估 6.5 GB）。
2. **首次請求 86 s**：不是 llama.cpp 的固定成本。NVMe 上 ready 3～7 s、首次請求 0.82～0.98 s。86 s 只出現在 HDD 滿載時（另一次 16.8 s），載入也從 3 s 變成 110 s 以上；確切是哪些頁面未能分辨。模型放在 HDD 時，程序看起來會像卡死（CPU 0、GPU 0、/health 503）。
3. **S3 的提示**：spike 原始提示沒有列出類別，模型只靠文法看到 enum，大多回答 `other`（準確率 0.243，已註記在 M0-S3）。產品提示有列出（0.749）。**類別一定要寫進提示，只靠 schema 不夠。**
4. **信心**：category 首 token 機率 ≥ 0.99 的 775 張準確率 0.823，其餘 225 張約 0.41～0.57（代理標籤）。logprob 有區辨力，但 0.8～0.95 這段沒有單調性。
5. **代理資料的限制**：COCO 圖比真實照片小（約 330 個 prompt token），所以張／秒偏樂觀，真實解析度未量；標籤由標註衍生，`landscape` 等類別本身有雜訊。
6. **WDDM**：`nvidia-smi -q -x` 的 `<gpu id=...>` 與 `used_memory` = N/A 使 `opagent_doctor.probes` 解析出外部 VRAM 0（實際 10.9 GB）；已回報，未改產品碼。

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


## v0.3 留待真機驗證的項目（2026-11-02）

| 項目 | 雲端已驗證 | 待你的電腦或 GPU runner |
|---|---|---|
| 問照片的本地 LLM | `OpenAICompatLLM`（ASK-LLM-001）以錄製回應驗證 tool call、壞 JSON、逾時、探測 | Worker 啟動 llama-server `--jinja`（S3/S7）。S7 通過前，tool loop 只在探測 3/3 時開啟；本地模式目前只用確定性模板回答 |
| ASK-EVAL-002 全集、ASK-PERF-001 延遲 | 60 題小集（ASK-EVAL-001）：引用有效率 1.0、拒答 1.0、統計 1.0、recall@50 1.0、precision@12 0.85 | 300 題全集與延遲：nightly GPU runner |
| Mini benchmark 真模型 | BENCH-001～003 以 FakeBackend 候選驗證 | Qwen3-VL、SigLIP2 實際分數與 VRAM：在 5070 Ti 上跑 `/benchmarks` |
| Watch folder 的 FSEvents 與 ReadDirectoryChangesW | Linux inotify（WATCH-001～006） | macOS 與 Windows 由 CI OS 矩陣執行 |
