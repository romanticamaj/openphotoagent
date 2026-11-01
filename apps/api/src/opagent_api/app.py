"""FastAPI app factory (docs/02 §5.1, §10.2). The API never touches files or GPUs (invariant 1):
all filesystem work is delegated to the Worker over the worker protocol (worker_task queue)."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from opagent_core.clock import Clock, SystemClock
from opagent_db.schema import engine_for

from . import routes_ops, routes_public, routes_review, routes_worker
from .problems import install_problem_handlers
from .state import AppState


def create_app(
    dsn: str, clock: Clock | None = None, launch_token: str | None = None, static_dir: str | None = None
) -> FastAPI:
    app = FastAPI(title="OpenPhotoAgent API", version="0.1.0-dev")
    st = AppState(engine=engine_for(dsn), clock=clock or SystemClock(), launch_token=launch_token)
    st.ensure_workspace()
    app.state.opa = st
    install_problem_handlers(app)
    app.include_router(routes_public.router, prefix="/api/v1")
    app.include_router(routes_ops.router, prefix="/api/v1")
    app.include_router(routes_review.router, prefix="/api/v1")
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

        app.mount("/", StaticFiles(directory=static_dir, html=True), name="web")
    return app
