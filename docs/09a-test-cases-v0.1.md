# 09a — v0.1 測試案例目錄（Test Case Catalog, v0.1）

> 狀態：Draft v0.1（2026-11-01）｜角色：測試架構師
> 上層文件：[09 — TDD 與測試套件](09-tdd-and-test-suite.md)（流程、工具、替身、CI、統計）。v0.2 以後的案例在 [09b](09b-test-cases-v0.2-plus.md)。
> 輸入：[11 §1 覆蓋矩陣](11-feature-coverage-review.md)（`#n` = 能力編號）與 [11 §5 v0.1 DoD](11-feature-coverage-review.md)（`DoD 5.x`）；02 §4/§6/§7/§10、03 §2/§3/§4/§10、06 §1–§5、08 §1–§6、10 §2–§5、04 §1/§8。
> 本文每一列都要能直接轉成一個測試函式（或一個參數化測試的一組參數）。「待 M0 確認」表示門檻數值要等 spike 量測後回填，但測試結構現在就要寫。

## 0. 讀法

### 0.1 欄位

| 欄 | 意義 |
|----|------|
| ID | `<模組>-<子模組>-<三位數>`；一旦合併就**不得重用或改號**（刪除時在表中標「廢止」） |
| 前置／輸入 | fixture、替身設定（見 09 §3）、輸入值或操作步驟。`hw:<profile>` = FakeGPU 探針（09 §3.4）、`clk` = FakeClock、`hf:<scenario>` = 錄製 HF 伺服器情境（09 §3.5）、`img:<set>` = golden 影像（09 §3.6）、`fb:<mode>` = FakeInferenceBackend 模式（09 §3.1） |
| 預期 | 斷言。數字是硬門檻；「golden」= 比對 `fixtures/golden/<module>/<ID>.json` |
| 層 | U unit · P property-based · C contract · I integration（真 PG / 真 FS）· E E2E（Playwright web build）· D 桌面煙霧（tauri-driver / WebdriverIO）· V visual · A a11y · F performance · S security · X chaos（當機、斷線、磁碟滿）· N nightly 真模型（GPU runner）· M 手動清單 |
| # | 11 §1 的能力編號；`DoD` 指 11 §5 的條目 |

### 0.2 版本

本文全部為 **v0.1**，除非該列另標版本（例如部分錯誤碼的 preflight 案例屬於 v0.2／v0.4／v0.5，為了讓錯誤碼目錄完整而放在同一張表）。

### 0.3 程式位置對照（細節見 09 §2.2）

| 前綴 | 被測程式 | 測試目錄 |
|------|----------|----------|
| CORE-* | `packages/core` | `packages/core/tests/{unit,property}/` |
| FS-* | `packages/fs-sandbox`（SafePath） | `packages/fs-sandbox/tests/{unit,property,os}/` |
| OUT-* | `packages/fs-sandbox`（executor、journal）+ `packages/output-actions` | `packages/fs-sandbox/tests/{executor,chaos}/` |
| SCAN-* | `apps/worker/scanner` | `apps/worker/tests/scanner/` |
| Q-* | `apps/api/scheduler` + `apps/worker/connection` | `apps/api/tests/integration/queue/` |
| INF-* | `packages/inference-sdk`、`packages/inference-adapters` | `packages/inference-sdk/conformance/`、`packages/inference-adapters/tests/` |
| MDL-*／DL-*／DOC-*／DIAG-* | `apps/worker/models`、`apps/worker/downloads`、`apps/worker/doctor` | `apps/worker/tests/{models,downloads,doctor}/` |
| PF-* | `apps/api/preflight` | `apps/api/tests/preflight/` |
| OP-*／ETA-* | `packages/core/operation`、`packages/core/eta`、`apps/api/operations` | `packages/core/tests/eta/`、`apps/api/tests/operations/` |
| ORG-*／REV-* | `apps/api/organize`、`apps/api/review` | `apps/api/tests/organize/` |
| SRCH-* | `packages/core/search`、`apps/api/search` | `packages/core/tests/search/`、`apps/api/tests/integration/search/` |
| UI-*／VIEW-*／ACT-*／E2E-* | `apps/web`、`packages/viewer` | `apps/web/src/**/*.test.tsx`、`apps/web/e2e/` |
| A11Y-*／VIS-*／I18N-*／PERF-* | 全前端 / 全系統 | `apps/web/e2e/{a11y,visual}/`、`tests/perf/` |
| DESK-*／PG-*／PACK-*／UPD-*／DOCK-* | `apps/desktop`（Rust + Python 啟動層）、`packaging/` | `apps/desktop/src-tauri/tests/`、`apps/desktop/smoke/`、`tests/desktop/` |

---

## 1. CORE — 純函式核心（`packages/core`，branch coverage ≥ 95%）

### 1.1 CORE-HASH：`config_hash` 與 `model_fingerprint`（02 §3.2、ADR-003、08 §4.3）

| ID | 標題 | 前置／輸入 | 預期 | 層 | # |
|----|------|-----------|------|----|---|
| CORE-HASH-001 | key 順序不影響 hash | 同一 `task_config`，`generation_params` 的 key 以兩種順序建構 | `config_hash` 相同；實作為 canonical JSON（key 排序、UTF-8、無多餘空白）→ BLAKE3 hex | U | #51 |
| CORE-HASH-002 | canonical 化對任意結構穩定 | Hypothesis 產生任意巢狀 JSON 值，隨機打亂 dict 順序與插入空白後序列化 | hash 恆等；不同語意值（任一葉節點改變）hash 不同（10k 例無碰撞） | P | #51 |
| CORE-HASH-003 | 數值正規化 | `1` vs `1.0`、`0.1` vs `0.1000000000000000055` | 兩組各自 hash 相同（數值以最短 round-trip repr 表示） | U | #51 |
| CORE-HASH-004 | 非法數值 | `temperature=NaN` 或 `Infinity` | 拋 `ConfigError(code="CONFIG_NON_FINITE")`，不產生 hash | U | — |
| CORE-HASH-005 | Unicode 正規化 | 類別描述以 NFC 與 NFD 兩種形式輸入「Café 美食」 | hash 相同（hash 前一律 NFC） | U | #51 |
| CORE-HASH-006 | 影響推論的欄位改變 → 失效 | 分別改 `prompt_template_ref`、`generation_params.max_tokens`、`label_set` 版本、類別 `description`、`structured_output_schema` | 每一項都讓 `config_hash` 改變 | U | #46 |
| CORE-HASH-007 | **只改門檻不重算** | 只改 `thresholds.accept` 0.80→0.70、`thresholds.review` | `config_hash` 不變 | U | #46、DoD 5.3 |
| CORE-HASH-008 | 只改輸出規則不重算 | 只改 `output_rule.path_template`、`target_id`、`conflict_policy` | `config_hash` 不變 | U | #46 |
| CORE-HASH-009 | fingerprint 用 `numerics_epoch` | pack 版本 `b7001-1`→`b7002-1`，`numerics_epoch` 同為 3 | `model_fingerprint` 不變 → 快取保留 | U | #51、08 §4.3 |
| CORE-HASH-010 | epoch 改變 → fingerprint 改變 | `numerics_epoch` 3→4；或權重 sha256 改變；或 adapter 版本改變 | fingerprint 改變 | U | #51 |
| CORE-HASH-011 | 快取鍵三元組 | `cache_key(asset_hash, config_hash, fingerprint)` 任一元素改變 | 鍵改變；三者相同時鍵相同 | U | #51 |
| CORE-HASH-012 | 跨語言／跨平台 golden | `fixtures/golden/config_hash.json`（20 筆 config → 預期 hash） | Python 3.12 於 Linux／Windows／macOS 全部符合；若 TS 端有預覽用實作，同一 golden 全部符合 | C | #51 |

### 1.2 CORE-TPL：`path_template` 渲染（02 §3.2、§6.3；Python 與 TS 共用 golden，#38）

> 語法：`{var}`、`{var:<strftime>}`；UI 友善別名 `{date:YYYY}`／`{date:YYYY-MM}`／`{date:MM}`／`{date:DD}` 在驗證時編譯成 `{taken_at:%Y}` 等（**本文提議，回寫 02 §3.2**）。v0.1 變數白名單：`category`、`category_key`、`taken_at`、`date`、`stem`、`ext`、`country`、`admin1`、`city`、`tag:<prefix>`、`source`。`{person}` 系列見 09b。`{category}` 渲染為 workspace 語系的顯示名稱，`{category_key}` 渲染為 ASCII key（提議）。

| ID | 標題 | 前置／輸入 | 預期 | 層 | # |
|----|------|-----------|------|----|---|
| CORE-TPL-001 | 基本渲染 | `{category}/{taken_at:%Y}/{taken_at:%Y-%m}/{stem}{ext}`；category=美食、taken_at=`2025-07-14T10:00+08:00`、stem=`IMG_0001`、ext=`.HEIC` | `美食/2025/2025-07/IMG_0001.HEIC` | U | #38 |
| CORE-TPL-002 | `{date:YYYY}` 別名 | `{category}/{date:YYYY}/{stem}{ext}` | 與 `{category}/{taken_at:%Y}/{stem}{ext}` 渲染結果逐字相同 | U | #38 |
| CORE-TPL-003 | 拍攝時區不轉 UTC | taken_at=`2025-12-31T23:30+09:00` | 年份資料夾為 `2025`（以拍攝當地時間），不是 UTC 的 `2025`→`2026` 錯位 | U | #40 |
| CORE-TPL-004 | 缺日期 → mtime 並標來源 | 無 EXIF 日期、mtime=`2024-03-02`、preset `by-date` | 渲染 `2024/2024-03/…`；回傳 `date_source="mtime"` 供 sidecar 與 UI 標註 | U | #40 |
| CORE-TPL-005 | 缺值 fallback | 無 GPS，模板 `{country}/{stem}{ext}`，`fallback_values={"country":"未知地點"}` | `未知地點/IMG_0001.jpg`；沒設定 fallback 時用 i18n 預設 `_未知`／`_unknown` | U | #38 |
| CORE-TPL-006 | 非法字元清理 | 變數值 `a/b\c:d*e?f"g<h>i\|j` | 該段變成 `a_b_c_d_e_f_g_h_i_j`；渲染結果的路徑段數不增加 | U | #38、DoD 5.3 |
| CORE-TPL-007 | 控制字元與 bidi | 值含 `\x00`–`\x1f`、`\x7f`、U+202E、U+200B、U+FEFF | 全部移除 | U | #38 |
| CORE-TPL-008 | `.` 與 `..` 值 | category 值為 `..`、`.`、`...` | 分別變成 `_`、`_`、`_`（全點字串一律替換），結果仍在 target 之下 | U/S | #38 |
| CORE-TPL-009 | 前後空白與結尾點 | 值 `  旅行. ` | `旅行`（去除前後空白與結尾 `.`，避免 Windows 自動截斷造成兩個名稱碰撞） | U | #38 |
| CORE-TPL-010 | Windows 保留名 | 值 `CON`、`con`、`Nul.txt`、`COM1`、`LPT9`、`AUX ` | 分別渲染為 `CON_`、`con_`、`Nul_.txt`、`COM1_`、`LPT9_`、`AUX_`；**所有 OS 一律套用**（圖庫可能被搬到 Windows） | U | #38 |
| CORE-TPL-011 | 斜線相似字元 | 值含 U+FF0F `／`、U+2215 `∕`、U+2044 `⁄`、U+FF3C `＼` | 換成 `_`（避免使用者誤以為是子資料夾） | U | #38 |
| CORE-TPL-012 | 段長度上限 | 值為 300 個「貓」（每字 3 bytes） | 段 ≤ 255 bytes（UTF-8），在字元（grapheme）邊界截斷，不出現半個字元 | U | #38 |
| CORE-TPL-013 | 截斷保留副檔名 | stem 為 260 bytes，ext=`.jpeg` | 截 stem 不截 ext；結果 `<…>.jpeg` ≤ 255 bytes | U | #38 |
| CORE-TPL-014 | Emoji 與組合字 | 值 `🐱‍👤家族👨‍👩‍👧` 需截斷時 | 不拆開 ZWJ 序列與 surrogate pair | U | #38 |
| CORE-TPL-015 | NFD → NFC | 值為 NFD 的 `é`（macOS 檔名常見） | 輸出 NFC；`unicodedata.is_normalized("NFC", out)` 為真 | U | #38 |
| CORE-TPL-016 | 全路徑過長（Windows 未開長路徑） | target 絕對路徑 200 字元 + 渲染結果 80 字元，`platform=windows`、`long_paths=false` | 回傳 `PathTooLong`（不拋例外），planner 將該 action 標 `conflict=path_too_long`，並附 doctor `fs.long_paths` fix | U | #38 |
| CORE-TPL-017 | 未知變數 | `{foo}/{stem}{ext}` | 驗證時（不是渲染時）拋 `TemplateError(code="UNKNOWN_VAR", pos=1)` | U | #38 |
| CORE-TPL-018 | 模板本身的路徑注入 | `/etc/{stem}`、`C:\{stem}`、`../{stem}`、`{category}/../{stem}`、`\\server\{stem}` | 驗證拒絕 `TemplateError(code="TEMPLATE_PATH_ESCAPE")` | U/S | #38 |
| CORE-TPL-019 | strftime 白名單 | `{taken_at:%Y}` ok；`{taken_at:%c}`、`%x`、`%s`、`%Z` | 後者拒絕（locale／平台相依） | U | #38 |
| CORE-TPL-020 | 模板長度上限 | 201 字元模板 | 拒絕（05 §3.3.5 上限 200） | U | #38 |
| CORE-TPL-021 | stem 與 ext 拆分 | `a.b.c.JPG`、`README`、`.hidden`、`x.tar.gz` | stem/ext：`a.b.c`/`.JPG`（保留大小寫）、`README`/``、`.hidden`/``、`x.tar`/`.gz` | U | #38 |
| CORE-TPL-022 | `{tag:<prefix>}` | tags=`[animal.cat(0.9), animal.dog(0.4)]`，`{tag:animal}` | 取 prefix 下最高分 → `cat` 的顯示名；無則 fallback | U | #38 |
| CORE-TPL-023 | casefold 鍵 | `Food` 與 `food` 兩個類別 | 渲染結果附 `casefold_key` 相同，供 OUT 在大小寫不敏感檔系統合併為同一資料夾 | U | #38 |
| CORE-TPL-024 | 性質：段永遠安全 | Hypothesis／fast-check：任意 Unicode 字串（含 surrogate 邊界、控制字元、保留名、超長）作為所有變數值 | 每段：不含 `/` `\` NUL、≠ `.`／`..`、≤ 255 bytes、為 NFC、不是保留名；渲染不拋例外 | P | #38、DoD 5.3 |
| CORE-TPL-025 | Python ↔ TS 一致 | `fixtures/golden/path_template/*.json` ≥ 200 筆（含 001–023 所有案例） | Python 與 TS 實作逐筆輸出相同（CI 兩邊都跑同一份 golden） | C | #38、DoD 5.3 |

### 1.3 CORE-THR：門檻判定與信心（03 §3.3、§3.3.3；純函式 `decide()`）

> 預設：`τ_high=0.80`、`m=0.15`、無範例圖時 `τ_high=0.90`；`other_policy.min_confidence=0.55`；task_config `thresholds={accept:0.80, review:0.50}`。

| ID | 標題 | 前置／輸入 | 預期 | 層 | # |
|----|------|-----------|------|----|---|
| CORE-THR-001 | embedding 高信心直接採用 | `p_top1=0.86, margin=0.22`，類別有 3 張範例 | `route=accept`、`source=embedding`、不需 VLM | U | #45 |
| CORE-THR-002 | margin 不足 → VLM | `p_top1=0.86, margin=0.10` | `route=vlm`，hints=top-3 | U | #45 |
| CORE-THR-003 | 邊界值含等號 | `p_top1=0.80, margin=0.15` | `accept`（≥ 為包含） | U | #45 |
| CORE-THR-004 | 無範例圖提高門檻 | 同 001 但範例數 0、`p_top1=0.86` | `route=vlm`（τ_high=0.90） | U | #42 |
| CORE-THR-005 | VLM 採用 | `p_vlm=0.71 ≥ 0.55` | `decision=accepted`、`source=vlm` | U | #45 |
| CORE-THR-006 | VLM 選 other | VLM 輸出 `category=other` | `decision=other` | U | #41 |
| CORE-THR-007 | 全部低於 min_confidence | 所有類別 `p<0.55` | `decision=other` | U | #41 |
| CORE-THR-008 | 介於 review 與 accept | top1=food、`p=0.62`（0.50 ≤ p < 0.80） | `decision=needs_review`、review.reason=`low_confidence` | U | #49 |
| CORE-THR-009 | E/V 一致加成 | `p_E=0.7, p_V=0.8` 同類 | `p=1-(0.3×0.2)=0.94`、`source=consensus` | U | #45 |
| CORE-THR-010 | E/V 不一致 | E=food 0.7、V=pets 0.8 | 採 V：`category=pets, p=0.64`；若 < accept → needs_review、reason=`conflict` | U | #45 |
| CORE-THR-011 | 規則先決 | `exif.is_screenshot=true` 規則 → documents | `confidence=1.0`、`source=rule`；不呼叫 embedding 也不呼叫 VLM | U | #45 |
| CORE-THR-012 | 溫度縮放 golden | 原始相似度向量 + `T=0.05` | softmax 結果與 golden 相差 ≤ 1e-9 | U | #45 |
| CORE-THR-013 | logprob 正規化 | 候選首 token logprob；其中一個候選不在 top_logprobs | 缺席者以 floor `1e-6`（**待 M0 確認**）計；Σp=1 | U | #45 |
| CORE-THR-014 | taxonomy 首 token 衝突 | ids `food`、`foot`（tokenizer fixture 顯示首 token 相同） | 建 taxonomy 時自動改為 `c1_food`、`c2_foot`，並保留顯示名 | U | #42 |
| CORE-THR-015 | 單調性 | Hypothesis：固定其他輸入，只提高 `p_top1` | decision 不會從 accepted 變回 needs_review／other | P | #45 |
| CORE-THR-016 | 完整性：每張照片恰一個結果 | Hypothesis：任意分數向量（含 NaN、空類別、全 0） | 恰一個 decision ∈ {accepted, other, needs_review}；NaN 視為 0 並設 `flags=["nan_score"]`；**沒有照片被漏掉** | P | #41 |
| CORE-THR-017 | 只重套門檻 = 純函式重算 | 已存 `stages`（各層 top-k），改 thresholds | 結果與「重新推論後判定」一致；不需任何 backend 呼叫 | U | #46 |
| CORE-THR-018 | accept 是 embedding 快速路徑的最終閘門（ADR-0001） | `p_top1=0.86, margin=0.22`、3 張範例；`thresholds.accept=0.90` | route=accept 但 0.86 < 0.90 → `needs_review`（low_confidence）；不需重新推論 | U | #45、#46 |
| CORE-THR-019 | conflict 低於 floor 仍交人工（ADR-0001） | E=food 0.9、V=pets 0.6 → p=0.48 | `needs_review`、reason=`conflict`、category=pets（不是 other） | U | #45、#49 |

### 1.4 CORE-SM：狀態機（02 §4.2 Job、06 §5.3 Operation、02 §3.2 output_plan）

| ID | 標題 | 前置／輸入 | 預期 | 層 | # |
|----|------|-----------|------|----|---|
| CORE-SM-001 | Job 合法轉移表 | 02 §4.2 全部 19 條邊（表驅動） | 每條都成功並發出 `job.status` 事件 | U | #47 |
| CORE-SM-002 | Job 非法轉移 | Hypothesis 從 (state × target) 補集抽樣 | 一律拋 `IllegalTransition`，狀態不變 | P | #47 |
| CORE-SM-003 | cancelling 收斂 | `running→cancelling`，仍有 2 個 lease | 不能進 `cancelled`；最後一個 lease 釋放後才轉 | U | #47 |
| CORE-SM-004 | 失敗重試只重置失敗項 | `failed→queued`（retry failed items） | 只有 `status=failed` 的 job_item 回 `pending`；`done/cached` 不動 | U | #47 |
| CORE-SM-005 | 只標註不輸出 | `awaiting_approval→completed` | 合法；output_plan 保持 `draft` 並標記 `discarded` | U | #47 |
| CORE-SM-006 | Operation 合法／非法轉移 | 06 §5.3 表驅動 + Hypothesis 補集 | 合法者成功；`paused→running` 直接轉移為非法（必須經 `queued`） | U/P | #33 |
| CORE-SM-007 | 心跳逾時 → interrupted | `clk`：owner 心跳 61 s 未更新，status=running | sweeper 設 `interrupted`，`operation_event.kind=OWNER_LOST`；`interrupted→queued` 合法 | U | #33 |
| CORE-SM-008 | Job → Operation 投影 | Job 進 `awaiting_review` | 對應 Operation `succeeded`，`result.links` 含 `前往覆核`；之後 apply 建立新的 `output.apply` Operation | U | #33 |
| CORE-SM-009 | output_plan 狀態 | `draft→approved→applying→applied→undone`；`applying` 中取消 | 取消後為 `partially_applied`，可 undo | U | #47 |
| CORE-SM-010 | 錯誤率自動暫停 | 51 個樣本中 11 個失敗（21.6%） | Job→`paused`、`waiting_reason=PAUSED_ERROR_RATE`；50 個樣本 100% 失敗時**不**暫停（樣本需 > 50） | U | #32 |
| CORE-SM-011 | seq 單調 | Hypothesis：隨機事件序列套用到 Operation | 每次更新 `seq` 嚴格遞增；`display_fraction` 單調不減（重試／回滾例外且帶 `regressed=true`） | P | #30 |

### 1.5 CORE-RULE：輸出規則 DSL 與內建 preset（02 §3.2、03 §10）

| ID | 標題 | 前置／輸入 | 預期 | 層 | # |
|----|------|-----------|------|----|---|
| CORE-RULE-001 | 依 priority 第一個命中 | 規則 p1 `category==food`→A、p2 `true`→B；照片 food | 只產生一個 action 到 A | U | #39 |
| CORE-RULE-002 | 類別各自目標 | screenshot→target S、food→target F | 兩類照片各自落在不同 target | U | #39 |
| CORE-RULE-003 | 沒有規則命中 → 其他 | 自訂規則都不符 | 落入 preset 的「其他」預設規則；計畫中照片總數 = 輸入總數 | U | #41 |
| CORE-RULE-004 | require_reviewed | `require_reviewed=true`，3 張 needs_review | 這 3 張不產生 action，直到 review；選「略過剩下的放進其他」後改進其他 | U | #49 |
| CORE-RULE-005 | DSL 語法錯誤 | `when: "category == "` | 驗證錯誤含 JSON pointer 與欄位位置 | U | #39 |
| CORE-RULE-006 | DSL 沙箱 | `when` 嘗試 `__import__`、屬性存取 `a.__class__`、函式呼叫 | 解析拒絕（DSL 只允許白名單欄位、比較、`in`、`and/or/not`） | S | #39 |
| CORE-RULE-007 | `by-date` preset 不需模型 | 載入 `presets/builtin/by-date.yaml` | task_configs 為空；模板 `{taken_at:%Y}/{taken_at:%Y-%m}/{stem}{ext}`；plan 產生不呼叫推論 | U | #40 |
| CORE-RULE-008 | `by-content-12` 完整性 | 載入 preset | 恰 12 類（人物、風景、美食、寵物、文件與截圖、旅行、活動、建築、植物、交通、藝術、其他），每類有 zh-Hant／en 名稱與描述，`other` 為 fallback | C | #41 |
| CORE-RULE-009 | `custom` preset 驗證 | 類別只有名稱、無描述也無範例 | 驗證錯誤「至少要有描述或 1 張範例照片」；有 3 張範例時通過 | U | #42 |
| CORE-RULE-010 | final_label 優先 review | result=food、review.corrected=pets | `final_label.category=pets`、`source=review` | U | #56 |

---

## 2. FS — 路徑沙箱 SafePath（`packages/fs-sandbox`，branch coverage ≥ 95%、mutation score ≥ 85%）

> 所有案例在 Linux／Windows／macOS 三個 runner 都跑（09 §5.2）；`os` 欄標示只在特定 OS 有意義的案例。fixture `sandbox_tree` 在 tmp 目錄建立 `roots/photos`（ro）、`roots/sorted`（rw）與 `outside/secret.txt`。

| ID | 標題 | 前置／輸入 | 預期 | 層 | # |
|----|------|-----------|------|----|---|
| FS-001 | 正常相對路徑 | `resolve("photos","2025/a.jpg")` | 回傳 root 下的真實路徑物件，`.rel == "2025/a.jpg"` | U | #37 |
| FS-002 | 絕對路徑 | `"/etc/passwd"`、`"C:/x"` | `PathViolation(code="PATH_VIOLATION", reason="absolute")` | U/S | #37 |
| FS-003 | `..` 元件 | `"../x"`、`"a/../../x"`、`"a/./../.."`、`"a/.."` | 全部拒絕（任一 `..` 元件即拒，不先正規化再判斷） | U/S | DoD 5.3 |
| FS-004 | 不做 URL decode | `"%2e%2e/x"` | 視為字面檔名 `%2e%2e`，在 root 內；不會被解碼成 `..` | U/S | — |
| FS-005 | NUL | `"a\x00.jpg"` | 拒絕 | U/S | — |
| FS-006 | Windows 磁碟、UNC、裝置路徑 | `"C:foo"`、`"\\\\server\\share\\x"`、`"\\\\?\\C:\\x"`、`"\\\\.\\PhysicalDrive0"` | 拒絕（所有 OS） | U/S | — |
| FS-007 | NTFS ADS（冒號） | `"a.jpg:secret"`、`"a.jpg::$DATA"` | 拒絕 | U/S | — |
| FS-008 | 反斜線 | `"a\\..\\b"`、`"a\\b.jpg"` | 一律拒絕（CP 只送 `/`），避免 Windows 上 `\` 被當分隔符 | U/S | — |
| FS-009 | 保留名路徑段 | `"CON/a.jpg"`、`"x/nul.txt"`、`"COM1"` | 所有 OS 拒絕 | U | — |
| FS-010 | 中間段是 symlink | `photos/link → /outside`，`"link/secret.txt"` | 拒絕（`follow_symlinks=false`），reason=`symlink` | I/S | — |
| FS-011 | 末段 symlink 指向 root 內 | `photos/a.jpg → photos/b.jpg` | 仍拒絕（預設不跟隨，與目標位置無關） | I/S | — |
| FS-012 | TOCTOU：resolve 後換成 symlink | 解析 `dir/a.jpg` 後、開檔前以 hook 將 `dir` 換成指向 outside 的 symlink | 開檔失敗；Linux 走 `openat2(RESOLVE_BENEATH\|RESOLVE_NO_SYMLINKS)`；Windows／macOS 逐段 `O_NOFOLLOW`／`FILE_FLAG_OPEN_REPARSE_POINT` 開啟並在開檔後以 fd 反查路徑驗證 | I/S/X | — |
| FS-013 | Windows junction／reparse point | `photos/j` 為 junction 指向 `C:\Windows` | 拒絕（os=windows） | I/S | — |
| FS-014 | 字串前綴假陽性 | root=`/r/photos`，某路徑解析到 `/r/photos2/x` | 拒絕（用 `commonpath`，不用 `startswith`） | U/S | — |
| FS-015 | 大小寫不敏感檔系統 | macOS APFS（預設）與 NTFS：rel=`2025/A.JPG`，實際檔名 `a.jpg` | 解析成功並回傳磁碟上的實際大小寫；root 比對用 `os.path.normcase` + inode／file id 相等 | I | — |
| FS-016 | Unicode NFD 檔名 | 磁碟上為 NFD `Café.jpg`（HFS+ 或手動建立），CP 送 NFC rel | 能解析到同一檔案；回報的 `rel` 為 NFC，`rel_path_hash` 以 NFC 計算 | I | #51 |
| FS-017 | 寫入 ro root | `open_write("photos","x.jpg")` | `PathViolation(reason="read_only_root")` | U/S | #48 |
| FS-018 | move 來源為 ro root | `allow_move_from_ro_roots=false`，action=move 來源在 photos | executor 自動降級為 copy，並在 plan 中標示 `downgraded=true` | U | #48 |
| FS-019 | 未知 alias | `resolve("nope","a.jpg")` | `FS_ROOT_NOT_ALLOWED` | U | #37 |
| FS-020 | 違規稽核且不重試 | 觸發任一違規 | 寫 `audit_log`（只含 alias + rel，不含絕對路徑）；錯誤分類 `permanent` | U | — |
| FS-021 | root 本身是 symlink | macOS `/tmp → /private/tmp` 作為 root | root 的 realpath 在註冊時計算一次；其下正常路徑可解析 | I | — |
| FS-022 | root 不存在（外接硬碟拔除） | 刪除 root 目錄後 resolve | `FS_ROOT_UNAVAILABLE`（transient），**不是** PATH_VIOLATION | I | #53 |
| FS-023 | 寫入目標 hardlink 防護 | dst 已存在且 `st_nlink > 1` | 拒絕覆寫（防止透過 hardlink 改到 root 外的檔案） | I/S | — |
| FS-024 | 性質 fuzz：路徑字串 | Hypothesis 依文法產生 rel：段 ∈ {`..`,`.`,``,`/`,`\`,NUL,`:`,保留名,隨機 Unicode,255+ bytes} | 結果只有兩種：在 root 之下的路徑，或 `PathViolation`；**永遠不回傳 root 外路徑**。PR 2,000 例、nightly 1,000,000 例 | P/S | DoD 5.3 |
| FS-025 | 性質 fuzz：真實樹 + 隨機 symlink | 每例在 tmp 建立隨機深度 ≤ 5 的樹，隨機放入指向內／外的 symlink 與 junction（Windows） | 任何解析結果的 realpath 都在 root 之下 | P/I/S | DoD 5.3 |
| FS-026 | import-linter 契約 | `apps/api`、`packages/db` 匯入 `fs_sandbox` 或 `inference_*` | import-linter 失敗（CI 守門） | C | — |
| FS-027 | 文字檔讀寫一律指定編碼（回歸） | repo 內所有 Python 檔的 `read_text`／`write_text`／`open` | 都帶 `encoding=`；Windows 預設 cp1252，CI 的 Windows runner 曾讀不了含 CJK 的 golden 檔 | C | #2 |

---

## 3. OUT — Output executor、journal、undo、當機恢復（02 §6.2）

### 3.1 OUT-EXE：執行

| ID | 標題 | 前置／輸入 | 預期 | 層 | # |
|----|------|-----------|------|----|---|
| OUT-EXE-001 | copy 快樂路徑 | plan 1 個 copy；FakeFS 記錄系統呼叫序列 | 序列為 `journal(intent)+fsync → write .opagent-tmp-<uuid> → blake3 == src_hash → fsync(file) → fsync(dir) → rename(noreplace) → journal(done)`；dst 內容與 src 相同 | U | #47 |
| OUT-EXE-002 | 原檔不變 | 同 001 後檢查 src | src 的 BLAKE3、mtime、size、權限全部不變 | I | #48、DoD 5.3 |
| OUT-EXE-003 | 保留 mtime | copy 後比對 | dst mtime == src mtime（copystat）；POSIX 權限位相同 | I | #48 |
| OUT-EXE-004 | 計畫後原檔被改 | plan 後修改 src 內容 | action `skipped`，error `PLAN_STALE`；不寫 dst | I | #47 |
| OUT-EXE-005 | dst 已存在且同 hash | dst 與 src 相同內容 | `conflict=exists_same` → skip（不論 policy 為 rename） | I | #48 |
| OUT-EXE-006 | 重名加後綴 | dst 已存在不同內容，policy=rename | 依序產生 `IMG_0001 (1).jpg`、`(2)`；後綴決定性（同輸入同結果） | U/I | #48 |
| OUT-EXE-007 | policy=skip／fail／overwrite_if_same_hash | 各自對 exists_diff | skip→skipped；fail→該 action failed 其他繼續；overwrite_if_same_hash→不覆寫（不同 hash） | U | #48 |
| OUT-EXE-008 | commit 時才出現的 dst | probe 後、rename 前由另一程序建立 dst | `rename(noreplace)` 得 EEXIST → 重新依 policy 處理（rename 後綴），**絕不覆寫** | I/X | #48 |
| OUT-EXE-009 | 計畫內互撞 | 兩張不同照片渲染到同一 dst | 計畫階段即標 `name_collision` 並指派後綴，執行期不撞 | U | #48 |
| OUT-EXE-010 | 大小寫不敏感目標 | 同一 plan 寫 `Food/a.jpg` 與 `food/A.JPG` 到 APFS／NTFS | 偵測目標檔系統大小寫敏感性（探測檔），視為同一路徑並套 rename 後綴 | I | #48 |
| OUT-EXE-011 | move 走垃圾桶 | action=move（已二次確認），預設 trash 保留 | copy+verify 後 src 移到 `<root>/.opagent-trash/<plan_id>/<rel>`，不 unlink；journal 記錄 | I | #48 |
| OUT-EXE-012 | move 未二次確認 | approval 缺 `move_confirmed=true` | `POST /output-plans/{id}/approve` 回 409 | I | #48 |
| OUT-EXE-013 | 跨磁碟 move | src 與 dst 在不同檔案系統（Linux CI 掛兩個 tmpfs；Windows 用兩個 VHD 或 subst 磁碟【待 M0 確認 runner 能力】） | 走 copy+verify+trash；不嘗試 rename；`fast_rename=true` 也只在同 FS 生效 | I | #48 |
| OUT-EXE-014 | 磁碟滿 | 目標為 8 MB tmpfs（或 FakeFS 在第 N 次 write 拋 ENOSPC） | tmp 檔刪除、dst 不存在；Operation `waiting`，`waiting_reason=DISK_INSUFFICIENT_*`；釋放空間後 resume 從該 action 繼續 | X | #47 |
| OUT-EXE-015 | 輸出目標離線 | apply 中途把 target root 目錄改名（模擬外接硬碟拔除） | 當前 action 回滾、Operation `waiting`（`FS_ROOT_UNAVAILABLE`／`SOURCE_OFFLINE`）而非 failed；復原後自動續跑 | X | #53 |
| OUT-EXE-016 | 檔案被鎖（Windows） | dst 目錄中同名暫存檔被另一程序以獨佔模式開啟 | transient 重試 3 次（1 s、4 s、10 s），仍失敗 → action failed，錯誤碼 `FS_FILE_LOCKED`（**提議新增至 06 §4.7**） | I | — |
| OUT-EXE-017 | 取消 | apply 第 3／10 個 action 時 cancel | 當前 action 完成後停止；plan `partially_applied`；可 undo | I | #47 |
| OUT-EXE-018 | 暫停／續跑 | action 之間 pause，再 resume | 不重做已 done；不跳過未做 | I | #47 |
| OUT-EXE-019 | 大量 move 確認字串 | 10,001 個 move | 核准需輸入確認字串，否則 409 | I | #48 |
| OUT-EXE-020 | sidecar JSON | action=sidecar_json | 寫 `<stem>.opagent.json`（含 `schema_version`、category、tags、caption、provenance、`date_source`）；原檔 bytes 不變；schema 與 `packages/schemas` 一致 | I/C | #50、DoD 5.3 |
| OUT-EXE-021 | CSV 匯出 | 3 張照片，caption 含逗號、換行、`=SUM(A1)` | RFC 4180 引號；以 `=`、`+`、`-`、`@` 開頭的值前置 `'`（防公式注入）；UTF-8（選項帶 BOM） | U/S | #50 |
| OUT-EXE-022 | 計畫摘要句 | 28,412 copy 到 13 個資料夾 | zh-TW「將複製 28,412 張到 13 個資料夾，原檔不動。可隨時復原」；en 對應句；move 時措辭不同 | U | DoD 5.3 |
| OUT-EXE-023 | 冪等重放 | 同一 plan 執行兩次（第二次在完成後） | 第二次全部 `skipped(exists_same)`，不產生新檔 | I | #47 |

### 3.2 OUT-JRN：journal

| ID | 標題 | 前置／輸入 | 預期 | 層 | # |
|----|------|-----------|------|----|---|
| OUT-JRN-001 | intent 先於任何檔案操作 | FakeFS 呼叫序列 | 第一個副作用之前，journal intent 已 commit + fsync | U | #47 |
| OUT-JRN-002 | append-only | 嘗試 UPDATE／DELETE journal row | SQLite trigger 拒絕；`seq` 單調 | U | #47 |
| OUT-JRN-003 | pre/post state 完整 | 每種 op（mkdir、copy、verify、fsync、rename、trash_move、write_sidecar） | `pre_state` 含 exists/hash/mtime；`post_state` 填於 done | U | #47 |
| OUT-JRN-004 | WAL 在 kill -9 後完整 | 寫入中 kill -9 | 重開 `PRAGMA integrity_check = ok`；最後一筆未 commit 的不存在 | X | #47 |
| OUT-JRN-005 | CP 鏡像冪等 | `POST /journal` 同一批送兩次 | CP 端無重複；以地端為準 | I | #47 |

### 3.3 OUT-CRASH：故障注入（每個步驟 kill -9，DoD 5.3）

> 執行方式：executor 在子程序執行，hook `OPAGENT_CRASH_AT=<point>` 於該點 `os._exit(137)`（Windows 用 `TerminateProcess`）；重啟後跑 recovery，再斷言共同不變量 **INV**：(a) 所有 src 的 hash 與 mtime 不變；(b) 每個 dst 要嘛不存在、要嘛 hash == src_hash；(c) 沒有殘留 `.opagent-tmp-*`；(d) journal 最終每個 action 為 done 或已回滾；(e) 再跑一次 apply 結果與無當機時逐檔相同；(f) undo 後樹狀快照與 apply 前相同。

| ID | 當機點 | 額外預期 | 層 | # |
|----|--------|----------|----|---|
| OUT-CRASH-001 | journal intent 寫入前 | action 視為未開始 | X | #47 |
| OUT-CRASH-002 | intent 已 fsync、tmp 尚未建立 | recovery 標記回滾，重跑時正常完成 | X | #47 |
| OUT-CRASH-003 | tmp 寫到一半 | 殘留 tmp 被清除 | X | #47 |
| OUT-CRASH-004 | tmp 完成、verify 前 | recovery 重新 verify 或刪 tmp 重做 | X | #47 |
| OUT-CRASH-005 | verify 後、fsync 前 | 同上；dst 不得出現未 fsync 的內容 | X | #47 |
| OUT-CRASH-006 | rename 後、journal done 前 | recovery 依 post_state 驗 dst hash 後補記 done，不重複複製 | X | #47 |
| OUT-CRASH-007 | move：trash 搬移後、done 前 | src 在 trash 中且 dst 正確；補記 done；undo 可還原 | X | #48 |
| OUT-CRASH-008 | move：trash 搬移進行中（跨 FS 情境） | src 仍存在或已完整在 trash，不會兩邊都缺 | X | #48 |
| OUT-CRASH-009 | sidecar 寫入中 | sidecar 要嘛完整要嘛不存在（tmp + rename） | X | #50 |
| OUT-CRASH-010 | undo 進行中（每個 undo 步驟各一次參數化） | 再次 undo 可完成；INV(f) 成立 | X | #47 |
| OUT-CRASH-011 | recovery 本身進行中再當機 | 第二次 recovery 收斂到相同結果（recovery 冪等） | X | #47 |
| OUT-CRASH-012 | 10,000 action 隨機當機 50 次（nightly） | INV 全部成立；0 個檔案遺失或損毀 | X | DoD 5.3 |

### 3.4 OUT-UNDO：復原

| ID | 標題 | 前置／輸入 | 預期 | 層 | # |
|----|------|-----------|------|----|---|
| OUT-UNDO-001 | undo copy 回到原狀 | apply 前後各取目錄樹快照（路徑、hash、mtime） | undo 後與 apply 前快照相同，包含 apply 建立的空資料夾被移除 | I | #47、DoD 5.3 |
| OUT-UNDO-002 | dst 被使用者改過 | apply 後修改其中 2 個 dst | 這 2 個標 `conflict`（`UNDO_TARGET_MODIFIED`），不刪除；其餘正常 undo | I | #47 |
| OUT-UNDO-003 | undo move | 從 trash 還原到原 rel | src 回到原位、hash 相同；dst 刪除 | I | #48 |
| OUT-UNDO-004 | 原位置已被佔用 | undo move 時原路徑已有新檔 | 該筆 `conflict`，不覆寫 | I | #48 |
| OUT-UNDO-005 | undo partially_applied | 只做了 3／10 | 只反轉那 3 個 | I | #47 |
| OUT-UNDO-006 | undo 冪等 | undo 兩次 | 第二次 no-op | I | #47 |
| OUT-UNDO-007 | trash 已過保留期 | `clk` 前進 N+1 天、trash 已清 | move 項目 `conflict(trash_expired)`，copy 項目照常 | I | #48 |
| OUT-UNDO-008 | undo 是 Operation | 觸發 undo | 建立 `output.undo` Operation（verify 20 → revert 80），可暫停、有 ETA | I | #33 |
| OUT-UNDO-009 | undo sidecar | 原本無 sidecar → 刪除；原本有 → 還原備份 | 兩種情況都回到原狀 | I | #50 |

---

## 4. SCAN — 掃描、hash、解碼、EXIF（02 §4.1、§10.5）

| ID | 標題 | 前置／輸入 | 預期 | 層 | # |
|----|------|-----------|------|----|---|
| SCAN-001 | globs | include `**/*.{jpg,heic}`、exclude `**/tmp/**` | 只列出符合者 | U | #37 |
| SCAN-002 | 永遠排除的檔案 | `.DS_Store`、`Thumbs.db`、`desktop.ini`、`.opagent-trash/**`、`.opagent-tmp-*`、`@eaDir/**` | 一律不出現在結果中 | U | — |
| SCAN-003 | BLAKE3 golden | `img:basic` 5 張 | content_hash 與 golden 相同 | U | #51 |
| SCAN-004 | 增量：未變更不重讀 | 掃兩次，第二次前不改檔 | 第二次 `bytes_read == 0`（本地快取 `(abs,size,mtime,file_id)→hash` 命中） | I | #51 |
| SCAN-005 | 變更檔重新 hash | 改 1 張內容（mtime 改變） | 只重算那張；同 location 指向新 asset | I | #51 |
| SCAN-006 | **搬移資料夾 0 重新推論** | 掃描 + 推論後把整個資料夾改名，再掃描並建立同設定的 Job | asset 數不變、`asset_location` 更新、Job counters `cached == total`、FakeBackend 呼叫數 0 | I | #51、DoD 5.3 |
| SCAN-007 | 完全重複 | 同內容兩個位置 | 1 個 asset、2 個 asset_location | I | #51 |
| SCAN-008 | HEIC | `img:heic`（iPhone 直式、HDR gain map、Live Photo 靜態圖） | 解碼成功、t256/t512 webp 產生、方向正確（與 golden 縮圖 SSIM ≥ 0.98） | I | #52 |
| SCAN-009 | RAW 內嵌預覽 | `img:raw`（DNG、CR3、NEF、ARW、RAF；來源 raw.pixls.us CC0） | 以內嵌 JPEG 產生縮圖；沒有內嵌預覽者標 `DECODE_RAW_MISSING`（warning）並略過 | I | #52 |
| SCAN-010 | 損毀檔 | 截斷 JPEG、0 byte、副檔名 `.jpg` 的文字檔、EXIF 區段損毀但影像可讀 | 前三者 `DECODE_FAILED`（permanent，掃描繼續）；第四者成功並忽略壞 EXIF | I | #52 |
| SCAN-011 | EXIF 方向 1–8 | `img:orientation` 8 張 | 縮圖方向與 golden 一致 | I | #52 |
| SCAN-012 | EXIF 時區優先序 | (a) DateTimeOriginal+OffsetTimeOriginal；(b) 無 offset 有 GPS（東京座標）；(c) 都沒有，workspace tz=Asia/Taipei | (a) 用 offset；(b) 以 GPS 時區（離線 tz 資料）推得 +09:00；(c) +08:00；`date_source` 分別為 `exif_offset`、`exif_gps_tz`、`exif_local_assumed` | U | #40 |
| SCAN-013 | 無 EXIF | `img:no-exif` | `taken_at=mtime`、`date_source=mtime` | U | #40 |
| SCAN-014 | 垃圾日期 | `0000:00:00 00:00:00`、`2099:13:45`、空字串 | 視為缺失 → 走 mtime | U | #40 |
| SCAN-015 | 權限錯誤 | 一個子資料夾 chmod 000（POSIX）／ACL deny（Windows） | 列入略過清單 `FS_PERMISSION_DENIED`，其餘照常；macOS TCC 的 EPERM 觸發 doctor `fs.roots` fix | I | #37 |
| SCAN-016 | OneDrive 雲端佔位檔 | Windows：設 `FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS` 的檔案（CI 以 `attrib`／API 模擬【待 M0 確認可模擬】） | 不開檔讀取；略過原因 `cloud_placeholder` 出現在掃描報告 | I | #54 |
| SCAN-017 | Photos Library 封裝 | 資料夾 `Photos Library.photoslibrary` | 略過 `photos_library_package`；UI 提示文案 | U | #54 |
| SCAN-018 | 來源中的 symlink | `follow_symlinks=false` | 略過並列出 | U | — |
| SCAN-019 | 分頁與游標續跑 | 3,500 檔；掃描到第 2 頁時 kill -9 worker | 重啟後從游標續掃，總數 3,500，不重複 upsert | X | #33 |
| SCAN-020 | 來源離線 | 掃描中把 root 改名 | Operation `waiting`、`SOURCE_OFFLINE`；改回後 30 s 內自動繼續 | X | #53、DoD 5.3 |
| SCAN-021 | ThumbHash 與感知 hash | 任一 JPEG | 寫入 `asset.thumbhash`（≤ 25 bytes）、`phash`、`dhash`；同圖縮放 50% 後 pHash Hamming ≤ 6 | U | #87 |
| SCAN-022 | 影片 v0.1 略過 | `.mp4`、`.mov` | 略過原因 `video_unsupported_v0_1`（或 `FFMPEG_MISSING` warning），不算錯誤 | U | #99 |
| SCAN-023 | Unicode 與長檔名 | NFC、NFD、255 bytes 檔名、emoji 檔名 | 全部被索引；`rel_path` 存 NFC | I | — |
| SCAN-024 | 輸出資料夾不被掃描 | 輸出 target 位於來源 root 之下（使用者仍堅持時） | 掃描自動排除該 target 子樹；preflight 另有 `OUTPUT_TARGET_INSIDE_SOURCE` | I | #47 |
| SCAN-025 | 掃描進度型態 | 10,000 檔，無上次總數 | enumerate 階段 `indeterminate` 並回報「已找到 N 張 · X 張/秒」；數完後轉 determinate；有上次總數時直接 rough | I | #30 |

## 5. Q — 佇列與 Job 生命週期（02 §4.3；真 PostgreSQL）

> fixture：`embedded_pg`（template DB 複製）＋ `fake_worker(n)`（同進程模擬 Worker 協定 client）＋ `clk`。

| ID | 標題 | 前置／輸入 | 預期 | 層 | # |
|----|------|-----------|------|----|---|
| Q-001 | SKIP LOCKED 不重複派工 | 1,000 個 pending item；8 個 fake worker 並行 `POST /leases {max_items:16}` 直到清空 | 每個 item 恰被 lease 一次；無 deadlock；總耗時 < 10 s | I | #3 |
| Q-002 | lease 過期回收 | lease 後不續租，`clk` 前進 121 s，sweeper 執行 | item 回 `pending`、`attempts+1`；`opagent_lease_expired_total` +1 | I | #3 |
| Q-003 | 心跳續租 | 每 30 s heartbeat 帶 lease ids，持續 5 分鐘 | lease 不過期 | I | #3 |
| Q-004 | 冪等回報 | 同一 `(job_item_id, attempt, idempotency_key)` 回報兩次 | `result` 只有 1 列；第二次回 200（已接受） | I | #3 |
| Q-005 | 過期後遲到的結果 | lease 過期並由 worker B 重新領取後，worker A 才回報 | 以 `(asset_id, task_config_hash, model_fingerprint)` unique 去重；不產生重複；counters 正確 | I | #3 |
| Q-006 | transient 重試與退避 | `fb:inject(OOM, times=2)` | 退避 10 s、60 s（`clk`），第 3 次成功；`attempts=3` | I | #3 |
| Q-007 | OOM 批次減半 | `fb:inject(OOM)` 於 batch=16 | 重試時 batch=8；再 OOM → 4；記錄於 provenance | I | #3 |
| Q-008 | permanent 不重試 | `fb:inject(BAD_INPUT)` | item `failed`、`error_code=DECODE_FAILED`、attempts=1 | I | #3 |
| Q-009 | 暫停（graceful） | 執行中 pause | 不再發新 lease；已 lease 批次完成後停止；Job `paused`、Operation `paused` | I | #47 |
| Q-010 | 暫停（immediate） | `pause_mode=immediate` | 當前 batch 中止、lease 歸還、item 回 pending（attempts 不增加） | I | #47 |
| Q-011 | 重啟後續跑 | 50% 時重啟 api 與 worker 程序 | 進度從 DB 繼續；最終 `done+cached+failed+review+skipped == total` | X | #3、DoD 5.1 |
| Q-012 | 取消 | 執行中 cancel | `cancelling`→`cancelled`；已寫入 result 保留；之後同設定新 Job 命中快取 | I | #47 |
| Q-013 | Worker 斷線 | 處理中切斷 WS 60 s 再恢復 | 已 lease 批次在 worker 端完成並存 outbox；重連後補傳；CP 去重接受 | X | #3 |
| Q-014 | 快取命中不派工 | 先跑完 Job A，再建立同 config 的 Job B | Job B 所有 item 為 `cached`；`POST /leases` 回 204；Operation `infer` 階段 `skipped` | I | #51 |
| Q-015 | **只調門檻不重算** | Job 完成後只改 thresholds 再「重套」 | 不建立任何 job_item 派工；只重跑 postprocess；preflight 回 `JOB_NOTHING_TO_DO`（warning）與「只重套門檻」fix | I | #46 |
| Q-016 | 換模型 variant → 重算 | 改 VLM variant（fingerprint 不同） | 只有需要該 VLM 的 item 重新派工；embedding 結果仍命中 | I | #51 |
| Q-017 | model affinity | worker 回報 `loaded_models=[siglip2]`；佇列有 embed 與 vlm item | 先派 embed item | I | — |
| Q-018 | interactive lane | 批次執行中送出「10 張預覽」 | 預覽 item 在下一個 lease 優先派出；不中斷當前 batch；預覽完成 < 20 s（FakeBackend 延遲 100 ms/張） | I | #46 |
| Q-019 | 背壓 | worker 本地佇列上限 4 batch | 前處理快於推論時，第 5 個 batch 不領取 | U | — |
| Q-020 | 同來源衝突 | 來源 X 已有 running Job，再建立一個 | preflight `JOB_CONFLICT_RUNNING`，fix「排在它後面」 | I | #32 |
| Q-021 | counters 不變量 | Hypothesis 產生隨機事件序列（lease、result、fail、expire、cancel） | 任一時刻 `done+cached+failed+review+skipped+pending+leased+running == total` | P | #30 |
| Q-022 | kill -9 worker 不產生重複結果 | 推論中 `kill -9` worker（chaos runner） | lease 到期後由新 worker 續做；`result` 無重複；最終計數正確 | X | #3、DoD 5.1 |
| Q-023 | 10 張預覽分層抽樣 | 1,000 張來源（多資料夾、多月份）、`seed=42` | 10 張涵蓋最多資料夾與月份；同 seed 結果相同 | U | #46 |
| Q-024 | 預覽只改門檻 / 樣板 | 預覽完成後改 thresholds 或 path_template | 結果 < 200 ms 更新，FakeBackend 呼叫數 0（不跑 GPU） | I/F | #46、DoD 5.3 |
| Q-025 | 預覽改類別描述 | 改某類 description | 圖像 embedding 命中快取；只重算文字 embedding 與受影響的 VLM 呼叫 | I | #46 |
| Q-026 | 電池暫停 | FakePower：拔電源 | 10 s 內重型 Operation 為 `paused`、`waiting_reason=PAUSED_ON_BATTERY`；搜尋仍可用；接回 → 自動繼續 | I | #9、DoD 5.2 |
| Q-027 | GPU 被占用 / 全螢幕 | `hw:game-running-5gb`、FakeFullscreen=true | `PAUSED_GPU_CONTENDED`；GPU 釋放後自動繼續；選「改用 CPU」可繼續 | I | #9、DoD 5.2 |
| Q-028 | 只在閒置時處理 | `only_when_idle=true`，FakeIdle 秒數 = 5 | `PAUSED_NOT_IDLE`；閒置達門檻後繼續；「現在就處理」覆寫 | I | #32 |

---

## 6. INF — 推論層（03；FakeInferenceBackend 於 PR，真模型於 nightly）

### 6.1 INF-REG：Model registry

| ID | 標題 | 前置／輸入 | 預期 | 層 | # |
|----|------|-----------|------|----|---|
| INF-REG-001 | 內建 YAML 與 lock 全部通過 schema | `registry/models/*.yaml`、`registry/runtimes/*.yaml`、`gpu_classes.yaml`、`registry/pins.lock` | 全部通過 JSON Schema；catalog 不含 `size`／`sha256`；每個 lock 條目指向存在的 variant 與檔案；合併後每個 `pinned` variant 的每個檔案都有 `size > 0` 與 64-hex `sha256`（ADR-0002） | C | #15 |
| INF-REG-002 | 缺 sha256 拒絕 | 使用者 YAML 檔案缺 sha256；內建 lock 條目 sha256 缺漏或格式錯；內建 variant 沒有 lock 條目 | 使用者檔：該模型標 `invalid`，不影響其他；內建 lock 條目不完整：載入錯誤（CI 失敗）；內建 variant 無 lock 條目：載入為 `unpinned`（ADR-0002） | U | #24 |
| INF-REG-003 | 使用者自訂 YAML | 在 `models/custom/` 放合法 YAML | Models 頁出現、跑過 compat；`source=user` | I | #24 |
| INF-REG-004 | 自訂 YAML 壞掉 | 語法錯誤／欄位錯誤 | 顯示錯誤與檔名行號，registry 其餘照常 | U | #24 |
| INF-REG-005 | 每任務預設 | `tasks_default_rank` 與 `task_default_model` 覆寫 | caption、classify、embed 各自解析到指定 variant | U | #25 |
| INF-REG-006 | 預設組合授權 | 檔位預設組合的所有 variant | `license.weights` 皆為 Apache-2.0 或同等寬鬆，`redistributable=true`；否則 CI 失敗 | C | #21 |
| INF-REG-013 | 發版前全部釘選 | release gate：所有內建 variant 與 runtime pack | 每個內建 variant 都是 `pinned`；每個 runtime pack 都有 sha256；否則 release gate 失敗（與 INF-REG-001 合起來等價於原本的「全部有 sha256」，ADR-0002） | G | #15 |
| INF-REG-014 | 空的 catalog 不會讓 recommend 當掉（回歸） | 沒有任何 model 與 runtime 的 registry | 回傳建議並列為未釘選，不丟 KeyError（曾讓 job.start 回 500） | U | #24 |

### 6.2 INF-CONF：InferenceBackend／Adapter 一致性套件（`packages/inference-sdk/conformance`）

> 同一套測試對每個 adapter 執行：FakeBackend（PR）、llama.cpp CPU（PR，用 tiny GGUF 測試模型【待 M0 確認可用的小模型】）、ORT CPU（PR，用 tiny ONNX）、llama.cpp CUDA/Vulkan/Metal、openai-compatible（nightly）。

| ID | 標題 | 前置／輸入 | 預期 | 層 | # |
|----|------|-----------|------|----|---|
| INF-CONF-001 | `describe()` 欄位完整 | — | adapter_key、model_fingerprint、task_types、preferred_input、max_batch、est_vram_mb、supports_structured_output 都有值 | C | #23 |
| INF-CONF-002 | load 可重入 | — | 連續 `load()` 兩次不重複佔用資源；`unload()` 後 `run_batch` 拋 `ModelUnavailable` | C | #23 |
| INF-CONF-003 | 一對一輸出 | — | N 張輸入 → N 個 ItemOutput，item_id 集合相同（順序不保證） | C | #23 |
| INF-CONF-004 | 空批次 | — | `run_batch([])` 回 `[]`，不載入模型 | C | — |
| INF-CONF-005 | 錯誤映射 | 注入 OOM／timeout／壞輸入／runtime crash | `InferenceError(code=OOM\|TIMEOUT\|BAD_INPUT\|RUNTIME, retryable=T/T/F/T)`；對應 06 錯誤碼 `MODEL_OOM`、`RUNTIME_TIMEOUT`、`DECODE_FAILED`、`RUNTIME_CRASHED` | C | #23 |
| INF-CONF-006 | deadline | `deadline_s=0.5`，模型延遲 2 s | TIMEOUT；子行程請求被取消 | C | — |
| INF-CONF-007 | embedding 形狀 | — | 維度符合 registry；L2 norm ∈ [0.999, 1.001] | C | #64 |
| INF-CONF-008 | 決定性 | temperature 0、固定 seed，同圖跑兩次 | FakeBackend 逐字相同；真模型 category 相同且 caption 相同率 ≥ 95%（nightly，**待 M0 確認**） | C/N | #43 |
| INF-CONF-009 | 結構化輸出 | 對 `img:golden-100` 用 fused schema | FakeBackend 100%；真模型 nightly ≥ 99% JSON 合法（S3） | C/N | #43、DoD 5.3 |
| INF-CONF-010 | 同 schema 跨 runtime | 同一任務分別經 llama.cpp 與 openai-compatible（指向同一 llama-server） | `ItemResult` 通過同一 pydantic schema；欄位集合相同 | C/N | #23 |
| INF-CONF-011 | 不接觸路徑 | 靜態檢查 `BatchRequest`／`ImageInput` 傳入 adapter 時 `path is None` | adapter 程式碼中無 `open(`／`Path(` 於影像輸入（ruff 自訂規則 + 執行期斷言） | C/S | — |
| INF-CONF-012 | `health()` | — | 回傳 `vram_used_mb`（無 GPU 時為 0） | C | — |
| INF-CONF-013 | 載入進度回報 | `load(opts, reporter)` | 至少回報一個子階段；無法取得時以時間模型進度且 ≤ 95% 直到 ready | C | #33 |

### 6.3 INF-JSON：fused 結構化輸出驗證與修復（03 §3.0、§2.3）

| ID | 標題 | 前置／輸入 | 預期 | 層 | # |
|----|------|-----------|------|----|---|
| INF-JSON-001 | 合法輸出 | 符合 `opagent.fused.v1` | 解析為 CaptionOut + TagOut + ClassifyOut | U | #43 |
| INF-JSON-002 | markdown 圍欄與前後文字 | ```` ```json {...} ``` ```` 加上「以下是結果」 | 修復成功，`repaired=true` | U | #43 |
| INF-JSON-003 | 截斷 | 因 max_tokens 截斷的 JSON | `json-repair` 成功則用；失敗 → 以 temperature 0 重問「只輸出 JSON」一次 → 仍失敗拋 `OutputParseError`，保存 raw，item `failed` | U | #43 |
| INF-JSON-004 | enum 外的類別 | `category="美食"`（顯示名）或 `"Food"` | 依 alias／顯示名映射回 `food`；無法映射 → `other` 並 `flags=["enum_violation"]` | U | #41 |
| INF-JSON-005 | 多餘欄位 | `additionalProperties` | 移除並記 warning，不視為失敗 | U | — |
| INF-JSON-006 | 長度上限 | 25 個 tags、單 tag 60 字、caption 900 字 | tags 截為 20、單 tag ≤ 40、caption ≤ 400（於字元邊界） | U | #43 |
| INF-JSON-007 | schema key 順序 | 送出給 runtime 的 schema | `caption → tags → category`（03 §3.0 預設） | U | #43 |
| INF-JSON-008 | 性質：解析器不崩潰 | Hypothesis 以合法 JSON 為種子做 byte 級突變 | 只會得到合法結果或 `OutputParseError`；不拋其他例外 | P | #43 |
| INF-JSON-009 | caption 語言與長度設定 | CaptionConfig `lang=zh-Hant, length=short`／`en, long` | prompt 渲染含對應指示；FakeBackend 回傳後語言標記正確 | U | #43 |
| INF-JSON-010 | 同義詞合併 | tags `["狗","犬","dog"]` | canonical key 皆為 `animal.dog`，只保留一個 | U | #44 |
| INF-JSON-011 | 新詞上限 | 受控詞彙外的新 tag 12 個，上限 5 | 只保留分數最高 5 個，其餘丟棄並計數 | U | #44 |
| INF-JSON-012 | 圖中文字標記 | 模型回傳 `contains_image_text=true` | 寫入結果欄位，供搜尋與 05 §3.9 injection 偵測使用 | U | #43 |

### 6.4 INF-CAS：cascade 路由（03 §3.3）

| ID | 標題 | 前置／輸入 | 預期 | 層 | # |
|----|------|-----------|------|----|---|
| INF-CAS-001 | 規則層命中不推論 | 截圖規則命中 | embed 與 VLM 呼叫數 0；`source=rule` | I | #45 |
| INF-CAS-002 | 高信心不送 VLM | `fb:scores(high)` 100 張 | VLM 呼叫數 0 | I | #45、DoD 5.3 |
| INF-CAS-003 | 低信心送 VLM | `fb:scores(low)` | VLM 收到 enum = top-k（3–5）+ other，prompt 含 hints | I | #45 |
| INF-CAS-004 | VLM 仍低 → 待確認或其他 | `fb:vlm(p=0.4)` | `needs_review` 或 `other`（依 CORE-THR）；不失敗 | I | #49 |
| INF-CAS-005 | 每筆記錄決策層與 fingerprint | 任意 Job | 每個 result 有 `source` 與 `model_fingerprint`；Lightbox 資訊面板 API 可讀出 | I | #45、#89、DoD 5.3 |
| INF-CAS-006 | 無 VLM（快速模式） | 只安裝 SigLIP2 | cascade 停在 embedding；低信心 → needs_review；Job 不失敗 | I | #28 |
| INF-CAS-007 | 序列模式呼叫順序 | `hw:rtx4060-laptop` + 8B variant（degraded `VRAM_SEQUENTIAL`） | 呼叫序列：load(embed) → 全批 embed → unload → load(vlm) → 只處理需要的 item | I | #28 |
| INF-CAS-008 | prototype 權重 α | 範例數 0、2、5、8 | α = 1.0、golden 內插值、0.4、0.4；≥ 8 張時使用 k-medoids(k=2) 多原型 | U | #42 |
| INF-CAS-009 | 反例降權 | 有 negative_examples 且與反例更相近 | 該類分數降低（golden） | U | #42 |
| INF-CAS-010 | 全庫預估命中數 | 自訂類別 + 3 張範例，1,000 張已有 embedding | 只用向量運算（不呼叫 VLM）回傳預估命中數與分布，< 500 ms | I/F | #42、#46 |

### 6.5 INF-CAL、INF-VRAM、INF-OOM、INF-RT：校準、資源、降級、runtime 選擇

| ID | 標題 | 前置／輸入 | 預期 | 層 | # |
|----|------|-----------|------|----|---|
| INF-CAL-001 | 溫度擬合 | 合成 logits + 標籤（已知最佳 T=0.07） | 擬合 T ∈ [0.06, 0.08]；ECE 低於預設 T | U | #45 |
| INF-CAL-002 | Platt 序列化 | 擬合後存入 `taxonomy.calibration`，重新載入 | 套用結果相同 | U | #45 |
| INF-CAL-003 | 真模型校準 | nightly，`img:by-content-12`（每類 50 張） | ECE ≤ 0.10（**待 M0 確認**），記錄於 benchmark 報告 | N | #45 |
| INF-VRAM-001 | 預算公式 golden | total 16 GB、safety 0.92、external 1.5 GB、desktop 0.8 GB | budget = 12.42 GB（±0.01） | U | #14 |
| INF-VRAM-002 | 並載判定 | need(vlm)=8.5、small slot=1.5、budget=14.7 | 並載；compat `ok` | U | #14 |
| INF-VRAM-003 | LRU 卸載與引用計數 | 已載 A（使用中）、B（閒置），需要載入 C | 卸載 B 不卸載 A；large slot 優先於 small slot | U | — |
| INF-VRAM-004 | 閒置卸載 | `clk` 前進 10 分鐘 large slot 閒置 | 自動 unload，發 `model.unloaded` | U | — |
| INF-VRAM-005 | 實測回寫 | 載入後 NVML 量到 9.1 GB（估算 8.5） | 寫入 `perf_observation`；下次 compat 用 9.1 且 `source=observed` | I | #15 |
| INF-VRAM-006 | Mac 統一記憶體 | `hw:m3-16`（recommendedMaxWorkingSetSize=10.7 GB fixture） | budget = 10.7 − 其他 app − 4 GB 保留 → golden；8B Q5 為 blocked、4B Q4 為 ok | U | #13 |
| INF-VRAM-007 | DXGI／Vulkan provider | `hw:amd-rx7800-win`（DXGI Budget fixture）、`hw:intel-arc-linux`（VK_EXT_memory_budget fixture） | 解析出 total/used/budget；無資料時 compat 用總量並標 `estimated` | U | #26 |
| INF-VRAM-008 | 外部佔用不改等級 | `hw:game-running-5gb` | compat 等級不變；`fits_now=false`、`fits_now_reason=GPU_BUSY_EXTERNAL` | U | #14 |
| INF-OOM-001 | llama-server 崩潰 | FakeRuntime 第一次請求 SIGKILL | 重啟一次並 `--parallel` 減半；第二次崩潰 → 改用下一個 variant；provenance 記錄 fallback | I | #28 |
| INF-OOM-002 | ORT OOM | `fb:inject(OOM)` 持續 | batch 16→8→4→2→1，仍 OOM → 降 `max_pixels` 一檔；最後失敗為 `MODEL_OOM` | U | #28 |
| INF-OOM-003 | CUDA pack 連續崩潰 → Vulkan | CUDA FakeRuntime 崩潰兩次 | doctor 產生「改用 Vulkan」建議；選擇後 Job 繼續並完成 | I | #28、DoD 5.2 |
| INF-RT-001 | 5070 Ti（Win，驅動 580，cc 12.0） | `hw:rtx5070ti-win11` | packs=`[llamacpp-cuda13]`；VLM=Qwen3-VL-8B Q5_K_M；ORT=DirectML；只推薦一個組合，附下載 GB 與分鐘數 | U | #13、#22 |
| INF-RT-002 | RTX 4060 8 GB 筆電 | `hw:rtx4060-laptop` | Qwen3-VL-4B Q4_K_M；8B Q4 為「序列模式」選項 | U | #13 |
| INF-RT-003 | Mac M2／M3 16 GB | `hw:m2-air-16`、`hw:m3-16` | 不需要 pack（Metal 內建）；4B Q4；CoreML | U | #13 |
| INF-RT-004 | Mac 8 GB | `hw:m1-8` | 2B 或 Gemma 4 E2B；建議「快速模式」 | U | #13 |
| INF-RT-005 | 無 GPU | `hw:no-gpu-linux` | 預設不跑 VLM（快速模式）；`GPU_NOT_FOUND` warning；`PERF_CPU_ONLY_SLOW` 若選 VLM | U | #28 |
| INF-RT-006 | AMD／Intel | `hw:amd-rx7800-win`、`hw:intel-arc-linux` | `llamacpp-vulkan` | U | #22 |
| INF-RT-007 | 舊驅動 | `hw:nvidia-drv535` | cuda13 不可用；若符合 cuda12 → 建議 cuda12；否則 `GPU_DRIVER_TOO_OLD` 並附 Vulkan 退路 | U | #28 |
| INF-RT-008 | Blackwell + cuda12.4 pack | `hw:blackwell-cuda12pack` | `GPU_ARCH_UNSUPPORTED`，primary fix = 下載 cuda13 pack | U | #22 |
| INF-RT-009 | Linux NVIDIA | `hw:linux-rtx3090` | cuda13（或 12）pack + 建議 `ort-cuda` | U | #22 |
| INF-RT-010 | 不支援平台 | `hw:intel-mac`、`hw:linux-arm64` | 明確「不支援」訊息與文件連結；不崩潰 | U | #1 |
| INF-RT-011 | 性質：永遠有退路 | Hypothesis 組合 hw 探針欄位 | 恰一個推薦組合；必有一條 CPU 可執行路徑（llama.cpp CPU 或 SigLIP 快速模式） | P | #28 |
| INF-RT-012 | 不推薦未釘選 variant | 最佳 variant 為 `unpinned`，次佳為 `pinned` | 跳過 unpinned，推薦次佳 pinned variant；說明列出「尚未釘選」（ADR-0002） | U | #28 |

---

## 7. MDL、DL、DOC、DIAG — 模型管理、下載、環境醫生、診斷（06 §1–§3）

### 7.1 MDL：Model Manager

| ID | 標題 | 前置／輸入 | 預期 | 層 | # |
|----|------|-----------|------|----|---|
| MDL-001 | compat 判定順序 | 平台不符的 variant | 立即 `blocked RUNTIME_UNSUPPORTED_PLATFORM`；不再評估後續硬性條件，但仍收集 prep | U | #14 |
| MDL-002 | compat golden 矩陣 | 6 個 hw profile × 8 個 variant（48 組參數） | 每組 `level`、`reasons[]`、`prep[]` 與 `fixtures/golden/compat_matrix.json` 相同 | U | #14 |
| MDL-003 | prep 不影響等級 | 未下載、未裝 pack、未同意授權 | `level` 不變，prep 依序列出 | U | #14 |
| MDL-004 | compat 快取失效 | 改變 hardware digest／安裝 pack／registry 版本 | `inputs_hash` 改變 → 重算；其他情況命中快取 | U | #14 |
| MDL-005 | 四處一致 | 選擇器、模型卡、精靈、preflight 四個 UI 位置 | 皆讀同一個 compat 物件；E2E 斷言四處的徽章文字與 reason code 相同 | E | #14 |
| MDL-006 | 模型卡資訊 | 未下載的 variant | 顯示大小、VRAM／RAM、授權、能力；速度欄標「估算」，有 perf_observation 時標「實測」 | C/E | #15 |
| MDL-007 | 刪除 | 模型使用中 → 刪除 | `MODEL_IN_USE` blocker；Job 完成後刪除成功、空間釋放、`model.removed` | I | #19 |
| MDL-008 | 搬移目錄 | 10 GB（稀疏檔 fixture）搬到另一目錄 | `model.move` Operation：prepare→copy→verify→switch，有進度與 ETA；完成後 `model.load` 成功 | I | #19、DoD 5.2 |
| MDL-009 | 搬移取消回滾 | copy 50% 時取消 | 原目錄完整可用；目標暫存清除 | I | #19 |
| MDL-010 | 搬移中當機 | copy 中 kill -9 | 重啟後從檔案清單續跑，或回滾；不出現兩邊都不可用 | X | #19 |
| MDL-011 | 授權同意 | 預設組合（Apache-2.0）／非商用權重 | 前者不需勾選；後者 `LICENSE_CONSENT_REQUIRED`，同意後寫 `license_acceptance(license_hash)`；授權文字改變（hash 不同）需重新同意 | I | #21 |
| MDL-012 | `list_models()` 相容欄位 | 呼叫 | 含 compat 物件與舊欄位 `fits_now` | C | #14 |
| MDL-013 | 回歸：徽章載入中 | 首次設定的方案卡，模型資料尚未回來 | 不顯示「? 未檢查」徽章，改顯示載入中；資料回來後才顯示與其他三處相同的徽章（MDL-005 偶發失敗） | U | #14 |

### 7.2 DL：Download Manager（`hf:<scenario>` 錄製伺服器）

| ID | 標題 | 前置／輸入 | 預期 | 層 | # |
|----|------|-----------|------|----|---|
| DL-001 | resolve 精確總量 | `hf:ok` | 開始前即知總 bytes；小檔（config、tokenizer）先下 | I | #17 |
| DL-002 | 完整下載 | `hf:ok` | sha256 驗證通過 → installation 寫入 → `model.installed` 事件 → compat 失效 | I | #17 |
| DL-003 | 暫停與續傳 | 下載到 40% 暫停，再續傳 | 續傳請求帶 `Range: bytes=<written>-` 與 `If-Range`；伺服器日誌顯示已下 bytes 未重送 | I | #16、DoD 5.2 |
| DL-004 | 斷網續傳 | `hf:drop-at-50%`（伺服器在 50% 切斷連線） | 退避 2 s 後重試並從斷點續傳；總傳輸量 ≤ 檔案大小 × 1.02 | X | #16、DoD 5.2 |
| DL-005 | 真取消 ≤ 2 s | 下載中取消 | 伺服器觀察到 socket 關閉時間 ≤ 2 s；預設刪除 `.part`；選「保留」時保留 | I | #16、DoD 5.2 |
| DL-006 | sha 不符 | `hf:bad-sha` | 刪檔自動重下 1 次 → 仍不符 → `DL_CHECKSUM_MISMATCH`；**不**寫 installation | I | #17、DoD 5.2 |
| DL-007 | 遠端檔案變更 | `hf:etag-changed`（If-Range 回 200） | 捨棄該檔重下，warning `DL_REMOTE_CHANGED` | I | #17 |
| DL-008 | 429 | `hf:429-retry-after-5` | 等 5 s（`clk`）；分段數減少；`DL_RATE_LIMITED` | I | #17 |
| DL-009 | 401／403／404 | `hf:401`、`hf:403-gated`、`hf:404` | `HF_AUTH_REQUIRED`、`HF_GATED_ACCESS_PENDING`、`DL_NOT_FOUND`（permanent） | I | #17 |
| DL-010 | 磁碟預檢 | FakeDisk free = 需求 − 1 MB | 下載前 blocker `DISK_INSUFFICIENT_MODELS`；伺服器未收到任何 GET | I | #17、DoD 5.2 |
| DL-011 | 執行中磁碟被吃掉 | 下載中 FakeDisk free 降到剩餘需求以下 | `paused`（auto）`waiting_reason=DISK_INSUFFICIENT_MODELS`；恢復空間後（勾自動繼續）繼續 | X | #17 |
| DL-012 | 佇列預檢 | 兩個排隊下載共需 31 GB，free 24 GB | 第二個在排入時即顯示 blocker 文案「排隊的 2 個下載共需 31 GB」 | U | #17 |
| DL-013 | 分段並行 | 1 GB 檔，預設 4 段；全域 8 連線上限 | 伺服器同時最多 8 條連線；`.part.json` 每段 written 正確 | I | — |
| DL-014 | 重啟自動續傳 | 下載中 kill -9 worker 後重啟 | Operation `interrupted` → `auto_resume` 續傳；Activity Center 顯示「已自動繼續」 | X | #33 |
| DL-015 | 鏡像切換 | `hf:5xx-x3` 主來源，鏡像正常 | 第 3 次 5xx 後切換到鏡像，續傳同一 `.part`；warning 顯示切換 | I | #17（v0.2 鏡像 UI） |
| DL-016 | 只有鏡像 metadata | 官方不可達、鏡像提供 sha | `DL_UNVERIFIED_SOURCE`，需使用者確認才繼續 | I | #17 |
| DL-017 | finalize 原子性 | finalize 中 kill -9 | 重啟後狀態只可能是「未安裝（有 .part）」或「已安裝」 | X | #17 |
| DL-018 | Windows 無 symlink 權限 | 模擬 `CreateSymbolicLink` 失敗 | 改用 hardlink 或 copy；安裝成功 | I | — |
| DL-019 | 下載不打斷 Job | Job 執行中下載 1 GB 模型（本機錄製伺服器限速 100 MB/s） | Job 吞吐下降 < 10%；Job 不中斷（nightly 量測） | F | #18 |
| DL-020 | onboarding 下載順序 | 推薦組合含 SigLIP2 + VLM | SigLIP2 排第一；完成後立即開始建索引，以文搜圖在 VLM 完成前可用 | I/E | DoD 5.2 |
| DL-021 | 限速 | 設定 20 MB/s | 10 秒平均 ∈ [18, 22] MB/s | I | — |
| DL-022 | 優先下載 | 佇列第 2 位按「優先下載」 | 目前下載暫停（保留斷點），被提升者開始 | I | — |
| DL-023 | 退避中取消（ADR-0004） | 來源連線被拒，下載在退避重試中（`waiting: NET_UNREACHABLE`）→ 取消 | Operation 顯示 `waiting` + NET_UNREACHABLE 問題卡（retry／鏡像／代理）；取消後 ≤ 2 s 到 `cancelled`；不再對來源發出請求；`.part` 已刪除 | I | #24、#32 |

### 7.3 DOC：Environment Doctor（`hw:<profile>` 探針注入，09 §3.4）

| ID | 標題 | 前置／輸入 | 預期 | 層 | # |
|----|------|-----------|------|----|---|
| DOC-001 | check registry 完整性 | 列舉 37 個 check | 每個都有 i18n（zh-TW/en）、≥ 1 個 fix、ok 與 fail 兩個 fixture 測試（meta 測試掃描 registry） | C | #26 |
| DOC-002 | 5070 Ti | `hw:rtx5070ti-win11` | gpu.* ok；`recommended_packs=[llamacpp-cuda13]`；檔位 `nvidia_16g` | U | #26 |
| DOC-003 | Mac M3 16 GB | `hw:m3-16` | gpu.metal ok；統一記憶體預算；無 pack | U | #26 |
| DOC-004 | 無 GPU | `hw:no-gpu-linux` | `GPU_NOT_FOUND` warning（不阻擋）；CPU 組合 | U | #26 |
| DOC-005 | 驅動過舊 | `hw:nvidia-drv535` | `gpu.driver` blocker 只影響 `*.cuda` feature；`caption.vlm.llamacpp` 仍可（Vulkan／CPU） | U | #28 |
| DOC-006 | AMD／Intel | `hw:amd-rx7800-win`、`hw:intel-arc-linux` | 走 Vulkan；不崩潰 | U | #26 |
| DOC-007 | 不需要 torch | 在未安裝 torch 的環境執行 doctor；測試同時斷言 `"torch" not in sys.modules` | 全部 check 完成 | U | #26、DoD 5.2 |
| DOC-008 | check 自身崩潰 | 某 check 拋例外 | 該 check `CHECK_CRASHED`（warning）；其他照常 | U | #26 |
| DOC-009 | check 逾時 | 某 check sleep 5 s | 2 s 後 `unknown` + retry fix | U | — |
| DOC-010 | port 被佔 | (a) 被另一個 OPA 佔（`/healthz` 簽章相符）；(b) 被其他程式佔 | (a)「切換到正在執行的實例」；(b) `auto_fix(switch_port)` | I | #8 |
| DOC-011 | disk.models 門檻 | free < 下一個下載需求；free < 20 GB | blocker；warning | U | #17 |
| DOC-012 | macOS TCC | `fs.roots` 讀取回 EPERM | `auto_fix(open_tcc_settings)` | U | #37 |
| DOC-013 | Windows 長路徑 | `LongPathsEnabled=0` | info + `copy_command(reg add …)` | U | #38 |
| DOC-014 | 時鐘偏差 | 偏差 90 s／6 分鐘 | warning／blocker；`copy_command` 依 OS | U | — |
| DOC-015 | 電池 | `sys.power` on battery | info 文案 | U | #9 |
| DOC-016 | auto_fix 白名單 | 前端送任意 `fix_id` 以外的 payload；或白名單外動作 | 拒絕 400；需要提權的動作只會以 `copy_command` 出現 | S | #27 |
| DOC-017 | fix 稽核 | 執行任一 auto_fix | `audit_log` 有 actor、fix 類型、結果 | I | — |
| DOC-018 | feature 對應 | `runtime.llamacpp` fail | 只有 `caption.vlm.llamacpp` 不可用；`embed.onnx` 照常 | U | #26 |
| DOC-019 | 結果快取與事件 | doctor 結果 `expires_at`；狀態改變 | 發 `doctor.changed`，preflight `revalidate_on` 觸發重算 | I | #27 |
| DOC-020 | 五類真機 | nightly／release：NVIDIA、AMD（Vulkan）、Intel、Apple Silicon、純 CPU | 報告產生且不崩潰；附在 release notes | N/M | #26、DoD 5.2 |

### 7.4 DIAG：診斷包

| ID | 標題 | 前置／輸入 | 預期 | 層 | # |
|----|------|-----------|------|----|---|
| DIAG-001 | 診斷包不含敏感資料 | 產生 zip（log 中植入 `/Users/alice/…`、`C:\Users\bob\…`、`/home/carol/…`、主機名、`opagent_pat_…`、IP、Bearer token） | zip 內 regex 掃描 0 命中；使用者名改 `~`、主機名改 hash；含 doctor 報告；不含任何影像 bytes（magic number 掃描） | S | #34、DoD 5.2 |
| DIAG-002 | 性質：redactor | Hypothesis 產生含路徑／token／IPv4／IPv6 的任意 log 行 | 輸出不含原值；非敏感文字保留 | P/S | #34 |
| DIAG-003 | Markdown 報告 | 「複製診斷報告」 | 含版本、部署形態、硬體摘要、所有非 ok check、最近 20 個失敗 Operation 的錯誤碼 | U | #34 |

## 8. PF — Preflight 與修復 CTA（06 §4；每個錯誤碼至少一個測試）

### 8.1 共用斷言（每一列都套用）

`PF-ASSERT`：(1) `POST /api/v1/preflight {action, params}` 回 `ready` 與 `blockers[]`／`warnings[]`，該 code 出現在正確的陣列（B → blockers、W → warnings）；(2) `fixes[]` 的 **type 序列**與本表「fixes 順序」完全相同，第一個 `primary=true`；(3) 每個 `choose_alternative` 都有 `consequence`；(4) 該 action 的實際 endpoint（例如 `POST /jobs`）在同一狀態下回 **409 `application/problem+json`**，`code` 與 `blockers` 相同；(5) `packages/i18n/errors.{zh-TW,en}.json` 有該 code 的標題、說明、主要 CTA；(6) Storybook 有該 code 的 ProblemCard story（VIS-003）。T（transient）者另斷言 `retryable=true`。

> 測試實作：`apps/api/tests/preflight/test_catalog.py` 以本表為參數（表格同時存成 `fixtures/golden/preflight_catalog.yaml`，CI 檢查兩者一致）。注入方式：`hw:` 探針、FakeDisk、FakeWorkerLink、FakeInstallations、FakeClock。

### 8.2 錯誤碼目錄（06 §4.7 #1–#62，加上 08 §10 與本文補充）

| ID | code | action | 注入狀態 | 嚴重度 | fixes 順序（type：目標） | 版 |
|----|------|--------|----------|--------|--------------------------|----|
| PF-001 | `WORKER_OFFLINE` | job.start | 本機引擎心跳 90 s 未更新 | B/T | `auto_fix:restart_runtime`（桌面）／`copy_command:worker run`（Docker）→ `retry`；下游 gates 為 `waiting` | v0.1 |
| PF-002 | `WORKER_VERSION_MISMATCH` | job.start | worker `protocol_version` 主版本不同 | B | `auto_fix:update`（桌面）／`open_docs:upgrade` | v0.1 |
| PF-003 | `GPU_NOT_FOUND` | job.start | `hw:no-gpu-linux` | W | `choose_alternative:cpu_combo` → `open_docs:gpu-setup`；`ready=true` | v0.1 |
| PF-004 | `GPU_DRIVER_TOO_OLD` | job.start（cuda variant） | `hw:nvidia-drv535` | B | `open_docs:driver-download` → `choose_alternative:vulkan` → `choose_alternative:cpu` | v0.1 |
| PF-005 | `GPU_ARCH_UNSUPPORTED` | job.start | `hw:blackwell-cuda12pack` | B | `download:llamacpp-cuda13` → `choose_alternative:llamacpp-vulkan` | v0.1 |
| PF-006 | `GPU_BUSY_EXTERNAL` | model.load | `hw:game-running-5gb`，free 2 GB | W/T | `retry` → `choose_alternative:smaller_variant`；`details.processes` 含 `game.exe` | v0.1 |
| PF-007 | `GPU_DOCKER_NO_PASSTHROUGH` | job.start | 容器內、無 `/dev/nvidia*`、compose 宣告 GPU | B | `copy_command:compose -f compose.cuda.yml` → `open_docs:nvidia-container-toolkit` | v0.1（Docker） |
| PF-008 | `VRAM_INSUFFICIENT` | job.start | 35B-A3B Q4 on 16 GB、無 offload | B | `choose_alternative:q4_k_m(8B)`（附 alt GB 與 consequence） | v0.1 |
| PF-009 | `VRAM_SEQUENTIAL` | job.start | 8B Q4 on `hw:rtx4060-laptop` | W | `open_docs:sequential-mode`；`ready=true`；estimate 含 +pct% | v0.1 |
| PF-010 | `RAM_INSUFFICIENT` | job.start | MoE offload 需 32 GB，RAM 16 GB | B | `choose_alternative:smaller_model` | v0.1 |
| PF-011 | `PERF_CPU_ONLY_SLOW` | job.start（VLM） | `hw:no-gpu-linux`、10,000 張 | W | `open_settings:external-endpoint` → `choose_alternative:continue_anyway`；訊息含 `{eta}` | v0.1 |
| PF-012 | `RUNTIME_PACK_MISSING` | job.start | 需要 `llamacpp-cuda13`，未安裝 | B | `download:llamacpp-cuda13`（附 size 與 est） | v0.1 |
| PF-013 | `RUNTIME_PACK_BROKEN` | job.start | pack self-test 失敗 | B | `auto_fix:install_pack(reinstall)` → `choose_alternative:cpu_build` | v0.1 |
| PF-014 | `RUNTIME_UNSUPPORTED_PLATFORM` | job.start | vLLM variant 於 Windows | B | `choose_alternative:llamacpp` | v0.1（vLLM pack v0.4） |
| PF-015 | `RUNTIME_CRASHED` | job.start（重試時） | runtime 近 5 分鐘崩潰 3 次 | B/T | `retry` → `open_docs:logs` | v0.1 |
| PF-016 | `RUNTIME_TIMEOUT` | job.retry_failed | 上一輪有 timeout 失敗項 | W/T | `retry:failed_items` | v0.1 |
| PF-017 | `ORT_EP_UNAVAILABLE` | job.start（embed） | DirectML EP 建立 session 失敗 | W | `download:ort-cuda`（Linux）／`open_docs` → `choose_alternative:cpu_ep` | v0.1 |
| PF-018 | `WSL_GPU_UNAVAILABLE` | job.start（vLLM） | Windows + WSL2 無 libcuda | B | `copy_command:wsl --install` → `choose_alternative:llamacpp` | v0.4 |
| PF-019 | `OLLAMA_NOT_RUNNING` | job.start（ollama variant） | `localhost:11434` 拒絕連線 | B/T | `copy_command:ollama serve` → `retry` | v0.1 |
| PF-020 | `EXTERNAL_ENDPOINT_UNREACHABLE` | job.start（openai-compatible） | endpoint 逾時 | B/T | `open_settings:endpoint` → `retry` | v0.1 |
| PF-021 | `DECODE_HEIC_MISSING` | scan | `pillow_heif` import 失敗 | W | `auto_fix:reinstall_core_dep` | v0.1 |
| PF-022 | `DECODE_RAW_MISSING` | scan | rawpy 不可用、來源有 120 張 RAW | W | `download:libraw`；訊息 `{n}=120` | v0.1 |
| PF-023 | `DECODE_FAILED` | job.start | 上次掃描有 7 張損毀 | W | `open_docs:skipped-list`（UI：查看清單） | v0.1 |
| PF-024 | `FFMPEG_MISSING` | scan | 來源有影片、無 ffmpeg | W | `download:ffmpeg` → `choose_alternative:keep_skipping` | v0.1 |
| PF-025 | `DISK_INSUFFICIENT_MODELS` | model.download | FakeDisk 不足 | B | `choose_alternative:smaller_variant` → `open_settings:manage-models` → `auto_fix:cleanup_downloads` → `open_settings:models-storage`（06 §2.5 順序） | v0.1 |
| PF-026 | `DISK_INSUFFICIENT_THUMBS` | scan | 縮圖需求 > free | B | `open_settings:thumbs-storage` → `choose_alternative:t256_only` | v0.1 |
| PF-027 | `DISK_LOW_THUMBS` | scan | 處理後低於 10% | W | `open_settings:storage` | v0.1 |
| PF-028 | `FS_PERMISSION_DENIED` | scan | macOS TCC EPERM／Linux EACCES | B | macOS：`auto_fix:open_tcc_settings`；其他：`open_settings:allowed-roots` | v0.1 |
| PF-029 | `FS_ROOT_NOT_ALLOWED` | scan | source 指向未註冊 alias | B | `open_settings:add-folder`（只經殼 IPC 新增） | v0.1 |
| PF-030 | `FS_ROOT_UNAVAILABLE` | scan／output.apply | root 不存在（外接硬碟拔除） | B/T | `retry` → `open_docs:mount` | v0.1 |
| PF-031 | `FS_NOT_WRITABLE` | output.apply | 輸出目錄唯讀 | B | `open_settings:choose-output` | v0.1 |
| PF-032 | `NET_UNREACHABLE` | model.download | 錄製伺服器關閉 | B/T | `retry` → `open_settings:mirrors` → `open_settings:proxy` | v0.1 |
| PF-033 | `NET_TLS_INTERCEPTED` | model.download | 伺服器出示自簽 CA（模擬企業 MITM） | B | `open_settings:custom-ca` | v0.1 |
| PF-034 | `HF_AUTH_REQUIRED` | model.download | `hf:401` | B | `open_settings:hf-token` | v0.1 |
| PF-035 | `HF_GATED_ACCESS_PENDING` | model.download | `hf:403-gated` | B | `open_docs:model-page` → `retry` | v0.1 |
| PF-036 | `LICENSE_CONSENT_REQUIRED` | model.download | 非商用權重、未同意 | B | `open_settings:license-dialog` | v0.1 |
| PF-037 | `MODEL_NOT_INSTALLED` | job.start | VLM 未下載，另有已安裝 Q4 | B | `download:variant`（label 含 GB 與 ETA）→ `choose_alternative:installed_q4`（附 eta_delta、quality） | v0.1 |
| PF-038 | `MODEL_DOWNLOADING` | job.start | 該 variant 下載 72% | B/T | `open_docs:view-download`（UI 跳到下載）；`revalidate_on` 含 `model.installed:<v>` | v0.1 |
| PF-039 | `MODEL_FILES_MISSING` | model.load | installation 存在、檔案被刪 | B | `download:redownload` → `open_settings:relocate` | v0.1 |
| PF-040 | `MODEL_CORRUPT` | model.load | 檔案 sha 與 manifest 不符 | B | `download:corrupt_files_only` | v0.1 |
| PF-041 | `MODEL_LOAD_FAILED` | model.load | FakeRuntime 載入回錯誤 | B | `retry` → `choose_alternative:other_variant` | v0.1 |
| PF-042 | `MODEL_OOM` | job.retry_failed | 上一輪 OOM | W/T | `choose_alternative:smaller_variant` → `choose_alternative:lower_resolution` | v0.1 |
| PF-043 | `MODEL_CAPABILITY_MISMATCH` | job.start | 指定 embed-only 模型做 caption | B | `choose_alternative:capable_model` | v0.1 |
| PF-044 | `MODEL_IN_USE` | model.delete／model.move | 有 Job 使用中 | B | `retry:after_jobs`（UI：任務完成後再執行） | v0.1 |
| PF-045 | `DL_CHECKSUM_MISMATCH` | model.download（重試） | 上次 sha 失敗兩次 | B | `retry:other_source` → `open_docs:report-issue` | v0.1 |
| PF-046 | `DL_RATE_LIMITED` | model.download | `hf:429` | W/T | `open_settings:hf-token`；自動 `retry(after_s)` | v0.1 |
| PF-047 | `DL_NOT_FOUND` | model.download | `hf:404` | B | `open_docs:update-catalog` | v0.1 |
| PF-048 | `DL_UNVERIFIED_SOURCE` | model.download | 只有鏡像 metadata | W | `choose_alternative:continue_unverified`（`destructive=false`，需明確確認）→ `retry`（取消） | v0.1 |
| PF-049 | `NET_PORT_IN_USE` | app.start | 偏好 port 被非 OPA 程式佔用 | B | `auto_fix:switch_port` | v0.1 |
| PF-050 | `TIME_SKEW` | model.download | 偏差 6 分鐘 | B（> 5 分）／W（> 60 s） | `copy_command:time-sync`（依 OS 分頁） | v0.1 |
| PF-051 | `JOB_NOTHING_TO_DO` | job.start | 快取命中 100% | W | `choose_alternative:reapply_thresholds` → `choose_alternative:force_rerun` | v0.1 |
| PF-052 | `JOB_CONFLICT_RUNNING` | job.start | 同來源已有 running Job | B | `open_docs:view-job` → `choose_alternative:queue_after` | v0.1 |
| PF-053 | `SEARCH_INDEX_INCOMPLETE` | search | 向量覆蓋率 87% | W | `auto_fix:reindex`（附 eta） | v0.1 |
| PF-054 | `SEARCH_MODEL_MISMATCH` | search | 索引 space ≠ 目前預設 embed 模型 | W | `auto_fix:reindex` | v0.1 |
| PF-055 | `PLAN_STALE` | output.apply | plan 後 3 個 src 被修改 | B | `retry:reprobe`（重新探測衝突） | v0.1 |
| PF-056 | `OUTPUT_TARGET_INSIDE_SOURCE` | job.start／output.apply | 輸出位置在來源 root 之下 | B | `open_settings:choose-output` | v0.1 |
| PF-057 | `UNDO_TARGET_MODIFIED` | output.undo | 2 個 dst 被改 | B | `open_docs:review-conflicts` → `choose_alternative:skip_conflicts` | v0.1 |
| PF-058 | `EGRESS_POLICY_BLOCKS` | job.start（雲端 endpoint） | workspace `results-only` | B | `choose_alternative:local_model` → `open_settings:privacy` | v0.5 |
| PF-059 | `PERMISSION_DENIED_ROLE` | output.apply | 使用者為 ws_reviewer | B | `open_docs:request-approval`（`requires_permission` 呈現） | v0.5 |
| PF-060 | `MCP_DISABLED` | mcp.connect | workspace `mcp.enabled=false` | B | `open_settings:enable-mcp` | v0.2 |
| PF-061 | `MCP_TOKEN_INVALID` | mcp.connect | PAT 已撤銷 | B | `auto_fix:regenerate_token`（UI 精靈） | v0.2 |
| PF-062 | `CHECK_CRASHED` | job.start | 某 doctor check 拋例外 | W | `retry:rerun_check` → `open_docs:copy-diagnostics` | v0.1 |
| PF-063 | `PACK_SIGNATURE_INVALID`（08 §10；**需回寫 06 §4.7**） | runtime.install | index minisig 驗證失敗 | B | `retry:other_mirror` → `open_docs:report-issue` | v0.1 |
| PF-064 | `PACK_INCOMPATIBLE`（08 §10；**需回寫 06 §4.7**） | runtime.install | pack_api 不在 `packs.lock` 允許範圍 | B | `auto_fix:install_pack(locked_version)` | v0.1 |
| PF-065 | `FS_FILE_LOCKED`（本文提議） | output.apply 重試 | dst 被其他程式獨佔 | W/T | `retry` | v0.1 |
| PF-066 | `DL_REMOTE_CHANGED`／`DL_MIRROR_FAILED`／`VRAM_OFFLOAD`（06 內文出現但目錄缺；**需回寫**） | model.download／job.start | 各自情境 | W／B／W | 各自 ≥ 1 fix；本列為三個參數化子案例 | v0.1 |

### 8.3 Preflight 行為

| ID | 標題 | 前置／輸入 | 預期 | 層 | # |
|----|------|-----------|------|----|---|
| PF-070 | blocker 根因優先 | Worker 離線且模型未下載 | blockers 只列 `WORKER_OFFLINE`；model gate 為 `waiting`（「等待上一步」），不是錯誤 | U | #27 |
| PF-071 | fix schema 全域守則 | 掃描 PF-001–066 的全部回應 | 每個 blocker ≥ 1 fix；primary 恰一個且為第一個；`choose_alternative` 皆有 consequence | C | #27、DoD 5.2 |
| PF-072 | 合併下載 fix | 兩個 blocker 都可下載解決 | 額外提供合併 fix「全部準備好（下載 2 項 · 11.0 GB · 約 3 分鐘）」 | U | #27 |
| PF-073 | 伺服器端強制 | 不經 UI 直接 `POST /jobs` | 409 problem+json，blockers 與 preflight 相同 | I | #27 |
| PF-074 | preflight fingerprint | 帶上次 `fingerprint` 送出、狀態未變 | 伺服器略過重算；狀態改變後重算 | U | — |
| PF-075 | 效能 | 快取狀態，連續 1,000 次 | p95 < 50 ms | F | — |
| PF-076 | Worker RPC gate 逾時 | 資料夾可讀性 gate 的 RPC 延遲 5 s | 2 s 後該 gate `unknown` + `retry` | I | — |
| PF-077 | 自動解鎖 | UI：blocker=MODEL_DOWNLOADING；SSE 送 `model.installed` | 300 ms debounce 後重算；列轉綠；按鈕解鎖；`aria-live` 播報「已就緒，可以開始」；「就緒後自動開始」勾選時自動送出 | E | #27 |
| PF-078 | i18n 完整 | errors.zh-TW.json、errors.en.json 對照 06 §4.7 目錄 | 每個 code 兩種語言都有 title/message/cta；參數佔位符集合相同 | C | #27、#11 |
| PF-079 | `opa/no-bare-disabled` | ESLint fixture：`<Button disabled={x}>`、`<Button disabledReason={{code}}>` | 前者報錯、後者通過；CI 在 `apps/web` 全域開啟 | C | #27、DoD 5.2 |
| PF-080 | 停用狀態可近用 | Storybook interaction：所有 disabled 狀態 | 每個都有 `aria-describedby` 指向原因；鍵盤 focus 可開 popover | A | #27 |
| PF-081 | InferenceError 映射 | OOM、TIMEOUT、BAD_INPUT、RUNTIME | `MODEL_OOM`、`RUNTIME_TIMEOUT`、`DECODE_FAILED`、`MODEL_LOAD_FAILED`／`RUNTIME_CRASHED`；`job_item.error_code` 同一套 | U | #27 |
| PF-082 | problem+json 全域 | schemathesis 對全部 `/api/v1` 端點產生錯誤輸入 | 所有 4xx／5xx 都是 RFC 9457 + `code` 欄位；無 HTML 錯誤頁 | C | #27 |
| PF-083 | 永遠有退路 | 對 PF-004、005、012、013、017 的狀態各自選 `choose_alternative` | 套用後 preflight `ready=true`，且 FakeBackend 下 Job 可跑完 | I | #28、DoD 5.2 |
| PF-084 | 不需模型的工作不受 GPU 檢查阻擋（回歸） | `hw:nvidia-drv535`：依日期 vs 依內容 | 依日期 0 blocker；依內容仍顯示 GPU_DRIVER_TOO_OLD 與修復 | I | #27、#28 |
| PF-085 | problem+json 細節欄位與保留欄位同名（回歸） | `HTTPException` 的 detail 帶 `status`、`type` 或 `title`（例如 `PLAN_NOT_APPLIED` 帶計畫狀態） | 回應仍是 RFC 9457 的 4xx，`code` 正確，原本的值放在 `params`（UI 的訊息插值來源）；不會變成 500 | C | #27 |

---

## 9. OP、ETA、ACT — Operation、時間、Activity Center（06 §5–§6）

### 9.1 OP：Operation 模型

| ID | 標題 | 前置／輸入 | 預期 | 層 | # |
|----|------|-----------|------|----|---|
| OP-001 | 所有長操作都是 Operation | 觸發 download、scan、thumbnail、job.label、output.plan、output.apply、output.undo、model.move、runtime.install、doctor.run | 每個都建立 Operation（type 正確）並發 `operation.created` | C | #33、DoD 5.2 |
| OP-002 | 階段權重 | `job.label` 有估算 → 依時間權重；無估算 → 06 §5.2 預設權重 | golden fraction；跳過 `model_load` 時權重重新分配、phase=`skipped` | U | #30 |
| OP-003 | 快取命中不讓進度跳 | 31% 快取命中 | 在 `cache_lookup` 階段計入，`infer` 階段開始時不瞬間跳到 31% | U | #30 |
| OP-004 | 子操作 | Job 內含模型下載子操作 | 父進度 = Σ(子權重 × 子進度)，子權重以預估時間計 | U | #30 |
| OP-005 | 持久化節流 | 高頻進度 10 秒 | `operation` 表最多每 2 s 寫一次；狀態變化立即寫；`operation_event` 不存 tick，只每分鐘一個取樣點 | I | #33 |
| OP-006 | 重啟恢復 | 2 個進行中（下載 3.2/10.6 GB、Job 68%）時關閉 app 再開 | Activity Center 恢復卡片列出兩者；下載自動繼續（`auto_resume` 預設開）；Job 顯示「繼續」 | E/X | #33、DoD 5.2 |
| OP-007 | 保留期限 | `clk` 前進 31 天／91 天 | 成功者 30 天後刪；失敗者 90 天後刪；最多 500 筆 | I | #33 |
| OP-008 | SSE 重連補發 | 斷線 30 s，`Last-Event-ID` 重連 | 補發缺漏事件；超過 10 分鐘保留期時前端改抓 `GET /operations?status=active` | I | #33 |
| OP-009 | 舊事件丟棄 | 亂序送達 seq 812、810 | 前端忽略 810 | U | #33 |
| OP-010 | 推送節流 | 每 100 ms 進度變化 0.1% | ≤ 1 Hz；變化 ≥ 1% 立即；頁面 hidden 時降到 0.2 Hz；狀態事件不受影響 | I | — |
| OP-011 | 控制前先 preflight | 磁碟仍不足時按 resume | 409 + `DISK_INSUFFICIENT_*` blocker | I | #27 |
| OP-012 | 取消模式 | graceful vs immediate；immediate 會丟 > 1 分鐘工作量 | graceful 等目前批次；immediate 中止並歸還 lease；丟棄量 > 1 分鐘時需確認 | E | #47 |
| OP-013 | 經過時間扣除暫停 | `clk`：執行 120 s、暫停 60 s、再執行 30 s | `active_ms=150000`、`paused_ms=60000`；完成文案「花了 2 分 30 秒（暫停 1 分鐘未計入）」 | U | #31、DoD 5.2 |
| OP-014 | 前端時鐘校正 | SSE `server_time` 比本機快 4 s | 本地累加使用 offset，顯示不倒退 | U | #31 |
| OP-015 | 完成回寫觀測 | Operation 成功 | 寫 `perf_observation`（吞吐、載入時間、頻寬）與 `eta_calibration(actual/predicted_p50)` | I | #30 |
| OP-016 | 同 lane 重新排序 | `POST /operations/reorder` | 只在同 worker × lane 生效；跨 lane 回 400 | I | — |
| OP-017 | 等待原因目錄 | 參數化 13 個 code：QUEUED_BEHIND、WAITING_GPU_SLOT、WAITING_MODEL_DOWNLOAD、WAITING_MODEL_LOAD、WORKER_OFFLINE、DISK_INSUFFICIENT_*、NET_UNREACHABLE、PAUSED_BY_USER、PAUSED_ERROR_RATE、PAUSED_ON_BATTERY、PAUSED_GPU_CONTENDED、PAUSED_NOT_IDLE、SOURCE_OFFLINE | 每個都有 zh-TW/en 顯示文字與 CTA；`status=waiting` 或自動 paused 時 `waiting_reason` 必填（schema 驗證） | C/E | #32、DoD 5.2 |
| OP-018 | 等待中暫停／取消（ADR-0004，回歸） | `waiting` 的 Operation：(a) Worker 持有 lease；(b) 沒有 lease | (a) 暫停 → `pausing`、取消 → `cancelling`，Worker 確認後才到 `paused`／`cancelled`；(b) 直接到 `paused`（`waiting_reason=PAUSED_BY_USER`）／`cancelled`；兩者狀態都真的改變 | I | #32、#47 |

### 9.2 ETA：估算（FakeClock 驅動，06 §2.3、§5.5）

| ID | 標題 | 前置／輸入 | 預期 | 層 | # |
|----|------|-----------|------|----|---|
| ETA-001 | bytes EWMA（不等間隔） | 樣本 Δt ∈ {0.2, 0.25, 0.4, 1.0} s、τ=5 s | speed 與 golden 相差 ≤ 0.1%；暫停期間不採樣 | U | #17 |
| ETA-002 | fast/slow 混合 | 開始 10 s 內 vs 之後 | 前 10 s 用 fast；之後 `0.3·fast + 0.7·slow` | U | #17 |
| ETA-003 | 估算中 | 開始 < 3 s 或樣本 < 4（下載）；無先驗且 < 2 batch（items） | `state=estimating`，不顯示數字 | U | #30 |
| ETA-004 | 貝氏收縮 | 本機歷史 rate=2.5、觀測 3.5、n=30 | `rate=(30·2.5+30·3.5)/60=3.0`；registry 先驗 n0=10、時間模型 n0=5 | U | #30 |
| ETA-005 | 先驗來源順序 | 分別提供 (1) 同 task_sig 歷史；(2) 不同 task_sig；(3) registry perf_prior × GPU 係數；(4) 都沒有 | 依序採用；`basis` 欄位為 `history`／`history`（scaled）／`registry_prior`／`none`；(2) 依輸出 token 比例換算 | U | #29 |
| ETA-006 | 多階段加總 | 下載剩 2 GB + 載入 + embed 1,000 + infer 1,000 | ETA = Σ 各階段 p50；變異數相加 | U | #29 |
| ETA-007 | 信心區間 | σ_rel=0.2；以及 σ_rel=1.5 | p10/p90 = p50×(1∓0.256)；後者 σ 上限 0.9 | U | #30 |
| ETA-008 | 狀態遲滯 | 條件滿足 precise 9 s 後失效 | 不升級；滿 10 s 才升級；降級立即 | U | #30 |
| ETA-009 | stalled | batch 間隔 4 s，`clk` 前進 31 s 無進度 | `stalled` 並附原因（若已知）與「查看 log」「重試」 | U | #30 |
| ETA-010 | unknown | 掃描 enumerate 階段且無上次總數 | `unknown`：只顯示經過時間與已完成量 | U | #30 |
| ETA-011 | 四捨五入規則 | 45 s、7.4 分、23 分、1 小時 37 分、7 小時 12 分 | 「不到 1 分鐘」「約 7 分鐘」「約 25 分鐘」「約 1 小時 40 分」「約 7 小時 · 明天 02:30 完成」；en 對應；> 15 分加完成時刻 | U | #30 |
| ETA-012 | 防抖動 | rough 數字變動 8%；precise 變動 2 s | 不更新顯示 | U | #30 |
| ETA-013 | 誠實說明變慢 | 實測 rate < 先驗 / 2，遙測 GPU util 30%、CPU 100% | 附一行「比預估慢：HEIC 解碼是瓶頸」；GPU_BUSY_EXTERNAL 時對應文案 | U | #30 |
| ETA-014 | 暫停中 | Operation paused（電池） | ETA 顯示「暫停中」，數字凍結、不倒數 | U | #30 |
| ETA-015 | cascade 混合工作量 | 預估送 VLM 比例 30%，實測 45% | 剩餘工作量隨比例 EWMA 上修 | U | #30 |
| ETA-016 | 區間涵蓋率模擬 | Monte Carlo：200 次模擬 Job，每張耗時 lognormal(σ=0.4) 並含 10% 漂移 | 80% 區間事後涵蓋真值比例 ≥ 70%（v0.1 門檻；目標 80%） | P | #30、DoD 5.2 |
| ETA-017 | 佇列等待 | 前方 2 個 Operation p50 為 300 s、600 s | `queue.ahead_seconds_p50=900` | U | #32 |
| ETA-018 | 開始前預估 | `POST /estimates`：10,234 張、3,150 快取、312 不支援 | `items` 正確；breakdown 含 download/load/embed/infer/postprocess；無頻寬歷史時文案為「100 Mbps 約 15 分鐘 · 1 Gbps 約 1.5 分鐘」 | I | #29、DoD 5.2 |
| ETA-019 | 參考機涵蓋率實測 | release gate：三台參考機跑測試圖庫 | 記錄 `eta_calibration` 涵蓋率（≥ 70%）並寫入 release notes | N/M | #30、DoD 5.2 |

### 9.3 ACT：Activity Center 與進度元件（Vitest + Playwright）

| ID | 標題 | 前置／輸入 | 預期 | 層 | # |
|----|------|-----------|------|----|---|
| ACT-001 | 頂列指示器五種狀態 | 無操作／進行中／有暫停／有失敗未讀／有中斷 | 對應 06 §6.1 的圖示、色彩 token 與 aria-label | U/V | #33 |
| ACT-002 | 分組 | 3 個 Operation 分屬 GPU、下載、Control plane lane | 依「worker × lane」分組顯示 | U | #33 |
| ACT-003 | 失敗列渲染 fix | 失敗 Operation 帶 `HF_AUTH_REQUIRED` | 列中顯示 ProblemCard 的 fix 按鈕（不是只有「重試」） | U | #27 |
| ACT-004 | 恢復卡片 | 重啟後有 interrupted | 卡片列出並提供「全部繼續」「逐一檢視」 | E | #33 |
| ACT-005 | 鍵盤 | `⌘J`／`Ctrl+J` 開關抽屜；`Alt+↑/↓` 排序 | 可用且 focus 管理正確 | A | #90 |
| ACT-006 | OperationProgress 四變體 | inline、compact、card、full × 每個 eta.state | 視覺快照 light/dark；full 變體 stepper 顯示 skipped 為灰色虛線 | V | #30 |
| ACT-007 | 經過時間本地累加 | running，SSE 停止 5 s | 經過時間每秒前進；paused 時停止 | U | #31 |

## 10. ORG、REV — 整理流程與待確認（killer ①；03 §10、04 §3.7、08 §8.1）

| ID | 標題 | 前置／輸入 | 預期 | 層 | # |
|----|------|-----------|------|----|---|
| ORG-001 | 依日期免模型 | 全新 data dir、未下載任何模型、`img:basic` 50 張 | preflight `ready=true`；計畫 → 核准 → 完成；FakeBackend 與真 runtime 均未被呼叫 | I | #40、DoD 5.3 |
| ORG-002 | 依內容 12 類不漏照片 | `fb:scores(random, seed)` 500 張 | 每張恰一個目的地（含「其他」）；計畫總數 = 500 | I | #41 |
| ORG-003 | 預設輸出位置 | Windows／macOS／Linux | `<Pictures known folder>/OpenPhotoAgent 整理結果`（en：`OpenPhotoAgent Sorted`【文案待定】）；Windows 用 `FOLDERID_Pictures`（含 OneDrive 重導），Linux 用 `xdg-user-dir PICTURES` | I | DoD 5.3 |
| ORG-004 | 預設複製 | 新整理任務的預設 rule | action=`copy`；move 選項需在進階中開啟 | U | #48、DoD 5.3 |
| ORG-005 | 預設設定全庫原檔不變 | nightly：測試圖庫 10,000 張（09 §3.6），預設設定跑完整流程 | 每個原檔 BLAKE3 與 mtime 不變（前後快照比對） | N | DoD 5.3 |
| ORG-006 | sidecar golden | 5 張，JSON 與 CSV | 與 `fixtures/golden/sidecar/*` 相同（時間戳記欄位遮罩） | I | #50、DoD 5.3 |
| ORG-007 | 預估分布與總時間 | 10 張預覽完成 | 回傳全庫預估分布（各類張數，來自 embedding 快速路徑）與總時間 p50 及 basis | I | #46 |
| ORG-008 | 試跑一張串流 | 「試跑這張」 | `InferenceService.preview` 串流事件到 UI；interactive lane | I | #46 |
| ORG-009 | caption 寫入搜尋 | Job 完成 | caption 經 index_outbox 寫入 `search_doc.tsv`，全文可查到 | I | #43 |
| ORG-010 | 就地修正回饋 | `PATCH /assets/{id}/labels {category: pets}` | 寫 `review(status=corrected)`；下一次整理 final_label 用 pets；`search_doc` 更新 | I | #56、DoD 5.4 |
| REV-001 | 低信心建立 review | needs_review 結果 | `review(status=pending, reason=low_confidence)`；SSE `review.created`；計數「待確認 N 張」 | I | #49 |
| REV-002 | 一次一張與數字鍵 | 待確認頁 | `1–9` 選候選類別、`0` 其他、`Enter` 接受建議、`S` 略過；處理後自動下一張 | E | #49、DoD 5.3 |
| REV-003 | 逃生門 | 剩 40 張時「略過剩下的，全部放進『其他』」 | 40 張 `final_label=other`（`source=review_skip`）；Job 可進 planning | I/E | #49、DoD 5.3 |
| REV-004 | 不覆核也能輸出 | 有 pending review 時直接產生計畫 | 未確認者進「其他」，計畫可核准 | I | #49 |
| REV-005 | 執行中即可覆核 | Job running 時開啟待確認 | 已產生的 review 可處理；新的即時加入 | E | #49 |

---

## 11. SRCH — v0.1 基礎搜尋（05 §1.3–§1.4；RRF 與 planner 見 09b）

| ID | 標題 | 前置／輸入 | 預期 | 層 | # |
|----|------|-----------|------|----|---|
| SRCH-TOK-001 | 正規化 | `ＣＡＴ`、`Ｃａｔ`、含 U+200B | 輸出 `cat`；零寬字元移除 | U | — |
| SRCH-TOK-002 | 簡繁互通 | 查詢「猫 海边」，caption「海邊的貓」 | OpenCC s2twp 後命中 | I | — |
| SRCH-TOK-003 | 自訂詞 | label alias「柴犬」、地名「新城鄉」加入 jieba | 各自為單一 token | U | — |
| SRCH-TOK-004 | CJK bigram 保底 | 「夕陽海灘」 | 產生 `§夕陽`、`§陽海`、`§海灘`（權重 D）；詞 AND 結果 < N 時降級為 bigram AND | U/I | — |
| SRCH-TOK-005 | 英文詞幹 | 查 `cats`，caption `a cat` | 命中 | I | — |
| SRCH-TOK-006 | canonical tag key | 「貓」「cat」「ねこ」 | 都映射 `animal.cat` 並命中權重 A | U | #44 |
| SRCH-TOK-007 | tokenizer golden | `fixtures/golden/tokenize.json` 100 句 | 輸出相同；`search_profile.fts.tokenizer` 記錄版本 | C | — |
| SRCH-TOK-008 | tsquery 注入 | 查詢含 `& \| ! ( ) : * '` 與 `\` | 一律跳脫；不產生 SQL／tsquery 語法錯誤；Hypothesis 任意字串不拋例外 | P/S | — |
| SRCH-TOK-009 | 回歸：控制字元 | 查詢含 NUL、BEL 等控制字元 | 移除控制字元後照常搜尋，不回 500（MCP-TOOL 產生輸入時發現） | I | #62 |
| SRCH-FAC-001 | 關鍵字 + tag + 日期 | 2,000 張 fixture DB；`q=海邊`、`tags_all=[scene.beach]`、`taken_at 2025` | 結果與 SQL 參考查詢相同；facet 計數正確 | I | DoD 5.4 |
| SRCH-FAC-002 | 日期來源 | 含 `date_source=mtime` 的照片 | facet 計入並可用 `date_source` 篩選 | I | #40 |
| SRCH-FAC-003 | facet 同 pre-filter | 限定某資料夾範圍 | facet 計數只含範圍內；範圍外不可推知 | I/S | — |
| SRCH-FAC-004 | 分頁游標 | 第 2 頁游標被竄改 | 400（簽章驗證失敗）；正常游標在資料新增後不重複不遺漏 | I/S | — |
| SRCH-FAC-005 | 結果集合＝全部符合（ADR-0005，回歸） | 330 張含「beach」（> 每路 200） | `total`＝SQL 參考計數 330；facet 加總＝330；翻頁無重複無遺漏；全選 selection 計數＝330，批次標籤恰好套用 330 張 | I | #64、#91 |
| SRCH-SEM-001 | 以文搜圖 | `fb:vectors(deterministic)`：查詢向量與 `img:semantic` 中「狗 + 海灘」最近 | top-5 含全部 3 張目標；nightly 真 SigLIP2 英文查詢 `a dog on the beach` 的 recall@10 ≥ 0.8（**待 M0 確認**） | I/N | #64 |
| SRCH-SEM-002 | Worker 離線降級 | 停止 worker | 文字搜尋照常；回應 `notice=degraded_search`；UI 顯示 | I | #64 |
| SRCH-SEM-003 | 索引進度 | 30,000 張中 12,300 有向量 | UI「已可搜尋 12,300 / 30,000 張」；`SEARCH_INDEX_INCOMPLETE` warning | I/E | DoD 5.4 |
| SRCH-SEM-004 | 過濾 + HNSW | 高選擇性 filter（剩 50 張）+ 向量查詢 k=24 | 回傳 min(24, 50) 筆（`iterative_scan` 或 pre-filter 策略），不因 HNSW 過濾而少回 | I | #64 |
| SRCH-SEM-005 | 語意 + facet | 向量查詢 + 日期 facet | 結果全部符合 facet | I | DoD 5.4 |
| SRCH-SEM-006 | review 修正後重索引 | 修正 tag 後 | 1 s 內（outbox）搜尋反映新 tag | I | #56 |

---

## 12. UI、VIEW、E2E — Viewer 與使用者旅程（04 §1、§8；10 §2–§5）

> E2E 在 **web build**（Playwright，Chromium + WebKit）與 **desktop build**（同一套 spec，以 mock `backend_info()` 跑在 Chromium；桌面真殼見 DESK）兩種 build 執行（08 §10 對 09 的需求 (6)）。後端為 all-in-one + FakeBackend + 錄製 HF 伺服器；圖庫為 `img:e2e`（300 張）。

### 12.1 旅程 A：Onboarding（04 §8.2、08 §8.1）

| ID | 標題 | 步驟 | 預期 | 層 | # |
|----|------|------|------|----|---|
| E2E-A-001 | 快樂路徑 | 全新 data dir → 歡迎「開始」→ 硬體一句話結論（`hw:rtx5070ti-win11`）→ 一個推薦組合（GB 與分鐘）→「開始」→ 下載進行中同時選資料夾 → 掃描拼貼牆出現 | 沒有帳號／Org／Workspace 畫面；SigLIP2 先完成後搜尋列可用；整段（FakeBackend、錄製伺服器限速）≤ 5 分鐘 | E | #13、#8、DoD 5.2 |
| E2E-A-002 | 驅動過舊不卡住 | `hw:nvidia-drv535` | 同時出現「開啟驅動下載頁」與「先用 Vulkan 繼續」；選後者可完成一次整理 | E | #28、DoD 5.2 |
| E2E-A-003 | 無 GPU | `hw:no-gpu-linux` | 推薦「快速模式」；明示預估時間 | E | #28 |
| E2E-A-004 | 離線安裝 | 錄製伺服器關閉 | `NET_UNREACHABLE` 卡片附鏡像／代理 CTA；「依日期」仍可完成 | E | #40 |
| E2E-A-005 | 授權免勾選 | 預設組合 | 只有一行「模型授權：Apache-2.0」，無勾選框 | E | #21、DoD 5.2 |

### 12.2 旅程 B：第一次分類到資料夾（dry-run → 核准 → undo）

| ID | 標題 | 步驟 | 預期 | 層 | # |
|----|------|------|------|----|---|
| E2E-B-001 | 依日期端到端 | 選資料夾 →「依日期」→ 預設輸出 → 預覽 → 核准 → 完成 → 復原 | 計畫摘要句正確；完成後輸出資料夾結構正確、原檔不變（測試以後端 fs 快照驗證）；undo 後輸出資料夾回到執行前 | E | #40、#47、DoD 5.3 |
| E2E-B-002 | 依內容 + 只改門檻 | 「依內容」→ 10 張預覽 → 拖動門檻滑桿 | 預覽 < 200 ms 更新；後端 FakeBackend 呼叫計數不增加；顯示全庫分布與總時間 | E/F | #46、DoD 5.3 |
| E2E-B-003 | 自訂類別 | 新增類別「貓咪」+ 描述 + 拖 3 張照片到類別卡 | 顯示全庫預估命中數；預覽中該類出現 | E | #42、DoD 5.3 |
| E2E-B-004 | 待確認與逃生門 | 有 12 張待確認 → 鍵盤處理 3 張 →「略過剩下的…」 | 9 張進「其他」；可繼續到計畫 | E | #49、DoD 5.3 |
| E2E-B-005 | 移動需二次確認 | 進階開啟「移動」→ 核准 | 出現二次確認對話框；未確認無法核准；> 10,000 move 時要求輸入確認字串（後端 fixture） | E | #48、DoD 5.3 |
| E2E-B-006 | 衝突自動處理 | 輸出資料夾預先放 2 個同名不同內容、1 個同內容 | 摘要顯示「2 個重名已加後綴、1 個相同已略過」；細節樹可展開 | E | #48、DoD 5.3 |
| E2E-B-007 | 完成畫面 | 完成 | 有「在檔案總管開啟」（desktop build）與「試試搜尋」；web build 不顯示前者 | E | DoD 5.3 |
| E2E-B-008 | 套用中取消 | apply 中按取消 | 計畫 `partially_applied`；「復原」可用且完成後還原 | E | #47 |
| E2E-B-009 | 外接硬碟離線 | Job 中把 fixture root 改名 | 來源顯示「離線」、Operation 等待原因 `SOURCE_OFFLINE`（不是失敗）；改回後自動繼續 | E/X | #53、DoD 5.3 |
| E2E-B-010 | 全鍵盤完成旅程 B | 不使用滑鼠完成 E2E-B-001 | 每一步可用 Tab／Enter／方向鍵完成 | A | #90 |

### 12.3 搜尋、Lightbox、選取

| ID | 標題 | 步驟 | 預期 | 層 | # |
|----|------|------|------|----|---|
| E2E-S-001 | 關鍵字 + facet | 輸入「海邊」→ 加 tag chip → 選 2025 | 結果數與 API 一致；chips 可移除 | E | DoD 5.4 |
| E2E-S-002 | 以文搜圖 | 輸入英文描述 | 結果出現 `matched_by=image_vector` 標示 | E | #64、DoD 5.4 |
| E2E-S-003 | 建索引中 | 覆蓋率 41% | 橫幅「已可搜尋 X / Y 張」 | E | DoD 5.4 |
| E2E-L-001 | Lightbox 完整 | 點網格照片 → 縮放、平移 → ←/→ → filmstrip → `I` 資訊面板 | 先低解析再換高解析；前後預載（網路面板觀察）；資訊面板含 EXIF、AI 結果、模型名稱與 cascade 層 | E | #87、#88、#89、DoD 5.4 |
| E2E-L-002 | 就地修正 | Lightbox 改類別 | toast「已記錄修正」；下一次整理計畫使用修正值 | E | #56、DoD 5.4 |
| E2E-L-003 | 在檔案總管顯示 | desktop build（mock platform） | 呼叫 `platform.revealInFileManager(alias, rel)`；web build 隱藏按鈕 | E | #92 |
| E2E-SEL-001 | ⌘A 選全部結果 | 搜尋結果 3,214 張（只渲染一部分）→ ⌘A | 批次列顯示「已選 3,214 張」；送出的 selection 為 query handle 而非 ID 清單 | E | #91、DoD 5.4 |
| E2E-SEL-002 | 批次動作 → 計畫 | Shift 範圍選 37 張 → 標籤、類別、「移到資料夾」 | 前兩者寫 review；後者產生 output plan 並導到核准頁 | E | #55、DoD 5.4 |

### 12.4 VIEW：元件與效能（Vitest／Playwright）

| ID | 標題 | 前置／輸入 | 預期 | 層 | # |
|----|------|-----------|------|----|---|
| VIEW-001 | justified 排版 golden | 長寬比陣列 + 容器寬 1280／375 | 列斷點與 golden 相同；每列高度在 [0.8, 1.25]×目標內 | U | #85 |
| VIEW-002 | 排版在 Web Worker | 捲動 10 萬張 | 主執行緒無 > 50 ms long task（PerformanceObserver） | F | #85 |
| VIEW-003 | 無空白格 | 快速捲動並截圖 50 幀 | 沒有任何 tile 只有背景色（ThumbHash 先行；像素統計） | V/F | #87、DoD 5.4 |
| VIEW-004 | DOM 上限 | 4K 視窗、緊密模式 | tile 元素 ≤ 400 | U | #85 |
| VIEW-005 | scrubber | 拖到 2019-03 | < 100 ms 定位；`role="slider"`、`aria-valuetext="2019 年 3 月"` | E/A | #86、DoD 5.4 |
| VIEW-006 | 時間軸鍵盤 | PgUp／PgDn／Home／End／`G` | 行為符合 10 §2.3 | E | DoD 5.4 |
| VIEW-007 | 轉場降級 | 刪除 `document.startViewTransition`（模擬 WebKitGTK） | 改用 FLIP；功能不受影響 | E | #88 |
| VIEW-008 | 漸進載入順序 | Lightbox 開啟 | 請求順序 ThumbHash → t512 → p1024 →（縮放時）原圖／串流 | E | #87 |
| VIEW-009 | 時間軸 API | 10 萬張 DB | `timeline/buckets` < 50 ms；單 bucket（≤ 5,000）< 80 ms | F | #85 |
| VIEW-010 | 記憶體預算 | 10 萬張捲動 3 分鐘 + 開 Lightbox 20 次 | 已解碼影像估算 ≤ 512 MB（CDP heap + ImageBitmap 計數） | F | #85 |
| VIEW-011 | 資訊面板資料 | 某照片有 EXIF、AI 結果 | 欄位齊全；AI 欄位附模型與 `source`；無 GPS 時不顯示地點（不編造） | U | #89 |

### 12.5 PERF、A11Y、VIS、I18N

| ID | 標題 | 前置／輸入 | 預期 | 層 | # |
|----|------|-----------|------|----|---|
| PERF-001 | 10 萬張 60 fps | 合成圖庫 100,000 張（09 §3.6），Chromium，參考規格 runner | 捲動 30 s：p95 frame ≤ 16.7 ms、掉幀 < 1%；nightly 回歸 > 10% 失敗 | F | #85、DoD 5.4 |
| PERF-002 | 首屏 | 熱快取 | < 1 s 首屏（DoD）；可互動 < 300 ms（10 §5.1 目標，記錄） | F | DoD 5.4 |
| PERF-003 | bundle 大小 | `size-limit`；production web build，每個路由以全新 context 首次載入 | 照片路由首次 **JS** ≤ 180 KB gzip；**每路由**首次 JS ≤ 250 KB gzip；CSS（含字型 `@font-face`）只記錄（ADR-0006） | F | — |
| PERF-004 | 掃描吞吐基準 | 10,000 張 SSD | 記錄 files/s；回歸 > 15% 失敗（nightly） | F | — |
| PERF-005 | 推論吞吐基準 | 三台參考機、測試圖庫子集 1,000 張 | 記錄張/秒、VRAM、載入時間；回歸 > 10% 警示、> 20% 失敗；寫入 release notes | N | DoD 5.5 |
| PERF-006 | 搜尋延遲 | 10 萬張，文字 + facet | p95 < 300 ms | F | DoD 5.4 |
| PERF-007 | 暖啟動 | 桌面（Win／Linux tauri-driver） | < 5 s 可操作 | D/F | #3、DoD 5.1 |
| PERF-008 | 釋放後的 app 不會留在記憶體（回歸） | 建立數個 `create_app`、放掉參考、`gc.collect()` | 不留下任何 FastAPI app 或 engine（端點不得是捕捉 app 的 closure，FastAPI 會把端點 callable 放在模組層級的快取裡）；這個洩漏曾讓長時間的測試行程 GC 變慢（洩漏已修正；VIEW-009 剩下的不穩定來自與整合測試共用行程，見 PERF-009） | I | DoD 5.4 |
| PERF-009 | Python 效能測試不與整合測試共用行程（回歸） | `just test-int` 與 ci.yml 整合 job 的 `-m` 選擇式；`just perf-api` 與 CI 效能步驟 | 整合選擇式收集不到任何 `perf` 測試；獨立的效能呼叫一定會收集到 VIEW-009 與 PF-075（ADR-0018） | C | DoD 5.4 |
| A11Y-001 | axe 全路由 | onboarding、照片、Lightbox、整理精靈、計畫核准、待確認、模型、環境醫生、Activity Center、設定 × light/dark × zh-TW/en | axe 0 serious／critical | A | #11、DoD 5.4 |
| A11Y-002 | 網格語意 | 照片網格 | `role=grid/row/gridcell`；roving tabindex；`aria-rowcount`／`aria-rowindex` | A | #90 |
| A11Y-003 | Lightbox | 開啟／切換 | `role=dialog aria-modal`；切換時 `aria-live` 播報「第 12 張，共 312 張」 | A | #90 |
| A11Y-004 | reduced motion | `prefers-reduced-motion: reduce` | 轉場改 100 ms 淡入；脈動改透明度 | A | — |
| A11Y-005 | forced colors | `forced-colors: active` | 選取框使用 `Highlight` | A | — |
| A11Y-006 | 色彩不是唯一資訊 | CompatBadge、狀態色 | 一律有圖示 + 文字（DOM 斷言） | A | #14 |
| VIS-001 | 元件視覺回歸 | Storybook：OperationProgress、ReadinessChecklist、CompatBadge、GatedButton、ProblemCard | light/dark 快照，差異 > 0.1% 失敗（只在 Linux Chromium、字型釘選） | V | — |
| VIS-002 | 頁面視覺回歸 | 10 個主要路由 × light/dark × zh-TW/en | Playwright `toHaveScreenshot`；動態區域（時間、ETA）遮罩 | V | #11 |
| VIS-003 | 每個錯誤碼一個 story | 自動產生 | story 數 = 錯誤碼目錄數；每個都有快照 | V/C | #27 |
| VIS-004 | 視覺基準只在釘選的 Playwright 映像算繪 | 所有 workflow | 跑 `visual` project 的 job 一律在 `mcr.microsoft.com/playwright:v<@playwright/test 版本>-noble`；nightly 與 `visual-baselines.yml` 都有；PR 的 `e2e-smoke` 只跑 `chromium` project（ADR-0019） | C | #85 |
| I18N-001 | 無缺漏 key | 比對 zh-TW 與 en key 集合 | 完全相同；無空字串 | C | #11、DoD 5.4 |
| I18N-002 | 無硬編碼字串 | ESLint `i18next/no-literal-string`（JSX 文字） | 0 違規（allowlist 除外） | C | #11 |
| I18N-003 | 數字與複數 | 1 photo／2 photos；「1,284 張」 | ICU MessageFormat 正確；`tnum` | U | #11 |
| I18N-004 | 偽語系 | pseudo-locale 字串加長 40% | 主要路由無文字截斷溢出（截圖 + DOM overflow 檢查） | V | #11 |
| I18N-005 | 單人模式術語 | 單人模式全路由文字掃描 | 不出現「Worker」「Pipeline」「dry-run」「Workspace」「cascade」「fingerprint」（08 §8.3） | E | #8 |
| I18N-006 | `Intl.DurationFormat` 缺席 | 刪除該 API（模擬 WebKitGTK） | 自製格式化器輸出與 golden 相同 | U | #31 |
| I18N-007 | 修復按鈕都有文案（回歸） | 錯誤碼目錄中每個錯誤碼的每個 fix，zh-TW 與 en | 按鈕文字是翻譯後的文案，不是 `fix.*` 原始 key；未有專用 key 時取 06 §4.7「修復」欄（`errors.<code>.cta` 依 ` · ` 切分的第 i 段） | C | #11、DoD 5.4 |

---

## 13. DESK、PG、PACK、UPD、DOCK — 桌面殼、嵌入式資料庫、Runtime pack、更新、Docker（08）

### 13.1 DESK：殼、監管、本機安全

| ID | 標題 | 前置／輸入 | 預期 | 層 | # |
|----|------|-----------|------|----|---|
| DESK-001 | 啟動畫面 | 冷啟動 | < 300 ms 出現啟動畫面（殼記錄時間戳；Win／Linux 自動化，macOS 手動清單） | D/M | DoD 5.1 |
| DESK-002 | kill -9 postgres | 執行 Job 中 | 殼 5 s 內偵測並重啟；UI「重新連線中」橫幅、畫面狀態不清空；Job 從 lease 續跑；30 s 內恢復 | D/X | #3、DoD 5.1 |
| DESK-003 | kill -9 api | 同上 | 退避 1→2→4 s 重啟；5 分鐘內第 6 次失敗顯示錯誤頁（附 log 與複製診斷） | D/X | #3 |
| DESK-004 | kill -9 worker | 同上 | 重啟；lease 回收續跑；UI「本機引擎重新啟動中」 | D/X | #3、DoD 5.1 |
| DESK-005 | kill -9 llama-server | 同上 | 推論層重啟並減半並行；Job 繼續 | D/X | #3、DoD 5.1 |
| DESK-006 | 殼崩潰不留孤兒 | `kill -9` 殼（Windows 工作管理員結束） | 5 s 內 postgres、api、worker、llama-server 全部結束（Job Object／PDEATHSIG／stdin EOF）；無 GPU 佔用 | D/X | #3 |
| DESK-007 | 單一實例 | 第二次啟動（含 deep link 參數） | 只把既有視窗帶到前景並轉交參數 | D | #7、DoD 5.1 |
| DESK-008 | 資料目錄鎖 | AppImage 與 deb 兩個安裝同時開啟同一 data dir | 第二個顯示「已有實例使用此資料」並結束 | D | #7 |
| DESK-009 | 殘留 postmaster.pid | pid 檔指向不存在的 PID；另一案例指向存活的非 PG 程序 | 前者清除後啟動；後者也清除（PID 不是 postgres）；PID 是存活 postgres 時不清除並提示 | U/I | #4 |
| DESK-010 | port 衝突 | 47800 被 (a) 其他程式 (b) 另一個 OPA 佔用 | (a) 改用隨機 port，`run/api.json`（0600）更新；(b) 聚焦該實例 | I | #8 |
| DESK-011 | launch token | 無 token、錯誤 token、正確 token 呼叫 `/api/v1/assets` | 401、401、200 | I/S | #8、DoD 5.1 |
| DESK-012 | token 不外洩 | 檢查 api 子程序的環境變數與命令列（`/proc/<pid>/environ`、`cmdline`；Windows WMI） | 都不含 token（只經 stdin 傳遞）；log 不含 token | S | #8 |
| DESK-013 | Host／Origin | `Host: evil.example`、`Origin: http://evil.example`；DNS rebinding 模擬（Host=攻擊者網域解析到 127.0.0.1） | 403 | S | #8、DoD 5.1 |
| DESK-014 | 只綁 loopback | 列出 listening sockets | 只有 `127.0.0.1`（PG 在 mac/linux 只有 unix socket） | S | #8、DoD 5.1 |
| DESK-015 | 區網存取強制登入 | 開啟「允許區網存取」 | 綁 `0.0.0.0` 並要求帳號登入；未登入請求 401 | I/S | #8 |
| DESK-016 | handoff code | 系統匣「在瀏覽器開啟」 | 一次性、30 s；重用或過期 → 401；成功設 HttpOnly cookie | I/S | #8 |
| DESK-017 | REST 無法新增 root | `POST /sources` 帶任意絕對路徑；嘗試所有 REST 方法 | 404／403；只有殼 IPC `add_root`（需使用者手勢）可新增 | S | #37、DoD 5.3 |
| DESK-018 | 拖放加入來源 | 拖資料夾入視窗 | 「新增為來源？」→ 確認後 root 註冊；拖入 root 外的單張照片 → 提示先加入來源 | D | #37、DoD 5.3 |
| DESK-019 | asset protocol scope | 從 WebView 讀取 `asset://` 快取縮圖、已註冊 root 下照片、`/etc/passwd`、`C:\Windows\win.ini`、另一個未註冊資料夾 | 前兩者成功；其餘失敗 | D/S | #93、DoD 5.4 |
| DESK-020 | 系統匣控制 | 系統匣「暫停全部／繼續全部」 | 所有重型 Operation 暫停（`PAUSED_BY_USER`）與恢復 | D | DoD 5.1 |
| DESK-021 | 關窗行為 | 任務執行中關窗 | 首次詢問「縮到系統匣／結束」並記住 | D | — |
| DESK-022 | OS 通知與 deep link | Job 完成 | 發 OS 通知；點擊經 `openphotoagent://` 開到結果頁 | D/M | #7、DoD 5.1 |
| DESK-023 | 電池 | FakePower IPC：拔電源／接回 | 10 s 內暫停顯示 `PAUSED_ON_BATTERY`；接回自動繼續 | D | #9、DoD 5.2 |
| DESK-024 | 防止睡眠 | 接電源且有任務 | 取得 OS 防睡眠 assertion；任務結束或用電池時釋放 | D | — |
| DESK-025 | 各 OS 路徑 | 三平台 | data／config／models／packs／cache／logs／backups 位置符合 08 §1.5；`OPAGENT_HOME` 可攜模式覆寫全部 | D | — |
| DESK-026 | log 格式 | 各程序 | JSON lines；10 MB × 5 輪替；不含絕對路徑與 token | I/S | #34 |
| DESK-027 | Python 隔離 | 設使用者 `PYTHONPATH`、`PYTHONHOME`、site-packages 放惡意 `json.py` | `python -I` 不載入；app 正常 | S | — |
| DESK-028 | 版本一致 | 手動替換 worker 版本 | 啟動時偵測不一致並提示 | U | — |
| DESK-029 | 解除安裝保留資料 | NSIS 解除安裝（預設選項）；macOS 拖垃圾桶；deb remove | 照片、輸出資料夾、data dir 皆保留；有 `.opagent-trash/` 檔案時先提示（`GET /trash/summary`） | M/D | #10 |
| DESK-030 | 安裝包大小 | CI 產物 | 三平台壓縮後各 ≤ 200 MB（硬上限）；超過則 build 失敗 | C | #1、DoD 5.1 |
| DESK-031 | 一般使用者安裝 | 乾淨 VM：Win 11（標準帳號）、macOS 14、Ubuntu 22.04 | 安裝並啟動成功，不需 UAC／sudo | M/D | #1、DoD 5.1 |
| DESK-032 | capabilities 驅動 UI | `GET /capabilities` 回 `single_user=true` | UI 無 Org／Workspace 選單、無 Workers 頁 | E | #8、DoD 5.1 |
| DESK-033 | Photos Library 選擇提示 | macOS 選 `Photos Library.photoslibrary` | 顯示封裝格式說明 | D/M | #54 |
| DESK-034 | 桌面煙霧總表 | Win、Linux：tauri-driver + WebdriverIO；macOS：手動清單 | 啟動 → onboarding（FakeBackend 模式）→ 依日期整理 → undo → 搜尋 → 結束，全程無錯誤 | D/M | DoD 5.5 |
| DESK-035 | `run/api.json` 出現時 port 已在 listen（回歸） | 啟動 api，偵測到 `run/api.json` 後立即連線，重複 5 次 | 每次都連得上；port 由同一個已 bind 的 socket 提供（沒有「檢查空閒 → 稍後 bind」的競態） | I | #8 |
| DESK-036 | 桌面傳輸與媒體解析（10 §5.4） | 殼注入 `__OPA_PLATFORM__`（backend `{baseUrl, token}`、`thumbsDir`、`assetUrl`） | REST 改打 `baseUrl` 並帶 `Bearer`；事件流以 fetch 讀 SSE 並帶 token；縮圖 URL＝`assetUrl(<thumbsDir>/<ch[:2]>/<ch>.<variant>.webp)`；影像 payload（時間軸、搜尋、待確認、預覽）都帶 `ch`；Web build 行為不變 | U/I | #8、#93 |

### 13.2 PG：嵌入式 PostgreSQL 17 + pgvector（08 §2.3）

| ID | 標題 | 前置／輸入 | 預期 | 層 | # |
|----|------|-----------|------|----|---|
| PG-001 | initdb | 全新 data dir | `--locale-provider=builtin --locale=C.UTF-8 -E UTF8`；第二次啟動不重跑 | I | #4 |
| PG-002 | pgvector 煙霧 | 三平台 | `CREATE EXTENSION vector`、建 HNSW、kNN 查詢結果正確 | I | #4 |
| PG-003 | desktop profile | RAM 8 GB 與 32 GB 的 FakeRAM | `shared_buffers` 128MB／256MB；`fsync=on`、`synchronous_commit=on` | U/I | #4 |
| PG-004 | migration 前備份 | 有待執行 migration | 先產生 `pg_dump -Fc` 備份再 `alembic upgrade` | I | #4、DoD 5.1 |
| PG-005 | 每日備份保留 | `clk` 前進 10 天 | 保留 7 份每日 + 每次 migration 前一份 | I | #4、DoD 5.1 |
| PG-006 | 還原 | 備份 → 新增資料 → 還原 | 資產數與結果數和備份時一致 | I | #4、DoD 5.1 |
| PG-007 | 從上一版升級 | `fixtures/db/v0.0.x.dump`（每次發版後新增一份） | migrate 成功；關鍵查詢結果與預期相同 | I | VERSIONING §4 |
| PG-008 | 拒絕降級 | DB revision 新於 app | 拒絕啟動 api，提示「用備份還原」 | I | #5 |
| PG-009 | 磁碟滿 | PG data 所在 FS 滿 | 錯誤碼與修復提示；**不刪 data dir** | X | #4 |
| PG-010 | 連續啟動穩定性 | Windows（Defender 開啟）連續 50 次啟動／關閉；macOS、Linux 各 50 次 | 0 失敗（M0 S1、V-1） | I/N | #4 |
| PG-011 | Windows 管理員帳號 | 以管理員執行 app | PG 能啟動（restricted token）或殼提示 | M/I | #4 |
| PG-012 | socket 與權限 | mac／linux | `listen_addresses=''`；socket 目錄 0700；Windows 為隨機 port + SCRAM 隨機密碼檔 0600 | S | #8 |
| PG-013 | 測試 fixture 速度 | `embedded_pg` session 級 initdb + 每測試 template clone | clone < 200 ms；三平台 CI 可用（macOS runner 無 Docker） | I | DoD 5.5 |
| PG-014 | Docker parity | Linux：testcontainers `pgvector/pgvector:pg17` | 同一組 migration 與 Q-* 整合測試全過 | I | #2 |
| PG-015 | 用到真 PostgreSQL 的測試一律是 integration（回歸） | 只標 `security`／`property`、或沒有標記，但使用 `embedded_pg` 的測試 | 快速選擇式（`just test`、CI unit-py）收集不到；`-m integration` 收集得到（CI unit-py 沒有 PostgreSQL，曾有 63 個這類測試在那裡出錯） | C | #2 |
| PG-016 | 桌面 initdb 只用每個 OS 都有的 locale（回歸） | PG 17、`desktop=True`；`tools/build_desktop_pg.sh` 的 smoke | `--locale-provider=builtin --builtin-locale=C.UTF-8 --locale=C`；不得有 `--locale=C.UTF-8`（只有 Linux 有，macOS 與 Windows 的 initdb 會拒絕） | U | #4 |

### 13.3 PACK：Runtime pack 驗證（08 §4.3）

| ID | 標題 | 前置／輸入 | 預期 | 層 | # |
|----|------|-----------|------|----|---|
| PACK-001 | 正常安裝 | 合法 index（minisig）+ 檔案 sha + manifest | 下載到 `packs/.staging/` → 驗證 → 原子 rename 到 `packs/<id>/<ver>/` → `active.json` 更新 | I | #22 |
| PACK-002 | 壞 index 簽章 | 竄改 index 一個 byte | `PACK_SIGNATURE_INVALID`；不下載任何 pack 檔 | S | #22 |
| PACK-003 | 壞 sha | 檔案與 index sha 不符 | 拒絕、staging 清除 | S | #22 |
| PACK-004 | tar 路徑穿越 | entry 含 `../evil`、`/abs`、symlink 指向外部、hardlink 指向外部、裝置檔 | 全部拒絕；staging 外沒有任何新檔（掃描 tmp 根） | S | #22 |
| PACK-005 | 大小與 manifest 不符 | 解壓後檔案多出、或總大小 > index 宣告 × 1.1（zip bomb） | 拒絕 | S | #22 |
| PACK-006 | pack_api 不相容 | `pack_api=2`，lock 只允許 1 | `PACK_INCOMPATIBLE` | U | #22 |
| PACK-007 | self-test 失敗 | 安裝後 smoke test 失敗 | `RUNTIME_PACK_BROKEN`；前一版仍為 active | I | #22 |
| PACK-008 | 隨 app 更新 | `packs.lock` 改變 | 背景下載新 pack，閒置時切換；舊版保留一版 | I | #22 |
| PACK-009 | 離線匯入 | 「匯入 pack 檔」選 `.tar.zst` + `.minisig` | 驗證同 001–005 | I | #22 |
| PACK-010 | 小版更新不讓快取失效 | 新 pack 版本、`numerics_epoch` 不變 | 既有 result 仍命中（接 CORE-HASH-009） | I | #51 |
| PACK-011 | smoke test 失敗自動退回 | onboarding：CUDA pack smoke test 失敗 | 自動改試 Vulkan → CPU，並顯示原因 | I/E | #28 |

### 13.4 UPD、DOCK：更新與 Docker

| ID | 標題 | 前置／輸入 | 預期 | 層 | # |
|----|------|-----------|------|----|---|
| UPD-001 | 更新保留資料 | VM：安裝 `v0.1.0-rc.N` → 建立資料 → 更新到 rc.N+1 | 資產、結果、設定、模型、pack 都保留；migration 前有備份 | D/M | #5、DoD 5.1 |
| UPD-002 | 竄改的更新包 | updater endpoint 回竄改過的 `.nsis.zip`／`.app.tar.gz`／AppImage | 拒絕安裝並顯示原因 | S/D | #5、DoD 5.1 |
| UPD-003 | 任務執行中不自動更新 | 有 running Job | 只提示，不重啟 | D | #5 |
| UPD-004 | 通道切換降級保護 | beta（DB 較新）切回 stable | 拒絕降級，提供「用備份還原」 | I | #5 |
| UPD-005 | 關閉更新 | `OPAGENT_DISABLE_UPDATER=1` | 不發出任何更新請求 | U | #5 |
| UPD-006 | manifest 產生 | release workflow 產生 `latest.json`／`beta.json` | 通過 Tauri updater schema；簽章欄位存在 | C | #5 |
| DOCK-001 | Docker all-in-one | `docker compose -f deploy/compose/allinone.yml up` | 健康檢查通過；Playwright 完成 onboarding 與一次「依日期」整理 | E | #2、DoD 5.1 |
| DOCK-002 | 同一份 web build | 比對 Docker 映像內與桌面 bundle 內的 web 資產 hash | 相同（`vite build --mode` 僅影響 runtime 設定檔） | C | #2 |
| DOCK-003 | 來源唯讀掛載 | compose 以 `:ro` 掛來源 | 嘗試寫入失敗於 OS 層（雙重保護） | I/S | — |
| DOCK-004 | 映像內含執行時讀取的資料（回歸） | 依 Dockerfile app stage 的 COPY 重建 `/app` | 只靠這個目錄樹就能載入 registry（含 SigLIP2）、內建 presets、hw profiles、FakeBackend 向量；第一次 docker.yml 因缺 `registry/` 而 job.start 回 500 | C | #2 |

