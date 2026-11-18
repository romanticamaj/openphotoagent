# v1.0 安全審查材料（09b §9.2）

雲端 session 已完成的自動化部分。外部審查（SEC-AUDIT-001）以這份文件為起點，見 BLOCKERS B-20。

| 項目 | 測試 | 結果（2026-11-03，本機實跑） |
|---|---|---|
| 路徑沙箱 fuzz | SEC-FUZZ-001 | PR CI 跑 10,000 例，nightly 跑 1,000,000 例，**0 逃逸**（本機 19 s）。輸入涵蓋 `..` 的各種寫法、絕對路徑與磁碟代號、UNC、反斜線、NUL 與控制字元、Unicode 相似字與正規化、Windows 裝置名、ADS、超長名稱；測試根目錄內含向外、向內與循環的 symlink |
| MCP 紅隊（確定性部分） | SEC-REDTEAM-001 | 110 條：20 種 payload × 5 個載體（caption、檔名、資料夾名、EXIF、相簿名），另加 10 條控制案例（越權 scope、偽造核准、跨資料夾、竄改 cursor 與 selection）。結果：payload 只出現在 UntrustedText 內，沒有控制字元，未經核准的寫入 = 0 |
| MCP 紅隊（agent 迴圈） | MCP-INJ agent 半部 | 需要真的本地 LLM（B-16） |
| 人臉刪除可驗證 | SEC-FACE-001 | 刪除報告附雜湊與 Worker 任務 id；`POST /api/v1/faces/purge/verify` 重新檢查：雜湊、資料庫、`bio` schema、Worker 已銷毀金鑰與 crop。刪除後以 `pg_dump` 做的備份不含 `bio` 物件；報告被改寫時驗證失敗 |
| SBOM 與漏洞掃描 | SEC-SBOM-001 | CycloneDX 1.5，涵蓋 uv.lock、pnpm-lock、Cargo.lock。首次掃描發現 `maplibre-gl` 5.6.0 有 **Critical** 等級的 GHSA-jrc7-96c5-q579（XSS sanitizer bypass），已升級到 6.16.0。目前沒有會擋下發版的項目；兩個 Moderate 都在只供開發用的 Storybook 依賴（glob、minimatch）。掃描器無法執行時視為失敗。release-gate 的 `supply-chain` job 會執行這項掃描 |
| control plane 認證 | MU-SEC-001～004、MU-OAUTH-001～008 | Worker 冒充、token 重放、跨 workspace 隔離、RPC 白名單、OAuth（PKCE S256、CIMD SSRF、refresh 重用撤銷、`organize:apply` 永不發出）全部綠燈 |

建議外部審查的重點：
- `packages/fs-sandbox`：SafePath 與 executor。
- `apps/api/src/opagent_api/multiuser.py`、`oauth.py`、`mcp_server.py`。
- `apps/worker/src/opagent_worker/remote.py`：EgressFilter。
- `apps/api/src/opagent_api/faces.py`：purge 與 verify。
