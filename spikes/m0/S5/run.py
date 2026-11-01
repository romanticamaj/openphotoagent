"""M0-S5 — face model license review result (document review, see README.md)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import write_result  # noqa: E402

if __name__ == "__main__":
    write_result(
        "M0-S5",
        status="partial",
        metrics={
            "yunet": {"license": "MIT", "source": "opencv/opencv_zoo@48b10fa5", "sha256_2023mar": "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4", "redistributable": True},
            "sface": {"license": "Apache-2.0", "source": "opencv/opencv_zoo@48b10fa5", "sha256": "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79", "redistributable": True},
            "ediffiqa_tiny": {"license": "CC-BY-4.0 (attribution required)", "source": "opencv/opencv_zoo@48b10fa5", "sha256": "9426c899cc0f01665240cb7d9e7f98e18e24e456c178326c771a43da289bfc6a", "redistributable": True},
            "auraface_v1": {"license": "unverified (huggingface.co blocked in cloud session)", "redistributable": None},
            "tar_at_far_0.1pct": None,
        },
        passed=None,
        notes="Upstream LICENSE files read via git on 2026-11-01. AuraFace and TAR@FAR pending; training-data licensing needs legal review (BLOCKERS).",
    )
