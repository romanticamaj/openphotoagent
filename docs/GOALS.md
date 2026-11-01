# Milestone `/goal` 指令集

> 每個里程碑都有一條可以直接貼上的 `/goal` 指令。建議的使用方式：**一個 goal 結束 → review 並合併 PR → `/compact` → 貼下一條**。
> 所有 goal 都遵守 [`CLAUDE.md`](../CLAUDE.md)：嚴格 TDD、11 條不變量、不得捏造實驗結果，也不得修改測試去配合實作。
> 對應規格：路線圖見 [08 §9](08-desktop-packaging-and-final-review.md)；切片見 [09 §7](09-tdd-and-test-suite.md)；DoD 見 [11 §5](11-feature-coverage-review.md)；測試案例見 [09a](09a-test-cases-v0.1.md) 與 [09b](09b-test-cases-v0.2-plus.md)。

## 總覽

| # | Goal | 範圍 | 在哪裡跑 | 前置條件 |
|---|---|---|---|---|
| 0 | `M0-cloud` | M0 實驗腳本與雲端可跑的實驗、規格回寫 W-1～W-6 | 雲端 session | 無 |
| 0H | `M0-hardware` | 需要 GPU、Windows 或 macOS 的 M0 實驗 | **你的電腦**（5070 Ti / Windows 11；有 Mac 時也在 Mac 上跑） | 0 |
| 1A | `v0.1-A` | 切片 0–1：測試基建與 walking skeleton | 雲端 | 0 |
| 1B | `v0.1-B` | 切片 2–6：core、fs-sandbox、executor、scanner、queue/Operation/ETA | 雲端 | 1A |
| 1C | `v0.1-C` | 切片 7–10：推論、模型/下載/doctor/preflight、搜尋、viewer 與 UX | 雲端（真 adapter 等 0H） | 1B |
| 1D | `v0.1-D` | 切片 11–12：桌面殼、打包、強化與 release gate | 雲端 + CI + 你的電腦 | 1C、0H |
| 2A | `v0.2-A` | 混合搜尋、重複照片、trash、XMP | 雲端 | v0.1.0 |
| 2B | `v0.2-B` | MCP server（stdio + PAT）與「連接 AI 助手」 | 雲端 | 2A |
| 2C | `v0.2-C` | 人臉找照片 | 雲端（真模型驗證在你的電腦） | 2A |
| 3 | `v0.3` | AI 問照片、watch folder、benchmark、presets | 雲端（tool-calling 驗證在你的電腦） | v0.2.0 |
| 4 | `v0.4` | 生態：MLX/vLLM/torch pack、Immich、Label Studio、套件管理器 | 雲端 + CI | v0.3.0 |
| 5 | `v0.5` | 自架 control plane、遠端 Worker、多人、OAuth MCP | 雲端 | v0.4.0 |
| 6 | `v1.0` | 介面凍結、安全審查、穩定版 | 雲端 + CI | v0.5.0 |

## 每條 goal 共通的規則

這些規則已經寫進每條指令，這裡集中列出，方便查閱。

1. 開始前先讀 `CLAUDE.md`、`docs/09` §1，以及該 goal 指定的章節。
2. 分支命名為 `feat/<goal>`，從**最新已合併的 `main`** 開出；前一個 goal 的 PR 還沒合併時，就從它的分支開出。
3. TDD 流程：測試標註 09a／09b 的 ID，先紅再綠；commit 依序為 `test:` → `feat:`，footer 寫 `Tests:` 與 `Refs:`。
4. 每完成一個切片或子項：執行 `just tc-status`，在 `CLAUDE.md` §8 追加進度紀錄，然後 commit、push。
5. 結束時：`just lint test test-int e2e` 全綠，開 PR 並在描述中列出已完成與待硬體驗證的項目，請使用者 review。
6. 停下來回報的時機：規格衝突、門檻做不到、需要使用者決定的事項（CLAUDE.md §8 的待決清單）、需要硬體的驗證。

---

## 0 · `M0-cloud`：M0 實驗腳本與規格回寫

```
/goal 在 /home/user/openphotoagent 依 CLAUDE.md 執行 M0 雲端部分：先讀 CLAUDE.md、docs/09 §6 與 §9、docs/08 §9.4 與 §11、docs/11 §4；從 main（若 claude/spec-expansion 未合併則從它）開分支 spike/m0；(1) 完成 09 §9 的規格回寫 W-1～W-6（含 .github/pull_request_template.md 與 CONTRIBUTING 更新）；(2) 在 spikes/m0/<id>/ 為 09 §6 每個實驗（M0-S1a～S7、V7～V9、T1、T2）寫好可直接執行的腳本與 README，輸出 spikes/m0/results/<id>.json（schema 依 09 §6）；(3) 實際執行雲端 Linux 可跑的部分（T1 的 embedded_pg 與 tiny 模型選擇、S2 的 Linux 打包大小估算、S4 的 CPU EP 一致性、V8 的 WebKitGTK 探測、S5 的授權文件審查），其餘標記 pending-hardware，不可捏造數據；(4) 寫 docs/research/m0-results.md：已得結果、未通過項的影響，以及給使用者在 Windows 5070 Ti 與 Mac 上執行 pending 實驗的逐步指令。完成條件：W-1～W-6 已回寫；每個 M0 實驗都有腳本與結果檔（實測值或 pending-hardware）；m0-results.md 完整；開 PR 並回報哪些實驗需要使用者硬體。
```

## 0H · `M0-hardware`：在你的電腦上跑硬體實驗

> 這條在**你本機的 Claude Code** 執行（Windows 11 + RTX 5070 Ti），不是在雲端。有 Mac 的話，同一條 goal 也在 Mac 上跑一次。

```
/goal 在本機的 openphotoagent repo（spike/m0 分支或已合併的 main）依 CLAUDE.md 與 docs/research/m0-results.md 執行所有標記 pending-hardware 的 M0 實驗：依序執行 S1a/S1b/S1c（嵌入式 PG、kill -9 恢復、AF_UNIX）、S3/S3b（llama.cpp Qwen3-VL GGUF 結構化 JSON、key 順序）、S4/S4b（SigLIP2 ONNX DirectML 一致性、繁中 zero-shot）、S7（tool-calling）、V7（ETA 涵蓋率）、V9（tauri-driver）、T2（錄製 fixtures/hw/<機器>.yaml）；只記錄實測結果，不可推估或捏造；把結果寫入 spikes/m0/results/*.json 與 docs/research/m0-results.md，未通過的項目依 09 §6「不通過時」欄位更新對應規格（docs/03、06、08）並標註來源；完成條件：所有在本機能跑的實驗都有實測結果檔，m0-results.md 的結論表更新完畢，開 PR 列出通過與未通過項目，以及對 v0.1 範圍的影響。
```

---

## 1A · `v0.1-A`：測試基建 + walking skeleton（切片 0–1）

```
/goal 在 /home/user/openphotoagent 依 CLAUDE.md 實作 v0.1 切片 0 與 1：先讀 CLAUDE.md、docs/09 §1–§3 與 §5、§7、docs/02 §4 §6 §7.3 §10、docs/11 §5.3；從最新 main 開分支 feat/v0.1-a；切片 0：建立 uv＋pnpm workspace 與 CLAUDE.md §6 的 monorepo 目錄、justfile（setup/test/test-int/e2e/lint/tc-status/mutate-diff/dev）、ci.yml（PR 必跑 job 骨架、import-linter 禁止 apps/api 依賴 fs-sandbox 與 inference-*）、pytest-opagent-tc 外掛與 tools/tc_status.py、FakeClock、sandbox_tree、embedded_pg fixture、FakeInferenceBackend 骨架、本地 HF 模擬伺服器骨架，依序以 TDD 完成 PG-013、FS-026、INF-CONF-001～004、PF-079、I18N-001；切片 1：依 09 §7 順序 TDD 完成 CORE-TPL-001、CORE-TPL-004、FS-001～003、FS-017、SCAN-001、SCAN-003、SCAN-013、OUT-JRN-001、OUT-EXE-001～002、OUT-UNDO-001、CORE-SM-001、ORG-001，最後是 E2E-B-001（web build：選資料夾 → 依日期整理計畫 → 核准 → 複製 → undo），Clock、FsOps、InferenceBackend、OperationReporter 介面在此切片就要以最小形式存在；完成條件：just lint test test-int e2e 全綠，E2E-B-001 通過，原檔內容與 mtime 不變，undo 後與執行前一致，coverage-status 與 CLAUDE.md §8 已更新，開 PR。
```

## 1B · `v0.1-B`：核心模組補厚（切片 2–6）

```
/goal 在 /home/user/openphotoagent 依 CLAUDE.md 實作 v0.1 切片 2～6：先讀 CLAUDE.md、docs/09 §7（切片 2～6 列）、docs/09a 對應的 CORE/FS/OUT/SCAN/Q/OP/ETA 章節、docs/02 §4 §6 §10、docs/06 §5；從 feat/v0.1-a（若已合併則從 main）開分支 feat/v0.1-b；依序：切片 2 core 純函式（path_template、config_hash、decide、狀態機、規則 DSL，含 TS 鏡像的 golden test）；切片 3 fs-sandbox 全部規則與 OS 差異、property 與 fuzz 測試；切片 4 executor（衝突、move 的 copy-verify-delete、journal、在每一步注入崩潰的恢復、undo、磁碟滿、外接硬碟離線）；切片 5 scanner（增量、hash、HEIC/RAW/損毀檔、EXIF 時區、權限、雲端佔位檔）；切片 6 queue（SKIP LOCKED lease、重試、暫停/取消、快取命中、調門檻不重算）、Operation 持久化、ETA（EWMA、冷啟動先驗、多階段權重、rough/precise/stalled、FakeClock）；每個切片照 09 §7「最先寫的測試」順序 TDD；完成條件：09a 中切片 2～6 的 v0.1 ID 全部有通過的測試；core 與 fs-sandbox 的 branch coverage ≥ 95%，path_template 與 fs-sandbox 的 mutation score ≥ 85%；OUT-CRASH 系列的不變量（0 檔案遺失）全綠；just lint test test-int e2e 全綠；更新 coverage-status 與 CLAUDE.md §8；開 PR。
```

## 1C · `v0.1-C`：推論、模型管理、搜尋、viewer（切片 7–10）

```
/goal 在 /home/user/openphotoagent 依 CLAUDE.md 實作 v0.1 切片 7～10：先讀 CLAUDE.md、docs/research/m0-results.md（M0 結果；若硬體實驗尚未完成，真實 adapter 依規格實作但只用 FakeBackend 與錄製的 fixtures 測試）、docs/09 §7（切片 7～10）、docs/03 §1～§4 §10、docs/06 全文、docs/05 §1～§2（v0.1 範圍）、docs/04 §3～§5 §8、docs/10 §A；從 feat/v0.1-b（或 main）開分支 feat/v0.1-c；切片 7：Model Registry、InferenceService、llama.cpp server 與 ONNX Runtime adapter（可切換）、fused JSON schema 驗證與修復、cascade（規則 → SigLIP2 → VLM → 待確認）、VRAM 預算、OOM 降級、runtime 選擇矩陣、三種整理方式（依日期／內建 12 類／自訂類別）、待確認佇列；切片 8：下載管理（暫停、續傳、≤2 s 取消、sha256、磁碟預檢）、模型管理、環境醫生（以 fixtures/hw profiles 模擬硬體）、preflight 與 06 §4.7 錯誤碼目錄（每個錯誤碼都有 PF 測試與修復 CTA）、runtime pack 驗證、診斷包；切片 9：繁中斷詞、facet、SigLIP 以文搜圖、索引進度；切片 10：React viewer（虛擬化 justified 網格、日期 scrubber、Lightbox、選取與批次、10 張即時預覽、計畫核准畫面、Activity Center、ESLint opa/no-bare-disabled），設計系統依 04 §4；完成條件：09a 切片 7～10 的 v0.1 ID 中，不需實體 GPU 的全部通過；旅程 A 與 B 的 E2E 全綠；axe 0 serious/critical；zh-TW 與 en 沒有缺漏的 key；just lint test test-int e2e 全綠；更新 coverage-status 與 CLAUDE.md §8；開 PR，並列出需要在使用者電腦上驗證的項目。
```

## 1D · `v0.1-D`：桌面殼、打包與 release gate（切片 11–12）

```
/goal 在 /home/user/openphotoagent 依 CLAUDE.md 完成 v0.1 切片 11～12 並準備發佈 v0.1.0：先讀 CLAUDE.md、docs/08 §1～§6 §9.3、docs/09 §5 §7（切片 11～12）、docs/11 §5（DoD）、docs/research/m0-results.md、VERSIONING.md；從 feat/v0.1-c（或 main）開分支 feat/v0.1-d；切片 11：Tauri 2 殼（三進程監管與 kill -9 恢復、單一實例、launch token、Host/Origin 檢查、asset protocol scope、系統匣、OS 通知、原生資料夾選擇器、電池與 GPU 占用時暫停）、嵌入式 PG 初始化、升級、備份與還原、runtime pack 簽章驗證、updater（stable/beta）、Docker all-in-one、GitHub Actions 三平台建置與打包 workflow（依 08 §5 與 W-4 的 workflow 清單，由 release-please 呼叫可重用的 build workflow）；切片 12：效能（10 萬張網格 60fps）、chaos 全套、release gate workflow（tc_status --gate=v0.1）；完成條件：09a 所有 v0.1 ID 中，能在雲端或 CI 驗證的全部通過；CI 三平台建置成功且安裝包 ≤ 200 MB；docs/checklists/release-v0.1.md 列出 11 §5 DoD 每一項的狀態，以及需要使用者在參考機手動驗證的步驟；README 快速開始與實際安裝流程一致；開 PR，請使用者完成手動驗證後合併，由 release-please 產生 v0.1.0 的 Release PR。
```

---

## 2A · `v0.2-A`：混合搜尋、重複照片、trash、XMP

```
/goal 在 /home/user/openphotoagent 依 CLAUDE.md 實作 v0.2 第一部分：先讀 CLAUDE.md、docs/05 §1～§2 §11、docs/09b §1～§2、docs/10 §A（viewer 強化）、docs/11 §1（v0.2 能力）；從最新 main（v0.1.0 之後）開分支 feat/v0.2-a；以 TDD 實作 09b 的 SRCH-RRF、SRCH-PLAN（規則式 query planner、自然語言轉 filter chips）、SRCH-IMG、SRCH-DUP（以圖搜圖、相似照片、重複群組）、TRASH（新增 trash 輸出動作，移到 .opagent-trash/，可 undo；重複照片一鍵產生清理計畫）、XMP sidecar 寫回，以及 10 §A 列為 v0.2 的 viewer 強化（比較模式、重複群組檢視、地圖）；中文日期表達的範圍若遇到 Q-T5 未定案的情況，停下來回報；完成條件：09b §1～§2 與相關 viewer ID 全部有通過的測試；既有 v0.1 測試全綠；just lint test test-int e2e 全綠；更新 coverage-status 與 CLAUDE.md §8；開 PR。
```

## 2B · `v0.2-B`：MCP server 與「連接 AI 助手」

```
/goal 在 /home/user/openphotoagent 依 CLAUDE.md 實作 v0.2 的 MCP：先讀 CLAUDE.md、docs/05 §3 §6 §11、docs/08 §9.2（v0.2 列）、docs/09b §3、docs/04 §3.10 §8；從 feat/v0.2-a（或 main）開分支 feat/v0.2-b；以 TDD 實作 packages/mcp-tools 的 ToolRegistry、`opagent mcp` stdio server 與 `opagent login`（本機 PAT 存 OS keyring，預設 scope 不含 photos:media；organize:apply 永不發給 MCP token）、v0.2 的全部 tools（search_photos 含抽象關鍵字、label、latent space 以文搜圖與以圖搜圖、get_photo、find_similar、find_duplicates、list_*、run_labeling_job、get_job_status、propose_organize、get_organize_plan、apply_organize 只回傳核准連結）、tools/list 依 scope 過濾、資料夾範圍、mcp_media_policy、速率限制、稽核日誌、prompt injection 淨化；UI 的 Integrations/MCP 頁與「連接 AI 助手」精靈（一鍵產生 Claude Desktop、Claude Code、Cursor 設定）與 agent 請求核准卡；完成條件：09b §3 全部 ID（含 324 個 scope 子案例與 24 條 MCP-INJ 紅隊）通過；MCP-APPR 證明 agent 無法自行套用變更；just lint test test-int e2e 全綠；在 docs 補上實際可用的 client 設定範例；更新 coverage-status 與 CLAUDE.md §8；開 PR。
```

## 2C · `v0.2-C`：人臉找照片（killer ②）

```
/goal 在 /home/user/openphotoagent 依 CLAUDE.md 實作 v0.2 人臉找照片：先讀 CLAUDE.md、docs/07 全文、docs/09b §4、docs/05 §11（faces scope）、docs/10（人物相關 UI）、docs/research/m0-results.md（S5 授權結論）；人臉測試影像的授權來源（Q-T1）若尚未定案，FACE-* 只用 FakeFaceBackend，並停下來回報需要使用者決定的部分；從 feat/v0.2-b（或 main）開分支 feat/v0.2-c；以 TDD 實作：workspace 層級 opt-in 與同意紀錄；YuNet 偵測、對齊、品質過濾、AuraFace embedding 的 ONNX adapter（CPU fallback 用 SFace）；faces schema 與 pgvector 索引；增量 k-NN 指派（分數、margin、核心鄰居門檻）與 HDBSCAN 未命名群組；人物命名、合併、拆分、排除；人工決定鎖定不被演算法覆寫；「這是不是 X？」確認模式；拖一張照片找人；person facet 與 {person} 輸出樣板；People 頁與人物詳情；MCP 的 list_people、get_person、find_photos_of_person（faces:read，預設關閉）；一鍵刪除全部人臉資料（含 Worker 端與金鑰）、匯出排除 embedding；InsightFace 只做「使用者同意後下載」的選項，不打包；新增 RESPONSIBLE_USE.md；完成條件：09b §4 全部 ID 通過；FACE-PRG 刪除驗證全綠；just lint test test-int e2e 全綠；更新 coverage-status 與 CLAUDE.md §8；開 PR，列出需要在使用者電腦上用真模型驗證的準確度項目。
```

---

## 3 · `v0.3`：AI 問照片（killer ③）+ watch folder

```
/goal 在 /home/user/openphotoagent 依 CLAUDE.md 實作 v0.3：先讀 CLAUDE.md、docs/10 §B、docs/09b §5～§6、docs/05 §3～§5、docs/research/m0-results.md（S7 tool-calling 結論：未達 95% 時只開放快速路徑 ASK-LOOP-003）；從最新 main（v0.2.0 之後）開分支 feat/v0.3；以 TDD 實作：Assistant（共用 ToolRegistry；檢索、統計、單張問答走 structured output 的快速路徑；複雜問題進入最多 6 步的工具迴圈；llama-server --jinja，加上外部 OpenAI 相容 endpoint 選項與資料外送警示）；防幻覺規則（引用的照片必須來自本輪工具結果、數字只能來自 count_photos、找不到就拒答）；動作型請求只產生計畫卡；/api/v1/ask SSE 與對話資料表；問 AI 側欄面板、Lightbox 內單張提問、建議問題 chips，以及工具呼叫進度的預估時間與經過時間；本地 LLM query planner；watch folder 自動標註與歸檔；mini benchmark（golden set、散佈圖、覆蓋率–準確率曲線）；Presets 匯入匯出；describe_photo 與 collections tools；完成條件：09b §5～§6 全部 ID 通過；問答評估集的 recall@k 與拒答正確率達到 10 §11 的門檻（用 FakeBackend 的評估集；真模型數據標註待使用者電腦驗證）；just lint test test-int e2e 全綠；更新 coverage-status 與 CLAUDE.md §8；開 PR。
```

---

## 4 · `v0.4`：生態與效能

> 09b §7 目前只有骨架，所以這條 goal 會**先細化規格與測試案例，再實作**。

```
/goal 在 /home/user/openphotoagent 依 CLAUDE.md 進行 v0.4：先讀 CLAUDE.md、docs/08 §3～§4 §9.2（v0.4 列）、docs/09b §7、docs/02 §8（Immich、Label Studio）、docs/05（caption 文字向量 R2）；從最新 main 開分支 feat/v0.4；第一步：把 09b §7 的 v0.4 骨架細化成完整測試案例（ID、輸入、預期、對應能力），並更新 docs/11 覆蓋矩陣，在獨立的 docs commit 中提交；第二步：以 TDD 實作 MLX pack（Mac）、vLLM pack（Linux）、torch pack（JoyCaption、Florence-2）的 runtime pack 格式、簽章與相容矩陣；Immich 回寫、Label Studio 匯出；caption 多語文字向量（候選 bge-m3 或 Qwen3-Embedding，選型前先記錄比較）；winget、Homebrew、Flathub 的發佈設定或評估文件；Windows ML 取代 DirectML 的評估；完成條件：新細化的 v0.4 測試全部通過，既有測試全綠，CI 能建置新的 pack，更新 coverage-status 與 CLAUDE.md §8，開 PR。
```

## 5 · `v0.5`：自架 control plane

```
/goal 在 /home/user/openphotoagent 依 CLAUDE.md 進行 v0.5：先讀 CLAUDE.md、docs/02 §1 §5.2～§5.3 §6、docs/05 §3.2（OAuth 2.1）、docs/08 §9.2（v0.5 列）與 C-21、docs/09b §7；從最新 main 開分支 feat/v0.5；第一步：把 09b §7 的 v0.5 骨架細化成完整測試案例並更新 docs/11，在獨立的 docs commit 中提交；第二步：以 TDD 實作 control plane 的 Docker 部署（NAS）、遠端 Worker 配對（一次性配對碼、ed25519、WSS 主動連出、斷線 long-poll）、桌面 app 當 client 或 headless Worker、多使用者與 RBAC、OIDC、四級隱私政策（含 results-only）、/mcp Streamable HTTP + OAuth 2.1（或委託外部 IdP，依 C-21 決定；未定案時停下來回報）；安全測試：Worker 冒充、token 重放、跨 workspace 隔離；完成條件：新測試全部通過，既有測試全綠，docker compose 的 controlplane 加 worker 雙機 E2E 通過，更新 coverage-status 與 CLAUDE.md §8，開 PR。
```

## 6 · `v1.0`：穩定版

```
/goal 在 /home/user/openphotoagent 依 CLAUDE.md 進行 v1.0 穩定化：先讀 CLAUDE.md、VERSIONING.md §4（各介面的相容性承諾）、docs/08 §9.2（v1.0 列）、docs/09 §5、docs/11；從最新 main 開分支 feat/v1.0；第一步：細化 09b §7 的 v1.0 測試案例並更新 11；第二步：凍結並產生快照測試（REST /api/v1 的 OpenAPI 快照、Worker protocol_version、MCP tool manifest、runtime pack_api、model registry schema_version、preset schema），任何破壞性變更都會讓 CI 失敗；安全審查任務（路徑沙箱 fuzz 1M 次 0 逃逸、MCP 紅隊集擴充、人臉資料刪除驗證、依賴與 SBOM 漏洞掃描）；完整 i18n 與 a11y 稽核；從 v0.x 各版升級的 migration 測試；stable 更新通道；完成條件：所有測試與 release gate 全綠；docs/checklists/release-v1.0.md 列出手動驗證項目；CHANGELOG 中的破壞性變更都附有遷移說明；開 PR，請使用者驗證後由 release-please 發佈 v1.0.0。
```

---

## ALL · 一條 goal 從 M0 連續做到 v1.0

> 不等每個里程碑的 PR 合併，直接以**疊加分支**往下做。需要硬體、手動驗證或發版的項目一律標記 pending，留給你之後處理。過程中會多次自動 compact，每一段的進度都會記錄在 `CLAUDE.md` §8 和 `docs/BLOCKERS.md`，方便接續。

```
/goal 在 /home/user/openphotoagent 依 CLAUDE.md 與 docs/GOALS.md，按順序連續完成里程碑 0（M0-cloud）→ 1A → 1B → 1C → 1D → 2A → 2B → 2C → 3 → 4 → 5 → 6，不要等待 PR 合併：每個里程碑開始前重讀 CLAUDE.md、GOALS.md 該節列出的規格章節、CLAUDE.md §8 進度紀錄與 docs/BLOCKERS.md；每個里程碑從上一個里程碑的分支開出自己的分支（第一個從 claude/spec-expansion 開出），完成時 push 並開一個以上一個分支為 base 的疊加 PR，PR 描述列出已完成、pending-hardware、需要人工驗證的項目，然後立刻開始下一個里程碑；全程嚴格 TDD（測試標註 09a/09b 的 ID，先紅再綠，禁止刪除、跳過或弱化測試，禁止修改測試去配合實作），遵守 CLAUDE.md §4 全部不變量；推論與硬體一律使用 FakeInferenceBackend 和 fixtures/hw profiles，需要實體 GPU、Windows、macOS 的實驗或驗證只寫腳本並標記 pending-hardware，絕不捏造數據；發版（tag/Release）與參考機手動驗證不做，記為待使用者處理；遇到規格衝突、做不到的門檻或 CLAUDE.md §8 列出的待決事項時，不要停下整個流程：記錄到 docs/BLOCKERS.md（里程碑、測試 ID、問題、建議的選項），能用規格已寫明的預設值就採用並註明，不能就跳過該項，繼續下一個不受影響的工作；違反不變量的風險則必須停下來回報；每完成一個切片就執行 just tc-status、更新 CLAUDE.md §8 進度紀錄並 commit、push，確保中途 compact 後可以從紀錄接續；v0.4～v1.0 先細化 09b §7 的測試案例再實作。完成條件：GOALS.md 中里程碑 0 至 6 每一條的完成條件都已達成（pending-hardware、人工驗證與發版項目除外，且這些項目都已在 BLOCKERS.md 與各 PR 中列出）；每個里程碑都有對應的疊加 PR；最終分支上 just lint、just test、just test-int、just e2e 全綠；docs/BLOCKERS.md 彙整所有待使用者決定或驗證的事項。
```

## 附註

- **一次跑完 v0.1 的替代指令**：如果想讓一個 goal 連續做完切片 0–12，可以改用 `CLAUDE.md` handoff 時提供的那條 v0.1 總指令。不過分成 1A–1D，每段都有 PR 可以 review，比較不容易偏離規格。
- **發版**：每個 milestone 的最後一個 PR 合併後，release-please 會自動開 Release PR，合併它就會打 tag、建 Release。雲端 session 不能 push tag。
- **調整 goal**：規格變動時，同步更新這份檔案和 `CLAUDE.md` §8。
