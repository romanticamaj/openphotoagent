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
import psycopg
import pytest

TOKEN = "s" * 64


def _launch(dsn, home: Path, port: int, extra_env=None, pre=(), extra_args=()):
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
        *extra_args,
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
                return json.loads(f.read_text(encoding="utf-8"))
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
        for line in Path(name).read_text(encoding="utf-8").splitlines()[1:]:
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
        "".join(f.read_text(errors="ignore", encoding="utf-8") for f in (home / "logs").glob("*.log*"))
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
        assert json.loads((home / "run" / "api.json").read_text(encoding="utf-8"))["pid"] == p.pid  # unchanged
    finally:
        blocker.close()


@pytest.mark.integration
@pytest.mark.tc("DESK-027")
def test_desk_027_user_python_environment_is_ignored(embedded_pg, tmp_path, stop):
    evil = tmp_path / "evil"
    evil.mkdir()
    marker = tmp_path / "pwned"
    (evil / "json.py").write_text(f"open({str(marker)!r}, 'w').write('x')\nraise SystemExit(9)\n", encoding="utf-8")
    (evil / "sitecustomize.py").write_text(f"open({str(marker)!r}, 'w').write('x')\n", encoding="utf-8")
    home = tmp_path / "data"
    p = _launch(
        embedded_pg.dsn, home, _free_port(), extra_env={"PYTHONPATH": str(evil), "PYTHONSTARTUP": str(evil / "json.py")}
    )
    stop(p)
    info = _wait_run(home, p)
    assert httpx.get(f"http://127.0.0.1:{info['port']}/healthz").status_code == 200
    assert not marker.exists()


# API start-up against the app database (08 §2.3): Alembic with a backup first; a newer database refuses to start
@pytest.mark.integration
@pytest.mark.tc("PG-004")
def test_pg_004_api_start_backs_up_before_pending_migration(embedded_pg, tmp_path, stop):
    # the test template was built by create_all, never stamped: from Alembic's view the baseline is pending
    home = tmp_path / "home"
    p = _launch(embedded_pg.dsn, home, _free_port())
    stop(p)
    _wait_run(home, p)
    dumps = sorted((home / "backups").glob("pre-migration-*.dump"))
    assert len(dumps) == 1 and dumps[0].stat().st_size > 0
    with psycopg.connect(embedded_pg.dsn) as c:
        assert c.execute("SELECT version_num FROM alembic_version").fetchone() is not None


@pytest.mark.integration
@pytest.mark.tc("PG-008")
def test_pg_008_api_refuses_a_newer_database(embedded_pg, tmp_path, stop):
    with psycopg.connect(embedded_pg.dsn, autocommit=True) as c:
        c.execute("CREATE TABLE IF NOT EXISTS alembic_version (version_num varchar(32) PRIMARY KEY)")
        c.execute("INSERT INTO alembic_version VALUES ('ffff_written_by_a_newer_app')")
    home = tmp_path / "home"
    p = _launch(embedded_pg.dsn, home, _free_port())
    stop(p)
    out, _ = p.communicate(timeout=60)
    assert p.returncode == 4, out.decode()[-2000:]
    text = out.decode()
    assert "DB_SCHEMA_TOO_NEW" in text and "restore" in text
    assert not (home / "run" / "api.json").exists()  # never served
    with psycopg.connect(embedded_pg.dsn) as c:  # and nothing was touched
        assert c.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "ffff_written_by_a_newer_app"


@pytest.mark.integration
@pytest.mark.tc("DESK-035")
def test_desk_035_port_listens_before_run_file_appears(embedded_pg, tmp_path, stop):
    """Regression: the shell reads run/api.json and connects at once; the port must already be accepting."""
    for i in range(5):
        home = tmp_path / f"h{i}"
        p = _launch(embedded_pg.dsn, home, _free_port())
        stop(p)
        f = home / "run" / "api.json"
        while not f.exists():  # tight poll: catch the earliest moment the file is visible
            assert p.poll() is None, p.stdout.read().decode()[-2000:]
        info = json.loads(f.read_text(encoding="utf-8"))
        with socket.create_connection(("127.0.0.1", info["port"]), timeout=2):
            pass
        p.terminate()
        p.wait(10)


@pytest.mark.integration
@pytest.mark.tc("DESK-015")
def test_desk_015_lan_mode_needs_a_password_then_binds_all_interfaces(embedded_pg, tmp_path, stop):
    home = tmp_path / "lan"
    port = _free_port()
    p = _launch(embedded_pg.dsn, home, port, extra_args=["--lan"])
    stop(p)
    out, _ = p.communicate(timeout=60)
    assert p.returncode == 5 and b"LAN_PASSWORD_REQUIRED" in out  # never open to the network without login
    r = subprocess.run(
        [sys.executable, "-I", "-m", "opagent_cli", "lan-password", "--home", str(home)],
        input=b"correct horse\n",
        capture_output=True,
        timeout=60,
    )
    assert r.returncode == 0, r.stderr
    cfg = json.loads((home / "config" / "lan.json").read_text(encoding="utf-8"))
    assert cfg["password_hash"].startswith("$argon2id$") and "correct horse" not in json.dumps(cfg)
    assert stat.S_IMODE((home / "config" / "lan.json").stat().st_mode) == 0o600
    p = _launch(embedded_pg.dsn, home, port, extra_args=["--lan"])
    stop(p)
    _wait_run(home, p)
    assert _listening(port) == ["00000000"]
    assert httpx.get(f"http://127.0.0.1:{port}/api/v1/capabilities").status_code == 401


@pytest.mark.integration
@pytest.mark.tc("DESK-006")
def test_desk_006_api_exits_when_the_shell_pipe_closes(embedded_pg, tmp_path, stop):
    """macOS has no PDEATHSIG: the sidecar watches the shell's stdin pipe and leaves on EOF (08 §1.1)."""
    home = tmp_path / "eof"
    p = _launch(embedded_pg.dsn, home, _free_port())
    stop(p)
    _wait_run(home, p)
    p.stdin.close()  # what the OS does when the shell is killed
    p.wait(timeout=5)
    assert p.returncode is not None


def _worker(api_port: int, home: Path, roots: str, token: str | None):
    env = {k: v for k, v in os.environ.items() if not k.startswith("OPAGENT_")}
    argv = [
        sys.executable,
        "-I",
        "-m",
        "opagent_cli",
        "worker",
        "--api",
        f"http://127.0.0.1:{api_port}",
        "--roots",
        roots,
        "--home",
        str(home),
        "--worker-id",
        "desk-w",
    ]
    if token is not None:
        argv.append("--token-stdin")
    p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
    if token is not None:
        p.stdin.write((token + "\n").encode())
        p.stdin.flush()
    return p


def _registered(dsn):
    with psycopg.connect(dsn) as c:
        return c.execute("SELECT count(*) FROM worker WHERE id = 'desk-w'").fetchone()[0] == 1


@pytest.mark.integration
@pytest.mark.tc("DESK-011", "DESK-006")
def test_desk_011_worker_authenticates_with_the_launch_token_from_stdin(embedded_pg, tmp_path, stop):
    home = tmp_path / "w"
    (tmp_path / "photos").mkdir()
    api = _launch(embedded_pg.dsn, home, _free_port())
    stop(api)
    port = _wait_run(home, api)["port"]
    stranger = _worker(port, home / "worker-x", f"photos={tmp_path / 'photos'}:ro", None)
    stop(stranger)
    time.sleep(2)  # noqa: TID251 - give an unauthenticated worker time to (fail to) register
    assert not _registered(embedded_pg.dsn)
    stranger.terminate()
    w = _worker(port, home / "worker", f"photos={tmp_path / 'photos'}:ro", TOKEN)
    stop(w)
    deadline = time.monotonic() + 20  # noqa: TID251 - waiting on real processes
    while not _registered(embedded_pg.dsn):
        out = w.stdout.read().decode()[-2000:] if w.poll() is not None else "never registered"
        assert w.poll() is None and time.monotonic() < deadline, out  # noqa: TID251
        time.sleep(0.2)  # noqa: TID251
    assert TOKEN not in Path(f"/proc/{w.pid}/cmdline").read_text(encoding="utf-8") and TOKEN not in Path(
        f"/proc/{w.pid}/environ"
    ).read_text(encoding="utf-8")
    w.stdin.close()  # the shell is gone
    w.wait(timeout=10)
