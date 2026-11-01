"""UUID v7 ids (docs/02 §3.2: time-ordered primary keys)."""

from __future__ import annotations

import os
import uuid

from .clock import Clock


def uuid7(clock: Clock) -> str:
    ms = int(clock.now().timestamp() * 1000) & ((1 << 48) - 1)
    rand = int.from_bytes(os.urandom(10), "big")
    value = (ms << 80) | (0x7 << 76) | ((rand >> 64) & 0xFFF) << 64 | (0b10 << 62) | (rand & ((1 << 62) - 1))
    return str(uuid.UUID(int=value))
