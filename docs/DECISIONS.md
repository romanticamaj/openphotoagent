# DECISIONS — 架構決策紀錄（ADR）

> 依 CLAUDE.md §5.8：規格衝突、做不到的門檻、設計取捨由獨立的審議 sub-agent 論證後決定；每一筆以 ADR 格式記錄（背景、選項、決定、理由、影響的測試 ID），並同步修改相關規格。結論不得用來修改測試去配合實作，也不得弱化 §4 不變量。
> 需要使用者本人決定的事項在 [`BLOCKERS.md`](BLOCKERS.md)。

## 審議索引

實作期間（M0 → v1.0）共開了 21 次審議、25 個 sub-agent，全部都有對應的 ADR。規劃階段撰寫規格 00–11 用的 sub-agent，以及提高 mutation score 的實作 sub-agent，不屬於審議，因此不列入。

| ADR | 審議題目 | sub-agent 數 | 里程碑 |
|---|---|---|---|
| ADR-0001 | 門檻（accept／review）與 cascade 的分工 | 1 | 1B（切片 6） |
| ADR-0002 | registry 的雜湊釘選 | 1 | 1C（切片 7） |
| ADR-0003 | Storybook 或自製元件展示頁 | 1 | 1C（切片 10） |
| ADR-0004 | 等待中的 Operation 能否暫停與取消 | 1 | 1C（切片 10） |
| ADR-0005 | 搜尋結果集合的上限 | 1 | 1C（切片 10） |
| ADR-0006 | PERF-003 的字型預算 | 1 | 1D（切片 12） |
| ADR-0007 | DL-019：下載與 Job 搶資源 | 1 | 1D |
| ADR-0008 | Q-T5「過年」的日期範圍 | 1 | 2A |
| ADR-0009 | v0.3 tool 與 MCP-SCOPE-001 的衝突 | 1 | 2B |
| ADR-0010 | 路徑字串與 UntrustedText | 1 | 2B |
| ADR-0011、ADR-0012 | bio 角色隔離與金鑰範圍（同一次審議，兩個決定） | 1 | 2C |
| ADR-0013 | ⌘J 快捷鍵衝突 | 1 | 3 |
| ADR-0014 | caption 文字向量模型選型 | 1 | 4 |
| ADR-0015 | C-21：內建 AS 或外部 IdP（使用者定案） | 2 | 5 |
| ADR-0016 | workspace 隔離：row filter 或每個 workspace 一個資料庫 | 2 | 5 |
| ADR-0017 | Worker 協定版本格式 | 1 | 6 |
| ADR-0018 | VIEW-009 在整合測試中偶發失敗：量測方式 | 1 | 6（最終 gate） |
| ADR-0019 | CI 的視覺基準與本機不一致 | 1 | 6（第一次 CI） |
| ADR-0020 | PG-001 的 initdb locale 在 macOS／Windows 無效 | 1 | 6（第一次 CI） |
| ADR-0021 | `tc-status --check` 的逐位元比對永遠無法通過 | 1 | 6（第一次 CI） |
| ADR-0022 | Linux AppImage 超過 200 MB；隨附的 Python runtime 不含直譯器 | 2 | 1D／6（desktop-build CI） |
| ADR-0023 | 安裝後的 app 讀不到自己的資料（registry、整理方式、標籤、詞彙） | 1 | 1D／6（desktop-build CI 之後） |
| ADR-0024 | Windows 上的 0600 | 1 | 地端 |
| ADR-0025 | Windows 安裝包內建哪個 llama-server | 1 | 地端 |
| ADR-0026 | SigLIP2 int8 未通過一致性 | 0（實測直接否決，見內文） | 地端 |
| ADR-0027 | 向量路徑的相似度下限（取代 ADR-0005 的「不設分數門檻」） | 1 | 地端 |
| ADR-0028 | ETA 區間：δ 隨剩餘時間變化，讓 precise 可達 | 1 | 地端 |
| ADR-0029 | 匯入與整理分開：加入資料夾前可預覽，加入後自動分析 | 0（使用者決定） | 地端 |
| ADR-0030 | VLM 看全部類別，信心改用 token 機率 | 1 | 地端 |
| ADR-0031 | 影像向量路徑：中文經詞彙表轉成英文，對不到就不查 | 1 | 地端 |
| ADR-0032 | 多個概念的查詢：每個概念都要符合 | 1 | 地端 |


## ADR-0001：任務門檻（accept／review）與分類 cascade 的關係

- 日期：2026-11-01｜狀態：已採納｜審議：1 個獨立 sub-agent（題目、03 §3.3／§3.3.3、02 §3.2／§4.1、09a CORE-THR-001～017 與 Q-015）

**背景**：02 §3.2 的 `task_config.thresholds = {accept, review}` 與 03 §3.3 的 cascade 參數（`τ_high`、margin `m`、`other_policy.min_confidence`）都出現 0.80 預設，規格沒寫清楚誰管哪一條路徑。Q-015（「只調門檻」重套，不派工）在實作時暴露了這個缺口：如果門檻不影響 embedding 快速路徑，使用者拉「自動採用」滑桿就會對大部分照片沒有作用。

**選項**：
- A：`accept`／`review` 只管 consensus 與 conflict；embedding 快速路徑只看 `τ_high`／`m`；VLM-only 看 `min_confidence`。
- B：`τ_high` 直接等於 `accept`，所有路徑一律以 `accept`／`review` 判定。
- A+（審議建議）：`τ_high`／`m` 只是**路由**參數（決定送不送 VLM，屬於 config_hash）；`accept` 是所有「有分數的最終判定」的**最終閘門**。

**決定**：採用 A+。各路徑規則（`floor = max(review, min_confidence)`）：

| 路徑 | 規則 | 受哪些參數影響 |
|---|---|---|
| 規則命中 | accepted，p=1.0，source=rule | — |
| embedding only | route=accept **且** p ≥ accept → accepted；否則 p ≥ floor → needs_review（low_confidence），否則 other | 路由：τ_high、m（hash 內）；判定：accept、review、min_confidence（hash 外） |
| VLM only | VLM 選 other → other；p ≥ min_confidence → accepted；否則 other | min_confidence |
| consensus（E、V 同類） | p = 1−(1−pE)(1−pV)；≥ accept → accepted；≥ floor → needs_review；否則 other | accept、review、min_confidence |
| conflict（E、V 不同類） | 採 V 類別，p = 0.8·pV；≥ accept → accepted；否則 **needs_review（reason=conflict）**，不落入 other（交給人看） | accept |

**理由**：B 會讓 CORE-THR-005 失敗（VLM-only 0.71 應 accepted），而且改 accept 會改變路由，違反 Q-015／CORE-THR-017「重套門檻不需推論」。A 讓 accept 滑桿對快速路徑無效，違反使用者預期。A+ 讓 CORE-THR-001～017 與 Q-015 全部成立，並且「只改 accept／review／min_confidence」永遠是純重算。

**每筆結果要存的 stages**（重套門檻 = 純函式）：embedding 分數向量與 margin、n_examples、route、VLM {category, p}、規則命中、nan 旗標；decision／confidence／source 只是衍生快取。

**影響的測試 ID**：新增 CORE-THR-018（embedding 快速路徑受 accept 閘門）、CORE-THR-019（conflict 低於 floor 仍為 needs_review）；Q-015 的測試以 embedding stage 的結果驗證重套效果。不弱化任何既有測試或不變量。

**同步修改的規格**：03 §3.3.3、02 §3.2、02 §4.1 階段 5、09a §1.3。

## ADR-0002：內建模型 registry 的雜湊釘選（catalog／lock 分離）

- 日期：2026-11-01｜狀態：已採納｜審議：1 個獨立 sub-agent（題目、03 §1.1、06 §1.1／§2／§2.6、09a INF-REG-001／002、INF-RT-001～011、CLAUDE.md §4 不變量 10 與「不得捏造資料」）

**背景**：INF-REG-001 要求內建 `variants[].files[]` 都有 `size` 與 `sha256`。這些值只能從 Hugging Face API 取得；開發環境（雲端 session）連不到 HF，而捏造雜湊會直接破壞供應鏈防護。若沒有決策，PR 必跑的 INF-REG-001 在任何開發分支都只能紅燈，或被迫填假值。

**選項**：
- (1) catalog／lock 分離：人寫的 `registry/models/*.yaml` 不放 `size`／`sha256`；CI 產生 `registry/pins.lock`（commit sha、size、sha256、lfs_oid）；載入時合併，每個 variant 標 `pin_state ∈ {pinned, unpinned}`。
- (2) 雜湊直接寫在 YAML 內，由 bot commit 回填：在 bot commit 進來之前 `just test` 永遠紅；bot 改寫人編輯的 YAML，會丟註解、造成 merge 衝突。
- (3) 未釘選 variant 放在 app 不載入的 `registry/candidates/`：INF-RT 推薦測試需要這些 variant，只好另建一份會漂移的 fixture registry。

**決定**：採用 (1)，並加上更嚴格的規則：
- `pins.lock` 中任何條目都必須完整（40-hex revision、size > 0、64-hex sha256）；部分釘選的 variant 是**載入錯誤**，不是 pinned。catalog YAML 出現 `size`／`sha256` 欄位時 schema 拒絕（單一真相來源）。
- 推薦器是 `(catalog, pin_states, hw)` 的純函式，**永遠不推薦 unpinned variant**；下載 preflight 遇到 unpinned 內建 variant → blocker `MODEL_NOT_PINNED`（06 §4.7 #69），附 CTA「更新模型目錄」「改用其他 variant」。
- 移除「內建模型沒有釘選時信任官方 HF API 當下的雜湊」這條退路（06 §2 `resolve`、§2.6 信任錨點）；使用者自訂 YAML 仍必須自帶 sha256（INF-REG-002）。
- 原本 INF-REG-001 中「所有內建 variant 都有 sha256」的保證**不刪除**，而是改由新 ID **INF-REG-013**（層級 G，release gate）強制：每個內建 variant 都是 pinned、每個 runtime pack 都有 sha256。INF-REG-001（層級 C，PR 必跑）改為驗證 schema、lock 條目指向存在的 variant／檔案、所有 pinned variant 的雜湊格式完整。兩者合起來與原保證等價，只是「全部已釘選」移到發版時檢查。
- 新增 INF-RT-012：unpinned variant 被跳過，改推薦下一個 pinned variant。
- 新增 `registry-pin.yml` workflow（push `registry/models/**`、`workflow_dispatch`、每週）：解析 revision 為 commit sha → `HfApi.model_info(files_metadata=True)` → 寫 `pins.lock` → 開 bot PR `chore(registry): pin …`（需要 GitHub App token）；nightly 檢查釘選仍可解析。

**理由**：(1) 讓開發分支不需要 HF 連線就能綠燈，又不需要任何假雜湊；保證由 release gate 補上，不是弱化。(2) 違反「PR 必跑測試在開發分支要綠」；(3) 讓測試資料與真實目錄分家。

**風險**：信任錨點是「釘選當下的 HF」（trust-on-first-use）——以人工審閱 bot PR diff、只釘 commit sha 不釘分支來緩解。開發版無法下載尚未釘選的預設模型，但使用者看到的是 blocker + CTA，不是死按鈕。第一次執行 `registry-pin.yml` 與合併 pin PR 需要使用者操作（BLOCKERS B-10）。

**影響的測試 ID**：INF-REG-001、INF-REG-002（文字精確化，不放寬）；新增 INF-REG-013（G）、INF-RT-012（U）；DL-* 使用 fake HF 伺服器的 fixture bytes 與對應的 fixture lock（真實 sha256）。

**同步修改的規格**：03 §1.1／§7、06 §1.1、06 §2 `resolve`、06 §2.6、06 §4.7 #69、09a §6.1／INF-RT、09 §5.5、08 C-27、11 §5.5。

## ADR-0003：元件文件與元件級測試用 Storybook 10（不是 8），以 addon-vitest + Playwright 執行

- 日期：2026-11-01｜狀態：已採納｜審議：1 個獨立 sub-agent（題目、04 §5.2、09 §2 測試層表、VIS-001／VIS-003／PF-080、apps/web 的套件版本）

**背景**：04 §5.2 寫「Storybook 8」，09 寫「Storybook test-runner」。實際檢查 npm：`@storybook/react-vite@8.x` 只接受 Vite ^4–^6，而本專案已是 Vite 8；目前的 `@storybook/test-runner` 又要求 Storybook ≥ 10 並帶入 Jest 30。規格原字面無法安裝。

**選項**：(A) Storybook 10（`@storybook/react-vite@10`，接受 Vite 8／React 19）+ `@storybook/addon-vitest`（Vitest 5 browser mode）+ Playwright `toHaveScreenshot`；(B) 自建 `/__gallery` 路由以 Playwright 驅動。

**決定**：採用 (A)，版本釘選、僅為 devDependency。VIS-001 以 Playwright 對建置後 Storybook 的 `iframe.html?id=…` 截圖（`maxDiffPixelRatio 0.001`、Linux Chromium、字型釘選）；VIS-003 由 `packages/i18n/errors.*.json` 自動產生每個錯誤碼一個 ProblemCard story，並以 contract 測試斷言 story 數 = 錯誤碼數；PF-080 以 `play` 函式經 addon-vitest 執行，另有測試列舉所有 GatedButton 停用狀態 story。

**理由**：符合規格原意（6 處提及 Storybook），提供設計系統文件與 a11y addon，stories 不進產品 bundle；(B) 需自建 story 列舉、主題／語系切換與隔離渲染，且測試碼留在產品原始碼樹。兩者測試強度相同，關鍵在執行方式。

**影響的測試 ID**：VIS-001、VIS-003、PF-080 的文字與門檻不變（不弱化）。

**補充（2026-11-01，實作時）**：不安裝 `@storybook/addon-vitest`，因為它還需要 Vitest browser mode 與 `@vitest/browser-playwright` 兩個額外相依，而且 Playwright 已能直接驅動建置後的 Storybook。PF-080 改由 Playwright 驗證：開啟 `iframe.html` 中每個停用狀態的 story，斷言 `aria-describedby` 指向可見的原因，並確認按鈕可以用鍵盤 Tab 到達（按鈕改用 `aria-disabled`，不用 `disabled`，以保留焦點）。斷言內容與門檻不變。字型依 04 §4.3 改為隨 app 打包（Inter、Noto Sans TC，Fontsource，版本釘選），讓視覺基準在不同機器上一致。**同步修改的規格**：04 §5.2（Storybook 8 → 10）、09 §2 測試層表（test-runner → addon-vitest + Playwright）。


## ADR-0004：等待中的 Operation 可以暫停與取消

- 日期：2026-11-01｜狀態：已採納｜審議：1 個獨立 sub-agent（題目、06 §2.9／§5.2／§5.3、DL-005、CORE-SM-006、OP-011／012／017、相關程式位置）

**背景**：06 §5.3 狀態圖中 `waiting` 只能轉到 `running`。實作 onboarding 離線情境時發現：下載退避重試（06 §2.9，2／8／30／120／300 秒）期間 Operation 應顯示 `waiting: NET_UNREACHABLE`，但使用者按取消或暫停時沒有合法轉移；API 照常回應，狀態卻不變（審議中確認為現存 bug）。

**選項**：(A) 新增 `waiting→pausing／cancelling`（Worker 持有 lease 時，由 Worker 確認），與 `waiting→paused／cancelled`（沒有 lease 時）；(B) 一律由控制平面直接轉為 `paused／cancelled`；(C) 維持狀態圖，等待中隱藏取消鈕。

**決定**：採用 (A)，並以「是否持有 lease」判斷，而不是依 `waiting_reason` 列舉。從 `waiting` 暫停時 `waiting_reason` 改為 `PAUSED_BY_USER`。下載退避期間每秒回報一次 waiting 心跳，讓控制通道在 2 秒內送達；退避的等待本身可被取消中斷。

**理由**：只有 Worker 能真正停止工作並刪除 `.part`（不變量 1、DL-005）；(B) 可能讓 Worker 從退避醒來後又對已取消的操作發出請求，破壞「已取消＝真的停了」。(C) 違反不變量 8（停用必附理由與修復）與 06 §5.2「所有類型可取消」，且可能把使用者卡住 5 分鐘。

**影響的測試 ID**：CORE-SM-006 的合法轉移表依新狀態圖加入 4 條邊（其餘非法轉移不變，`waiting→succeeded／failed` 仍非法）；新增 OP-018（回歸）、DL-023。**同步修改的規格**：06 §5.3 狀態圖與說明、09a（DL-023、OP-018）。


## ADR-0005：搜尋的「結果集合」與「排序」分開定義

- 日期：2026-11-01｜狀態：已採納｜審議：1 個獨立 sub-agent（題目、05 §2 與 TL;DR、05 selection_id 段落、10 §6、E2E-SEL-001、E2E-S-001、SRCH-FAC-*、search.py 與 routes_selection.py）

**背景**：05 寫「每路取 top-200，用 RRF 融合」。實作把每路截在 200 筆，並以截斷後的清單計算 `total` 與 facet。於是 3,214 張都帶「beach」的照片庫，搜尋只回報 230 張，「全選結果」也到不了 E2E-SEL-001 要求的 3,214 張。

**選項**：(A) 文字與標籤路徑回傳全部符合；(B) 維持每路 200，UI 顯示「200+」，全選只涵蓋前 N 張（需改測試文字）；(C) 「集合」與「排序」分開：集合決定數量、facet 與選取，RRF 只決定前段排序。

**決定**：採用 (C)，集合的定義取自 (A)。
- **符合集合 M** ＝（所選模式下的全文符合）∪（標籤符合）∪（向量 top-K，K＝200，不設分數門檻）。全部在 pre-filter 之後計算，並固定在 snapshot 上。
- `total`＝|M|，facet 在 M 上計算，selection 涵蓋 M。
- 排序：各路 top-200 組成的 RRF 視窗依分數在前，M 的其餘部分接在後面，依穩定順序（拍攝時間、asset_id）排列。
- 純向量查詢的 total＝K。

**理由**：
- 05 的 top-200 描述的是融合排序用的視窗，沒有定義結果集合的大小。05 的 selection_id 與 10 §6（優先序高於 05）都把「全選」定義為完整結果。
- (B) 會讓批次動作套用在任意的前 N 張，與使用者看到的數字不一致。
- 不設分數門檻：SigLIP 相似度尚未校準（待 M0），設門檻會讓數量無法重現。

**影響的測試 ID**：新增 SRCH-FAC-005（回歸）。E2E-SEL-001、E2E-S-001、SRCH-FAC-001／003／004 的文字不變。100k 照片的延遲基準記為 nightly 待辦。**同步修改的規格**：05 §2（結果集合與排序的定義）、09a（SRCH-FAC-005）。


## ADR-0006：PERF-003 量測「每路由首次載入的 JavaScript」，字型維持釘選

- 日期：2026-11-02｜狀態：已採納｜審議：1 個獨立 sub-agent（題目、09a PERF-003、10 §5.1 JS bundle 列、04 §4.3 字型與 l.649 效能預算、ADR-0003、量測數字）

**背景**：實作 PERF-003 時，照片路由的首次載入原本為 JS 172 KB + CSS 52 KB（gzip），全部路由在同一個 chunk，Noto Sans TC 的小子集字型也被 Vite 內嵌成 base64。改成路由 code-split、字型不內嵌後，JS 為 160 KB；CSS 仍有 42 KB，幾乎都是 `@fontsource-variable/noto-sans-tc` 的約 100 條 `unicode-range` `@font-face` 宣告。09a 原文「照片路由首次 ≤ 180 KB gzip；總預算 250 KB」沒有寫明要量哪些檔案。

**選項**：(1) 只量 JS，或 JS 加 CSS；(2) 若 CSS 也要計入，可以：(a) 延後載入字型 CSS，(b) 產品改用 OS 的 CJK 字型、只有測試用 Noto，(c) 把字型裁成固定字集。

**決定**：PERF-003 量**每個路由首次載入的 JavaScript（gzip）**：
- 照片路由 ≤ 180 KB；
- 任一路由 ≤ 250 KB；
- CSS（含字型宣告）照常記錄，但不設門檻。

字型維持 04 §4.3 的釘選與 unicode-range 切片，不採用 (a)–(c)。

**理由**：
- 10 §5.1 把 180 KB 放在「JS bundle」列，04 l.649 寫的是「首次載入 **JS** ≤ 250 KB」。250 KB 是首次載入的預算，不是所有 chunk 的總和：10 l.154 估計 MapLibre 單獨就約 250 KB，而且是 lazy 載入。
- 預算的用意是主執行緒的解析與編譯成本，CSS 宣告沒有這項成本；瀏覽器也只會下載頁面上用到的字形切片。
- (b) 會讓產品與視覺基準的渲染不一致（違反 ADR-0003），Linux 桌面可能沒有 CJK 字型。(c) 對使用者自己的資料（caption、資料夾名、人名）不可行。(a) 在桌面版沒有效益，還會帶來 FOUT 和視覺測試的競態。

**影響的測試 ID**：PERF-003 的 180／250 KB 門檻不變，只把量測範圍寫清楚（09a 改寫為「JS gzip」「每路由首次」）。實測：照片路由 160.1 KB；各路由最大 167.9 KB（/review）；CSS 42.3 KB 只記錄。


## ADR-0007：模型下載在 Worker 的子進程執行

- 日期：2026-11-02｜狀態：已採納｜審議：1 個獨立 sub-agent（題目、09a DL-019、06 §2、08 §1.1、`downloads.py`／`agent.py` 位置、量測數字）

**背景**：
- DL-019 要求在 1 GB、100 MB/s 的下載進行時，Job 吞吐下降 < 10%。
- Worker 原本在背景**執行緒**下載。每次讀取 socket、搬移 chunk、更新進度，以及每 1 MB 寫一次續傳狀態，都需要 GIL。
- 把模型主機移到獨立進程、比較兩個同樣大小的 Job 後，量到吞吐仍下降約 25%（50→38、46→34 items/s）。

**選項**：
- (a) 下載改在 Worker 的子進程執行；
- (b) 降低每個 chunk 的 Python 成本，下載仍留在執行緒；
- (c) 把測試改成與正式環境一致後再判斷，例如 API 改到獨立進程；
- (d) 降低下載執行緒的優先序。

**決定**：
- **採用 (a)，加上 (b) 的低成本部分，測試依 (c) 的方向重建。**
- 常駐迴圈（`background_downloads`）的每個下載都在 `multiprocessing` 的 `spawn` 子進程中執行，`Downloader` 本身不變。
- 進度與控制經兩條單向 pipe 傳遞。
- Worker 消失時，子進程讀到 EOF，保留 `.part` 後結束。
- 取消送出 1 s 後子進程仍未結束，就 `terminate()`。
- 續傳狀態改為每 8 MB、停止時與分段完成時才寫入。
- 一次性執行與注入 clock 的測試仍走進程內的 `_do_download`。

**理由**：
- 執行緒無法避開 GIL。(b) 只能降低成本，邊際會隨機器與 CPython 版本變動，10% 的門檻會時好時壞。
- (d) 對 GIL 沒有作用，Windows 也沒有對應機制。
- (c) 有一部分正確（測試中的 API 在同一個進程），但真實的 Worker 工作同樣有一部分是 Python，所以不能把差距歸給測試。
- `spawn` 在三個平台的行為一致，入口是模組層級的函式，可在可重定位的 venv（08 D3）中執行。
- 續傳狀態原本就存在磁碟上，DL-014 不需要新的機制。

**影響的測試 ID**：
- DL-019 改走正式路徑重建：經安裝 API 啟動，由 `run_once(background_downloads=True)` 租用；模型主機在獨立進程；比較兩組「單獨／下載中」同樣大小 Job 的中位數，門檻仍是 ≥ 0.9。
- 新增 DESK-006 子案例：Worker 消失時，下載子進程在 5 s 內結束並保留 `.part`。
- DL-003／005／007／014／022 維持綠燈。
- **量測**：雲端磁碟不足以放 1 GB 加 2 GB 預留空間，正式的 DL-019 由 nightly runner 執行（B-12）。本機以 250 MB／400 MB 的臨時實驗觀察到：子進程版是單獨執行吞吐的 0.88～0.97 倍，執行緒版約 0.75 倍。


## ADR-0008：「過年」等中文節日日期的範圍（Q-T5）

- 日期：2026-11-02｜狀態：已採納｜審議：1 個獨立 sub-agent（題目、09 Q-T5、09b SRCH-PLAN-005、05 §2.3）

**背景**：
- 規則式 planner 要把「過年」轉成 `taken_at` 區間。
- 09b SRCH-PLAN-005 的預期值標著「範圍定義待確認」（Q-T5）。

**選項**：
- 錨點：以除夕或以初一為準。
- 範圍：只有官方連假，或固定的相對區間。
- 沒有年份時的處理方式。
- 是否要依賴外部函式庫。

**決定**：
- **過年(Y) = `[初一 − 4 天, 初一 + 5 天)`**，半開區間，以 workspace 時區的本地日期計算。等同「除夕前 3 天」到「初五」（含）。
- 以初一為錨點：除夕在臘月只有 29 天的年份落在廿九，以初一為準可以避開這個例外。
- **同義詞**：過年、春節、農曆新年、農曆年、新春、舊曆年、大年。
- 「新年」單獨出現時有歧義，不解析，保留在 `text`，explain 提示「元旦／過年」。
- 元旦 = `[Y-01-01, Y-01-02)`。
- 「YYYY 過年」與「民國 N 年過年」指初一落在該西元年的那一次過年。
- 沒有年份時，取起點不晚於今天的最近一次；今天落在某次範圍內時就用那一次。
- 初一日期使用 `packages/core` 內建的 1900–2099 對照表，由 `tools/gen_lunar_table.py` 以 `lunardate` 在開發時產生，執行期沒有額外相依。
- 年份超出對照表時不加日期 filter，也不拋例外。
- 元宵、端午、中秋、清明延到 v0.3，屆時各自新增測試 ID。

**理由**：
- 照片裡的「過年」包含返鄉、除夕圍爐、初一到初三拜年與初二回娘家。
- 範圍寬一點的代價很小，因為 filter 是可移除的 chip；漏掉除夕晚餐才是真正的失敗。
- 固定的相對規則與「2024 夏天」一樣可預測、容易說明，也不依賴每年都會變動的政府行事曆。

**影響的測試 ID**：
- SRCH-PLAN-005 的預期值不變：「2025 過年」→ `[2025-01-25, 2025-02-03)`，初一為 2025-01-29。只更新範圍定義的文字，並移除「待確認」。
- 新增：
  - SRCH-PLAN-013：只寫「過年」、沒有年份。
  - SRCH-PLAN-014：同義詞「春節」。
  - SRCH-PLAN-015：「新年」不解析。
  - SRCH-PLAN-016：除夕落在廿九的年份（2030）。
  - SRCH-PLAN-017：年份超出對照表時不加 filter、不拋例外。
- 新增 CORE-LUNAR-001：對照表與已知日期一致。

## ADR-0009：v0.3 的 MCP tools 與 MCP-SCOPE-001 的拒絕語意

- 日期：2026-11-02｜狀態：已採納｜審議：1 個獨立 sub-agent（題目、09b §3.1–§3.2、05 §3.2.3 W-3、08 §9 路線圖、11 #100）

**背景**：
- 09b MCP-SCOPE-001 標為 v0.2，矩陣涵蓋 27 個 tools。其中 8 個在 09b §3.1 標為 v0.3：describe_photo、create_collection、add_to_collection、remove_from_collection、list_collections、count_photos、verify_photos、compare_photos。08 §9 也把它們排在 v0.3。
- 09b §3.2 寫的拒絕方式是 `insufficient_scope`（403）。05 §3.2.3 已採納的 W-3 則寫 tool 不出現在 `tools/list`、呼叫回 `-32601`、不洩漏 tool 的存在。兩者互相矛盾。

**選項**：
- A：把 8 個 tools 提前到 v0.2 實作。
- B：27 個定義都進 registry，以 `available_from` 標版本；v0.3 tools 在 v0.2 一律不可見、呼叫回 `-32601`。
- C：v0.2 先註冊 stub，呼叫回 NOT_AVAILABLE。
- D：B 加上統一拒絕語意。

**決定**：採 **D**。
- registry（`packages/mcp-tools/opagent_mcp/tools.py`）是 tool 名稱、schema、annotations、所需 scope、workspace 開關與 `available_from` 的唯一來源。
- 判斷順序：版本 → scope → workspace 開關，對應原因 `not_available`／`insufficient_scope`／`setting_disabled`。
- 對外：`tools/list` 只列可呼叫的 tools，順序固定；不可呼叫的一律回 JSON-RPC `-32601`。
- 對內：`mcp_call_log` 記 `status=denied` 與原因。
- 例外：token **已有** scope、只是 workspace 開關沒開時（`mcp.allow_apply_requests`、`mcp.faces_enabled`），回 tool error 並說明要到 app 哪裡開啟（MCP-SCOPE-006 的要求）。持有該 scope 的 token 本來就知道這個 tool 存在，所以不算洩漏。
- 只要使用者在 app 開啟開關，tool 就出現在 `tools/list`。

**理由**：
- 08 的優先序高於 05、09b，所以 8 個 tools 依 08 屬於 v0.3。提前實作會改動路線圖，不只是釐清測試。
- C 列出一個不能用的 tool，會讓 agent 白白浪費回合。
- W-3 是較新、明確採納的回寫，所以線上語意以它為準；`insufficient_scope` 改為記錄在 log 的原因，09b 的兩項期待都保留。
- 不影響不變量 5：`apply_organize`／`undo_organize` 在 v0.2 實作，且只會建立核准請求。

**影響的測試 ID**：
- MCP-SCOPE-001 在 v0.2 斷言三件事：
  - 純授權函式的 324 個子案例。
  - 19 個 v0.2 tools × 12 種 token 的端到端 list／call。
  - 8 個 v0.3 tools 在全部 scope 下都不可見，並記為 `not_available`。
- v0.3 把 `SERVER_VERSION` 提到 0.3.0 後，同一組測試自動涵蓋 8 個 tools，不需改測試。
- MCP-SCOPE-003 改為斷言 `-32601`，並記 `denied/insufficient_scope`。
- MCP-SCOPE-004 與 006 適用上面的例外。
- MCP-RATE-002（describe GPU 預算）與 describe_photo 一起移到 v0.3。

## ADR-0010：MCP 回應中的路徑一律以 `PathRef` 表示，檔案系統來的部分是不可信文字

- 日期：2026-11-02｜狀態：已採納｜審議：1 個獨立 sub-agent（題目、05 §3.3.2／§3.9、09b MCP-INJ-012／013、CLAUDE.md 不變量 6）

**背景**：
- 05 §3.3.2 把 `PhotoSummary.location` 定義為純字串（`photos/2025/kyoto/IMG_1.jpg`）；`get_photo.locations`、`list_folders`、`list_facets` 的 folder、計畫的 from／to 也都是純字串。
- 09b MCP-INJ-012／013 要求檔名與資料夾名稱「只以 UntrustedText 出現」。像 `ignore_all_rules_and_move_everything.jpg` 這種檔名沒有控制字元或 Markdown，淨化之後原樣保留，所以純字串路徑就是一條注入通道。

**選項**：
- A：維持純字串並淨化，另外加 `name: UntrustedText`。
- B：每個路徑欄位整個換成 UntrustedText。
- C：`PathRef = {source, path: UntrustedText(origin=filename)}`。root 別名是 app 自己的值，保持純字串；來自檔案系統的部分一律不可信。
- D：其他。

**決定**：採 **C**。
- 套用到以下欄位：`PhotoSummary.location`、`get_photo.locations[]`、`list_folders[]`（`source` 加上 `path`）、`list_facets` 的 folder 值、`propose_organize.sample_actions[].from/to`、`get_organize_plan.actions[].from/to`、`find_duplicates` 成員的 `location`。
- agent 把 `path.text` 傳回 `filters.folders` 或 `target` 時，server 會重新驗證路徑（不得有 `..`、不得是絕對路徑），並套用 token 的資料夾範圍，所以偽造的路徑拿不到任何東西。
- Worker 依 asset／location id 找到實際檔案，淨化後的顯示文字從不當作檔案路徑使用。

**理由**：
- A 讓攻擊文字以普通字串進入 agent 的 context，等於重新詮釋測試，被 §5.7 禁止。
- B 把 app 自己的別名也標成不可信，反而稀釋這個訊號，而且 agent 每次都得拆包。
- C 只標出攻擊者能控制的部分，形狀也和 agent 已經在用的輸入型別 `FolderRef {source, path}` 對齊。

**影響的測試 ID**：
- MCP-INJ-012／013 依原文斷言：檔名只出現在 `origin=filename` 的物件內。
- MCP-FOLD-001／003／005、MCP-TOOL-008 改用 `PathRef` 的形狀斷言。
- 同時新增 06 §4.7 #74～#93 的 MCP 錯誤碼（所有 MCP 錯誤都帶目錄中的錯誤碼）。路徑格式錯誤用 `PATH_INVALID`，只有違反 inputSchema 才回 `INVALID_ARGUMENT`。人物條件的權限不足用 `MCP_INSUFFICIENT_SCOPE`。

## ADR-0011：`bio` schema 的角色隔離（FACE-PRV-003）

- 日期：2026-11-02｜狀態：已採納｜審議：1 個獨立 sub-agent（07 §6.3、09b FACE-PRV-003、embedded PG 的 trust 認證）

**背景**：
- 07 §6.3(b) 要求 `bio` schema 只授權給 `opagent_bio`。
- 桌面版的內嵌叢集以 `initdb -U opagent -A trust` 建立，API 以 superuser 連線；superuser 不受 GRANT 限制。

**選項**：
- A：API 連線一律 `SET ROLE opagent_api`，臉部 repository 在交易內 `SET LOCAL ROLE opagent_bio`。
- B：拆出獨立的 LOGIN role 與兩個連線池。
- C：只記錄限制，不實作。

**決定**：採 A。
- `opagent_api`／`opagent_bio` 都是 NOLOGIN，彼此不是成員關係。
- API engine 的 connect hook 執行 `SET ROLE opagent_api`。
- 只有 `opagent_api.faces.bio_session` 會切換角色；`bio` schema 與分區由 `opagent_bio` 擁有。
- migration、pg-prepare 與備份仍以 owner 身分執行。

**理由**：
- trust 認證下，任何本機程序都能用任何角色登入，B 不會多提供安全性，卻要多一套連線與憑證。
- A 達成 07 的實際目的：一般查詢或除錯 SQL 意外碰到 `bio` 時，會直接失敗。
- 誠實的限制：這是防意外，不是對抗本機程式碼執行的安全邊界；邊界是作業系統帳號與全磁碟加密。

**影響的測試 ID**：FACE-PRV-003（API 連線的 `current_user`、`bio` 權限被拒、只有 faces.py 切換角色）。

## ADR-0012：單機的工作區臉部金鑰與 purge（FACE-PRG-002／003）

- 日期：2026-11-02｜狀態：已採納｜審議：同上 sub-agent

**背景**：
- 09b 把 FACE-PRG-002 寫成「edge vault 模式（v0.5 場景先以單機 vault 測）」。
- v0.2 桌面版的 embedding 存在 CP 的 `bio`，Worker vault 是 v0.5 edge 模式的元件。

**選項**：
- A：現在實作窄版：工作區金鑰（DEK）放 OS keyring，封裝可選的 `bio.sealed` 備份，加上 Worker `face_purge` 任務。
- B：兩個 ID 都延到 v0.5。

**決定**：採 A（窄版）。
- `opagent_worker.facevault`：AES-256-GCM，AAD 含格式版本與工作區 id。
- `face_purge` 任務刪除臉部縮圖並銷毀金鑰；它在佇列中排第一，Worker 重新連線時最先執行。
- UI 顯示「Worker 端待刪除」（`worker_purge_pending`）。
- edge 向量 vault、`face.knn`、`face.cluster` 留在 v0.5。

**理由**：
- 沒有可銷毀的金鑰，purge 就無法讓舊的封裝備份失效（07 §6.2 F6 的核心承諾）。
- 依「只有 Worker 碰檔案」，這些副作用屬於 Worker。

**影響的測試 ID**：FACE-PRG-002 的前置條件改為「單機 DEK／sealed 模式」；FACE-PRG-003。


## ADR-0013：問 AI 用 `⌘I`，`⌘J` 留給活動中心；兩個右側面板互斥

- 日期：2026-11-02｜狀態：已採納｜審議：1 個獨立 sub-agent（題目、06 §6.1 與 §6.6、09a ACT-005、10 §9.1／§3.5、00、04、ActivityCenter.tsx 與 activity.test.tsx）

**背景**：06 §6.1 與 09a ACT-005 把 `⌘J`／`Ctrl+J` 給活動中心抽屜（v0.1 已實作、測試綠燈）；10 §9.1、§3.5、00、04 又把 `⌘J` 給 v0.3 的問 AI 面板。06 與 10 優先序相同，11 與 08 沒有寫到快捷鍵。

**選項**：(A) 活動中心保留 `⌘J`，問 AI 改用別的鍵；(B) 問 AI 拿 `⌘J`，活動中心改鍵（要改 ACT-005 的規格文字與既有測試）；(C) 一個 `⌘J` 開共用面板的兩個分頁。

**決定**：採用 (A)，問 AI 用 **`⌘I`／`Ctrl+I`**。Lightbox 內仍用 `A`，⌘K 指令面板的「✦ 問 AI」列不變。兩個面板都在右側 420 px，所以**開一個就關另一個**；各自的快捷鍵只開關自己。關閉時焦點回到開啟面板的元素；那個元素已不存在時（例如從活動中心按 ⌘I），回到頂列的「✦ 問 AI」按鈕。

**理由**：
- (B) 等於為了還沒做的功能改寫已經綠燈的 v0.1 測試，違反「不改測試配合實作」。
- (C) 把「看背景進度」和「打字問問題」兩件事綁在一起，每次按鍵都得猜要開哪個分頁，06 的焦點規則也要重寫。
- 規格中已用的 ⌘ 組合：⌘K、⌘A、⌘J、⌘Z、⌘C、⌘Enter、⌘+／−、⌘／Ctrl+點擊。⌘I 不在其中，頁面內也能攔下瀏覽器預設行為（Firefox 的 Page Info、Safari 的 Mail this page）。
- Lightbox 的 `I`（資訊面板）不帶修飾鍵，與 ⌘I 不衝突。

**影響的測試 ID**：ACT-005 不變；新增 ASK-UI-002（09b §5）。**同步修改的規格**：10 §3.5、§9.1、§12 的對照表、00 §3、04 的導覽與 v0.3 表。

## ADR-0014：caption 文字向量（R2）預設用 Qwen3-Embedding-0.6B，低階機器用 multilingual-e5-small

- 日期：2026-11-02｜狀態：已採納（真機數字待 nightly）｜審議：1 個獨立 sub-agent，比較表見 `docs/research/text-embedding-choice.md`

**背景**：05 的 R2 路需要多語文字向量，路線圖列出 bge-m3 與 Qwen3-Embedding 兩個候選；查詢以繁中與英文為主，caption 由 VLM 產生。

**選項**（比較表見研究文件）：
- Qwen3-Embedding-0.6B
- BAAI bge-m3
- multilingual-e5-small
- 沿用已內建的 SigLIP2 文字塔

**決定**：
- 預設 **Qwen3-Embedding-0.6B**，存 1024 維。若 nightly 顯示 recall 損失可忽略，可改為 MRL 768 維。
- CPU only 與 Mac 8 GB 用 **multilingual-e5-small**（int8 ONNX）。它使用自己的 `caption:<model>` space 與 `query:`／`passage:` 前綴；這兩種機器也可以關閉 R2。
- 前綴與指令文字算在 config hash 裡（不變量 7）。

**理由**：
- 授權與專案相同（Apache-2.0）。
- MMTEB 多語平均比 bge-m3 高約 4.8 點，檢索高約 10 點。
- GGUF 可直接在已內建的 llama.cpp 執行，不需新 runtime。
- bge-m3 是次選：沒有前綴、有官方 ONNX；但分數較低，官方 ONNX 只有 2.27 GB fp32。
- SigLIP2 文字塔是為圖文配對訓練，64 token 上限太緊，而且與 R1 重疊，不採用。

**實作與校準（2026-11-10）**：之前 R2 只有測試用的假模型，模型目錄沒有文字模型，worker 也沒有 adapter，所以「建立索引」無法運作。改為在內建的 llama.cpp 以 `--embedding --pooling last` 執行 Qwen3-Embedding-0.6B（Q8_0 GGUF，639 MB）；查詢加上模型卡的檢索指令，照片說明原樣嵌入（SRCH-R2-006）。下限依參考機 645 張照片校準（照片說明作弱標註，22 個中英查詢）：0.45 保留相關 78%、混入不相關 4.65%；**0.50 保留 61%、混入 1.65%**；0.55 保留 32%、混入 0.14%。取 0.50：「夜景」「長頸鹿」為 0 張，「口罩」10/10、「跳舞」20/20。e5-small 尚未接入。

**影響的測試 ID**：SRCH-R2-001～004 以假的多語 embedder 驗證流程；真模型的召回、延遲與量化漂移列入 nightly（研究文件第 1～7 項）；模型 registry 新增兩筆 `text_embed` 條目（sha 由 `registry-pin.yml` 釘選）。

## ADR-0015：C-21 定案 — v0.5 內建精簡的 OAuth 2.1 授權伺服器，外部 IdP 之後再以進階模式提供

- 日期：2026-11-02｜狀態：已採納（**使用者決定**，2026-11-02）｜審議：2 個獨立 sub-agent，分別論證「內建 AS」與「委託外部 IdP」

**背景**：08 C-21 把 OAuth AS 延到 v0.5，並要求開工前重新評估是否改為委託外部 IdP（Authentik、Keycloak），CP 只當 RS。GOALS v0.5 規定：未定案時停下來回報。

**選項**：
- A. 內建精簡的 AS（CP 同時是 AS 與 RS）。
- B. 委託外部 IdP（Authentik、Keycloak），CP 只當 RS。

兩個 sub-agent 各自論證一個選項。

**兩份審議的共識**：
- 不論 AS 由誰提供，scope、資料夾範圍、media policy、workspace 這些授權政策都存在 CP 自己的 `oauth_grant`。有效權限 = RBAC ∩ grant。
- 撤銷 grant 時，下一個 MCP 呼叫就失敗（MU-OAUTH-001）。
- `organize:apply` 永遠不出現在 MCP token 的有效 scope 中。
- PAT 保留為退路。

**分歧**：
- **內建 AS**：沒有 IdP 的 NAS／homelab 使用者也能使用；PKCE、RFC 8707 `resource`、client 註冊都由我們掌握；可在 CI 中確定性地測試。代價是要維護一份安全關鍵的程式碼。
- **外部 IdP**：自己的安全程式碼較少。但很多自架者沒有 IdP，而且 IdP 對 MCP client 註冊（DCR／CIMD）的支援未經驗證。

**理由**：
- 兩份審議的共識部分在兩個選項下都成立，所以選擇只取決於分歧點。
- 沒有 IdP 的自架者也能使用，而且 CI 可以確定性地測試，所以選 A。
- A 的代價是要維護安全關鍵的程式碼。緩解方式：使用成熟的函式庫（Authlib）、刻意縮小功能範圍，並保留日後改用 B 的介面。
- C-21 是 GOALS 規定「未定案要停下來回報」的項目，最後由使用者選擇。

**決定**（使用者選擇）：
- v0.5 內建精簡 AS，以 **Authlib** 的 AuthorizationServer／ResourceProtector 實作，不自寫流程或加密。功能刻意縮小：
  - 只支援 authorization code ＋ PKCE S256、public client；不支援 implicit、password、client_credentials。
  - redirect_uri 完全比對，只接受 loopback 或 https。
  - 回應帶 `iss`（RFC 9207）；access token 為綁定 audience 的 15 分鐘 JWT。
  - refresh token 不透明、每次輪替；偵測到重用時撤銷整個 grant 家族。
  - CIMD 抓取強化：只接受 https、DNS 解析後拒絕私有與 link-local 位址、限制大小與時間。
  - 同意頁顯示 client_id 的網域，不只顯示 client 自報的名稱。
- AS 建在可替換的 RS 介面之上，**外部 IdP 模式之後以進階選項加入**（v0.5.x／v1.x），仍保留 CP 端的 grant 表。
- 人類登入維持本機帳號或 OIDC（02 ADR-006），與 MCP 的 AS 分開。
- 05 §3.2 的 `organize:apply` 寫法與 CLAUDE.md §3 衝突。依 CLAUDE.md（優先序較高的定案）：OAuth 同意流程**永遠不會**授予此 scope，請求中帶有它時直接剔除。

**影響的測試 ID**：MU-OAUTH-001～003 依此實作，並新增審議建議的 MU-OAUTH-004～008：
- PKCE 與 redirect 嚴格比對、授權碼重用；
- refresh 輪替與重用時撤銷；
- CIMD 的 SSRF 防護；
- 端到端探索（PRM → AS metadata → authorize → token → tools/call）；
- 有效 scope 永不含 `organize:apply`（property test）。

**同步修改的規格**：08 C-21、05 §3.2（`organize:apply` 註記）、09b §8.6。


**補充（2026-11-04，ADR-0019）**：「字型釘選」只釘選了字型檔；FreeType 的 hinting、antialias 與 fallback 字型依 OS 而異。CI 的視覺基準因此改在釘選的 Playwright 官方映像算繪。
---

## ADR-0016：v0.5 的 workspace 隔離採「每個 workspace 一個資料庫」

- 日期：2026-11-02｜狀態：已採納｜影響：02 §1.3、09b §8.4（MU-SEC-003）

**背景**：MU-SEC-003 要求 workspace A 的使用者、token、Worker 存取 B 的資產、計畫、搜尋、MCP 一律 404，搜尋 total 不含 B。02 §1.3 原本寫的是「業務表帶 workspace_id，TenantContext＋Repository 過濾，v2 再開 RLS」。現有程式碼以單一 workspace 寫成：約 557 個查詢點、79 處 `DEFAULT_WS`，還有多個以 id 直查、沒有 workspace 條件的端點。

**選項與審議**（兩個 sub-agent，各自論證一方）：
- **單一資料庫＋過濾＋RLS**：符合原規格；一套備份、migration 和連線池；多 workspace 的使用者與共用 Worker 較自然。代價是要替約 26 張子表補 `workspace_id`、composite FK，並逐一改寫 150～250 個查詢。只要漏一個過濾就是跨租戶洩漏（IDOR）。RLS 可以讓遺漏變成 fail closed，但 superuser 連線、背景工作與 outbox 都必須逐筆設定 context，同樣容易出錯。
- **每個 workspace 一個資料庫**：隔離是結構性的，A 的 engine 根本連不到 B 的資料列，所以遺漏過濾也不會洩漏。現有的 `create_app(dsn)`、`migrate(dsn)`、`pg_dump`、`embedded_pg` 的 template clone 都已經以 DSN 為單位。代價有四：連線數（NAS 的 `max_connections`）、N 個資料庫的 migration、跨 workspace 的管理檢視要在應用層 fan-out、一個 Worker 服務多個 workspace 時需要 CP 端排程。

**理由**：
- MU-SEC-003 要求的是零跨租戶洩漏。單一資料庫方案要正確，前提是改寫 150～250 個查詢時一個都不能漏。每個 workspace 一個資料庫則是在結構上就做不到洩漏。
- 現有程式碼本來就以 DSN 為單位（`create_app(dsn)`、`migrate`、`pg_dump`、`embedded_pg`），改動最少。
- 它的四項代價都可以接受，或可以延後：
  - 自架規模的 workspace 很少；連線池很小，而且延遲建立。
  - migration 可以逐一執行。
  - 跨 workspace 的檢視與排程留到之後。

**決定**：
- v0.5 採用**每個 workspace 一個資料庫**（同一個 Postgres server）。
  - 主資料庫存放控制表（`app_user`、`membership`、`user_session`、`enrollment_code`、`worker_credential`、`worker_nonce`，以及 OAuth grant），同時也是 `default` workspace 的資料。
  - 其他 workspace 各自有一個 `opagent_ws_<id>` 資料庫，含完整 schema。
- 控制平面的 guard 就是 TenantContext：依 session 選擇的 workspace，或 token、Worker credential 綁定的 workspace，把請求分派給該 workspace 的內部 app。
  - **認證只讀主資料庫**；workspace 資料庫裡的控制表一律不使用。
  - 內部 app 的連線池很小，而且延遲建立。
- 一個 Worker credential 只綁一個 workspace。同一台機器要服務多個 workspace 時，每個 workspace 各配對一次（v0.5 的限制）。跨 workspace 的 GPU 排程與 org 層總覽留到之後，以應用層 fan-out 處理。
- 各表保留 `workspace_id` 欄位並照常寫入，日後要合併資料庫或啟用 RLS 時不必重新設計。
- 不違反 CLAUDE.md §4 的任何不變量：SafePath、journal、Operation 都是 workspace 內的資料，恢復也在 workspace 內完成。

**影響的測試 ID**：MU-SEC-003（以兩個資料庫的情境驗證 404 與 total）。
**同步修改的規格**：02 §1.3「資料隔離」。

---

## ADR-0017：Worker 協定版本採 `"MAJOR.MINOR"` 字串，CP 服務 N 與 N-1 主版本

- 日期：2026-11-03｜狀態：已採納｜影響：VERSIONING §4、02 §5.3、09b §9.1（FRZ-002）

**背景**：v1.0 凍結 Worker 協定時，兩份規格的寫法互相衝突：
- VERSIONING §4：`protocol_version` 是整數，CP 接受「目前與前一個 minor 版本」的 Worker。
- 02 §5.3：版本採 semver，CP 同時支援 N 與 N-1 主版本。v0.5 的實作與 MU-WORKER-005 依照的是這一份。

**選項**：
- A. 整數 `protocol_version`，接受目前與前一個 minor（VERSIONING §4 原文）。
- B. `"MAJOR.MINOR"` 字串，服務 N 與 N-1 主版本（02 §5.3 與 v0.5 的實作）。

**理由**（一個 sub-agent 審議）：
- 協定在凍結後真正的破壞性變更，一年大約 0～1 次。
- 整數方案每次變更都要升號，也包括新增欄位這類相容的變更。這會讓「前一個 minor」的範圍跟著產品版本走，即使 wire 格式根本沒變，NAS 上更新較慢的 Worker 也可能被拒絕。
- 字串方案只看協定本身：minor 只能新增，major 才能破壞。快照測試（FRZ-002）可以直接用相容性比對器檢查這條規則。
- 遷移成本：字串方案為 0，因為程式碼、測試與 02 §5.3 已經一致。改用整數反而要在凍結前，對 v0.5 的 Worker 做一次破壞性變更。

**決定**：採用 `"MAJOR.MINOR"` 字串，與產品版本無關，在 `hello`／`welcome` 協商。
- minor 只能新增：選填欄位、新訊息、新的 task 種類；不移除也不收緊任何東西。
- 任何破壞性變更都要升 major。
- CP 服務主版本 N 與 N-1；更舊的 Worker 收到 `upgrade_required`（lease 與 poll 回 426），更新的收到 `server_too_old`。
- VERSIONING §4 改寫為上述規則；02 §5.3 維持不變。

**影響的測試 ID**：FRZ-002（快照比對 `x-protocol-version`、`/worker-api/v1` 的 schema 與 RPC 種類）、MU-WORKER-005（不變）。

---

## ADR-0018：Python 效能測試（layer F）在自己的 pytest 行程執行，不與整合測試共用

- 日期：2026-11-03｜狀態：已採納｜審議：1 個獨立 sub-agent（題目、09a VIEW-009／PERF-008、CLAUDE.md §4／§5、`test_timeline.py`、`justfile`、`ci.yml`、量測數字）

**背景**：VIEW-009（10 萬張的時間軸 API，單一 bucket p95 < 80 ms）單獨執行時一定通過，放在完整的 `just test-int` 最後執行時偶發失敗。在同一個行程、同一個資料庫上量測：
- SQL 本身只要 0.3 ms：index scan 加 incremental sort。
- 一般情況下 median 9.3 ms、p95 117.8 ms。用 `gc.callbacks` 追蹤，30 個請求期間剛好發生一次 generation-2 GC，耗時 232 ms；其他 gen-0／1 都在 0.4 ms 以下。
- 關閉 GC 時 p95 11.1 ms；先 `gc.collect()` 再 `gc.freeze()` 時 p95 12.3 ms。

這時行程裡有約 41 萬個被 GC 追蹤的物件，大多是前面約 300 個測試留下的。審議另外確認：
- 沒有 app 洩漏，PERF-008 的修正有效。
- 光是載入 `opagent_*` 模組就有約 19 萬個物件，完整一次 collect 約 46 ms。gen-2 的成本隨整個 heap 線性成長，所以 232 ms 符合預期，不是病態行為。
- 根本原因在選擇式：`test-int` 的 `-m` 覆蓋了 pyproject `addopts` 裡的 `not perf`，因此兩個效能測試（VIEW-009、PF-075）在整合測試行程的最後執行，量到的是別的測試塞滿的 heap。

**選項**：
- A. 不處理，或失敗時重跑：違反 09 §1.6（PR 不自動重跑，不穩定的測試必須修正），也會讓真正的退化被重跑掩蓋。否決。
- B. 計時前執行 `gc.collect()` + `gc.freeze()`：會連產品本身的 gen-2 成本一起拿掉，量到的是沒有出貨的設定，等於悄悄弱化測試。除非產品啟動時也呼叫 `gc.freeze()`（那是另一個產品決定，需要自己的測試 ID），否則不採用。
- C. 效能測試在自己的行程執行：不是弱化。它拿掉的是其他測試造成的干擾；產品的 API 本來就是獨立行程，產品本身的載入量與 GC 仍然算在量測裡。門檻、p95、10 萬張資料、暖身全部不變。
- D. 找出讓 gen-2 變慢的原因並修正：查過，沒有洩漏，產品程式碼不需要修改。
- E. 放寬門檻或改用 median：屬於弱化（§5.7 禁止），而且會掩蓋 tail latency 的退化。否決。

**理由**：
- C 是唯一不弱化測試，又能拿掉干擾來源的選項。
- 它也和正式環境一致：API 本來就以獨立行程執行。

**決定**：C。
- `just test-int` 與 ci.yml 的整合 job 改用 `-m "(integration or chaos) and not perf and not nightly and not release_gate"`。
- 新增 `just perf-api`，以 `-m "perf and not nightly and not gpu and not real_model"` 在獨立行程執行，結果寫到 `.tc/py-perf.json`。
- `just test-int` 先跑整合測試，再呼叫 `just perf-api`；CI 在同一個 job 加一個獨立步驟。PR 仍然必須通過這兩個效能測試。

**影響的測試 ID**：
- 新增 PERF-009（回歸）：整合選擇式收集不到任何 `perf` 測試，獨立的效能呼叫一定會收集到 VIEW-009 與 PF-075。先紅後綠。
- VIEW-009 與 PF-075 的斷言、門檻都沒有修改。

**同步修改的規格**：09 §2.1 Performance 列、09a PERF-008 備註與 PERF-009、CLAUDE.md §6 指令清單。

---

## ADR-0019：視覺基準只在釘選的 Playwright 官方映像中算繪；PR 的 E2E 不含 visual

- 日期：2026-11-04｜狀態：已採納｜審議：1 個獨立 sub-agent（題目、09 §2 與 §5、09a VIS-001～003／ACT-006、ADR-0003、`playwright.config.ts`、`visual.spec.ts`、ci.yml、nightly.yml、justfile、CI 的差異數字）

**背景**：
- PR #12 第一次跑 GitHub Actions，`e2e-smoke` 有 9 個視覺測試失敗。每張圖都約有 1% 的像素不同（723～3,517 px），元件、頁面、兩種主題都一樣，門檻是 0.1%。
- 兩邊的 Chromium 完全相同：@playwright/test 1.63.0，revision 1243。
- 差異來自 OS 的字型算繪：雲端開發容器有自己的 fontconfig（subpixel、hinting slight、系統 Inter、wqy fallback）。
- @fontsource 釘選的是字型檔，不是 FreeType 的 hinting、antialias 與 fallback 字型。所以 ADR-0003「字型釘選就能跨機器一致」的前提並不完整。
- 另外，09 §2／§5 規定 PR 只跑 E2E 煙霧、visual 在 nightly。但 ci.yml 的 `e2e-smoke` 跑的是 `just e2e` 全套（含 visual），nightly.yml 也沒有 Playwright job。

**選項**：
- A. 放寬到 0.02：違反 VIS-001 的「差異 > 0.1% 失敗」與 ADR-0003 的「門檻不變」，屬於弱化。而且改一個字或位移 1px 往往不到 2%，會漏掉真正的回歸。否決。
- B. 在 CI 用釘選的官方映像算繪與比對；基準由 `workflow_dispatch` 的 workflow 在同一個映像產生，人工審閱後提交。不是弱化，算繪環境也變得可重現。
- C. 依規格分層：PR 跑 E2E（不含 visual），visual 放 nightly。這符合規格，但單獨採用只是把失敗移到 nightly。
- D. 每個環境各一份基準：兩份會漂移，每次都要審兩次。否決。
- E. 在裸的 ubuntu runner 重產基準：runner 映像與 apt 字型會無預警變動。比 B 差。

**決定**：B＋C。
- Playwright 分成 `chromium` 與 `visual` 兩個 project；門檻 0.1% 與 `animations: "disabled"` 不變。
- `visual` 的快照路徑沿用拆分前的檔名。
- PR 的 `e2e-smoke` 改跑 `just e2e-pr`，也就是 `--project=chromium`：除了 visual，其他 E2E 全部照跑，不縮小 PR 的涵蓋範圍。
- nightly.yml 新增 `visual` job，在 `mcr.microsoft.com/playwright:v1.63.0-noble` 內執行 `just visual`。
- 新增 `visual-baselines.yml`（`workflow_dispatch`，同一個映像）：
  - 先比對並保留報告，再 `--update-snapshots`；
  - 上傳新基準，由人逐張審閱、提交。
- 本機 `just e2e` 仍然跑兩個 project：現有基準就是在這個開發容器產生的，所以本機仍然可以重現。
- 新基準提交後，本機與 CI 以釘選映像的基準為準。

**理由**：
- 這是唯一不弱化門檻，又讓 CI 能重現基準的做法。
- CI 的輸出本身沒有錯；問題是基準只記錄了一個別人無法重建的環境。
- 因此，改用釘選映像重產基準，不是「修改 golden 去配合錯誤的輸出」。但這仍然是 golden 的變更，所以必須人工審閱。

**影響的測試 ID**：
- 新增 VIS-004：跑 `visual` 的 job 一律使用與 `@playwright/test` 同版本的映像；nightly 與 baselines workflow 都有；PR 只跑 `chromium`。
- VIS-001～003、ACT-006 的斷言與門檻都沒有修改。新基準提交前，這四條在 CI nightly 會是紅的，記在 B-21。

**同步修改的規格**：09 §2 Visual regression 列；ADR-0003 的「字型釘選」補充為「字型檔釘選，算繪環境由 ADR-0019 釘選」；CLAUDE.md §6 指令清單。

---

## ADR-0020：桌面 PG 17 的 initdb 用 builtin provider 的 `C.UTF-8`，OS 層的 lc_* 用 `C`

- 日期：2026-11-04｜狀態：已採納｜審議：1 個獨立 sub-agent（題目、09a PG-001、08 §1 initdb 列、`embedded.py` 的 `_initdb`、`build_desktop_pg.sh`、CI 的錯誤訊息）

**背景**：
- 09a PG-001 規定 `--locale-provider=builtin --locale=C.UTF-8 -E UTF8`。
- desktop-build 在 macos-14 與 windows-latest 上，initdb 以 `invalid locale name "C.UTF-8"` 失敗。
- 原因：`--locale` 同時設定 OS 層（libc）的 lc_collate、lc_ctype、lc_messages 等，而 `C.UTF-8` 只有 glibc 有。builtin provider 的 locale 要用 `--builtin-locale` 指定。
- 所以原本的規格在三個目標平台中的兩個上根本無法滿足，桌面版在那兩個平台會建立不了資料庫。

**選項**：
- (a) 保留 `--locale=C.UTF-8`，macOS 與 Windows 改用各自的 libc locale：catalog 與設定在每個 OS 都不同，違反 08 §1「不依賴 OS」的目的。否決。
- (b) `--locale-provider=builtin --builtin-locale=C.UTF-8 --locale=C`：
  - SQL 的排序、大小寫與字元類別全部來自 PostgreSQL 本身，三個 OS 完全一致。
  - 排序依 code point；大小寫轉換與字元類別支援 Unicode。
  - `lc_messages = C`：伺服器訊息為英文，app 本來就把錯誤對應到自己的錯誤碼與 i18n。
- (c) `--builtin-locale=C`：完全不受版本影響，但 `lower()` 與 `ILIKE` 只處理 ASCII。`person_name_uq`（`lower(display_name)`）會把「Émile」與「émile」當成兩個人，是行為回歸。否決。

**決定**：(b)。
- 程式碼（PG-016）與 `build_desktop_pg.sh` 已經是這組參數。
- PG < 17 的 `--locale=C.UTF-8` 只保留給 Linux 開發環境（雲端 PG 16）。

**理由**：
- (b) 正確表達了 PG-001 的原意：UTF8、builtin provider、C.UTF-8 語意、不依賴 OS。
- 原本規格的寫法是錯的：它把 provider 的 locale 與 libc 的類別混在一起。

**影響的測試 ID**：PG-001 改為更嚴格的斷言。在 PG 17 上檢查：
- `datlocprovider = 'b'`、`datlocale = 'C.UTF-8'`、`datcollate = datctype = 'C'`；
- `upper('é') = 'É'`、`'é' ~ '^\w$'`；
- 排序為 `B,a,é`。

在 PG 16 上檢查 libc 的 `C.UTF-8` 與 `datlocprovider = 'c'`。兩條路徑都是真的斷言，不是 skip。PG-016 不變。

**同步修改的規格**：09a PG-001；08 §1 initdb 列補上「主版本升級後要 REINDEX 依賴 `lower()` 的運算式索引」。

---

## ADR-0021：coverage-status 由 CI 檢查一致性，而不是逐位元比對

- 日期：2026-11-04｜狀態：已採納｜審議：1 個獨立 sub-agent（題目、09 步驟 6、`tools/tc_status.py`、pytest 外掛、ci.yml、nightly.yml、release-gate.yml、本機 `.tc/` 的內容）

**背景**：
- 09 步驟 6 規定 CI 重新產生 `docs/generated/coverage-status.md` 並比對，未更新就失敗；`--check` 是逐位元比對。
- 在 upload-artifact 開始收錄隱藏的 `.tc/` 之後，CI 才第一次拿到結果，然後一直失敗。
- 兩邊的輸入本來就不同：
  - 提交的檔案含有 PR CI 從來不會產生的證據：本機 nightly、visual project、獨立行程的效能測試。
  - CI 看得到開發機看不到的結果：其他 OS、PG 17 專屬分支。
- 因此沒有任何一份提交的檔案能等於 CI 重新產生的結果：這個檢查從來不會通過，也就證明不了任何事。

**選項**：
- (a) 只用 CI 產物逐位元比對：每個 nightly／visual／硬體 ID 都會變成未開始，檔案反而不真實。開發者也只能提交上一次 run 的產物，永遠差一次 push、無法收斂。否決。
- (b) 一致性檢查。
- (c) bot 依 CI 產物重新產生並 commit：需要對 PR 分支的寫入權限（fork 不行），`GITHUB_TOKEN` 的 commit 不會觸發 CI，而且會改寫別人的分支。否決。
- (d) 提交 nightly／visual 的原始 JSON 一起合併：自行宣告，隨手就能偽造。否決。

**決定**：(b)。`tools/tc_status.py --verify --pr-results <CI 產物> --inventory <測試清單>`：
1. 檔案必須能由 09a／09b 與它自己的每個 ID 狀態重新產生（標題、版本、ID 集合、摘要數字都不能手改或漂移）。
2. CI 結果或 inventory 出現 09a／09b 沒有的 ID 就失敗。
3. PR CI 跑過的 ID：CI 為紅，檔案只能是紅；CI 為部分，只能是部分或紅；CI 為綠而檔案較保守，只給警告。
4. PR CI 沒跑的 ID：只有在 inventory 中確實有屬於 nightly、visual、perf、OS、release-gate 層的測試時，才能標成未開始以外的狀態；有 PR 層測試卻沒有 CI 結果，就失敗。
5. 提供 `--nightly-results` 與 `--strict-nightly`：nightly 證據比檔案紅時警告，strict 模式下失敗。

配套：
- inventory 由 pytest 外掛（`OPAGENT_TC_INVENTORY`，收集所有層、不套用 OS 篩選）與 `tools/tc_inventory_e2e.py`（Playwright `--list`）產生，在新的 `tc-inventory` job。
- OS 矩陣上傳結果。
- `tc-status` 以 `if: always()` 執行，紅的 job 的結果也算證據。
- nightly 的 visual 產物改名為 `tc-visual`，並收錄隱藏檔，release gate 才真的看得到。
- release gate（`--gate`）只採用 CI 產物，不讀提交的檔案。

**理由**：
- (b) 檢查的正是規格的本意：PR 不能把紅的或不存在的測試宣告成綠，而且規格列表沒有漂移。
- 它不會比原本弱：原本的檢查從來無法通過。
- 唯一能自行宣告的是 PR CI 不跑的層，而規則 4 要求那一層確實有測試；發版的權威仍然是只讀 CI 產物的 release gate。

**影響的測試 ID**：新增 TCS-001（回歸）～TCS-006，先紅後綠。

**同步修改的規格**：09 步驟 6、09 test-integrity 列；09a 新增 TCS 列。另外補上 FS-028～030 與 MCP-SCOPE-001 的規格列：這次的一致性檢查發現它們有測試，卻沒有出現在 09a／09b 的表格裡。

**後續**：nightly 以 `--strict-nightly` 檢查 `main` 提交的檔案，需要 nightly run 同時拿到當次的 PR 層結果，目前尚未接上。在那之前，規則 5 只在本機與 PR 上以警告形式提供。

## ADR-0022：Linux 安裝包的大小，以及隨附 Python runtime 的 P0 錯誤

- 日期：2026-11-04｜狀態：已採納｜審議：2 個獨立 sub-agent（打包面、payload 面各一個；題目、08 §4.1 D6、DESK-030、desktop-build.yml、`tools/build_desktop_python.py`、`tools/build_desktop_pg.sh`、CI 的大小明細）

**背景**：
- desktop-build 第一次完整跑完：Windows NSIS 與 macOS dmg 通過 200 MB；Linux AppImage 為 220.9 MiB，**超過硬上限**；.deb 為 186.3 MiB，但大小閘門只檢查 AppImage。
- 明細：AppImage 內含 linuxdeploy-plugin-gtk 帶進來的 WebKitGTK、GTK、ICU 等，解壓後約 180 MB。08 §4.1 原本就寫「AppImage 不附 WebKitGTK，依賴系統」，實作與規格不一致。
- payload 面的審議另外發現一個 **P0 錯誤**：`uv venv --relocatable` 只讓套件可搬移，直譯器本身是指向建置機 uv 目錄的 symlink，樹中也沒有標準函式庫。安裝後的 app 在任何使用者機器上都無法啟動 sidecar。CI 沒有發現，因為建置機上的 symlink 剛好有效。

**選項**：
- (a) 放寬 Linux 的上限：違反 D6 的硬上限。否決。
- (b) AppImage 依賴系統 WebKitGTK（符合 08 §4.1），缺少時由 AppRun 給出原因與修復指示。
- (c) 縮減 payload：jieba 未使用的子模組（約 15 MB）、ORT 未使用的 provider（約 10 MB）、strip debug info（約 8 MB）、標準函式庫中不會用到的模組；以自己的實作取代 HDBSCAN 相依（約 44 MB）。
- (d) 把 GeoNames 改成 data pack（D6 原本的第一順位）：資料只有約 10 MB，不足以解決問題。

**決定**：
1. **先修 P0**：runtime 改為複製整個 uv 管理的 python-build-standalone，再把套件裝進去。新增 DESK-039（回歸）：複製到別的路徑、以空環境 `-I` 執行；desktop-build 對實際產物再跑一次同樣的檢查。
2. **大小閘門檢查每一種安裝包**：Linux 同時檢查 AppImage 與 .deb（DESK-030 的文字同步修改）。
3. **低風險的 payload 縮減先做**：jieba 子模組、標準函式庫的 `tkinter`／`idlelib`／`turtledemo`／`ensurepip`／`lib2to3`／`pydoc_data`。
4. **做 (b)**。這本來就是 08 §4.1 的設計，與大小無關，而且修正 runtime 只會讓安裝包變大（多了標準函式庫），所以不等重新量測：AppImage 不附 WebKitGTK／GTK，AppRun 先檢查系統函式庫，缺少時顯示新的錯誤碼 `DESK_WEBVIEW_MISSING`（含各發行版的安裝指令）。另外新增 DESK-037（AppImage 內沒有 WebKitGTK 的結構檢查）與 DESK-038（AppRun preflight）。
5. HDBSCAN 改寫延後：風險較高，而且人臉分群的測試要重新驗證，等真的需要時再做。

**理由**：
- 正確性優先於大小：一個無法啟動的安裝包，大小合格也沒有意義。
- (b) 只是讓實作回到 08 §4.1 原本的設計；Linux 桌面環境幾乎都已有 WebKitGTK，.deb 本來就是用套件相依取得。
- (c) 之中風險低的項目不改變任何行為，只移除不會被 import 的檔案與除錯資訊。

**影響的測試 ID**：新增 DESK-039（回歸，先紅後綠）；修改 DESK-030 的範圍（檢查更多產物，不是放寬）；新增 DESK-037／038（決定 4，先紅後綠）。

**同步修改的規格**：09a DESK-030、DESK-037～039；06 §4.7 #111 `DESK_WEBVIEW_MISSING`；08 §4.1 D6 的超標處理順序。

**實作補記**：strip debug info 試過一次後撤回。加上 strip 之後，Ubuntu 22.04 runner 上 linuxdeploy 的 `ldd` 在 scipy 的擴充模組上失敗；沒有 strip 時同一批套件可以正常打包。原因推測是舊版 binutils 處理 auditwheel 以 patchelf 修改過的函式庫時出錯，但沒有在 runner 上單獨驗證。這只省約 8 MB，不值得冒險。DESK-039 與 CI 的 runtime 煙霧測試因此擴大到 numpy、scipy、sklearn、onnxruntime、Pillow、pillow-heif、psycopg 等原生擴充，壞掉的函式庫會在這裡就失敗。

**實作**：`tools/slim_appimage.py`。AppRun 會依名稱 source 每個 hook，所以它會重寫 AppRun：移除 GTK hook，先 source 我們的 preflight，再保留其他 hook。重新打包時沿用原本的 AppImage runtime 與 squashfs 壓縮方式；desktop-build 會重新解開並驗證，在 release tag 上重新簽章。

**量測（desktop-build，commit `1368247`）**：

| 安裝包 | 大小 | 結果 |
|---|---|---|
| Windows NSIS | 101.3 MB | 在目標 150 MB 內 |
| macOS dmg | 124.7 MB | 在目標內 |
| Linux AppImage | 158.6 MB（精簡前 224.1 MiB，移除 119 項） | 通過上限；DESK-037 驗證不含 WebKitGTK |
| Linux .deb | 198.6 MB | 通過上限，但只剩 1.4 MB |

三個平台的 DESK-039 煙霧測試都通過（含原生擴充）。.deb 的餘裕太小，下一次相依升級就可能超標，因此再從 runtime 移除 `pip`（安裝後的 app 不安裝任何東西；runtime pack 是簽章過的封存檔），DESK-039 同時檢查 runtime 中沒有 pip／setuptools／wheel。.deb 仍高於 150 MB 目標：下一步是 ORT 未使用的 provider（約 10 MB）與 HDBSCAN 改寫（決定 5），列為後續工作。

移除 pip 後（commit `d2b3e8d`）：NSIS 100.4 MB、dmg 123.2 MB、AppImage 157.2 MB、.deb 197.0 MB，全部通過；.deb 的餘裕約 3 MB，仍然偏小，上述後續縮減應在下一次加入相依前完成。

## ADR-0023：app 的資料隨套件安裝，不再從原始碼樹找

- 日期：2026-11-04｜狀態：已採納｜審議：1 個獨立 sub-agent（題目、08 D3、`tools/build_desktop_python.py`、八個以 `Path(__file__).parents[N]` 找資料的位置、Dockerfile、DOCK-004、ADR-0002）

**背景**：
- ADR-0022 之後，CI 上的 integration job 卡住，追查時發現隨附 runtime 的另一個 P0：
  - `uv export` 把 workspace 自己的套件輸出成 editable，runtime 裡只有指回建置機 repo 的 `.pth`。
  - 即使改成真正的 wheel，模型 registry、內建整理方式、zh-TW 標籤、標籤詞彙都以 `Path(__file__).parents[N]` 從 repo 根目錄讀取。
- 桌面安裝包只帶 `python/` 與 `pg/`，所以安裝後的 app 既 import 不到自己的程式，也讀不到資料。
- DESK-039 沒有發現，因為測試與 CI 煙霧測試執行時，repo 仍在原來的路徑。
- Docker 映像靠逐一 COPY repo 目錄繞過這個問題，而且這份清單已經漂移過一次（DOCK-004）。

**選項**：
- (A) 每份資料移進擁有它的 Python 套件，成為 package data。
- (B) 資料不動，加一個 data-root 解析器（環境變數 → 安裝位置 → repo），由打包流程複製資料。
- (C) 用 hatchling `force-include` 在建 wheel 時把樹外的目錄帶進去。

**決定**：(A)，只有 zh-TW 標籤例外。
- `registry/` → `packages/registry/src/opagent_registry/builtin/`。
- `presets/builtin/` → `apps/api/src/opagent_api/presets/builtin/`。
- 標籤詞彙 → `packages/core/src/opagent_core/vocabularies/`。
  - 審議建議放在 opagent_inference，再由 search import。但 API 經由 search 會間接 import inference，違反不變量 1，所以改放 core。
  - core 只提供路徑常數，讀檔由呼叫端負責，不破壞 core 的純度。
- zh-TW 標籤：web build 也讀同一份，所以來源仍在 `packages/i18n`。由 `tools/gen_api_labels.py` 產生 `opagent_api/_data/zh-TW.json`，`just lint` 以 `--check` 比對，不使用執行期的退回路徑。
- runtime 建置改為 `--no-editable`、`--no-sources`，並重建 app 自己的套件（`--refresh-package`）。
- hw profile 與 FakeBackend 向量只供測試使用，留在 `fixtures/`，不進安裝包。

**理由**：
- (A) 讓開發、Docker、wheel、桌面讀同一個路徑，沒有退回鏈，也沒有各自維護的複製清單。
- (B) 的環境變數覆寫會讓人能把釘選過的 registry 換成未釘選的版本，削弱 ADR-0002 的信任錨。它也保留了雙路徑與清單漂移的問題。
- (C) 從 sdist 建置會失效，editable 安裝也不一定支援。

**影響的測試 ID**（都先紅後綠）：
- DESK-039 加強：每個 import 的模組都必須從搬移後的 runtime 載入。
- 新增 DESK-040：以 app 自己的載入函式讀資料，並稽核每一次開檔。
- 新增 DESK-041：`src/` 不得以 `parents[N]` 找資料，例外只有三個測試 fixture，各附理由。
- 其他測試只改了資料路徑，斷言不變。
- DOCK-004 不變；Dockerfile 移除已不存在的 `registry/`、`presets/` COPY。

**同步修改的規格**：
- 03、06、09、09a、11 中 registry、presets 的路徑。
- 09a 新增 DESK-040／041。
- `registry-pin.yml` 與 `tools/pin_registry.py` 的路徑。

**後續**：jieba 把字典快取寫在系統暫存目錄。這不影響正確性，但多人共用的 Linux 上宜改到 app 的快取目錄。

**CI 補記**：
- integration job 卡住的真正原因：`uv python find --managed-python --no-project` 會回傳專案目前的 `.venv`。CI 的 venv 建在 runner 的系統 Python 上，所以 runtime 的「來源 prefix」變成 `/usr`，建置開始複製整個 `/usr`。
  - 開發容器的 venv 剛好是 uv 管理的直譯器，所以沒有發現。
  - 改從 `uv python dir` 內的安裝挑選，並拒絕任何不在該目錄下的來源。
  - 新增 DESK-042（回歸，先紅後綠）。
- 同一次 CI 中，IMG-002 再次看到 `outbox.db-journal`：outbox 改成在 worker 建立時（單執行緒）就以 WAL 建立。Q-029 加強為「建立後立即是 WAL」。

## ADR-0024：Windows 上的「0600」是受保護的 DACL，並在建立時設定

**日期**：2026-11-05｜**狀態**：已採納｜**審議**：1 個 sub-agent（依 CLAUDE.md §5.8）

**問題**：規格（08 §1.3、02 §5.2、02 §6）要求秘密檔案 0600、秘密資料夾 0700。Windows 的 `os.chmod` 只切換唯讀位元，所以 `run/api.json`、`remote.json`、`worker.key`、`config/lan.json` 與 credstore 的退回檔案都只拿到繼承來的 ACL。資料夾放在非系統碟時，這個 ACL 實測是 `AU` 可修改。

**選項**：
- A：維持只有 POSIX 的規格，把 Windows 的落差寫進文件。
- B：`icacls` 子行程。
- C：pywin32。
- D：DPAPI 或 Credential Manager。
- E：以 ctypes 呼叫 advapi32，在建立時帶入 security descriptor。

**決定**：E。
- 規格裡的「0600／0700」一律解讀為 **owner-only**。POSIX 維持完全相同的 mode。
- Windows 為受保護的 DACL，只有目前使用者（取自 process token）與 SYSTEM，都是完全控制：`D:P(A;;FA;;;<user>)(A;;FA;;;SY)`。資料夾的項目加上 `OICI`。
- 新套件 `packages/secure-file`（`opagent_secure`），沒有相依，`secure-leaf` 合約確保它不 import 其他套件。
- `write_private()`：暫存檔用唯一檔名，`CreateFileW(CREATE_NEW)` 時就帶 descriptor，從 handle 讀回確認後才寫入，再 `os.replace`。
- `restrict()`：收緊既有檔案與資料夾。`private_dir()`：建立並收緊資料夾。
- 不支援 ACL 的磁碟區（FAT、exFAT、部分網路磁碟）：照寫，回傳 `False`，記錄 `SECRET_UNPROTECTED_VOLUME`。支援 ACL 但讀回不符：拒絕（fail closed）。

**理由**：
- A 不成立：資料夾可以放在 `D:\`，而且 `run/api.json` 也有完整性問題。`opagent mcp` 會把 PAT 送到這個檔案指定的 port，其他本機使用者可以改檔，把 PAT 導走。所以 `api.json` 雖然只有 port 和 pid，也比照秘密處理。
- B 是建立之後才改，留有空窗；C 增加安裝包大小；D 不涵蓋 `api.json` 的完整性，worker 也沒有 keyring 相依。
- 納入 SYSTEM：它相當於 root，POSIX 的 0600 也擋不住 root，排除它只會弄壞系統服務。
- 不納入 Administrators：提權的管理員本來就能取得所有權，而未提權的行程不應該因為在這個群組就能讀。
- 放在新套件：`core` 必須純粹；`opagent_fs` 不行，因為 api 經由 mcp-tools 用到 credstore，會間接違反不變量 1。

**最強的反對意見**：單人桌面在使用者設定檔底下，繼承的 ACL 本來就安全，這些 ctypes 程式碼不划算。但資料夾明確允許放在其他磁碟，上述 PAT 導向風險就是實際的。

**測試**：
- 新增 DESK-060、DESK-061（回歸，先紅後綠）。
- `opagent_testing.perms.assert_owner_only`：POSIX 分支仍是完全相等的 mode 比對；Windows 分支是實作的 oracle，不共用任何程式碼（`icacls /save` 取 SDDL、`whoami` 取 SID）。
- pytest 的 tmp 資料夾本來就是私有的，測試會先用 `widen()` 放寬，並有負向對照，否則斷言沒有意義。
- MIG-002、MCP-PAT-001、DESK 系列的 mode 斷言改用此 helper，POSIX 行為不變。

**同步修改的規格**：08 §1.3、09a DESK-060／051、PG-022、09b MCP-PAT-001。

**補記**：`EmbeddedPg` 的 SCRAM 密碼檔（`pgpass`、`.pw-initdb`）也走同一個 helper（PG-022）。

**順帶修正的 POSIX 問題**：`Credentials.save` 先寫入才 chmod；credstore 寫入既有檔案後才 chmod；`api.json.tmp` 是固定檔名。現在都是原子寫入。

**後續（尚未處理）**：
- SID 改變（重灌 Windows、把磁碟搬到別台）之後，受保護的 `run/` 對新帳號不可寫，app 會起不來。需要獨立的錯誤碼與修復 CTA（不變量 8）。
- 資料庫、縮圖與人臉資料在 `D:\` 上仍依繼承的 ACL。是否保護整個資料夾要另行決定。
- OneDrive 資料夾：ACL 擋不住以使用者身分執行的同步程式，應由 doctor 警示。

## ADR-0025：Windows 安裝包內建 Vulkan 版 llama-server

- 日期：2026-11-05｜狀態：已採納｜審議：1 個獨立 sub-agent（題目、08 §0 D5、§3、§4.1、實測數據）

**背景**：08 §4.1 規定每個平台都有零下載的路徑，內建 CPU 版，GPU 靠簽章過的 runtime pack。但 pack 要等使用者的簽章金鑰與 CI recipe（B-5、B-17）。在那之前，只有 CPU 版的安裝包會讓 GPU 使用者跑不動 8B VLM，而 08 §3.3 的 CPU 檔位預設沒有 VLM，v0.1 的招牌功能等於不存在。

**選項**：(A) 依規格內建 CPU 版；(B) Windows 內建 Vulkan 版，pack 裝好時優先；(C) 兩個都帶，執行時選擇。

**決定**：採用 (B)。官方 win-vulkan 包已含所有 CPU backend，保留了零下載的 CPU 路徑；壓縮後約 38 MB，安裝包約 139 MB，仍在 150 MB 目標內。(C) 多 23 MB 與選擇邏輯，只有在 Vulkan 版無法退回 CPU 時才值得。macOS 維持 Metal，Linux 維持 CPU（.deb 空間不足）。

**實測**（RTX 5070 Ti，Qwen3-VL-8B Q4_K_M，b12121）：CUDA 13 約 125 tok/s；Vulkan 只用獨顯 **約 85 tok/s，每張約 1.3 秒**。

**實機發現**：這台機器同時有 AMD 內顯。llama.cpp 預設把模型切到所有裝置，內顯還回報整個系統記憶體，速度掉到 27 tok/s。`load_local` 因此解析 `--list-devices`，排除內顯，只用最大的獨顯（INF-LOCAL-006）。原本審議時擔心的「比 CUDA 慢 3–4 倍」，大部分是這個原因。

**退回 CPU**：`GGML_VK_VISIBLE_DEVICES=none` 時 `--list-devices` 乾淨地回報沒有裝置；`OPAGENT_LLAMA_CPU=1` 強制 `--device none`。

**仍待驗證**：沒有 `vulkan-1.dll`、壞掉的 ICD、無 GPU 的 VM；AMD 獨顯與 Intel 內顯的實機表現（只有這一台參考機）。NVIDIA 使用者在 pack 可用後仍應改用 CUDA。

**同步修改的規格**：08 §3.2、§4.1。

## ADR-0026：移除 SigLIP2 int8，CPU 機器也用 fp16

- 日期：2026-11-05｜狀態：已採納｜審議：未開 sub-agent：M0-S4 的門檻已在 09 §6 定案，實測遠低於門檻，沒有取捨空間

**背景**：03 與 recommend 讓沒有 GPU 的機器用 `onnx-int8`。M0-S4 實測公開 int8 匯出的影像向量 cosine 最低 **0.263**、平均 0.544，門檻是每個向量 ≥ 0.999。這樣的向量等於錯誤的搜尋結果。

**決定**：從目錄移除 int8，`EMBED_CPU` 改用 fp16。fp16 在 `onnxruntime-directml` 1.24.4 的 CPU EP 上圖優化會失敗，所以 `OrtEmbedAdapter` 在載入失敗時關閉圖優化重試。

**代價**：沒有 GPU 的機器下載 2.3 GB 而不是 1.1 GB。實測 CPU 嵌入 0.78 張／秒（Ryzen 9 9900X，未做圖優化），以 `onnx-fp16-cpu` variant 記錄效能先驗。

**同一批實測的目錄修正**：AuraFace 實際是 fp32、249 MiB，超過安裝包預算，`bundled` 改為 false，variant 改名 `onnx-fp32`，開啟人臉功能時才下載。

**影響的測試**：把 int8 當範例 variant 的 fixture 改為 fp16，斷言不變；INF-RT 的 CPU 機器推薦改為 fp16。

## ADR-0027：向量路徑有相似度下限，低於下限的另列「可能相關」

- 日期：2026-11-06｜狀態：已採納，**取代 ADR-0005 的「不設分數門檻」一句**，其餘不變｜審議：1 個獨立 sub-agent（題目、ADR-0005、05 §2、search.py、實測分布）

**背景**：向量路徑取最近的 K＝200 張、不看分數。80 張的相簿裡每個查詢都回傳 80 張；真實 AI client 透過 MCP 自己指出「這 80 張大多不是熊」。問 AI 因此改用硬性標籤，又只找到 2 張海邊照片。

**實測**（80 張 COCO、真 SigLIP2 fp16，cosine）：相關照片約 0.07～0.14，中位數接近 0。例如「滑雪」前 5 名 0.095～0.137 後掉到 0.054，且恰好是標了滑雪的 5 張。

**選項**：(A) 維持；(B) 每個 embedding 空間一個絕對下限；(C) 依查詢分布的相對下限；(D) 下限只影響計數。

**決定**：(B)，加上不計入的 `related` 清單。
- 向量命中＝cosine ≥ 該空間下限，且在 top-K 內。`total`、facet、selection 都是同一個集合 M，所以 ADR-0005 保護的一致性不變。
- `related`：最多 24 張低於下限的鄰近照片，只在第一頁，不計數、不進 facet、不進全選；目前只在沒有結果時顯示。
- `total_capped`：向量路徑達到 K 時為 true，`total` 是下限值。
- 下限在模型目錄（`embedding.min_cosine`），查詢時套用，不需重新推論（不變量 7）；未設定的空間行為與之前相同。

**修訂（2026-11-10，使用者決定）**：「可能相關」清單停用。驗證第 2 輪指出「夜景」在沒有夜景照片時列出 24 張不相關的照片；使用者選擇「直接說沒有」。`related` 欄位保留但一律為空，SRCH-SEM-008 依此更新。

**(C) 不採用**：成員會隨相簿與篩選改變，每個查詢都會有自己的「最佳」照片，無法表達「沒有」。**(D) 不採用**：讓 total 與 selection 分家，正是 ADR-0005 要避免的。

**風險**：0.07 是**暫定值**，只有 80 張的證據。10 萬張時仍可能達到 K；「長頸鹿」這種真的沒有的查詢，最高分 0.06 接近下限。要有 ≥ 5,000 張與 50 個中英查詢的標註集才能定案；如果沒有單一下限能分開，退回 ADR-0005 的集合並把純語意的 total 標為未知；中英落差超過分隔幅度時再加語言偏移。caption 向量路徑尚未設下限。

## ADR-0028：ETA 的 δ 隨剩餘時間變化，並加上速率估計誤差

- 日期：2026-11-06｜狀態：已採納｜審議：1 個獨立 sub-agent（題目、06 §5.5.5–5.5.6、ETA-016、eta.py）

**背景**：06 §5.5.5 的 δ 固定 0.1，區間相對寬度最小 2×1.28×0.1 = 0.256；§5.5.6 的 `precise` 要求寬度 ≤ 0.2，所以永遠到不了。把 ETA 接進實際路徑時才發現，之前 ETA 引擎沒有呼叫者。

**選項**：(A) δ 隨剩餘時間變化；(B) 降低固定 δ；(C) 放寬 precise 門檻；(D) 維持現狀。

**決定**：改良的 (A)：`σ_rel = cv·√(1/N_rem + 1/N_eff) + δ(T)`，`δ(T) = clamp(0.1·√(T/3600), 0.04, 0.1)`。門檻 0.2 與 p50 < 10 分鐘不動。

**依據（模擬，不是實測）**：ETA-016 情境的涵蓋率由 99.6%（過寬）變成 89.3%；precise 時間占比由 0% 變成 63%，其間涵蓋率 89%。純 (A) 在短工作只有 56%～65%，因為短工作的誤差來自速率估計，所以要加 `1/N_eff`。(B) 固定 0.04 在長工作隨機漂移時只有 53%。(C) 違背「剩 2:41」的精度。(D) 留著永遠到不了的狀態。

**風險**：真實漂移未知；模擬中漂移 +30% 或中途變慢 1.3 倍時低於 70%，要靠 M0-V7 驗證，不通過就依 V7 的處置調整 δ。150 張的短工作約 66%。工作快結束時 precise 可能退回 rough。

## ADR-0029：匯入與整理分開

- 日期：2026-11-07｜狀態：已採納｜決定者：使用者（要求依開源工具研究重做介面與流程）

**背景**：使用者第一次實際使用時看不出進度，也看不出分析結果。原因有三：照片只有建立整理工作時才會處理；選「依日期」時完全不跑 AI，卻沒有說明；進度只在「活動」抽屜裡。選資料夾時也只看得到資料夾名稱。研究見 `docs/research/photo-apps-ux-2026-11.md`。

**決定**：
- 新增「匯入」：左邊資料夾樹，右邊顯示張數、大小、抽樣日期、略過的檔案與 12 張縮圖樣本。預覽由 Worker 產生，路徑走 SafePath，不寫任何檔案（不變量 1、2）。
- 加入資料夾即 `POST /sources/{id}/analyze`：`kind=label` 的工作，只產生標籤，不產生計畫，不動檔案（不變量 3、4）。模型未就緒時顯示原因，可改成只掃描。
- 掃描結果每 100 個檔案送出一次，圖庫邊掃邊出現。解碼改為多執行緒，送出順序不變，SCAN-019 的 cursor 語意不變。
- Worker 新增側邊執行緒處理 `browse`、`folder_preview`、`folder_thumb`，掃描中仍能瀏覽資料夾。`results-only` 模式拒絕後兩者。
- 全域進度卡：每一頁都看得到分階段數字與剩餘時間，完成後提示下一步。
- 側欄分成圖庫、工作、系統三組。
- 預設主題改為深色（04 §4.2 本來就以深色為預設，但實作跟隨系統）。

**不做的選項**：把 AI 分析放進 `organize` 流程繼續綁定——這正是使用者卡住的原因。自動把所有 root 都掃描——使用者要先看到內容再決定。

**影響**：視覺基準要由 `visual-baselines` workflow 在釘選映像重新產生（ADR-0019）。第 2、3 批（資料夾檢視、分類瀏覽、篩選、狀態列、整理預覽、批次待確認）另行進行。

## ADR-0030：VLM 看全部類別，信心改用 token 機率

- 日期：2026-11-07｜狀態：已採納｜審議：1 個獨立 sub-agent｜修訂 ADR-0001、03 §3.3

**背景**：使用者實測後覺得分類很差。135 張真實照片裡 30 張分到「其他」，64 張進待確認，但同一次呼叫產生的說明文字是對的。查出四個原因：(1) VLM 只能從 SigLIP 的前 3 類加 other 裡選，而 SigLIP 的分數接近均勻（top-1 約 0.10～0.25），香蕉的前三名是寵物、旅行、建築；(2) prompt 只有類別代號，03 §3.3.2 的類別說明沒有實作；(3) 信心是模型自評，135 張全部回答 high，對應成固定的 0.9，`normalize_logprobs` 沒有呼叫者；(4) 衝突信心最高 0.8×0.9=0.72，低於 accept 0.80，所以只要兩段不同就一定進待確認。

**量測（同一模型、同一批照片、production prompt 與 schema，每次只改一項；沒有標準答案，這是觀察不是準確率）**：

| 變體 | other | events | people | food |
|---|---:|---:|---:|---:|
| A 前 3 類（原本） | 30 | 11 | 70 | 5 |
| B 全部類別 | 15 | 53 | 29 | 11 |
| C 全部類別 + 說明 | 11 | 14 | 69 | 11 |

C 的 token 機率：95 張 ≥ 0.99，10 張 < 0.8，後者幾乎都是「人物／活動」的模糊照片。剩下的 other 多半是野生動物，因為 12 類只有「寵物」。

**決定**：
- N（不含 other）≤ `vlm_max_candidates`（16，暫定，未量測超過 12 類）時提供全部類別；否則取分數最高 16 類，依分類表順序。保存 `stages.candidates`、`truncated`。截斷時 VLM 回 other 改為 `needs_review`、reason=`shortlist`。
- prompt 帶每類的名稱與說明，不帶 embedding 候選（會誤導 VLM，也破壞共識公式假設的獨立性）。
- 信心改用 `category` 值首 token 的 logprob，對候選正規化；沒有 logprobs 時退回自評，來源記在 `confidence_source`。
- embedding 的 top-1 < `policy.review` 時不算意見，改由 VLM 依門檻判斷；≥ review 時維持原本的衝突規則（CORE-THR-010／019 不變）。
- `prompt_template_ref` 改為 `fused_v2`、`label_set` 改為 `builtin-12@2`，`classes`、`vlm_max_candidates` 納入 config hash（不變量 7）：舊結果不會被當成快取。
- 「寵物」改為「動物」（key 仍為 `pets`）。

**放棄的選項**：維持 top-k（72 張答案在候選外）；加大 k（分數接近均勻，只是延後問題）；把候選當提示文字。

**風險**：沒有標準答案，要用手動標註的 100～200 張驗證；logprob 只量分類 token，模型可能高度確定地答錯，錯誤不會進待確認，靠複製預設、計畫核准與復原兜底；人物、活動、旅行本來就重疊，單一分類無法完全解決，需要自訂類別；embedding 溫度未校準，零樣本時衝突規則幾乎不觸發；類別說明修改後，全部送 VLM 的照片都要重問（Q-025 的行為改變）。

**實作後修正（同日）**：第一次安裝後 156 張全部變成 other，因為 `" architecture"` 這類帶空白的 token 蓋掉了 `"architecture"` 的機率。模型答案是對的，錯在讀機率的程式；單元測試的資料沒有這種 token，真機才抓到（INF-CAS-015 回歸）。`prompt_template_ref` 改為 `fused_v3`，讓這批結果不被當成快取。

**第二次修正（2026-11-09，驗證第 1 輪）**：「其他」裡 75 張小孩的照片，VLM 其實答了 people，但信心都在 0.5 以下。people 與 pets 開頭的 token 都是 `pe`，只讀第一個 token 時兩者各拿到相同的機率，正確的那個永遠過不了一半。改為沿著產生的 token 走到候選分開為止，各類別取離開路徑當下的機率（INF-CAS-017 回歸）。`prompt_template_ref` 改為 `fused_v4`。

## ADR-0031：影像向量路徑的查詢文字：中文經詞彙表轉英文，對不到就不查

- 日期：2026-11-09｜狀態：已採納，**修訂 ADR-0027 的「語言偏移」一句**，下限 0.07 不變｜審議：1 個獨立 sub-agent（題目、ADR-0005／0014／0027、search.py、tokenize.py、詞彙表、實測分布）

**背景**：驗證第 1 輪，「貓」回傳 29 張，其中 27 張是教室裡的小孩；「海邊」列出操場照片；「長頸鹿」（相簿裡沒有）也有結果。

**實測**（參考機 645 張真實照片；照片說明經抽查 46/48 正確，用作弱標註；原樣查詢、不加模板）：
- 英文分得開：cat 相關 0.107／不相關 p99 0.051；elephant 0.122／0.044；beach 只有 1 張 ≥ 0.07，而且是對的；giraffe、night 為 0。
- 中文分不開：貓的相關中位數 0.070，比不相關 p99 0.076 還低；海邊相關中位數 0.032，不相關 p99 0.080；長頸鹿有 19 張 ≥ 0.07。
- 加 "This is a photo of {q}." 模板更糟：0.07 時不相關照片從 6.8% 變成 55%。

**選項**：(A) 中文詞經詞彙表換成英文名；(B) 中文另設較高的下限；(C) 中文查詢不走影像向量，改靠全文、標籤與 caption 向量；(D) 用本機 LLM 翻譯；(E) 組合。

**決定**：(A) 的窄版加 (C)。
- 只送詞彙表 `en` 名稱，不送同義詞（同義詞混了其他文字，例如 ねこ）。同義詞只用來對照，例如「海邊」→ scene.beach → "beach"。
- 部分對得到時只送對到的詞（「小孩在玩水」→ "child"）；中文詞會拉高所有照片的分數，混進去會破壞對到的部分。標籤路徑本來就會加入 people.child 的照片，所以結果集合不會因此變大。
- 完全對不到時不查影像向量，也不算降級（「長頸鹿」可以真的回答「沒有」）；由全文、bigram 與 caption 向量回答。
- 拉丁字母查詢不變。

**不採用**：(B) 兩個分布重疊，不是平移，任何下限不是丟掉真的照片就是留下雜訊。(D) 搜尋只有約 2 秒，CPU 機器上做不到；之後如果要加，在背景做並依查詢快取。

**風險與後續**：涵蓋率取決於詞彙表（目前 22 個詞，不含大象、長頸鹿），要依相簿常見的 VLM 標籤擴充（匯入字典前先確認授權）。caption 向量路徑目前沒有下限，啟用前要依同樣方法校準（ADR-0027 的風險一節）。之後要在 ≥ 5,000 張、50 個中英查詢的標註集上重新量測。

## ADR-0032：多個概念的查詢，每個概念都要符合

- 日期：2026-11-10｜狀態：已採納，**細化 ADR-0005 的結果集合 M**（M 仍是一個條件，total、facet、選取都讀它）｜審議：1 個獨立 sub-agent（題目、ADR-0005／0027／0031、search.py、tokenize.py、ask.py）

**背景**：驗證第 2 輪，「小孩在玩水」「戴口罩的小女孩」「kids playing soccer」「小朋友踢足球」都回傳 558 張，也就是全部小孩的照片；問 AI「小朋友踢足球的照片有幾張？」答 558（約 119 才對）。原因是 M 是各路線的聯集：標籤路線只要有一個 key（people.child）就算，影像向量也會把對到的那半個問題加進來。

**選項**：(A) 概念層級 AND；(B) 標籤路線改 tags_all（只要有一個詞沒有 key 就失效，最後還是會走到 A）；(C) 分「完全符合」與不計數的「部分符合」；(D) 只改排序（問 AI 的數字仍錯，違反 ADR-0005）。

**決定**：(A)，加上可說明的放寬。
- 概念：去掉停用詞與填充詞（照片、拍、幾張、photo…）；同一個詞彙 id 的詞算一個概念，其他每個詞各算一個。
- 只有一個概念：M 不變。
- 兩個以上：照片必須符合每個概念；一個概念可以由全文（含該詞彙的所有名稱）、該 id 的標籤，或（中文、詞彙外的詞）字對 bigram 滿足。影像與 caption 向量只負責排序，不加入成員；**例外**是整個問題都送進了影像模型（每個概念都是英文或都在詞彙表），這時向量命中代表整句，例如 "a dog on the beach"（SRCH-SEM-001）。
- 放寬：詞彙外的概念若讓 M 變空，就略過，回應帶 `relaxed`，畫面說明「沒有照片符合「X」」。詞彙內的概念不放寬，「長頸鹿」仍可以回答沒有。
- 最多取 5 個概念。

**風險**：說明用了別的詞（戲水、水池 vs 玩水）會漏，要靠詞彙表與同義詞擴充；多加一個詞可能讓向量命中退出 M，這是刻意的。之後在 30～50 個多概念查詢上量 precision、recall、total 誤差與放寬頻率，以及 10 萬張的 p95。
