# CLAUDE.md — OpenPhotoAgent 開發指南與 Session Handoff

> 給在這個 repo 工作的 AI coding agent（Claude Code）與人類貢獻者。**每個 session 開始時先讀完本檔**，再依任務讀對應的規格章節。
> 最後更新：2026-11-01（規劃階段完成，進入實作）｜目前版本：`v0.0.1`（Design Preview）｜下一個目標：**M0 spike → `v0.1.0` 桌面 MVP**

---

## 1. 專案一句話

**OpenPhotoAgent**：Apache-2.0 純開源（不收費、沒有商業版）的照片 AI。它在使用者自己的電腦／GPU 上替照片產生 caption、tag、分類，並辨識人臉，**安全地自動整理到指定資料夾**；提供精美的 React viewer、混合搜尋、「AI 問照片」，也透過 **MCP** 讓第三方 AI 搜尋照片。交付形態有兩種：**跨平台桌面執行檔**（Tauri 2 + Python sidecar），以及 Docker 自架。

| Killer 功能 | 版本 |
|---|---|
| ① 照片自動分類到指定資料夾（dry-run → 核准 → 執行 → 可 undo） | **v0.1** |
| ② 人臉找照片 | v0.2 |
| ③ AI 問照片 | v0.3 |

核心能力：模型管理、環境醫生（每個被擋住的操作都要給修復 CTA）、**每個長操作都要顯示預估時間與經過時間**、批次處理、MCP（關鍵字／label／latent space 搜尋）、精美的照片 viewer。

---

## 2. 規格地圖（Source of Truth）

規格都在 `docs/`，以繁體中文撰寫。**當規格之間互相衝突時，優先序是 11 > 08 > 06／07／10 > 00–05**，因為較新的審查會覆蓋較舊的假設。

| 文件 | 內容 | 什麼時候讀 |
|---|---|---|
| `docs/11-feature-coverage-review.md` | **107 條能力的覆蓋矩陣、v0.1 驗收清單（DoD，§5）**、待決事項 | 每次選工作前 |
| `docs/09-tdd-and-test-suite.md` | **TDD 流程、測試替身、CI、M0 實驗、v0.1 切片順序（§7）** | 每次寫測試前 |
| `docs/09a-test-cases-v0.1.md` | v0.1 的 606 條測試案例（ID、輸入、預期、對應能力） | 實作 v0.1 時 |
| `docs/09b-test-cases-v0.2-plus.md` | v0.2 之後的 234 條測試案例 | v0.2 之後 |
| `docs/08-desktop-packaging-and-final-review.md` | **最新決策總表（§0）**、桌面進程模型、嵌入式 PG、runtime 矩陣、runtime pack、安裝與更新、路線圖（§9） | 架構與打包 |
| `docs/02-system-architecture.md` | 資料模型、Job 生命週期、API、Worker 協定、路徑沙箱、output executor、monorepo；**§10 是審查後的增補** | 後端 |
| `docs/03-inference-layer.md` | Model Registry、`InferenceService`／adapter、三種任務策略、VRAM、benchmark；**§10 是增補** | 推論 |
| `docs/06-models-environment-operations.md` | 模型管理、下載管理、環境醫生、**Preflight 與修復 CTA（§4，含錯誤碼目錄）**、**Operation 與 ETA（§5）** | 任何長操作或可能失敗的操作 |
| `docs/04-control-panel-ux.md` | 頁面 wireframe、設計系統（「暗房裡的燈箱」）、前端技術棧；**§8 是增補** | 前端 |
| `docs/10-photo-viewer-and-ask-ai.md` | Viewer（網格、scrubber、Lightbox、比較、地圖）與 AI 問照片 | viewer、Ask |
| `docs/05-search-and-mcp.md` | 混合搜尋、繁中斷詞、MCP server（tools、scope、核准、prompt injection）；**§11 是增補** | 搜尋、MCP |
| `docs/07-face-recognition.md` | 人臉 pipeline、模型與授權、分群、隱私 | v0.2 人臉 |
| `docs/00-master-plan.md` | 總覽與跨文件裁決 | 需要全貌時 |
| `docs/01-prior-art-and-naming.md`、`docs/research/voicebox-study.md` | 競品調查、voicebox 參考（MIT，可借用程式碼，需附 NOTICE） | 參考 |
| `VERSIONING.md`、`CHANGELOG.md` | SemVer、release-please 流程 | 發版 |

---

## 3. 已定案的決策（不要重新討論，除非使用者要求）

| 主題 | 決策 |
|---|---|
| 授權 | Apache-2.0；純開源社群專案，沒有付費版 |
| 名稱 | 產品 OpenPhotoAgent；CLI **`opagent`**；環境變數前綴 **`OPAGENT_`** |
| 交付 | 桌面優先（Windows／macOS arm64／Linux），加上 Docker all-in-one；自架 control plane 在 v0.5 |
| 桌面架構 | Tauri 2（Rust 殼）+ React 19／Vite 前端（同一份程式碼也輸出 Web build）+ Python FastAPI sidecar（api + worker）|
| 資料庫 | **只用 PostgreSQL 17 + pgvector**，桌面版也內嵌；不做 SQLite 雙後端。佇列用 `SKIP LOCKED`，通知用 `LISTEN/NOTIFY` |
| 推論 | **llama.cpp（大型 VLM，GGUF）+ ONNX Runtime（SigLIP2、人臉）** 是跨平台主軸；vLLM／MLX／torch 屬於 v0.4 的進階 runtime pack |
| 預設模型（16GB） | Qwen3-VL-8B GGUF（依 VRAM 分檔）+ SigLIP2 so400m（ONNX）；人臉用 YuNet + AuraFace（Apache／MIT，可隨安裝包散布） |
| 安裝包 | ≤ 200 MB（目標 150 MB），內含 CPU／Metal／DirectML／CoreML 能力；CUDA／ROCm 等以簽章過的 runtime pack 按需下載 |
| 使用模式 | 桌面版預設單人、免登入；API 只綁 `127.0.0.1`，並用每次啟動產生的 launch token 保護 |
| MCP | 桌面版走 stdio（v0.2），HTTP + OAuth 在 v0.5；本機 PAT 預設**不含** `photos:media`；`organize:apply` **永遠不發給** MCP token |
| 整理動作 | 預設**複製**；move 要二次確認；v0.2 新增 `trash`（移到 `.opagent-trash/`，可 undo） |
| 人臉 | 預設關閉，需要明確 opt-in；只在本機計算；非商用授權的權重（InsightFace）只能讓使用者自行同意後下載，**不得打包進 repo 或安裝包** |
| 路線圖 | M0 spike → v0.1 自動分類到資料夾 → v0.2 人臉 + 混合搜尋 + MCP → v0.3 問照片 + watch folder → v0.4 生態 → v0.5 control plane → v1.0 |

---

## 4. 不可違反的不變量（Invariants）

違反任一條，都視為 P0 bug。相關測試**不得隔離或跳過**（見 09 §1.6）。

1. **只有 Worker 能碰原檔與 GPU。** `apps/api` 不得 import `fs-sandbox` 或 `inference-*`，CI 用 import-linter 強制檢查。
2. **路徑一律走 SafePath**（「root 別名 + 相對路徑」）：遇到 `..`、絕對路徑、symlink 逃逸、Windows 保留名一律拒絕。新增 root 只能由使用者在 app 內操作，REST 和 MCP 都不開放。
3. **檔案變更一律走「計畫 → 核准 → journal → 執行」。** move 必須先 copy、驗證 hash，再刪原檔；任何一步當機都要能恢復，而且 **0 個檔案遺失**；每個操作都能 undo。
4. **預設設定下，原檔的內容與 mtime 不變。**
5. **MCP 和 AI 問照片都不能自行套用檔案變更**，只能產生計畫，由人在 UI 核准。
6. **照片中的文字、EXIF、檔名一律視為不可信資料**（防 prompt injection）。
7. **推論快取鍵 = (asset content hash, config hash, model fingerprint)**；門檻不算在 hash 裡，調整門檻不會觸發重新推論。
8. **每個可能失敗的操作都要先跑 preflight**；被擋住時要顯示原因和至少一個修復 CTA。禁止「只有 disabled、沒有理由」的按鈕，ESLint 規則 `opa/no-bare-disabled` 會檢查。
9. **每個長操作都是 Operation**：開始前給預估，執行中顯示 ETA（rough／precise／stalled）、經過時間（扣除暫停時間）和等待原因；狀態要持久化，重啟後仍看得到。
10. **只接受 `safetensors`／`gguf`／`onnx` 權重**；拒絕 pickle（`.bin`），`trust_remote_code` 必須經過明確確認。
11. 測試與 `packages/core` 裡**禁止直接讀取時間**，例如 `time.time()`、`datetime.now()`、`sleep`；一律使用 `Clock` 介面或 FakeClock。

---

## 5. 開發流程：嚴格 TDD

完整規則見 `docs/09-tdd-and-test-suite.md` §1。摘要如下：

1. **選題**：依 09 §7 的切片順序，一次只取 ≤ 10 個測試 ID，範圍限一個能力。
2. **紅**：先寫測試，標註 `@pytest.mark.tc("FS-012")` 或 `it("[VIEW-005] …")`。執行後必須**失敗在預期的斷言上**；ImportError 或 fixture 錯誤都不算紅。
3. **綠**：只寫讓測試通過的最小實作，不要順便做還沒有測試的行為。
4. **重構**：保持全綠。改到 `fs-sandbox` 或 `path_template` 時，要在本機跑 `just mutate-diff`，score 需 ≥ 85%。
5. **狀態**：執行 `tools/tc_status.py`，更新 `docs/generated/coverage-status.md`。
6. **Commit**：使用 Conventional Commits，scope 用套件名。`test:` 必須早於或同時於對應的 `feat:`／`fix:`。footer 必填 `Tests: <IDs>`，並寫 `Refs: 11#<能力編號>`。
7. **禁止**：刪除測試、加 skip／xfail／`.only`、把斷言改弱、修改 golden 去配合錯誤的輸出。
8. **規格衝突、做不到的門檻或設計取捨：開 sub-agent 討論。** 用 Agent 工具開獨立的審議 sub-agent（影響大的事項開兩個，各自論證不同選項），只給它問題、規格段落與程式碼位置，不給傾向結論。依論證做決定，以 ADR 格式記到 `docs/DECISIONS.md`，並同步修改規格。只有必須由使用者本人決定的事（金錢、法律、帳號與簽章憑證、資料授權來源的最終確認、發版）才記到 `docs/BLOCKERS.md`，並在 PR 描述列出。**任何結論都不能用來修改測試去配合實作，也不能弱化 §4 的不變量；有違反不變量的風險時，停下來回報使用者。**
9. **修 bug 的順序**：先新增一條能重現問題的回歸測試（新 ID，標「回歸」），再修。

覆蓋率門檻：`core`／`fs-sandbox` 的 branch coverage ≥ 95%，其餘 ≥ 80%；diff coverage ≥ 90%。

---

## 6. Monorepo 結構與指令（在切片 0 建立）

```
openphotoagent/
├─ apps/
│  ├─ web/          React 19 + Vite + TanStack (Router/Query/Virtual) + shadcn/ui + Tailwind v4
│  ├─ api/          FastAPI（REST /api/v1、SSE、之後的 /mcp）
│  ├─ worker/       scanner、preprocess、GPU 排程、output executor
│  ├─ cli/          opagent（login / mcp / doctor / models …）
│  └─ desktop/      Tauri 2 殼（src-tauri, Rust）：sidecar 監管、單一實例、asset protocol、updater
├─ packages/
│  ├─ core/         純函式：path_template、config_hash、decide、狀態機、規則 DSL（無 I/O）
│  ├─ schemas/      Pydantic DTO → OpenAPI / JSON Schema / TS 型別
│  ├─ db/           SQLAlchemy + Alembic；embedded PG 管理
│  ├─ fs-sandbox/   SafePath、檔案操作原語、journal
│  ├─ inference-sdk/ InferenceBackend SPI、FakeBackend、一致性測試套件
│  ├─ inference-adapters/ llama.cpp server、ONNX Runtime（SigLIP2、faces）
│  ├─ operations/   Operation、ETA 估算、Activity 事件
│  ├─ doctor/       環境檢查 registry、preflight、錯誤碼目錄
│  ├─ search/ mcp-tools/ geo/ output-actions/ integrations/
│  └─ ts-client/    由 OpenAPI 產生的 TS client
├─ tools/           tc_status.py、pytest_tc/、codegen、release
├─ spikes/m0/       M0 實驗腳本與結果
├─ fixtures/        CC0 測試影像、hw profiles（fixtures/hw/*.yaml）
├─ deploy/          compose/、docker/
└─ docs/
```

工具鏈：Python 用 **uv workspace**（ruff、mypy strict for core/fs-sandbox、pytest + hypothesis + pytest-randomly + pytest-socket）；TS 用 **pnpm**（Vitest、fast-check、Playwright、axe）；Rust 用 cargo；任務入口統一用 **`just`**。

切片 0 完成後，下列指令必須可用。實際指令以 `justfile` 為準，有變動時同步更新本節：

```bash
just setup          # uv sync + pnpm install + 下載測試用嵌入式 PG
just test           # unit + property + contract（快速，PR 必跑）
just test-int       # integration（真 Postgres）
just e2e            # Playwright（web build + FakeBackend）
just lint           # ruff + mypy + eslint + tsc + import-linter
just tc-status      # 更新 docs/generated/coverage-status.md
just mutate-diff    # mutation testing（fs-sandbox / path_template）
just dev            # all-in-one 開發模式（api + worker + PG + web）
```

---

## 7. 執行環境注意事項

- **雲端 session 沒有 GPU、Windows 或 macOS。** 推論一律使用 `FakeInferenceBackend`（決定性輸出，可注入 OOM／timeout／parse error），硬體則用 `fixtures/hw/*.yaml` 的探針 profile 模擬，例如 5070 Ti、M2 16GB、無 GPU、驅動過舊。
- **真模型與真硬體的測試**只在 nightly GPU runner 或使用者本機（RTX 5070 Ti 16GB, Windows 11）執行。需要硬體的 M0 實驗見 09 §6：S1 嵌入式 PG（Windows／Defender）、S3 llama.cpp Qwen3-VL 結構化輸出、S4 SigLIP2 ONNX、S7 tool-calling、V7 ETA 涵蓋率。**不要捏造實驗結果。** 沒有硬體可跑的實驗，在 `spikes/m0/results/` 標為 `pending-hardware`，並寫好可直接執行的腳本與說明。
- macOS／Windows 安裝包的建置與簽章由 GitHub Actions matrix 負責，本地無法驗證時以 CI 結果為準。
- 雲端網路政策：Hugging Face、PGDG apt、GitHub release 下載被擋；PyPI、npm、crates.io、GitHub git 協定可用。雲端只有 Ubuntu 的 **PG 16 + pgvector 0.6**（`apt install postgresql-16-pgvector`），整合測試的 `embedded_pg` 以 `OPAGENT_PG_BIN` 找二進位；CI 用 PG 17。程式碼不得使用 PG 17 專屬語法。`just` 以 `uv tool install rust-just` 安裝。
- 這個雲端 session 不能 push tag、建立 Release 或觸發 workflow。發版走 release-please（合併 Release PR）；需要手動操作時，請使用者處理。

---

## 8. Session Handoff：目前狀態與下一步

### 已完成
- 規劃文件 00–11（含 09a／09b，共 840 條測試案例），v0.0.1 Design Preview 已發佈。
- Repo 基礎設施：雙語 README、Apache-2.0、CONTRIBUTING、SECURITY、issue templates、VERSIONING、CHANGELOG、release-please、release-on-tag workflow。
- 規格分支：`claude/spec-expansion`（包含 06–11 與本檔）。**實作請從這個分支開始**，或先把它合併回 `main`。

### 下一步（依序）
1. **切片 0：測試基建**（09 §7）。同時完成 09 §9 的規格回寫：
   - W-1：06 §4.7 的錯誤碼目錄新增 6 個錯誤碼：`PACK_SIGNATURE_INVALID`、`PACK_INCOMPATIBLE`、`FS_FILE_LOCKED`、`DL_REMOTE_CHANGED`、`DL_MIRROR_FAILED`、`VRAM_OFFLOAD`。
   - W-2：02 §3.2 `output_rule` 新增 `{date:YYYY}` 別名與 `{category_key}`，`action` 新增 `trash`。
   - W-3：05 §3.2.3 規定 `tools/list` **一律依 token scope 過濾**（已採納，適用所有 tools）。
   - W-4：08 C-27 的 workflow 清單加入 `nightly.yml`、`nightly-gpu.yml`、`release-gate.yml`、`m0-spike.yml`。
   - W-5：11 §1 連到 `docs/generated/coverage-status.md`。
   - W-6：CONTRIBUTING 加入 09 §1.3–1.5，並新增 `.github/pull_request_template.md`。
2. **M0 spike 腳本**（`spikes/m0/`）：寫好全部實驗腳本。能在雲端跑的（M0-T1 部分、S2 打包大小的 Linux 部分、V8）就實際執行；需要硬體的標記 `pending-hardware`，並在 `docs/research/m0-results.md` 寫給使用者的執行步驟。
3. **切片 1：Walking skeleton**（依日期複製 + undo），終點是 **E2E-B-001** 在 web build 綠燈。
4. 切片 2 → 12，依 09 §7 的順序。切片 7（推論）和 11（桌面殼）依賴 M0 結果：結果還沒出來時，先以 FakeBackend 或抽象介面完成，真實 adapter 保持在可切換狀態。
5. 每完成一個切片：更新 coverage-status，在本檔 §8 追加一行進度紀錄，然後 commit、push。

> **各里程碑可直接貼上的 `/goal` 指令見 [`docs/GOALS.md`](docs/GOALS.md)**（M0、v0.1-A～D、v0.2-A～C、v0.3～v1.0）。

### 進度紀錄（每個切片完成後追加）
| 日期 | 切片 | 狀態 | 備註 |
|---|---|---|---|
| 2026-11-01 | 規劃 | ✅ | 規格 00–11、測試 840 條、v0.0.1 |
| 2026-11-01 | M0-cloud（`spike/m0`） | ✅ | W-1～W-6 回寫；17 個結果檔（S1a/S1b/T1a/V8 實測、S2/S4-pipeline/S5 部分、其餘 pending-hardware）；`docs/research/m0-results.md`、`docs/BLOCKERS.md`、`docs/DECISIONS.md` |
| 2026-11-01 | 切片 0（`feat/v0.1-a`） | ✅ | uv＋pnpm workspace、justfile、ci.yml（PR 必跑 job）、pytest-opagent-tc、tc_status.py、Vitest／Playwright tc reporter、FakeClock、sandbox_tree、embedded_pg、FakeBackend、HF 伺服器骨架；PG-013、FS-026、INF-CONF-001～004、PF-079、I18N-001 綠 |
| 2026-11-01 | 切片 1 walking skeleton | ✅ | CORE-TPL-001/004、CORE-SM-001、FS-001～003/017、SCAN-001/003/013、OUT-JRN-001、OUT-EXE-001/002、OUT-UNDO-001、ORG-001、**E2E-B-001** 綠；`opagent dev` all-in-one；Clock／FsOps／InferenceBackend／OperationReporter 介面就位 |
| 2026-11-01 | 切片 2 core（`feat/v0.1-b`） | ✅ | CORE-TPL-001～025（含 TS 鏡像 golden 245 筆）、CORE-HASH-001～012、CORE-THR-001～017、CORE-SM-001～011、CORE-RULE-001～010；core branch 99%；path_template mutation 96.1%（等價 mutant 見 docs/mutation-equivalents.md） |
| 2026-11-01 | 切片 3 fs-sandbox | ✅ | FS-001～026（FS-013 Windows、FS-015 macOS/Windows 由 CI OS 矩陣執行）；FS-024 fuzz 抓到超長檔名 bug 並修正 |
| 2026-11-01 | 切片 4 executor | ✅ | OUT-EXE-001～023、OUT-JRN-001～005、OUT-CRASH-001～012（012 nightly，本機實跑 10,000 actions × 50 次 kill -9 通過）、OUT-UNDO-001～009；fs-sandbox branch 98% |
| 2026-11-01 | 切片 5 scanner | ✅ | SCAN-001～025 全綠（SCAN-006／019／020 在切片 6 補完）；RAW fixtures 為合成 CC0 容器（raw.pixls.us 無法連線） |
| 2026-11-01 | 切片 6 queue／Operation／ETA | ✅ | Q-001～024、026～028、OP-001～005、007～011、013～017、ETA-001～018 綠；ADR-0001（門檻與 cascade 分工）→ 新增 CORE-THR-018／019；Q-025 依賴切片 7 cascade 移至 1C；OP-006／012（E2E）隨切片 10 UI；ETA-019 需參考機 |
| 2026-11-01 | 切片 7 推論（`feat/v0.1-c`） | ✅ | INF-REG／RT／VRAM／CAL／CAS／JSON／CONF／OOM、ORG-002～004／007～010、REV-001／003／004、Q-025 綠；ADR-0002（registry catalog／lock 分離，B-10）；新套件 registry、doctor、inference-adapters（llama.cpp 經 FakeRuntime、ORT 用 tiny ONNX）；hw profiles 為 synthesized-from-docs；INF-REG-013 屬 release gate；INF-CAL-003／ORG-005 nightly；ORG-006 待 fs-sandbox mutation 修補合併後做；REV-002／005 隨切片 10 E2E |

| 2026-11-01 | 切片 8 模型／下載／doctor／preflight／pack | ✅ | DL-001～018、020～022（DL-019 nightly）、MDL-001～004／007～012（MDL-005／006 隨切片 10 UI）、DOC-001～019（DOC-020 真機）、PF-001～066 v0.1 列與 PF-070～076／078／081～083（PF-077／080 E2E）、PACK-001～011、DIAG-001～003；新增 06 §4.7 #70／#71；錯誤碼目錄與 i18n 由 tools/gen_* 產生並在測試中比對 |
| 2026-11-01 | 切片 9 搜尋 | ✅ | SRCH-TOK-001～008、SRCH-FAC-001～004、SRCH-SEM-001～006（SEM-001 真 SigLIP2 recall 為 nightly）、PF-053／054；新套件 opagent_search（jieba 經簡體影子斷詞＋OpenCC＋bigram＋tag key）；tsvector 以 array_to_tsvector 存原樣詞位；雲端 pgvector 0.6 無 halfvec → 精確掃描，PG17 的 HNSW 隨 1D |
| 2026-11-01 | 切片 10 viewer 與整理 UX | ✅ | VIEW-001/004～009/011、E2E-A-001～005、E2E-B-001～010、E2E-L-001～003、E2E-S-001～003、E2E-SEL-001/002、REV-002/005、MDL-005/006、PF-077/080/084、OP-006/012/018、ACT-004/006、A11Y-001～006、VIS-001～003、I18N-002/004/005、ORG-006、SRCH-FAC-005、DL-023 綠；ADR-0004（等待中可暫停／取消）、ADR-0005（搜尋結果集合 vs 排序）、ADR-0003 補充；E2E stack（`opagent_testing.e2e_stack`，每測試 reset 情境）；Storybook 10 + 字型釘選的視覺基準；VIEW-002/003/010、PERF-* 屬切片 12 |
| 2026-11-02 | 切片 11 桌面殼（`feat/v0.1-d`） | ✅（D/M 待實機） | `opagent-supervisor`（Rust）：重啟退避、exit code 分類、資料目錄鎖、孤兒防護、updater 規則（DESK-002/003/004/006/008、UPD-003/004/005 以 cargo test）；Tauri 殼（`src-tauri`，只在 CI 編譯）注入 `__OPA_PLATFORM__`、系統匣、OS 通知；桌面 PG（socket-only、profile、stale pid、Alembic migration 前備份、拒絕降級、每日備份、立即備份／還原、`pg-prepare` 由殼持有 postmaster）；API／worker 的 stdin token、parent-pipe EOF、區網模式強制登入、`/trash/summary`、`/operations/pause-all`、電池暫停；DESK-035（run file 先於 listen）與 I18N-007（修復按鈕原始 key）兩條回歸；workflow：desktop-build／nightly-desktop／docker／release-gate／nightly／nightly-gpu；README 快速開始、`docs/checklists/release-v0.1.md` |
| 2026-11-02 | 切片 12 效能與 release gate | ✅（PERF-001 待參考 runner） | `just perf`：VIEW-002/003/010、PERF-002/003/006 綠；PERF-001 雲端 p95 16.8 ms（0% 掉幀，門檻 16.7 ms 以參考規格 runner 為準）；ADR-0006（PERF-003 量每路由首次 JS）；路由 code-split、字型不內嵌、閒置後預取；nightly 本機實跑 PG-010、ORG-005（10,000 張原檔不變）、PERF-004（139 files/s 基準）；e2e stack 與 embedded_pg 的 /tmp 洩漏已修正（既有殘留見 B-14） |
| 2026-11-02 | v0.2-A（`feat/v0.2-a`） | ✅（RRF-008／009 待 CI／2B） | 規則式 planner（SRCH-PLAN-001～018；ADR-0008 過年範圍；LLM planner RPC 1.5 s 退回規則）、`opagent_geo`、`opagent_core.lunar`、RRF／短語加權／重複折疊（RRF-001～007）、以圖搜圖（IMG-001～003，上傳只在記憶體）、重複照片（DUP-001～004，union-find＋多索引雜湊）與 `/duplicates` 頁、一鍵清理計畫（TRASH-005）、`trash` 動作（TRASH-001～008）、自動核准驗證（TRASH-007）、XMP sidecar（XMP-001/003～006，defusedxml）。地圖／比較模式依 11 為 v0.4；SRCH-RRF-008 需 pgvector 0.8（CI）；RRF-009 隨 2B MCP；XMP-002 為手動。fs-sandbox mutation 94.0%；回歸 SRCH-PLAN-019（只有停用詞的查詢）、MDL-013（方案卡載入中徽章）；e2e reset 在 TRUNCATE 死結時重試 |
| 2026-11-02 | v0.2-B（`feat/v0.2-b`） | ✅（實機 client 與 agent 迴圈待驗證，B-15／B-16） | `packages/mcp-tools`（27 個 tool 定義、scope gate、UntrustedText 淨化、簽章 cursor／selection、token bucket、credstore）；`/mcp/v1/rpc` JSON-RPC（2026-07-28／2025-11-25、PAT、append-only `mcp_call_log`、approval 30 分鐘＋plan_hash、資料夾範圍、media 取最嚴格）；`opagent mcp`（stdio 薄 relay、重讀 run/api.json）與 `opagent login`；「連接 AI 助手」頁、精靈（diff 後寫入＋備份）、核准頁與請求卡；MCP-SCOPE／FOLD／MEDIA／APPR／RATE／AUD／INJ（確定性）／TOOL（19 個 v0.2 tool）／TRN／PAT 綠；ADR-0009（v0.3 tool 以 available_from 隱藏、-32601）、ADR-0010（PathRef）；06 §4.7 新增 #74～#93；回歸 SRCH-TOK-009（查詢含 NUL）；SRCH-RRF-009 綠；MCP-RATE-002 隨 describe_photo 移到 v0.3 |
| 2026-11-02 | v0.2-C（`feat/v0.2-c`） | 🟡 實作完成，完整 test-int／e2e 受磁碟限制未跑完（B-14） | `opagent_core.faces`（品質分級、線上指派、Jaccard 穩定 ID、合併建議、找人分級、Chinese Whispers）；FakeFaceBackend；`bio` schema（opagent_bio，API 以 opagent_api 連線，ADR-0011）；Worker 人臉步驟共用解碼、query 向量只在記憶體、face_purge 先執行並銷毀工作區金鑰（ADR-0012）；HDBSCAN 群組、命名／合併可復原／拆分／忽略兩種、cannot-link、Review Y/N/S、找這個人、拖照片找人（10 分鐘 handle）、`{person}` fan-out、一鍵 purge、備份排除 bio、換模型藍綠、消失 30 天清理；REST persons 篩選與 MCP 人物工具；registry：YuNet＋AuraFace 隨安裝包、SFace、InsightFace 僅同意後下載、禁止遠端 runtime；People 頁、opt-in 卡片與同意精靈、停用預設刪除；RESPONSIBLE_USE.md；FACE-N-001 需真模型與授權資料（B-2） |
| 2026-11-02 | v0.3（`feat/v0.3`） | ✅（真模型與真機項目待驗證，見 m0-results v0.3 節） | `packages/assistant`（grounding、Budget、memory、FakeLLM、OpenAICompatLLM 與 tool-call 探測）；`/api/v1/ask` SSE、共用 ToolRegistry（actor builtin-assistant）、計畫卡、雲端隱私（https、keyring、egress log）、保存與刪除；ASK-EVAL-001 60 題小集（找出並修正：loop 丟棄最終回答、檢索與比較的數字未檢查、單張問答未讀 metadata、部分符合誤報找到）；問 AI 面板、Lightbox「問這張」、建議 chips、助手設定頁；ADR-0013（⌘I 給問 AI，⌘J 留給活動中心，兩面板互斥）；watch folder（watchdog、網路磁碟輪詢、每來源一個掃描、計畫取代）；mini benchmark（覆蓋率–準確率曲線、一鍵套用 τ 不重算）；preset 匯出與匯入（schema_version 2、YAML 不接受 tag 與 alias）；v0.3 MCP tools、GPU 預算 600 s/天；06 §4.7 新增 #100～#106；回歸 MCP-FOLD-006 與 MCP-TOOL-028（sub_path 來源的路徑重複）|
| 2026-11-02 | v0.4（`feat/v0.4`） | ✅（真 runtime、真模型與上架待驗證：B-17、B-18、nightly GPU） | 先細化 09b §7 為 31 條（docs commit），再實作：MLX／vLLM／torch pack 的 registry 與平台過濾、分段下載可續傳、可重現的 pack 建置與 `packs.yml`、vLLM 與 MLX adapter（假 runtime 的 flavor 通過 INF-CONF）、torch sidecar 與 Blackwell 自檢、`trust_remote_code` 綁定檔案 hash 的確認（06 §4.7 #107）；匯入 Ollama 與資料夾模型（不下載、不複製、拒絕 pickle）；Immich 回寫（以 SHA-1 對照，journal 後套用，可 undo，#108／#109）；Label Studio 匯出與匯回；ADR-0014（caption 向量預設 Qwen3-Embedding-0.6B，備援 e5-small）與 R2 路、藍綠 reindex；winget、Homebrew、Flathub manifest 產生器；發佈管道與 Windows ML 評估；離線地圖（MapLibre 無圖磚模式）與比較模式。回歸 MCP-FOLD-006／MCP-TOOL-028 已於 v0.3 修正 |
| 2026-11-03 | v0.5（`feat/v0.5`） | ✅（實機部署、真 IdP、真 MCP client、compose nightly 待驗證：B-19） | 先細化 09b §8（docs commit）；C-21 由使用者定案為內建 AS（ADR-0015）；ADR-0016 每個 workspace 一個資料庫（兩個 sub-agent 審議）。Worker 配對（OPA-XXXX-XXXX、ed25519 簽 `worker_id|ts|nonce`、15 分鐘 aud=worker JWT、nonce 單次、撤銷即斷）、WSS `/connect` + `GET /poll` 降級、`welcome` 協商 N/N-1、RPC 白名單；四級 egress 由 Worker 端 `EgressFilter` 執行（本機 floor 不可被 CP 放寬、results-only 路徑 hash 與本機對照表、edge-thumbs 中繼不落地）；`opagent worker enroll/roots/run --headless`（不開 port）；RBAC 六角色 × permission、所有寫入路由必須宣告權限、MCP 有效 scope = token ∩ 角色；密碼與 OIDC 登入（假 IdP、金鑰輪替）、單人升級；Authlib OAuth 2.1 AS（S256、CIMD SSRF 防護、refresh 輪替與重用撤銷、RFC 9728/8414、`/mcp`）；`deploy/compose/controlplane.yml`、`worker.yml`、`opagent controlplane`；Web 登入頁與 workspace 切換；桌面 `connection.json` 遠端模式（cargo test）；`docs/deploy/controlplane.md` |
| 2026-11-03 | v1.0（`feat/v1.0`） | ✅（外部審查與發版待使用者：B-20） | 先細化 09b §9 為 21 條並新增 11 #114～#116（docs commit）。介面凍結：`contracts/` 六份快照與 `opagent_schemas.compat` 比對器，`just contracts` 已加入 lint（FRZ-001～007）；ADR-0017 協定版本維持 `"MAJOR.MINOR"`、服務 N 與 N-1，並修正 VERSIONING §4。升級：修正 v0.x 安裝永遠不會補上新表的問題（`frozen_v1_0` 加上 `0002` 補齊 migration，以各里程碑真實的 schema 測試）、`layout_version`、`DATA_LAYOUT_TOO_NEW`（#110）、CHANGELOG 與 commit 的 `Migration:` 檢查（MIG-001～003）。安全：沙箱 fuzz 1M 例 0 逃逸、MCP 紅隊 110 條、可驗證的人臉刪除、SBOM 與漏洞閘門；掃描發現 maplibre-gl 的 Critical 漏洞，已升級到 6.16.0（SEC-*）。i18n 與 a11y：雙語完整性與修復選項代價（修正標題截斷、單人版用語、缺少的按鈕文字）；每個路由都跑 axe；只用鍵盤的流程；問 AI 面板可用 Esc 關閉；搜尋結果改為鍵盤網格並可開 Lightbox（I18N-010/011、A11Y-010/011）。stable 與 beta 通道（UPD-010）；`docs/checklists/release-v1.0.md`（REL-010）、`docs/security/review-v1.0.md`、`docs/upgrading-to-1.0.md` |

### 仍待決定／待驗證（可用 sub-agent 討論的依 §5.8 處理；屬於使用者本人決定的記到 `docs/BLOCKERS.md`）
- 人臉測試影像的授權來源（09 Q-T1）；中文日期表達的精確範圍，例如「過年」（Q-T5）。
- Windows 程式碼簽章經費與 SignPath 申請時程（08）；人臉功能的法規需要有經驗的人審閱。
- 11 §4 列出的其他待驗證項；所有「待 M0 確認」的門檻。

---

## 9. Git 與發版

- 分支命名：`feat/<slice>-<topic>`、`fix/…`、`spike/m0-…`；PR 合併方式用 squash，**PR 標題就是 Conventional Commit**，release-please 依此產生 CHANGELOG。
- 不要直接 push 到 `main`；不要改寫別人分支的歷史。
- commit 結尾附上 session 提供的 attribution 行。
- 版本規則：v0.0.x 任何變更都升 patch；0.x 的破壞性變更升 minor。`v0.1.0` 的發版門檻是 11 §5 的 DoD 全綠，加上 09 的 release gate。
