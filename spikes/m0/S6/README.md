# M0-S6 — 簽章流水線演練（需要使用者帳號與憑證 → BLOCKERS）

| 項目 | 需要什麼 | 誰 |
|---|---|---|
| macOS notarize | Apple Developer Program（每年 99 美元）、Developer ID Application 憑證、App Store Connect API key；GitHub secrets：`APPLE_CERTIFICATE`、`APPLE_CERTIFICATE_PASSWORD`、`APPLE_API_KEY`、`APPLE_API_ISSUER`、`APPLE_API_KEY_ID` | 使用者 |
| Windows SignPath | 送出 SignPath Foundation OSS 申請（需 repo 公開、有 release、維護者身分驗證） | 使用者 |
| updater／pack 簽章 | `tauri signer generate` 產生 minisign 金鑰；私鑰放 `TAURI_SIGNING_PRIVATE_KEY` secret；公鑰寫進 `tauri.conf.json` | 使用者（私鑰不可進 repo） |

演練步驟（取得帳號後）：
1. 設好上列 secrets。
2. 手動觸發 `desktop-build.yml`（`workflow_dispatch`，`sign=true`）。
3. 下載 dmg，在乾淨 Mac 上 `spctl -a -vv -t install <app>` 應顯示 `accepted, source=Notarized Developer ID`；`codesign --verify --deep --strict` 對 `Contents/Resources` 內所有 dylib 都通過。
4. 把結果填進 `spikes/m0/results/M0-S6.json`（`pass` = notarize 通過）。
