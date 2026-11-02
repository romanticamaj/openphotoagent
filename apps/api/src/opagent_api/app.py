"""FastAPI app factory (docs/02 §5.1, §10.2). The API never touches files or GPUs (invariant 1):
all filesystem work is delegated to the Worker over the worker protocol (worker_task queue)."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from opagent_core.clock import Clock, SystemClock
from opagent_db.schema import engine_for

from . import (
    approvals,
    ask,
    faces,
    mcp_server,
    routes_doctor,
    routes_duplicates,
    routes_mcp,
    routes_models,
    routes_ops,
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
    )
    # API connections run as opagent_api; only the face repository reaches the bio schema (ADR-0011)
    faces.install_roles(st.engine)
    st.engine.dispose()
    faces.api_role_hook(st.engine)
    st.ensure_workspace()
    app.state.opa = st
    install_problem_handlers(app)
    from . import security

    app.include_router(security.router)

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        """Identity for port-conflict checks (08 §1.3): which app, which data folder — no secrets."""
        return {"app": "openphotoagent", "version": app.version, "data_fp": data_fp}

    app.add_middleware(security.DesktopGuard, state=st)
    app.include_router(routes_public.router, prefix="/api/v1")
    app.include_router(routes_mcp.router, prefix="/api/v1")
    app.include_router(approvals.router, prefix="/api/v1")
    app.include_router(mcp_server.router)
    app.include_router(faces.router, prefix="/api/v1")
    app.include_router(ask.router, prefix="/api/v1")
    app.include_router(routes_watch.router, prefix="/api/v1")
    app.include_router(routes_watch.worker_router, prefix="/worker-api/v1")
    app.include_router(faces.worker_router, prefix="/worker-api/v1")
    app.include_router(routes_system.router, prefix="/api/v1")
    app.include_router(routes_duplicates.router, prefix="/api/v1")
    app.include_router(routes_ops.router, prefix="/api/v1")
    app.include_router(routes_review.router, prefix="/api/v1")
    app.include_router(routes_models.router, prefix="/api/v1")
    app.include_router(routes_doctor.router, prefix="/api/v1")
    app.include_router(routes_viewer.router, prefix="/api/v1")
    app.include_router(routes_selection.router, prefix="/api/v1")
    app.include_router(search.router, prefix="/api/v1")
    app.include_router(search.worker_router, prefix="/worker-api/v1")
    app.include_router(routes_models.worker_router, prefix="/worker-api/v1")
    app.include_router(routes_worker.router, prefix="/worker-api/v1")

    @app.get("/metrics", include_in_schema=False)
    def metrics() -> Any:
        from fastapi.responses import PlainTextResponse
        from opagent_db.schema import metric
        from sqlalchemy import select

        with st.engine.begin() as c:
            rows = c.execute(select(metric.c.name, metric.c.value)).all()
        return PlainTextResponse("".join(f"{n} {v}\n" for n, v in rows))

    if static_dir:
        from fastapi.staticfiles import StaticFiles
        from starlette.exceptions import HTTPException as StarletteHTTPException

        class SpaFiles(StaticFiles):
            """Client-side routes (``/organize``, ``/review/…``) get index.html; real files are served as-is."""

            async def get_response(self, path: str, scope: Any) -> Any:
                try:
                    return await super().get_response(path, scope)
                except StarletteHTTPException as e:
                    if (
                        e.status_code != 404
                        or path.startswith(("api/", "worker-api/", "mcp/"))
                        or "." in path.rsplit("/", 1)[-1]
                    ):
                        raise
                    return await super().get_response("index.html", scope)

        app.mount("/", SpaFiles(directory=static_dir, html=True), name="web")
    return app
