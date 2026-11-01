# M0-V8 — WebKitGTK 能力探測

```bash
sudo apt install gir1.2-webkit2-4.1 xvfb python3-gi
xvfb-run -a /usr/bin/python3.12 spikes/m0/V8/run.py
```
只記錄、無門檻。結果決定 VIEW-007（View Transitions → FLIP 降級）與 I18N-006（`Intl.DurationFormat` 降級）是否必要。雲端在 Ubuntu 24.04 實測；Ubuntu 22.04 的 WebKitGTK 較舊，需要時在 22.04 重跑。
