# M0-S1a — 嵌入式 PG 啟動時間

來源：08 §9.4 S1、11 V-1、09 §6。

```bash
# Linux / macOS（需 PG 17 二進位；或設定 OPAGENT_PG_BIN）
uv run python spikes/m0/S1a/run.py --runs 50
# Windows（PowerShell；分別以一般帳號與系統管理員帳號各跑一次，Defender 保持開啟）
$env:OPAGENT_PG_BIN="C:\path\to\pg17\bin"; $env:OPAGENT_M0_MACHINE="rtx5070ti-win11-admin"
uv run python spikes/m0/S1a/run.py --runs 50
```

通過：暖啟動 p95 < 5 s；50 次 0 失敗；管理員帳號可啟動。不通過：改用 embedded-postgres 套件（08 §2.3）。
結果：`spikes/m0/results/M0-S1a.json`（雲端 Linux 只量 Linux 部分，status=`partial`）。
