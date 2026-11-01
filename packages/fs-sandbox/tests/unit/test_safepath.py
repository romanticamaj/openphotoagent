from pathlib import Path

import pytest

from opagent_fs.safepath import PathViolation, RootConfig, Sandbox


@pytest.fixture
def sb(sandbox_tree):
    return Sandbox([RootConfig(**r) for r in sandbox_tree.roots])


@pytest.mark.unit
@pytest.mark.tc("FS-001")
def test_fs_001_normal_relative_path(sb, sandbox_tree):
    p = sb.resolve("photos", "2025/a.jpg")
    assert p.rel == "2025/a.jpg"
    assert p.real == Path(sandbox_tree.photos).resolve() / "2025" / "a.jpg"
    assert p.alias == "photos"


@pytest.mark.unit
@pytest.mark.security
@pytest.mark.tc("FS-002")
@pytest.mark.parametrize("rel", ["/etc/passwd", "C:/x", "/"])
def test_fs_002_absolute_rejected(sb, rel):
    with pytest.raises(PathViolation) as e:
        sb.resolve("photos", rel)
    assert e.value.code == "PATH_VIOLATION"
    assert e.value.reason == "absolute"


@pytest.mark.unit
@pytest.mark.security
@pytest.mark.tc("FS-003")
@pytest.mark.parametrize("rel", ["../x", "a/../../x", "a/./../..", "a/..", ".."])
def test_fs_003_dotdot_component_rejected(sb, rel):
    with pytest.raises(PathViolation) as e:
        sb.resolve("photos", rel)
    assert e.value.reason == "dotdot"


@pytest.mark.unit
@pytest.mark.security
@pytest.mark.tc("FS-017")
def test_fs_017_write_to_ro_root_rejected(sb):
    with pytest.raises(PathViolation) as e:
        sb.open_write("photos", "x.jpg")
    assert e.value.reason == "read_only_root"
    # rw roots are writable
    assert sb.open_write("sorted", "x.jpg").rel == "x.jpg"
