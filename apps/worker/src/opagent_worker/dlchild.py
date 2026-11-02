"""Model downloads in a child process (ADR-0007, DL-019).

A download at 100 MB/s needs the GIL for every socket read, chunk copy and progress update; on a background
*thread* that slows labelling and scanning in the same worker by ~25 %. The child runs the unchanged
``Downloader`` and talks to the worker over two one-way pipes:

* events (child → worker): ``("progress", ev)`` at most every 250 ms (always for waiting states / the last byte),
  then exactly one ``("result", dict)``;
* control (worker → child): ``"pause"`` / ``"cancel"``. EOF on the control pipe means the worker is gone: the child
  stops and keeps its ``.part`` files, so the next worker resumes them (DL-014).

``spawn`` is used on every OS so behaviour is identical (no fork of a threaded process); the entry point is a
module-level function, which works from the relocatable venv (08 D3).
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from pathlib import Path
from typing import Any

PROGRESS_EVERY_S = 0.25


def child_main(
    events: Any, control: Any, models_root: str, sources: list[str], op_id: str, files: list[dict[str, Any]]
) -> None:
    from .downloads import CancelToken, DlFile, Downloader, DownloadError

    token = CancelToken()

    def watch_control() -> None:
        try:
            while True:
                msg = control.recv()
                if msg == "pause":
                    token.pause()
                elif msg == "cancel":
                    token.cancel()
        except (EOFError, OSError):  # the worker died: stop, keep the parts for resume, never linger as an orphan
            token.cancel(keep_part=True)
            threading.Timer(3.0, lambda: os._exit(0)).start()

    threading.Thread(target=watch_control, daemon=True).start()
    last = [0.0]

    def progress(ev: dict[str, Any]) -> None:
        now = time.monotonic()  # noqa: TID251 - rate limit for a pipe, not product time
        if not ev.get("waiting_reason") and now - last[0] < PROGRESS_EVERY_S and ev["done_bytes"] < ev["total_bytes"]:
            return
        last[0] = now
        events.send(("progress", ev))

    d = Downloader(Path(models_root), sources=sources, on_progress=progress)
    dl = [DlFile(f["repo"], f["revision"], f["path"], int(f["size"]), f["sha256"]) for f in files]
    try:
        res = asyncio.run(d.download(op_id, dl, token))
        out: dict[str, Any] = {
            "status": res.status,
            "error_code": res.error_code,
            "warnings": res.warnings,
            "installed": res.installed,
            "waiting_reason": res.waiting_reason,
        }
    except DownloadError as e:
        out = {"status": "failed", "error_code": e.code, "details": e.details, "permanent": e.permanent}
    events.send(("result", out))


class ChildDownload:
    """Worker-side handle: start, read events, forward controls, and enforce cancel ≤ 2 s (DL-005)."""

    def __init__(self, models_root: Path, sources: list[str], op_id: str, files: list[dict[str, Any]]) -> None:
        import multiprocessing as mp

        ctx = mp.get_context("spawn")
        self._ev_r, ev_w = ctx.Pipe(duplex=False)
        ctl_r, self._ctl_w = ctx.Pipe(duplex=False)
        self.proc = ctx.Process(
            target=child_main,
            args=(ev_w, ctl_r, str(models_root), list(sources), op_id, files),
            daemon=True,
            name=f"opagent-download-{op_id}",
        )
        self.proc.start()
        ev_w.close()
        ctl_r.close()
        self.cancel_sent_at: float | None = None

    def send(self, msg: str) -> None:
        try:
            self._ctl_w.send(msg)
        except (BrokenPipeError, OSError):
            return
        if msg == "cancel" and self.cancel_sent_at is None:
            self.cancel_sent_at = time.monotonic()  # noqa: TID251

    def next_event(self, timeout_s: float = 0.2) -> tuple[str, Any] | None:
        if self.cancel_sent_at is not None and time.monotonic() - self.cancel_sent_at > 1.0:  # noqa: TID251
            if self.proc.is_alive():  # the OS closes the socket at once: cancel stays within 2 s
                self.proc.terminate()
                self.proc.join(2)
            return ("result", {"status": "cancelled", "error_code": None, "warnings": [], "installed": []})
        if self._ev_r.poll(timeout_s):
            try:
                return self._ev_r.recv()  # type: ignore[no-any-return]
            except EOFError:
                pass
        if not self.proc.is_alive() and not self._ev_r.poll(0):
            self.proc.join(1)
            # an unexpected exit is reported like any unexpected worker exception; the parts stay for resume
            raise RuntimeError(f"download process exited with code {self.proc.exitcode}")
        return None

    def close(self) -> None:
        self._ctl_w.close()
        self.proc.join(5)
        if self.proc.is_alive():
            self.proc.terminate()
