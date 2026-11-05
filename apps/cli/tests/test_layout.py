"""Desktop data-folder layout version (VERSIONING §4 `layout_version`; 09b §9.4 MIG-002)."""

import json
import os
import stat

import pytest
from opagent_cli.layout import LAYOUT_VERSION, LayoutTooNew, ensure_layout


def mode(p):
    return stat.S_IMODE(p.stat().st_mode)


def v0_folder(home):
    """A data folder as v0.x left it: no layout.json, permissive modes from an older umask."""
    (home / "run").mkdir(parents=True)
    (home / "run" / "api.json").write_text(json.dumps({"port": 47800}), encoding="utf-8")
    os.chmod(home / "run", 0o755)
    os.chmod(home / "run" / "api.json", 0o644)
    (home / "secrets").mkdir()
    (home / "secrets" / "mcp-pat-default").write_text("opagent_pat_x", encoding="utf-8")
    os.chmod(home / "secrets", 0o755)
    os.chmod(home / "secrets" / "mcp-pat-default", 0o644)
    (home / "worker").mkdir()
    (home / "worker" / "journal.db").write_bytes(b"keep me")


@pytest.mark.tc("MIG-002")
def test_mig_002_layout_migration(tmp_path):
    home = tmp_path / "data"
    v0_folder(home)
    done = ensure_layout(home)
    assert done == {"from": 0, "to": LAYOUT_VERSION, "backup": done["backup"]}
    assert json.loads((home / "layout.json").read_text(encoding="utf-8"))["layout_version"] == LAYOUT_VERSION == 1
    assert mode(home / "run") == 0o700 and mode(home / "run" / "api.json") == 0o600
    assert mode(home / "secrets") == 0o700 and mode(home / "secrets" / "mcp-pat-default") == 0o600
    assert (home / "worker" / "journal.db").read_bytes() == b"keep me"  # nothing moved or lost
    backup = home / done["backup"]
    assert (
        backup.exists()
        and "run/api.json" in json.loads((backup / "manifest.json").read_text(encoding="utf-8"))["files"]
    )
    assert ensure_layout(home) is None  # idempotent: already current
    (home / "layout.json").write_text(json.dumps({"layout_version": LAYOUT_VERSION + 1}), encoding="utf-8")
    with pytest.raises(LayoutTooNew) as e:
        ensure_layout(home)
    assert e.value.code == "DATA_LAYOUT_TOO_NEW" and e.value.found == LAYOUT_VERSION + 1
    assert {f["type"] for f in e.value.fixes} == {"open_settings", "open_docs"}  # a way out, not a bare refusal


@pytest.mark.tc("MIG-002")
def test_mig_002_new_folder(tmp_path):
    home = tmp_path / "fresh"
    assert ensure_layout(home) == {"from": None, "to": LAYOUT_VERSION, "backup": None}
    assert json.loads((home / "layout.json").read_text(encoding="utf-8"))["layout_version"] == LAYOUT_VERSION


@pytest.mark.unit
@pytest.mark.tc("DESK-052")
def test_desk_052_spawned_children_do_not_rerun_the_cli(monkeypatch):
    """回歸: `multiprocessing` spawn re-imports the parent's main module as ``__mp_main__``. ``__main__.py`` called
    main() unconditionally, so the download child ran the worker CLI again and blocked reading the token from stdin:
    every download in the installed app sat at "in progress" with no bytes moving."""
    import runpy

    import opagent_cli.main as cli

    calls = []
    monkeypatch.setattr(cli, "main", lambda: calls.append(1))
    runpy.run_module("opagent_cli.__main__", run_name="__mp_main__")
    assert calls == []
    runpy.run_module("opagent_cli.__main__", run_name="__main__")
    assert calls == [1]


CHILD_UNDER_WATCH = """
import multiprocessing as mp, sys
from opagent_cli.main import watch_parent_pipe

def hello(c):
    c.send("hello")

if __name__ == "__main__":
    watch_parent_pipe()
    ctx = mp.get_context("spawn")
    r, w = ctx.Pipe(duplex=False)
    ctx.Process(target=hello, args=(w,), daemon=True).start()
    w.close()
    print(r.recv() if r.poll(20) else "CHILD_STUCK", flush=True)
    import time
    time.sleep(60)
"""


@pytest.mark.integration
@pytest.mark.tc("DESK-053")
def test_desk_053_a_sidecar_can_spawn_while_it_watches_the_parent_pipe(tmp_path):
    """回歸: a blocking read of the stdin pipe froze every spawned child inside interpreter start-up on Windows, so the
    download child never ran. The watch must still notice the shell going away (DESK-006)."""
    import subprocess
    import sys

    script = tmp_path / "sidecar.py"
    script.write_text(CHILD_UNDER_WATCH, encoding="utf-8")
    p = subprocess.Popen(
        [sys.executable, str(script)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        assert p.stdout is not None and p.stdin is not None
        assert p.stdout.readline().strip() == "hello"
        p.stdin.close()  # the shell is gone
        assert p.wait(timeout=15) is not None
    finally:
        p.kill()
