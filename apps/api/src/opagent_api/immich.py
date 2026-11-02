"""Immich write-back (02 §8.2; 09b §7.3): tags, album and description for photos that are also in Immich.

Same discipline as file actions (invariant 3), applied to someone else's server:
* **plan** — the worker computes each photo's SHA-1 (only the worker reads photos, invariant 1); the API finds the
  matching Immich asset by checksum and lists only the writes that are still missing, so a second apply writes
  nothing (IMM-005). Photos not in Immich are listed as skipped, not failed (IMM-002).
* **approve + apply** — UI only; every write is journalled before and after it happens.
* **undo** — reverses the journal: removes only the tags and album entries we added, restores descriptions.
The API key lives in the OS keyring (credstore kind ``immich-key``) and is never returned (IMM-004).
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from opagent_core.ids import uuid7
from opagent_db.schema import (
    asset_location,
    integration_journal,
    integration_plan,
    search_doc,
    source,
    worker,
    worker_task,
    workspace,
)
from pydantic import BaseModel, Field
from sqlalchemy import insert, select, update

from .approvals import ui_only
from .state import DEFAULT_WS, AppState

router = APIRouter()
TAG_ROOT = "OpenPhotoAgent"


def _st(req: Request) -> AppState:
    return req.app.state.opa  # type: ignore[no-any-return]


class ImmichError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ImmichClient:
    def __init__(self, url: str, key: str | None, transport: Any = None) -> None:
        self.base = url.rstrip("/")
        self.http = httpx.Client(timeout=10.0, headers={"x-api-key": key or ""}, transport=transport)

    def _req(self, method: str, path: str, **kw: Any) -> Any:
        try:
            r = self.http.request(method, self.base + path, **kw)
        except httpx.HTTPError:
            raise ImmichError("IMMICH_UNREACHABLE") from None
        if r.status_code in (401, 403):
            raise ImmichError("IMMICH_AUTH_FAILED")
        r.raise_for_status()
        return r.json() if r.content else None

    def check(self) -> None:
        self._req("GET", "/api/server/ping")
        self._req("GET", "/api/users/me")

    def find(self, checksum_b64: str) -> str | None:
        out = self._req("POST", "/api/search/metadata", json={"checksum": checksum_b64})
        items = ((out or {}).get("assets") or {}).get("items") or []
        return str(items[0]["id"]) if items else None

    def asset(self, aid: str) -> dict[str, Any]:
        a = self._req("GET", f"/api/assets/{aid}") or {}
        return {
            "description": ((a.get("exifInfo") or {}).get("description") or ""),
            "tags": {t["value"] for t in a.get("tags") or []},
        }

    def tag_id(self, value: str, create: bool) -> str | None:
        tags = {t["value"]: t["id"] for t in self._req("GET", "/api/tags") or []}
        if value in tags or not create:
            return tags.get(value)
        parent = None
        if "/" in value:
            parent = self.tag_id(value.rsplit("/", 1)[0], create=True)
        out = self._req("POST", "/api/tags", json={"name": value.rsplit("/", 1)[-1], "parentId": parent})
        return str(out["id"])

    def album(self, name: str) -> tuple[str | None, set[str]]:
        for a in self._req("GET", "/api/albums") or []:
            if a["albumName"] == name:
                detail = self._req("GET", f"/api/albums/{a['id']}") or {}
                return str(a["id"]), {x["id"] for x in detail.get("assets") or []}
        return None, set()


def _settings(c: Any) -> dict[str, Any]:
    cur = c.execute(select(workspace.c.settings).where(workspace.c.id == DEFAULT_WS)).scalar() or {}
    return dict(cur.get("immich") or {})


def client(st: AppState, c: Any) -> ImmichClient:
    from opagent_mcp import credstore

    from .ask import _home

    s = _settings(c)
    if not s.get("url"):
        raise HTTPException(409, detail={"code": "IMMICH_NOT_CONFIGURED"})
    key = credstore.load(_home(st), kind="immich-key")
    return ImmichClient(s["url"], key, transport=getattr(st, "immich_transport", None))


# ---------------------------------------------------------------- settings
class ImmichIn(BaseModel):
    url: str = Field(min_length=1, max_length=500)
    api_key: str | None = Field(default=None, max_length=500)


@router.put("/integrations/immich")
def put_immich(body: ImmichIn, req: Request) -> dict[str, Any]:
    ui_only(req)
    st = _st(req)
    from opagent_mcp import credstore

    from .ask import _home

    if body.api_key is not None:
        credstore.save(body.api_key, _home(st), kind="immich-key")
    with st.engine.begin() as c:
        cur = c.execute(select(workspace.c.settings).where(workspace.c.id == DEFAULT_WS)).scalar() or {}
        im = {**(cur.get("immich") or {}), "url": body.url.rstrip("/")}
        if body.api_key is not None:
            im["has_api_key"] = bool(body.api_key)
        c.execute(update(workspace).where(workspace.c.id == DEFAULT_WS).values(settings={**cur, "immich": im}))
        return _view(c)


def _view(c: Any) -> dict[str, Any]:
    s = _settings(c)
    return {"url": s.get("url"), "has_api_key": bool(s.get("has_api_key"))}


@router.get("/integrations/immich")
def get_immich(req: Request) -> dict[str, Any]:
    with _st(req).engine.begin() as c:
        return _view(c)


def preflight_check(st: AppState, c: Any) -> str | None:
    """None when Immich answers and accepts the key, else the error code (06 §4.7 #108/#109)."""
    try:
        client(st, c).check()
    except ImmichError as e:
        return e.code
    except HTTPException:
        return "IMMICH_UNREACHABLE"
    return None


# ---------------------------------------------------------------- plans
class PlanIn(BaseModel):
    asset_ids: list[str] = Field(min_length=1, max_length=10_000)
    tags: bool = True
    album: str | None = Field(default=None, max_length=200)
    description: bool = False
    overwrite_description: bool = False


@router.post("/integrations/immich/plans", status_code=201)
def create_plan(body: PlanIn, req: Request) -> dict[str, Any]:
    ui_only(req)
    st = _st(req)
    pid = uuid7(st.clock)
    with st.engine.begin() as c:
        rows = c.execute(
            select(asset_location.c.asset_id, asset_location.c.rel_path, source.c.root_alias, source.c.worker_id)
            .join(source, source.c.id == asset_location.c.source_id)
            .where(asset_location.c.asset_id.in_(body.asset_ids), asset_location.c.missing.is_(False))
            .order_by(asset_location.c.asset_id, asset_location.c.rel_path)
        ).all()
        first: dict[str, Any] = {}
        for r in rows:
            first.setdefault(r.asset_id, r)
        wid = next((r.worker_id for r in first.values()), None) or c.execute(select(worker.c.id).limit(1)).scalar()
        c.execute(
            insert(integration_plan).values(
                id=pid, kind="immich", status="hashing", spec=body.model_dump(), workspace_id=DEFAULT_WS
            )
        )
        c.execute(
            insert(worker_task).values(
                id=f"{pid}:sha1",
                kind="hash_sha1",
                worker_id=wid,
                payload={
                    "items": [{"asset_id": a, "root_alias": r.root_alias, "rel": r.rel_path} for a, r in first.items()]
                },
            )
        )
    return {"id": pid, "status": "hashing"}


def _build(st: AppState, c: Any, plan: Any, sums: dict[str, str]) -> dict[str, Any]:
    from .mcp_tools import tag_display

    spec = plan.spec
    im = client(st, c)
    match: dict[str, str] = {}
    skipped = []
    for aid in spec["asset_ids"]:
        found = im.find(sums[aid]) if aid in sums else None
        if found:
            match[aid] = found
        else:
            skipped.append({"asset_id": aid, "reason": "NOT_IN_IMMICH" if aid in sums else "FILE_MISSING"})
    docs = {
        r.asset_id: r
        for r in c.execute(select(search_doc).where(search_doc.c.asset_id.in_(list(match) or ["-"]))).all()
    }
    state = {imid: im.asset(imid) for imid in match.values()}
    actions: list[dict[str, Any]] = []
    if spec.get("tags"):
        want: dict[str, list[str]] = {}
        for aid, imid in match.items():
            for key in (docs[aid].tags or []) if aid in docs else []:
                value = f"{TAG_ROOT}/{tag_display(key)}"
                if value not in state[imid]["tags"]:
                    want.setdefault(value, []).append(imid)
        actions += [{"op": "tag", "value": v, "ids": sorted(ids)} for v, ids in sorted(want.items())]
    if spec.get("album"):
        _, inside = im.album(spec["album"])
        missing = sorted(set(match.values()) - inside)
        if missing:
            actions.append({"op": "album_add", "name": spec["album"], "ids": missing})
    if spec.get("description"):
        for aid, imid in sorted(match.items()):
            text = (docs[aid].caption or "") if aid in docs else ""
            before = state[imid]["description"]
            if text and text != before and (not before or spec.get("overwrite_description")):
                actions.append({"op": "describe", "id": imid, "text": text[:2000], "previous": before})
    summary = {"matched": len(match), "skipped": len(skipped), "actions": len(actions)}
    return {"actions": actions, "skipped": skipped, "summary": summary}


@router.get("/integrations/immich/plans/{pid}")
def get_plan(pid: str, req: Request) -> dict[str, Any]:
    st = _st(req)
    with st.engine.begin() as c:
        p = c.execute(select(integration_plan).where(integration_plan.c.id == pid)).first()
        if p is None:
            raise HTTPException(404, detail={"code": "NOT_FOUND"})
        if p.status == "hashing":
            t = c.execute(select(worker_task).where(worker_task.c.id == f"{pid}:sha1")).first()
            if t is None or t.status != "done":
                return {"id": pid, "status": "hashing"}
            try:
                built = _build(st, c, p, dict((t.result or {}).get("sha1") or {}))
            except ImmichError as e:
                raise HTTPException(409, detail={"code": e.code}) from None
            c.execute(update(integration_plan).where(integration_plan.c.id == pid).values(status="draft", body=built))
            p = c.execute(select(integration_plan).where(integration_plan.c.id == pid)).one()
        return {"id": pid, "status": p.status, **(p.body or {})}


def _journal(c: Any, st: AppState, pid: str, seq: int, op: str, params: dict[str, Any], state: str) -> int:
    row = c.execute(
        insert(integration_journal)
        .values(plan_id=pid, seq=seq, op=op, params=params, state=state, at=st.clock.now())
        .returning(integration_journal.c.id)
    ).scalar_one()
    return int(row)


@router.post("/integrations/immich/plans/{pid}/apply")
def apply_plan(pid: str, req: Request) -> dict[str, Any]:
    ui_only(req)  # approval is a person's click in the app (invariant 5: never MCP or the assistant)
    st = _st(req)
    with st.engine.begin() as c:
        p = c.execute(select(integration_plan).where(integration_plan.c.id == pid).with_for_update()).first()
        if p is None or p.status != "draft":
            raise HTTPException(409, detail={"code": "PLAN_NOT_DRAFT", "status": p.status if p else None})
        im = client(st, c)
    done = 0
    for seq, a in enumerate((p.body or {}).get("actions") or []):
        with st.engine.begin() as c:
            jid = _journal(c, st, pid, seq, a["op"], a, "intent")  # durable before the write
        try:
            if a["op"] == "tag":
                tid = im.tag_id(a["value"], create=True)
                im._req("PUT", f"/api/tags/{tid}/assets", json={"ids": a["ids"]})
                params = {**a, "tag_id": tid}
            elif a["op"] == "album_add":
                alb, _ = im.album(a["name"])
                created = alb is None
                if alb is None:
                    alb = str(im._req("POST", "/api/albums", json={"albumName": a["name"]})["id"])
                im._req("PUT", f"/api/albums/{alb}/assets", json={"ids": a["ids"]})
                params = {**a, "album_id": alb, "created": created}
            else:
                im._req("PUT", f"/api/assets/{a['id']}", json={"description": a["text"]})
                params = a
        except ImmichError as e:
            with st.engine.begin() as c:
                c.execute(update(integration_plan).where(integration_plan.c.id == pid).values(status="failed"))
            raise HTTPException(502, detail={"code": e.code, "done": done}) from None
        with st.engine.begin() as c:
            c.execute(
                update(integration_journal).where(integration_journal.c.id == jid).values(state="done", params=params)
            )
        done += 1
    with st.engine.begin() as c:
        c.execute(update(integration_plan).where(integration_plan.c.id == pid).values(status="applied"))
    return {"id": pid, "status": "applied", "writes": done}


@router.post("/integrations/immich/plans/{pid}/undo")
def undo_plan(pid: str, req: Request) -> dict[str, Any]:
    ui_only(req)
    st = _st(req)
    with st.engine.begin() as c:
        p = c.execute(select(integration_plan).where(integration_plan.c.id == pid)).first()
        if p is None or p.status != "applied":
            raise HTTPException(409, detail={"code": "PLAN_NOT_APPLIED"})
        im = client(st, c)
        rows = c.execute(
            select(integration_journal)
            .where(integration_journal.c.plan_id == pid, integration_journal.c.state == "done")
            .order_by(integration_journal.c.seq.desc())
        ).all()
    for r in rows:
        a = r.params
        if r.op == "tag":
            im._req("DELETE", f"/api/tags/{a['tag_id']}/assets", json={"ids": a["ids"]})
        elif r.op == "album_add":
            im._req("DELETE", f"/api/albums/{a['album_id']}/assets", json={"ids": a["ids"]})
        else:
            im._req("PUT", f"/api/assets/{a['id']}", json={"description": a.get("previous") or ""})
        with st.engine.begin() as c:
            c.execute(update(integration_journal).where(integration_journal.c.id == r.id).values(state="undone"))
    with st.engine.begin() as c:
        c.execute(update(integration_plan).where(integration_plan.c.id == pid).values(status="undone"))
    return {"id": pid, "status": "undone", "reversed": len(rows)}
