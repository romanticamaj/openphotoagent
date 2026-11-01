# M0-S2 — 安裝包大小

雲端（Linux 組件估算）：
```bash
uv python install 3.12
uv run python spikes/m0/S2/run.py --python-home "$(dirname $(dirname $(uv python find 3.12)))" \
  --pg-dirs /usr/lib/postgresql/16,/usr/share/postgresql/16
```
權威量測：`desktop-build.yml` 產出的實際 AppImage／NSIS／dmg（DESK-030，CI 硬上限 200 MB）。有實際安裝包時：`--app-bundle <file>`。
不通過：移出 GeoNames（08 §4.1）。
