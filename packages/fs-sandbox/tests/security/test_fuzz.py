"""SafePath under adversarial input: zero escapes (09b §9.2 SEC-FUZZ-001; invariant 2).

A seeded generator builds relative paths from segments known to break naive sandboxes — ``..`` in every disguise,
absolute and drive-letter prefixes, backslashes, NUL and control bytes, Unicode look-alikes and normalisation
tricks, Windows device names and alternate data streams, over-long names — and resolves them against a root that
contains symlinks pointing outside, inside, and in loops. PR CI runs 10 000 cases; nightly runs 1 000 000.

For every input the sandbox must either refuse it (``PathViolation``) or return a path that is inside the root
*after* the operating system resolves it, with no symlink anywhere on the way.
"""

import os
import random
import stat

import pytest
from opagent_fs.safepath import PathViolation, RootConfig, Sandbox, is_within

SEGMENTS = [
    "..", ".", "", "...", "a", "b", "dir", "deep", "photo.jpg", "out", "in", "loop", "dirlink",
    "..\\..", "\\", "/", "//", "C:", "C:\\Windows", "\\\\server\\share", "~", "~root", "$HOME",
    "%2e%2e", "%2f", "..%2f", "\u2025", "\uff0e\uff0e", "\u2024\u2024", "．．", "\u202e..", "..\u200b",
    "a\x00b", "\x00", "\x1f", "\n", "CON", "con.txt", "NUL", "aux.jpg", "COM1", "LPT9.png", "file.txt:stream",
    "a.", "a ", " a", "a" * 255, "a" * 256, "é" * 128, "e\u0301", "\u00e9", "dir/..", "./..", "../in",
]
SEPS = ["/", "/", "/", "\\", "//"]


def build_tree(base):
    root, outside = base / "root", base / "outside"
    (root / "dir" / "deep").mkdir(parents=True)
    (root / "in").mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("nope")
    (root / "dir" / "photo.jpg").write_bytes(b"x")
    (root / "dir" / "deep" / "photo.jpg").write_bytes(b"x")
    os.symlink(outside, root / "out")  # escapes
    os.symlink(outside / "secret.txt", root / "dir" / "secret.jpg")
    os.symlink(root / "in", root / "dirlink")  # stays inside, but is still a symlink
    os.symlink(root / "loop", root / "loop")  # a loop
    os.symlink("../../outside", root / "dir" / "rel-out")
    return root, outside


def gen(r):
    n = r.randint(1, 7)
    segs = [r.choice(SEGMENTS + ["out", "dirlink", "rel-out", "secret.jpg"]) for _ in range(n)]
    path = ""
    for s in segs:
        path += s + r.choice(SEPS)
    if r.random() < 0.15:
        path = r.choice(["/", "\\", "C:/", "//", "~/"]) + path
    return path.rstrip("/") if r.random() < 0.5 else path


def no_symlink_on_the_way(root, p):
    cur = root
    for part in p.relative_to(root).parts:
        cur = cur / part
        if os.path.lexists(cur) and stat.S_ISLNK(os.lstat(cur).st_mode):
            return False
    return True


def run(n, tmp_path, seed=20261103):
    root, outside = build_tree(tmp_path)
    sb = Sandbox([RootConfig("photos", str(root), "rw")])
    real_root = os.path.realpath(root)
    r = random.Random(seed)
    accepted = refused = 0
    for _ in range(n):
        rel = gen(r)
        try:
            sp = sb.resolve("photos", rel)
        except PathViolation:
            refused += 1
            continue
        accepted += 1
        real = os.path.realpath(sp.real)
        assert is_within(real_root, real) and real.startswith(real_root), (rel, real)
        assert not real.startswith(os.path.realpath(outside)), rel
        assert no_symlink_on_the_way(root, sp.real), rel
        assert "\x00" not in sp.rel and ".." not in sp.rel.split("/"), rel
    return accepted, refused


@pytest.mark.security
@pytest.mark.tc("SEC-FUZZ-001")
def test_sec_fuzz_001_pr_size(tmp_path):
    accepted, refused = run(10_000, tmp_path)
    assert accepted > 100 and refused > 1000  # the generator reaches both sides of the boundary


@pytest.mark.nightly
@pytest.mark.security
@pytest.mark.tc("SEC-FUZZ-001")
def test_sec_fuzz_001_one_million(tmp_path):
    accepted, refused = run(1_000_000, tmp_path, seed=int(os.environ.get("OPAGENT_FUZZ_SEED", "20261103")))
    assert accepted + refused == 1_000_000
