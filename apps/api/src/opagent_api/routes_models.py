"""Model Manager API (06 §1.11): models with compat, installs as Operations, installations, licences, onboarding.

The API never touches model files (invariant 1): downloads and removals are worker tasks; the API keeps the
registry view, compat (computed from the worker's reported hardware summary) and the installation records.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from opagent_core.hw import Gpu, HwSummary
from opagent_core.ids import uuid7
from opagent_db.schema import installation, job, license_acceptance, perf_observation, worker, worker_task
from opagent_registry.compat import compat
from opagent_registry.loader import LoadedModel, Registry
from pydantic import BaseModel
from sqlalchemy import delete, insert, select

from .labels import emit
from .operations import ensure_operation
from .state import DEFAULT_WS, AppState

router = APIRouter()
worker_router = APIRouter()
LEVEL_ORDER = {"ok": 0, "degraded": 1, "blocked": 2}
ACTIVE_JOBS = ("queued", "running", "paused", "planning", "applying", "awaiting_review")


def _st(req: Request) -> AppState:
    return req.app.state.opa  # type: ignore[no-any-return]


def fix(fid: str, ftype: str, primary: bool = False, **payload: Any) -> dict[str, Any]:
    return {
        "id": fid,
        "fix_id": fid,
        "type": ftype,
        "kind": ftype,
        "primary": primary,
        "label_key": f"fix.{fid}",
        **payload,
    }


def blocker(code: str, fixes: list[dict[str, Any]], **params: Any) -> dict[str, Any]:
    return {
        "code": code,
        "severity": "blocker",
        "title_key": f"err.{code}.title",
        "message_key": f"err.{code}.message",
        "params": params,
        "fixes": fixes,
    }


def blocked(blockers: list[dict[str, Any]]) -> HTTPException:
    return HTTPException(409, detail={"code": "PREFLIGHT_BLOCKED", "blockers": blockers})


def hw_of(caps: dict[str, Any] | None) -> HwSummary:
    h = dict((caps or {}).get("hw") or {})
    if not h:
        return HwSummary(os="unknown", arch="unknown")
    gpus = tuple(Gpu(**g) for g in h.pop("gpus", []))
    packs = tuple(tuple(p) for p in h.pop("installed_packs", []))
    return HwSummary(gpus=gpus, installed_packs=packs, **h)


def license_hash(m: LoadedModel) -> str:
    lic = m.spec.license.model_dump()
    return hashlib.sha256(json.dumps(lic, sort_keys=True).encode()).hexdigest()[:16]


def _ctx(c: Any, st: AppState, worker_id: str) -> dict[str, Any]:
    reg = st.registry()
    caps = c.execute(select(worker.c.capabilities).where(worker.c.id == worker_id)).scalar()
    if caps is None:
        raise HTTPException(404, detail={"code": "WORKER_NOT_FOUND"})
    inst = {
        r.key: r
        for r in c.execute(
            select(installation).where(installation.c.worker_id == worker_id, installation.c.kind == "model")
        )
    }
    acc = {
        r.model_id: r.license_hash
        for r in c.execute(select(license_acceptance).where(license_acceptance.c.revoked_at.is_(None)))
    }
    consents = [mid for mid, h in acc.items() if mid in reg.models and h == license_hash(reg.models[mid])]
    obs: dict[str, float] = {}
    for r in c.execute(
        select(perf_observation.c.variant, perf_observation.c.vram_gb)
        .where(perf_observation.c.worker_id == worker_id, perf_observation.c.vram_gb.is_not(None))
        .order_by(perf_observation.c.id)
    ):
        obs[r.variant] = float(r.vram_gb)
    return {"reg": reg, "hw": hw_of(caps), "inst": inst, "consents": consents, "obs": obs}


def _variant_view(m: LoadedModel, vid: str, ctx: dict[str, Any]) -> dict[str, Any]:
    reg: Registry = ctx["reg"]
    key = f"{m.spec.id}@{vid}"
    v = m.spec.variant(vid)
    c = compat(
        m,
        vid,
        ctx["hw"],
        reg.runtimes,
        gpu_classes=reg.gpu_classes,
        installed=list(ctx["inst"]),
        consents=ctx["consents"],
        observed_need_gb=ctx["obs"],
    )
    files = m.pinned_files(vid)
    exact = all(f.size for f in files) and bool(files)
    size = sum(f.size or 0 for f in files) if exact else int((v.vram_gb.weights + v.vram_gb.mmproj) * 1e9)
    inst = ctx["inst"].get(key)
    cd = c.to_dict()
    return {
        "id": vid,
        "key": key,
        "runtime": v.runtime,
        "quant": v.quant,
        "pin_state": m.pin_state.get(vid, "unpinned"),
        "size_bytes": size,
        "size_estimated": not exact,
        "vram_gb": v.vram_gb.model_dump(),
        "ram_gb": v.ram_gb,
        "compat": cd,
        "fits_now": cd["fits_now"],  # legacy field (03 §7.3, MDL-012)
        "installation": {"id": inst.id, "status": inst.status, "size_bytes": inst.size_bytes} if inst else None,
        "perf": {"basis": "observed" if key in ctx["obs"] else "estimated", "prior": v.perf_prior},
    }


def model_view(m: LoadedModel, ctx: dict[str, Any]) -> dict[str, Any]:
    variants = [_variant_view(m, v.id, ctx) for v in m.spec.variants] if m.status == "valid" else []
    best = min(variants, key=lambda x: LEVEL_ORDER[x["compat"]["level"]], default=None)
    return {
        "id": m.spec.id,
        "display_name": m.spec.display_name,
        "kind": m.spec.kind,
        "family": m.spec.family,
        "capabilities": m.spec.capabilities,
        "languages": m.spec.languages,
        "license": {**m.spec.license.model_dump(), "license_hash": license_hash(m)},
        "source": m.source,
        "status": m.status,
        "errors": [e.__dict__ for e in m.errors],
        "level": best["compat"]["level"] if best else "blocked",
        "best_variant": best["id"] if best else None,
        "variants": variants,
        "notes": m.spec.notes,
    }


@router.get("/models")
def list_models(req: Request, worker_id: str, kind: str | None = None, task: str | None = None) -> dict[str, Any]:
    st = _st(req)
    with st.engine.begin() as c:
        ctx = _ctx(c, st, worker_id)
    reg: Registry = ctx["reg"]
    items = [
        model_view(m, ctx)
        for m in reg.models.values()
        if (kind is None or m.spec.kind == kind) and (task is None or task in m.spec.capabilities)
    ]
    return {"items": items, "registry_errors": [e.__dict__ for e in reg.errors], "registry_version": reg.version}


@router.get("/models/{model_id}")
def get_model(model_id: str, req: Request, worker_id: str) -> dict[str, Any]:
    st = _st(req)
    with st.engine.begin() as c:
        ctx = _ctx(c, st, worker_id)
    m = ctx["reg"].models.get(model_id)
    if m is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND"})
    return model_view(m, ctx)


def install_blockers(m: LoadedModel, vid: str, ctx: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    if m.pin_state.get(vid) != "pinned":  # ADR-0002: never download unpinned built-ins
        alts = [v.id for v in m.spec.variants if v.id != vid and m.pin_state.get(v.id) == "pinned"]
        out.append(
            blocker(
                "MODEL_NOT_PINNED",
                [
                    fix("update_catalog", "open_docs", True, doc="models/pinning"),
                    fix(
                        "other_variant",
                        "choose_alternative",
                        payload={"variants": alts},
                        consequence={"key": "fix.other_variant.consequence"},
                    ),
                ],
                variant=f"{m.spec.id}@{vid}",
            )
        )
    if m.spec.license.requires_acceptance and m.spec.id not in ctx["consents"]:
        out.append(
            blocker(
                "LICENSE_CONSENT_REQUIRED",
                [fix("accept_license", "open_settings", True, route=f"/models/{m.spec.id}#license")],
                model=m.spec.id,
            )
        )
    return out


def _queue_download(c: Any, st: AppState, worker_id: str, m: LoadedModel, vid: str) -> str:
    tid = uuid7(st.clock)
    files = [
        {"repo": f.repo, "revision": m.revision(vid), "path": f.path, "size": f.size, "sha256": f.sha256}
        for f in m.pinned_files(vid)
    ]
    c.execute(
        insert(worker_task).values(
            id=tid,
            kind="download",
            worker_id=worker_id,
            payload={"op_id": tid, "variant": f"{m.spec.id}@{vid}", "kind": m.spec.kind, "files": files},
        )
    )
    ensure_operation(c, st.clock, tid, "model.download", {"kind": "variant", "id": f"{m.spec.id}@{vid}"})
    return tid


class InstallIn(BaseModel):
    variant: str


@router.post("/workers/{worker_id}/installs", status_code=202)
def install(worker_id: str, body: InstallIn, req: Request) -> dict[str, Any]:
    st = _st(req)
    mid, _, vid = body.variant.partition("@")
    with st.engine.begin() as c:
        ctx = _ctx(c, st, worker_id)
        m = ctx["reg"].models.get(mid)
        if m is None or m.status != "valid" or vid not in {v.id for v in m.spec.variants}:
            raise HTTPException(404, detail={"code": "NOT_FOUND"})
        bl = install_blockers(m, vid, ctx)
        if bl:
            raise blocked(bl)
        tid = _queue_download(c, st, worker_id, m, vid)
    return {"operation_id": tid, "status": "queued"}


@router.delete("/workers/{worker_id}/installations/{inst_id}", status_code=202)
def remove_installation(worker_id: str, inst_id: str, req: Request) -> dict[str, Any]:
    st = _st(req)
    with st.engine.begin() as c:
        inst = c.execute(
            select(installation).where(installation.c.id == inst_id, installation.c.worker_id == worker_id)
        ).first()
        if inst is None:
            raise HTTPException(404, detail={"code": "NOT_FOUND"})
        using = [
            r.id
            for r in c.execute(select(job.c.id, job.c.params).where(job.c.status.in_(ACTIVE_JOBS)))
            if inst.key in ((r.params or {}).get("models") or [])
        ]
        if using:
            raise blocked(
                [
                    blocker(
                        "MODEL_IN_USE",
                        [
                            fix("delete_after_jobs", "auto_fix", True),
                            fix(
                                "cancel_jobs", "choose_alternative", consequence={"key": "fix.cancel_jobs.consequence"}
                            ),
                        ],
                        jobs=using,
                    )
                ]
            )
        tid = uuid7(st.clock)
        c.execute(
            insert(worker_task).values(
                id=tid,
                kind="remove",
                worker_id=worker_id,
                payload={"installation_id": inst.id, "key": inst.key, "files": inst.files},
            )
        )
        ensure_operation(c, st.clock, tid, "model.remove", {"kind": "installation", "id": inst.id})
    return {"operation_id": tid, "freed_bytes": inst.size_bytes or 0}


@router.get("/licenses/{model_id}")
def get_license(model_id: str, req: Request) -> dict[str, Any]:
    m = _st(req).registry().models.get(model_id)
    if m is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND"})
    return {**m.spec.license.model_dump(), "model_id": model_id, "license_hash": license_hash(m)}


class AcceptIn(BaseModel):
    license_hash: str


@router.post("/licenses/{model_id}/accept")
def accept_license(model_id: str, body: AcceptIn, req: Request) -> dict[str, Any]:
    st = _st(req)
    m = st.registry().models.get(model_id)
    if m is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND"})
    cur = license_hash(m)
    if body.license_hash != cur:  # the user saw an outdated licence text
        raise HTTPException(409, detail={"code": "LICENSE_CHANGED", "license_hash": cur})
    with st.engine.begin() as c:
        c.execute(delete(license_acceptance).where(license_acceptance.c.model_id == model_id))
        c.execute(
            insert(license_acceptance).values(
                workspace_id=DEFAULT_WS, model_id=model_id, license_id=m.spec.license.spdx, license_hash=cur
            )
        )
        emit(c, "license.accepted", {"model_id": model_id})
    return {"model_id": model_id, "license_hash": cur, "accepted": True}


@router.delete("/licenses/{model_id}/accept")
def revoke_license(model_id: str, req: Request) -> dict[str, Any]:
    st = _st(req)
    with st.engine.begin() as c:
        c.execute(delete(license_acceptance).where(license_acceptance.c.model_id == model_id))
        emit(c, "license.revoked", {"model_id": model_id})
    return {"model_id": model_id, "accepted": False}


class OnboardIn(BaseModel):
    worker_id: str
    variants: list[str]


@router.post("/onboarding/install", status_code=202)
def onboarding_install(body: OnboardIn, req: Request) -> dict[str, Any]:
    """The recommended combination in one go; the embedding model first so search works before the VLM (DL-020)."""
    st = _st(req)
    with st.engine.begin() as c:
        ctx = _ctx(c, st, body.worker_id)
        reg: Registry = ctx["reg"]

        def rank(key: str) -> int:
            m = reg.models.get(key.split("@")[0])
            return 0 if m and m.spec.kind == "embedding" else 1

        order = sorted(body.variants, key=rank)
        ops, skipped = [], []
        for key in order:
            mid, _, vid = key.partition("@")
            m = reg.models.get(mid)
            if m is None:
                skipped.append({"variant": key, "code": "NOT_FOUND"})
                continue
            bl = install_blockers(m, vid, ctx)
            if bl:
                skipped.append({"variant": key, "code": bl[0]["code"]})
                continue
            ops.append(_queue_download(c, st, body.worker_id, m, vid))
    return {"order": order, "operations": ops, "blocked": skipped}


class ObservationIn(BaseModel):
    worker_id: str
    kind: str
    key: str
    value: float


@worker_router.post("/observations")
def observation(body: ObservationIn, req: Request) -> dict[str, str]:
    """Measured numbers from the worker (vram after load, load time, throughput) → perf_observation."""
    st = _st(req)
    col = {"vram": "vram_gb", "load_s": "load_s", "items_per_s": "items_per_s", "bytes_per_s": "bytes_per_s"}.get(
        body.kind
    )
    if col is None:
        raise HTTPException(422, detail={"code": "UNKNOWN_OBSERVATION"})
    with st.engine.begin() as c:
        c.execute(
            insert(perf_observation).values(
                worker_id=body.worker_id,
                variant=body.key,
                op_type=f"observe.{body.kind}",
                at=st.clock.now(),
                **{col: body.value},
            )
        )
    return {"status": "ok"}


def on_download_complete(st: AppState, c: Any, t: Any, result: dict[str, Any]) -> None:
    p = t.payload
    if result.get("status") != "done":
        return
    size = sum(int(f["size"] or 0) for f in p["files"])
    vals = {"status": "installed", "size_bytes": size, "files": result.get("installed") or p["files"]}
    existing = c.execute(
        select(installation.c.id).where(installation.c.worker_id == t.worker_id, installation.c.key == p["variant"])
    ).first()
    if existing:
        from sqlalchemy import update

        c.execute(update(installation).where(installation.c.id == existing.id).values(**vals))
    else:
        c.execute(
            insert(installation).values(
                id=uuid7(st.clock), worker_id=t.worker_id, kind="model", key=p["variant"], **vals
            )
        )
    emit(c, "model.installed", {"variant": p["variant"], "worker_id": t.worker_id})
    if p.get("kind") == "embedding":  # semantic search becomes available before the VLM finishes (DL-020)
        rid = f"reindex:{t.worker_id}:{p['variant']}"
        ensure_operation(c, st.clock, rid, "search.reindex", {"kind": "variant", "id": p["variant"]})


def on_remove_complete(st: AppState, c: Any, t: Any, result: dict[str, Any]) -> None:
    c.execute(delete(installation).where(installation.c.id == t.payload["installation_id"]))
    emit(
        c,
        "model.removed",
        {"key": t.payload["key"], "worker_id": t.worker_id, "freed_bytes": result.get("freed_bytes")},
    )
