"""FastAPI app factory (docs/02 §5.1, §10.2). The API never touches files or GPUs (invariant 1):
all filesystem work is delegated to the Worker over the worker protocol (worker_task queue)."""

from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, Request
from opagent_core.clock import Clock, SystemClock
from opagent_db.schema import engine_for

from . import (
    approvals,
    ask,
    faces,
    immich,
    labelstudio,
    mcp_server,
    routes_basemap,
    routes_bench,
    routes_caption,
    routes_doctor,
    routes_duplicates,
    routes_mcp,
    routes_models,
    routes_ops,
    routes_presets,
    routes_public,
    routes_review,
    routes_selection,
    routes_system,
    routes_viewer,
    routes_watch,
    routes_worker,
    search,
)
from .problems import install_problem_handlers
from .state import AppState


# Endpoints are module-level functions that read the app from the request: FastAPI caches endpoint callables in a
# module-level lru_cache, so a closure over ``app`` would keep every app (and its engine) alive for the process.
def healthz(req: Request) -> dict[str, Any]:
    """Identity for port-conflict checks (08 §1.3): which app, which data folder — no secrets."""
    return {"app": "openphotoagent", "version": req.app.version, "data_fp": req.app.state.opa.data_fp}


def metrics(req: Request) -> Any:
    from fastapi.responses import PlainTextResponse
    from opagent_db.schema import metric
    from sqlalchemy import select

    with req.app.state.opa.engine.begin() as c:
        rows = c.execute(select(metric.c.name, metric.c.value)).all()
    return PlainTextResponse("".join(f"{n} {v}\n" for n, v in rows))


def create_app(
    dsn: str,
    clock: Clock | None = None,
    launch_token: str | None = None,
    static_dir: str | None = None,
    registry_dir: Any = None,
    user_models_dir: Any = None,
    thumbs_dir: Any = None,
    data_fp: str | None = None,
    lan_password_hash: str | None = None,
    backups_dir: Any = None,
    pg_bin: Any = None,
    home: Any = None,
    multi_user: bool = False,
    jwt_secret: str | None = None,
    owner: tuple[str, str] | None = None,
    insecure_cookies: bool = False,
    tenant_of: AppState | None = None,
    public_url: str | None = None,
    oidc: dict[str, Any] | None = None,
    ws_id: str = "default",
) -> FastAPI:
    from opagent_core.version import VERSION

    app = FastAPI(title="OpenPhotoAgent API", version=VERSION)
    from pathlib import Path

    st = AppState(
        engine=engine_for(dsn),
        clock=clock or SystemClock(),
        launch_token=launch_token,
        lan_password_hash=lan_password_hash,
        backups_dir=Path(backups_dir) if backups_dir else None,
        pg_bin=Path(pg_bin) if pg_bin else None,
        dsn=dsn,
        registry_dir=Path(registry_dir) if registry_dir else None,
        user_models_dir=Path(user_models_dir) if user_models_dir else None,
        thumbs_dir=Path(thumbs_dir) if thumbs_dir else None,
        home=Path(home) if home else None,
        multi_user=multi_user,
        jwt_secret=jwt_secret,
        insecure_cookies=insecure_cookies,
    )
    if public_url:
        st.public_url = public_url.rstrip("/")
    st.oidc = oidc
    if tenant_of is not None:  # an inner workspace app behind the control plane's guard (ADR-0016)
        st.multi_user, st.control, st.ws_id, st.jwt_secret = True, tenant_of, ws_id, tenant_of.jwt_secret
        st.insecure_cookies = tenant_of.insecure_cookies
    elif multi_user:
        st.app_kwargs = {
            "registry_dir": registry_dir,
            "user_models_dir": user_models_dir,
            "thumbs_dir": None,
            "backups_dir": backups_dir,
            "pg_bin": pg_bin,
        }
    if multi_user and (not jwt_secret or len(jwt_secret) < 32):
        raise ValueError("multi-user mode needs a jwt_secret of at least 32 characters")
    # API connections run as opagent_api; only the face repository reaches the bio schema (ADR-0011)
    faces.install_roles(st.engine)
    st.engine.dispose()
    faces.api_role_hook(st.engine)
    st.ensure_workspace()
    if multi_user and tenant_of is None:
        from .multiuser import bootstrap_owner

        bootstrap_owner(st, owner)  # an upgraded single-user database: its owner becomes org_owner (MU-AUTH-003)
    app.state.opa = st
    st.fastapi_app = app
    install_problem_handlers(app)
    from . import security

    app.include_router(security.router)

    st.data_fp = data_fp
    app.add_api_route("/healthz", healthz, methods=["GET"])

    app.add_middleware(security.DesktopGuard, state=st)
    from . import multiuser

    if multi_user and tenant_of is None:
        app.add_middleware(multiuser.ControlPlaneGuard, state=st)
        app.include_router(multiuser.router, prefix="/api/v1")
        app.include_router(multiuser.worker_router, prefix="/worker-api/v1")
        from . import oauth

        app.include_router(oauth.router)
        from . import oidc as oidc_routes

        app.include_router(oidc_routes.router, prefix="/api/v1")
    if multi_user or tenant_of is not None:
        from . import oauth

        app.include_router(oauth.mcp_router)
    app.include_router(routes_public.router, prefix="/api/v1")
    app.include_router(routes_mcp.router, prefix="/api/v1")
    app.include_router(approvals.router, prefix="/api/v1")
    app.include_router(mcp_server.router)
    app.include_router(faces.router, prefix="/api/v1")
    app.include_router(ask.router, prefix="/api/v1")
    app.include_router(routes_watch.router, prefix="/api/v1")
    app.include_router(routes_bench.router, prefix="/api/v1")
    app.include_router(routes_presets.router, prefix="/api/v1")
    app.include_router(immich.router, prefix="/api/v1")
    app.include_router(labelstudio.router, prefix="/api/v1")
    app.include_router(routes_caption.router, prefix="/api/v1")
    app.include_router(routes_watch.worker_router, prefix="/worker-api/v1")
    app.include_router(faces.worker_router, prefix="/worker-api/v1")
    app.include_router(routes_system.router, prefix="/api/v1")
    app.include_router(routes_duplicates.router, prefix="/api/v1")
    app.include_router(routes_ops.router, prefix="/api/v1")
    app.include_router(routes_review.router, prefix="/api/v1")
    app.include_router(routes_models.router, prefix="/api/v1")
    app.include_router(routes_doctor.router, prefix="/api/v1")
    app.include_router(routes_viewer.router, prefix="/api/v1")
    app.include_router(routes_basemap.router, prefix="/api/v1")
    app.include_router(routes_selection.router, prefix="/api/v1")
    app.include_router(search.router, prefix="/api/v1")
    app.include_router(search.worker_router, prefix="/worker-api/v1")
    app.include_router(routes_models.worker_router, prefix="/worker-api/v1")
    app.include_router(routes_worker.router, prefix="/worker-api/v1")
    from . import routes_remote

    app.include_router(routes_remote.worker_router, prefix="/worker-api/v1")
    app.include_router(routes_remote.router, prefix="/api/v1")

    app.add_api_route("/metrics", metrics, methods=["GET"], include_in_schema=False)

    if static_dir:
        from fastapi.staticfiles import StaticFiles
        from starlette.exceptions import HTTPException as StarletteHTTPException

        class SpaFiles(StaticFiles):
            """Client-side routes (``/organize``, ``/review/…``) get index.html; real files are served as-is."""

            async def get_response(self, path: str, scope: Any) -> Any:
                try:
                    return await super().get_response(path, scope)
                except StarletteHTTPException as e:
                    url = path.replace(os.sep, "/")  # StaticFiles hands over an OS path: api\\v1\\x on Windows
                    if (
                        e.status_code != 404
                        or url.startswith(("api/", "worker-api/", "mcp/"))
                        or "." in url.rsplit("/", 1)[-1]
                    ):
                        raise
                    return await super().get_response("index.html", scope)

        app.mount("/", SpaFiles(directory=static_dir, html=True), name="web")
    return app
