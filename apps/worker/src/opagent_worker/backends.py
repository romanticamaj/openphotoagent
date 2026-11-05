"""The real adapters for the models on this machine (03 §2.4, 08 §3). ``--backend fake`` is for tests; this is what the
desktop worker runs. It decides from the files on disk, so a remote worker and a desktop one behave alike, and a model
counts only once every file of its variant has landed. Nothing starts here: adapters load on first use.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from opagent_registry.loader import LoadedModel, Registry, load_registry

from .downloads import _cache_dir

LLAMA = "llama-server.exe" if os.name == "nt" else "llama-server"
TEXT_LENGTH = 64  # SigLIP2 was trained with fixed-length, max_length-padded text


@dataclass
class LocalBackends:
    vlm: Any = None
    embedder: Any = None
    faces: Any = None
    problems: list[str] = field(default_factory=list)


def snapshot_path(root: Path, repo: str, revision: str, path: str) -> Path:
    return _cache_dir(root, repo) / "snapshots" / revision / path


def _files(root: Path, m: LoadedModel, variant: str) -> dict[str, Path] | None:
    """role → path, or None unless every file of the variant is there."""
    out: dict[str, Path] = {}
    for f in m.pinned_files(variant):
        p = snapshot_path(root, f.repo, m.revision(variant), f.path)
        if not p.is_file():
            return None
        out[f.role or f.path] = p
    return out


def _installed(root: Path, reg: Registry, kind: str) -> list[tuple[LoadedModel, str, dict[str, Path]]]:
    """Largest first: the recommender only downloads what fits, so the biggest one present is the one chosen."""
    found = []
    for m in reg.models.values():
        if m.status != "valid" or m.spec.kind != kind:
            continue
        for v in m.spec.variants:
            files = _files(root, m, v.id)
            if files:
                found.append((v.vram_gb.weights if v.vram_gb else 0, m, v.id, files))
    found.sort(key=lambda x: x[0], reverse=True)
    return [(m, vid, files) for _, m, vid, files in found]


def find_llama_server(env: Mapping[str, str], packs_root: Path) -> Path | None:
    """Explicit override, then an installed GPU pack, then the build inside the app (08 §4.1)."""
    override = env.get("OPAGENT_LLAMA_SERVER")
    if override and Path(override).is_file():
        return Path(override)
    if packs_root.is_dir():
        packs = sorted(packs_root.glob(f"llamacpp-*/*/**/{LLAMA}"), key=lambda p: p.stat().st_mtime, reverse=True)
        if packs:
            return packs[0]
    bundled = Path(env.get("OPAGENT_RUNTIME_DIR") or "") / LLAMA
    return bundled if env.get("OPAGENT_RUNTIME_DIR") and bundled.is_file() else None


def siglip_tokenizer(path: Path, length: int = TEXT_LENGTH) -> Callable[[Sequence[str]], np.ndarray]:
    from tokenizers import Tokenizer

    tok = Tokenizer.from_file(str(path))
    pad = tok.token_to_id("<pad>") or 0
    tok.enable_truncation(max_length=length)
    tok.enable_padding(length=length, pad_id=pad, pad_token="<pad>")  # noqa: S106 - a vocabulary token

    def encode(texts: Sequence[str]) -> np.ndarray:
        return np.array([e.ids for e in tok.encode_batch(list(texts))], dtype=np.int64)

    return encode


def _providers() -> list[str]:
    import onnxruntime as ort

    have = ort.get_available_providers()
    order = ["DmlExecutionProvider", "CoreMLExecutionProvider", "CPUExecutionProvider"]
    return [p for p in order if p in have] or ["CPUExecutionProvider"]


def _fingerprint(m: LoadedModel, variant: str) -> str:
    """Invariant 7: results are cached per model, so the variant and its weights' hash are both in it."""
    shas = sorted(f.sha256 or f.path for f in m.pinned_files(variant))
    return f"{m.key}@{variant}:{shas[0][:12]}"


def load_local(
    models_root: Path,
    env: Mapping[str, str] | None = None,
    registry: Registry | None = None,
    embed_dim: int | None = None,
    packs_root: Path | None = None,
) -> LocalBackends:
    env = os.environ if env is None else env
    reg = registry or load_registry()
    out = LocalBackends()
    vlms = _installed(models_root, reg, "vlm")
    if vlms:
        exe = find_llama_server(env, packs_root or models_root.parent / "packs")
        if exe is None:
            out.problems.append("RUNTIME_NOT_INSTALLED")
        else:
            from opagent_adapters.llamacpp import LlamaCppServerAdapter

            m, variant, files = vlms[0]
            v = m.spec.variant(variant)
            out.vlm = LlamaCppServerAdapter(
                [str(exe)],
                str(files["weights"]),
                str(files["mmproj"]) if "mmproj" in files else None,
                parallel=v.recommended_batch or 2,
                fingerprint=_fingerprint(m, variant),
                has_gpu=True,
            )
    embedders = _installed(models_root, reg, "embedding")
    if embedders:
        from opagent_adapters.ort_embed import OrtEmbedAdapter

        m, variant, files = embedders[0]
        spec = m.spec.embedding
        out.embedder = OrtEmbedAdapter(
            str(files["vision"]),
            str(files["text"]),
            embed_dim or int((spec or {}).get("dim", 1152)),
            providers=_providers(),
            tokenizer=siglip_tokenizer(files["tokenizer"]),
            fingerprint=_fingerprint(m, variant),
            max_batch=m.spec.variant(variant).recommended_batch or 8,
        )
    detectors = _installed(models_root, reg, "face_detector")
    face_embedders = _installed(models_root, reg, "face_embedder")
    if detectors and face_embedders:
        from opagent_adapters.ort_face import OrtFaceBackend

        dm, dv, dfiles = detectors[0]
        em, ev, efiles = face_embedders[0]
        out.faces = OrtFaceBackend(
            str(dfiles["detector"]),
            str(efiles["embedder"]),
            providers=_providers(),
            fingerprint=f"{_fingerprint(dm, dv)}+{_fingerprint(em, ev)}",
        )
    return out
