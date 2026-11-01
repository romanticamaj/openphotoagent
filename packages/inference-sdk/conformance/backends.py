"""Backends that must pass the conformance suite. Adapters append their factory here."""

from __future__ import annotations

from collections.abc import Callable

from opagent_inference.fake import FakeBackend
from opagent_inference.types import InferenceBackend

FACTORIES: dict[str, Callable[[], InferenceBackend]] = {
    "fake": lambda: FakeBackend(seed=0),
}
