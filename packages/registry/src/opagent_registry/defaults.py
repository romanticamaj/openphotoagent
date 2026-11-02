"""Per-task default models (03 §1.1 ``tasks_default_rank``, 06 §1.6 ``task_default_model``)."""

from __future__ import annotations

from dataclasses import dataclass

from .loader import Registry

TASKS = ("caption", "tag", "classify", "embed")
_CAP = {"caption": "caption", "tag": "tag", "classify": "classify", "embed": "embed"}


@dataclass(frozen=True)
class TaskDefault:
    task: str
    model: str
    variant: str
    source: str  # "rank" | "override"


def _usable(reg: Registry, model_id: str, task: str) -> bool:
    m = reg.models.get(model_id)
    if m is None or m.status != "valid" or _CAP[task] not in m.spec.capabilities:
        return False
    # classify defaults to the VLM of the cascade; embedding models classify only inside the cascade
    return not (task == "classify" and m.spec.kind == "embedding")


def resolve_task_defaults(
    reg: Registry, overrides: dict[str, str] | None = None, variant_for: dict[str, str] | None = None
) -> dict[str, TaskDefault]:
    """``overrides`` maps task → ``model`` or ``model@variant``; an override naming a model without that
    capability raises ``KeyError``. ``variant_for`` (model → variant) lets the caller apply compat's choice."""
    out: dict[str, TaskDefault] = {}
    for task in TASKS:
        ov = (overrides or {}).get(task)
        if ov:
            mid, _, vid = ov.partition("@")
            if not _usable(reg, mid, task):
                raise KeyError(f"{mid} cannot serve task {task}")
            spec = reg.models[mid].spec
            vid = vid or (variant_for or {}).get(mid) or spec.variants[0].id
            spec.variant(vid)
            out[task] = TaskDefault(task, mid, vid, "override")
            continue
        ranked = sorted(
            (m for m in reg.models if _usable(reg, m, task) and task in reg.models[m].spec.tasks_default_rank),
            key=lambda m: (reg.models[m].spec.tasks_default_rank[task], m),
        )
        if ranked:
            spec = reg.models[ranked[0]].spec
            out[task] = TaskDefault(task, spec.id, (variant_for or {}).get(spec.id) or spec.variants[0].id, "rank")
    return out
