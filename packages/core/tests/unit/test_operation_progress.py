import json
from pathlib import Path

import pytest

from opagent_core.operation import Elapsed, Phase, job_label_fraction, parent_fraction, phase_fraction

LOCALES = Path(__file__).resolve().parents[4] / "packages/i18n/locales"
JOB = [("plan", 2), ("cache_lookup", 3), ("model_load", 5), ("embed", 10), ("infer", 75), ("postprocess", 5)]


@pytest.mark.unit
@pytest.mark.tc("OP-002")
def test_op_002_phase_weights_static_time_and_skipped():
    ph = [Phase(i, w) for i, w in JOB]
    ph[0].status = ph[1].status = "done"
    ph[4].status, ph[4].fraction = "running", 0.5
    assert phase_fraction(ph) == pytest.approx((2 + 3 + 0.5 * 75) / 100)
    ph[2].status = "skipped"  # model already loaded: its weight is redistributed
    assert phase_fraction(ph) == pytest.approx((2 + 3 + 0.5 * 75) / 95)
    timed = [Phase("a", 50, est_seconds=10, status="done"), Phase("b", 50, est_seconds=30, status="running",
                                                                  fraction=0.5)]
    assert phase_fraction(timed) == pytest.approx((10 + 15) / 40)


@pytest.mark.unit
@pytest.mark.tc("OP-003")
def test_op_003_cache_hits_do_not_jump_infer():
    c = {"total": 1000, "cached": 310, "done": 0, "failed": 0, "skipped": 0}
    start_infer = job_label_fraction(c, phase="infer")
    assert start_infer == pytest.approx((2 + 3) / 95)  # model_load skipped; cache hits counted in cache_lookup
    half = job_label_fraction({**c, "done": 345}, phase="infer")
    assert half == pytest.approx((2 + 3 + 0.5 * 75) / 95)


@pytest.mark.unit
@pytest.mark.tc("OP-004")
def test_op_004_child_operation_weights_by_time():
    assert parent_fraction([(190.0, 0.5), (3000.0, 0.1)]) == pytest.approx((95 + 300) / 3190)
    assert parent_fraction([]) == 0.0


@pytest.mark.unit
@pytest.mark.tc("OP-013")
def test_op_013_elapsed_excludes_pauses():
    e = Elapsed()
    e.start(0)
    e.pause(120)
    e.resume(180)
    e.finish(210)
    assert (e.active_ms, e.paused_ms) == (150_000, 60_000)
    assert e.summary("zh-TW") == "花了 2 分 30 秒（暫停 1 分鐘未計入）"
    assert e.summary("en") == "Took 2 min 30 s (1 min paused not counted)"
    assert Elapsed.clock_text(3725_000) == "1:02:05" and Elapsed.clock_text(724_000) == "12:04"


WAITING = ["QUEUED_BEHIND", "WAITING_GPU_SLOT", "WAITING_MODEL_DOWNLOAD", "WAITING_MODEL_LOAD", "WORKER_OFFLINE",
           "DISK_INSUFFICIENT_OUTPUT", "DISK_INSUFFICIENT_MODELS", "DISK_INSUFFICIENT_THUMBS", "NET_UNREACHABLE",
           "PAUSED_BY_USER", "PAUSED_ERROR_RATE", "PAUSED_ON_BATTERY", "PAUSED_GPU_CONTENDED", "PAUSED_NOT_IDLE",
           "SOURCE_OFFLINE"]


@pytest.mark.contract
@pytest.mark.tc("OP-017")
def test_op_017_waiting_reason_catalog_and_schema():
    from pydantic import ValidationError

    from opagent_schemas.operation import WAITING_REASONS, OperationDTO

    assert set(WAITING) <= set(WAITING_REASONS)
    for lang in ("zh-TW", "en"):
        d = json.loads((LOCALES / f"{lang}.json").read_text(encoding="utf-8"))["waiting"]
        for code in WAITING:
            assert d[code]["text"] and d[code]["cta"], (lang, code)
    OperationDTO(id="o", type="scan", status="waiting", waiting_reason="SOURCE_OFFLINE")
    with pytest.raises(ValidationError):
        OperationDTO(id="o", type="scan", status="waiting")
    with pytest.raises(ValidationError):
        OperationDTO(id="o", type="scan", status="paused", auto_paused=True)
    OperationDTO(id="o", type="scan", status="paused", waiting_reason="PAUSED_BY_USER")
    with pytest.raises(ValidationError):
        OperationDTO(id="o", type="scan", status="waiting", waiting_reason="MADE_UP")
