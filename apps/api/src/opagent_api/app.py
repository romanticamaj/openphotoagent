"""FastAPI app factory (docs/02 §5.1, §10.2)."""

from __future__ import annotations

from fastapi import FastAPI


def create_app(dsn: str) -> FastAPI:
    return FastAPI(title="OpenPhotoAgent API")
