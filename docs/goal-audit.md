# GOALS.md 完成條件逐條稽核（M0 → v1.0）

> 對照 [`docs/GOALS.md`](GOALS.md) 里程碑 0～6 的「完成條件」，逐條附上證據。日期：2026-11-03；分支：`feat/v1.0`。
> 證據的來源：
> - 測試狀態：[`docs/generated/coverage-status.md`](generated/coverage-status.md)（`just tc-status`）。
> - 未綠的 ID：每一條都列在 [`BLOCKERS.md` 附錄](BLOCKERS.md#附錄還沒有綠燈的測試-id-與對應項目)，並對到負責的 B-x。
> - 決定：[`DECISIONS.md` 審議索引](DECISIONS.md)。
>
> 圖例：✅ 達成｜⏳ 依 GOALS 的例外（pending-hardware、人工驗證、發版）延後，已列在 BLOCKERS 與 PR。

## 疊加 PR

| 里程碑 | 分支 | PR | base |
|---|---|---|---|
| 0 M0-cloud | `spike/m0` | [#1](https://github.com/romanticamaj/openphotoagent/pull/1) | `claude/spec-expansion` |
| 1A | `feat/v0.1-a` | [#2](https://github.com/romanticamaj/openphotoagent/pull/2) | `spike/m0` |
| 1B | `feat/v0.1-b` | [#3](https://github.com/romanticamaj/openphotoagent/pull/3) | `feat/v0.1-a` |
| 1C | `feat/v0.1-c` | [#4](https://github.com/romanticamaj/openphotoagent/pull/4) | `feat/v0.1-b` |
| 1D | `feat/v0.1-d` | [#5](https://github.com/romanticamaj/openphotoagent/pull/5) | `feat/v0.1-c` |
| 2A | `feat/v0.2-a` | [#6](https://github.com/romanticamaj/openphotoagent/pull/6) | `feat/v0.1-d` |
| 2B | `feat/v0.2-b` | [#7](https://github.com/romanticamaj/openphotoagent/pull/7) | `feat/v0.2-a` |
| 2C | `feat/v0.2-c` | [#8](https://github.com/romanticamaj/openphotoagent/pull/8) | `feat/v0.2-b` |
| 3 | `feat/v0.3` | [#9](https://github.com/romanticamaj/openphotoagent/pull/9) | `feat/v0.2-c` |
| 4 | `feat/v0.4` | [#10](https://github.com/romanticamaj/openphotoagent/pull/10) | `feat/v0.3` |
| 5 | `feat/v0.5` | [#11](https://github.com/romanticamaj/openphotoagent/pull/11) | `feat/v0.4` |
| 6 | `feat/v1.0` | [#12](https://github.com/romanticamaj/openphotoagent/pull/12) | `feat/v0.5` |

## 0 · M0-cloud

| 完成條件 | 狀態 | 證據 |
|---|---|---|
| W-1～W-6 已回寫 | ✅ | W-1：06 §4.7 #63～#68；W-2：02 §3.2 `{date:YYYY}`、`{category_key}`、`trash`；W-3：05 §3.2.3；W-4：08 C-27；W-5：11 §1 → coverage-status；W-6：CONTRIBUTING 與 `.github/pull_request_template.md` |
| 每個 M0 實驗都有腳本與結果檔 | ✅ | `spikes/m0/<id>/` 與 `spikes/m0/results/` 共 17 個檔：S1a、S1b、S1c、S2、S3、S3b、S4、S4-pipeline、S4b、S5、S6、S7、T1、T2、V7、V8、V9 |
| 實測或 pending-hardware，不捏造 | ✅／⏳ | `measured`：V8；`partial`（雲端 Linux 能跑的部分已實測）：S1a、S1b、S2、S4-pipeline、S5、T1、T2；`blocked`：S6（需要 Apple 帳號與憑證，B-3）；`pending-hardware`：S1c、S3、S3b、S4、S4b、S7、V7、V9 → B-1 |
| `m0-results.md` 完整 | ✅ | `docs/research/m0-results.md`：結論表、影響、Windows 5070 Ti 與 Mac 的逐步指令 |
| 開 PR 並列出需要硬體的實驗 | ✅ | PR #1 |

## 1A · 測試基建＋walking skeleton

| 完成條件 | 狀態 | 證據 |
|---|---|---|
| just lint／test／test-int／e2e 全綠 | ✅ | 該分支 PR 時全綠；最終分支見本文末「最終 gate」 |
| E2E-B-001 通過 | ✅ | `apps/web/e2e/journeys/organize-by-date.spec.ts` |
| 原檔內容與 mtime 不變，undo 後與執行前一致 | ✅ | E2E-B-001 比對樹的雜湊與 mtime；ORG-001、OUT-UNDO-001 |
| coverage-status 與 §8 已更新，開 PR | ✅ | CLAUDE.md §8 切片 0、1；PR #2 |

## 1B · 核心模組（切片 2～6）

| 完成條件 | 狀態 | 證據 |
|---|---|---|
| 切片 2～6 的 v0.1 ID 全部通過 | ✅／⏳ | CORE-*、FS-*、OUT-*、SCAN-*、Q-*、OP-*：全綠。例外：OUT-EXE-010 由 CI OS 矩陣執行；ETA-019 需參考機 |
| core／fs-sandbox branch ≥ 95% | ✅ | core 99%、fs-sandbox 98%（CLAUDE.md §8 切片 2、4） |
| path_template、fs-sandbox mutation ≥ 85% | ✅ | path_template 96.1%；fs-sandbox 94.0%（`docs/mutation-equivalents.md`） |
| OUT-CRASH 全綠（0 檔案遺失） | ✅ | OUT-CRASH-001～012；012（10,000 actions × 50 次 kill -9）已在本機實際跑過 |
| gate 全綠，更新紀錄，開 PR | ✅ | PR #3 |

## 1C · 推論、模型管理、搜尋、viewer（切片 7～10）

| 完成條件 | 狀態 | 證據 |
|---|---|---|
| 不需 GPU 的切片 7～10 v0.1 ID 全部通過 | ✅／⏳ | INF-*、DL-*、MDL-*、DOC-*、PF-*、PACK-*、SRCH-*、VIEW-*、E2E-*。例外：INF-CAL-003、DOC-020 需真機。DL-019（nightly）在 2026-11-03 於本機以目前的程式重跑通過：單獨 47 張/秒、下載中 45 張/秒（−4%，門檻 −10%） |
| 旅程 A、B 的 E2E 全綠 | ✅ | E2E-A-001～005、E2E-B-001～010 |
| axe 0 serious／critical | ✅ | A11Y-001～006；v1.0 擴大到每個路由（A11Y-010） |
| zh-TW、en 沒有缺漏的 key | ✅ | I18N-001～005；v1.0 加上 I18N-010 雙語完整性檢查 |
| gate 全綠，更新紀錄，PR 列出使用者驗證項目 | ✅ | PR #4；B-11 |

## 1D · 桌面殼、打包、release gate（切片 11～12）

| 完成條件 | 狀態 | 證據 |
|---|---|---|
| 能在雲端或 CI 驗證的 v0.1 ID 全部通過 | ✅／⏳ | 雲端能驗證的部分全綠。D／M 類（DESK-*、UPD-001／002、PG-011、PERF-007）→ B-13；Docker（DOCK-*、PG-014）→ B-12 |
| CI 三平台建置成功，安裝包 ≤ 200 MB | ⏳ | `desktop-build.yml` 和 DESK-030 大小門檻已寫好；雲端不能觸發 workflow → B-12 |
| `release-v0.1.md` 列出 DoD 每一項 | ✅ | `docs/checklists/release-v0.1.md` |
| README 快速開始與安裝流程一致 | ✅ | README 雙語「快速開始」；`opagent dev` |
| 開 PR，請使用者驗證後合併 | ✅ | PR #5；發版 → B-9 |

## 2A · 混合搜尋、重複照片、trash、XMP

| 完成條件 | 狀態 | 證據 |
|---|---|---|
| 09b §1～§2 與相關 viewer ID 全部通過 | ✅／⏳ | SRCH-RRF、PLAN、IMG、DUP、TRASH、XMP 全綠。例外：SRCH-RRF-008 需 pgvector 0.8（CI）；XMP-002 手動。地圖與比較模式依 11 屬 v0.4，已在 v0.4 完成 |
| Q-T5 未定案時停下回報 | ✅ | 依 goal 的規則改用審議 → ADR-0008 |
| 既有測試全綠，gate 全綠，開 PR | ✅ | PR #6 |

## 2B · MCP server 與「連接 AI 助手」

| 完成條件 | 狀態 | 證據 |
|---|---|---|
| 09b §3 全部 ID 通過（含 324 個 scope 子案例與 24 條 MCP-INJ） | ✅ | MCP-SCOPE／FOLD／MEDIA／APPR／RATE／AUD／INJ／TOOL／TRN／PAT；MCP-RATE-002 依 ADR-0009 移到 v0.3 並已綠 |
| MCP-APPR 證明 agent 無法自行套用變更 | ✅ | MCP-APPR-001～006；`organize:apply` 永遠不發給 MCP token（MU-OAUTH-008 也涵蓋 OAuth） |
| docs 有可用的 client 設定範例 | ✅ | `docs/mcp-clients.md` |
| 補上的缺口（2026-11-03 稽核時發現） | ✅ | PF-060（`MCP_DISABLED`）、PF-061（`MCP_TOKEN_INVALID`）原本沒有測試：已實作 `mcp.enabled` 開關（兩種 transport 都會執行）、`mcp.connect` preflight 與「測試連線」按鈕 |
| gate 全綠，開 PR | ✅ | PR #7；真 client → B-15、B-16 |

## 2C · 人臉找照片

| 完成條件 | 狀態 | 證據 |
|---|---|---|
| 09b §4 全部 ID 通過 | ✅／⏳ | FACE-* 全綠，使用 FakeFaceBackend；FACE-N-001 需要真模型與授權資料（B-2） |
| FACE-PRG 刪除驗證全綠 | ✅ | FACE-PRG-001～004；v1.0 加上 SEC-FACE-001 可驗證的刪除報告 |
| Q-T1 未定案時停下回報 | ✅ | B-2 |
| gate 全綠，PR 列出真模型準確度項目 | ✅ | PR #8 |

## 3 · AI 問照片＋watch folder

| 完成條件 | 狀態 | 證據 |
|---|---|---|
| 09b §5～§6 全部 ID 通過 | ✅／⏳ | ASK-*、WATCH-*、BENCH-*、PRESET-* 全綠；ASK-EVAL-002、ASK-PERF-001 需真模型（nightly） |
| 評估集 recall@k 與拒答正確率達 10 §11 門檻（FakeBackend） | ✅ | ASK-EVAL-001 60 題小集；真模型數據 → B-1 |
| gate 全綠，開 PR | ✅ | PR #9 |

## 4 · 生態與效能

| 完成條件 | 狀態 | 證據 |
|---|---|---|
| 先細化 09b §7（獨立 docs commit） | ✅ | v0.4 的 31 條 |
| 新細化的 v0.4 測試全部通過 | ✅／⏳ | 29／31 綠（PF-018 在本次稽核補上 catalog 測試）；DIST-002、WINML-001 是 M 類（文件已寫好，等待審閱） |
| CI 能建置新的 pack | ⏳ | `packs.yml` 和可重現的 recipe 已寫好；第一次執行與簽章金鑰 → B-17 |
| caption 向量選型前先記錄比較 | ✅ | `docs/research/text-embedding-choice.md`、ADR-0014 |
| 更新紀錄，開 PR | ✅ | PR #10 |

## 5 · 自架 control plane

| 完成條件 | 狀態 | 證據 |
|---|---|---|
| 先細化 09b §8，C-21 未定案時停下回報 | ✅ | 細化放在獨立的 docs commit；C-21 由使用者定案（ADR-0015） |
| 新測試全部通過 | ✅ | v0.5 34／34 綠 |
| compose 的 controlplane＋worker 雙機 E2E | ✅／⏳ | MU-DEPLOY-001 以雙進程實際跑過；用 `docker compose` 的版本在 CI nightly 執行（雲端沒有 Docker daemon）→ B-19 |
| 安全測試：Worker 冒充、token 重放、跨 workspace 隔離 | ✅ | MU-SEC-001～004 |
| 更新紀錄，開 PR | ✅ | PR #11 |

## 6 · v1.0

| 完成條件 | 狀態 | 證據 |
|---|---|---|
| 細化 09b 的 v1.0 測試並更新 11 | ✅ | 09b §9 共 21 條；11 #114～#116 |
| 六種介面的快照，破壞性變更會讓 CI 失敗 | ✅ | `contracts/` 與 FRZ-001～007；`just contracts` 已加入 lint |
| 安全：fuzz 1M 次 0 逃逸、MCP 紅隊、人臉刪除驗證、SBOM | ✅ | SEC-FUZZ-001、SEC-REDTEAM-001（110 條）、SEC-FACE-001、SEC-SBOM-001 |
| 完整 i18n 與 a11y 稽核 | ✅ | I18N-010／011、A11Y-010／011 |
| 從 v0.x 升級的 migration 測試 | ✅ | MIG-001～003 |
| stable 更新通道 | ✅ | UPD-010 |
| VIEW-009 偶發失敗的根因 | ✅ | 不是產品問題：前面約 300 個測試留下的 heap 造成一次 232 ms 的 gen-2 GC；端點本身 p95 約 12 ms。效能測試改在獨立行程執行（ADR-0018），並以 PERF-009 防止回歸 |
| 所有測試與 release gate 全綠 | ✅／⏳ | 雲端能跑的全綠（見最終 gate）；release gate 的真機項目 → B-20 |
| `release-v1.0.md` 列出手動項目 | ✅ | `docs/checklists/release-v1.0.md`（REL-010 檢查它涵蓋所有開放中的 BLOCKERS） |
| 破壞性變更都有遷移說明 | ✅ | `docs/upgrading-to-1.0.md`；MIG-003 lint |
| 開 PR | ✅ | PR #12；外部審查與發版 → B-20 |

## ALL：整條 goal 的完成條件

| 條件 | 狀態 | 證據 |
|---|---|---|
| 里程碑 0～6 的完成條件都已達成（例外都列在 BLOCKERS 與 PR） | ✅ | 上面各表；BLOCKERS 附錄 |
| 每個里程碑都有疊加 PR | ✅ | PR #1～#12 |
| 最終分支 lint／test／test-int／e2e 全綠 | 見下 | 最終 gate |
| DECISIONS 記錄所有審議 | ✅ | 16 次審議、19 個 sub-agent → ADR-0001～0018（審議索引） |
| BLOCKERS 彙整所有使用者事項 | ✅ | B-1～B-20 與附錄 |

## 審議 sub-agent 對照（逐筆）

依 session 紀錄裡每一次 Agent 呼叫的時間與標題。規劃階段的規格撰寫（A～K、voicebox）與 mutation 修補是實作工作，不是審議，因此不列入。

| 時間（UTC） | sub-agent 標題 | ADR | ADR 內有：背景／選項／決定／理由／影響的測試 ID |
|---|---|---|---|
| 2026-11-01 18:33 | Deliberate threshold semantics | ADR-0001 | ✅ |
| 2026-11-01 19:06 | Deliberate registry pinning | ADR-0002 | ✅ |
| 2026-11-01 21:07 | Deliberate Storybook vs gallery | ADR-0003 | ✅ |
| 2026-11-01 21:50 | Deliberate waiting-state cancel gap | ADR-0004 | ✅ |
| 2026-11-01 22:33 | Deliberate search result-set cap | ADR-0005 | ✅ |
| 2026-11-02 03:35 | Deliberate PERF-003 font budget | ADR-0006 | ✅ |
| 2026-11-02 05:42 | Deliberate DL-019 download contention | ADR-0007 | ✅ |
| 2026-11-02 08:32 | Deliberate Q-T5 Lunar New Year range | ADR-0008 | ✅ |
| 2026-11-02 09:25 | Deliberate MCP v0.3-tool scope test conflict | ADR-0009 | ✅ |
| 2026-11-02 10:19 | Deliberate path strings vs UntrustedText | ADR-0010 | ✅ |
| 2026-11-02 11:54 | Deliberate bio role isolation and vault scope | ADR-0011、ADR-0012 | ✅ |
| 2026-11-02 15:53 | Deliberate ⌘J shortcut conflict | ADR-0013 | ✅ |
| 2026-11-02 16:44 | Compare caption text embedders | ADR-0014 | ✅ |
| 2026-11-02 17:52 | C-21: argue built-in OAuth AS | ADR-0015 | ✅ |
| 2026-11-02 17:53 | C-21: argue external IdP | ADR-0015 | ✅ |
| 2026-11-02 19:30 | Deliberate: tenant isolation (row filter) | ADR-0016 | ✅ |
| 2026-11-02 19:30 | Deliberate: tenant isolation (db per ws) | ADR-0016 | ✅ |
| 2026-11-03 13:56 | Deliberate protocol_version format | ADR-0017 | ✅ |
| 2026-11-03 16:1x | Deliberate VIEW-009 GC measurement | ADR-0018 | ✅ |

共 19 個 sub-agent、16 次審議。全部 18 份 ADR 都有五個必填段落，由 `docs/DECISIONS.md` 的段落檢查確認：2026-11-03 已補齊 ADR-0014～0018 的段落標題。

## 最終 gate

2026-11-03，在 `feat/v1.0` 的 commit `94968c0` 上依序執行一次；開始前與結束後工作樹都是乾淨的，HEAD 沒有變動：

| gate | 結果 | 時間 | 內容 |
|---|---|---|---|
| `just lint` | ✅ exit 0 | 11 s | ruff、mypy、import-linter、i18n／stories／contracts／changelog 檢查、eslint、tsc、cargo fmt＋clippy |
| `just test` | ✅ exit 0 | 184 s | pytest 1,200 passed；Vitest 37＋7；cargo test |
| `just test-int` | ✅ exit 0 | 453 s | 整合 498 passed；接著在獨立行程跑效能測試 2 passed（VIEW-009、PF-075，ADR-0018） |
| `just e2e` | ✅ exit 0 | 534 s | Playwright 83 passed |

`just tc-status`：940 個案例中 905 綠、1 紅、34 未開始。紅的是 PERF-001：雲端量到 16.8 ms，門檻 16.7 ms 以參考 runner 為準（B-13）。34 個未開始的 ID 都在 BLOCKERS 附錄裡對到負責的 B-x。
