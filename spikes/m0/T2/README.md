# M0-T2 — 硬體探針錄製

在每台參考機與可取得的 AMD／Intel／無 GPU 機器上：
```bash
uv run python spikes/m0/T2/record.py --profile rtx5070ti-win11   # 參考機 1
uv run python spikes/m0/T2/record.py --profile rtx4060-laptop    # 參考機 2
uv run python spikes/m0/T2/record.py --profile m2-air-16         # 參考機 3
```
產出 `fixtures/hw/<profile>.yaml`（原始探針輸出，`source: recorded`）。無法取得實機的 profile 由 09 §3.4 規格合成，`source: synthesized-from-docs`，並在檔頭註明。
雲端 session 已實錄 `cloud-linux-nogpu`（無 GPU 的 Linux 容器）。
