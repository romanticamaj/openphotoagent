"""回歸: the ETA engine had tests but no caller, so the app never showed a remaining time (invariant 9)."""

import random

import pytest
from opagent_core.eta import eta_active_s, live_eta, suspend_eta


def run(points, unit="items", eta=None):
    for t, f in points:
        eta = live_eta(eta, t, f, unit)
    return eta


@pytest.mark.unit
@pytest.mark.tc("ETA-020")
def test_eta_020_steady_progress_gives_a_remaining_time():
    eta = run([(float(t), t / 100) for t in range(0, 41, 2)])
    assert eta["state"] == "precise"  # 40 s of perfectly steady progress
    assert eta["p50_s"] == pytest.approx(60, rel=0.01)  # 40 % in 40 s
    assert eta["p10_s"] < eta["p50_s"] < eta["p90_s"]


@pytest.mark.unit
@pytest.mark.tc("ETA-020")
def test_eta_020_too_early_to_say():
    assert run([(0.0, 0.0), (1.0, 0.1)])["state"] == "estimating"
    assert run([(0.0, 0.0), (1.0, 0.1)]).get("p50_s") is None


@pytest.mark.unit
@pytest.mark.tc("ETA-020")
def test_eta_020_bytes_need_four_samples():
    assert run([(0.0, 0.0), (4.0, 0.1), (8.0, 0.2)], "bytes")["state"] == "estimating"
    assert run([(float(t), t / 100) for t in range(0, 21, 4)], "bytes")["state"] != "estimating"


@pytest.mark.unit
@pytest.mark.tc("ETA-020")
def test_eta_020_no_total_means_unknown():
    assert run([(0.0, None), (10.0, None)])["state"] == "unknown"


@pytest.mark.unit
@pytest.mark.tc("ETA-020")
def test_eta_020_reports_closer_than_a_sample_do_not_count():
    a = run([(0.0, 0.0), (5.0, 0.05)])
    assert live_eta(a, 5.1, 0.9, "items")["samples"] == a["samples"]


@pytest.mark.unit
@pytest.mark.tc("ETA-020")
def test_eta_020_no_progress_becomes_stalled():
    eta = run([(float(t), t / 100) for t in range(0, 21, 2)])
    assert live_eta(eta, 25.0, 0.2, "items")["state"] != "stalled"
    assert live_eta(eta, 60.0, 0.2, "items")["state"] == "stalled"


@pytest.mark.unit
@pytest.mark.tc("ETA-020")
def test_eta_020_precise_only_after_ten_steady_seconds():
    assert run([(float(t), t / 500) for t in range(0, 8)])["state"] == "rough"
    eta = run([(float(t), t / 500) for t in range(0, 301)])
    assert eta["p50_s"] < 600 and eta["state"] == "precise"


@pytest.mark.unit
@pytest.mark.tc("ETA-026")
def test_eta_026_a_precise_interval_is_honest():
    """ADR-0028: precise became reachable, so what it promises has to hold. ETA-016 does not look at this state."""
    hit = shown = 0
    for seed in range(40):
        rng = random.Random(seed)
        batches = [sum(rng.lognormvariate(0, 0.4) for _ in range(8)) / 8 for _ in range(400)]
        actual = sum(batches)
        eta, t = None, 0.0
        for i, dur in enumerate(batches):
            t += dur
            eta = live_eta(eta, t, (i + 1) / len(batches), "items")
            if eta["state"] == "precise":
                shown += 1
                hit += eta["p10_s"] <= actual - t <= eta["p90_s"]
    assert shown > 500
    assert hit / shown >= 0.70


@pytest.mark.unit
@pytest.mark.tc("ETA-026")
def test_eta_026_drift_needs_time_to_act():
    from opagent_core.eta import sigma_rel

    assert sigma_rel(0.0, 10, 50, 2.0, 60.0, 120.0) == pytest.approx(0.04)
    assert sigma_rel(0.0, 10, 50, 2.0, 60.0, 900.0) == pytest.approx(0.05)
    assert sigma_rel(0.0, 10, 50, 2.0, 60.0, 7200.0) == pytest.approx(0.1)


@pytest.mark.unit
@pytest.mark.tc("ETA-021")
def test_eta_021_the_first_shown_estimate_is_kept_for_calibration():
    """V7 judges what the user was first told, as a total duration, not the last and easiest estimate."""
    eta = run([(float(t), t / 100) for t in range(0, 11, 2)])
    first = eta["first"]
    assert first["p50_s"] == pytest.approx(100, rel=0.01)
    assert first["p10_s"] < first["p50_s"] < first["p90_s"]
    assert run([(12.0, 0.5), (14.0, 0.9)], eta=eta)["first"] == first


@pytest.mark.unit
@pytest.mark.tc("ETA-021")
def test_eta_021_paused_time_is_not_active_time():
    eta = suspend_eta(run([(0.0, 0.0), (10.0, 0.1)]), 12.0)
    eta = run([(500.0, 0.1), (510.0, 0.2)], eta=eta)
    assert eta_active_s(eta, 510.0) == pytest.approx(22)


@pytest.mark.unit
@pytest.mark.tc("ETA-021")
def test_eta_021_the_state_survives_json():
    import json

    a = run([(0.0, 0.0), (5.0, 0.1), (10.0, 0.2)])
    b = json.loads(json.dumps(a))
    assert live_eta(a, 15.0, 0.3, "items") == live_eta(b, 15.0, 0.3, "items")


@pytest.mark.unit
@pytest.mark.tc("ETA-029")
def test_eta_029_an_estimate_seeded_at_the_start_is_rough_by_the_second_report():
    """回歸 (audit round 19): a real 133 s run of 40 photos never left 「estimating」: the first report only set the
    baseline, and two samples are needed. Seeded at the operation's start (0 done), the second report gives a time."""
    from opagent_core.eta import seed_eta

    eta = seed_eta(None, 100.0)
    eta = live_eta(eta, 110.0, 4 / 40, "items")
    assert eta["samples"] == 1 and eta["active_s"] == pytest.approx(10.0)
    eta = live_eta(eta, 120.0, 8 / 40, "items")
    assert eta["state"] in ("rough", "precise") and eta["p50_s"] == pytest.approx(80.0, rel=0.2)
    assert seed_eta(eta, 500.0)["_pt"] == eta["_pt"]  # an estimate already running is left alone


@pytest.mark.unit
@pytest.mark.tc("ETA-033")
def test_eta_033_the_start_up_time_leaves_the_rate_once_photos_flow():
    """回歸 (audit round 22): the first time left, 「約 2 分鐘」, was about twice the real 62 s: the sample seeded at the
    operation's start held the scan and the model loading. Once a second real sample is in, the rate is the photos'."""
    from opagent_core.eta import seed_eta

    eta = seed_eta(None, 100.0)
    eta = live_eta(eta, 125.0, 8 / 40, "items")  # 25 s for 8, of which ~13 s was loading
    eta = live_eta(eta, 131.0, 12 / 40, "items")  # then 4 photos in 6 s
    assert eta["p50_s"] == pytest.approx((28 / 40) / ((4 / 40) / 6.0), rel=0.15)  # about 42 s, not about 85
