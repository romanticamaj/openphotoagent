"""UUID v7 ids (docs/02 §3.2: time-ordered primary keys).

RFC 9562 §6.2 method 1: the 12-bit ``rand_a`` field is a counter that increments for ids created in the same
millisecond (and starts from a random value each new millisecond), so ids sort in creation order within a
process. When the counter overflows the timestamp advances by one millisecond, as the RFC allows.
"""

from __future__ import annotations

import os
import threading
import uuid

from .clock import Clock

_lock = threading.Lock()
_last_ms = -1
_counter = 0
_COUNTER_MAX = (1 << 12) - 1


def uuid7(clock: Clock) -> str:
    global _last_ms, _counter
    ms = int(clock.now().timestamp() * 1000) & ((1 << 48) - 1)
    with _lock:
        if ms > _last_ms:
            _last_ms = ms
            _counter = int.from_bytes(os.urandom(2), "big") & 0x7FF  # leave head-room before overflow
        else:  # same millisecond (or a clock that went backwards): keep ordering
            _counter += 1
            if _counter > _COUNTER_MAX:
                _last_ms += 1
                _counter = 0
            ms = _last_ms
        counter = _counter
    rand_b = int.from_bytes(os.urandom(8), "big") & ((1 << 62) - 1)
    value = (ms << 80) | (0x7 << 76) | (counter << 64) | (0b10 << 62) | rand_b
    return str(uuid.UUID(int=value))
