"""Desktop data-folder layout (VERSIONING §4 ``layout_version``; 09b §9.4 MIG-002).

``<data dir>/layout.json`` records the layout version. On start-up the shell's sidecar calls ``ensure_layout``:

* a new folder gets the current version;
* an older one is migrated **after** a backup of the small files the migration may touch (``backups/layout-…``,
  with a manifest), one step at a time, each step idempotent;
* a folder written by a newer app is refused (``DATA_LAYOUT_TOO_NEW``) with a way out — restore a backup or install
  the newer version — and nothing is changed.

Steps:
* 0 → 1 (v1.0): folders and files older releases created with the process umask are tightened — ``run/`` and
  ``secrets/`` to 0700, the run file and stored tokens to 0600. Nothing is moved or deleted.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

LAYOUT_VERSION = 1
FILE = "layout.json"


class LayoutTooNew(Exception):
    code = "DATA_LAYOUT_TOO_NEW"

    def __init__(self, found: int) -> None:
        super().__init__(f"data folder layout {found} is newer than this app ({LAYOUT_VERSION})")
        self.found = found
        from opagent_doctor.codes import problem

        self.fixes = problem(self.code, {"found": found})["fixes"]


def _tighten(path: Path, want: int) -> None:
    if path.exists() and stat.S_IMODE(path.stat().st_mode) & ~want:
        os.chmod(path, want)


def _step_0_to_1(home: Path) -> list[str]:
    touched = []
    for d in ("run", "secrets"):
        p = home / d
        if p.is_dir():
            _tighten(p, 0o700)
            touched.append(d)
            for f in p.iterdir():
                if f.is_file():
                    _tighten(f, 0o600)
                    touched.append(f"{d}/{f.name}")
    return touched


STEPS: dict[int, Callable[[Path], list[str]]] = {0: _step_0_to_1}
# what a step may touch, backed up first (small files only: never the database, thumbnails or models)
BACKUP: dict[int, list[str]] = {0: ["run", "secrets"]}


def current(home: Path) -> int | None:
    f = Path(home) / FILE
    if f.exists():
        return int(json.loads(f.read_text()).get("layout_version", 0))
    if any(Path(home).iterdir()) if Path(home).is_dir() else False:
        return 0  # a v0.x folder: no layout file yet
    return None


def _backup(home: Path, version: int) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")  # noqa: TID251 - a file name, not logic
    dst = home / "backups" / f"layout-{version}-{stamp}"
    dst.mkdir(parents=True, exist_ok=False, mode=0o700)
    files = []
    for rel in BACKUP.get(version, []):
        src = home / rel
        if src.is_dir():
            for f in src.rglob("*"):
                if f.is_file():
                    r = f.relative_to(home).as_posix()
                    (dst / r).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, dst / r)
                    files.append(r)
    (dst / "manifest.json").write_text(json.dumps({"from": version, "files": files}, indent=2))
    return dst


def _write(home: Path, version: int) -> None:
    tmp = home / f".{FILE}.tmp"
    tmp.write_text(json.dumps({"layout_version": version}))
    tmp.replace(home / FILE)


def ensure_layout(home: Path) -> dict[str, Any] | None:
    """Bring the folder to ``LAYOUT_VERSION``. Returns what happened, or ``None`` when it was already current."""
    home = Path(home)
    found = current(home)
    if found == LAYOUT_VERSION:
        return None
    if found is not None and found > LAYOUT_VERSION:
        raise LayoutTooNew(found)
    home.mkdir(parents=True, exist_ok=True)
    if found is None:
        _write(home, LAYOUT_VERSION)
        return {"from": None, "to": LAYOUT_VERSION, "backup": None}
    backup = _backup(home, found)
    v = found
    while v < LAYOUT_VERSION:
        STEPS[v](home)
        v += 1
        _write(home, v)  # each completed step is recorded: a crash resumes from the last one
    return {"from": found, "to": LAYOUT_VERSION, "backup": backup.relative_to(home).as_posix()}
