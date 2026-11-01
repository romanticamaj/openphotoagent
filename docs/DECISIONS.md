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
