import os
import tempfile
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from opagent_fs.safepath import PathViolation, RootConfig, Sandbox, is_within

SEGMENTS = st.one_of(
    st.sampled_from(
        [
            "..",
            ".",
            "",
            "\\",
            "\x00",
            ":",
            "CON",
            "nul.txt",
            "a",
            "b.jpg",
            "%2e%2e",
            "..a",
            "a..",
            " ",
            "C:",
            "\\\\?\\",
            "~",
            "$DATA",
        ]
    ),
    st.text(max_size=12),
    st.text(alphabet="x", min_size=250, max_size=300),
)
REL = st.lists(SEGMENTS, max_size=6).map("/".join) | st.text(max_size=40)


@pytest.fixture(scope="module")
def tree():
    base = Path(tempfile.mkdtemp(prefix="fs024-"))
    (base / "root").mkdir()
    (base / "outside").mkdir()
    return base


@pytest.mark.property
@pytest.mark.security
@pytest.mark.tc("FS-024")
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(rel=REL)
def test_fs_024_never_returns_path_outside_root(tree, rel):
    sb = Sandbox([RootConfig("r", str(tree / "root"), "rw")])
    root = (tree / "root").resolve()
    try:
        p = sb.resolve("r", rel)
    except PathViolation:
        return
    assert is_within(root, p.real)
    assert ".." not in Path(p.rel).parts


@st.composite
def random_tree(draw):
    """(directories, symlinks) to build under root; symlink targets are inside or outside the root."""
    dirs = draw(st.lists(st.lists(st.sampled_from(["a", "b", "c"]), min_size=1, max_size=5).map("/".join), max_size=6))
    links = draw(
        st.lists(
            st.tuples(
                st.lists(st.sampled_from(["a", "b", "c", "l"]), min_size=1, max_size=5).map("/".join),
                st.sampled_from(["in", "out", "out_file", "root"]),
            ),
            max_size=4,
        )
    )
    probes = draw(
        st.lists(
            st.lists(st.sampled_from(["a", "b", "c", "l", "secret.txt"]), min_size=1, max_size=6).map("/".join),
            min_size=1,
            max_size=8,
        )
    )
    return dirs, links, probes


@pytest.mark.property
@pytest.mark.integration
@pytest.mark.security
@pytest.mark.tc("FS-025")
@settings(max_examples=100, deadline=None)
@given(spec=random_tree())
def test_fs_025_real_tree_with_random_symlinks(spec):
    dirs, links, probes = spec
    base = Path(tempfile.mkdtemp(prefix="fs025-"))
    root, outside = base / "root", base / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("s")
    for d in dirs:
        (root / d).mkdir(parents=True, exist_ok=True)
    targets = {"in": root / "a", "out": outside, "out_file": outside / "secret.txt", "root": root}
    for path, kind in links:
        p = root / path
        if p.parent.is_dir() and not os.path.lexists(p) and not p.parent.is_symlink():
            os.symlink(targets[kind], p)
    sb = Sandbox([RootConfig("r", str(root), "ro")])
    real_root = Path(os.path.realpath(root))
    for rel in probes:
        try:
            p = sb.resolve("r", rel)
        except PathViolation:
            continue
        assert is_within(real_root, Path(os.path.realpath(p.real)))
        if p.real.exists() and p.real.is_file():
            with sb.open_read(p) as fh:
                assert fh.read() != b"s" or is_within(real_root, Path(os.path.realpath(p.real)))
