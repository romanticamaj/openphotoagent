import json
import math
import random
from datetime import UTC, datetime
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from opagent_core.eta import (
    CascadeMix,
    ItemsEta,
    display_eta,
    format_eta,
    queue_ahead,
    should_update,
    slow_explanation,
)

LOCALES = Path(__file__).resolve().parents[4] / "packages/i18n/locales"


@pytest.mark.unit
@pytest.mark.tc("ETA-011")
def test_eta_011_rounding_and_finish_time():
    assert format_eta(45, "zh-TW", datetime(2026, 11, 1, 14, 0, tzinfo=UTC)) == "不到 1 分鐘"
    assert format_eta(7.4 * 60, "zh-TW", datetime(2026, 11, 1, 14, 0, tzinfo=UTC)) == "約 7 分鐘"
    assert format_eta(23 * 60, "zh-TW", datetime(2026, 11, 1, 14, 7, tzinfo=UTC)) == "約 25 分鐘 · 預計 14:30 完成"
    assert format_eta(97 * 60, "zh-TW", datetime(2026, 11, 1, 10, 0, tzinfo=UTC)).startswith("約 1 小時 40 分")
    assert format_eta(7 * 3600 + 12 * 60, "zh-TW", datetime(2026, 11, 1, 19, 18, tzinfo=UTC)) == (
        "約 7 小時 · 明天 02:30 完成"
    )
    assert format_eta(45, "en", datetime(2026, 11, 1, tzinfo=UTC)) == "Less than a minute"
    assert format_eta(7.4 * 60, "en", datetime(2026, 11, 1, tzinfo=UTC)) == "About 7 min"
    assert format_eta(23 * 60, "en", datetime(2026, 11, 1, 14, 7, tzinfo=UTC)) == "About 25 min · done by 14:30"
    assert format_eta(7 * 3600 + 12 * 60, "en", datetime(2026, 11, 1, 19, 18, tzinfo=UTC)) == (
        "About 7 h · done tomorrow 02:30"
    )


@pytest.mark.unit
@pytest.mark.tc("ETA-012")
def test_eta_012_no_jitter():
    assert should_update(shown=100, new=108, state="rough") is False
    assert should_update(shown=100, new=111, state="rough") is True
    assert should_update(shown=100, new=98, state="precise") is False
    assert should_update(shown=100, new=96, state="precise") is True


@pytest.mark.unit
@pytest.mark.tc("ETA-013")
def test_eta_013_honest_slowdown_note():
    zh = json.loads((LOCALES / "zh-TW.json").read_text(encoding="utf-8"))
    k = slow_explanation(rate_obs=1.0, rate_prior=2.5, gpu_util=30, cpu_util=100, gpu_busy_external=False)
    assert k == "eta.slow.decode_bottleneck"
    assert zh["eta"]["slow"]["decode_bottleneck"] == "比預估慢：HEIC 解碼是瓶頸"
    k2 = slow_explanation(rate_obs=1.0, rate_prior=2.5, gpu_util=95, cpu_util=40, gpu_busy_external=True)
    assert k2 == "eta.slow.gpu_busy" and zh["eta"]["slow"]["gpu_busy"].startswith("比預估慢")
    assert slow_explanation(rate_obs=2.0, rate_prior=2.5, gpu_util=30, cpu_util=100, gpu_busy_external=False) is None


@pytest.mark.unit
@pytest.mark.tc("ETA-014")
def test_eta_014_paused_freezes():
    eta = {"state": "precise", "p50": 161}
    d = display_eta(eta, status="paused", waiting_reason="PAUSED_ON_BATTERY")
    assert d == {"text_key": "eta.paused", "frozen": True, "p50": 161, "countdown": False}
    assert display_eta(eta, status="running")["countdown"] is True


@pytest.mark.unit
@pytest.mark.tc("ETA-015")
def test_eta_015_cascade_mix_tracks_vlm_share():
    m = CascadeMix(prior_ratio=0.30)
    before = m.remaining_seconds(items=1000, vlm_s=2.0, emb_s=0.05)
    for _ in range(20):
        m.observe(vlm=45, total=100)
    assert m.ratio == pytest.approx(0.45, abs=0.02)
    assert m.remaining_seconds(items=1000, vlm_s=2.0, emb_s=0.05) > before


def _simulate(seed: int) -> tuple[int, int]:
    rnd = random.Random(seed)
    n, b = 1000, 16
    times = [rnd.lognormvariate(math.log(0.4), 0.4) * (1 + 0.10 * i / n) for i in range(n)]
    batches = [sum(times[i : i + b]) for i in range(0, n, b)]
    est = ItemsEta(total=n, batch_size=b)
    hits = tot = 0
    marks = {int(len(batches) * f) for f in (0.2, 0.5, 0.8)}
    for k, bt in enumerate(batches):
        est.batch_done(items=min(b, n - est.done), seconds=bt)
        if k in marks:
            p10, p50, p90 = est.estimate()
            truth = sum(batches[k + 1 :])
            tot += 1
            hits += p10 <= truth <= p90
    return hits, tot


@pytest.mark.property
@pytest.mark.tc("ETA-016")
def test_eta_016_interval_coverage_monte_carlo():
    hits = tot = 0
    for seed in range(200):
        h, t = _simulate(seed)
        hits += h
        tot += t
    assert hits / tot >= 0.70


@pytest.mark.property
@pytest.mark.tc("ETA-016")
@settings(max_examples=30, deadline=None)
@given(seed=st.integers(0, 10**6))
def test_eta_016_interval_contains_p50(seed):
    rnd = random.Random(seed)
    est = ItemsEta(total=500, batch_size=10)
    for _ in range(rnd.randint(1, 40)):
        est.batch_done(items=10, seconds=rnd.uniform(0.5, 8))
    p10, p50, p90 = est.estimate()
    assert p10 <= p50 <= p90


@pytest.mark.unit
@pytest.mark.tc("ETA-017")
def test_eta_017_queue_wait():
    assert queue_ahead([300, 600]) == 900
    assert queue_ahead([]) == 0
