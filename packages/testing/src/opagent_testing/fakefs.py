"""FaultFs: wraps a real FsOps and injects errno failures or hooks on the N-th call (docs/09 §3.3 FakeFS)."""

from __future__ import annotations

import errno
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Fault:
    method: str
    nth: int = 1  # 1-based call index of that method
    err: int | None = None  # errno to raise
    times: int = 1  # how many consecutive calls fail
    before: Callable[..., None] | None = None  # hook run before the real call (e.g. create a racing dst)


@dataclass
class FaultFs:
    inner: Any
    faults: list[Fault] = field(default_factory=list)
    calls: dict[str, int] = field(default_factory=dict)
    log: list[str] = field(default_factory=list)

    def __getattr__(self, name: str) -> Any:
        fn = getattr(self.inner, name)

        def wrapper(*a: Any, **kw: Any) -> Any:
            n = self.calls[name] = self.calls.get(name, 0) + 1
            self.log.append(name)
            for f in self.faults:
                if f.method == name and f.nth <= n < f.nth + f.times:
                    if f.before is not None:
                        f.before(*a, **kw)
                    if f.err is not None:
                        if f.err == errno.EEXIST:
                            raise FileExistsError(f.err, "injected", str(a[-1] if a else ""))
                        raise OSError(f.err, f"injected {errno.errorcode.get(f.err, f.err)}")
            return fn(*a, **kw)

        return wrapper
