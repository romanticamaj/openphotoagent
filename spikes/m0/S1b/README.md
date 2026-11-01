# M0-S1b — kill -9 恢復

```bash
uv run python spikes/m0/S1b/run.py --kills 20       # 目前只涵蓋 postgres
```
通過：每次 ≤ 30 s 恢復、0 遺失。api／worker／llama-server 需要切片 11 的桌面殼監管器；完成後在三平台各跑一次（Windows 參考機必跑）。
