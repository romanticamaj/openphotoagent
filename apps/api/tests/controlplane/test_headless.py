"""Headless remote worker from the CLI (02 §5.2, §6.3; 09b §8.1 MU-WORKER-001, MU-WORKER-006)."""

import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from opagent_api.app import create_app
from opagent_testing.perms import assert_owner_only
from sqlalchemy import text

FX = Path(__file__).resolve().parents[4] / "fixtures/photos/basic"
PW = "correct horse battery"


def cli(*args, home, check=True):
    r = subprocess.run(
        [sys.executable, "-m", "opagent_cli", "worker", *args, "--home", str(home)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if check:
        assert r.returncode == 0, r.stderr
    return r


def listening_inodes():
    out = set()
    for f in ("/proc/net/tcp", "/proc/net/tcp6", "/proc/net/udp", "/proc/net/udp6"):
        if not os.path.exists(f):
            continue
        for line in Path(f).read_text(encoding="utf-8").splitlines()[1:]:
            cols = line.split()
            state, inode = cols[3], cols[9]
            if f.endswith(("tcp", "tcp6")) and state != "0A":  # TCP LISTEN
                continue
            if f.endswith(("udp", "udp6")) and cols[2] != "00000000:0000" and not cols[2].endswith(":0000"):
                continue  # a connected UDP socket is not listening
            out.add(inode)
    return out


def sockets_of(pid):
    socks = set()
    for fd in os.listdir(f"/proc/{pid}/fd"):
        try:
            link = os.readlink(f"/proc/{pid}/fd/{fd}")
        except OSError:
            continue
        if link.startswith("socket:["):
            socks.add(link[8:-1])
    return socks


@pytest.fixture
def real_cp(embedded_pg, tmp_path, api_server):
    """A control plane on a real port with the real clock (the subprocess signs with its own clock)."""
    app = create_app(
        dsn=embedded_pg.dsn,
        multi_user=True,
        jwt_secret="s" * 40,
        owner=("admin@example.invalid", PW),
        thumbs_dir=tmp_path / "cp-thumbs",
    )
    ui = TestClient(app, base_url="https://testserver")
    r = ui.post("/api/v1/auth/login", json={"email": "admin@example.invalid", "password": PW})
    ui.headers.update({"X-CSRF-Token": r.json()["csrf"], "X-OPA-UI": "1"})
    with api_server(app) as srv:
        yield app, ui, srv.url
    app.state.opa.engine.dispose()


def wait_for(pred, n=600):
    ev = threading.Event()
    for _ in range(n):
        v = pred()
        if v:
            return v
        ev.wait(0.05)
    raise AssertionError("timed out")


def sql(app, q, **p):
    with app.state.opa.engine.begin() as c:
        return c.execute(text(q), p).all()


@pytest.mark.integration
@pytest.mark.tc("MU-WORKER-006")
def test_worker_006_headless_from_cli(real_cp, tmp_path):
    app, ui, url = real_cp
    home = tmp_path / "rw"
    photos = tmp_path / "pics"
    photos.mkdir()
    for i in range(3):
        shutil.copyfile(FX / f"IMG_{i + 1:04d}.jpg", photos / f"{i}.jpg")
    code = ui.post("/api/v1/workers/enrollment-codes", json={"name": "gpu-box"}).json()["code"]
    out = json.loads(cli("enroll", "--server", url, "--code", code, "--name", "gpu-box", home=home).stdout)
    wid = out["worker_id"]
    for f in ("worker.key", "remote.json"):
        assert_owner_only(home / f, 0o600)
    assert b"PRIVATE" not in (home / "remote.json").read_bytes()
    assert cli("enroll", "--server", url, "--code", code, home=tmp_path / "again", check=False).returncode != 0
    cli("roots", "add", "photos", str(photos), "--mode", "ro", home=home)
    cli("run", "--headless", "--once", home=home)
    assert sql(app, "select roots from worker where id = :w", w=wid)[0][0] == [{"alias": "photos", "mode": "ro"}]
    # the CP has no way to add a root: no REST route changes worker roots (invariant 2, C-09)
    writes = [
        (m, p)
        for p, ops in app.openapi()["paths"].items()
        for m in ops
        if m != "get" and p.startswith("/api/v1") and "root" in p
    ]
    assert writes == []
    src = ui.post("/api/v1/sources", json={"root_alias": "photos", "sub_path": ""}).json()["id"]
    assert ui.post(f"/api/v1/sources/{src}/scan").status_code in (200, 202)
    with app.state.opa.engine.begin() as c:  # and a task naming a folder it was not given is refused
        c.execute(
            text(
                "insert into worker_task(id, kind, payload, status, worker_id)"
                " values ('t-etc', 'browse', '{\"root_alias\": \"etc\", \"path\": \"\"}', 'pending', :w)"
            ),
            {"w": wid},
        )
    cli("run", "--headless", "--once", home=home)
    assert len(sql(app, "select id from asset_location")) == 3
    assert sql(app, "select status from worker_task where id = 't-etc'")[0][0] == "failed"


@pytest.mark.integration
@pytest.mark.security
@pytest.mark.tc("MU-WORKER-001")
def test_worker_001_outbound_only(real_cp, tmp_path):
    app, ui, url = real_cp
    home = tmp_path / "rw"
    photos = tmp_path / "pics"
    photos.mkdir()
    for i in range(3):
        shutil.copyfile(FX / f"IMG_{i + 1:04d}.jpg", photos / f"{i}.jpg")
    code = ui.post("/api/v1/workers/enrollment-codes", json={"name": "gpu-box"}).json()["code"]
    wid = json.loads(cli("enroll", "--server", url, "--code", code, home=home).stdout)["worker_id"]
    cli("roots", "add", "photos", str(photos), home=home)
    proc = subprocess.Popen(
        [sys.executable, "-m", "opagent_cli", "worker", "run", "--headless", "--backend", "fake", "--home", str(home)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        wait_for(lambda: sql(app, "select roots from worker where id = :w and roots::text <> '[]'", w=wid))
        src = ui.post("/api/v1/sources", json={"root_alias": "photos", "sub_path": ""}).json()["id"]
        r = ui.post(
            "/api/v1/jobs",
            json={"kind": "organize", "preset": "by-content-12", "source_id": src, "target": {"root_alias": "photos"}},
        )
        assert r.status_code in (200, 201, 202), r.text
        seen, bad = [], []

        def done():
            socks = sockets_of(proc.pid)
            seen.append(socks)
            bad.extend(socks & listening_inodes())
            return len(sql(app, "select id from result where category is not null")) == 3  # scanned and labelled

        wait_for(done)
        assert bad == []  # never a listening socket, during the whole job
        assert any(seen)  # it did talk to the CP (outbound)
    finally:
        proc.terminate()
        out, err = proc.communicate(timeout=20)
    assert proc.returncode in (0, -15), err
