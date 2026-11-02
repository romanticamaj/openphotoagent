"""Mini benchmark (03 §6; 09b §6): golden sets, benchmark runs, the report, and "apply the suggested τ".

The worker runs every candidate over the golden set's photos (it is the only process that reads them, invariant 1)
and returns predictions and timings; the scores are computed here with ``opagent_core.bench`` — rule-based, no
model judges. Applying a threshold only stores it: results are re-decided from stored outputs, never re-inferred
(invariant 7)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from opagent_core import bench as B
from opagent_core.ids import uuid7
from opagent_db.schema import asset_location, benchmark, golden_set, source, worker_task, workspace
from pydantic import BaseModel, Field
from sqlalchemy import insert, select, update

from .approvals import ui_only
from .state import DEFAULT_WS, AppState

router = APIRouter()


def _st(req: Request) -> AppState:
    return req.app.state.opa  # type: ignore[no-any-return]


class GoldenIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    classes: list[str] = Field(min_length=2, max_length=50)
    items: dict[str, str]


class BenchIn(BaseModel):
    golden_set_id: str
    candidates: list[str] = Field(min_length=1, max_length=8)
    constraints: dict[str, float] = Field(default_factory=dict)  # vram_mb, items, within_s
    target: float = Field(default=0.95, gt=0, le=1)


class ApplyIn(BaseModel):
    candidate: str
    target: float = Field(default=0.95, gt=0, le=1)


@router.post("/golden-sets", status_code=201)
def create_golden(body: GoldenIn, req: Request) -> dict[str, Any]:
    ui_only(req)
    st = _st(req)
    bad = sorted({v for v in body.items.values() if v not in body.classes})
    if bad:
        raise HTTPException(422, detail={"code": "VALIDATION", "field": "items", "unknown_classes": bad[:10]})
    gid = uuid7(st.clock)
    with st.engine.begin() as c:
        c.execute(
            insert(golden_set).values(
                id=gid, workspace_id=DEFAULT_WS, name=body.name, classes=body.classes, items=body.items
            )
        )
    ok, why = B.golden_ready(body.items, body.classes)
    return {"id": gid, "ready": ok, "missing": why}


def golden_check(c: Any, gid: str) -> tuple[Any, dict[str, Any] | None]:
    g = c.execute(select(golden_set).where(golden_set.c.id == gid)).first()
    if g is None:
        return None, None
    ok, why = B.golden_ready(g.items, g.classes)
    return g, None if ok else why


@router.post("/benchmarks", status_code=201)
def start(body: BenchIn, req: Request) -> dict[str, Any]:
    ui_only(req)
    st = _st(req)
    from .operations import ensure_operation
    from .preflight import enforce

    with st.engine.begin() as c:
        g = c.execute(select(golden_set).where(golden_set.c.id == body.golden_set_id)).first()
        if g is None:
            raise HTTPException(404, detail={"code": "NOT_FOUND"})
        enforce(c, st, "benchmark.run", {"golden_set_id": body.golden_set_id})
        locs: dict[str, Any] = {}
        for r in c.execute(
            select(asset_location.c.asset_id, asset_location.c.rel_path, source.c.root_alias, source.c.worker_id)
            .join(source, source.c.id == asset_location.c.source_id)
            .where(asset_location.c.asset_id.in_(list(g.items)), asset_location.c.missing.is_(False))
            .order_by(asset_location.c.asset_id, asset_location.c.rel_path)
        ):
            locs.setdefault(r.asset_id, r)
        bid = uuid7(st.clock)
        c.execute(
            insert(benchmark).values(
                id=bid,
                workspace_id=DEFAULT_WS,
                golden_set_id=g.id,
                candidates=body.candidates,
                constraints={**body.constraints, "target": body.target},
            )
        )
        wid = next((r.worker_id for r in locs.values()), None)
        c.execute(
            insert(worker_task).values(
                id=bid,
                kind="benchmark",
                worker_id=wid,
                payload={
                    "benchmark_id": bid,
                    "candidates": body.candidates,
                    "classes": g.classes,
                    "items": [{"asset_id": a, "root_alias": r.root_alias, "rel": r.rel_path} for a, r in locs.items()],
                },
            )
        )
        ensure_operation(c, st.clock, bid, "benchmark.run", {"kind": "benchmark", "id": bid})
    return {"id": bid, "operation_id": bid, "status": "queued"}


def on_benchmark_complete(st: AppState, c: Any, t: Any, res: dict[str, Any]) -> None:
    c.execute(
        update(benchmark)
        .where(benchmark.c.id == t.payload["benchmark_id"])
        .values(status="completed", raw=res, finished_at=st.clock.now())
    )


def report(c: Any, b: Any, target: float | None = None) -> dict[str, Any]:
    g = c.execute(select(golden_set).where(golden_set.c.id == b.golden_set_id)).one()
    tgt = float(target if target is not None else (b.constraints or {}).get("target", 0.95))
    out: list[dict[str, Any]] = []
    for cand in (b.raw or {}).get("candidates") or []:
        if cand.get("error"):
            out.append({"name": cand["name"], "error": cand["error"]})
            continue
        preds = {a: (p[0], float(p[1])) for a, p in (cand.get("preds") or {}).items()}
        m = B.classification(g.items, preds, g.classes)
        m.update(
            name=cand["name"],
            latency=B.latency(cand.get("latency_ms") or []),
            items_per_s=float(cand.get("items_per_s") or 0.0),
            vram_mb=float(cand.get("vram_mb") or 0.0),
            suggested=B.suggest_threshold(m["coverage_curve"], tgt),
        )
        out.append(m)
    cons = b.constraints or {}
    ok = [x for x in out if "error" not in x]
    rec = B.recommend(
        ok,
        vram_mb=float(cons.get("vram_mb") or 1e12),
        items=int(cons["items"]) if cons.get("items") else None,
        within_s=float(cons["within_s"]) if cons.get("within_s") else None,
    )
    return {"id": b.id, "status": b.status, "target": tgt, "candidates": out, "recommended": rec}


@router.get("/benchmarks/{bid}")
def get_benchmark(bid: str, req: Request, target: float | None = None) -> dict[str, Any]:
    with _st(req).engine.begin() as c:
        b = c.execute(select(benchmark).where(benchmark.c.id == bid)).first()
        if b is None:
            raise HTTPException(404, detail={"code": "NOT_FOUND"})
        return report(c, b, target)


@router.post("/benchmarks/{bid}/apply")
def apply_threshold(bid: str, body: ApplyIn, req: Request) -> dict[str, Any]:
    """One click: the candidate's suggested τ becomes the accept threshold. Stored, not recomputed (invariant 7)."""
    ui_only(req)
    st = _st(req)
    with st.engine.begin() as c:
        b = c.execute(select(benchmark).where(benchmark.c.id == bid)).first()
        if b is None or b.status != "completed":
            raise HTTPException(404, detail={"code": "NOT_FOUND"})
        cand = next((x for x in report(c, b, body.target)["candidates"] if x["name"] == body.candidate), None)
        if cand is None or not cand.get("suggested"):
            raise HTTPException(422, detail={"code": "NO_SUGGESTED_THRESHOLD"})
        tau = float(cand["suggested"]["tau"])
        cur = c.execute(select(workspace.c.settings).where(workspace.c.id == DEFAULT_WS)).scalar() or {}
        th = {**(cur.get("thresholds") or {}), "default": {"accept": tau}}
        c.execute(update(workspace).where(workspace.c.id == DEFAULT_WS).values(settings={**cur, "thresholds": th}))
    return {"applied": {"accept": tau}, "recompute": False}


@router.get("/settings/thresholds")
def get_thresholds(req: Request) -> dict[str, Any]:
    with _st(req).engine.begin() as c:
        cur = c.execute(select(workspace.c.settings).where(workspace.c.id == DEFAULT_WS)).scalar() or {}
    return {"default": {"accept": 0.80, "review": 0.50}, **(cur.get("thresholds") or {})}
