# v1.0.0 發版檢查清單

> 對應 08 §9.2 的 v1.0 列、09 §5、09b §9 與 VERSIONING §4。
>
> 驗證地點：
> - **雲端**：PR CI 與雲端 session 已經是綠的。
> - **CI**：需要 workflow 第一次執行。
> - **實機**：參考機或 VM。
> - **使用者**：帳號、憑證、外部審查或發版。
>
> 測試狀態以 `docs/generated/coverage-status.md` 為準。v0.1～v0.5 各自的清單（`release-v0.1.md` 與各 PR）仍然適用。

## 1. 自動化項目（雲端已綠，CI 會再跑一次）

| 條目 | 測試 ID | 驗證 |
|---|---|---|
| REST `/api/v1` 介面凍結 | FRZ-001 | 雲端：`just contracts`（在 lint 中） |
| Worker 協定（`"MAJOR.MINOR"`，服務 N 與 N-1，ADR-0017） | FRZ-002 | 雲端 |
| MCP tool manifest 1.0.0 | FRZ-003 | 雲端 |
| `pack_api` 與 packs.lock | FRZ-004 | 雲端 |
| registry `schema_version`：v0.1 的檔案可讀 | FRZ-005 | 雲端 |
| preset：v0.1 與 v0.3 匯出的檔案可匯入 | FRZ-006 | 雲端 |
| 相容性比對器 | FRZ-007 | 雲端（property test） |
| 路徑沙箱 fuzz 1,000,000 例、0 逃逸 | SEC-FUZZ-001 | 雲端（PR 跑 10k，本機實跑 1M）＋ CI nightly |
| MCP 紅隊 110 條 | SEC-REDTEAM-001 | 雲端 |
| 人臉刪除可驗證 | SEC-FACE-001 | 雲端 |
| SBOM 與漏洞掃描 | SEC-SBOM-001 | 雲端（本機實跑 0 個阻擋項）＋ CI `release-gate`／`supply-chain` |
| 兩種語言完整、錯誤碼與修復按鈕雙語 | I18N-010、I18N-011 | 雲端 |
| 每個路由都通過 axe、只用鍵盤走完主要流程 | A11Y-010、A11Y-011 | 雲端（e2e） |
| 從 v0.1～v0.5 各版升級資料庫 | MIG-001 | 雲端（以各里程碑真實的 schema） |
| 桌面資料夾 `layout_version` | MIG-002 | 雲端＋實機（Windows、macOS 的權限行為） |
| 破壞性變更都有 `Migration:` 說明 | MIG-003 | 雲端（lint）＋ CI（PR commit 範圍） |
| stable 與 beta 更新通道 | UPD-010 | 雲端（cargo test、manifest 產生器） |
| control plane 的 compose 雙機 E2E | MU-DEPLOY-001 | CI nightly（本環境沒有 Docker daemon，見 B-19） |

## 2. 手動項目

| 條目 | 測試 ID／BLOCKERS | 驗證 |
|---|---|---|
| 外部安全審查（材料見 `docs/security/review-v1.0.md`） | SEC-AUDIT-001、B-20 | 使用者 |
| 真模型、真硬體：M0 實驗、校準、吞吐量、ETA 涵蓋率 | B-1、B-7、B-11、B-13 | 實機 |
| 人臉測試影像授權、真人臉模型 | B-2 | 使用者 |
| Windows 程式碼簽章、SignPath | B-3、B-4 | 使用者 |
| 簽章金鑰（updater、runtime pack） | B-5、B-17 | 使用者 |
| 法規意見（人臉、隱私） | B-6 | 使用者 |
| 帳號：Hugging Face 鏡像、模型釘選、套件管理器上架 | B-8、B-10、B-18 | 使用者 |
| workflow 第一次執行（desktop-build、release-gate、packs、nightly-gpu） | B-12、B-17 | CI |
| 真 AI client 走 MCP stdio 與 OAuth、agent 迴圈紅隊 | B-15、B-16、B-19 | 實機 |
| control plane 實機部署（NAS、反向代理、真 IdP、桌面當 client） | B-19、MU-DEPLOY-002 | 實機 |
| 舊版桌面實際升級到 v1.0（Windows、macOS、Linux 各一台，含 v0.x 資料夾） | MIG-001、MIG-002、UPD-010 | 實機 VM |
| 參考 runner 上的效能門檻（PERF-001 等） | B-13 | 實機 |
| 視覺基準改在釘選的 Playwright 映像產生、人工審閱後提交，再確認 nightly 的 visual 為綠（ADR-0019） | B-21、VIS-001～004、ACT-006 | CI＋使用者 |
| 環境清理（只限雲端 session） | B-14 | — |

## 3. 發版

| 條目 | BLOCKERS | 驗證 |
|---|---|---|
| CHANGELOG：release-please 由 commit 產生；有破壞性變更就要附 `Migration:`（MIG-003），升級說明見 `docs/upgrading-to-1.0.md` | B-9 | 使用者審閱 Release PR |
| 合併 release-please 的 Release PR → 打 tag `v1.0.0` → release-on-tag 與 desktop-build 上傳安裝包和 `latest.json` | B-9、B-20 | 使用者 |
| 發版後：stable 通道收到 1.0.0；beta 使用者不會被降級 | UPD-010 | 實機 |
| 這份清單本身涵蓋所有開放中的 BLOCKERS 與 v1.0 測試 | REL-010 | 雲端（`tools/test_release_checklist.py`） |
