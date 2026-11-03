"""RFC 9457 problem+json for every error (docs/02 §10.2, 06 §4.6; PF-082)."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

MEDIA = "application/problem+json"
RESERVED = ("type", "title", "status")  # RFC 9457 members set by `problem` itself


def problem(status: int, code: str, **extra: Any) -> JSONResponse:
    body = {"type": "about:blank", "title": code, "status": status, "code": code, **extra}
    return JSONResponse(body, status_code=status, media_type=MEDIA)


def install_problem_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        d = exc.detail
        if isinstance(d, dict) and "code" in d:
            extra = {k: v for k, v in d.items() if k not in RESERVED}
            clash = {k: v for k, v in d.items() if k in RESERVED and k != "code"}
            if clash:  # e.g. PLAN_NOT_DRAFT's plan `status`: the message parameter, never an RFC 9457 member (PF-085)
                extra["params"] = {**(extra.get("params") or {}), **clash}
            return problem(exc.status_code, **extra)
        return problem(
            exc.status_code, "NOT_FOUND" if exc.status_code == 404 else f"HTTP_{exc.status_code}", detail=str(d)
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        return problem(422, "VALIDATION_ERROR", errors=exc.errors())
