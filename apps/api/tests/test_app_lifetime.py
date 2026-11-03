"""Regression: an app must be collectable once nothing references it (09a PERF-008)."""

import gc

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opagent_api.app import create_app


@pytest.mark.integration
@pytest.mark.tc("PERF-008")
def test_perf_008_released_apps_are_collected(embedded_pg):
    gc.collect()
    before = sum(isinstance(o, FastAPI) for o in gc.get_objects())
    for _ in range(3):
        app = create_app(dsn=embedded_pg.dsn)
        assert TestClient(app).get("/healthz").json()["app"] == "openphotoagent"
        app.state.opa.engine.dispose()
        del app
    gc.collect()
    assert sum(isinstance(o, FastAPI) for o in gc.get_objects()) == before
