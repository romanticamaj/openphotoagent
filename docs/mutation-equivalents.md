# 等價 mutant 審查紀錄（09 §5.6）

`mutmut-equivalents.txt` 列出經審查確認「行為等價」的 mutant，計分時排除。行號會隨程式碼變動，變動後重新審查。

| 位置 | 運算子 | 為何等價 |
|---|---|---|
| path_template.py `DateSource = Literal[...]` | str | 只用於型別標註，執行期無作用 |
| `_graphemes` 的 `join_next = False` 初值 | bool | 第一個字元時 `out` 為空，`out and …` 已為假，初值不影響結果 |
| `compile_template` 的 `_TOKEN.sub("x", …)` 佔位字串 | str | 佔位內容只需非空且不含 `/`、`\`，換成其他非空字串結果相同 |
| `skeleton.startswith("/")` | str | 以 `/` 開頭的樣板，下一行的分段檢查會因第一段為空字串而同樣拒絕 |
| `if m.start() > pos` | cmp | 改成 `>=` 只會多加空字串 literal，渲染結果不變 |
| `var == "taken_at" and fmt` | boolop | 其他變數的 fmt（目前只有 tag prefix）不含 `%` 時 strftime 檢查必定通過；含 `%` 的 tag prefix 不是合法 tag key |
| `if pos < len(template)` | cmp | 同上，只多一個空字串 literal |
| `assert … , "either taken_at or mtime is required"` 訊息 | str | 只改變 assert 訊息 |
| `len(last.encode()) > MAX_SEGMENT_BYTES` | cmp | 恰好 255 bytes 時走截斷路徑，`truncate_bytes(…, 255)` 回傳原字串，結果相同 |
| `values.ext and last.endswith(values.ext)` | boolop | `ext==""` 時兩種寫法都得到 `ext=""` |
| `key.rsplit(".", 1)[-1]` | num | maxsplit 1 或 2 取最後一段結果相同 |

## 執行器自動排除的類別（tools/mutation_runner.py `_excluded_ids`）

以下位置的 mutant 在此 runner 上無法改變可觀察行為，因此不產生（審查規則，2026-11-01）：

| 類別 | 為何排除 |
|---|---|
| f-string 片段、例外訊息文字（含空白的字串參數） | 只影響訊息內容；錯誤碼與 reason 欄位（不含空白）仍會被 mutate 並由測試檢查 |
| decorator 的 keyword 參數（如 `@dataclass(frozen=True)`） | frozen 只影響不可變性；不可變性另由 CORE-TPL 的 frozen 測試覆蓋 |
| 型別標註（函式參數、回傳、`AnnAssign`、`Literal[...]` 別名） | 執行期不參與運算 |
| 含 `sys.platform` 的比較 | 該分支只在對應 OS 上可觀察；由 CI OS 矩陣（Windows／macOS runner）執行對應測試 |
