"""Watch folders on the worker (05 §5; 09b §6). Only the worker touches the photo folders (invariant 1).

* Native change events (inotify / FSEvents / ReadDirectoryChangesW through watchdog) feed a debouncer per source;
  network file systems (SMB, NFS, …) send no reliable events, so they are polled at the configured interval.
* ``tick`` hands the files that settled to the API as one batch per source; the API turns batches into a single
  incremental scan (and labelling, and at most a *plan* — files never move without approval).
* Paths stay relative to the source; the absolute folder is resolved through the sandbox once, when watching starts.
"""

from __future__ import annotations

import os
import queue
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from opagent_core import watch as W

IGNORED_SUFFIXES = (".part", ".tmp", ".crdownload", ".download", "~")


@dataclass
class _Watch:
    source_id: str
    folder: Path
    mode: str
    fstype: str
    debounce: W.Debouncer
    poll_s: float
    observer: Any = None
    snapshot: dict[str, tuple[int, int]] = field(default_factory=dict)
    last_poll: float | None = None


def _read_mounts() -> str:
    try:
        return Path("/proc/mounts").read_text(encoding="utf-8")
    except OSError:  # macOS / Windows: FSEvents and ReadDirectoryChangesW cover local volumes; shares are polled
        return ""


class WatchManager:
    def __init__(self, agent: Any, mounts: str | None = None, clock: Any = None) -> None:
        self.agent = agent
        self._clock = clock or agent.clock.monotonic
        self._mounts = mounts
        self._events: queue.SimpleQueue[tuple[str, str]] = queue.SimpleQueue()
        self._watches: dict[str, _Watch] = {}
        self._lock = threading.Lock()

    # ---- configuration -------------------------------------------------
    def sync(self) -> None:
        """Start and stop watchers to match what the API says is enabled for this worker."""
        r = self.agent.client.get("/worker-api/v1/watches", params={"worker_id": self.agent.worker_id})
        r.raise_for_status()
        wanted = {w["source_id"]: w for w in r.json()}
        for sid in list(self._watches):
            if sid not in wanted:
                self._stop_one(sid)
        for sid, w in wanted.items():
            if sid not in self._watches:
                self._start(w)

    def _start(self, w: dict[str, Any]) -> None:
        sp = self.agent.sb.resolve(w["root_alias"], w.get("sub_path") or "")
        folder = Path(sp.real)
        mounts = self._mounts if self._mounts is not None else _read_mounts()
        fstype = W.fstype_of(str(folder), mounts) if mounts else ""
        mode = W.mode_for(fstype)
        wt = _Watch(
            source_id=w["source_id"],
            folder=folder,
            mode=mode,
            fstype=fstype,
            debounce=W.Debouncer(debounce_s=float(w["debounce_s"]), stable_s=float(w["stable_s"])),
            poll_s=float(w["poll_interval_s"]),
        )
        if mode == "native":
            wt.observer = self._observe(wt)
        self._watches[wt.source_id] = wt
        self.agent.client.post(
            f"/worker-api/v1/watches/{wt.source_id}/status", json={"mode": mode, "fstype": fstype or None}
        )

    def _observe(self, wt: _Watch) -> Any:
        from watchdog.events import FileSystemEvent, FileSystemEventHandler
        from watchdog.observers import Observer

        events = self._events
        sid, root = wt.source_id, wt.folder

        class Handler(FileSystemEventHandler):
            def on_any_event(self, event: FileSystemEvent) -> None:
                if event.is_directory or event.event_type not in ("created", "modified", "moved", "closed"):
                    return
                raw = getattr(event, "dest_path", "") or event.src_path
                p = os.fsdecode(raw)
                if p.endswith(IGNORED_SUFFIXES) or os.path.basename(p).startswith("."):
                    return
                try:
                    rel = Path(p).relative_to(root).as_posix()
                except ValueError:
                    return
                events.put((sid, rel))

        obs = Observer()
        obs.schedule(Handler(), str(root), recursive=True)
        obs.daemon = True
        obs.start()
        return obs

    def observer_kind(self, source_id: str) -> str:
        return self._watches[source_id].mode

    def _stop_one(self, sid: str) -> None:
        wt = self._watches.pop(sid)
        if wt.observer is not None:
            wt.observer.stop()
            wt.observer.join(timeout=5)

    def stop(self) -> None:
        for sid in list(self._watches):
            self._stop_one(sid)

    # ---- events ----------------------------------------------------------
    def pump(self, now: float | None = None) -> int:
        """Move events delivered by the observer threads into the debouncers."""
        t = self._now(now)
        n = 0
        while True:
            try:
                sid, rel = self._events.get_nowait()
            except queue.Empty:
                return n
            wt = self._watches.get(sid)
            if wt is not None:
                wt.debounce.event(rel, now=t)
                n += 1

    def poll(self, now: float | None = None) -> int:
        """Network file systems: compare a listing with the previous one; new or changed files become events."""
        t = self._now(now)
        n = 0
        for wt in self._watches.values():
            if wt.mode != "poll" or (wt.last_poll is not None and t - wt.last_poll < wt.poll_s):
                continue
            wt.last_poll = t
            current: dict[str, tuple[int, int]] = {}
            for dirpath, dirs, files in os.walk(wt.folder, followlinks=False):
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                for f in files:
                    if f.startswith(".") or f.endswith(IGNORED_SUFFIXES):
                        continue
                    p = Path(dirpath) / f
                    try:
                        st = p.lstat()
                    except OSError:
                        continue
                    rel = p.relative_to(wt.folder).as_posix()
                    current[rel] = (st.st_size, st.st_mtime_ns)
                    if wt.snapshot.get(rel) != current[rel]:
                        wt.debounce.event(rel, now=t)
                        n += 1
            wt.snapshot = current
        return n

    def pending(self) -> int:
        return sum(wt.debounce.pending() for wt in self._watches.values())

    def tick(self, now: float | None = None) -> int:
        """Send settled files as batches; returns how many files were handed on."""
        t = self._now(now)
        sent = 0
        for wt in self._watches.values():

            def size_of(rel: str, wt: _Watch = wt) -> int | None:
                try:
                    return (wt.folder / rel).lstat().st_size
                except OSError:
                    return None

            ready = wt.debounce.ready(now=t, size_of=size_of)
            for b in W.batches(ready):
                r = self.agent.client.post(
                    f"/worker-api/v1/watches/{wt.source_id}/batch",
                    json={"files": b.count, "priority": b.priority, "sample": list(b.paths[:20])},
                )
                r.raise_for_status()
                sent += b.count
        return sent

    def step(self) -> None:
        """One pass of the long-running loop."""
        self.pump()
        self.poll()
        self.tick()

    def _now(self, now: float | None) -> float:
        return float(self._clock()) if now is None else now
