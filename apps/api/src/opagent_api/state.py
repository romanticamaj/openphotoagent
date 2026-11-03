from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from opagent_core.clock import Clock
from opagent_db.schema import workspace
from sqlalchemy import insert, select
from sqlalchemy.engine import Engine

DEFAULT_WS = "default"


def with_dbname(dsn: str, name: str) -> str:
    """The same server and credentials, another database (URL-form DSNs, as everywhere in the app)."""
    from urllib.parse import urlsplit, urlunsplit

    u = urlsplit(dsn)
    return urlunsplit((u.scheme, u.netloc, "/" + name, u.query, u.fragment))


def dbname(dsn: str) -> str:
    from urllib.parse import urlsplit

    return urlsplit(dsn).path.lstrip("/") or "opagent"


@dataclass
class AppState:
    engine: Engine
    clock: Clock
    launch_token: str | None = None
    lan_password_hash: str | None = None  # set only when "allow LAN access" is on (08 §1.3)
    backups_dir: Path | None = None  # desktop: Settings → Data backups (08 §2.3)
    pg_bin: Path | None = None
    dsn: str = ""
    ask_turns: dict[str, Any] = field(default_factory=dict)  # running assistant turns (cancel flags)
    llm: Any = None  # the assistant's model (llama-server / OpenAI-compatible); FakeLLM in tests
    llm_cache: dict[str, Any] | None = None  # configured endpoint clients, by URL
    llm_transport: Any = None  # tests: an httpx transport standing in for the endpoint
    ask_tool_delay_s: float = 0.0  # test hook: a slow tool
    vlm_wait_s: float = 5.0  # how long a describe/verify call waits for the worker before answering "queued"
    face_queries: dict[str, Any] = field(default_factory=dict)  # 「拖照片找人」 vectors, memory only (FACE-FIND-002)
    blobs: dict[str, bytes] = field(default_factory=dict)  # uploaded query images, memory only (SRCH-IMG-002)
    handoffs: dict[str, Any] = field(default_factory=dict)  # one-time browser handoff codes → expiry
    login_failures: dict[str, list[Any]] = field(default_factory=dict)  # LAN login rate limit per client
    sessions: set[str] = field(default_factory=set)  # browser sessions opened from the tray
    registry_dir: Path | None = None
    user_models_dir: Path | None = None
    thumbs_dir: Path | None = None
    home: Path | None = None  # data folder: the local MCP credential fallback lives here (MCP-PAT-001)
    public_url: str = "http://127.0.0.1:47800"  # web/Docker approval links (05 §3.6)
    multi_user: bool = False  # the self-hosted control plane (v0.5, multiuser.py)
    jwt_secret: str | None = None  # signs worker access JWTs; from OPAGENT_JWT_SECRET
    insecure_cookies: bool = False  # dev only: plain-http control plane (never in the Docker image)
    protocol_version: str = "1.0"  # worker protocol this CP speaks (N and N-1 majors are served, 02 §5.3)
    ws_id: str = DEFAULT_WS  # control plane: which workspace this state serves (ADR-0016)
    control: Any = None  # inner workspace apps: the control plane's state (users, roles, credentials)
    ws_apps: dict[str, Any] = field(default_factory=dict)  # control plane: workspace id → inner FastAPI app
    app_kwargs: dict[str, Any] = field(default_factory=dict)  # how to build an inner workspace app
    fastapi_app: Any = None  # for the control-plane guard's route table
    enroll_guesses: dict[str, list[Any]] = field(default_factory=dict)  # wrong enrollment codes per client
    handle_key: bytes = field(default_factory=lambda: __import__("secrets").token_bytes(32))  # MCP cursors

    @property
    def root(self) -> AppState:
        """The control-plane state (auth, roles, worker credentials live only there)."""
        return self.control if self.control is not None else self

    def workspace_db(self, ws: str) -> str | None:
        """The DSN of a workspace, or ``None`` when it does not exist (looked up in the control database)."""
        if ws == DEFAULT_WS:
            return self.root.dsn
        from opagent_db.schema import workspace as w

        with self.root.engine.begin() as c:
            name = c.execute(select(w.c.database).where(w.c.id == ws, w.c.database.is_not(None))).scalar()
        if not name:
            return None
        return with_dbname(self.root.dsn, name)

    def ws_app(self, ws: str) -> Any:
        """The FastAPI app answering for ``ws`` (built lazily); ``None`` for an unknown workspace."""
        root = self.root
        if ws == DEFAULT_WS:
            return root.fastapi_app
        if ws not in root.ws_apps:
            dsn = root.workspace_db(ws)
            if dsn is None:
                return None
            from .app import create_app

            kw = dict(root.app_kwargs)
            if root.thumbs_dir is not None:
                kw["thumbs_dir"] = root.thumbs_dir / ws
            root.ws_apps[ws] = create_app(dsn=dsn, clock=root.clock, tenant_of=root, ws_id=ws, **kw)
        return root.ws_apps[ws]

    def ws_state(self, ws: str) -> AppState:
        app = self.ws_app(ws)
        if app is None:
            raise KeyError(ws)
        return app.state.opa  # type: ignore[no-any-return]

    def all_ws(self) -> list[str]:
        from opagent_db.schema import workspace as w

        with self.root.engine.begin() as c:
            extra = c.execute(select(w.c.id).where(w.c.database.is_not(None)).order_by(w.c.id)).scalars().all()
        return [DEFAULT_WS, *extra]

    def workspace_settings(self, c: Any) -> dict[str, Any]:
        return dict(c.execute(select(workspace.c.settings).where(workspace.c.id == DEFAULT_WS)).scalar() or {})

    def registry(self) -> Any:
        """Built-in catalog + pins + the user's YAML (re-read so a new custom model shows up, INF-REG-003)."""
        from opagent_registry.loader import load_registry

        users = [self.user_models_dir] if self.user_models_dir else []
        return load_registry(self.registry_dir, user_dirs=users)

    def ensure_workspace(self) -> None:
        with self.engine.begin() as c:
            if c.execute(select(workspace.c.id).where(workspace.c.id == DEFAULT_WS)).first() is None:
                c.execute(insert(workspace).values(id=DEFAULT_WS, name="Default", settings={}))
