"""Fixture loading for the FakeBackend (kept out of the backend module: adapters never open files, INF-CONF-011)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

FIXTURES = Path(__file__).resolve().parents[4] / "fixtures"


def fake_vectors() -> dict[str, Any]:
    return dict(yaml.safe_load((FIXTURES / "fake_vectors.yaml").read_text(encoding="utf-8")))
