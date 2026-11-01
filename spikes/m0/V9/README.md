# M0-V9 — tauri-driver 桌面 E2E 可行性（pending：需要切片 11 的桌面殼 + Windows）

1. 建置 debug 版桌面 app：`pnpm --filter desktop tauri build --debug`。
2. 安裝 `cargo install tauri-driver`；Windows 另需 `msedgedriver`（版本對應 WebView2），Linux 需要 `WebKitWebDriver`（`webkit2gtk-driver` 套件）。
3. `pnpm --filter desktop smoke -- --spec smoke/desk_034.e2e.ts --repeat 10`
4. 記錄 10 次成功數到 `spikes/m0/results/M0-V9.json`（通過 10/10；不通過時把桌面煙霧改為手動清單）。
macOS 沒有 WKWebView WebDriver（11 V-9）→ 手動清單。
