"""RT-PACK-013/014 (09b §7.1): multi-part packs and reproducible pack builds."""

import io
import tarfile
import urllib.request

import pytest
from opagent_testing.packserver import PackServer, TestKey
from opagent_worker.packs import PackError, PackInstaller

ENTRIES = [{"name": f"lib/blob{i}.so", "data": bytes([i % 251]) * 40_000} for i in range(6)] + [
    {"name": "bin/vllm-serve", "data": b"#!/bin/sh\necho vllm\n"}
]


@pytest.fixture
def srv(tmp_path):
    s = PackServer(tmp_path / "srv", TestKey.new()).start()
    yield s
    s.stop()


def make(srv, tmp_path, fetch=None):
    return PackInstaller(
        tmp_path / "packs",
        public_key=srv.key.public_minisign(),
        index_url=srv.url + "/packs-index.json",
        lock={"vllm-cu128": {"pack_api": [1]}},
        self_test=lambda d: (d / "bin/vllm-serve").exists(),
        fetch=fetch,
    )


@pytest.mark.integration
@pytest.mark.tc("RT-PACK-013")
def test_rt_pack_013_parts_resume_and_verify(srv, tmp_path):
    srv.add("vllm-cu128", "0.11.0-1", ENTRIES, parts=3)
    srv.publish()
    seen = []
    failed = {"once": False}

    def fetch(url):
        seen.append(url.rsplit("/", 1)[-1])
        if url.endswith(".part02") and not failed["once"]:
            failed["once"] = True
            raise OSError("connection reset")
        with urllib.request.urlopen(url, timeout=10) as r:
            return r.read()

    i = make(srv, tmp_path, fetch)
    with pytest.raises(OSError):
        i.install("vllm-cu128")
    r = i.install("vllm-cu128")  # resumes: part01 is not fetched again
    assert r.status == "installed"
    parts = [x for x in seen if ".part" in x]
    assert parts == [
        "vllm-cu128-0.11.0-1.tar.zst.part01",
        "vllm-cu128-0.11.0-1.tar.zst.part02",
        "vllm-cu128-0.11.0-1.tar.zst.part02",
        "vllm-cu128-0.11.0-1.tar.zst.part03",
    ]
    assert (tmp_path / "packs/vllm-cu128/0.11.0-1/lib/blob5.so").read_bytes() == bytes([5]) * 40_000
    assert not any((tmp_path / "packs/.staging").iterdir())


@pytest.mark.integration
@pytest.mark.security
@pytest.mark.tc("RT-PACK-013")
def test_rt_pack_013_a_corrupt_part_rejects_the_whole_pack(srv, tmp_path):
    srv.add("vllm-cu128", "0.11.0-1", ENTRIES, parts=3)
    srv.publish()
    p2 = tmp_path / "srv/vllm-cu128-0.11.0-1.tar.zst.part02"
    b = bytearray(p2.read_bytes())
    b[10] ^= 0xFF
    p2.write_bytes(bytes(b))
    i = make(srv, tmp_path)
    with pytest.raises(PackError) as e:
        i.install("vllm-cu128")
    assert e.value.code == "PACK_CHECKSUM_MISMATCH"
    assert not (tmp_path / "packs/vllm-cu128").exists() and not any((tmp_path / "packs/.staging").iterdir())


ROOT = __import__("pathlib").Path(__file__).resolve().parents[4]


def _build_pack_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("build_pack", ROOT / "tools/build_pack.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.unit
@pytest.mark.tc("RT-PACK-014")
def test_rt_pack_014_ci_builds_the_new_packs():
    import yaml

    wf = yaml.safe_load((ROOT / ".github/workflows/packs.yml").read_text(encoding="utf-8"))
    ids = {row["pack"] for row in wf["jobs"]["build"]["strategy"]["matrix"]["include"]}
    assert {"mlx", "vllm-cu128", "torch-cu128", "torch-mps", "torch-cpu"} <= ids
    runs = " ".join(str(s.get("run", "")) for s in wf["jobs"]["build"]["steps"])
    assert "tools/build_pack.py" in runs and "v*" not in str(wf.get("on"))  # pack tags never look like app releases


@pytest.mark.unit
@pytest.mark.tc("RT-PACK-014")
def test_rt_pack_014_reproducible_build(tmp_path):
    import os

    bp = _build_pack_module()
    BuildError, build_pack = bp.BuildError, bp.build_pack
    src = tmp_path / "src"
    (src / "bin").mkdir(parents=True)
    (src / "bin/run").write_bytes(b"#!/bin/sh\n")
    os.chmod(src / "bin/run", 0o755)
    (src / "LICENSE").write_text("MIT", encoding="utf-8")
    a = build_pack(src, tmp_path / "a.tar.zst")
    os.utime(src / "LICENSE", (1, 1))  # timestamps and order must not matter
    b = build_pack(src, tmp_path / "b.tar.zst")
    assert (tmp_path / "a.tar.zst").read_bytes() == (tmp_path / "b.tar.zst").read_bytes()
    assert a["sha256"] == b["sha256"] and [m["path"] for m in a["files_manifest"]] == ["LICENSE", "bin/run"]
    import zstandard

    raw = zstandard.ZstdDecompressor().decompress((tmp_path / "a.tar.zst").read_bytes(), max_output_size=10**7)
    with tarfile.open(fileobj=io.BytesIO(raw)) as t:
        assert {m.uid for m in t.getmembers()} == {0} and {m.mtime for m in t.getmembers()} == {0}
        assert t.getmember("bin/run").mode & 0o111
    os.symlink("/etc/passwd", src / "evil")
    with pytest.raises(BuildError):
        build_pack(src, tmp_path / "c.tar.zst")
