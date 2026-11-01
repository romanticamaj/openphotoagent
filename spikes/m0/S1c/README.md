# M0-S1c — Windows AF_UNIX（pending-hardware）

在 Windows 參考機：以 `unix_socket_directories='C:/opagent/sock'` 啟動 PG 17，然後
```powershell
uv run --with asyncpg,"psycopg[binary]" python spikes/m0/S1c/run.py --sockdir C:/opagent/sock --port 5432
```
通過：asyncpg 與 psycopg 各 10,000 次 0 錯誤 → 08 §1.3 可改用 AF_UNIX；否則維持 127.0.0.1 + SCRAM（目前預設）。
