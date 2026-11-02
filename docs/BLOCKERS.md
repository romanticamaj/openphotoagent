# BLOCKERS — 需要使用者本人決定或驗證的事項

> 只收錄 CLAUDE.md §5.8 定義的「必須由使用者本人決定」的事：金錢、法律意見、帳號與簽章憑證、資料授權來源的最終確認、發版，以及需要實體硬體或參考機的驗證。工程取捨一律經 sub-agent 審議記在 [`DECISIONS.md`](DECISIONS.md)。
> 每一項標註受影響的里程碑；流程不因這些項目停止，受影響的部分以 pending 方式交付。

| # | 類別 | 事項 | 影響 | 現況／建議下一步 | 里程碑 |
|---|------|------|------|------------------|--------|
| B-1 | 硬體 | 執行 pending-hardware 的 M0 實驗（S1a Win/Mac、S1c、S3、S3b、S4、S4b、S7、T1c、T2 參考機錄製） | cascade 信心方案、ONNX 一致性、tool-calling 是否開放完整迴圈、安裝包實測 | 步驟見 `docs/research/m0-results.md` §3 | M0-hardware |
| B-2 | 資料授權 | 人臉測試影像的授權來源（09 Q-T1）；M0-S5 的 TAR@FAR 需要授權允許的人臉資料集 | FACE-N-001 與真模型準確度驗證 | 確認前 FACE-* 只用 FakeFaceBackend | M0、v0.2-C |
| B-3 | 金錢／帳號 | Apple Developer Program（每年 99 美元）、Developer ID 憑證與 notarize 用 API key（M0-S6） | macOS dmg 簽章與 notarize | 取得後設定 secrets，依 `spikes/m0/S6/README.md` 演練 | M0、v0.1-D |
| B-4 | 帳號 | SignPath Foundation OSS 申請（Windows 簽章） | Windows SmartScreen | 未核准前以未簽章版本 + sha256 發佈（08 §9.3，不擋發佈） | v0.1-D |
| B-5 | 憑證 | Tauri updater 與 runtime pack 的 minisign 私鑰（`TAURI_SIGNING_PRIVATE_KEY`、pack 簽章金鑰） | 自動更新與 pack 驗證的正式金鑰 | 測試用金鑰對只放在 `tests/support/packserver`；正式私鑰由使用者產生並存為 GitHub secret | v0.1-D |
| B-6 | 法律意見 | 人臉模型訓練資料集授權對再散布的影響；人臉功能的法規（BIPA、EU AI Act、台灣個資法，11 V-10） | v0.2 人臉功能發佈前的文案與預設 | 需要有經驗的人審閱 | v0.2-C |
| B-7 | 硬體驗證 | AuraFace v1 的授權檔與 sha（HF 在雲端被擋） | 預設辨識模型能否打包 | 在可連 HF 的機器確認 `fal/AuraFace-v1` LICENSE | v0.2-C |
| B-8 | 帳號 | GitHub org、PyPI、npm 名稱與 `openphotoagent.org` 網域註冊（11 D-7） | 套件發佈、bundle id | 盡快註冊 | v0.1-D |
| B-9 | 發版 | 每個里程碑的 release-please Release PR 合併、tag 與 GitHub Release | 版本發佈 | 雲端 session 不能 push tag；由使用者合併 Release PR | 每個里程碑 |
| B-10 | 帳號 | `registry-pin.yml` 需要的 GitHub App token（bot PR 才會觸發 CI）；第一次執行該 workflow 並審閱、合併 `chore(registry): pin …` PR（ADR-0002） | 內建模型的 size／sha256 釘選；INF-REG-013 release gate | 建立 GitHub App、設定 secret，觸發 workflow 後人工審閱 diff 再合併 | v0.1-C、v0.1-D |
| B-11 | 參考機手動驗證 | v0.1-C 的介面在參考機（Windows 11、RTX 5070 Ti 16GB）走一遍：歡迎精靈的硬體結論與推薦組合、真實下載（需 B-10 釘選）、整理（依日期／依內容）、待確認、搜尋、Lightbox、環境檢查；並確認視覺快照在 Windows 字型渲染下的差異只當參考（基準以 Linux Chromium 為準） | 真模型路徑與實機畫面的驗收 | 雲端以 FakeBackend 與合成 hw profile 驗證，步驟見 1C PR 的「需要人工驗證」 | v0.1-C |
| B-12 | CI 首次執行 | 新 workflow 的第一次執行與審閱：`desktop-build.yml`（三平台 pg pack、Python runtime、Tauri bundle、200 MB 門檻 DESK-030）、`docker.yml`（DOCK-001～003）、`nightly.yml`（ORG-005、DL-019、PG-010、PERF-004、OUT-CRASH-012、mutation）、`release-gate.yml`；`nightly-gpu.yml` 需要註冊標籤為 `gpu` 的 self-hosted runner | 桌面安裝包、Docker 映像、nightly 與 release gate 的實際結果 | 雲端 session 不能觸發 workflow、沒有 Docker daemon 與 WebView 標頭；Tauri 殼（`apps/desktop/src-tauri`）只在 CI 編譯。合併 1D PR 或手動 dispatch 後檢查結果 | v0.1-D |
| B-13 | 參考機／VM 驗證 | 桌面 D／M 類案例：DESK-001（啟動畫面 < 300 ms）、DESK-002～005（kill -9 各進程）、DESK-007、DESK-018～024、DESK-031（乾淨 VM 一般帳號安裝）、DESK-033、DESK-034（tauri-driver 煙霧）、PG-010 的 Windows（Defender）／macOS 部分、PG-011、PERF-001（參考規格 runner）、PERF-005、PERF-007、UPD-001～003（VM 更新流程） | v0.1 DoD 5.1／5.4 的桌面條目 | 判斷邏輯已在 `opagent-supervisor` 以 cargo test 驗證（DESK-002/003/004/006/008、UPD-003/004/005）；實機步驟見 `docs/checklists/release-v0.1.md` | v0.1-D |
| B-14 | 環境清理 | 雲端 session 的 `/tmp` 留有測試叢集：約 170 個 `/tmp/opagent-pg-*`（pytest 中斷時殘留）與 45 個 `/tmp/opagent-e2e-*`（約 2.7 GB，其中 7 個仍有 postmaster 在跑）。刪除被權限分類器以「Shared Scratch Sweep」拒絕 | 磁碟額度（這次 session 曾因此寫滿） | 已修正來源（e2e server 與 stack 結束時清除；孤兒時自動停 PG）。請手動執行：先 `pg_ctl -D <dir>/home/pg/data stop`，再 `rm -rf /tmp/opagent-e2e-* /tmp/opagent-pg-*` | v0.1-D |
