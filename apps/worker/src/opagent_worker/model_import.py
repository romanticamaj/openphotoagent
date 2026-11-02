"""Find models already on this computer (06 §1.8; MDL-IMP-001/002): Ollama's store or a folder the user picked.

Only reads: nothing is copied or moved, and files stay where they are (the installation is marked unmanaged).
Hashes are computed here so the API can match them against pinned registry variants. Pickle-based weights
(``.bin``, ``.pt``, ``.pth``, ``.ckpt``, ``.pkl``) are listed as rejected, never offered (invariant 10).
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

SAFE = {".gguf": "gguf", ".safetensors": "safetensors", ".onnx": "onnx"}
PICKLE = {".bin", ".pt", ".pth", ".ckpt", ".pkl", ".pickle"}
MAX_DEPTH = 4


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _file(p: Path) -> dict[str, Any]:
    return {"path": str(p), "sha256": sha256_file(p), "size": p.stat().st_size}


def default_ollama_dir() -> Path:
    env = os.environ.get("OLLAMA_MODELS")
    return Path(env) if env else Path.home() / ".ollama" / "models"


def scan_ollama(root: Path) -> dict[str, Any]:
    out: list[dict[str, Any]] = []
    man_root = root / "manifests"
    if not man_root.is_dir():
        return {"candidates": [], "rejected": [], "source": "ollama", "found": False}
    for man in sorted(p for p in man_root.rglob("*") if p.is_file()):
        try:
            doc = json.loads(man.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        layers = {(lay.get("mediaType") or "").rsplit(".", 1)[-1]: lay for lay in doc.get("layers") or []}
        model = layers.get("model")
        if model is None:
            continue

        def blob(layer: dict[str, Any]) -> Path:
            return root / "blobs" / str(layer["digest"]).replace(":", "-")

        if not blob(model).is_file():
            continue
        cand: dict[str, Any] = {
            "id": f"ollama:{man.parent.name}:{man.name}",
            "name": f"{man.parent.name}:{man.name}",
            "format": "gguf",
            "weights": _file(blob(model)),
        }
        proj = layers.get("projector")
        if proj is not None and blob(proj).is_file():
            cand["mmproj"] = _file(blob(proj))
        out.append(cand)
    return {"candidates": out, "rejected": [], "source": "ollama", "found": True}


def scan_folder(root: Path) -> dict[str, Any]:
    cands: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    base_depth = len(root.parts)
    ggufs: dict[Path, list[Path]] = {}
    for dirpath, dirs, files in os.walk(root, followlinks=False):
        if len(Path(dirpath).parts) - base_depth >= MAX_DEPTH:
            dirs[:] = []
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for name in sorted(files):
            p = Path(dirpath) / name
            ext = p.suffix.lower()
            rel = p.relative_to(root).as_posix()
            if ext in PICKLE:
                rejected.append({"path": rel, "reason": "PICKLE_REJECTED"})
            elif ext == ".gguf":
                ggufs.setdefault(p.parent, []).append(p)
            elif ext in SAFE:
                cands.append({"id": f"folder:{rel}", "name": rel, "format": SAFE[ext], "weights": _file(p)})
    for folder, items in sorted(ggufs.items()):
        projs = [p for p in items if p.name.lower().startswith("mmproj")]
        for w in (p for p in items if p not in projs):
            c: dict[str, Any] = {"id": f"folder:{w.relative_to(root).as_posix()}", "name": w.name, "format": "gguf"}
            c["weights"] = _file(w)
            if projs:
                c["mmproj"] = _file(projs[0])
            cands.append(c)
    return {"candidates": sorted(cands, key=lambda c: c["id"]), "rejected": rejected, "source": "folder", "found": True}
