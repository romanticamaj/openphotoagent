"""Favourites and albums (ADR-0041). Database sets, never files (05 §3.3.5): adding a photo to an album or marking it a
favourite changes nothing on disk (invariant 4), and deleting an album keeps its photos. 「收藏」 is one fixed album per
workspace, created on first use; it can be emptied, not deleted."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from opagent_db.schema import asset, collection, collection_member, search_doc
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from .state import AppState

router = APIRouter()
DEFAULT_WS = "default"
FAVORITES = "fav-default"
FAVORITES_NAME = "收藏"


def _st(req: Request) -> AppState:
    st: AppState = req.app.state.opa
    return st


def _name(raw: str) -> str:
    """Plain text: never a path template or a path (INJ-017), at most 100 characters."""
    from opagent_core.path_template import sanitize_segment

    plain = raw.replace("{", "（").replace("}", "）").replace("/", "／").replace("\\", "＼").strip()
    if not plain:  # checked before sanitising, which turns "" into a placeholder
        raise HTTPException(422, detail={"code": "ALBUM_NAME_REQUIRED"})
    return sanitize_segment(plain).strip()[:100]


def _favorites(c: Any) -> str:
    c.execute(
        pg_insert(collection)
        .values(id=FAVORITES, workspace_id=DEFAULT_WS, name=FAVORITES_NAME)
        .on_conflict_do_nothing(index_elements=["id"])
    )
    return FAVORITES


def _album(c: Any, album_id: str) -> Any:
    row = c.execute(select(collection).where(collection.c.id == album_id)).first()
    if row is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND"})
    return row


def _known(c: Any, ids: list[str]) -> None:
    found = {r[0] for r in c.execute(select(asset.c.id).where(asset.c.id.in_(ids or ["-"])))}
    if set(ids) - found:
        raise HTTPException(404, detail={"code": "NOT_FOUND"})


def _add(c: Any, album_id: str, ids: list[str]) -> list[str]:
    """The photos it added, not those already there: what 復原 takes out again (COL-017)."""
    added = []
    for a in dict.fromkeys(ids):
        r = c.execute(
            pg_insert(collection_member)
            .values(collection_id=album_id, asset_id=a)
            .on_conflict_do_nothing(index_elements=["collection_id", "asset_id"])
            .returning(collection_member.c.asset_id)  # a row only when it went in: rowcount can be -1
        ).first()
        if r is not None:
            added.append(a)
    return added


def _count(c: Any, album_id: str) -> int:
    return int(
        c.execute(
            select(func.count()).select_from(collection_member).where(collection_member.c.collection_id == album_id)
        ).scalar_one()
    )


def asset_albums(c: Any, asset_id: str) -> tuple[bool, list[dict[str, str]]]:
    """For the info panel: whether the photo is a favourite, and the albums it is in."""
    rows = c.execute(
        select(collection.c.id, collection.c.name)
        .join(collection_member, collection_member.c.collection_id == collection.c.id)
        .where(collection_member.c.asset_id == asset_id)
        .order_by(collection.c.name)
    ).all()
    return any(r.id == FAVORITES for r in rows), [{"id": r.id, "name": r.name} for r in rows if r.id != FAVORITES]


@router.get("/albums")
def list_albums(req: Request) -> dict[str, Any]:
    with _st(req).engine.begin() as c:
        _favorites(c)
        rows = c.execute(
            select(collection.c.id, collection.c.name, collection.c.created_at)
            .where(collection.c.workspace_id == DEFAULT_WS)
            .order_by(collection.c.created_at.desc())
        ).all()
        out = []
        for r in rows:
            cover = c.execute(
                select(collection_member.c.asset_id, asset.c.content_hash)
                .join(asset, asset.c.id == collection_member.c.asset_id)
                .where(collection_member.c.collection_id == r.id)
                .order_by(collection_member.c.added_at.desc(), collection_member.c.asset_id)
                .limit(1)
            ).first()
            out.append(
                {
                    "id": r.id,
                    "name": r.name,
                    "kind": "favorites" if r.id == FAVORITES else "album",
                    "count": _count(c, r.id),
                    "cover": {"asset_id": cover.asset_id, "ch": cover.content_hash} if cover else None,
                }
            )
    out.sort(key=lambda a: a["kind"] != "favorites")  # 收藏 first, then the newest albums
    return {"albums": out}


class AlbumIn(BaseModel):
    name: str = Field(max_length=200)


@router.post("/albums", status_code=201)
def create_album(body: AlbumIn, req: Request) -> dict[str, Any]:
    from opagent_core.ids import uuid7

    st = _st(req)
    name = _name(body.name)
    aid = uuid7(st.clock)
    with st.engine.begin() as c:
        c.execute(collection.insert().values(id=aid, workspace_id=DEFAULT_WS, name=name))
    return {"id": aid, "name": name, "count": 0}


@router.get("/albums/{album_id}")
def get_album(album_id: str, req: Request) -> dict[str, Any]:
    with _st(req).engine.begin() as c:
        if album_id == FAVORITES:
            _favorites(c)
        row = _album(c, album_id)
        # the date the timeline shows: the capture date, or the file time it is indexed by (COL-023)
        when = func.coalesce(asset.c.taken_at, search_doc.c.taken_at).label("taken_at")
        photos = c.execute(
            select(collection_member.c.asset_id, asset.c.content_hash, when, asset.c.rotation)
            .join(asset, asset.c.id == collection_member.c.asset_id)
            .outerjoin(search_doc, search_doc.c.asset_id == asset.c.id)
            .where(collection_member.c.collection_id == album_id)
            .order_by(collection_member.c.added_at.desc(), collection_member.c.asset_id)
        ).all()
    return {
        "id": row.id,
        "name": row.name,
        "kind": "favorites" if row.id == FAVORITES else "album",
        "photos": [
            {
                "asset_id": p.asset_id,
                "ch": p.content_hash,
                "taken_at": p.taken_at.isoformat() if p.taken_at else None,
                "rot": p.rotation,  # turned in the app (ROT-001)
            }
            for p in photos
        ],
    }


@router.patch("/albums/{album_id}")
def rename_album(album_id: str, body: AlbumIn, req: Request) -> dict[str, Any]:
    name = _name(body.name)
    with _st(req).engine.begin() as c:
        _album(c, album_id)
        if album_id == FAVORITES:
            raise HTTPException(409, detail={"code": "ALBUM_FIXED"})
        c.execute(update(collection).where(collection.c.id == album_id).values(name=name))
    return {"id": album_id, "name": name}


@router.delete("/albums/{album_id}")
def delete_album(album_id: str, req: Request) -> dict[str, Any]:
    with _st(req).engine.begin() as c:
        if album_id == FAVORITES:
            raise HTTPException(409, detail={"code": "ALBUM_FIXED"})  # 收藏 is emptied, not deleted
        _album(c, album_id)
        c.execute(delete(collection).where(collection.c.id == album_id))  # members go with it; the photos stay
    return {"deleted": album_id}


class PhotosIn(BaseModel):
    asset_ids: list[str] = Field(min_length=1, max_length=500)


@router.post("/albums/{album_id}/photos")
def add_photos(album_id: str, body: PhotosIn, req: Request) -> dict[str, Any]:
    with _st(req).engine.begin() as c:
        if album_id == FAVORITES:
            _favorites(c)
        _album(c, album_id)
        _known(c, body.asset_ids)
        _add(c, album_id, body.asset_ids)
        return {"id": album_id, "count": _count(c, album_id)}


@router.delete("/albums/{album_id}/photos/{asset_id}")
def remove_photo(album_id: str, asset_id: str, req: Request) -> dict[str, Any]:
    with _st(req).engine.begin() as c:
        _album(c, album_id)
        c.execute(
            delete(collection_member).where(
                collection_member.c.collection_id == album_id, collection_member.c.asset_id == asset_id
            )
        )
        return {"id": album_id, "count": _count(c, album_id)}


@router.put("/assets/{asset_id}/favorite")
def favorite(asset_id: str, req: Request) -> dict[str, Any]:
    with _st(req).engine.begin() as c:
        _known(c, [asset_id])
        _add(c, _favorites(c), [asset_id])
    return {"asset_id": asset_id, "favorite": True}


@router.delete("/assets/{asset_id}/favorite")
def unfavorite(asset_id: str, req: Request) -> dict[str, Any]:
    with _st(req).engine.begin() as c:
        c.execute(
            delete(collection_member).where(
                collection_member.c.collection_id == FAVORITES, collection_member.c.asset_id == asset_id
            )
        )
    return {"asset_id": asset_id, "favorite": False}


@router.post("/albums/{album_id}/photos/remove")
def remove_photos(album_id: str, body: PhotosIn, req: Request) -> dict[str, Any]:
    """Several photos out at once: undoing a batch add (COL-017). The photos themselves stay."""
    with _st(req).engine.begin() as c:
        if album_id == FAVORITES:
            _favorites(c)
        _album(c, album_id)
        c.execute(
            delete(collection_member).where(
                collection_member.c.collection_id == album_id, collection_member.c.asset_id.in_(body.asset_ids)
            )
        )
        return {"id": album_id, "count": _count(c, album_id)}
