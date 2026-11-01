# DECISIONS — 架構決策紀錄（ADR）

> 依 CLAUDE.md §5.8：規格衝突、做不到的門檻、設計取捨由獨立的審議 sub-agent 論證後決定；每一筆以 ADR 格式記錄（背景、選項、決定、理由、影響的測試 ID），並同步修改相關規格。結論不得用來修改測試去配合實作，也不得弱化 §4 不變量。
> 需要使用者本人決定的事項在 [`BLOCKERS.md`](BLOCKERS.md)。


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
