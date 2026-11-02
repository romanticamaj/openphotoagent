"""Build a runtime pack archive reproducibly (08 §4.3; RT-PACK-014).

Same input tree → byte-identical ``.tar.zst``: entries sorted, uid/gid 0, mtime 0, fixed modes (0644, or 0755 when
executable), no symlinks, hard links, absolute paths or ``..``. Prints the index fields (size, sha256,
unpacked_size, files_manifest) the release job merges into ``packs-index.json``.

Usage: ``python tools/build_pack.py SRC_DIR OUT.tar.zst``
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import sys
import tarfile
from pathlib import Path
from typing import Any

import zstandard


class BuildError(ValueError):
    pass


def _walk(src: Path) -> list[Path]:
    out = []
    for dirpath, dirs, files in os.walk(src, followlinks=False):
        dirs.sort()
        for name in sorted(files) + [d for d in dirs if (Path(dirpath) / d).is_symlink()]:
            p = Path(dirpath) / name
            st = p.lstat()
            if stat.S_ISLNK(st.st_mode):
                raise BuildError(f"symlink not allowed in a pack: {p.relative_to(src)}")
            if not stat.S_ISREG(st.st_mode):
                raise BuildError(f"not a regular file: {p.relative_to(src)}")
            if st.st_nlink > 1:
                raise BuildError(f"hard link not allowed in a pack: {p.relative_to(src)}")
            out.append(p)
    return sorted(out, key=lambda p: p.relative_to(src).as_posix())


def build_pack(src: Path, out: Path) -> dict[str, Any]:
    src = Path(src)
    buf = io.BytesIO()
    manifest = []
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as t:
        for p in _walk(src):
            rel = p.relative_to(src).as_posix()
            if rel.startswith("/") or ".." in rel.split("/"):
                raise BuildError(f"unsafe path: {rel}")
            data = p.read_bytes()
            info = tarfile.TarInfo(rel)
            info.size = len(data)
            info.mtime = 0
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mode = 0o755 if os.access(p, os.X_OK) else 0o644
            t.addfile(info, io.BytesIO(data))
            manifest.append({"path": rel, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    blob = zstandard.ZstdCompressor(level=19, write_checksum=True, threads=0).compress(buf.getvalue())
    Path(out).write_bytes(blob)
    return {
        "size": len(blob),
        "sha256": hashlib.sha256(blob).hexdigest(),
        "unpacked_size": sum(m["size"] for m in manifest),
        "files_manifest": manifest,
        "files_manifest_sha256": hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest(),
    }


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    print(json.dumps(build_pack(Path(argv[0]), Path(argv[1])), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
