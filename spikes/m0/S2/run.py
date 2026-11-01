"""M0-S2 — installer size (docs/09 §6, 08 S2 / §4.1, 11 V-4).

Measures the compressed size of each installer component that can be built on
this machine and adds them up. Components that cannot be built here (Tauri
shell, SPA bundle, llama-server, GeoNames) are reported separately as
*estimates* (08 §4.1 upper bounds) and never mixed into the measured total.

    uv run python spikes/m0/S2/run.py --python-home <python-build-standalone dir> \
        --pg-dirs /usr/lib/postgresql/17,/usr/share/postgresql/17 [--llama-dir build/bin] [--app-bundle <AppImage>]

The authoritative check is DESK-030 in desktop-build.yml (real AppImage/NSIS/dmg ≤ 200 MB).
"""

from __future__ import annotations

import argparse
import io
import lzma
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import write_result  # noqa: E402

MB = 1024 * 1024
ESTIMATE_UPPER_MB = {"tauri_shell": 15, "spa_assets": 5, "llama_server_cpu": 15, "geonames": 15}


def xz_size(paths: list[Path], strip_tests: bool = True) -> tuple[float, float]:
    """Returns (raw MB, xz -6 compressed MB) of a tar of the given paths."""
    buf = io.BytesIO()
    raw = 0
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for root in paths:
            for dirpath, dirnames, files in os.walk(root):
                if strip_tests:
                    dirnames[:] = [d for d in dirnames if d not in {"__pycache__", "tests", "test", "idlelib", "tkinter", "turtledemo", "ensurepip"}]
                for f in files:
                    p = Path(dirpath, f)
                    if p.is_symlink() or not p.is_file():
                        continue
                    raw += p.stat().st_size
                    tf.add(p, arcname=str(p.relative_to(root.parent)))
    comp = lzma.compress(buf.getvalue(), preset=6)
    return raw / MB, len(comp) / MB


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--python-home", type=Path, required=True)
    ap.add_argument("--pg-dirs", default="")
    ap.add_argument("--llama-dir", type=Path)
    ap.add_argument("--app-bundle", type=Path, help="a real installer; when given it is the authoritative measurement")
    a = ap.parse_args()
    m: dict[str, object] = {}
    measured_total = 0.0
    # 1. CPython
    raw, comp = xz_size([a.python_home])
    m["cpython"] = {"raw_mb": round(raw, 1), "xz_mb": round(comp, 1)}
    measured_total += comp
    # 2. Python deps installed into a fresh target dir
    tgt = Path(tempfile.mkdtemp(prefix="m0s2-deps-"))
    py = next(p for p in sorted((a.python_home / "bin").glob("python3*")) if not p.name.endswith("-config"))
    subprocess.run(["uv", "pip", "install", "-q", "--python", str(py), "--target", str(tgt),
                    "-r", str(Path(__file__).with_name("requirements-sidecar.txt"))], check=True)
    ort = [tgt / "onnxruntime"]
    others = [p for p in tgt.iterdir() if p.is_dir() and p.name != "onnxruntime"]
    raw, comp = xz_size(ort)
    m["onnxruntime_cpu"] = {"raw_mb": round(raw, 1), "xz_mb": round(comp, 1)}
    measured_total += comp
    raw, comp = xz_size(others)
    m["python_deps_excl_ort"] = {"raw_mb": round(raw, 1), "xz_mb": round(comp, 1)}
    measured_total += comp
    # 3. PostgreSQL
    if a.pg_dirs:
        pg_paths = [Path(p) for p in a.pg_dirs.split(",") if Path(p).exists()]
        raw, comp = xz_size(pg_paths, strip_tests=False)
        m["postgres_pgvector"] = {"raw_mb": round(raw, 1), "xz_mb": round(comp, 1), "dirs": [str(p) for p in pg_paths]}
        measured_total += comp
    estimates = dict(ESTIMATE_UPPER_MB)
    if a.llama_dir:
        raw, comp = xz_size([a.llama_dir])
        m["llama_server"] = {"raw_mb": round(raw, 1), "xz_mb": round(comp, 1)}
        measured_total += comp
        estimates.pop("llama_server_cpu")
    m["measured_total_xz_mb"] = round(measured_total, 1)
    m["estimates"] = {"label": "08 §4.1 upper-bound estimates for components not built here (NOT measured)", **estimates}
    m["projected_total_mb"] = round(measured_total + sum(estimates.values()), 1)
    if a.app_bundle:
        m["app_bundle_mb"] = round(a.app_bundle.stat().st_size / MB, 1)
        write_result("M0-S2", status="measured", metrics=m, passed=m["app_bundle_mb"] <= 200, notes="real installer measured")
    else:
        write_result("M0-S2", status="partial", metrics=m, passed=None,
                     notes="Linux component sizes measured (xz -6 of tar, tests/pycache stripped). Tauri shell, SPA, "
                           "llama-server and GeoNames are estimates until desktop-build.yml produces real installers (DESK-030). "
                           "PG dirs measured are the distro PG16 build (desktop ships PG17 pack).")
    print(m)


if __name__ == "__main__":
    main()
