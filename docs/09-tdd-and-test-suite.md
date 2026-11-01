# 09 — TDD 流程與測試套件（TDD Workflow & Test Suite）

> 狀態：Draft v0.1（2026-11-01）｜角色：測試架構師
> 範圍：OpenPhotoAgent 從 M0 到 v1.0 的測試策略、TDD 工作流程、測試替身、CI 設計、M0 spike 驗證，以及完整的測試案例目錄。案例本身放在兩份附件：
> - [09a — v0.1 測試案例目錄](09a-test-cases-v0.1.md)（606 條，v0.1 最詳細）
> - [09b — v0.2 以後的測試案例目錄](09b-test-cases-v0.2-plus.md)（234 條）
>
> 輸入：[11 §1 功能覆蓋矩陣與 §5 v0.1 DoD](11-feature-coverage-review.md)（本文所有案例都標註 11 的能力編號 `#n`）、02 §4/§6/§7/§10、03 §2/§3/§4/§9/§10、05 §2/§3/§11、06 §1–§5、07、08 §1–§5/§9–§11、10 §5/§8–§11、04 §1/§8。
> 已定案的前提：Apache-2.0；桌面（Tauri 2 + React 19/Vite + Python FastAPI sidecar + 嵌入式 PostgreSQL 17 + pgvector）＋ Docker；推論 llama.cpp + ONNX Runtime；CLI `opagent`；Python uv workspace（ruff、mypy、pytest）、TS pnpm（Vitest、Playwright）。
> 「待 M0 確認」= 測試結構現在就要寫，門檻數值等 spike 量到後回填（§6）。

---

## 0. TL;DR

| 項目 | 決定 |
|------|------|
| 開發方式 | **嚴格 TDD**：每個功能從 11 的驗收標準出發 → 在 09a／09b 找到測試 ID → 先寫失敗測試（紅，且失敗原因正確）→ 最小實作（綠）→ 重構 → 重新產生覆蓋狀態檔。AI coding agent 與人類用同一套規則（§1） |
| 測試 ID | `<模組>-<子模組>-<三位數>`（`FS-012`、`MCP-SCOPE-007`）；測試程式以 `@pytest.mark.tc("FS-012")`／`it("[FS-012] …")` 標註；CI 檢查每個已實作功能的 ID 都有對應測試 |
| 案例數 | **840 條**：v0.1 **601**（另 1 條選用）、v0.2 173、v0.3 52、v0.4 6、v0.5 6、v1.0 1（§8） |
| 金字塔 | unit 251 · integration 311（含 80 條 preflight 錯誤碼目錄）· contract 76 · security 52 · E2E 48 · chaos 27 · 桌面煙霧 22 · property 15（另有大量案例以 property 為次要層級）· perf 11 · nightly 真模型 9 · a11y 9 · visual 6 · 手動 3 |
| 替身 | FakeInferenceBackend（決定性、可注入 OOM／timeout／parse error／crash）、FakeClock、FakeFS／FakeDisk／tmp 沙箱、FakeGPU 探針（14 個硬體 profile）、錄製的 HF 下載伺服器（12 個情境）、FakeLLM、FakeFaceBackend、CC0 golden 影像；**真模型只在 nightly GPU runner** |
| CI | PR 必跑：lint、type、unit、property（CI profile）、contract、Linux integration（嵌入式 PG）、OS 矩陣（Windows／macOS／Linux：core、fs-sandbox、executor、嵌入式 PG）、E2E 煙霧；nightly：E2E 全套、visual、a11y、chaos、fuzz 1M、perf、mutation、桌面煙霧；nightly-GPU：真模型與 benchmark 回歸；release gate：11 §5 DoD 全綠 |
| 門檻 | branch coverage：`packages/core`、`packages/fs-sandbox` ≥ 95%，其餘 ≥ 80%；PR diff coverage ≥ 90%；mutation score（mutmut／Stryker，fs-sandbox 與 path_template）≥ 85% |
| v0.1 切片 | 0 測試基建 → **1 walking skeleton（依日期複製 + undo，端到端）** → 2 core → 3 fs-sandbox → 4 executor → 5 scanner → 6 queue／Operation／ETA → 7 推論 → 8 模型／下載／doctor／preflight／pack → 9 搜尋 → 10 viewer 與整理 UX → 11 桌面殼與打包 → 12 強化與 release gate（§7） |

---

## 1. TDD 工作流程（AI agent 與人類共用）

### 1.1 每個功能的循環

```mermaid
flowchart LR
  A[11 §1 能力 #n<br/>與 §5 DoD] --> B[09a/09b 找到測試 ID<br/>沒有就先補案例到文件]
  B --> C[寫測試<br/>@tc 標註 ID]
  C --> D{執行：紅?}
  D -- 綠（太早通過）--> C2[檢查測試是否真的有測到<br/>改寫斷言]
  C2 --> C
  D -- 紅，但原因是 import／fixture 錯 --> C
  D -- 紅，原因是預期斷言 --> E[最小實作]
  E --> F{綠?}
  F -- 否 --> E
  F -- 是 --> G[重構<br/>保持綠；fs-sandbox／path_template 跑 mutation]
  G --> H[tools/tc_status.py<br/>重新產生覆蓋狀態]
  H --> I[commit + PR 檢查清單]
```

| 步驟 | 規則 |
|------|------|
| 1. 選題 | 依 §7 的切片順序取一個（或一小批，≤ 10 條）測試 ID。每次只處理一個能力的範圍。 |
| 2. 找規格 | 讀 ID 所在列的「前置／輸入」「預期」，以及 `#n` 指向的 11 矩陣列和原始規格章節。**案例與規格衝突時，停下來**：在 PR 描述與對應文件標註衝突，交給人類決定；不要自己改測試去配合實作。 |
| 3. 紅 | 寫測試並執行，確認它失敗，**而且失敗在預期的斷言上**（不是 `ImportError`、fixture 錯誤或語法錯誤）。AI agent 必須在 PR 描述貼出紅燈時的失敗摘要（測試名稱 + 斷言訊息一行）。 |
| 4. 綠 | 寫讓測試通過的最小實作；不要順便實作還沒有測試的行為。 |
| 5. 重構 | 在全綠狀態下整理程式碼；重構 commit 不得修改測試的斷言。`packages/fs-sandbox` 與 `packages/core/path_template` 的變更要在本機跑一次 `just mutate-diff`。 |
| 6. 狀態 | 執行 `tools/tc_status.py`，重新產生 `docs/generated/coverage-status.md`（每個 11 能力 → 測試 ID → 狀態：`未開始／紅／綠／部分`）。CI 會重新產生並比對，未更新就失敗。11 §1 矩陣的「驗收標準」欄因此有可追蹤的測試狀態。 |

### 1.2 測試 ID 標註與追蹤

- Python：`@pytest.mark.tc("FS-012")`，可多個；參數化測試用 `pytest.param(..., marks=pytest.mark.tc("PF-037"))`。函式命名 `test_fs_012_symlink_middle_segment_rejected`。
- TypeScript：`it("[VIEW-005] scrubber 定位 < 100 ms", …)`；Playwright 用 `test("[E2E-B-001] …", { tag: ["@critical"] }, …)`。
- Rust（桌面殼）：`#[test] fn desk_009_stale_postmaster_pid()`，在 doc comment 寫 `/// tc: DESK-009`。
- 外掛 `pytest-opagent-tc`（`tools/pytest_tc/`）與 Vitest reporter 會輸出 `tc-results.json`；`tools/tc_status.py` 合併三種語言結果、解析 09a／09b 的表格，產生狀態檔，並在 `--gate=v0.1` 時要求「09a 所有 v0.1 ID 都至少有一個通過的測試」（release gate 使用；平時只做增量檢查：PR 新增或修改的 ID 必須有測試）。
- 一個 ID 可以對應多個測試（例如 PF-ASSERT 的 6 個斷言）；一個測試也可以標多個 ID。手動（`M`）案例記錄在 `docs/checklists/release-v0.X.md`，由 release 負責人勾選。

### 1.3 Commit 規範（Conventional Commits，延續 CONTRIBUTING）

| 規則 | 內容 |
|------|------|
| 類型 | `test:`、`feat:`、`fix:`、`refactor:`、`perf:`、`docs:`、`build:`、`ci:`、`chore:`；scope 用套件名：`core`、`fs-sandbox`、`worker`、`api`、`inference`、`web`、`viewer`、`desktop`、`mcp`、`packaging` |
| 順序 | 同一 PR 中，**`test:` commit 必須先於對應的 `feat:`／`fix:` commit，或兩者在同一個 commit**。CI `tdd-order` 檢查：PR 內每個修改 `src` 的 `feat`／`fix` commit，在它之前（含自己）必須有修改 `tests/` 或 `*.test.ts` 的 commit（啟發式；`docs`／`ci`／`refactor` 不檢查） |
| footer | `Tests: FS-003, FS-004, FS-005`（必填於 `test`／`feat`／`fix`）；`Refs: 11#38`（能力編號）；golden 變更另加 `Golden-Update: <原因>` |
| fix 先寫回歸測試 | 每個 bug fix 先新增一條能重現 bug 的測試（新 ID，追加在對應模組最後，標「回歸」），再修 |
| squash | PR 標題成為 squash commit（release-please 依此產生 CHANGELOG）；PR 標題用主要的 `feat:`／`fix:` |

### 1.4 PR 檢查清單（`.github/pull_request_template.md`）

```markdown
## 變更
- 能力：11 #__（DoD __）
- 測試 ID：____（新增 __／修改 __）

## TDD
- [ ] 先寫的測試在實作前是紅燈，失敗原因是預期斷言（貼一行失敗摘要）：
- [ ] 沒有刪除、跳過（skip／xfail／only）、放寬任何既有測試或門檻；若有，已在下方說明並取得 reviewer 明確同意
- [ ] 沒有修改 golden 檔；若有，commit footer 有 `Golden-Update:` 並說明原因
- [ ] 已執行 `tools/tc_status.py` 並提交 `docs/generated/coverage-status.md`

## 安全與資料
- [ ] 會移動或刪除檔案的程式碼走 plan → approve → journal → execute（02 §6.2），並有 OUT-CRASH 類案例
- [ ] 新的錯誤狀態都有 06 §4.7 錯誤碼、i18n 文案與至少一個 fix（PF-071、PF-078）
- [ ] 不可信文字（照片文字、EXIF、檔名、人名）以 `UntrustedText` 傳遞（05 §3.9）
- [ ] 沒有真實網路、真實時間（`time.sleep`、`datetime.now()`）或真模型進入 PR 測試

## 覆蓋
- [ ] diff coverage ≥ 90%；core／fs-sandbox branch ≥ 95%
- [ ] fs-sandbox／path_template 變更已跑 `just mutate-diff`（score ≥ 85%）
```

### 1.5 不得為了綠燈刪除或跳過測試

1. **禁止**：刪除測試、加 `skip`／`xfail`／`.only`／`test.fixme`、把斷言改弱（例如 `==` 改成 `in`、硬門檻改大）、以 mock 取代被測模組本身、修改 golden 檔讓它符合錯誤輸出。CI 的 `test-integrity` job 會比對 base 分支：被刪除或新增 skip 的測試、門檻常數（`tests/**/thresholds.py`、`*.thresholds.ts`）的放寬，都會讓 PR 需要 `test-change-approved` 標籤（只有 CODEOWNERS 能加）。
2. **AI agent 額外規則**：遇到「規格說 A、測試說 A、但實作很難做到 A」時，回報並停止，不修改測試；遇到測試本身錯誤（與規格不符）時，在獨立的 `test:` commit 修正並在 PR 描述引用規格段落。
3. **合法的移除**：功能被產品決策移除（文件先改）→ 對應案例在 09a／09b 標「廢止」並註明決策出處，測試同步刪除。

### 1.6 Flaky 測試政策

| 項目 | 規則 |
|------|------|
| 定義 | 同一 commit 重跑結果不同的測試 |
| PR | Python **不自動重跑**（`pytest-rerunfailures` 只在 nightly 偵測模式使用）；Playwright `retries: 1`，但 `@critical` 測試若「重跑才過」視為失敗 |
| 偵測 | nightly 對當週新增或修改的測試跑 20 次（`pytest --count=20`、Playwright `--repeat-each=20`）；全套每週一次 ×5；結果寫入 `flaky-report.json` |
| 隔離（quarantine） | `@pytest.mark.quarantine(issue="#123", until="YYYY-MM-DD")`／`test.describe.configure({ tag: "@quarantine" })`：移到非阻擋的 `quarantine` job；**最長 14 天**，過期 CI 失敗；同時開 issue 並指派 |
| 不可隔離 | `CORE-*`、`FS-*`、`OUT-*`、`PF-071`、`MCP-SCOPE-*`、`MCP-INJ-*`、`MCP-APPR-*`、`FACE-PRG-*`、`DESK-011`–`DESK-019`、`PACK-002`–`PACK-005`、`UPD-002`：這些是資料安全與安全性案例，flaky 時要嘛當天修好，要嘛 revert 造成 flaky 的變更 |
| 常見根因與預防 | 時間 → 一律 FakeClock（ruff 自訂規則禁止 `time.sleep`、`time.time()`、`datetime.now()` 出現在 `tests/` 與 `packages/core`）；port 衝突 → 一律 port 0；檔案系統時序 → 等事件不等時間；網路 → `pytest-socket` 只允許 localhost；順序依賴 → `pytest-randomly` 隨機順序；Playwright → 只用 web-first assertion，禁止 `waitForTimeout` |
| 統計 | flaky 率 > 1% 的套件，下一個 sprint 的第一個任務就是修 flaky |

---

## 2. 測試金字塔與工具

### 2.1 各層級

| 層 | 代號 | 工具 | 對象 | 目錄 | 命名 | 時間預算 |
|----|------|------|------|------|------|----------|
| Unit | U | pytest、Vitest、`cargo test` | 純函式、元件、單一類別；無 I/O 或只用 fake | `<pkg>/tests/unit/`、`src/**/*.test.ts(x)` | `test_<module>.py`、`<Comp>.test.tsx` | 每套件 < 60 s；單一測試 < 100 ms |
| Property-based | P | Hypothesis（profile `ci`=200 例、`nightly`=10k 例、`fuzz`=1M 例）、fast-check | path_template、SafePath、decide、狀態機、counters、tokenizer、JSON parser、ETA 涵蓋率模擬、planner、redactor | `<pkg>/tests/property/` | `test_<module>_props.py` | PR 全部 < 3 min |
| Contract | C | schemathesis（OpenAPI）、`oasdiff`（破壞性變更守門）、JSON Schema 快照（Worker 協定、MCP tools、preflight 目錄、registry）、InferenceBackend 一致性套件、Python↔TS golden | 介面與跨語言一致性 | `tests/contract/`、`packages/inference-sdk/conformance/` | 快照在 `tests/contract/snapshots/` | < 4 min（FakeBackend + tiny 模型） |
| Integration | I | pytest + **真 PostgreSQL 17**（`embedded_pg` fixture；Linux 另跑 testcontainers `pgvector/pgvector:pg17` 一致性）、真檔案系統（tmpfs、ext4、NTFS、APFS）、錄製 HF 伺服器 | queue／lease、executor、scanner、download、preflight、search SQL | `apps/*/tests/integration/`、`packages/*/tests/integration/` | `test_<feature>_it.py` | Linux PR < 8 min；OS 矩陣每 OS < 12 min |
| E2E | E | Playwright（Chromium + WebKit）對 **web build**；同一份 spec 以 mock `backend_info()` 跑 **desktop build**；後端 all-in-one + FakeBackend | 使用者旅程 A／B、搜尋、Lightbox、整理、待確認 | `apps/web/e2e/` | `<journey>.spec.ts` | PR 煙霧子集 < 12 min；nightly 全套 < 60 min |
| 桌面煙霧 | D | `tauri-driver` + WebdriverIO（Windows、Linux）；macOS 手動清單（WKWebView 沒有 WebDriver，11 V-9） | 殼監管、單一實例、IPC、asset protocol、通知、更新 | `apps/desktop/smoke/`、Rust `apps/desktop/src-tauri/tests/` | `desk_*.e2e.ts` | nightly < 30 min／OS |
| Visual regression | V | Playwright `toHaveScreenshot`（對建置後的 Storybook `iframe.html?id=…` 與實際路由）+ Storybook `addon-vitest`（Vitest browser mode 執行 `play`；ADR-0003）（只在 Linux Chromium、字型釘選、動態區域遮罩） | 元件 light/dark、每個錯誤碼 ProblemCard、主要路由 | `apps/web/e2e/visual/`、`*.stories.tsx` | — | nightly < 15 min；PR 只跑變更元件的 story |
| a11y | A | `@axe-core/playwright`、Storybook a11y addon、鍵盤腳本 | 全路由 0 serious/critical、grid／dialog 語意、鍵盤旅程 | `apps/web/e2e/a11y/` | — | PR 3 路由；nightly 全部 |
| Performance | F | Playwright + CDP tracing、`size-limit`、pytest-benchmark、`k6`（API） | 10 萬張捲動、首屏、bundle、API 延遲、吞吐基準 | `tests/perf/` | `perf_<area>.py/.ts` | nightly < 30 min；回歸 > 10% 警示、> 20% 失敗（個別案例另有門檻） |
| Security | S | Hypothesis fuzz、紅隊集、`bandit`、`semgrep`、`pip-audit`、`pnpm audit`、`cargo audit`、`gitleaks` | 路徑沙箱、pack 驗證、loopback 認證、MCP scope／injection、redaction | `tests/security/`、各套件 `tests/**` 標 `security` marker | — | PR 確定性部分 < 5 min；nightly fuzz 1M 例 |
| Chaos | X | 子程序 + `OPAGENT_CRASH_AT` hook、`kill -9`、tmpfs 容量限制、toxiproxy（網路斷線／延遲）、FakeDisk | 當機注入、斷線、磁碟滿、來源離線 | `tests/chaos/`、`packages/fs-sandbox/tests/chaos/` | `chaos_<area>.py` | PR 子集（OUT-CRASH-001–011 各 1 次）< 4 min；nightly 全套 < 45 min |
| Nightly 真模型 | N | self-hosted GPU runner（§5.4） | 一致性、JSON 遵循率、校準、吞吐、ETA 涵蓋率、Ask 評估集 | `bench/`、`packages/inference-adapters/tests/real/` | marker `real_model`、`gpu` | < 3 h |
| 手動 | M | `docs/checklists/release-v0.X.md` | macOS 桌面、乾淨 VM 安裝、XMP 第三方相容 | — | — | release 前 |

### 2.2 目錄結構

```
openphotoagent/
├─ packages/
│  ├─ core/tests/{unit,property,eta,search}/
│  ├─ fs-sandbox/tests/{unit,property,os,executor,chaos}/
│  ├─ inference-sdk/
│  │  ├─ opagent/inference/fake.py        # FakeInferenceBackend、FakeRuntime
│  │  └─ conformance/                     # 一致性套件（每個 adapter 都要過）
│  ├─ inference-adapters/tests/{unit,real}/
│  ├─ testing/                             # opagent-testing：FakeClock、FakeFS、FakeDisk、FakePower…、fixtures loader
│  └─ ts-path-template/src/*.test.ts       # 與 Python 共用 fixtures/golden/path_template
├─ apps/
│  ├─ api/tests/{unit,integration,preflight,operations,organize,contract}/
│  ├─ worker/tests/{scanner,models,downloads,doctor,integration,chaos}/
│  ├─ web/
│  │  ├─ src/**/*.test.tsx
│  │  └─ e2e/{journeys,a11y,visual,perf}/
│  └─ desktop/
│     ├─ src-tauri/tests/                  # Rust：supervisor、single-instance、pack 驗證
│     └─ smoke/                            # tauri-driver + WebdriverIO
├─ tests/
│  ├─ contract/{openapi,worker-protocol,mcp,preflight}/snapshots/
│  ├─ security/{redteam,fuzz}/
│  ├─ chaos/
│  ├─ perf/
│  └─ support/{hfserver,packserver,fakellm}/
├─ bench/                                  # nightly GPU：benchmark、校準、Ask 評估
├─ spikes/m0/                              # M0 實驗腳本與 results/*.json（§6）
├─ fixtures/
│  ├─ photos/                              # golden 影像（CC0 或自產，LICENSES.csv）
│  ├─ hw/                                  # FakeGPU／doctor 探針 profile
│  ├─ hf/                                  # 錄製下載伺服器情境
│  ├─ db/                                  # 舊版 DB dump（升級測試）
│  └─ golden/                              # 各模組 golden JSON
├─ docs/generated/coverage-status.md       # tools/tc_status.py 產生
└─ tools/{pytest_tc,tc_status.py,gen_synth_library.py,gen_injection_images.py}
```

### 2.3 共用 pytest 設定

- markers：`unit`、`property`、`contract`、`integration`、`e2e`、`chaos`、`security`、`perf`、`real_model`、`gpu`、`os_windows`、`os_macos`、`os_linux`、`quarantine`、`tc(id)`。PR 預設 `-m "not real_model and not gpu and not perf"`。
- 全域 fixture：`clk`（FakeClock）、`sandbox_tree`、`embedded_pg`（session 層級 initdb，每測試 template clone）、`fake_backend`、`hw`（profile 名稱 → 探針注入）、`hf_server(scenario)`、`fake_worker(n)`、`api_client`（帶 launch token）。
- 隔離：`pytest-socket`（只允許 127.0.0.1／unix socket）、`pytest-randomly`、每個測試獨立 tmp HOME 與 `OPAGENT_HOME`。
- `OPAGENT_TEST_MODE=1` 才允許注入 hw profile、FakePower 等測試 hook；**release build 收到這些環境變數必須拒絕啟動**（安全案例，納入 DESK-027 同一測試檔）。

---

## 3. 測試替身策略

> 原則：替身放在系統邊界（GPU、模型、時間、檔案系統錯誤、網路、OS 電源與視窗狀態），**不 mock 被測模組本身**。每個替身都有自己的單元測試，證明它模擬的行為與真實元件一致（例如 FakeBackend 也必須通過 INF-CONF 一致性套件）。

### 3.1 FakeInferenceBackend（`packages/inference-sdk/opagent/inference/fake.py`）

| 能力 | 介面 | 說明 |
|------|------|------|
| 決定性輸出 | `FakeBackend(seed=0, mode=...)` | 輸出由 `sha256(image_id + seed)` 推得；若 `fixtures/photos/labels.json` 有該圖標註，就用標註的 category／tags（讓 E2E 有可預期的整理結果） |
| 分數模式 | `fb:scores(high\|low\|random(seed)\|scripted(map))` | 控制 cascade 路由（high → 不送 VLM；low → 送 VLM） |
| 向量 | `fb:vectors(deterministic)` | 依 `fixtures/fake_vectors.yaml` 定義語意群：文字查詢「a dog on the beach」的向量靠近標為 dog+beach 的影像；L2 正規化 |
| VLM 回應 | `fb:vlm(p=…, text=…)` | 回傳 fused JSON，或注入下列 parse error |
| 故障注入 | `inject(code, times=n, after_calls=k, on_item=pred)` | `OOM`、`TIMEOUT`、`BAD_INPUT`、`RUNTIME`、`CRASH`（拋錯並變成 unloaded）、`PARSE_FENCED`、`PARSE_TRUNCATED`、`PARSE_ENUM_VIOLATION`、`PARSE_GARBAGE` |
| 延遲模型 | `latency_ms_per_item`、`load_ms`、`jitter` | 有注入 FakeClock 時以 `clk.advance()` 取代 sleep；ETA 測試用 lognormal 延遲 |
| 呼叫紀錄 | `fb.calls(task="classify")` | 斷言「VLM 呼叫數 0」「只解碼一次」 |
| VRAM 模擬 | `est_vram_mb`、`vram_used_mb` | 與 ResourceManager 測試搭配 |
| FakeRuntime | `tests/support/fakeruntime` | 小型 OpenAI 相容 HTTP 伺服器，模擬 `llama-server` 子程序：腳本化回應、logprobs、SIGKILL 自己、啟動慢、`--parallel` 參數回顯；用來測 adapter 的程序管理與重啟策略，不需要模型 |

### 3.2 FakeClock（`opagent.testing.clock`）

- 所有產品程式碼透過 `Clock` 協定取得時間（`now()`、`monotonic()`、`async sleep()`、`call_later()`）；ruff 自訂規則禁止 `packages/core`、`apps/*/eta`、`apps/*/operations`、`apps/api/scheduler` 直接呼叫 `time.*` 或 `datetime.now()`。
- `clk.advance(seconds)` 會依序觸發排程的 callback（lease 回收、sweeper、EWMA 取樣、重試退避、ETA 遲滯、TTL）。
- 前端：Vitest fake timers + 注入 `now()`；ETA 顯示與經過時間測試（ETA-011、OP-013、ACT-007）都用它。

### 3.3 FakeFS、FakeDisk、tmp 沙箱

| 替身 | 用途 |
|------|------|
| `sandbox_tree` fixture | 在 tmp 建立 `roots/photos`（ro）、`roots/sorted`（rw）、`outside/`，並產生 worker 設定；測試結束自動清除 |
| `FakeFS`（實作 `FsOps` 協定，executor 只透過它操作檔案） | 記錄系統呼叫序列（OUT-EXE-001、OUT-JRN-001）；在第 N 次呼叫注入 `ENOSPC`、`EACCES`、`EEXIST`、`EXDEV`、`EBUSY`（Windows sharing violation）；模擬大小寫不敏感與 NFD 正規化 |
| 真實檔案系統 | Linux：tmpfs（可設容量，磁碟滿）、loop device ext4（跨裝置）；Windows：NTFS + junction + `subst`／VHD 第二磁碟【待 M0 確認 runner 權限】；macOS：APFS（大小寫不敏感） |
| `FakeDisk` | `disk_usage()` 回傳腳本化 free bytes，讓下載與縮圖的預檢、執行中監控可測 |
| crash hook | `OPAGENT_CRASH_AT=<point>`（只在 `OPAGENT_TEST_MODE=1` 生效）讓 executor、scanner、downloader 在指定點 `os._exit(137)` |

### 3.4 FakeGPU／Doctor 探針注入（`fixtures/hw/<profile>.yaml`）

Doctor 的所有探針（NVML、`nvidia-smi -q -x`、DXGI、Vulkan、Metal／`system_profiler`、psutil、電池、全螢幕、閒置、`LongPathsEnabled`、TCC）都是可替換的 `Probe` 實作。profile 檔存放**原始探針輸出**（例如錄製的 `nvidia-smi` XML），而不是 doctor 的結論，這樣 doctor 的解析邏輯也被測到。

| profile | 模擬環境 | 主要用途 |
|---------|----------|----------|
| `rtx5070ti-win11` | RTX 5070 Ti 16 GB、cc 12.0、驅動 580、Windows 11 | 參考機 1、INF-RT-001、DOC-002 |
| `rtx4060-laptop` | RTX 4060 Laptop 8 GB、Windows 11、電池 | 參考機 2、序列模式 |
| `m2-air-16` | M2 16 GB、macOS 14 | 參考機 3 |
| `m3-16` | M3 16 GB（`recommendedMaxWorkingSetSize` fixture） | 統一記憶體預算 |
| `m1-8` | M1 8 GB | 快速模式 |
| `no-gpu-linux` | Ubuntu 22.04、無 GPU、8 核 | CPU 路徑 |
| `amd-rx7800-win` | RX 7800 XT 16 GB、DXGI Budget | Vulkan 路徑 |
| `intel-arc-linux` | Arc A770、`VK_EXT_memory_budget` | Vulkan 路徑 |
| `nvidia-drv535` | RTX 3080、驅動 535 | 驅動過舊 |
| `blackwell-cuda12pack` | RTX 5070 Ti + 已裝 cuda12.4 pack | `GPU_ARCH_UNSUPPORTED` |
| `linux-rtx3090` | Linux NVIDIA 24 GB | cuda pack + ort-cuda 建議 |
| `docker-no-passthrough` | 容器內、無 `/dev/nvidia*` | `GPU_DOCKER_NO_PASSTHROUGH` |
| `game-running-5gb` | 任一 NVIDIA profile + 外部程序佔 5 GB VRAM、全螢幕 D3D | GPU 被占用 |
| `intel-mac`、`linux-arm64` | 不支援平台 | INF-RT-010 |

搭配的 OS 狀態替身：`FakePower`（AC／電池／省電模式）、`FakeIdle`（閒置秒數）、`FakeFullscreen`，在桌面測試中經 Tauri IPC 注入，在 Python 測試中直接注入 provider。

### 3.5 錄製的 HF 下載伺服器（`tests/support/hfserver`）

本地 HTTP 伺服器，提供 `/api/models/{repo}`（含 `siblings` 與 LFS sha256）與 `/{repo}/resolve/{rev}/{path}`（302 → `/cdn/...`，支援 `Range`、`If-Range`、`ETag`）。檔案內容是以種子產生的決定性 bytes（大小可設，sha 預先算好），**repo 不放任何真實權重**。伺服器記錄每個請求（Range、連線開關時間）供斷言。

| 情境 | 行為 | 用於 |
|------|------|------|
| `ok` | 正常 | DL-001、002 |
| `slow-1MBps`、`limit-100MBps` | 限速 | 鏡像切換、DL-019 |
| `drop-at-50%` | 傳到 50% 時切斷 TCP | DL-004 |
| `bad-sha` | 內容與宣告 sha 不符 | DL-006 |
| `etag-changed` | `If-Range` 時回 200 與新內容 | DL-007 |
| `429-retry-after-5` | 回 429 與 `Retry-After: 5` | DL-008 |
| `5xx-x3` | 連續 3 次 503 | DL-015 |
| `401`、`403-gated`、`404` | 認證與缺檔 | DL-009 |
| `tls-selfsigned` | 自簽憑證（模擬企業 MITM） | PF-033 |
| `mirror-only` | 官方不可達，鏡像可達 | DL-016 |

另有 `tests/support/packserver`：提供 `packs-index.json` + minisign 簽章（測試專用金鑰對），以及惡意 tar（路徑穿越、symlink、zip bomb），供 PACK-* 使用。

### 3.6 Golden 影像 fixtures（`fixtures/photos/`）

所有影像必須是 **CC0** 或由專案自行產生並以 CC0 釋出；每張在 `fixtures/photos/LICENSES.csv` 記錄來源 URL、作者、授權、sha256。repo 內 fixture 總量 ≤ 40 MB（縮到長邊 ≤ 1024，RAW 與 HEIC 例外）；較大的集合以 manifest（URL + sha256）在 nightly 下載。來源優先序：專案自產（程式生成、維護者自己拍攝並釋出 CC0）→ Wikimedia Commons 的 CC0／Public Domain 類別 → Openverse 的 CC0 篩選 → raw.pixls.us（CC0 RAW 樣本）。**Unsplash／Pexels 授權不是 CC0，不使用。**

| set | 內容 | 數量 | 來源 | 位置 |
|-----|------|------|------|------|
| `img:basic` | 一般 JPEG，含完整 EXIF | 20 | 自拍／Commons CC0 | repo |
| `img:by-content-12` | 內建 12 類各 10 張（含易混淆樣本） | 120 | Commons CC0、自拍 | repo |
| `img:by-content-12-large` | 12 類各 50 張（校準、zero-shot 品質） | 600 | 同上 | manifest（nightly） |
| `img:orientation` | EXIF orientation 1–8 | 8 | 程式生成 | repo |
| `img:heic` | iPhone HEIC：直式、HDR gain map、Live Photo 靜態圖、人像模式、48 MP、HEIF 序列 | 6 | 維護者自拍 CC0 | repo |
| `img:raw` | DNG×2、CR3、NEF、ARW、RAF（含一張無內嵌預覽） | 6 | raw.pixls.us（CC0） | repo（≤ 25 MB） |
| `img:corrupt` | 截斷 JPEG、0 byte、文字檔偽裝、EXIF 損毀、PNG 偽裝 HEIC、超大尺寸宣告 | 6 | 程式生成 | repo |
| `img:no-exif` | 移除 EXIF | 10 | 衍生自 basic | repo |
| `img:timezone` | OffsetTime 有／無、GPS 時區、跨日界線、垃圾日期 | 8 | 程式寫入 EXIF | repo |
| `img:gps` | 台灣、日本（京都、春日）、歐洲、海上座標 | 10 | 程式寫入 EXIF | repo |
| `img:screenshot` | 手機與桌面截圖（含 `is_screenshot` 線索） | 10 | 程式生成 UI 畫面 | repo |
| `img:dup` | 完全重複 5 組；近似重複 5 組 × 4（縮放、重新壓縮、輕裁切、濾鏡） | 25 | 衍生 | repo |
| `img:names` | NFC、NFD、255 bytes、emoji、保留名、前後空白檔名 | 8 | 衍生 | repo |
| `img:semantic` | 以文搜圖用：狗＋海灘、夕陽、貓、食物等語意群 | 30 | Commons CC0 | repo |
| `img:injection` | 招牌、白板、截圖中含 injection 文字；QR code | 20 | `tools/gen_injection_images.py` 生成 | repo |
| `img:video` | 極短 mp4、mov（v0.1 略過用） | 2 | 程式生成 | repo |
| `img:e2e` | E2E 用 300 張（上述集合的聯集 + 衍生，含 `labels.json`） | 300 | — | repo（符號引用，不重複存） |
| 合成 10 萬張 | 漸層 + 雜訊 + 隨機 EXIF 日期（跨 20 年）、長寬比分布模擬真實圖庫 | 100,000 | `tools/gen_synth_library.py` 執行期生成 | 不入 repo |
| 測試圖庫（DoD） | 10,000 張：JPEG／HEIC／RAW 混合，含 500 張無 EXIF、200 組重複、一個外接硬碟資料夾 | 10,000 | CC0 manifest + 合成 + 維護者 CC0 照片 | 參考機本地快取 |
| 人臉 | 同一人多張、不同年齡、雙胞胎、遮擋 | — | **待 M0 確認**：必須是取得當事人同意且可再散布的資料集，或合成人臉；在確認前 PR 只用 FakeFaceBackend | — |

### 3.7 其他替身

| 替身 | 用途 |
|------|------|
| `FakeLLM`（`tests/support/fakellm`） | Ask 與 MCP agent 迴圈：腳本化 tool-call 序列與串流 token；可注入捏造 ID、捏造數字、超步數、逾時 |
| `FakeFaceBackend` | 決定性人臉偵測與 512 維向量，`person_seed` 產生同人／不同人的向量分布 |
| `FakeWorkerLink` | Worker 協定連線：斷線、延遲、重連、訊息重送 |
| `FakeKeyring` | OS keyring（含 Linux 無 Secret Service 的降級路徑） |
| `FakeNativePicker`、mock `backend_info()`、mock `platform` | desktop build 在 Playwright 中執行；原生對話框回傳 fixture 路徑 |
| toxiproxy | E2E／chaos 中的網路斷線與延遲（Worker ↔ api、下載） |

### 3.8 真模型測試只在 nightly GPU runner

- PR 只跑 FakeBackend，以及 CPU 上的 tiny 模型（ORT tiny ONNX、llama.cpp tiny GGUF）做 adapter 契約測試【tiny 模型選擇待 M0 確認】。
- 真模型測試標 `@pytest.mark.real_model`，在 `nightly-gpu.yml` 的 self-hosted runner 執行；模型以 registry 釘選的 sha 快取在 runner 上，不從測試中下載。
- 真模型測試的門檻（JSON 遵循率、ECE、recall）在 `bench/thresholds.yaml` 集中管理；放寬門檻屬於 `test-integrity` 管制的變更。

---

## 4. 測試案例目錄（索引）

> 全部案例都在 09a、09b；本節只列索引。欄位、層級代號與替身代號定義在 [09a §0](09a-test-cases-v0.1.md)。

| 模組 | 子模組（前綴） | 文件 § | 案例數 | 版本 | 主要對應能力 |
|------|----------------|--------|--------|------|--------------|
| core | CORE-HASH、CORE-TPL、CORE-THR、CORE-SM、CORE-RULE | 09a §1 | 75 | v0.1 | #38–#46、#51 |
| fs-sandbox | FS | 09a §2 | 26 | v0.1 | #37、#38、#48、DoD 5.3 |
| output executor | OUT-EXE、OUT-JRN、OUT-CRASH、OUT-UNDO | 09a §3 | 49 | v0.1 | #47、#48、#50 |
| scanner | SCAN | 09a §4 | 25 | v0.1 | #40、#51–#54 |
| queue／job | Q | 09a §5 | 28 | v0.1 | #3、#9、#46、#51 |
| inference | INF-REG、INF-CONF、INF-JSON、INF-CAS、INF-CAL、INF-VRAM、INF-OOM、INF-RT | 09a §6 | 66 | v0.1 | #13、#14、#22–#28、#41–#45 |
| models／downloads／doctor／diag | MDL、DL、DOC、DIAG | 09a §7 | 57 | v0.1 | #13–#21、#26、#34 |
| preflight | PF（錯誤碼目錄 66 + 行為 14） | 09a §8 | 80 | v0.1（5 條 v0.2／v0.4／v0.5） | #27、#28 |
| operation／ETA／activity | OP、ETA、ACT | 09a §9 | 43 | v0.1 | #29–#33 |
| 整理流程與待確認 | ORG、REV | 09a §10 | 15 | v0.1 | #40–#49、#56 |
| 搜尋（基礎） | SRCH-TOK、SRCH-FAC、SRCH-SEM | 09a §11 | 18 | v0.1 | #43、#64、DoD 5.4 |
| viewer／UI | E2E-A、E2E-B、E2E-S、E2E-L、E2E-SEL、VIEW、PERF、A11Y、VIS、I18N | 09a §12 | 56 | v0.1 | #11、#46–#56、#85–#93 |
| desktop | DESK、PG、PACK、UPD、DOCK | 09a §13 | 68 | v0.1 | #1–#10、#22、#37、#93 |
| 搜尋（混合） | SRCH-RRF、SRCH-PLAN、SRCH-IMG、SRCH-DUP | 09b §1 | 28 | v0.2 | #62–#66、#79 |
| trash／XMP | TRASH、XMP | 09b §2 | 15 | v0.2 | #50、#59 |
| MCP | MCP-TOOL、MCP-SCOPE、MCP-FOLD、MCP-MEDIA、MCP-APPR、MCP-RATE、MCP-AUD、MCP-INJ、MCP-TRN、MCP-PAT | 09b §3 | 99 | v0.2（12 條 v0.3、1 條 v0.5、1 條 v0.1 選用） | #60–#73、#84 |
| faces | FACE-GATE、-DET、-ASN、-CLU、-MGT、-FIND、-TPL、-REV、-PRG、-PRV、-SPACE、-N | 09b §4 | 42 | v0.2 | #74–#84 |
| Ask AI | ASK-GRD、-ACT、-SSE、-LOOP、-PRV、-SEC、-EVAL、-PERF、-MEM | 09b §5 | 29 | v0.3 | #100–#107 |
| watch／benchmark／preset | WATCH、BENCH、PRESET | 09b §6 | 11 | v0.3 | #35、#57、#58 |
| v0.4 以後 | RT、MDL-IMP、MAP、CMP、MU、SEC-AUDIT | 09b §7 | 10 | v0.2–v1.0 | #12、#20、#23、#73、#96、#97 |
| **合計** | | | **840** | | |

### 4.1 11 §5 DoD → 測試對照（release gate 依此判定）

| DoD | 條目 | 自動化測試 | 手動 |
|-----|------|------------|------|
| 5.1 | 安裝包 ≤ 200 MB、一般使用者安裝 | DESK-030、DESK-031 | DESK-031（乾淨 VM） |
| 5.1 | Docker all-in-one | DOCK-001、DOCK-002 | — |
| 5.1 | 啟動畫面 < 300 ms、暖啟動 < 5 s | DESK-001、PERF-007 | macOS |
| 5.1 | 無登入、loopback、401／403 | DESK-011–DESK-016、DESK-032、I18N-005 | — |
| 5.1 | kill -9 任一進程自動恢復 | DESK-002–DESK-006、Q-011、Q-022 | macOS |
| 5.1 | 單一實例、系統匣、OS 通知 | DESK-007、DESK-020、DESK-022 | macOS 通知 |
| 5.1 | 自動更新保留資料、拒絕竄改 | UPD-001、UPD-002 | UPD-001（VM） |
| 5.1 | 備份與還原 | PG-004–PG-006 | — |
| 5.2 | 四台參考機 doctor 不崩潰 | DOC-007、DOC-020 | 參考機報告 |
| 5.2 | 一個推薦組合、授權免勾選 | INF-RT-001–005、E2E-A-001、E2E-A-005 | — |
| 5.2 | 每個阻擋都有原因與 CTA、`opa/no-bare-disabled` | PF-001–PF-083 | — |
| 5.2 | 驅動過舊／pack 失敗可退回 Vulkan／CPU | PF-083、INF-OOM-003、PACK-011、E2E-A-002 | — |
| 5.2 | 下載暫停、續傳、取消、sha、磁碟預檢 | DL-003–DL-006、DL-010 | — |
| 5.2 | 下載同時可掃描；SigLIP2 先下載 | DL-020、E2E-A-001 | — |
| 5.2 | 模型刪除、搬移 | MDL-007–MDL-010 | — |
| 5.2 | 所有長操作都是 Operation（預估、ETA、經過時間、等待原因） | OP-001、OP-013、OP-017、ETA-018 | — |
| 5.2 | ETA 80% 區間涵蓋率 ≥ 70% | ETA-016、ETA-019 | 參考機紀錄 |
| 5.2 | 電池、全螢幕暫停 | Q-026、Q-027、DESK-023 | 筆電實機 |
| 5.2 | 重啟後 Activity Center 狀態 | OP-006、ACT-004 | — |
| 5.2 | 診斷包不含路徑與照片 | DIAG-001、DIAG-002 | — |
| 5.3 | 選擇器與拖放；REST 不能新增 root | DESK-017、DESK-018 | — |
| 5.3 | 三種整理方式 | ORG-001、ORG-002、CORE-RULE-007–009、E2E-B-001–003 | — |
| 5.3 | 預設輸出位置、預設複製、移動二次確認 | ORG-003、ORG-004、OUT-EXE-012、E2E-B-005 | — |
| 5.3 | 預覽 < 200 ms 不跑 GPU、全庫分布 | Q-024、E2E-B-002、ORG-007 | — |
| 5.3 | path template golden、非法字元、traversal fuzz | CORE-TPL-001–025、FS-024、FS-025 | — |
| 5.3 | cascade 正確並記錄決策層與 fingerprint | INF-CAS-002、INF-CAS-005 | — |
| 5.3 | JSON 成功率 ≥ 99% | INF-CONF-009（N） | — |
| 5.3 | 待確認與逃生門 | REV-002–REV-004、E2E-B-004 | — |
| 5.3 | 計畫摘要句、衝突自動處理 | OUT-EXE-022、OUT-EXE-005、OUT-EXE-006、E2E-B-006 | — |
| 5.3 | 任一點注入崩潰 0 遺失；undo 一致 | OUT-CRASH-001–012、OUT-UNDO-001 | — |
| 5.3 | 全庫原檔 hash 與 mtime 不變 | ORG-005、OUT-EXE-002 | — |
| 5.3 | JSON／CSV sidecar | OUT-EXE-020、OUT-EXE-021、ORG-006 | — |
| 5.3 | 搬移後 0 重新推論 | SCAN-006 | — |
| 5.3 | 外接硬碟離線等待 | SCAN-020、OUT-EXE-015、E2E-B-009 | — |
| 5.3 | 完成畫面 | E2E-B-007 | — |
| 5.4 | 60 fps、首屏、無空白格 | PERF-001、PERF-002、VIEW-003 | — |
| 5.4 | scrubber 與鍵盤 | VIEW-005、VIEW-006 | — |
| 5.4 | Lightbox 全功能、FLIP 降級 | E2E-L-001、VIEW-007、VIEW-008 | Linux WebKitGTK 實機 |
| 5.4 | 就地修正 | E2E-L-002、ORG-010 | — |
| 5.4 | 選取與批次 | E2E-SEL-001、E2E-SEL-002 | — |
| 5.4 | asset protocol scope | DESK-019 | — |
| 5.4 | 關鍵字 + tag + 日期、以文搜圖、索引進度 | SRCH-FAC-001、SRCH-SEM-001–005、E2E-S-001–003 | — |
| 5.4 | axe 0 serious、i18n 無缺漏 | A11Y-001、I18N-001 | — |
| 5.5 | 09 測試全部通過 | `tc_status.py --gate=v0.1` | — |
| 5.5 | 參考機效能紀錄 | PERF-005、ETA-019 | 附 release notes |
| 5.5 | sha256、SBOM、SmartScreen 說明 | release workflow 檢查 | 文件 |

---

## 5. CI 設計

### 5.1 Workflow 一覽（回寫 08 C-27 的 workflow 清單）

| Workflow | 觸發 | 內容 | 阻擋合併 |
|----------|------|------|----------|
| `ci.yml` | 每個 PR、push 到 main | §5.2 的 PR 必跑 job | ✓ |
| `nightly.yml` | 每日（UTC 18:00，台灣深夜） | E2E 全套（web + desktop build × Chromium + WebKit）、visual、a11y 全路由、chaos 全套、fuzz 1M、Hypothesis `nightly` profile、perf、mutation 全量、桌面煙霧（Win／Linux tauri-driver）、嵌入式 PG 50 次啟動（Windows Defender 開啟）、flaky 偵測、未簽章 edge artifact | 失敗時自動開 issue；main 標紅，下一個合併前必須修好或 revert |
| `nightly-gpu.yml` | 每日（self-hosted） | §5.4 真模型與 benchmark | 同上 |
| `release-gate.yml` | release-please PR、`v*` tag 前 | §5.5 | ✓（阻擋 `desktop-build.yml` 發佈） |
| `desktop-build.yml`、`packs-build.yml` | 依 08 §5.4 | 打包、簽章；安裝包大小守門（DESK-030） | 發佈 |
| `m0-spike.yml` | 手動 | §6 的實驗腳本，彙整 `spikes/m0/results/*.json` | — |

### 5.2 PR 必跑（`ci.yml`）

| Job | 內容 | Runner | 預算 |
|-----|------|--------|------|
| `lint` | ruff check／format、ESLint（含 `opa/no-bare-disabled`、`i18next/no-literal-string`、禁止 `waitForTimeout`）、Prettier、`cargo fmt` + `clippy -D warnings`、import-linter（FS-026）、i18n key 比對（I18N-001）、ruff 自訂規則（禁止 `time.sleep`／`datetime.now()`）、`gitleaks` | ubuntu | 3 min |
| `typecheck` | mypy（`packages/core`、`packages/fs-sandbox`、`packages/schemas` 為 `--strict`，其餘標準）、`tsc --noEmit` | ubuntu | 4 min |
| `unit-py` | `pytest -m "unit or property"`（Hypothesis `ci` profile）+ coverage（branch） | ubuntu | 5 min |
| `unit-ts` | Vitest + coverage + fast-check | ubuntu | 4 min |
| `contract` | `oasdiff` 對 base 分支（破壞性變更需 `api-breaking` 標籤與 VERSIONING 說明）；schemathesis（PF-082）；Worker 協定、preflight 目錄、registry、MCP tools（v0.2 起）JSON Schema 快照；INF-CONF 一致性（FakeBackend、tiny ORT、tiny llama.cpp CPU）；path_template Python↔TS golden（CORE-TPL-025） | ubuntu | 6 min |
| `integration-linux` | 嵌入式 PG pack（`embedded_pg`）跑 Q、OUT、SCAN、DL、PF、SRCH、OP 整合測試；另跑一次 testcontainers `pgvector/pgvector:pg17` 一致性（PG-014）；tmpfs 磁碟滿與 loop device 跨裝置 | ubuntu | 8 min |
| `os-matrix` | **windows-latest、macos-14、ubuntu-22.04**：core 全部、fs-sandbox 全部（含 `os` 案例）、executor + OUT-CRASH-001–011、scanner 的 OS 相關案例（SCAN-015、016、023）、嵌入式 PG（PG-001–006、PG-013）、Rust 殼單元測試 | 3 OS | 每 OS 12 min |
| `chaos-smoke` | OUT-CRASH-001–011 各 1 次、Q-022、DL-004 | ubuntu | 4 min |
| `e2e-smoke` | Playwright web build（Chromium）：E2E-A-001、E2E-B-001、E2E-B-002、E2E-L-001、E2E-S-001；axe 3 個路由；desktop build（mock backend_info）跑 E2E-B-001 | ubuntu | 12 min |
| `size` | `size-limit`（PERF-003）；packaging 路徑變更時建置 Linux AppImage 並檢查 ≤ 200 MB | ubuntu | 3 min（打包時 15 min） |
| `test-integrity` | 刪除測試、新增 skip、放寬門檻、golden 變更偵測（§1.5）；`tdd-order`（§1.3）；`tc_status.py` 產物是否最新 | ubuntu | 1 min |
| `mutation-diff` | 只在 `packages/fs-sandbox/**`、`packages/core/path_template*`、`packages/ts-path-template/**` 變更時：mutmut／Stryker 只對變更檔案 | ubuntu | ≤ 15 min |
| `audit` | `pip-audit`、`pnpm audit --prod`、`cargo audit`、`bandit`、`semgrep`（自訂規則：路徑操作必經 SafePath、禁止 `shell=True`） | ubuntu | 3 min |

PR 總牆鐘時間目標 ≤ 15 分鐘（job 並行）。Required checks：上表全部（`mutation-diff` 只在觸發時必要）。

### 5.3 OS 矩陣的重點

- **為什麼這三個 runner 都要跑嵌入式 PG**：GitHub macOS runner 沒有 Docker（08 §2.3），所以整合測試一律用同一份 `pg` pack；Windows 上的 PG 啟動與鎖檔是 M0 最大風險（V-1）。
- Windows 專屬：junction／reparse point（FS-013）、保留名、ADS、`\\?\` 長路徑（CORE-TPL-016、DOC-013）、sharing violation（OUT-EXE-016）、OneDrive 佔位檔（SCAN-016）、Job Object 孤兒防護（DESK-006）。
- macOS 專屬：APFS 大小寫不敏感（FS-015、OUT-EXE-010）、NFD 檔名（FS-016）、TCC（DOC-012，以探針注入）、`/tmp` symlink root（FS-021）。
- Linux 專屬：`openat2`（FS-012）、tmpfs 容量、loop device 跨裝置、PDEATHSIG。

### 5.4 Nightly GPU runner（`nightly-gpu.yml`）

| Runner label | 機器 | 對應參考機 |
|--------------|------|------------|
| `gpu-5070ti-win` | RTX 5070 Ti 16 GB、Windows 11 | 參考機 1 |
| `gpu-4060-win` | RTX 4060 8 GB 筆電、Windows 11 | 參考機 2 |
| `mac-m2-16` | M2 MacBook Air 16 GB | 參考機 3 |
| `linux-vulkan` | Ubuntu 22.04 + AMD 或 Intel GPU | 參考機 4 |

執行：INF-CONF-008／009／010（真 runtime）、INF-CAL-003、SRCH-SEM-001（真 SigLIP2）、ORG-005（測試圖庫 10,000 張全流程）、PERF-005（吞吐、VRAM、載入時間，與前 7 天中位數比較：退步 > 10% 警示、> 20% 失敗）、ETA-019（`eta_calibration` 涵蓋率）、DOC-020；v0.2 起加 FACE-N-001、MCP-INJ agent 迴圈；v0.3 起加 ASK-EVAL-002、ASK-PERF-001。結果寫入 `bench/results/<date>/<runner>.json`，並由 `bench/report.py` 產生趨勢圖（Actions artifact，release 時附在 release notes）。

### 5.5 Release gate（`release-gate.yml`）

全部滿足才允許 `desktop-build.yml` 發佈 `v0.X.0`：

1. `tc_status.py --gate=v0.X`：09a（及 09b 該版本）所有 ID 都有通過的測試，`M` 案例在 `docs/checklists/release-v0.X.md` 全部勾選且有簽名。
2. §4.1 DoD 對照表每一列都綠。
3. 最近 3 次 nightly 與 nightly-gpu 全綠；不可隔離清單（§1.6）中沒有任何 quarantine。
4. 覆蓋率與 mutation 門檻（§5.6）達標。
5. 安裝包 ≤ 200 MB；SBOM 與 sha256 產生；四台參考機效能紀錄已附上。
6. INF-REG-013：所有內建模型 variant 都已在 `registry/pins.lock` 釘選（ADR-0002），`registry-pin.yml` 最近一次 nightly 驗證仍可解析。

### 5.6 覆蓋率與 mutation testing

| 範圍 | 指標 | 門檻 | 工具 |
|------|------|------|------|
| `packages/core`、`packages/fs-sandbox` | branch coverage | **≥ 95%** | coverage.py `--branch` |
| 其他 Python 套件、`apps/*` | branch coverage | ≥ 80% | coverage.py |
| `apps/web`、`packages/viewer`、`packages/ts-path-template` | branch coverage | ≥ 80%（ts-path-template ≥ 95%） | Vitest v8 coverage |
| `apps/desktop/src-tauri` | line coverage | ≥ 80% | `cargo llvm-cov` |
| PR 變更行 | diff coverage | ≥ 90% | `diff-cover` |
| `packages/fs-sandbox`（SafePath、executor、journal、recovery）、`packages/core/path_template.py` | mutation score | **≥ 85%**；存活的等價 mutant 記錄在 `mutmut-equivalents.txt` 並經 review | mutmut（PR diff + 每週全量） |
| `packages/ts-path-template` | mutation score | ≥ 85% | Stryker |
| `packages/core/decide.py`、`packages/core/eta/` | mutation score | ≥ 75%（追蹤，v0.2 起守門） | mutmut |

覆蓋率不得以 `# pragma: no cover` 規避；`fs-sandbox` 與 `core` 中每一個 `no cover` 需要 CODEOWNERS 同意並說明原因。

---

## 6. M0 spike 的驗證測試

> 把 08 §9.4（S1–S7）、08 §11、11 §4.2（V-1–V-10）、03 §9、07 §12 的待驗證項變成可量測的實驗。每個實驗是 `spikes/m0/<id>/run.py`（或 `.ts`），輸出 `spikes/m0/results/<id>.json`（schema：`{id, machine, metrics{}, pass: bool, notes}`）；`m0-spike.yml` 彙整成報告，結果回填到各文件與本文標「待 M0 確認」的門檻。

| ID | 驗證項（來源） | 腳本做什麼 | 量測 | 通過標準 | 不通過時 |
|----|----------------|------------|------|----------|----------|
| M0-S1a | 嵌入式 PG 啟動（S1、V-1） | Tauri 殼 + python-build-standalone + PG pack，三平台各 50 次冷／暖啟動；Windows 以一般與管理員帳號、Defender 開啟 | 暖啟動 p95、冷啟動 p95、失敗次數、鎖檔錯誤 | 暖啟動 p95 < 5 s；50 次 0 失敗；管理員帳號可啟動 | 改用 embedded-postgres 套件（08 §2.3 退路） |
| M0-S1b | kill -9 恢復（S1） | 對 postgres／api／worker／llama-server 各 kill -9 20 次 | 恢復時間、Job 是否續跑、資料一致 | 每次 ≤ 30 s 恢復；0 遺失 | 調整監管策略 |
| M0-S1c | Windows AF_UNIX（08 §1.3） | asyncpg／psycopg 經 AF_UNIX 連 PG，10,000 次連線與查詢 | 錯誤數 | 0 錯誤才改用 socket | 維持 127.0.0.1 + SCRAM |
| M0-S2 | 安裝包大小（S2、V-4） | 三平台實際打包 | 壓縮後 MB | 各 ≤ 200 MB（目標 150） | 移出 GeoNames（08 §4.1） |
| M0-S3 | llama.cpp Qwen3-VL 結構化輸出（S3、V-2、03 §9-2/5） | 5070 Ti（CUDA 13）、4060（CUDA）、AMD／Intel（Vulkan）、M2（Metal 4B）各跑測試圖庫子集 1,000 張，fused schema | 首次即合法 JSON 率、修復後合法率、category 首 token logprob 是否可取得、enum 違規率、張/秒、VRAM 峰值 | **首次合法 ≥ 99%**、修復後 ≥ 99.9%；logprob 可取得（或確認替代信心方案：自我一致性 n=5 的成本 ≤ 低信心樣本 ×5）；吞吐寫入 registry `perf_prior` | 改用 grammar 模式；信心改走 embedding margin + 自我一致性 |
| M0-S3b | key 順序影響（03 §3.0、§9-5） | 同 1,000 張比較「caption → category」與「category → caption」 | 準確率差 | 記錄；差異 > 2 pp 時選較佳者並更新 INF-JSON-007 | — |
| M0-S4 | SigLIP2 ONNX 一致性（S4、V-3） | 匯出 so400m-patch14-384，對 500 張影像與 200 條文字比較 PyTorch 與 ORT（CPU、DirectML、CoreML） | 影像與文字向量 cosine 最小值與平均 | **每個向量 cosine ≥ 0.999** | 改用 onnx-community 的 patch16 匯出並重新評估 |
| M0-S4b | 繁中 zero-shot（03 §9-4） | `img:by-content-12-large`（600 張），分別用 zh-Hant 類別名稱＋描述、自動英譯、兩者 ensemble | top-1 準確率、ECE | zh-Hant ensemble ≥ **0.80**（**待確認**的初始門檻），且不低於英譯版 3 pp 以上；以結果決定 03 §3.3.1 的 prompt ensemble 預設 | 預設改用英譯 prompt；提高 VLM 送審比例 |
| M0-S5 | 人臉授權（S5、V-5、07 §12） | 文件審查（不是程式）：YuNet、AuraFace、eDifFIQA 的 LICENSE 與上游 sha；以小型授權資料集跑 TAR@FAR | 授權結論、TAR@FAR=0.1% | 授權允許 Apache-2.0 專案再散布並商用；TAR 記錄 | 07 改用替代模型 |
| M0-S6 | 簽章流水線（S6） | macOS notarize（含 resources 內所有 dylib）、SignPath 申請 | 通過與否 | notarize 通過 | Windows 未簽章發佈並附說明 |
| M0-S7 | tool-calling（S7、V-6） | llama-server `--jinja`，Qwen3-VL-8B 與 Qwen3.6-35B-A3B，20 次連續 + 100 次擴充 | 解析成功率、工具選擇正確率 | 20 次中 ≥ 19 次（≥ 95%） | v0.3 只開快速路徑（ASK-LOOP-003） |
| M0-V7 | ETA 涵蓋率（V-7） | 參考機 1、3 跑 3 種任務 × 3 次，記錄 `eta_calibration` | 80% 區間涵蓋率 | ≥ 70%（v0.1 門檻） | 調整 δ 與 n0（06 §5.5.5） |
| M0-V8 | WebKitGTK 能力（V-8） | Ubuntu 22.04 WebKitGTK 開啟探測頁 | View Transitions、`Intl.DurationFormat`、`createImageBitmap`、fetch streaming 是否可用 | 記錄；決定 VIEW-007、I18N-006 的降級是否必要 | — |
| M0-V9 | 桌面 E2E 可行性（V-9、08 C-11） | tauri-driver + WebdriverIO 在 Windows、Linux 跑 DESK-034 草稿 | 是否可穩定執行 10 次 | 10/10 成功 | 桌面煙霧改為更多手動清單 |
| M0-T1 | 測試基建 | (a) `embedded_pg` 在 GitHub macOS／Windows runner 的 template clone 時間；(b) PR 用 tiny GGUF／ONNX 模型的選擇與大小；(c) Windows runner 能否建立 junction、第二磁碟（`subst`／VHD）、設定 `FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS`；(d) Playwright 以 mock `backend_info()` 跑 desktop build | 時間、可行性 | (a) < 200 ms；(b) 每個 < 50 MB 且 CPU 單次推論 < 2 s；(c)(d) 可行 | (c) 不可行的案例改為 FakeFS 單元測試 + 參考機手動 |
| M0-T2 | 硬體探針錄製 | 在四台參考機與可取得的 AMD／Intel／無 GPU 機器上錄製 `fixtures/hw/*.yaml` 的原始輸出 | 錄製完成 | §3.4 的 profile 都有真實來源（其餘以文件規格合成並標記） | — |

---

## 7. 實作順序建議（v0.1 TDD 切片）

> 原則：**先打通 walking skeleton**（一條最薄但端到端可用的路徑：選資料夾 → 依日期計畫 → 核准 → 複製 → 復原），讓所有層（core、fs-sandbox、scanner、executor、api、PG、web）從第一週就存在並被測試；之後一個模組一個模組補厚。每一片列出**最先要寫的測試 ID**（依序），其餘同模組的 ID 在該片內完成。切片 7 與 11 依賴 M0 S1／S3／S4 的結果。

| 切片 | 目標 | 最先寫的測試（依序） | 完成條件 |
|------|------|----------------------|----------|
| **0. 測試基建** | uv／pnpm workspace、`ci.yml` 骨架、`pytest-opagent-tc`、`tc_status.py`、FakeClock、`sandbox_tree`、`embedded_pg`、FakeBackend 骨架、HF 伺服器骨架 | PG-013 → FS-026 → INF-CONF-001–004（對 FakeBackend）→ PF-079 → I18N-001 | PR 必跑 job 全部存在且綠（測試數少但真實執行） |
| **1. Walking skeleton：依日期複製 + undo** | 最薄的端到端：all-in-one（api + worker + PG）+ 最小 web UI | CORE-TPL-001 → CORE-TPL-004 → FS-001 → FS-002 → FS-003 → FS-017 → SCAN-001 → SCAN-003 → SCAN-013 → OUT-JRN-001 → OUT-EXE-001 → OUT-EXE-002 → OUT-UNDO-001 → CORE-SM-001（job 子集）→ ORG-001 → **E2E-B-001** | E2E-B-001 在 web build 綠燈；原檔不變；undo 回到原狀 |
| 2. core 純函式 | path_template、config_hash、decide、狀態機、規則 DSL 補齊 | CORE-TPL-006 → 010 → 016 → 018 → 024 → 025；CORE-HASH-001 → 007 → 009；CORE-THR-001 → 016；CORE-SM-002 → 006；CORE-RULE-001 → 008 | core branch ≥ 95%、path_template mutation ≥ 85% |
| 3. fs-sandbox 完整 | SafePath 全部規則、OS 差異、fuzz | FS-004 → 009 → FS-010 → 012 → FS-014 → 016 → FS-024 → FS-025 → 其餘 | 三 OS 綠；fuzz nightly 1M 0 逃逸；mutation ≥ 85% |
| 4. executor 安全 | 衝突、move、journal、當機恢復、undo、磁碟滿 | OUT-EXE-004 → 005 → 006 → 008 → 011 → 014 → OUT-JRN-002 → 004 → OUT-CRASH-001 → 011 → OUT-UNDO-002 → 009 → OUT-CRASH-012（nightly） | OUT-CRASH 不變量全綠；DoD 5.3 的崩潰條目可勾 |
| 5. scanner | 增量、hash、HEIC／RAW、EXIF 時區、離線、雲端佔位 | SCAN-004 → 006 → 007 → 008 → 009 → 010 → 012 → 015 → 016 → 020 → 其餘 | SCAN-006（0 重新推論）綠 |
| 6. queue、Operation、ETA | lease、重試、暫停／取消、快取、Operation 投影、ETA 與等待原因 | Q-001 → 002 → 004 → 005 → 014 → 015 → 011 → 022；CORE-SM-007 → 008；OP-001 → 002 → 006 → 013 → 017；ETA-001 → 004 → 005 → 011 → 016 | Q-022 chaos 綠；ETA-016 涵蓋率 ≥ 70% |
| 7. 推論層（需 M0 S3／S4） | FakeBackend → ORT SigLIP2 → llama.cpp；fused JSON、cascade、VRAM、OOM、runtime 選擇 | INF-CONF-005 → 013；INF-JSON-001 → 004 → 008；INF-CAS-001 → 002 → 005 → 006 → 007；INF-VRAM-001 → 003；INF-OOM-001；INF-RT-001 → 011；ORG-002 → ORG-007 → ORG-009；REV-001 → 004 | 依內容 12 類端到端（FakeBackend）；nightly INF-CONF-009 ≥ 99% |
| 8. 模型、下載、doctor、preflight、pack | 06 全部 v0.1 能力 | DL-001 → 003 → 004 → 005 → 006 → 010；MDL-001 → 002 → 007 → 008；DOC-001 → 002 → 007 → 016；PF-071 → PF-001–066（依錯誤碼表參數化，一次加一組 gate）→ PF-073 → PF-083；PACK-001 → 004；DIAG-001 | 錯誤碼目錄 100% 有測試；PF-083 退路綠 |
| 9. 搜尋 | tokenizer、facet、以文搜圖、索引進度 | SRCH-TOK-001 → 008；SRCH-FAC-001 → 004；SRCH-SEM-001 → 006 | DoD 5.4 搜尋條目可勾 |
| 10. viewer 與整理 UX | 網格、scrubber、Lightbox、選取、批次、預覽、待確認、計畫核准、Activity Center | VIEW-001 → 004 → 005；E2E-A-001 → 005；E2E-B-002 → 010；E2E-L-001 → 003；E2E-SEL-001 → 002；ACT-001 → 007；A11Y-001 → 006；VIS-001 → 003；I18N-002 → 006 | 旅程 A／B 全部綠；axe 0 serious |
| 11. 桌面殼與打包（需 M0 S1） | 監管、單一實例、launch token、asset scope、PG 備份／升級、pack、updater、Docker | DESK-011 → 014 → DESK-017 → DESK-019 → DESK-002 → 006 → DESK-007 → 010；PG-001 → 008；UPD-001 → 006；DOCK-001 → 003；DESK-030 → 034 | 桌面煙霧 Win／Linux 綠；macOS 手動清單通過 |
| 12. 強化與 release gate | 效能、chaos 全套、真模型、參考機紀錄 | PERF-001 → 007；VIEW-002 → 010；ORG-005；ETA-019；DOC-020；DL-019；PG-010 | §5.5 release gate 全綠 |

切片 1 的建議節奏：第 1 天寫完 CORE-TPL-001／004 與 FS-001–003（純單元），第 2–3 天 SCAN 與 OUT 的最小路徑，第 4–5 天 api 端點與 ORG-001，第 2 週 E2E-B-001。walking skeleton 不做 cascade、不做 Operation 的 ETA、不做桌面殼——但這些模組的介面（`Clock`、`FsOps`、`InferenceBackend`、`OperationReporter`）在切片 1 就要以最小形式存在，後續切片只填內容不改介面。

---

## 8. 測試案例統計

### 8.1 每模組數量

| 模組 | v0.1 | v0.2 | v0.3 | v0.4 | v0.5 | v1.0 | 合計 |
|------|------|------|------|------|------|------|------|
| core（CORE-*） | 75 | — | — | — | — | — | 75 |
| fs-sandbox（FS） | 26 | — | — | — | — | — | 26 |
| output executor（OUT-*） | 49 | — | — | — | — | — | 49 |
| scanner（SCAN） | 25 | — | — | — | — | — | 25 |
| queue／job（Q） | 28 | — | — | — | — | — | 28 |
| inference（INF-*） | 66 | — | — | 3（RT-*） | — | — | 69 |
| models／downloads／doctor／diag | 57 | 1（MDL-IMP） | — | — | — | — | 58 |
| preflight（PF） | 75 | 2 | — | 1 | 2 | — | 80 |
| operation／ETA／activity | 43 | — | — | — | — | — | 43 |
| 整理與待確認（ORG、REV） | 15 | — | — | — | — | — | 15 |
| 搜尋（SRCH-*） | 18 | 28 | — | — | — | — | 46 |
| trash／XMP | — | 15 | — | — | — | — | 15 |
| MCP（MCP-*） | 1（選用） | 85 | 12 | — | 1 | — | 99 |
| faces（FACE-*） | — | 42 | — | — | — | — | 42 |
| Ask AI（ASK-*） | — | — | 29 | — | — | — | 29 |
| watch／benchmark／preset | — | — | 11 | — | — | — | 11 |
| viewer／UI（E2E、VIEW、PERF、A11Y、VIS、I18N、MAP、CMP） | 56 | — | — | 2 | — | — | 58 |
| desktop（DESK、PG、PACK、UPD、DOCK） | 68 | — | — | — | — | — | 68 |
| 多人與安全審查（MU、SEC-AUDIT） | — | — | — | — | 3 | 1 | 4 |
| **合計** | **602**（含 1 選用） | **173** | **52** | **6** | **6** | **1** | **840** |

> v0.1 必要案例 = **601**（MCP-TRN-006 是 11 §5.5 的「`--experimental` 唯讀 stdio」選用例外）。PF 的 80 條中，PF-018（v0.4）、PF-058／PF-059（v0.5）、PF-060／PF-061（v0.2）為了錯誤碼目錄完整而放在 09a。MCP-SCOPE-001 一條即展開為 324 個參數化子案例（27 tools × 12 種 token），未計入上表。

### 8.2 每層級數量（主要層級；許多案例另有次要層級，例如 `U/S`、`I/X`）

| 層級 | v0.1（09a） | v0.2+（09b） | 合計 |
|------|-------------|--------------|------|
| Unit（U） | 201 | 50 | 251 |
| Integration（I，含 PF 目錄 80） | 227 | 84 | 311 |
| Contract（C） | 34 | 42 | 76 |
| Security（S） | 14 | 38 | 52 |
| E2E（E） | 36 | 12 | 48 |
| Chaos（X） | 26 | 1 | 27 |
| 桌面煙霧（D） | 21 | 1 | 22 |
| Property（P） | 14 | 1 | 15 |
| Performance（F） | 10 | 1 | 11 |
| Nightly 真模型（N） | 5 | 4 | 9 |
| a11y（A） | 9 | 0 | 9 |
| Visual（V） | 6 | 0 | 6 |
| 手動（M） | 3 | 0 | 3 |
| **合計** | **606** | **234** | **840** |

> 以次要層級計算時，property-based 參與的案例約 30 條、security 約 90 條、chaos 約 45 條。

---

## 9. 需要回寫其他文件的地方與待決事項

| # | 文件 | 修改 | 來源案例 |
|---|------|------|----------|
| W-1 | 06 §4.7 錯誤碼目錄 | 新增 `PACK_SIGNATURE_INVALID`、`PACK_INCOMPATIBLE`（08 §10 已提出）、`FS_FILE_LOCKED`（本文提議）、`DL_REMOTE_CHANGED`、`DL_MIRROR_FAILED`、`VRAM_OFFLOAD`（06 內文已使用但目錄缺） | PF-063–PF-066、OUT-EXE-016 |
| W-2 | 02 §3.2 `output_rule` | `path_template` 增加 UI 別名 `{date:YYYY}` 等與 `{category_key}`；`{category}` 渲染顯示名稱；`action` 增加 `trash`（11 D-2） | CORE-TPL-002、TRASH-001 |
| W-3 | 05 §3.2.3 | `tools/list` 依 token scope 過濾（目前只有 faces tools 明文要求） | MCP-SCOPE-001 |
| W-4 | 08 C-27 | workflow 清單加入 `nightly.yml`、`nightly-gpu.yml`、`release-gate.yml`、`m0-spike.yml` | §5.1 |
| W-5 | 11 §1 | 矩陣驗收標準連到 `docs/generated/coverage-status.md`（測試狀態） | §1.1 |
| W-6 | CONTRIBUTING | 加入 §1.3 commit 規範、§1.4 PR 檢查清單、§1.5 測試完整性規則 | §1 |

| 待決 | 說明 | 建議 |
|------|------|------|
| Q-T1 | 人臉測試影像的授權來源 | M0 期間確認；確認前 FACE-* 只用 FakeFaceBackend，FACE-N-001 延後 |
| Q-T2 | PR 用 tiny GGUF／ONNX 模型 | M0-T1 選定；若找不到夠小的 VLM，PR 的 llama.cpp 契約測試只驗證程序管理與 OpenAI 相容協定（FakeRuntime），結構化輸出只在 nightly |
| Q-T3 | self-hosted GPU runner 的維運（安全隔離、只跑 main 分支、不跑 fork PR） | nightly-gpu 只在 main 與受信任分支執行；runner 使用專用帳號、無 secrets 之外的權限 |
| Q-T4 | Windows runner 上的第二磁碟與 OneDrive 佔位檔模擬 | M0-T1 (c) |
| Q-T5 | 「過年」等中文日期表達的精確範圍 | 產品決定後回填 SRCH-PLAN-005 |
