import pytest
from fastapi.testclient import TestClient
from opagent_api.app import create_app


@pytest.mark.integration
def test_client_routes_fall_back_to_the_web_build(embedded_pg, tmp_path):
    """Deep links such as /organize reload into the SPA; API 404s stay JSON problems (02 §5)."""
    (tmp_path / "index.html").write_text("<!doctype html><div id=root></div>", encoding="utf-8")
    (tmp_path / "app.js").write_text("1", encoding="utf-8")
    c = TestClient(create_app(dsn=embedded_pg.dsn, static_dir=str(tmp_path)))
    assert "id=root" in c.get("/organize").text
    assert "id=root" in c.get("/review/abc").text
    assert c.get("/app.js").text == "1"
    r = c.get("/api/v1/nope")
    assert r.status_code == 404 and r.headers["content-type"].startswith("application/")
    assert "id=root" not in r.text


@pytest.mark.integration
@pytest.mark.tc("VIEW-012")
@pytest.mark.parametrize("path", ["/api/v1/nope", "/api/v1/deep/er/nope", "/worker-api/v1/nope", "/mcp/nope"])
def test_view_012_unknown_api_paths_are_never_the_web_app(embedded_pg, tmp_path, path):
    """回歸: on Windows StaticFiles hands over ``api\\v1\\nope``, so the ``api/`` check never matched and an API
    client got index.html with a 200 for every unknown route."""
    (tmp_path / "index.html").write_text("<!doctype html><div id=root></div>", encoding="utf-8")
    c = TestClient(create_app(dsn=embedded_pg.dsn, static_dir=str(tmp_path)))
    r = c.get(path)
    assert r.status_code == 404 and "id=root" not in r.text


@pytest.mark.integration
@pytest.mark.tc("VIEW-012")
def test_view_012_a_dotted_folder_in_a_client_route_is_still_the_web_app(embedded_pg, tmp_path):
    (tmp_path / "index.html").write_text("<!doctype html><div id=root></div>", encoding="utf-8")
    c = TestClient(create_app(dsn=embedded_pg.dsn, static_dir=str(tmp_path)))
    assert "id=root" in c.get("/review/v1.2/abc").text  # only the last segment decides "this is a file"
