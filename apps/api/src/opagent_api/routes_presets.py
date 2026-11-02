"""Organize presets: list, export, import (04 §2.2; VERSIONING §4; 09b §6 PRESET).

Import is untrusted input (a file someone shared): it is parsed and validated by ``opagent_core.preset_io`` and
stored only when every template and rule passed. Built-in presets cannot be overwritten; a clash offers importing
under a new name. Imported presets are used by organize jobs like built-in ones."""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from opagent_core import preset_io as P
from opagent_db.schema import preset_table
from opagent_doctor.codes import problem
from pydantic import BaseModel, Field
from sqlalchemy import insert, select

from .approvals import ui_only
from .state import DEFAULT_WS, AppState

router = APIRouter()


def _st(req: Request) -> AppState:
    return req.app.state.opa  # type: ignore[no-any-return]


def _builtin_keys() -> list[str]:
    from .organize import PRESETS_DIR

    return sorted(p.stem for p in PRESETS_DIR.glob("*.yaml"))


def user_doc(c: Any, key: str) -> dict[str, Any] | None:
    row = c.execute(select(preset_table.c.doc).where(preset_table.c.key == key)).first()
    return dict(row.doc) if row else None


class ImportIn(BaseModel):
    text: str = Field(max_length=P.MAX_BYTES + 1)
    rename: bool = False


@router.get("/presets")
def list_presets(req: Request) -> list[dict[str, Any]]:
    from .organize import preset

    out = [{"key": k, "name": preset(k).name, "source": "builtin"} for k in _builtin_keys()]
    with _st(req).engine.begin() as c:
        for r in c.execute(select(preset_table).order_by(preset_table.c.key)):
            out.append({"key": r.key, "name": r.doc["preset"].get("name") or {}, "source": "imported"})
    return out


@router.get("/presets/{key}/export")
def export_preset(key: str, req: Request) -> Response:
    from .organize import PRESETS_DIR

    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", key):
        raise HTTPException(404, detail={"code": "NOT_FOUND"})
    if key in _builtin_keys():
        doc = P.import_text((PRESETS_DIR / f"{key}.yaml").read_text(encoding="utf-8"))
    else:
        with _st(req).engine.begin() as c:
            found = user_doc(c, key)
        if found is None:
            raise HTTPException(404, detail={"code": "NOT_FOUND"})
        doc = found
    from opagent_core.version import VERSION as APP_VERSION

    return Response(
        P.export_text(doc, app_version=APP_VERSION),
        media_type="application/yaml",
        headers={"content-disposition": f'attachment; filename="{key}.opagent-preset.yaml"'},
    )


@router.post("/presets/import", status_code=201)
def import_preset(body: ImportIn, req: Request) -> dict[str, Any]:
    ui_only(req)  # a person picks the file in the app; never MCP
    try:
        doc = P.import_text(body.text)
    except P.PresetError as e:
        if e.code in ("PRESET_TOO_NEW", "PRESET_TOO_LARGE"):
            raise HTTPException(422, detail=problem(e.code, e.params)) from None
        params = {"reason": e.params.get("reason", e.code), "pointer": e.params.get("pointer", "/")}
        if e.code != "PRESET_INVALID":  # a template / rule check: the validator's own code is the reason
            params["reason"] = e.code
        raise HTTPException(422, detail=problem("PRESET_INVALID", params)) from None
    st = _st(req)
    with st.engine.begin() as c:
        key = str(doc["preset"]["key"])
        taken = set(_builtin_keys()) | {r[0] for r in c.execute(select(preset_table.c.key))}
        if key in taken:
            if not body.rename:
                raise HTTPException(409, detail=problem("PRESET_KEY_TAKEN", {"key": key}))
            n = 2
            while f"{key}-{n}" in taken:
                n += 1
            key = f"{key}-{n}"
            doc = {**doc, "preset": {**doc["preset"], "key": key}}
        c.execute(
            insert(preset_table).values(
                key=key, workspace_id=DEFAULT_WS, doc=doc, migrated_from=doc.get("migrated_from")
            )
        )
    return {"key": key, "migrated_from": doc.get("migrated_from")}
