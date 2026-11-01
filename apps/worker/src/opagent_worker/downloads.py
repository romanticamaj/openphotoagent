from dataclasses import dataclass
from typing import Any


@dataclass
class DlFile:
    repo: str
    revision: str
    path: str
    size: int
    sha256: str


class CancelToken:
    requested = False

    def pause(self) -> None: ...

    def cancel(self, keep_part: bool = False) -> None: ...


class DownloadError(Exception):
    code = ""
    details: dict[str, Any] = {}


class Downloader:
    def __init__(self, root: Any, sources: list[str], reserve_floor: int = 0, **kw: Any) -> None:
        self.on_progress = kw.get("on_progress")
        self.segments = 0

    async def download(self, op_id: str, files: list[DlFile], token: CancelToken) -> Any:
        return None

    def installed_state(self, files: list[DlFile]) -> str:
        return ""


class DownloadQueue:
    def __init__(self, d: Downloader) -> None:
        self.log: list[Any] = []
        self.progress: dict[str, int] = {}


def queue_precheck(sizes: list[int], free_bytes: int) -> Any:
    return {}
