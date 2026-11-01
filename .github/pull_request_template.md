<!-- PR 標題就是 squash 後的 Conventional Commit（例如 `feat(fs-sandbox): reject symlink escape`） -->

## 變更
- 能力：11 #__（DoD __）
- 測試 ID：____（新增 __／修改 __）

## TDD
- [ ] 先寫的測試在實作前是紅燈，失敗原因是預期斷言（貼一行失敗摘要）：
- [ ] 沒有刪除、跳過（skip／xfail／only）、放寬任何既有測試或門檻；若有，已在下方說明並取得 reviewer 明確同意
- [ ] 沒有修改 golden 檔；若有，commit footer 有 `Golden-Update:` 並說明原因
- [ ] 已執行 `tools/tc_status.py` 並提交 `docs/generated/coverage-status.md`

## 安全與資料
- [ ] 會移動或刪除檔案的程式碼走 plan → approve → journal → execute（02 §6.2），並有 OUT-CRASH 類案例
- [ ] 新的錯誤狀態都有 06 §4.7 錯誤碼、i18n 文案與至少一個 fix（PF-071、PF-078）
- [ ] 不可信文字（照片文字、EXIF、檔名、人名）以 `UntrustedText` 傳遞（05 §3.9）
- [ ] 沒有真實網路、真實時間（`time.sleep`、`datetime.now()`）或真模型進入 PR 測試

## 覆蓋
- [ ] diff coverage ≥ 90%；core／fs-sandbox branch ≥ 95%
- [ ] fs-sandbox／path_template 變更已跑 `just mutate-diff`（score ≥ 85%）

## 待硬體／人工驗證
<!-- 列出 pending-hardware、需要在參考機手動驗證的項目 -->
