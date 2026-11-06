# v0.1.0 發版檢查清單

> 對應 11 §5（DoD）與 09 §5.4（release gate）。每一列標出驗證地點：
> **雲端**＝PR CI 與雲端 session 已綠；**CI**＝需要 workflow 首次執行（B-12）；**實機**＝參考機或 VM（B-13）；**使用者**＝帳號、簽章或發版（B-3/4/5/9/10）。
> 測試狀態以 `docs/generated/coverage-status.md` 為準；`python3 tools/tc_status.py --gate=v0.1` 為最終門檻。

## 5.1 安裝與啟動

| 條目 | 測試 ID | 驗證 |
|------|---------|------|
| 三平台安裝包 ≤ 200 MB、一般權限安裝 | DESK-030（size gate 已有單元測試）、DESK-031 | CI（`desktop-build.yml`）＋實機 VM |
| Docker 同一份 web build、onboarding 可完成 | DOCK-001／002／003 | CI（`docker.yml`） |
| 啟動畫面 < 300 ms；暖啟動 < 5 s | DESK-001、PERF-007 | 實機 |
| 無登入、無 Org／Workspace；只綁 127.0.0.1；無 token 401、錯 Host／Origin 403 | DESK-011～017、DESK-032、DESK-035、DESK-015（區網強制登入） | 雲端 |
| kill -9 任一進程自動恢復、任務續跑 | DESK-002～006（restart 規則與孤兒防護：cargo test）、OUT-CRASH-* | 雲端（邏輯）＋實機（整機） |
| 單一實例、系統匣、OS 通知 | DESK-007／008（資料目錄鎖：cargo test）、DESK-020～022 | 雲端（鎖）＋實機 |
| 自動更新保留資料、拒絕竄改包 | UPD-001～006（UPD-003/004/005 cargo test；UPD-006 manifest 單元測試） | 實機 VM ＋ 使用者（B-5 簽章金鑰） |
| 每日備份、migration 前備份、立即備份／還原 | PG-004／005／006／008、PG-009 | 雲端 |

## 5.2 環境醫生、模型、時間

| 條目 | 測試 ID | 驗證 |
|------|---------|------|
| 四台參考機的醫生報告 | DOC-001～019（合成 profile）、DOC-020 | 雲端＋實機 |
| 單一推薦組合、修復 CTA、`opa/no-bare-disabled` | MDL-*、PF-*、PF-080 | 雲端 |
| 下載暫停／續傳／取消 ≤ 2 s、sha256、磁碟預檢；背景下載不擋其他工作 | DL-001～023、DL-019（nightly） | 雲端＋CI nightly |
| 所有 variant 已釘選 | INF-REG-013 | 使用者（B-10）＋release gate |
| Operation、ETA、重啟後仍可見；ETA 80% 涵蓋率 ≥ 70% | OP-*、ETA-001～018、ETA-019 | 雲端＋實機 |

## 5.3 自動分類到資料夾

| 條目 | 測試 ID | 驗證 |
|------|---------|------|
| 三種整理方式、預設複製、move 二次確認 | ORG-*、OUT-EXE-012、E2E-B-* | 雲端 |
| Path template golden、traversal fuzz | CORE-TPL-*、FS-024／025 | 雲端（fuzz 1M 為 nightly） |
| cascade、待確認、JSON 成功率 ≥ 99% | INF-CAS-*、REV-*、INF-CONF-009 | 雲端（FakeBackend）＋實機（真模型） |
| 崩潰注入 0 遺失、undo 一致 | OUT-CRASH-001～012、OUT-UNDO-* | 雲端（012 為 nightly） |
| 全庫預設設定原檔不變 | ORG-005（nightly，10,000 張） | CI nightly |
| sidecar、搬移後 0 重新推論、離線來源 | ORG-006、SCAN-006、SCAN-019／020 | 雲端 |

## 5.4 Viewer 與搜尋

| 條目 | 測試 ID | 驗證 |
|------|---------|------|
| 10 萬張 60 fps、首屏 < 1 s、無空白格 | PERF-001、PERF-002、VIEW-002、VIEW-003、VIEW-010（`just perf`） | 雲端記錄＋PERF-001 以參考規格 runner 為準 |
| bundle 預算 | PERF-003（ADR-0006） | 雲端 |
| scrubber、Lightbox、就地修正、選取與批次 | VIEW-*、E2E-L-*、E2E-SEL-* | 雲端 |
| 桌面 asset protocol、scope 外失敗 | DESK-036（傳輸與解析）、DESK-019 | 雲端（解析）＋實機（scope） |
| 搜尋與索引進度；p95 < 300 ms | SRCH-*、PERF-006 | 雲端 |
| axe 0 serious／critical；i18n 完整 | A11Y-001～006、I18N-* | 雲端 |

## 5.5 品質與發佈

- [ ] `just lint`、`just test`、`just test-int`、`just e2e` 全綠（雲端）。
- [ ] `release-gate.yml`：`tc_status --gate=v0.1` OK（CI，B-12）。
- [ ] 四台參考機的效能紀錄（張／秒、VRAM、ETA 涵蓋率）附在 release notes（實機，B-1／B-13）。
- [ ] Release 附 sha256、SBOM；Windows 未簽章時文件說明 SmartScreen（使用者，B-4）。
- [ ] 合併 release-please 的 Release PR → `desktop-build.yml` 產生安裝包與 `latest.json`（使用者，B-9）。

## 實機步驟（B-13）

1. 乾淨 VM（Win 11 標準帳號、macOS 14、Ubuntu 22.04）安裝 `desktop-build.yml` 的產物，記錄啟動畫面時間（DESK-001）與暖啟動時間（PERF-007）。
2. 執行中以工作管理員／`kill -9` 分別結束 postgres、api、worker：5 s 內偵測，UI 顯示「重新連線中」，Job 續跑（DESK-002～004）；結束殼本身，5 s 內所有子進程消失（DESK-006）。
3. 第二次開啟 app → 只帶出既有視窗（DESK-007）；同一資料夾開兩種安裝 → 第二個提示並結束（DESK-008）。
4. 拖放資料夾、系統匣暫停／繼續、關窗行為、OS 通知與 deep link、拔電源（DESK-018、020～024）。
5. 從 `v0.1.0-rc.N` 更新到 rc.N+1；竄改更新包；有任務時只提示（UPD-001～003）。
6. Windows Defender 開啟下連續 50 次啟動／關閉；以系統管理員執行（PG-010、PG-011）。

## 參考機實測紀錄（2026-11-05～06）

機器：Windows 11 Pro、Ryzen 9 9900X（24 threads）、64 GB、RTX 5070 Ti 16 GB（驅動 591.86）＋ AMD 內顯。真安裝包、真模型，以 CDP 操作 WebView2。**資料為實測值。**

| 項目 | 結果 |
|---|---|
| NSIS 安裝包 | 131.2 MB，靜默安裝，一般使用者權限，裝到 `%LOCALAPPDATA%\Programs` |
| 首次啟動（DESK-034 流程） | 13 s 到 API 就緒；onboarding、原生資料夾對話框、掃描、整理、復原、搜尋、關閉、再開皆通過 |
| 暖啟動（PERF-007，門檻 5 s） | 1.66／1.89 s；前次被強殺後 3.55 s |
| 結束（關視窗） | 1.1～1.3 s，PG 乾淨關機，沒有殘留進程 |
| 強殺 worker／api／postgres（DESK-002～004） | 1.5／1.7／0.6 s 內重啟，API 在 3 s 內恢復，資料不變 |
| 強殺殼（DESK-006） | 13 個子進程 1.3 s 內全部消失 |
| 第二次開啟（DESK-007） | 第二個進程結束，只有一個殼 |
| 真實下載 | SigLIP2 fp16、Qwen3-VL Q4_K_M，sha256 驗證；中斷後續傳成功 |
| 依內容整理 80 張 | 83 s（含模型載入），內建 Vulkan；原檔 hash 與 mtime 不變；復原後輸出 0 檔 |
| 語意搜尋 | 繁中與英文查詢排序正確，約 0.4 s |
| 人臉 | 80 張偵測 136 張臉，HDBSCAN 2 群；同一人（鏡像）0.61～0.89，不同人 ≤ 0.22；真實身分準確度仍待 B-2 |
| 問 AI | 計數與找照片可用；本地模式是確定性模板，未走 tool loop |
| MCP（B-15，Claude Code） | 精靈的設定可啟動，權杖在 Windows 認證管理員，中文搜尋成功；Claude Desktop、Cursor 未測 |
| 效能（`just perf`） | PERF-001 60 fps（1798 幀／30 s，掉幀 0.00%）、VIEW-002／003、PERF-003 通過 |


### 2026-11-06 補測（參考機，CI 建置 `81b0b8a`，真模型）

- 向量下限（ADR-0027）：「滑雪」5 張、「長頸鹿」0 張、「一隻熊」2 張。之前每個查詢都是 80 張。
- ETA：掃描與 80 張標註工作都寫入 `eta_calibration`，第一次顯示的區間涵蓋實際時間，但樣本太少，**不算 M0-V7**（V7 要 3 種任務 × 3 次、各 1,000 張）。Activity Center 顯示「估算中…」→「不到 1 分鐘」；完成後不再顯示。掃描有即時計數。
- 模型已在本機時，`search.reindex` 會結束；未知的 preset 回 404，不建立工作。
- `opagent ops export-calibration`：`81b0b8a` 的安裝版失敗（找不到 PostgreSQL 執行檔），`960f354` 已修，在該版安裝包實跑成功。
