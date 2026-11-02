"""Label Studio export and import (02 §8.3; 09b §7.4).

* Export: one task per photo; ``data.image`` is a Label Studio *local-files* URL built from the root alias and the
  root-relative path (no host path ever leaves the app); our current result is a pre-annotation (``predictions``)
  with ``model_version`` = model fingerprint. The labeling config is generated XML with every name escaped.
* Import: annotations come back as **human labels** (review corrections) — no job, no inference. Unknown labels
  and photos that are gone are reported, not guessed.
"""

from __future__ import annotations

from typing import Any
from xml.sax.saxutils import quoteattr

from fastapi import APIRouter, HTTPException, Request
from opagent_db.schema import asset, asset_location, result, source
from pydantic import BaseModel, Field
from sqlalchemy import select

from .approvals import ui_only
from .state import AppState

router = APIRouter()
FROM = "category"


def _st(req: Request) -> AppState:
    return req.app.state.opa  # type: ignore[no-any-return]


def labeling_config(cats: list[dict[str, Any]]) -> str:
    """Single-choice classification over the taxonomy; values are category keys, aliases the display names."""
    choices = "".join(
        f"<Choice value={quoteattr(str(c['key']))} alias={quoteattr(str(c.get('zh-TW') or c.get('en') or c['key']))}/>"
        for c in cats
    )
    return (
        '<View><Image name="image" value="$image"/>'
        f'<Choices name="{FROM}" toName="image" choice="single" showInline="true">{choices}</Choices></View>'
    )


def _cats(preset_key: str, c: Any) -> list[dict[str, Any]]:
    from .organize import preset

    try:
        return list(preset(preset_key, None, c).categories)
    except (KeyError, FileNotFoundError):
        raise HTTPException(422, detail={"code": "VALIDATION", "field": "preset"}) from None


class ExportIn(BaseModel):
    asset_ids: list[str] = Field(min_length=1, max_length=50_000)
    preset: str = "by-content-12"


@router.post("/export/label-studio")
def export(body: ExportIn, req: Request) -> dict[str, Any]:
    st = _st(req)
    with st.engine.begin() as c:
        cats = _cats(body.preset, c)
        locs: dict[str, Any] = {}
        for r in c.execute(
            select(asset_location.c.asset_id, asset_location.c.rel_path, source.c.root_alias)
            .join(source, source.c.id == asset_location.c.source_id)
            .where(asset_location.c.asset_id.in_(body.asset_ids), asset_location.c.missing.is_(False))
            .order_by(asset_location.c.asset_id, asset_location.c.rel_path)
        ):
            locs.setdefault(r.asset_id, r)
        latest: dict[str, Any] = {}
        for r in c.execute(
            select(result.c.asset_id, result.c.category, result.c.confidence, result.c.model_fingerprint)
            .where(result.c.asset_id.in_(list(locs) or ["-"]))
            .order_by(result.c.asset_id, result.c.created_at.desc())
        ):
            latest.setdefault(r.asset_id, r)
    tasks = []
    for aid in body.asset_ids:
        loc = locs.get(aid)
        if loc is None:
            continue
        t: dict[str, Any] = {
            "data": {"image": f"/data/local-files/?d={loc.root_alias}/{loc.rel_path}", "opagent_id": aid}
        }
        r = latest.get(aid)
        if r is not None and r.category:
            t["predictions"] = [
                {
                    "model_version": r.model_fingerprint,
                    "score": float(r.confidence or 0.0),
                    "result": [
                        {"from_name": FROM, "to_name": "image", "type": "choices", "value": {"choices": [r.category]}}
                    ],
                }
            ]
        tasks.append(t)
    return {"config": labeling_config(cats), "tasks": tasks}


class ImportIn(BaseModel):
    export: list[dict[str, Any]] = Field(max_length=50_000)
    preset: str = "by-content-12"


@router.post("/import/label-studio")
def import_(body: ImportIn, req: Request) -> dict[str, Any]:
    ui_only(req)  # labels from a file a person chose, in the app
    from .routes_review import apply_correction

    st = _st(req)
    applied, unknown, missing, empty = 0, [], [], 0
    with st.engine.begin() as c:
        keys = {str(x["key"]) for x in _cats(body.preset, c)}
        for task in body.export:
            aid = str((task.get("data") or {}).get("opagent_id") or "")
            anns = task.get("annotations") or []
            label = None
            for res in (anns[-1].get("result") or []) if anns else []:
                if res.get("from_name") == FROM:
                    label = ((res.get("value") or {}).get("choices") or [None])[0]
            if label is None:
                empty += 1
                continue
            if c.execute(select(asset.c.id).where(asset.c.id == aid)).first() is None:
                missing.append(aid)
                continue
            if label not in keys:
                unknown.append({"asset_id": aid, "label": str(label)[:100]})
                continue
            apply_correction(st, c, aid, label, None, reason="label-studio")
            applied += 1
    return {"applied": applied, "unknown_labels": unknown, "missing_assets": missing, "unannotated": empty}
