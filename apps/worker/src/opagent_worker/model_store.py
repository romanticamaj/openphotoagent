from pathlib import Path
from typing import Any


class MoveError(Exception):
    pass


class ModelStore:
    def __init__(self, cfg: Path) -> None:
        self.models_root = Path()

    def move(self, dst: Path, token: Any, on_progress: Any = None, cross_fs: bool = False) -> Any:
        return None

    def verify(self, root: Path) -> list[str]:
        return ["x"]

    def recover(self) -> Any:
        return None
