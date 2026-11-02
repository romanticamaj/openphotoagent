"""Test stand for the MCP endpoint: an API with a registered worker, a library seeded straight into the DB and
helpers to mint tokens (through the UI route) and speak JSON-RPC as ``opagent mcp`` would."""

from __future__ import annotations

import io
import itertools
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from opagent_api.app import create_app
from opagent_db.schema import engine_for
from sqlalchemy import text

UI = {"X-OPA-UI": "1"}
_ids = itertools.count(1)


class M:
    def __init__(self, dsn: str, tmp: Path, clock: Any = None, launch_token: str | None = None) -> None:
        self.thumbs = tmp / "thumbs"
        self.thumbs.mkdir(parents=True, exist_ok=True)
        self.launch_token = launch_token
        self.client = TestClient(
            create_app(dsn=dsn, clock=clock, thumbs_dir=self.thumbs, launch_token=launch_token),
            base_url="http://127.0.0.1:8080",
        )
        self.eng = engine_for(dsn)
        self.ui = dict(UI)
        if launch_token:
            self.ui["Authorization"] = f"Bearer {launch_token}"
        roots = [{"alias": "photos", "mode": "ro"}, {"alias": "sorted", "mode": "rw"}, {"alias": "inbox", "mode": "rw"}]
        r = self.client.post("/worker-api/v1/register", json={"worker_id": "w1", "roots": roots}, headers=self.ui)
        assert r.status_code == 200, r.text
        self.src = self._source("photos")
        self.inbox = self._source("inbox")

    def _source(self, alias: str) -> str:
        r = self.client.post("/api/v1/sources", json={"root_alias": alias, "sub_path": ""}, headers=self.ui)
        assert r.status_code in (200, 201), r.text
        return str(r.json()["id"])

    def sql(self, q: str, **p: Any) -> Any:
        with self.eng.begin() as c:
            return c.execute(text(q), p)

    # ---- library
    def add(
        self,
        rel: str,
        caption: str = "",
        tags: tuple[str, ...] = (),
        category: str | None = None,
        taken: datetime | None = None,
        exif: dict[str, Any] | None = None,
        source: str | None = None,
        thumb: bool = True,
        size: int = 1000,
    ) -> str:
        n = next(_ids)
        aid = f"a{n:05d}"
        taken = taken or datetime(2025, 1, 1, tzinfo=UTC) + timedelta(days=n)
        self.sql(
            "insert into asset(id, workspace_id, content_hash, size_bytes, taken_at, date_source, width, height, exif)"
            " values (:a,'default',:h,:s,:t,'exif_offset',4000,3000,CAST(:e AS jsonb))",
            a=aid,
            h=f"h{n:05d}",
            s=size,
            t=taken,
            e=json.dumps(exif or {}),
        )
        self.sql(
            "insert into asset_location(id, asset_id, source_id, rel_path, mtime, missing, size)"
            " values (:i,:a,:s,:r,:t,false,:z)",
            i=f"l{n}",
            a=aid,
            s=source or self.src,
            r=rel,
            t=taken,
            z=size,
        )
        self.sql(
            "insert into result(id, asset_id, task_config_hash, model_fingerprint, task_type, category, confidence,"
            " caption, tags, raw) values (:i,:a,'c','f','classify',:cat,0.95,:cap,CAST(:tags AS jsonb),'{}')",
            i=f"r{n}",
            a=aid,
            cat=category,
            cap=caption,
            tags=json.dumps(list(tags)),
        )
        self.sql("insert into index_outbox(asset_id) values (:a)", a=aid)
        if thumb:
            from PIL import Image

            buf = io.BytesIO()
            Image.new("RGB", (512, 384), (n * 37 % 255, 90, 160)).save(buf, "JPEG")
            (self.thumbs / f"h{n:05d}.jpg").write_bytes(buf.getvalue())
        return aid

    def index(self) -> None:
        from opagent_api.labels import drain_index

        with self.eng.begin() as c:
            drain_index(c)

    # ---- tokens and settings
    def token(self, scopes: list[str] | None = None, folders: list[dict[str, str]] | None = None, **kw: Any) -> str:
        body: dict[str, Any] = {"name": kw.pop("name", "Claude Desktop"), **kw}
        if scopes is not None:
            body["scopes"] = scopes
        if folders is not None:
            body["folders"] = folders
        r = self.client.post("/api/v1/mcp/tokens", json=body, headers=self.ui)
        assert r.status_code == 201, r.text
        self.last_token = r.json()
        return str(r.json()["token"])

    def settings(self, **kw: Any) -> None:
        r = self.client.put("/api/v1/mcp/settings", json=kw, headers=self.ui)
        assert r.status_code == 200, r.text

    # ---- JSON-RPC
    def rpc(self, pat: str | None, method: str, params: dict[str, Any] | None = None, **headers: str) -> Any:
        h = {"Authorization": f"Bearer {pat}"} if pat else {}
        h.update({k.replace("_", "-"): v for k, v in headers.items()})
        body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
        return self.client.post("/mcp/v1/rpc", json=body, headers=h)

    def list_tools(self, pat: str) -> list[dict[str, Any]]:
        r = self.rpc(pat, "tools/list")
        assert r.status_code == 200, r.text
        return list(r.json()["result"]["tools"])

    def call(self, pat: str, tool: str, args: dict[str, Any] | None = None, **headers: str) -> dict[str, Any]:
        r = self.rpc(pat, "tools/call", {"name": tool, "arguments": args or {}}, **headers)
        assert r.status_code == 200, r.text
        return dict(r.json())

    def ok(self, pat: str, tool: str, args: dict[str, Any] | None = None, **headers: str) -> dict[str, Any]:
        out = self.call(pat, tool, args, **headers)
        assert "result" in out and not out["result"]["isError"], out
        return dict(out["result"]["structuredContent"])

    def calls(self) -> list[Any]:
        return list(self.sql("select * from mcp_call_log order by id").all())
