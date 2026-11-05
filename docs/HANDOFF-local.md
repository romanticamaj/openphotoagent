# 地端接手（Handoff）：從雲端 session 交給參考機

> **2026-11-05 地端 session 中途更新（因用量上限暫停）：下面 §1「里程碑全部完成」並不準確，請先讀本節。**
>
> **實機發現的缺口（雲端驗證不到）：**
> 1. **安裝版從未真的啟動過**：已修一批（commit `fix(desktop): the installed app starts on Windows`），但到暫停為止**還沒看到安裝版完整開起來**。最後一個錯誤是 `\\?\` 路徑讓 initdb 失敗，修正（DESK-049）已 commit，尚未重新實測。
> 2. **生產路徑沒有接真推論**：`opagent worker --backend` 只有 `fake`／`none`，殼傳 `none`；`LlamaCppServerAdapter`、`OrtEmbedAdapter` 沒有被 worker 建立。人臉只有 `FakeFaceBackend`，沒有真 adapter。**這是最大的未完成項。**
> 3. **安裝包沒有 `llama-server`**：08 要求內建 CPU 版，`tauri.conf.json` 只打包 python 與 pg。.deb 只剩約 3 MB 餘裕，要先縮減。
> 4. **模型目錄**：`pins.lock` 已在本機釘選 7 個 variant（B-10 實質部分）。仍有問題：`openphotoagent/siglip2-...-onnx` repo 不存在（要自行上傳或換來源）、`deepinsight/buffalo_l` 404、Qwen3-VL Q5_K_M 官方 repo 沒有此檔。
> 5. **`feat/v1.0` 在 c3ac250 的 CI 其實是紅的**（SRCH-IMG-002、DESK-042，連帶 tc-status）；已修，待 push 後 CI 確認。
>
> **這台機器：** D: 是 HDD，**不要**照 §2 把 TEMP 放 D:（initdb 超過 3 分鐘，C: NVMe 只要 7 秒）。環境見 `D:\tools\opa-env.sh`：PG 17.6＋pgvector（`D:\tools\pg17`）、`TEMP=C:\t`、`CARGO_TARGET_DIR=C:\t\cargo-target`。帳號非管理員且未開開發者模式，5 條 symlink 測試因 WinError 1314 無法在此跑（CI Windows 可以，或開啟開發者模式）。Vitest 需 `--pool=threads`。
>
> **平行工作的分支（尚未合併）：** `worktree-agent-a7278ca19d4aa9e0c`（Windows gate 修正）、`worktree-agent-a130b03183d039e03`（M0 實機結果）。合併前先看各分支的 commit；結果以實際內容為準。llama-server 與模型的路徑見 `D:\t\agentB\READY.md`。
>
> **建議順序：** 合併兩個分支 → 重新 build 並實測安裝版（`tools/build_desktop_python.py` 要重跑，別只替換 exe）→ 加入 CI 實際啟動安裝版的煙霧測試 → 接真推論與 llama-server → 其餘 §4。

> 寫於 2026-11-04，雲端 session 結束前。對象是你本人，以及在參考機（Windows 11、RTX 5070 Ti 16 GB）上接手的 Claude Code session。
> 接手的 session 請依序讀：`CLAUDE.md`（特別是 §4 不變量、§5 TDD、§8 進度紀錄）→ 本檔 → `docs/BLOCKERS.md` → `docs/research/m0-results.md` §3。

## 1. 現況

- **程式**：里程碑 0～6（M0、v0.1-A～D、v0.2-A～C、v0.3、v0.4、v0.5、v1.0）全部完成。每個里程碑都有一個疊加 PR（#1～#12），base 是前一個里程碑的分支。
- **接手的分支**：`feat/v1.0`（PR #12），包含前面所有里程碑的內容。
- **雲端能驗證的都已綠燈**：
  - 本機四個 gate（`just lint`、`just test`、`just test-int`、`just e2e`）。
  - PR CI 的所有 job：Windows／macOS／Ubuntu 測試矩陣、mutation、audit、tc-status 一致性檢查。
  - 三平台安裝包建置與 200 MB 門檻。
  - 最終 commit 的結果記在 PR #12 描述的 Gate 一節。
- **還沒驗證的**：凡是需要真 GPU、真模型、真 Windows／macOS 桌面、真 AI client、參考機效能的項目，雲端都做不到。這些是地端的主要工作（§4）。

### 1.1 最後一段雲端工作修掉的安裝包問題（地端實測時特別留意）

| 問題 | 修正 | 測試 | ADR |
|---|---|---|---|
| 隨附的 Python 只是指向建置機的 venv，沒有直譯器與標準函式庫 | 改為複製整個 python-build-standalone | DESK-039 | 0022 |
| AppImage 塞了系統的 WebKitGTK，超過 200 MB | 依 08 §4.1 改用系統 WebKitGTK；缺少時 AppRun 顯示 `DESK_WEBVIEW_MISSING` | DESK-037／038 | 0022 |
| app 自己的套件以 editable `.pth` 安裝；registry、整理方式、標籤、詞彙從 repo 讀取 | 真正的 wheel；資料移進各自的套件 | DESK-039～041 | 0023 |
| CI 的 runtime 建置把 venv 背後的 `/usr` 當成 Python 來源複製 | 只從 `uv python dir` 取 | DESK-042 | 0023 |

**這些都只在 Linux 容器與 CI 上驗證過。實際安裝到 Windows 並啟動，是地端第一件要確認的事（§3）。**

## 2. 參考機環境準備（Windows 11）

1. **取得程式碼**：`git fetch origin feat/v1.0 && git checkout feat/v1.0`
2. **工具鏈**：
   - uv：`winget install astral-sh.uv`
   - Node 22 與 pnpm：`corepack enable`
   - Rust（MSVC toolchain）、Visual Studio Build Tools（C++）
   - just：`uv tool install rust-just`
3. **PostgreSQL 17 與 pgvector**：
   - 安裝官方 PostgreSQL 17。
   - 在「x64 Native Tools Command Prompt」中編譯 pgvector v0.8.0：`nmake /F Makefile.win` 與 `nmake /F Makefile.win install`。
   - 設定 `OPAGENT_PG_BIN=C:\Program Files\PostgreSQL\17\bin`。
   - 這和 CI 的 Windows job 相同（`.github/workflows/ci.yml` 的 os-matrix）。
4. **測試暫存目錄**：
   - 把 `TEMP`、`TMP` 指到非系統碟（例如 `D:\t`），並把該目錄與 repo 加入 Defender 排除。
   - 原因：C: 的 `%TEMP%` 加上即時掃描，會讓 PG-013 的 template clone 超過 200 ms 預算（CI 已實測）。
5. **安裝相依**：`just setup`

### 2.1 先跑一次 gate（確認地端環境與雲端一致）

```powershell
just lint
just test
just test-int     # 需要 OPAGENT_PG_BIN；整合測試有 15 分鐘的逐測試逾時
just e2e          # chromium + visual 兩個 project
```

- **visual 可能出現約 1% 的差異**：視覺基準目前是在 Linux 容器算繪的。依 ADR-0019，正式基準只在釘選的 Playwright 映像產生（B-21）；在那之前，Windows 上的 visual 差異不代表回歸。
- **其他測試失敗**：依 CLAUDE.md §5.9 處理，先寫回歸測試再修。

## 3. 第一件事：安裝包實測

1. **下載安裝包**：
   - 從 PR #12 最新一次 `desktop-build` run 的 artifact 下載 `desktop-x86_64-pc-windows-msvc`（NSIS 安裝檔，約 100 MB）。
   - Mac 用 `desktop-aarch64-apple-darwin` 的 dmg。
2. **安裝前**：確認機器上沒有開發用的 repo 路徑會被誤用。安裝包不應依賴 repo，這正是 DESK-039～041 要防的事。
3. **安裝並逐項確認**，結果記在 `docs/checklists/release-v0.1.md`：
   - 安裝 → 啟動 → onboarding（DESK-034 的煙霧流程）
   - 依日期整理 → undo
   - 搜尋
   - 關閉
   - 再開一次（嵌入式 PG 重啟）
4. **檢查 log**（`%LOCALAPPDATA%\OpenPhotoAgent\logs`）：沒有 `ModuleNotFoundError`、registry 不是空的、整理方式清單有 `by-date` 與 `by-content-12`。
5. **預期中的現象**：安裝包未簽章（B-4），SmartScreen 會警告，這是預期的。

## 4. 地端要做的驗證（對照 BLOCKERS）

| 項目 | 內容 | 步驟／腳本 |
|---|---|---|
| B-1 | M0 實機實驗：S1a、S1c、S3、S3b、S4、S4b、S7、V7、V9、T1c、T2 | `docs/research/m0-results.md` §3；腳本在 `spikes/m0/<實驗>/`；結果寫回 `spikes/m0/results/*.json`。**照實記錄，不要捏造或「修飾」數字** |
| B-11 | v0.1-C 介面在參考機走一遍：歡迎精靈的硬體結論、推薦組合、下載、第一次整理 | `docs/checklists/release-v0.1.md` |
| B-13 | 桌面 D／M 類：DESK-001（啟動畫面 < 300 ms）、DESK-002～005（kill -9 各進程）、DESK-007 等 | 09a 各 DESK 列的「步驟」欄 |
| PERF-001 | 10 萬張 60 fps：雲端 p95 為 16.8 ms，門檻 16.7 ms 以參考機為準 | `just perf` |
| B-15 | 真 AI client 走一遍 MCP：Claude Desktop、Claude Code、Cursor | 「連接 AI 助手」精靈 → `opagent mcp` |
| B-16 | MCP-INJ 的 agent 迴圈（真本地 LLM） | nightly 腳本，見 BLOCKERS |
| B-19 | v0.5 control plane 實機部署（NAS + HTTPS reverse proxy、真 IdP） | `docs/deploy/controlplane.md` |
| B-7 | AuraFace 授權檔與 sha（雲端擋了 Hugging Face） | 下載後更新 registry pin |

每完成一項，在 `CLAUDE.md` §8 進度紀錄追加一行，把 `docs/BLOCKERS.md` 對應項目標成已完成並附上證據，然後執行 `just tc-status`、commit、push。

## 5. 只有你本人能做的事

- **B-5（擋住發版）**：`TAURI_SIGNING_PRIVATE_KEY` secret 格式不正確，請用 `cargo tauri signer generate` 重新產生並設定。
- **B-21**：在 `feat/v1.0` 手動觸發 `visual-baselines.yml` → 下載 artifact，確認差異只是字型算繪 → 提交新的 `*-snapshots/` → 觸發一次 `nightly.yml`。
- **帳號與費用**：B-3（Apple Developer）、B-4（SignPath）、B-8（名稱與網域）、B-10（registry-pin 的 GitHub App token）、B-17（runtime pack 建置與簽章）、B-18（winget／Homebrew／Flathub 上架）。
- **法律與資料**：B-2（人臉測試影像授權）、B-6（人臉模型授權與法規）。
- **發版**：B-9、B-20（外部安全審查、v1.0 發版）。
- **合併**：PR 依 #1 → #12 的順序合併（squash；PR 標題就是 Conventional Commit）。

## 6. 已知問題與風險（未解決，接手時留意）

1. **.deb 只剩約 3 MB 餘裕**（197.0 MB／200 MB）：下一次新增相依前，先做 ADR-0022 列的縮減（ORT 未使用的 provider、HDBSCAN 改寫）。
2. **jieba 字典快取寫在系統暫存目錄**：多人共用的 Linux 上宜改到 app 的快取目錄（ADR-0023 後續）。
3. **MU-WORKER-004 曾在本機 test-int 出現一次 segfault**（websockets C 擴充的 `serialize`）：之後連續多次完整執行都沒有重現。若地端再遇到，保留 faulthandler 輸出，並先寫重現測試。
4. **websockets 的 DeprecationWarning**：`apps/worker/src/opagent_worker/remote.py:306`，`connect()` 未以 context manager 使用。目前無害，升級 websockets 前要處理。
5. **AppImage 依賴系統的 WebKitGTK 4.1**：Ubuntu 22.04 以上、Fedora、Arch 都有，但沒有實機驗證。缺少時應顯示 `DESK_WEBVIEW_MISSING` 與安裝指令（DESK-038）。

## 7. 給接手的 Claude Code session

- 規則不變：
  - 嚴格 TDD（先紅再綠）。
  - 不得刪除、跳過或弱化測試。
  - §4 不變量違反風險要停下來回報。
  - 規格衝突與設計取捨開審議 sub-agent，結論記成 ADR。
  - 只有使用者本人能決定的事記到 BLOCKERS。
- 真機實驗的數據**只能來自實際執行**。跑不出來就寫明原因，不要估算填空。
- 第一步：跑 §2.1 的四個 gate，把結果與機器規格記到 `CLAUDE.md` §8，再開始 §3。
