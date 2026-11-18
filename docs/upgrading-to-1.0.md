# 從 v0.x 升級到 1.0

> 1.0 是第一個做出相容性承諾的版本（VERSIONING §4）。在那之前的 v0.x 都是預覽版本。以下列出從任何 v0.x 升級時你會看到的變化，每一項都附有 `Migration:` 說明。

## 自動完成

- **資料庫**：第一次啟動 1.0 時，會先做一份 `pre-migration` 備份，再把任何 v0.x 的資料庫補齊到 1.0 的 schema（migration `0002`）。補齊只會新增，不會刪除或改寫資料；已用 v0.1～v0.5 各版的真實 schema 測試過（MIG-001）。
  Migration: 不需要動作。若升級失敗，備份在「設定 → 資料」，可從那裡還原。
- **桌面資料夾**：寫入 `layout.json`，版本為 1。`run/` 與 `secrets/` 收緊為只有本人可讀（0700），裡面的檔案為 0600。不會搬動任何檔案；修改前會先在 `backups/layout-0-…` 備份。
  Migration: 不需要動作。若資料夾是由更新的版本建立的，app 會拒絕啟動，並給出「從備份還原」或「安裝新版」兩個選項（`DATA_LAYOUT_TOO_NEW`）。

## 你可能會注意到的行為改變

- **搜尋結果可以用鍵盤操作**（10 §5.5）：方向鍵移動焦點，Space 選取，Shift＋方向鍵連續選取，Enter 用 Lightbox 打開照片。
  Migration: 以前在搜尋結果按 Enter 是選取，現在改成 Space。
- **錯誤卡片會說明每個替代方案的代價**。修復按鈕下方會顯示「選這個會怎樣」。

## 介面整合者（REST、MCP、Worker）

- **REST `/api/v1`**：從 1.0 開始凍結（`contracts/openapi-v1.json`），之後只會新增。
  Migration: 依賴 v0.x 未公開欄位的整合，請改用快照中列出的欄位。
- **problem+json**：細節欄位如果和 RFC 9457 的成員同名（例如計畫的 `status`），現在放在 `params` 裡。以前這種情況會造成 500 錯誤（PF-085）。
  Migration: 改從 `params.status` 讀取計畫狀態。
- **修復選項的代價**：key 從 `fix.<id>.consequence` 改為 `fix_consequence.<id>`；含 `.` 或 `-` 等字元的修復 id，現在只保留英數與底線（例如 `compose__f_compose_cuda_yml`）。
  Migration: 自行顯示修復選項的 client，請改用 `fix.consequence.key` 指定的 key。
- **MCP tool manifest 1.0.0**（`contracts/mcp-manifest.json`）：之後不會移除或改名任何 tool，也不會新增必填參數。`organize:apply` 不會發給任何 MCP token，OAuth 也一樣。
  Migration: 不需要動作。
- **Worker 協定 `"1.0"`**（ADR-0017）：CP 服務主版本 N 與 N-1；更舊的 Worker 會收到 `upgrade_required`。
  Migration: 先升級所有遠端 Worker，再升級 control plane。
- **更新通道**：stable 只提供正式版，beta 會同時提供預覽版與正式版。從 beta 切回 stable 不會降級，會等到下一個正式版。
  Migration: 不需要動作。
