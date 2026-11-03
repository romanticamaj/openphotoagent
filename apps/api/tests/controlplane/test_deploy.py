"""Control plane + remote worker, end to end (09b §8.5 MU-DEPLOY-001).

* ``test_deploy_001_two_processes``: the CP (``opagent controlplane``) and a headless worker (``opagent worker
  run``) as separate processes with separate data folders, talking only over HTTP/WSS.
* ``test_deploy_001_compose`` (nightly, needs a Docker daemon): the same flow with ``deploy/compose/controlplane.yml``
  and ``worker.yml`` in different networks.
"""

import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
from pathlib import Path

import httpx
import pytest

FX = Path(__file__).resolve().parents[4] / "fixtures/photos/basic"
ROOT = Path(__file__).resolve().parents[4]
PW = "correct horse battery"


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def wait_for(pred, n=1200, what="condition"):
    ev = threading.Event()
    last = None
    for _ in range(n):
        try:
            last = pred()
        except (httpx.HTTPError, KeyError, ValueError) as e:
            last = e
        if last and not isinstance(last, Exception):
            return last
        ev.wait(0.05)
    raise AssertionError(f"timed out waiting for {what}: {last!r}")


def digest(folder):
    return {
        p.relative_to(folder).as_posix(): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
        for p in sorted(folder.rglob("*"))
        if p.is_file()
    }


class Ui:
    """A browser stand-in over plain http: carries the Secure session cookie itself (TLS is the proxy's job)."""

    def __init__(self, base):
        self.c = httpx.Client(base_url=base, timeout=30)
        r = self.c.post("/api/v1/auth/login", json={"email": "owner@example.invalid", "password": PW})
        assert r.status_code == 200, r.text
        sid = r.headers["set-cookie"].split(";", 1)[0]
        self.h = {"Cookie": sid, "X-CSRF-Token": r.json()["csrf"], "X-OPA-UI": "1"}

    def get(self, path, **kw):
        return self.c.get(path, headers=self.h, **kw)

    def post(self, path, **kw):
        return self.c.post(path, headers=self.h, **kw)


def flow(ui, enroll_and_start, photos, sorted_dir):
    """pair → scan → label → plan → approve → apply → undo, with the originals untouched."""
    code = ui.post("/api/v1/workers/enrollment-codes", json={"name": "gpu-box"}).json()["code"]
    wid = enroll_and_start(code)
    wait_for(lambda: [w for w in ui.get("/api/v1/workers").json() if w["id"] == wid and w.get("roots")], what="worker")
    before = digest(photos)
    src = ui.post("/api/v1/sources", json={"root_alias": "photos", "sub_path": ""})
    assert src.status_code == 201, src.text
    j = ui.post(
        "/api/v1/jobs",
        json={
            "kind": "organize",
            "source_id": src.json()["id"],
            "preset": "by-date",
            "target": {"root_alias": "sorted"},
        },
    )
    assert j.status_code in (200, 201, 202), j.text
    jid = j.json()["id"]
    plan = wait_for(lambda: ui.get(f"/api/v1/jobs/{jid}").json().get("plan_id"), what="plan")
    assert ui.post(f"/api/v1/output-plans/{plan}/approve", json={}).status_code in (200, 202)
    wait_for(lambda: len([p for p in sorted_dir.rglob("*") if p.is_file()]) == 3, what="apply")
    assert digest(photos) == before  # copy is the default: originals unchanged, mtime included (invariant 4)
    assert ui.post(f"/api/v1/output-plans/{plan}/undo").status_code in (200, 202)
    wait_for(lambda: not [p for p in sorted_dir.rglob("*") if p.is_file()], what="undo")
    assert digest(photos) == before
    return wid


@pytest.mark.integration
@pytest.mark.tc("MU-DEPLOY-001")
def test_deploy_001_two_processes(embedded_pg, tmp_path):
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    env = {
        **os.environ,
        "OPAGENT_JWT_SECRET": "x" * 48,
        "OPAGENT_OWNER_EMAIL": "owner@example.invalid",
        "OPAGENT_OWNER_PASSWORD": PW,
        "OPAGENT_PUBLIC_URL": base,
    }
    cp = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "opagent_cli",
            "controlplane",
            "--dsn",
            embedded_pg.dsn,
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--home",
            str(tmp_path / "cp"),
            "--schema",
            "create-all",
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    procs = [cp]
    photos, sorted_dir = tmp_path / "pics", tmp_path / "sorted"
    photos.mkdir()
    sorted_dir.mkdir()
    for i in range(3):
        shutil.copyfile(FX / f"IMG_{i + 1:04d}.jpg", photos / f"{i}.jpg")
    home = tmp_path / "worker"

    def worker(*args):
        r = subprocess.run(
            [sys.executable, "-m", "opagent_cli", "worker", *args, "--home", str(home)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert r.returncode == 0, r.stderr
        return r.stdout

    def enroll_and_start(code):
        wid = json.loads(worker("enroll", "--server", base, "--code", code))["worker_id"]
        worker("roots", "add", "photos", str(photos), "--mode", "ro")
        worker("roots", "add", "sorted", str(sorted_dir), "--mode", "rw")
        procs.append(
            subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "opagent_cli",
                    "worker",
                    "run",
                    "--headless",
                    "--backend",
                    "fake",
                    "--home",
                    str(home),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        )
        return wid

    try:
        wait_for(lambda: httpx.get(base + "/healthz", timeout=1).status_code == 200, what="control plane")
        flow(Ui(base), enroll_and_start, photos, sorted_dir)
        assert not (tmp_path / "cp" / "thumbs").exists() or all(
            max(__import__("PIL.Image", fromlist=["Image"]).open(p).size) <= 512
            for p in (tmp_path / "cp" / "thumbs").rglob("*.webp")
        )  # the CP's default policy is `thumbs` (02 §1.4)
    finally:
        for p in reversed(procs):
            p.terminate()
        for p in procs:
            try:
                p.communicate(timeout=20)
            except subprocess.TimeoutExpired:
                p.kill()


def _compose(*args, env=None, check=True):
    r = subprocess.run(["docker", "compose", *args], cwd=ROOT, capture_output=True, text=True, timeout=1800, env=env)
    if check:
        assert r.returncode == 0, r.stdout + r.stderr
    return r


@pytest.mark.nightly
@pytest.mark.integration
@pytest.mark.tc("MU-DEPLOY-001")
def test_deploy_001_compose(tmp_path):
    """Two compose projects in different networks; the worker reaches the CP only through the published port."""
    photos, sorted_dir = tmp_path / "pics", tmp_path / "sorted"
    photos.mkdir()
    sorted_dir.mkdir()
    for i in range(3):
        shutil.copyfile(FX / f"IMG_{i + 1:04d}.jpg", photos / f"{i}.jpg")
    envf = ROOT / "deploy/compose/controlplane.env"
    envf.write_text(
        f"OPAGENT_PUBLIC_URL=http://host.docker.internal:47800\nOPAGENT_JWT_SECRET={'y' * 48}\n"
        f"OPAGENT_OWNER_EMAIL=owner@example.invalid\nOPAGENT_OWNER_PASSWORD={PW}\n"
    )
    env = {
        **os.environ,
        "OPAGENT_DB_PASSWORD": "pw-" + os.urandom(6).hex(),
        "OPAGENT_CP_BIND": "0.0.0.0",
        "OPAGENT_PHOTOS": str(photos),
        "OPAGENT_SORTED": str(sorted_dir),
        "OPAGENT_WORKER_BACKEND": "fake",
    }
    cp = ["-p", "opa-cp", "-f", "deploy/compose/controlplane.yml"]
    wk = ["-p", "opa-wk", "-f", "deploy/compose/worker.yml"]
    try:
        _compose(*cp, "up", "-d", "--build", "--wait", env=env)
        cp_port = _compose(*cp, "port", "controlplane", "47800", env=env).stdout.strip().rsplit(":", 1)[1]
        base = f"http://127.0.0.1:{cp_port}"
        server = f"http://host.docker.internal:{cp_port}"
        wrun = [*wk, "run", "--rm", "--add-host", "host.docker.internal:host-gateway", "worker"]

        def enroll_and_start(code):
            out = _compose(*wrun, "worker", "enroll", "--server", server, "--code", code, "--home", "/data", env=env)
            wid = json.loads(out.stdout.strip().splitlines()[-1])["worker_id"]
            _compose(*wrun, "worker", "roots", "add", "photos", "/photos", "--mode", "ro", "--home", "/data", env=env)
            _compose(*wrun, "worker", "roots", "add", "sorted", "/sorted", "--mode", "rw", "--home", "/data", env=env)
            _compose(*wk, "up", "-d", env=env)
            return wid

        flow(Ui(base), enroll_and_start, photos, sorted_dir)
        ports = _compose(*wk, "ps", "--format", "json", env=env).stdout
        assert all(not (json.loads(line).get("Publishers") or []) for line in ports.splitlines() if line.strip())
    finally:
        _compose(*wk, "down", "-v", env=env, check=False)
        _compose(*cp, "down", "-v", env=env, check=False)
        envf.unlink(missing_ok=True)
