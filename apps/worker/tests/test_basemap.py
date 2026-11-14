"""Offline street map (10 §2.5, ADR-0037): the worker fetches the pinned pmtiles tool, checks it, extracts the region
from the latest Protomaps build and installs the file only when it is a PMTiles v3 archive. No network, no tool runs
here: fetching and running are injected."""

import hashlib
import io
import zipfile
from dataclasses import replace

import pytest


def _zip(member: str, data: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(member, data)
    return buf.getvalue()


def _io(tmp_path, out_bytes=b"PMTiles\x03" + b"\x00" * 200, builds=None):
    from opagent_worker import basemap as B

    pkg = _zip(B.exe_name(), b"fake-pmtiles")
    pin = B.ToolPin(url="https://example.invalid/pmtiles.zip", sha256=hashlib.sha256(pkg).hexdigest(), size=len(pkg))
    calls, progress = [], []

    def fetch(url, dest):
        dest.write_bytes(pkg)

    def get_json(url):
        return (
            builds
            if builds is not None
            else [
                {"key": "20261109.pmtiles", "version": "4.15.2"},
                {"key": "20261110.pmtiles", "version": "4.15.2"},
                {"key": "20270101.pmtiles", "version": "5.0.0"},  # a newer schema than the pinned style reads
            ]
        )

    def run(args, on_line):
        calls.append(args)
        from pathlib import Path

        Path(args[3]).write_bytes(out_bytes)
        on_line("fetching tiles 50%")
        return 0

    return B, pin, fetch, get_json, run, calls, progress


@pytest.mark.unit
@pytest.mark.tc("MAP-016")
def test_map_016_the_worker_extracts_taiwan_with_the_pinned_tool(tmp_path):
    B, pin, fetch, get_json, run, calls, progress = _io(tmp_path)
    got = B.install(tmp_path, pin=pin, fetch=fetch, get_json=get_json, run=run, on_progress=progress.append)
    assert got.path == tmp_path / "maps" / "taiwan.pmtiles" and got.path.read_bytes().startswith(b"PMTiles\x03")
    assert got.build == "20261110.pmtiles"  # the newest build of the schema the style reads
    args = calls[0]
    assert args[1] == "extract" and args[2] == "https://build.protomaps.com/20261110.pmtiles"
    assert f"--bbox={B.BBOX}" in args and f"--maxzoom={B.MAXZOOM}" in args
    assert progress and progress[-1]["stage"] == "done"
    assert not list((tmp_path / "maps").glob("*.part"))  # nothing half-written is left behind


@pytest.mark.unit
@pytest.mark.tc("MAP-016")
def test_map_016_a_tool_that_fails_its_check_never_runs(tmp_path):
    B, pin, fetch, get_json, run, calls, _ = _io(tmp_path)
    with pytest.raises(B.BasemapError) as e:
        B.install(tmp_path, pin=replace(pin, sha256="0" * 64), fetch=fetch, get_json=get_json, run=run)
    assert e.value.code == "DL_CHECKSUM_MISMATCH" and calls == []
    assert not (tmp_path / "tools").exists() or not list((tmp_path / "tools").rglob(B.exe_name()))


@pytest.mark.unit
@pytest.mark.tc("MAP-016")
def test_map_016_an_extract_that_is_not_pmtiles_is_not_installed(tmp_path):
    B, pin, fetch, get_json, run, _, _ = _io(tmp_path, out_bytes=b"<html>error</html>")
    with pytest.raises(B.BasemapError) as e:
        B.install(tmp_path, pin=pin, fetch=fetch, get_json=get_json, run=run)
    assert e.value.code == "DL_MIRROR_FAILED" and not (tmp_path / "maps" / "taiwan.pmtiles").exists()
    B, pin, fetch, get_json, run, _, _ = _io(tmp_path, builds=[{"key": "x.pmtiles", "version": "3.0.0"}])
    with pytest.raises(B.BasemapError) as e:
        B.install(tmp_path, pin=pin, fetch=fetch, get_json=get_json, run=run)
    assert e.value.code == "DL_NOT_FOUND"


@pytest.mark.unit
@pytest.mark.tc("MAP-016")
def test_map_016_the_task_reports_and_a_remote_worker_refuses(tmp_path, sandbox_tree):
    from types import SimpleNamespace

    from opagent_worker.agent import WorkerAgent

    class Client:
        posted = []

        def post(self, url, json=None):
            self.posted.append((url, json))
            return SimpleNamespace(status_code=200, json=lambda: {}, raise_for_status=lambda: None)

    B, pin, fetch, get_json, run, _, _ = _io(tmp_path)
    a = WorkerAgent(Client(), roots=sandbox_tree.roots, home=tmp_path / "worker", worker_id="w1")
    a.basemap_io = {"pin": pin, "fetch": fetch, "get_json": get_json, "run": run}
    res = a._do_basemap({}, "t1")
    assert res["status"] == "succeeded" and res["build"] == "20261110.pmtiles"
    assert (tmp_path / "maps" / "taiwan.pmtiles").exists()  # next to the worker folder: the data folder
    a.remote = True
    assert a._do_basemap({}, "t2")["status"] == "failed"
