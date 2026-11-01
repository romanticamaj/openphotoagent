from pathlib import Path
from typing import Any

BUILTIN_DIR = Path(__file__).resolve().parents[4] / "registry"


class RegistryLoadError(ValueError):
    pass


class _R:
    models: dict[str, Any] = {}
    runtimes: dict[str, Any] = {}
    gpu_classes: list[Any] = []
    errors: list[Any] = []


def load_registry(root: Path | None = None, user_dirs: list[Path] | None = None) -> Any:
    return _R()
