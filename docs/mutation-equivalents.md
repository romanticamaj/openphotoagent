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

## fs-sandbox（2026-11-01 審查）

| 位置 | 運算子 | 為何等價 |
|---|---|---|
| executor.py `_commit(..., op="copy")` 預設值 | str | 唯一的呼叫端一律傳入 `op`，預設值從不被使用 |
| executor.py `undo` 的 `e["phase"] == "undone" and e["ref_seq"] is not None` | boolop | done 項目的 seq 只會被它的 `undone` 項目引用；改成 `or` 只會多加入 intent 的 seq 與 `None`，不影響「哪些 done 尚未 undo」 |
| fsops.py `CHUNK = 1 << 20` | num | I/O 緩衝大小；任何 chunk 大小下 hash 與複製結果的 bytes 都相同 |
| fsops.py `half = max(1, len(chunk) // 2)` | num | 只改變第一個 chunk 在測試用 crash hook 前後的切分點，寫出的 bytes 相同 |
| fsops.py `first = False` | bool | 改成 `True` 只會讓每個 chunk 都在 crash hook 前後切兩次寫入，寫出的 bytes 相同；crash hook 在產品模式下是 no-op |
| fsops.py `FileExistsError(errno.EEXIST, "exists", …)`（Linux 與 darwin 兩處） | str | 只改變 strerror 文字；例外型別與 errno 不變 |
| fsops.py `rename_excl = 0x4` | num | 位於 darwin 專屬分支內，Linux runner 無法觀察；由 macOS CI runner 執行 |
| journal.py `int(cur.lastrowid or 0)` | num | 成功 INSERT 進 AUTOINCREMENT 表後 `lastrowid` 永遠不是 `None`／0 |
| safepath.py `getattr(st, "st_file_attributes", 0)` 預設值 | num | `1 & 0x400 == 0`，沒有該屬性的 POSIX stat 仍判定為非 reparse point |
| safepath.py `missing = True` | bool | 只是略過缺少的父目錄底下的查詢；改成 `False` 時那些查詢會得到 ENOENT／listdir 失敗，回傳的名稱完全相同 |

## 執行器自動排除的類別（tools/mutation_runner.py `_excluded_ids`）

以下位置的 mutant 在此 runner 上無法改變可觀察行為，因此不產生（審查規則，2026-11-01）：

| 類別 | 為何排除 |
|---|---|
| f-string 片段、例外訊息文字（含空白的字串參數） | 只影響訊息內容；錯誤碼與 reason 欄位（不含空白）仍會被 mutate 並由測試檢查 |
| decorator 的 keyword 參數（如 `@dataclass(frozen=True)`） | frozen 只影響不可變性；不可變性另由 CORE-TPL 的 frozen 測試覆蓋 |
| 型別標註（函式參數、回傳、`AnnAssign`、`Literal[...]` 別名） | 執行期不參與運算 |
| 含 `sys.platform` 的比較 | 該分支只在對應 OS 上可觀察；由 CI OS 矩陣（Windows／macOS runner）執行對應測試 |
