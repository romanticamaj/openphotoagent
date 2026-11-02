# 連接 AI 助手：MCP client 設定

OpenPhotoAgent 透過 MCP（Model Context Protocol）讓 Claude Desktop、Claude Code、Cursor 等 AI 助手搜尋你的照片。桌面版只走 **stdio**：AI 助手啟動 `opagent mcp`，它再把請求轉給執行中的 app。整個過程不經過網路，照片也不會離開你的電腦；只有你允許的縮圖和文字會交給 AI 助手。

> 規格：05 §3、ADR-0009。測試：09b §3。

## 最簡單的方式：app 內的精靈

**設定 → 連接 AI 助手 → 新增**：

1. 選擇 AI 助手：Claude Desktop、Claude Code 或 Cursor。
2. 選擇權限。預設**只能讀取**（`photos:read`）。
   - 勾選「允許傳送縮圖」（`photos:media`）後，縮圖會送到該 AI 助手的服務供應商。
   - 也可以把範圍限縮在某些資料夾。
3. 精靈會先顯示它要寫入的設定，按「寫入」才會寫。寫入前原設定檔會備份成 `*.opagent-backup`。
4. 存取權杖（PAT）存在系統的鑰匙圈（macOS 鑰匙圈、Windows 認證管理員、Linux Secret Service），**不會出現在設定檔中**。Linux 沒有 Secret Service 時，會改存在資料夾中權限為 0600 的檔案，精靈也會顯示警示。
5. 重新啟動 AI 助手。

精靈寫入的設定一律使用 app 內 `opagent` 的**絕對路徑**（安裝程式不會修改 PATH）。`opagent mcp` 會從資料夾的 `run/api.json` 找到 app 目前的 port，所以 app 換了 port 也不必重新設定。

## 手動設定

先確認 app 正在執行，再在「連接 AI 助手」頁建立權杖，或用 CLI 存入權杖：

```bash
# 從標準輸入讀取權杖並存進鑰匙圈（不會出現在 shell 歷史紀錄）
opagent login --token-stdin < token.txt
```

### Claude Desktop（`claude_desktop_config.json`）

| OS | 路徑 |
|---|---|
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |
| Linux | `~/.config/Claude/claude_desktop_config.json` |

```json
{
  "mcpServers": {
    "openphotoagent": {
      "command": "/Applications/OpenPhotoAgent.app/Contents/MacOS/bin/opagent",
      "args": ["mcp"]
    }
  }
}
```

Windows 的 `command` 是安裝位置的 `...\OpenPhotoAgent\bin\opagent.exe`。精靈會自動填入正確的路徑。

### Claude Code

```bash
claude mcp add openphotoagent -- /Applications/OpenPhotoAgent.app/Contents/MacOS/bin/opagent mcp
```

精靈選 Claude Code 時會寫入 `~/.claude.json` 的 `mcpServers`，效果與上面的指令相同。

### Cursor（`~/.cursor/mcp.json`）

```json
{
  "mcpServers": {
    "openphotoagent": {
      "command": "/Applications/OpenPhotoAgent.app/Contents/MacOS/bin/opagent",
      "args": ["mcp"]
    }
  }
}
```

### Docker all-in-one

在宿主機安裝 `opagent`（`uv tool install openphotoagent`），並指定 server 位址：

```json
{
  "mcpServers": {
    "openphotoagent": {
      "command": "opagent",
      "args": ["mcp", "--server", "http://127.0.0.1:8080"]
    }
  }
}
```

## `opagent mcp` 參數

| 參數 | 說明 |
|---|---|
| `--server` / `OPAGENT_SERVER` | app 的位址；省略時讀取 `run/api.json` |
| `--readonly` | 即使權杖有寫入權限，也只提供唯讀的 tools |
| `OPAGENT_TOKEN` | 改用環境變數提供權杖（優先於鑰匙圈） |

## 安全須知

- AI 助手**不能自行套用任何檔案變更**。`apply_organize` 只會建立一個核准請求，你要在 app 內核准，30 分鐘後失效。桌面版會跳出系統通知，點擊即可打開核准頁。
- 沒有刪除照片、寫入 EXIF、新增根目錄或取得 embedding 的 tool。
- 照片中的文字、檔名、EXIF 和人名一律標為不可信資料。即使如此，也請**不要讓同一個 AI 助手同時連接照片與能對外寄送的工具**（例如 email），並開啟自動核准。
- 在「連接 AI 助手」頁可以看到每個權杖最近的呼叫與送出的縮圖數量，並可隨時撤銷。撤銷後，下一次呼叫就會失敗。

## 驗證狀態

- 用 `opagent mcp` 搭配 2025-11-25 與 2026-07-28 兩個協定版本的請求，已在 CI 中驗證：MCP-TRN-001～005。
- 在 Claude Desktop、Claude Code、Cursor 的實際介面中操作，需要使用者在自己的電腦上確認（`docs/BLOCKERS.md` B-15）。
