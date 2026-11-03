"""Control-plane fixtures: a multi-user app (https base URL, so Secure cookies flow) and logged-in clients."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from opagent_api.app import create_app
from opagent_api.multiuser import create_user
from opagent_testing.clock import FakeClock

SECRET = "test-secret-" + "x" * 32
PW = "correct horse battery"


def login(app, email, password=PW):
    c = TestClient(app, base_url="https://testserver")
    r = c.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    c.headers["X-CSRF-Token"] = r.json()["csrf"]
    c.headers["X-OPA-UI"] = "1"
    return c


@pytest.fixture
def cp_app(embedded_pg):
    clock = FakeClock(datetime(2026, 11, 2, 12, 0, tzinfo=UTC))
    app = create_app(
        dsn=embedded_pg.dsn, clock=clock, multi_user=True, jwt_secret=SECRET, owner=("admin@example.invalid", PW)
    )
    app.clock = clock
    yield app
    st = app.state.opa
    names = []
    for ws, inner in st.ws_apps.items():
        inner.state.opa.engine.dispose()
        names.append(st.workspace_db(ws))
    st.engine.dispose()
    import psycopg
    from opagent_api.state import dbname, with_dbname

    with psycopg.connect(with_dbname(embedded_pg.dsn, "postgres"), autocommit=True) as c:
        for dsn in names:
            c.execute(
                psycopg.sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(psycopg.sql.Identifier(dbname(dsn)))
            )


@pytest.fixture
def login_as(cp_app):
    return lambda email, password=PW: login(cp_app, email, password)


@pytest.fixture
def as_role(cp_app):
    """``as_role("ws_viewer")`` → a client logged in as a fresh member with that role."""

    def make(role):
        email = f"{role}@example.invalid"
        create_user(cp_app.state.opa, email, PW, role=role)
        return login(cp_app, email)

    return make
