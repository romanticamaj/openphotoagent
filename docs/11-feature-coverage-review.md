# 11 — 規格完整性與一致性審查（Feature Coverage Review）

> 狀態：Draft v0.1（2026-11-01）｜角色：規格完整性與一致性審查員
> 範圍：以使用者的最終需求為最高準則，檢查 docs/00–08、10、research/voicebox-study.md、README（雙語）、VERSIONING.md、CHANGELOG.md 是否完整覆蓋需求、彼此是否一致；直接修正缺口與衝突，並給出 v0.1 MVP 的完成定義。09（TDD 測試策略）尚未撰寫，不在本次審查範圍，但 §5 的驗收清單可直接作為 09 的輸入。
> 版本欄一律依 [08 §9](08-desktop-packaging-and-final-review.md) 的新路線圖：M0 spike → v0.1 桌面 MVP → v0.2 人臉 + 混合搜尋 + MCP → v0.3 AI 問照片 + watch folder → v0.4 生態 → v0.5 自架 control plane → v1.0 穩定。
> 深度定義：**完整** = 有資料結構、API 或 UI 規格，實作者不需要再做產品決策；**部分** = 有方向，但缺少關鍵細節（畫面、門檻、資料欄位或流程其中之一）；**缺** = 規格裡沒有。標「（審查補充）」的表示本次審查才補齊。

---

## 0. TL;DR

| 項目 | 結果 |
|------|------|
| 覆蓋矩陣 | **107 條能力**：完整 **99**（其中 9 條是本次審查才補齊）、部分 **7**、缺 **1** |
| 重大衝突（已修正） | ① 人臉版本（07：v0.3 實驗）與問照片版本（10：v0.2 lite）和 08 路線圖衝突 → 統一為人臉 v0.2、問照片 v0.3；② 人臉 MCP scope 三種寫法（`faces:read`／`people:read`／併入 `photos:media`）→ 統一為 07 的 `faces:read` + `faces:media`；③ 06／07／08／10 的介面變更全部沒有寫回 02–05 → 已集中寫回 02 §10、03 §10、04 §8、05 §11；④ 00 與 README 仍是「vLLM FP8 預設、v1.0 才有桌面、SaaS、CLI 待定」→ 全部改為最新決策；⑤ CLI `opl` → `opagent`、`OPL_` → `OPAGENT_`（12 個檔案） |
| 主要剩餘風險 | M0 spike 尚未證明的技術前提（嵌入式 PG 在 Windows／Defender 下的穩定性、llama.cpp Qwen3-VL 結構化輸出與 logprob、SigLIP2 ONNX 匯出、tool-calling 可靠度）；v0.1 範圍偏大；簽章經費與 SignPath 時程；人臉法規；ETA 準確度只有工程目標 |

---

## 1. 功能覆蓋矩陣

> （回寫 W-5，09 §9）每個能力的驗收標準對應的測試 ID 與目前狀態（未開始／紅／綠／部分），見自動產生的 [`docs/generated/coverage-status.md`](generated/coverage-status.md)（由 `just tc-status` 更新）。

### 1.1 交付形態與平台

| # | 能力 | 規格位置 | 深度 | 版本 | 驗收標準（可測） |
|---|------|----------|------|------|------------------|
| 1 | Windows x64／macOS arm64／Linux x64 安裝包（NSIS／dmg／AppImage + deb） | 08 §5.1、§9.3 | 完整 | v0.1 | 三平台在乾淨 VM 上以一般使用者權限安裝並啟動成功；壓縮後各 ≤ 200 MB（CI 守門） |
| 2 | Docker all-in-one（同一份 web build） | 02 §1.1、08 §1.6 | 完整 | v0.1 | `docker compose -f deploy/compose/allinone.yml up` 後，瀏覽器完成 onboarding 並跑完一次整理任務 |
| 3 | Rust 殼監管 postgres／api／worker，崩潰自動恢復 | 08 §1.1、§1.4 | 完整 | v0.1 | 暖啟動 < 5 s 可操作；對任一子進程 `kill -9` 後 30 s 內恢復，UI 不清空狀態、Job 從 lease 續跑 |
| 4 | 嵌入式 PostgreSQL 17 + pgvector，自動備份與還原 | 08 §2.3、02 §2.2 | 完整 | v0.1 | 每日備份保留 7 份、每次 migration 前多一份；「還原」後資產數與結果數和備份時一致 |
| 5 | 自動更新（stable／beta 通道、ed25519 簽章） | 08 §5.3 | 完整 | v0.1 | 從 vN 更新到 vN+1 後資料保留；竄改過的更新包被拒絕並顯示原因 |
| 6 | 程式碼簽章（macOS notarize、Windows SignPath） | 08 §5.2 | 部分（SignPath 尚未申請核准，經費未定） | v0.1 | macOS notarize 通過；Windows 若未簽章，Release 附 sha256 與 SmartScreen 說明 |
| 7 | 單一實例、系統匣、OS 通知、deep link | 08 §1.3、§6 | 完整 | v0.1 | 第二次啟動只把既有視窗帶到前景；任務完成發 OS 通知，點擊開到結果頁 |
| 8 | 單人模式、免登入、launch token 保護 loopback API | 08 §1.3、D10；02 §1.3、§6.3（審查補充） | 完整 | v0.1 | 不帶 token 的 `/api/v1` 請求回 401；Host／Origin 不符回 403；UI 沒有登入畫面與 Org／Workspace 選單 |
| 9 | 電池、GPU 被占用、只在閒置時處理 | 08 §6；06 §5.5.7（審查補充）；02 §10.5 | 完整 | v0.1 | 筆電拔電源後重型任務在 10 s 內暫停並顯示 `PAUSED_ON_BATTERY`；接回電源自動繼續 |
| 10 | 解除安裝預設保留資料、提示垃圾桶內檔案 | 08 §5.5；02 §10.2（`/trash/summary`） | 完整 | v0.1 | 解除安裝不刪照片、輸出資料夾、XMP；有 `.opagent-trash/` 檔案時先提示 |
| 11 | zh-TW／en 介面、WCAG 2.2 AA | 04 §4.6–4.7、10 §5.5 | 完整 | v0.1（完整版 v1.0） | 兩種語言無未翻譯 key；axe 檢查 0 個 serious／critical |
| 12 | 自架 control plane + 遠端 Worker 配對、多人 RBAC、OIDC | 02 §1、§5.2；08 §9.2 | 完整（設計） | v0.5 | 遠端 Worker 只靠 outbound 連線完成配對與任務；`ws_viewer` 無法經由任何 API 或 MCP 寫入 |

### 1.2 模型管理、環境醫生、預估時間（需求 (1)）

| # | 能力 | 規格位置 | 深度 | 版本 | 驗收標準 |
|---|------|----------|------|------|----------|
| 13 | 依硬體只推薦一個組合（含下載大小與預估時間） | 08 §3.3、§8.1；06 §6.6；04 §8.2（審查補充） | 完整 | v0.1 | 三台參考機（5070 Ti、RTX 4060、M2 16GB）onboarding 各只顯示一個推薦組合，標出 GB 數與分鐘數 |
| 14 | 相容性三級（ok／degraded／blocked）+ 原因碼 + 替代方案 | 06 §1.2；03 §10 | 完整 | v0.1 | 每個 variant 在選擇器、卡片、精靈、preflight 四處顯示相同等級與原因 |
| 15 | 模型卡：大小、VRAM／RAM、授權、能力、速度（實測或估算並標來源） | 06 §1.3–1.4 | 完整 | v0.1 | 未下載的模型也顯示大小；速度欄標示「估算」或「實測」 |
| 16 | 下載暫停、續傳、真取消 | 06 §2.2 | 完整 | v0.1 | 下載到 50% 時斷網再恢復，從斷點續傳且不重下已完成 bytes；按取消後 ≤ 2 s 網路連線關閉 |
| 17 | 下載速度、ETA、sha256 驗證、磁碟預檢與執行中監控、鏡像 | 06 §2.3–2.7 | 完整 | v0.1（鏡像 v0.2） | 磁碟不足時下載前就擋下並給 CTA；sha 不符的檔案不會被標成已安裝 |
| 18 | 下載不佔 VRAM、不打斷正在跑的 Job | 06 §2.8（M5） | 完整 | v0.1 | Job 執行中下載另一個模型，Job 吞吐下降 < 10% 且不中斷 |
| 19 | 刪除模型、搬移模型目錄 | 06 §1.7 | 完整 | v0.1 | 搬移 10 GB 模型目錄期間有進度與 ETA，完成後模型可直接載入 |
| 20 | 匯入既有模型（Ollama、LM Studio、HF cache、任意資料夾） | 06 §1.8 | 完整 | v0.2 | 偵測到本機 Ollama 的 Qwen3-VL GGUF 後可一鍵登記，不重新下載 |
| 21 | 授權同意流程（gated／非商用權重） | 06 §1.9；07 §1.4 | 完整 | v0.1 | 預設組合免勾選；非商用權重需勾選並寫入 `license_acceptance` |
| 22 | Runtime pack 按需下載、簽章、相容矩陣 | 08 §4；06 §1.1（審查補充：pack 目錄以 08 為準） | 完整 | v0.1 | NVIDIA 機器被建議 `llamacpp-cuda13`（Blackwell）或 `cuda12`；壞簽章、壞 sha、tar 路徑穿越的 pack 都被拒絕 |
| 23 | 支援各種 runtime：llama.cpp、ONNX Runtime、vLLM（Linux）、MLX（Mac）、Ollama、外部 OpenAI 相容 | 03 §2.4、§10（審查補充）；08 §3 | 完整 | v0.1（vLLM／MLX／torch pack v0.4） | 同一個整理任務分別用 llama.cpp 與外部 OpenAI 相容 endpoint 跑通，結果 schema 相同 |
| 24 | 使用者自訂模型 YAML | 03 §1.1 | 完整 | v0.1 | 放入自訂 YAML 後 Models 頁出現該模型並跑過 compat 判定 |
| 25 | 每個任務的預設模型 | 06 §1.6 | 完整 | v0.1 | caption、classify、embedding 可分別指定預設 variant |
| 26 | 環境醫生：GPU、驅動、記憶體、磁碟、pack smoke test（不需 torch） | 06 §3；08 §10；03 §10 | 完整 | v0.1 | NVIDIA、AMD（Vulkan）、Intel、Apple Silicon、純 CPU 五種機器都能產生報告且不崩潰 |
| 27 | Preflight + 修復 CTA（停用必附理由與 fix） | 06 §4；04 U9（審查補充） | 完整 | v0.1 | ESLint `opa/no-bare-disabled` 擋下沒有 reason 的 disabled；06 §4.7 每個錯誤碼都有 i18n 文案與至少一個 fix |
| 28 | 永遠有退路（CUDA → Vulkan → CPU；VLM → SigLIP 快速模式） | 08 §8.2 U9、§3.2 | 完整 | v0.1 | 驅動過舊時同時出現「開啟驅動下載頁」與「先用 Vulkan 繼續」，選後者可完成任務 |
| 29 | 開始前預估（張數、時間、磁碟用量） | 06 §5.9、§6.3；05 §11（審查補充：MCP 也回傳 estimate） | 完整 | v0.1 | 精靈預覽頁在按「開始」前顯示 p50 時間與依據（實測／先驗） |
| 30 | 執行中 ETA（附可信度、rough／precise／stalled） | 06 §5.5 | 完整 | v0.1 | 事後回看 80% 區間涵蓋真值的比例 ≥ 70%（v0.1 門檻）；目標 80% |
| 31 | 經過時間（扣除暫停） | 06 §5.6 | 完整 | v0.1 | 暫停 1 分鐘後恢復，經過時間不含暫停期間；完成時顯示總耗時 |
| 32 | 等待原因（排隊、等 GPU、等下載、Worker 離線、電池、來源離線） | 06 §5.5.7（審查補充 4 個 code） | 完整 | v0.1 | 每種等待狀態都顯示原因與 CTA，不出現沒有說明的停滯 |
| 33 | Activity Center + Operation 持久化與恢復 | 06 §5.8、§6.1 | 完整 | v0.1 | 重新啟動 app 後，進行中與最近完成的操作仍顯示正確進度 |
| 34 | log、診斷包匯出（去識別化） | 06 §3.7；08 §1.5 | 完整 | v0.1 | 診斷包內不含絕對路徑與照片內容；包含 doctor 報告 |
| 35 | Mini benchmark（用自己的 golden set 比較模型） | 03 §6 | 完整 | v0.3 | 對 2 個候選模型輸出準確率、速度、VRAM 與門檻覆蓋率曲線 |
| 36 | 模型目錄更新與「有更好的推薦模型」通知 | 06 §2.9 僅有「更新模型目錄」CTA | **缺** | 未排程 | （建議）registry 隨 app 更新；新版推薦組合改變時，在 Models 頁提示而不自動下載 |

### 1.3 照片管理、自動分類、標記、批次、caption、資料夾（需求 (2) 與 killer ①）

| # | 能力 | 規格位置 | 深度 | 版本 | 驗收標準 |
|---|------|----------|------|------|----------|
| 37 | 選擇來源資料夾（原生選擇器、多選、拖放；自動加入 allowlist） | 08 §6；04 §3.3 | 完整 | v0.1 | 拖資料夾進視窗即可加入；新增 root 只能經殼 IPC，REST 呼叫回 404／403 |
| 38 | 選擇輸出資料夾與路徑樣板 `{category}/{date:YYYY}/{place}` | 02 §3.2 `output_rule`；04 §3.2 | 完整 | v0.1 | path template 的 Python 與 TS 實作通過同一組 golden test；檔名非法字元被清理 |
| 39 | 分類資料夾（各類別對應不同目標與規則） | 02 §3.2 `output_rule.when/target_id` | 完整 | v0.1 | 「螢幕截圖」與「美食」可各自輸出到不同 target |
| 40 | 「依日期」整理（不需要模型） | 03 §10（審查補充 `by-date` preset）；08 §8.1 | 完整 | v0.1 | 未下載任何模型時即可完成依日期整理；缺 EXIF 的照片以 mtime 並標註來源 |
| 41 | 「依內容」內建 12 類 | 03 §10（審查補充 `by-content-12`）；08 §8.1 | 完整 | v0.1 | 12 類各有 zh-Hant／en 名稱與描述；無法判斷者落入「其他」，不會遺漏任何照片 |
| 42 | 自訂類別（名稱 + 描述 + 範例照片） | 03 §3.3、§3.3.1；04 W2 | 完整 | v0.1 | 拖 3 張照片建立類別後，精靈顯示全庫預估命中數 |
| 43 | Caption（繁中／英文、短／長） | 03 §3.1 | 完整 | v0.1 | 結構化 JSON 成功率 ≥ 99%（M0 S3）；caption 寫入 search_doc 可被全文搜尋 |
| 44 | Tagging（受控詞彙 + 新詞上限 + 同義詞合併） | 03 §3.2 | 完整 | v0.1 | 同義詞「狗／犬」合併成同一 canonical key |
| 45 | Fused 推論 + cascade（規則 → SigLIP2 → VLM → 待確認） | 03 §3.0、§3.3 | 完整 | v0.1 | 高信心照片不送 VLM；每筆結果記錄是哪一層決定的 |
| 46 | 10 張即時預覽 + 全庫預估分布 | 04 §3.2、W1；08 §8.1 | 完整 | v0.1 | 只改門檻或樣板時預覽 < 200 ms 更新且不跑 GPU |
| 47 | 預覽變更 → 核准 → journal → 執行 → 復原 | 02 §4、§6.2；04 §3.5、§3.11 | 完整 | v0.1 | 在 copy／verify／delete 任一步驟注入崩潰，恢復後 0 個檔案遺失或損毀；undo 後資料夾狀態與執行前相同 |
| 48 | 預設複製；移動需二次確認；衝突自動處理（同 hash 略過、重名加後綴） | 08 §8.1；02 §6.2 | 完整 | v0.1 | 預設設定下原檔 mtime 與內容完全不變 |
| 49 | 待確認佇列（一次一張、數字鍵、「其他」逃生門） | 04 §3.7；08 §8.1 | 完整 | v0.1 | 不覆核也能輸出（未確認者進「其他」）；全程可用鍵盤完成 |
| 50 | JSON／CSV sidecar；XMP 寫回 | 02 §6.2 | 完整 | v0.1（XMP v0.2） | XMP 可被 digiKam／Lightroom 讀出 `dc:subject`；預設不改原檔 |
| 51 | 增量掃描、內容 hash 去重、推論快取 | 02 §4.1、ADR-003 | 完整 | v0.1 | 搬移資料夾後重新掃描，0 張重新推論 |
| 52 | HEIC／RAW 解碼與預覽 | 02 §4.1；10 §3.11 | 完整 | v0.1（RAW 內嵌預覽強化 v0.2） | iPhone HEIC 與常見 RAW 都能產生縮圖並推論 |
| 53 | 外接硬碟／NAS 離線處理 | 02 §10.5（審查補充）；06 §5.5.7 | 完整 | v0.1 | 拔掉外接硬碟後 Source 顯示「離線」、任務進入等待而非失敗，接回後自動繼續 |
| 54 | 雲端佔位檔與 Photos Library 封裝略過 | 02 §10.5；08 C-31 | 完整 | v0.1 | OneDrive 佔位檔不觸發下載，略過原因顯示在掃描報告 |
| 55 | Viewer 批次動作（標籤、類別、收藏、加入相簿、移到資料夾 → dry-run） | 10 §4.2 | 完整 | v0.1（收藏與相簿 v0.2） | 選 37 張 → 移到資料夾，產生 output plan 並導到核准頁 |
| 56 | 就地修正 AI 結果 → 寫入 review | 10 §3.8 | 完整 | v0.1 | Lightbox 改類別後，下次整理使用修正值，且 review 記錄為 corrected |
| 57 | Watch folder 自動標註 | 05 §5 | 完整 | v0.3 | 新照片放入監看資料夾 2 分鐘內完成標註與索引；move 永遠需要人工核准 |
| 58 | Presets 匯入／匯出與分享 | 04 §2.2 Presets；VERSIONING §4 | 部分（分享格式與流程未詳述） | v0.3 | 匯出的 preset 在新版可匯入（`schema_version`） |
| 59 | 重複照片清理（保留代表張、其餘移到垃圾桶，可復原） | 10 §4.2（審查補充：提案）；05 §2.2 | 部分（`trash` 動作待確認） | v0.2 | 重複群組可一鍵產生計畫，核准後檔案進 `.opagent-trash/`，undo 可還原 |

### 1.4 MCP 給第三方 AI（需求 (3)）

| # | 能力 | 規格位置 | 深度 | 版本 | 驗收標準 |
|---|------|----------|------|------|----------|
| 60 | stdio `opagent mcp` + 本機 PAT（自動找 port、絕對路徑） | 05 §3.1（審查補充）、§3.10；08 D11、C-20 | 完整 | v0.2 | app 重啟換 port 後，Claude Desktop 不改設定仍可呼叫 |
| 61 | 「連接 AI 助手」精靈（偵測 Claude Desktop／Code／Cursor，顯示 diff 後寫入） | 04 W12、§8.3（審查補充）；08 C-19 | 部分（缺 wireframe 與各 client 設定檔路徑表） | v0.2 | 三個 client 都能一鍵寫入並在 client 端列出 tools；只寫入已知設定檔路徑 |
| 62 | 抽象關鍵字查詢（自然語言 → planner → filter） | 05 §2.1、§2.3、§3.3.3 | 完整 | v0.2 | 「去年在京都的貓」轉成日期、地點、tag filter，結果附 `matched_by` |
| 63 | Label／tag／category／facet 結構化過濾 | 05 §3.3.2–3.3.3、§1.4 | 完整 | v0.2 | `tags_any`、`category`、`taken_at` 組合查詢結果與 REST `/search` 相同 |
| 64 | Latent space 以文搜圖（SigLIP2 跨模態） | 05 §2.1；05 §3.3.1（審查補充說明） | 完整 | v0.2（UI 以文搜圖 v0.1） | 英文描述查詢能找到沒有對應 tag 的照片 |
| 65 | Latent space 以圖搜圖（`find_similar`，限圖庫內照片） | 05 §2.2、§3.3.5 | 完整 | v0.2 | 指定 asset_id 回傳 cosine 排序結果；MCP 不接受上傳任意圖片 |
| 66 | 重複照片查詢（`find_duplicates`） | 05 §2.2 | 完整 | v0.2 | 同 hash 與近似重複分組正確 |
| 67 | 人物 tools（`list_people`、`get_person`、`find_photos_of_person`） | 07 §5.2；05 §3.3.1（審查補充） | 完整 | v0.2 | 沒有 `faces:read` 時 tools 不出現、`PhotoSummary.people` 欄位不存在 |
| 68 | 預設唯讀、資料夾範圍、scope = RBAC permission | 05 §3.2.3；00 R7 | 完整 | v0.2 | 只有 `photos:read` 的 token 呼叫寫入 tool 回 403 `insufficient_scope` |
| 69 | 檔案變更只能提案，人工核准（桌面 deep link） | 05 §3.6（審查補充規則 0）；08 C-22 | 完整 | v0.2 | 任何 MCP token 都無法呼叫核准 API；核准綁定 `plan_hash`，計畫變更後失效 |
| 70 | 縮圖外送政策 `mcp_media_policy` + `photos:media` | 05 §3.5（審查補充桌面預設） | 完整 | v0.2 | 未授 `photos:media` 時回應附 `media_withheld_reason` |
| 71 | Prompt injection 防護（照片文字、EXIF、人名一律不可信） | 05 §3.9；07 §5.3 | 完整 | v0.2 | 紅隊測試集中未經核准的寫入次數 = 0 |
| 72 | 稽核紀錄與速率限制 | 05 §3.7–3.8；07 §5.3 | 完整 | v0.2 | 每次 tool 呼叫都寫 `mcp_call_log`；超過速率回 429 |
| 73 | MCP over HTTP + OAuth 2.1 | 05 §3.2.2（審查補充：延到 v0.5） | 完整（延後） | v0.5 | Claude Code 以 `/mcp` 完成 OAuth 並受 grant 撤銷即時影響 |

### 1.5 人臉找照片（需求 (4)、killer ②）

| # | 能力 | 規格位置 | 深度 | 版本 | 驗收標準 |
|---|------|----------|------|------|----------|
| 74 | 預設關閉、opt-in 同意紀錄（onboarding 後的卡片，可略過） | 07 §0、§4.2；04 §8.2（審查補充） | 完整 | v0.2 | 未同意前不跑任何人臉模型；同意紀錄寫入 `face_consent` |
| 75 | 偵測 → 對齊 → 品質 → embedding（ORT，寬鬆授權預設） | 07 §1、§2.1–2.2 | 完整 | v0.2 | 預設安裝包不含任何 `redistributable=false` 權重（CI 檢查） |
| 76 | 線上指派 + HDBSCAN 分群，人工決策凍結 | 07 §2.3、ADR-702 | 完整 | v0.2 | 重跑分群不會改動任何人工確認或命名過的指派 |
| 77 | 命名、合併、拆分、忽略、合併建議 | 07 §2.4、§4.3–4.4 | 完整 | v0.2 | 合併後兩人的照片全部歸到同一 person，可 undo |
| 78 | 點一張臉或**拖一張照片**找這個人 | 07 §2.5、§4.5；02 §10.2 `/faces/query-image` | 完整 | v0.2 | 拖入的照片只在記憶體（TTL 10 分鐘），不落地；回傳「確定」「可能」兩級結果 |
| 79 | 人物 + 其他條件組合（「媽媽 海邊」） | 07 §3.3；05 §11（planner 詞典） | 完整 | v0.2 | 搜尋列輸入人名 + 關鍵字，結果同時符合兩者 |
| 80 | `{person}` 輸出樣板與 fan-out | 07 §4.6 | 完整 | v0.2 | 一張有兩個人的照片依設定複製到兩個人的資料夾 |
| 81 | Review 人臉確認模式（Y／N） | 07 §4.7 | 完整 | v0.2 | 「這是不是 X？」可用鍵盤連續處理 |
| 82 | 一鍵刪除所有人臉資料（可驗證） | 07 §6.3、§10；02 §10.6 | 完整 | v0.2（安全審查 v1.0） | purge 後 `bio` partition 不存在、人臉縮圖目錄為空、產生刪除報告 |
| 83 | 換人臉模型（embedding space 藍綠） | 07 §2.8 | 完整 | v0.2 | 切換模型期間舊結果仍可用，完成後原子切換 |
| 84 | 人臉 MCP 隱私（`faces:read`／`faces:media`、不回傳 embedding、不接受上傳參考臉） | 07 §5；05 §3.2.3（審查補充） | 完整 | v0.2 | 有 `faces:read` 但沒有 `faces:media` 時回應中沒有任何人臉影像 |

### 1.6 精美的 React 照片 viewer（需求 (5)）

| # | 能力 | 規格位置 | 深度 | 版本 | 驗收標準 |
|---|------|----------|------|------|----------|
| 85 | Justified 時間軸網格（虛擬化、Web Worker 排版、月 bucket） | 10 §2.2、§5.1 | 完整 | v0.1 | 10 萬張圖庫捲動維持 60 fps（10 §5.1 預算），首屏 < 1 s |
| 86 | 日期 scrubber（密度直方圖、拖動只畫 ThumbHash） | 10 §2.3 | 完整 | v0.1 | 拖到任一月份 < 100 ms 定位；`role="slider"` 有正確 aria-valuetext |
| 87 | ThumbHash → 縮圖 → 預覽 → 原圖的漸進載入 | 10 §3.3、§5.3 | 完整 | v0.1 | 網格不出現空白格；Lightbox 先顯示低解析再換高解析 |
| 88 | Lightbox：共享元素轉場、縮放平移、預載、filmstrip | 10 §3.1–3.4 | 完整 | v0.1 | 不支援 View Transitions 的 WebView（WebKitGTK）自動降級為 FLIP |
| 89 | 資訊面板：EXIF、AI 結果、模型來源、信心 | 10 §3.7 | 完整 | v0.1 | 每個 AI 欄位顯示是哪個模型、哪一層 cascade 產生 |
| 90 | 鍵盤與觸控手勢 | 10 §3.5–3.6 | 完整 | v0.1 | 不用滑鼠可完成瀏覽、選取、批次、開啟 Lightbox |
| 91 | 選取模型（點、Shift 範圍、⌘A 全選結果集） | 10 §4.1 | 完整 | v0.1 | ⌘A 選取「全部 3,214 張結果」而不是只選畫面上的 |
| 92 | 資料夾檢視（含輸出資料夾）與「在檔案總管顯示」 | 10 §2.6；08 §6 | 完整 | v0.1 | 桌面版可從任一照片跳到 Finder／檔案總管並選中檔案 |
| 93 | 桌面以 Tauri asset protocol 直接讀本地快取 | 10 §5.4、§D（審查補充：提前到 v0.1） | 完整 | v0.1 | asset protocol scope 只含快取目錄與已註冊 root；其他路徑讀取失敗 |
| 94 | 人物檢視（人物牆、人物時間軸、與…合照、臉框） | 10 §2.4；07 §4 | 完整 | v0.2 | 從人物牆點進去顯示該人的時間軸網格 |
| 95 | 相簿、收藏、評分、旗標 | 10 §2.7、§10.3 `asset_flag` | 完整 | v0.2 | 評分與收藏可作為搜尋 filter |
| 96 | 地圖（MapLibre + PMTiles 離線，無圖磚模式） | 10 §2.5 | 完整 | v0.4 | 離線時仍顯示照片聚類與國界 |
| 97 | 比較模式（2–4 張）、DZI 深度縮放、色彩管理（P3） | 10 §3.9、§3.11 | 完整 | v0.4 | 比較模式同步縮放平移；P3 照片在支援的螢幕上不被壓成 sRGB |
| 98 | 回憶（旅程、那年今日） | 10 §2.8 | 部分（只有方向） | v1.x | — |
| 99 | 影片播放與封面 | 10 §D v0.4 | 部分（02 掃描與推論未定義影片處理） | v0.4 | — |

### 1.7 AI 問照片（killer ③）

| # | 能力 | 規格位置 | 深度 | 版本 | 驗收標準 |
|---|------|----------|------|------|----------|
| 100 | 快速路徑：檢索、統計、單張問答（只用 structured output） | 10 §8.4 | 完整 | v0.3 | 檢索型首個答案 token < 2 s（16GB 參考機）；統計數字只來自 `count_photos` |
| 101 | 有界通用 tool loop（≤ 6 步，16GB+） | 10 §8.5；08 S7 | 完整 | v0.3 | S7 未通過時自動只開快速路徑，不影響發佈 |
| 102 | 步驟可視化（每步預估與經過時間）、可取消 | 10 §8.7、§10.2 | 完整 | v0.3 | 每個 `step.started` 帶 `eta_ms`，`step.completed` 帶 `elapsed_ms`；取消後 ≤ 1 s 停止 |
| 103 | 引用照片與 grounding（只引用工具回傳過的照片） | 10 §8.6、§8.11 | 完整 | v0.3 | 引用有效率 ≥ 0.98；拒答正確率 ≥ 0.90 |
| 104 | 動作型：計畫卡 + UI 人工核准 | 10 §9.4；05 §4（審查補充） | 完整 | v0.3 | Assistant 工具集中沒有 `apply_organize`；injection 測試寫入次數 = 0 |
| 105 | 本地 LLM 與外部 endpoint（`assistant_media_policy`，預設不送影像） | 10 §8.2、§8.9 | 完整 | v0.3 | 雲端模式時 Ask 面板常駐警示；預設只送文字 metadata |
| 106 | 評估集與 CI（PR 小集、nightly 全集） | 10 §11 | 完整 | v0.3 | 300 題問題集指標達 10 §11.2 目標 |
| 107 | 8 GB／純 CPU 機器的 Ask 體驗 | 10 §8.2；08 §3.2-5 | 部分（純 CPU 的本地 LLM 選型未定；只能建議外部 endpoint） | v0.3 | （建議）CPU only 時明確顯示「需要外部 endpoint 或較慢的本地模式」並給設定 CTA |

### 1.8 統計

| 群組 | 條數 | 完整 | 部分 | 缺 |
|------|------|------|------|----|
| 交付與平台 | 12 | 11 | 1 | 0 |
| 模型管理、環境醫生、預估時間 | 24 | 23 | 0 | 1 |
| 照片管理與自動分類 | 23 | 21 | 2 | 0 |
| MCP | 14 | 13 | 1 | 0 |
| 人臉 | 11 | 11 | 0 | 0 |
| Viewer | 15 | 13 | 2 | 0 |
| AI 問照片 | 8 | 7 | 1 | 0 |
| **合計** | **107** | **99** | **7** | **1** |

> 「完整」裡有 9 條是本次審查才補齊到可實作的程度：#8、#9、#32、#40、#41、#53、#60、#69、#93。部分與缺的 8 條都列在 §4.1（D-2、D-5、D-6）或 §4.3 的後續工作中。

---

## 2. 本次修改清單

### 2.1 全域

| 檔案 | 修改 |
|------|------|
| 00、02–08、10、README（雙語） | CLI `opl` → **`opagent`**、`OPL_*` → **`OPAGENT_*`**、`~/.opl/` → `~/.opagent/`（實際路徑依 08 §1.5）、`opl_pat_` → `opagent_pat_`、`.opl-trash` → `.opagent-trash`、Python 套件路徑 `opl/…` → `opagent/…`；各文件標頭的「CLI 暫沿用」改為「已定案」。01 保留原始調查內容，只在標頭加上後續決定 |
| 02、05 | 「SaaS」改為「自架 control plane」；Postgres 16 → PostgreSQL 17 + pgvector 0.8 |
| 02 | 縮寫「OPL」改為 OpenPhotoAgent；配對碼範例改為 `OPA-7KQ2-9XMV` |

### 2.2 各文件

| 檔案 | 章節 | 修改摘要 |
|------|------|----------|
| **00** | 標頭、§0 | 新定位（桌面 app + 三個 killer 功能）；授權、CLI、商業模式標為已定案 |
| 00 | §1 | 文件索引加入 06、07、08、09（撰寫中）、10、11、voicebox 研究 |
| 00 | §2 | 架構圖改為桌面主形態、PG 17、llama.cpp + ORT；部署形態改為三種 |
| 00 | §3 | 改寫為「三個 killer 功能」「五項最重要能力」對照表；16GB vLLM 預設改為依 08 §3.3 的各檔位 llama.cpp 預設 |
| 00 | §4 | R5 更新（SSE 改用 `operation.*`）、R8 定案；新增 R9–R13（介面回寫、版本衝突、scope 命名、桌面本機安全、media policy） |
| 00 | §5 | 路線圖改為 08 §9（M0 → v0.1 → … → v1.0） |
| 00 | §6、§7、§8、§9 | monorepo 加入 desktop、assistant、faces-core、viewer、packaging；實作分工加入 Desktop、Models & Ops、Faces、Assistant；ADR 摘要加入 9–11；§8 改為決策紀錄（Q1–Q4 結案，新增 Q5–Q7）；§9 下一步更新 |
| **01** | 標頭 | 註明正式名稱與 CLI 的後續決定（本文保留原始調查） |
| **02** | §1.1 | 新增 (c) 桌面形態說明；目標使用者改為 homelab／自架 |
| 02 | §1.2 | `OPAGENT_MODE` 加 `desktop`、`OPAGENT_AUTH` 加 `launch_token`；桌面 bootstrap token 經 stdin |
| 02 | §1.3 | 單人模式預設；人臉與 Assistant permission |
| 02 | §1.4 | 桌面 egress 固定 `full`；三個 media policy 取最嚴格者 |
| 02 | §2.2 | DB 改 PG 17 + pgvector 0.8；Worker 三種交付方式 |
| 02 | §6.3 | 新增 launch token、handoff code、本機 PAT；所有 API 驗證 Host／Origin；root 只能經 IPC 新增 |
| 02 | **§10（新增）** | 06／07／08／10 的資料表、欄位、REST、SSE、Worker 協定、scanner、安全、monorepo、ADR-008–011 全部集中寫回；補上 `/trash/summary`、備份還原 API、Source 離線處理 |
| **03** | 標頭 | 推論主軸改為 llama.cpp + ORT；路徑慣例 |
| 03 | §1.3、§8 | 16GB 預設 VLM 改為 Qwen3-VL-8B GGUF on llama.cpp，vLLM FP8 改為 Linux 進階；SigLIP2 走 ORT；small slot 加人臉 |
| 03 | **§10（新增）** | 內建三種整理方式 preset（`by-date`、`by-content-12`、`custom`）；06／07／08／10 對推論層的全部變更（registry 欄位、compat、下載器、doctor、VRAM provider、fingerprint `numerics_epoch`、資源策略、人臉、Ask `chat`／`verify`） |
| **04** | §0 | 新增 U8–U11（一條推薦路徑、永遠不卡住、術語兩層、時間永遠看得見） |
| 04 | §1.2、§2.3、§3.9、§5.6、§6 W12、§7.2、§7.3 | 指向新 onboarding；TaskTray → Activity Center；PrivacyBadge 桌面版；Workers 頁隱藏；Tauri 改為 v0.1 主形態；W12 擴大為「連接 AI 助手」；里程碑對照；SSE 事件更新 |
| 04 | **§8（新增）** | 單人模式導覽重排、onboarding、06／07／10 的新頁面與元件（含版本）、技術棧與 lint 規範 |
| **05** | 標頭 | 桌面優先調整摘要；PG 17 |
| 05 | §1.2、§1.4 | `embedding_space.modality` 加 `face`；`search_doc` 加 `person_ids`、`person_ids_suggested`、`face_count`、`rating`、`favorite`；人物 facet 改為 v0.2 |
| 05 | §3.1、§3.2.2、§3.2.3、§3.3.1 | 桌面 stdio 唯一預設與本機 PAT；OAuth 延到 v0.5；新增 `faces:read`／`faces:media` scope 與刻意不提供的能力；新增人物、`count_photos`、`verify_photos`、`compare_photos` tools；`PhotoFilters` 擴充；latent space 檢索說明 |
| 05 | §3.5、§3.6、§3.10、§4、§6 | 桌面 media 預設；deep link 核准；桌面設定範例；chat 助手指向 10（v0.3）；分期對照 08 §9 |
| 05 | **§11（新增）** | 整合索引表；MCP 回傳 `estimate` 與 `elapsed_ms` |
| **06** | §1.1、§3.4、§5.2、§5.5.7、§9 | `face_attr` → `face_quality`（07 ADR-707 不做屬性推論）；pack 目錄以 08 為準並修正內建 pack；`face.*` 依賴的 check；`face.cluster` 階段依 07；新增 `PAUSED_GPU_CONTENDED`、`PAUSED_NOT_IDLE`、`SOURCE_OFFLINE`，電池暫停改為預設；【待 07】【待 08】結案；M1–M3 對照 v0.1–v0.3 |
| **07** | §0、§3.2、§5.1、§10、§11 | 版本改為 **v0.2 交付**（遠端 vault／多人 v0.5、影片等 v1.x）；PG 17；`faces:write` 改 v1.x |
| **08** | §0 標頭、§7、§9.2、§10、C-25、C-29 | 標註 06／07／10 已完成；變更清單落實狀態指向本文；v0.2 MCP tools 補齊；C-25 改用 `faces:read`／`faces:media`；C-29 結案 |
| **10** | 標頭、§0、§2.4、§8.3、§10.4、§C、§D、§4.2 | 07 對齊（scope、filter 名稱、API）；分期改為 Viewer v0.1、人物 v0.2、**Ask v0.3**、進階 v0.4／v1.x；Tauri asset protocol 提前到 v0.1；新增「移到垃圾桶」批次動作提案 |
| **README.md／README.zh-TW.md** | 全文 | 標語與「為什麼」改為桌面 app；Features 改為八張卡（自動分類、人臉、問照片、模型管理與環境醫生、時間預估、viewer、混合搜尋、MCP）；Models 改為各硬體的 llama.cpp 預設（vLLM 進階）；MCP 加「連接 AI 助手」、人物 tools、`opagent` 範例；隱私、架構、快速開始（桌面 + Docker）、路線圖（08 §9）、比較表、Design docs（加入 06／07／08／10／11）；徽章改為桌面三平台與 8 GB／Apple Silicon／CPU。雙語內容一致 |
| **VERSIONING.md** | §3、§4、§5、中文摘要 | 里程碑改為 08 §9；新增 Runtime pack（`pack_api`）與 Desktop data dir（`layout_version`）兩個介面版本；桌面安裝包從 v0.1 起發佈 |
| CHANGELOG.md | — | **未修改**：`[0.0.1]` 是已發佈版本的紀錄，release-please 會在下一次發版時依 commit 產生新段落。建議這批修改以 `docs:` commit 合併，自然出現在 0.0.2 的 changelog |

---

## 3. 隱私與安全一致性（審查結論）

| 主題 | 結論 | 位置 |
|------|------|------|
| 人臉 scope | 統一為 `faces:read`（預設不授予，且需 workspace `mcp.faces_enabled=true`）+ `faces:media`（受 `mcp_face_media_policy`，預設 `none`）。agent 拿不到 embedding、不能上傳參考臉、看不到未命名臉群 | 07 §5；05 §3.2.3；08 C-25；10 §10.4 |
| 外送政策 | `egress_policy` 與 `mcp_media_policy`、`mcp_face_media_policy`、`assistant_media_policy` 一律取最嚴格者；桌面 `egress_policy` 固定 `full` 並隱藏，只在三種外送情境警示 | 02 §1.4；05 §3.5；07 §5.4；10 §8.9 |
| 桌面免登入 | UI 用 launch token（每次啟動輪替、只在記憶體、經 stdin 傳遞）；所有 API 驗證 Host／Origin；「允許區網存取」會強制啟用登入 | 08 §1.3；02 §6.3 |
| 本機 MCP 憑證 | launch token **不能**給 MCP；MCP 用 keyring 裡的本機 PAT，預設只有 `photos:read`（Q5 待使用者確認） | 05 §3.1、§3.5；02 §6.3 |
| 擴大可讀範圍 | 新增或刪除 root 只能經殼 IPC（使用者手勢）或本機 CLI；REST 與 MCP 都沒有 | 02 §6.3；05 §3.2.3；08 C-09 |
| 核准 | MCP 與 Ask 的寫入一律只產生計畫；核准只能在 app／瀏覽器 session 完成；桌面為 `openphotoagent://approvals/{id}` | 05 §3.6；10 §9.4 |
| 不可信資料 | 照片文字、EXIF、檔名、caption、**人名**一律 `UntrustedText` | 05 §3.9；07 §5.3；10 §8.10 |

---

## 4. 仍待決定或待驗證事項

### 4.1 需要使用者決定

| # | 事項 | 建議 |
|---|------|------|
| D-1 | 桌面「連接 AI 助手」建立的本機 PAT 是否預設包含 `photos:media` | **已決定（採建議）**：預設**不含**，精靈中提供勾選與外送說明（00 Q5） |
| D-2 | 是否新增 output 動作 `trash`（重複照片清理，移到 `.opagent-trash/`，可 undo） | **已決定（採建議）**：新增 `trash`，v0.2 與重複照片清理一起做；實作時回寫 02 `output_rule.action` |
| D-3 | 簽章經費（Apple 每年 99 美元）與 SignPath 申請 | 盡早送出 SignPath 申請；v0.1 不因此延期（08 §9.3） |
| D-4 | v0.1 範圍是否過大（三平台安裝包 + doctor + 模型管理 + 整理 + viewer + ETA） | 建議以 §5 的 DoD 為硬門檻，必要時把 Linux deb／rpm、Docker 版延到 v0.1.x，安裝包先保留 Windows 與 macOS |
| D-5 | 模型目錄更新與新推薦模型通知（矩陣 #36） | 建議 v0.2：registry 隨 app 更新，推薦組合改變時提示，不自動下載 |
| D-6 | 純 CPU 機器的 Ask 方案（矩陣 #107） | 建議 v0.3 只提供外部 endpoint，加上明確 CTA；本地小 LLM 列為實驗 |
| D-7 | GitHub org、PyPI、npm 名稱與 `openphotoagent.org` 網域註冊 | 盡快註冊（bundle id 已用 `org.openphotoagent.desktop`） |

### 4.2 待驗證（M0 spike 或實作時）

| # | 項目 | 影響 | 來源 |
|---|------|------|------|
| V-1 | 嵌入式 PG 17 在 Windows 一般與管理員帳號、Defender 開啟下的冷啟動與鎖檔率 | v0.1 的 DB 方案；失敗時改用 embedded-postgres 套件補洞 | 08 S1、§2.3 |
| V-2 | llama.cpp Qwen3-VL GGUF + mmproj 的結構化 JSON 成功率與 logprob 介面（CUDA 13／Vulkan／Metal） | cascade 信心分數；失敗時需替代信心方案 | 08 S3、03 §9 |
| V-3 | SigLIP2 so400m-patch14-384 ONNX 匯出數值對齊（cosine ≥ 0.999）與繁中 zero-shot 品質 | 內建 12 類與以文搜圖品質 | 08 S4、03 §9 |
| V-4 | 安裝包 ≤ 200 MB（含 PG、Python、llama.cpp CPU／Metal、ORT DirectML／CoreML） | 下載體驗 | 08 S2 |
| V-5 | AuraFace／YuNet 授權與上游檔案；eDifFIQA(T) 授權 | 人臉預設組合能否隨 app 散布 | 07 §12、08 S5 |
| V-6 | llama-server `--jinja` tool-calling 連續 20 次成功率 ≥ 95% | v0.3 通用 tool loop 是否開啟 | 08 S7 |
| V-7 | ETA 80% 區間的實際涵蓋率（`eta_calibration`） | 「預估時間」可信度；v0.1 先以 ≥ 70% 為門檻 | 06 §5.5.5 |
| V-8 | WebKitGTK（Linux）上 View Transitions、`Intl.DurationFormat`、`createImageBitmap` 的支援 | viewer 降級路徑 | 10 §0、06 §9 |
| V-9 | macOS WKWebView 沒有 WebDriver：桌面 E2E 只能做煙霧測試 + 手動清單 | 09 的測試策略 | 08 C-11 |
| V-10 | 人臉法規（BIPA、EU AI Act、台灣個資法）的實際義務 | v0.2 發佈前的文案與預設 | 07 §6.1 |

### 4.3 文件層面的後續工作

- 09（TDD 測試策略）撰寫時，把 §5 每一條轉成自動化測試或手動清單項目，並引用 02 §10、06 §4.7 的錯誤碼。
- 04 的 wireframe 尚未涵蓋：Activity Center 抽屜、「連接 AI 助手」精靈、人物頁（07 §4.3 已有）、問照片面板（10 §9.3 已有）。建議 v0.1 開工前補 Activity Center 與簡化後的 onboarding 兩張。
- 02 §5.1 原始 REST 表與 §10.2 新增端點分開維護，產生 OpenAPI 時要合併；建議實作時以 `packages/schemas` 為唯一來源。
- `.github/workflows` 的 `desktop-build.yml`、`packs-build.yml`、`nightly-desktop.yml`（08 C-27）尚未建立。

---

## 5. v0.1 MVP 驗收清單（Definition of Done）

> 全部勾選才發 `v0.1.0`。「參考機」= RTX 5070 Ti 16GB（Windows 11）、RTX 4060 8GB 筆電（Windows 11）、M2 MacBook Air 16GB，加上一台 Ubuntu 22.04 x64（NVIDIA 或 AMD）。「測試圖庫」= 10,000 張混合 JPEG／HEIC／RAW，含 500 張無 EXIF、200 組重複、一個外接硬碟資料夾。

### 5.1 安裝與啟動

- [ ] Windows（NSIS per-user）、macOS arm64（dmg，已 notarize）、Linux x64（AppImage + deb）安裝包各 ≤ 200 MB，以一般使用者權限安裝成功。
- [ ] Docker all-in-one 以同一份 web build 啟動，onboarding 可完成。
- [ ] 首次啟動 < 300 ms 出現啟動畫面；暖啟動 < 5 s 可操作。
- [ ] 沒有登入畫面、沒有 Org／Workspace 選單；API 只綁 `127.0.0.1`；不帶 launch token 的請求回 401，錯誤 Host／Origin 回 403。
- [ ] 對 postgres、api、worker、llama-server 任一進程 `kill -9`，app 自動恢復且進行中的任務續跑，不遺失進度。
- [ ] 第二次啟動只帶出既有視窗；系統匣可暫停／繼續全部任務；任務完成發 OS 通知。
- [ ] 自動更新：從 `v0.1.0-rc.N` 更新到下一版保留所有資料；竄改的更新包被拒絕。
- [ ] 每日備份與 migration 前備份存在；「立即備份」「還原」可用。

### 5.2 環境醫生、模型管理、時間

- [ ] 四台參考機的環境醫生都能產生報告且不崩潰（含沒有 torch、AMD 走 Vulkan、Apple 走 Metal／CoreML）。
- [ ] onboarding 只顯示一個推薦組合，附下載大小與預估時間；授權為 Apache-2.0 的預設組合免勾選。
- [ ] 每一個會被擋住的操作在按下前就顯示原因與至少一個修復 CTA；ESLint `opa/no-bare-disabled` 在 CI 開啟且通過。
- [ ] 驅動過舊或 GPU pack 失敗時，提供「先用 Vulkan／CPU 繼續」並能完成任務。
- [ ] 模型下載可暫停、續傳（斷網後從斷點繼續）、取消（≤ 2 s 停止）；sha256 驗證；磁碟不足時下載前就擋下。
- [ ] 所有內建模型 variant 都已在 `registry/pins.lock` 釘選 commit／size／sha256（INF-REG-013，ADR-0002）。
- [ ] 下載在背景進行時，可以同時選資料夾、掃描、產生縮圖；SigLIP2 先下載，以文搜圖最早可用。
- [ ] 模型可刪除、可搬移目錄（附進度與 ETA）；搬移後可直接載入。
- [ ] 所有長操作（下載、掃描、縮圖、推論、整理、復原、搬移、pack 安裝）都是 Operation：開始前預估、執行中 ETA（rough／precise／stalled）、經過時間（扣除暫停）、等待原因。
- [ ] 在參考機上跑完測試圖庫後，ETA 80% 區間的事後涵蓋率 ≥ 70%（記錄實際數字，回饋 06 校準）。
- [ ] 筆電拔電源時重型任務自動暫停並顯示原因，接回電源自動繼續；全螢幕遊戲時暫停。
- [ ] 重新啟動 app 後，Activity Center 仍顯示進行中與最近完成的操作。
- [ ] 「匯出診斷包」產生的 zip 不含絕對路徑與照片內容。

### 5.3 自動分類到資料夾（killer ①）

- [ ] 原生選擇器與拖放都能加入來源資料夾；REST 呼叫無法新增 root。
- [ ] 三種整理方式可用：**依日期**（未下載任何模型也能用）、**依內容 12 類**、**自訂類別**（名稱 + 描述 + 範例照片）。
- [ ] 預設輸出到 `圖片/OpenPhotoAgent 整理結果`、預設**複製**；移動需另外開啟並二次確認。
- [ ] 10 張即時預覽：改類別描述或模型會重算，只改門檻或樣板時 < 200 ms 且不跑 GPU；顯示全庫預估分布與總時間。
- [ ] Path template 的 Python 與 TS 實作通過同一組 golden test；非法檔名字元被清理；path traversal fuzz 測試通過。
- [ ] cascade 正確：高信心不送 VLM，低信心進「待確認」；每筆結果記錄決策層與 model fingerprint。
- [ ] 結構化 JSON 成功率 ≥ 99%（每台參考機，測試圖庫）。
- [ ] 「待確認」一次一張、數字鍵操作；「略過剩下的，全部放進『其他』」可用；不覆核也能輸出。
- [ ] 整理計畫摘要一句話（「將複製 N 張到 M 個資料夾，原檔不動」）；衝突自動處理（同 hash 略過、重名加後綴）。
- [ ] 在 copy、verify、delete、journal 寫入的任一點注入崩潰，恢復後 **0 個檔案遺失或損毀**；undo 後資料夾與執行前一致。
- [ ] 預設設定跑完整個測試圖庫後，所有原檔的內容 hash 與 mtime 不變。
- [ ] JSON／CSV sidecar 輸出正確。
- [ ] 搬移已整理的資料夾後重新掃描，0 張重新推論（快取命中）。
- [ ] 外接硬碟拔掉時 Source 顯示「離線」、任務等待而不是失敗；接回後自動繼續。
- [ ] 完成畫面提供「在檔案總管開啟」「試試搜尋」。

### 5.4 Viewer 與搜尋

- [ ] Justified 時間軸網格在 10 萬張合成圖庫上捲動維持 60 fps；首屏 < 1 s；不出現空白格（ThumbHash 先行）。
- [ ] 日期 scrubber 拖到任一月份 < 100 ms 定位；鍵盤 PgUp／PgDn／Home／End／`G` 可用。
- [ ] Lightbox：轉場（WebKitGTK 降級為 FLIP）、縮放平移、前後預載、filmstrip、資訊面板（EXIF、AI 結果、模型來源）、「在檔案總管顯示」。
- [ ] 就地修正類別或標籤後寫入 review，下次整理使用修正值。
- [ ] 選取（點、Shift、⌘A 全選結果集）與批次動作（標籤、類別、移到資料夾 → 產生計畫）可用。
- [ ] 桌面版經 Tauri asset protocol 讀快取；scope 外的路徑讀取失敗。
- [ ] 關鍵字 + tag + 日期 facet 搜尋可用；SigLIP 以文搜圖可用；還在建索引時顯示「已可搜尋 X / Y 張」。
- [ ] axe 檢查 0 個 serious／critical；zh-TW 與 en 沒有未翻譯 key。

### 5.5 品質與發佈

- [ ] 09 定義的測試全部通過：core 純函式、fs-sandbox fuzz、journal 故障注入、嵌入式 PG 三平台整合測試、FakeBackend E2E（web 與 desktop 兩種 build）、桌面煙霧測試（Windows、Linux 用 tauri-driver；macOS 手動清單）。
- [ ] 四台參考機的效能紀錄（張／秒、VRAM、ETA 涵蓋率）附在 release notes。
- [ ] Release 附 sha256、SBOM；Windows 若尚未簽章，文件說明 SmartScreen 處理方式。
- [ ] README 的快速開始與實際安裝流程一致；CHANGELOG 由 release-please 產生。
- [ ] 明確**不在** v0.1：人臉、MCP（可用 `--experimental` 唯讀 stdio 例外）、RRF 混合搜尋與 planner、重複照片、XMP、問照片、watch folder、benchmark、vLLM／MLX／torch pack、Immich、Label Studio、遠端 Worker、多人、OAuth。
