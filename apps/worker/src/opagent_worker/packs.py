from typing import Any


class PackError(Exception):
    code = ""
    params: dict[str, Any] = {}


class PackInstaller:
    def __init__(self, root: Any, public_key: str, index_url: str, **kw: Any) -> None:
        pass

    def install(self, pid: str) -> Any:
        return None


def smoke_fallback(order: list[str], smoke: Any) -> Any:
    return None
