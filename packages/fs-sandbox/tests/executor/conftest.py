from pathlib import Path

import blake3
import pytest

from opagent_fs.executor import Action, Executor
from opagent_fs.fsops import RealFs, RecordingFs
from opagent_fs.journal import Journal
from opagent_fs.safepath import RootConfig, Sandbox


def b3(p: Path) -> str:
    return blake3.blake3(p.read_bytes()).hexdigest()


class Env:
    def __init__(self, tree, tmp_path):
        self.tree = tree
        self.sb = Sandbox([RootConfig(**r) for r in tree.roots])
        self.log: list[str] = []
        self.fs = RecordingFs(RealFs(), self.log)
        self.journal = Journal(tmp_path / "journal.db", on_event=self.log.append)
        self.ex = Executor(self.sb, self.fs, self.journal)

    def photo(self, rel: str, data: bytes) -> Action:
        p = self.tree.photos / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return Action(action_id=f"a-{rel}", op="copy", src_alias="photos", src_rel=rel, dst_alias="sorted",
                      dst_rel=f"2025/2025-07/{Path(rel).name}", src_hash=b3(p))


@pytest.fixture
def env(sandbox_tree, tmp_path):
    return Env(sandbox_tree, tmp_path)


@pytest.fixture
def helpers():
    class H:
        pass

    h = H()
    h.b3 = b3
    h.snapshot = snapshot
    return h


def snapshot(root: Path) -> dict[str, tuple[str, int] | str]:
    out: dict[str, tuple[str, int] | str] = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        out[rel] = "dir" if p.is_dir() else (b3(p), p.stat().st_mtime_ns)
    return out
