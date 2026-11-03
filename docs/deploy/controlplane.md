# 自架 control plane（v0.5）

> 對應 02 §1.1、§5.2、§6.3，ADR-0015（OAuth）、ADR-0016（每個 workspace 一個資料庫）。

control plane（CP）放在一直開著的機器上，例如 NAS。它只保存 metadata、結果、依政策上傳的縮圖，以及使用者與權限。**照片原檔和 GPU 都留在 Worker 那台機器上**：Worker 只會主動往外連到 CP，自己不開任何 port。

## 1. 啟動 CP

```bash
cp deploy/compose/controlplane.env.example deploy/compose/controlplane.env   # 編輯：網址、密鑰、第一個 owner
OPAGENT_DB_PASSWORD=… docker compose -f deploy/compose/controlplane.yml up -d
```

- 在前面放一層 HTTPS reverse proxy（Caddy、Traefik、Synology 內建的都可以），`OPAGENT_PUBLIC_URL` 填這個 https 網址。session cookie 帶有 `Secure`，MCP 的 OAuth 也要求 https。
- `OPAGENT_OWNER_EMAIL`／`OPAGENT_OWNER_PASSWORD` 只在資料庫還沒有任何帳號時使用。若是從單人資料庫升級，原本的 owner 會成為 `org_owner`，資料原樣保留（MU-AUTH-003）。
- OIDC（選用）：設定 `OPAGENT_OIDC_ISSUER`、`_CLIENT_ID`、`_CLIENT_SECRET`，callback 是 `<網址>/api/v1/auth/oidc/callback`。成員要先由管理員依 email 邀請；用 OIDC 登入不會自動建立帳號。

## 2. 配對 Worker

1. 在 UI 的「設定 → Worker → 新增」取得一次性配對碼（`OPA-XXXX-XXXX`，10 分鐘內有效，只能用一次）。
2. 在 GPU 機器上執行：

```bash
opagent worker enroll --server https://photos.example.lan --code OPA-XXXX-XXXX
opagent worker roots add photos /mnt/photos --mode ro      # 根目錄只能在這台機器上設定
opagent worker roots add sorted /mnt/sorted --mode rw
opagent worker run --headless
```

或使用 `deploy/compose/worker.yml`，指令寫在檔案開頭。

- 私鑰存在 `worker.key`（權限 0600），不會離開這台機器。
- `--egress-floor results-only` 可以把上傳下限鎖在本機：就算 CP 被竄改，Worker 送出的資料也不會比這個政策更多。

## 3. 隱私政策（每個 workspace 各自設定）

| 模式 | CP 會收到 |
|---|---|
| `full` | metadata、EXIF、縮圖、embedding、結果 |
| `thumbs`（預設） | 同上；縮圖最大 512 px，可剝除 GPS |
| `edge-thumbs` | 不收縮圖；瀏覽器要看縮圖時經 Worker 中繼，CP 不儲存 |
| `results-only` | 只收路徑 hash 與結果；沒有縮圖、embedding、GPS、檔名明文 |

這些規則在 **Worker 端**執行（MU-PRIV-005）。

## 4. 多個 workspace

- 每個 workspace 都是同一個 Postgres 裡的獨立資料庫（ADR-0016）。看不到的 workspace 和不存在的 workspace，回應完全一樣。
- 一個 Worker 只服務一個 workspace。要服務多個，就在同一台機器上各配對一次，每次使用不同的 `--home`。

## 5. AI 助手（MCP over HTTP）

MCP client 連到 `<網址>/mcp`，依序經過 401 → `/.well-known/oauth-protected-resource` → AS metadata → 瀏覽器同意頁 → token 完成授權。

- 同意頁顯示 client 網址所屬的網域。
- `organize:apply` 永遠不會發出：套用變更只能由人在 UI 核准。
- 在「設定 → 已授權的 AI」撤銷後，該 client 的下一個呼叫就會失敗。

## 6. 桌面 app 當 client

在資料夾 `connection.json` 寫入 `{"mode": "remote", "url": "https://photos.example.lan"}`。桌面 app 就不會啟動本機的資料庫、API 或 Worker，視窗直接開啟 CP 的網頁並在那裡登入，而且只能停留在 CP 的網域內。
