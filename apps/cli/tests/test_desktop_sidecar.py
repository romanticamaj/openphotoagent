"""The Python sidecar as the desktop shell runs it (08 §1.3–1.5): token on stdin, loopback only, port handling,
run/api.json, isolation from the user's Python environment."""

import json
import os
import socket
import stat
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

TOKEN = "s" * 64


def _launch(dsn, home: Path, port: int, extra_env=None, pre=()):
    env = {k: v for k, v in os.environ.items() if not k.startswith("OPAGENT_")}
    env.update(extra_env or {})
    argv = [
        sys.executable,
        *pre,
        "-I",
        "-m",
        "opagent_cli",
        "api",
        "--dsn",
        dsn,
        "--port",
        str(port),
        "--token-stdin",
        "--home",
        str(home),
    ]
    p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
    p.stdin.write((TOKEN + "\n").encode())
    p.stdin.flush()
    return p


def _wait_run(home: Path, p, timeout=30.0):
    f = home / "run" / "api.json"
    t0 = time.monotonic()  # noqa: TID251 - waiting on a real subprocess
    while time.monotonic() - t0 < timeout:  # noqa: TID251
        if f.exists():
            try:
                return json.loads(f.read_text())
            except json.JSONDecodeError:
                pass
        if p.poll() is not None:
            raise AssertionError(p.stdout.read().decode()[-2000:])
        time.sleep(0.1)  # noqa: TID251
    raise AssertionError("run/api.json never appeared")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _listening(port: int) -> list[str]:
    out = []
    for name in ("/proc/net/tcp", "/proc/net/tcp6"):
        if not Path(name).exists():  # kernels without IPv6
            continue
        for line in Path(name).read_text().splitlines()[1:]:
            local, state = line.split()[1], line.split()[3]
            addr, p = local.split(":")
            if state == "0A" and int(p, 16) == port:
                out.append(addr)
    return out


@pytest.fixture
def stop():
    procs = []
    yield procs.append
    for p in procs:
        p.terminate()
        p.wait(10)


@pytest.mark.integration
@pytest.mark.tc("DESK-012", "DESK-014")
def test_desk_012_014_token_on_stdin_and_loopback_only(embedded_pg, tmp_path, stop):
    home = tmp_path / "data"
    p = _launch(embedded_pg.dsn, home, _free_port())
    stop(p)
    info = _wait_run(home, p)
    port = info["port"]
    assert stat.S_IMODE((home / "run" / "api.json").stat().st_mode) == 0o600
    # the token reached the API but is in neither the environment nor the command line
    assert TOKEN not in Path(f"/proc/{p.pid}/environ").read_bytes().decode(errors="ignore")
    assert TOKEN not in Path(f"/proc/{p.pid}/cmdline").read_bytes().decode(errors="ignore")
    base = f"http://127.0.0.1:{port}"
    assert httpx.get(f"{base}/api/v1/sources").status_code == 401
    assert httpx.get(f"{base}/api/v1/sources", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200
    assert _listening(port) == ["0100007F"]  # 127.0.0.1 only
    logs = (
        "".join(f.read_text(errors="ignore") for f in (home / "logs").glob("*.log*"))
        if (home / "logs").exists()
        else ""
    )
    assert TOKEN not in logs


@pytest.mark.integration
@pytest.mark.tc("DESK-010")
def test_desk_010_port_conflicts(embedded_pg, tmp_path, stop):
    pref = _free_port()
    # (a) another program holds the preferred port → a random port, recorded in run/api.json
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", pref))
    blocker.listen()
    try:
        home = tmp_path / "a"
        p = _launch(embedded_pg.dsn, home, pref)
        stop(p)
        info = _wait_run(home, p)
        assert info["port"] != pref and info["pid"] == p.pid
        assert httpx.get(f"http://127.0.0.1:{info['port']}/healthz").json()["app"] == "openphotoagent"
        # (b) another OpenPhotoAgent on the same data dir holds the port → do not start a second one
        p2 = _launch(embedded_pg.dsn, home, info["port"])
        assert p2.wait(30) == 3, p2.stdout.read().decode()[-2000:]
        assert json.loads((home / "run" / "api.json").read_text())["pid"] == p.pid  # unchanged
    finally:
        blocker.close()


@pytest.mark.integration
@pytest.mark.tc("DESK-027")
def test_desk_027_user_python_environment_is_ignored(embedded_pg, tmp_path, stop):
    evil = tmp_path / "evil"
    evil.mkdir()
    marker = tmp_path / "pwned"
    (evil / "json.py").write_text(f"open({str(marker)!r}, 'w').write('x')\nraise SystemExit(9)\n")
    (evil / "sitecustomize.py").write_text(f"open({str(marker)!r}, 'w').write('x')\n")
    home = tmp_path / "data"
    p = _launch(
        embedded_pg.dsn, home, _free_port(), extra_env={"PYTHONPATH": str(evil), "PYTHONSTARTUP": str(evil / "json.py")}
    )
    stop(p)
    info = _wait_run(home, p)
    assert httpx.get(f"http://127.0.0.1:{info['port']}/healthz").status_code == 200
    assert not marker.exists()
