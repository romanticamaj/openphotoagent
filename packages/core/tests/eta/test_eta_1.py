import math

import pytest

from opagent_core.eta import (
    BytesRate,
    EtaTracker,
    interval,
    multi_phase,
    select_prior,
    shrink,
)


@pytest.mark.unit
@pytest.mark.tc("ETA-001")
def test_eta_001_time_weighted_ewma_irregular_samples():
    r = BytesRate(tau_fast=5.0, tau_slow=30.0)
    t, total = 0.0, 0
    samples = [(0.2, 2_000_000), (0.25, 2_200_000), (0.4, 4_400_000), (1.0, 9_000_000), (0.25, 2_600_000)]
    golden = None
    for dt, b in samples:
        t += dt
        total += b
        r.update(t, total)
        x = b / dt
        a = 1 - math.exp(-dt / 5.0)
        golden = x if golden is None else golden + a * (x - golden)
    assert abs(r.fast - golden) / golden <= 0.001
    # paused time is not sampled: a 60 s pause does not decay the rate
    r.pause(t)
    t += 60
    r.resume(t)
    t += 0.5
    total += 5_000_000
    r.update(t, total)
    x = 5_000_000 / 0.5
    a = 1 - math.exp(-0.5 / 5.0)
    assert abs(r.fast - (golden + a * (x - golden))) / r.fast <= 0.001


@pytest.mark.unit
@pytest.mark.tc("ETA-002")
def test_eta_002_fast_then_blend():
    r = BytesRate()
    r.fast, r.slow = 100.0, 50.0
    assert r.speed(elapsed=9.9) == 100.0
    assert r.speed(elapsed=10.0) == pytest.approx(0.3 * 100 + 0.7 * 50)


@pytest.mark.unit
@pytest.mark.tc("ETA-003")
def test_eta_003_estimating():
    tr = EtaTracker(kind="bytes")
    assert tr.state(elapsed=2.0, samples=10, has_prior=True) == "estimating"
    assert tr.state(elapsed=5.0, samples=3, has_prior=True) == "estimating"
    it = EtaTracker(kind="items")
    assert it.state(elapsed=20.0, samples=1, has_prior=False) == "estimating"
    assert it.state(elapsed=20.0, samples=2, has_prior=False) != "estimating"
    assert it.state(elapsed=20.0, samples=0, has_prior=True) != "estimating"


@pytest.mark.unit
@pytest.mark.tc("ETA-004")
def test_eta_004_bayesian_shrinkage():
    assert shrink(prior=2.5, basis="history", observed=3.5, n=30) == pytest.approx(3.0)
    assert shrink(prior=2.5, basis="registry_prior", observed=3.5, n=30) == pytest.approx((10 * 2.5 + 30 * 3.5) / 40)
    assert shrink(prior=2.5, basis="time_model", observed=3.5, n=30) == pytest.approx((5 * 2.5 + 30 * 3.5) / 35)
    assert shrink(prior=2.5, basis="history", observed=None, n=0) == 2.5


@pytest.mark.unit
@pytest.mark.tc("ETA-005")
def test_eta_005_prior_source_order():
    assert select_prior(same_sig=2.0, other_sig=(4.0, 40, 150), registry=1.0, gpu_factor=1.2) == (2.0, "history")
    rate, basis = select_prior(same_sig=None, other_sig=(4.0, 40, 150), registry=1.0, gpu_factor=1.2)
    assert basis == "history" and rate == pytest.approx(4.0 * 40 / 150)  # scaled by output-token ratio
    assert select_prior(same_sig=None, other_sig=None, registry=1.0, gpu_factor=1.2) == (pytest.approx(1.2),
                                                                                          "registry_prior")
    assert select_prior(same_sig=None, other_sig=None, registry=None, gpu_factor=1.2) == (None, "none")


@pytest.mark.unit
@pytest.mark.tc("ETA-006")
def test_eta_006_multi_phase_sum():
    phases = [(2e9 / 100e6, 4.0), (40.0, 25.0), (1000 / 50.0, 4.0), (1000 / 2.5, 400.0)]
    p50, var = multi_phase(phases)
    assert p50 == pytest.approx(20 + 40 + 20 + 400)
    assert var == pytest.approx(4 + 25 + 4 + 400)


@pytest.mark.unit
@pytest.mark.tc("ETA-007")
def test_eta_007_interval():
    p10, p90 = interval(100.0, 0.2)
    assert (p10, p90) == (pytest.approx(74.4), pytest.approx(125.6))
    p10, p90 = interval(100.0, 1.5)
    assert (p10, p90) == (pytest.approx(100 * (1 - 1.28 * 0.9)), pytest.approx(100 * (1 + 1.28 * 0.9)))


@pytest.mark.unit
@pytest.mark.tc("ETA-008")
def test_eta_008_hysteresis():
    tr = EtaTracker(kind="items")
    base = {"samples": 10, "has_prior": True, "observed": True}
    assert tr.update(t=0, p50=200, p10=190, p90=210, **base) == "rough"  # precise-eligible, not yet 10 s
    assert tr.update(t=9, p50=190, p10=180, p90=200, **base) == "rough"
    assert tr.update(t=9.5, p50=190, p10=100, p90=300, **base) == "rough"  # condition broke at 9.5 s
    assert tr.update(t=12, p50=180, p10=172, p90=188, **base) == "rough"
    assert tr.update(t=22, p50=170, p10=163, p90=177, **base) == "precise"  # 10 s continuous
    assert tr.update(t=23, p50=170, p10=80, p90=300, **base) == "rough"  # downgrade is immediate


@pytest.mark.unit
@pytest.mark.tc("ETA-009")
def test_eta_009_stalled():
    tr = EtaTracker(kind="items")
    tr.progress(t=0.0, done=10, batch_interval=4.0)
    assert tr.stalled_state(t=30.0) is None
    st = tr.stalled_state(t=31.0, reason="GPU_BUSY_EXTERNAL")
    assert st["state"] == "stalled" and st["reason"] == "GPU_BUSY_EXTERNAL"
    assert {c["id"] for c in st["ctas"]} == {"view_logs", "retry"}
    tr.progress(t=0.0, done=10, batch_interval=10.0)  # slow batches: 5 x interval = 50 s
    assert tr.stalled_state(t=45.0) is None


@pytest.mark.unit
@pytest.mark.tc("ETA-010")
def test_eta_010_unknown_during_enumeration():
    tr = EtaTracker(kind="items")
    assert tr.state(elapsed=50, samples=0, has_prior=False, phase="enumerate") == "unknown"
    assert tr.state(elapsed=50, samples=0, has_prior=True, phase="enumerate") != "unknown"
