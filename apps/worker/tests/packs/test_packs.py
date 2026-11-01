import json

import pytest
from opagent_testing.packserver import PackServer, TestKey

from opagent_worker.packs import PackError, PackInstaller, smoke_fallback

ENTRIES = [{"name": "bin/llama-server", "data": b"#!/bin/sh\necho version b7001\n"}, {"name": "LICENSE", "data": b"MIT"}]


@pytest.fixture
def srv(tmp_path):
    s = PackServer(tmp_path / "srv", TestKey.new()).start()
    yield s
    s.stop()


def inst(srv, tmp_path, **kw):
    kw.setdefault("lock", {"llamacpp-cpu": {"pack_api": [1]}})
    kw.setdefault("self_test", lambda d: (d / "bin/llama-server").exists())
    return PackInstaller(tmp_path / "packs", public_key=srv.key.public_minisign(), index_url=srv.url + "/packs-index.json", **kw)


def files_outside(root, allowed):
    return [p for p in root.rglob("*") if p.is_file() and not any(str(p).startswith(str(a)) for a in allowed)]


@pytest.mark.integration
@pytest.mark.tc("PACK-001")
def test_pack_001_install(srv, tmp_path):
    srv.add("llamacpp-cpu", "b7001-1", ENTRIES)
    srv.publish()
    i = inst(srv, tmp_path)
    r = i.install("llamacpp-cpu")
    assert r.status == "installed"
    assert (tmp_path / "packs/llamacpp-cpu/b7001-1/bin/llama-server").read_bytes().startswith(b"#!/bin/sh")
    assert json.loads((tmp_path / "packs/active.json").read_text()) == {"llamacpp-cpu": "b7001-1"}
    assert not any((tmp_path / "packs/.staging").iterdir())


@pytest.mark.security
@pytest.mark.tc("PACK-002")
def test_pack_002_bad_index_signature(srv, tmp_path):
    srv.add("llamacpp-cpu", "b7001-1", ENTRIES)
    srv.publish(tamper=True)
    with pytest.raises(PackError) as e:
        inst(srv, tmp_path).install("llamacpp-cpu")
    assert e.value.code == "PACK_SIGNATURE_INVALID"
    assert not (tmp_path / "packs/.staging").exists() or not any((tmp_path / "packs/.staging").iterdir())


@pytest.mark.security
@pytest.mark.tc("PACK-003")
def test_pack_003_bad_sha(srv, tmp_path):
    srv.add("llamacpp-cpu", "b7001-1", ENTRIES, sha256="0" * 64)
    srv.publish()
    with pytest.raises(PackError) as e:
        inst(srv, tmp_path).install("llamacpp-cpu")
    assert e.value.code == "PACK_CHECKSUM_MISMATCH"
    assert not any((tmp_path / "packs/.staging").iterdir())
    assert not (tmp_path / "packs/llamacpp-cpu").exists()


EVIL = [
    [{"name": "../evil", "data": b"x"}],
    [{"name": "/abs/evil", "data": b"x"}],
    [{"name": "link", "type": "symlink", "target": "/etc/passwd"}],
    [{"name": "hl", "type": "hardlink", "target": "/etc/passwd"}],
    [{"name": "dev", "type": "chr"}],
]


@pytest.mark.security
@pytest.mark.tc("PACK-004")
@pytest.mark.parametrize("entries", EVIL, ids=["dotdot", "abs", "symlink", "hardlink", "device"])
def test_pack_004_tar_traversal(srv, tmp_path, entries):
    srv.add("llamacpp-cpu", "b7001-1", ENTRIES + entries)
    srv.publish()
    with pytest.raises(PackError) as e:
        inst(srv, tmp_path).install("llamacpp-cpu")
    assert e.value.code == "PACK_UNSAFE_ARCHIVE"
    assert files_outside(tmp_path, [tmp_path / "srv", tmp_path / "packs"]) == []
    assert not (tmp_path / "evil").exists() and not (tmp_path / "packs/llamacpp-cpu").exists()


@pytest.mark.security
@pytest.mark.tc("PACK-005")
@pytest.mark.parametrize("kind", ["extra_file", "bomb"])
def test_pack_005_size_and_manifest(srv, tmp_path, kind):
    if kind == "extra_file":
        p = srv.add("llamacpp-cpu", "b7001-1", ENTRIES + [{"name": "extra.so", "data": b"1"}])
        p["files_manifest"] = [m for m in p["files_manifest"] if m["path"] != "extra.so"]
    else:
        p = srv.add("llamacpp-cpu", "b7001-1", ENTRIES + [{"name": "zeros.bin", "data": b"\0" * 2_000_000}])
        p["unpacked_size"] = 1000
    srv.publish()
    with pytest.raises(PackError) as e:
        inst(srv, tmp_path).install("llamacpp-cpu")
    assert e.value.code == "PACK_UNSAFE_ARCHIVE"


@pytest.mark.unit
@pytest.mark.tc("PACK-006")
def test_pack_006_pack_api_incompatible(srv, tmp_path):
    srv.add("llamacpp-cpu", "b8000-1", ENTRIES, pack_api=2)
    srv.publish()
    with pytest.raises(PackError) as e:
        inst(srv, tmp_path).install("llamacpp-cpu")
    assert e.value.code == "PACK_INCOMPATIBLE" and e.value.params["range"] == [1]


@pytest.mark.integration
@pytest.mark.tc("PACK-007")
def test_pack_007_self_test_failure_keeps_previous(srv, tmp_path):
    srv.add("llamacpp-cpu", "b7001-1", ENTRIES)
    srv.publish()
    assert inst(srv, tmp_path).install("llamacpp-cpu").status == "installed"
    srv.packs.clear()
    srv.add("llamacpp-cpu", "b7002-1", ENTRIES)
    srv.publish()
    r = inst(srv, tmp_path, self_test=lambda d: False).install("llamacpp-cpu")
    assert r.status == "broken" and r.code == "RUNTIME_PACK_BROKEN"
    assert json.loads((tmp_path / "packs/active.json").read_text())["llamacpp-cpu"] == "b7001-1"


@pytest.mark.integration
@pytest.mark.tc("PACK-008")
def test_pack_008_lock_change_updates_on_idle_keeps_one_old(srv, tmp_path):
    for v in ("b7001-1", "b7002-1", "b7003-1"):
        srv.packs.clear()
        srv.add("llamacpp-cpu", v, ENTRIES)
        srv.publish()
        i = inst(srv, tmp_path, lock={"llamacpp-cpu": {"pack_api": [1], "version": v}})
        staged = i.sync_lock(idle=False)  # background download, not yet switched
        assert staged == ["llamacpp-cpu"] or v == "b7001-1"
        i.sync_lock(idle=True)
        assert json.loads((tmp_path / "packs/active.json").read_text())["llamacpp-cpu"] == v
    kept = sorted(p.name for p in (tmp_path / "packs/llamacpp-cpu").iterdir())
    assert kept == ["b7002-1", "b7003-1"]  # one previous version kept for rollback


@pytest.mark.integration
@pytest.mark.tc("PACK-009")
def test_pack_009_offline_import(srv, tmp_path):
    p = srv.add("llamacpp-cpu", "b7001-1", ENTRIES)
    srv.publish()
    i = inst(srv, tmp_path)
    archive = srv.root / "llamacpp-cpu-b7001-1.tar.zst"
    r = i.import_file(archive, srv.root / "packs-index.json", srv.root / "packs-index.json.minisig")
    assert r.status == "installed"
    bad = srv.root / "packs-index.json"
    bad.write_bytes(bad.read_bytes().replace(b"b7001-1", b"b7001-2", 1))
    with pytest.raises(PackError) as e:
        i.import_file(archive, bad, srv.root / "packs-index.json.minisig")
    assert e.value.code == "PACK_SIGNATURE_INVALID"
    _ = p


@pytest.mark.integration
@pytest.mark.tc("PACK-010")
def test_pack_010_minor_update_keeps_cache(srv, tmp_path):
    from opagent_core.config_hash import model_fingerprint

    srv.add("llamacpp-cpu", "b7001-1", ENTRIES, numerics_epoch=3)
    srv.publish()
    i = inst(srv, tmp_path)
    i.install("llamacpp-cpu")
    fp1 = i.fingerprint_for("llamacpp-cpu", adapter="llama.cpp", weights="w" * 64)
    srv.packs.clear()
    srv.add("llamacpp-cpu", "b7002-1", ENTRIES, numerics_epoch=3)
    srv.publish()
    i.install("llamacpp-cpu")
    assert i.fingerprint_for("llamacpp-cpu", adapter="llama.cpp", weights="w" * 64) == fp1
    srv.packs.clear()
    srv.add("llamacpp-cpu", "b7003-1", ENTRIES, numerics_epoch=4)
    srv.publish()
    i.install("llamacpp-cpu")
    assert i.fingerprint_for("llamacpp-cpu", adapter="llama.cpp", weights="w" * 64) != fp1
    assert fp1 == model_fingerprint("llama.cpp", "1", "w" * 64, 3)


@pytest.mark.integration
@pytest.mark.tc("PACK-011")
def test_pack_011_smoke_fallback():
    tried = []

    def smoke(pack):
        tried.append(pack)
        return pack == "llamacpp-cpu"  # CUDA and Vulkan smoke tests fail on this machine

    res = smoke_fallback(["llamacpp-cuda13", "llamacpp-vulkan", "llamacpp-cpu"], smoke)
    assert tried == ["llamacpp-cuda13", "llamacpp-vulkan", "llamacpp-cpu"]
    assert res.chosen == "llamacpp-cpu"
    assert [r["pack"] for r in res.failures] == ["llamacpp-cuda13", "llamacpp-vulkan"]
    assert all(r["code"] == "RUNTIME_PACK_BROKEN" for r in res.failures)

