# 發佈管道評估：winget、Homebrew、Flathub（DIST-002，2026-11-02）

> manifest 由 `tools/gen_dist_manifests.py` 從 release metadata 產生（DIST-001）。實際上架需要維護者的帳號與審核，屬於使用者決定（`docs/BLOCKERS.md` B-8、B-18）。標【未驗證】的項目是依撰寫時的了解，送審前要再確認。

| | winget（Windows） | Homebrew Cask（macOS） | Flathub（Linux） |
|---|---|---|---|
| 送審方式 | 對 `microsoft/winget-pkgs` 開 PR，放入三個 manifest 檔（version／installer／defaultLocale，schema 1.6） | 對 `Homebrew/homebrew-cask` 開 PR（或自建 tap `romanticamaj/homebrew-tap`，不需審核） | 對 `flathub/flathub` 開新 app 的 PR，審核通過後在 `flathub/<app-id>` 建立 repo |
| 審核 | 自動驗證（安裝、SmartScreen、病毒掃描）＋ 人工審閱【未驗證：時程約數天】 | CI 的 `brew audit --cask`，維護者審閱；需要一定的知名度門檻才收進官方 cask【未驗證】 | 人工審閱，要求完整的 metadata（AppStream、桌面檔、圖示）與合理的 sandbox 權限 |
| 簽章需求 | 安裝檔強烈建議 Authenticode 簽章（未簽章會被 SmartScreen 擋），對應 08 的 SignPath 申請（B-3） | 需要 Developer ID 簽章＋公證，否則 Gatekeeper 阻擋（B-4） | Flatpak 由 Flathub 建置與簽章；不需要我們的 GPG 金鑰 |
| 更新 | 每版一個 PR；可用 `wingetcreate` 或 GitHub Action 自動開 PR | 每版更新 `version`／`sha256`；可用 `brew bump-cask-pr` 自動化 | Flathub 的 x-checker-data 可自動偵測新 release 並開 PR |
| 對我們的限制 | Tauri 的 NSIS 安裝檔為 `nullsoft` 類型；使用者層級安裝（Scope: user） | 只支援 Apple Silicon（`depends_on arch: :arm64`），對應 08 的 Intel Mac 不支援 | AppImage 重新包裝：需要 WebKitGTK（GNOME runtime 已提供）；`--filesystem` 權限要限縮到使用者選的照片資料夾【未驗證：portal 的檔案選擇器能否用於長期 root】 |
| 建議 | v0.4 先在 release 附上產生的 manifest；簽章到位後送 winget | 先用自建 tap（立即可用），知名度足夠後送官方 cask | 先等 Linux 的檔案權限模型確認（portal vs `xdg-pictures`），再送審 |

## 建議順序
1. 自建 Homebrew tap（不需要第三方審核）。
2. winget（Windows 程式碼簽章到位後；B-3）。
3. Flathub（確認 sandbox 權限設計後）。
