"""Process logs (08 §1.5): JSON lines, 10 MB × 5 rotation, no absolute paths (alias + relative, 02 §7.1) and never
the launch token."""

from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


class _Redact(logging.Filter):
    def __init__(self, token: str | None, roots: dict[str, str]) -> None:
        super().__init__()
        self.token = token
        # longest path first so nested roots map to the most specific alias
        self.roots = sorted(((p.rstrip("/\\"), a) for a, p in roots.items()), key=lambda x: -len(x[0]))

    def scrub(self, s: str) -> str:
        if self.token:
            s = s.replace(self.token, "***")
        for path, alias in self.roots:
            s = s.replace(path + "/", f"{alias}:").replace(path + "\\", f"{alias}:").replace(path, f"{alias}:")
        return s

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self.scrub(record.getMessage())
        record.args = None
        return True


class _Json(logging.Formatter):
    def __init__(self, proc: str) -> None:
        super().__init__()
        self.proc = proc

    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(
            {
                "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
                "level": record.levelname,
                "proc": self.proc,
                "logger": record.name,
                "msg": record.getMessage(),
            },
            ensure_ascii=False,
        )


def setup_logging(
    log_dir: Path, proc: str, token: str | None = None, roots: dict[str, str] | None = None
) -> RotatingFileHandler:
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    h = RotatingFileHandler(log_dir / f"{proc}.log", maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8")
    h.setFormatter(_Json(proc))
    h.addFilter(_Redact(token, roots or {}))
    root = logging.getLogger()
    root.addHandler(h)
    if root.level > logging.INFO or root.level == logging.NOTSET:
        root.setLevel(logging.INFO)
    return h
