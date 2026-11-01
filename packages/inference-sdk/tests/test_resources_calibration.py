import math

import numpy as np
import pytest

from opagent_core.vram import budget_gb, can_coload
from opagent_inference.calibration import ece, fit_platt, fit_temperature, platt_from_dict
from opagent_inference.prototypes import alpha_for, class_scores
from opagent_inference.resources import ResourceManager, Slot
from opagent_testing.clock import FakeClock


@pytest.mark.unit
@pytest.mark.tc("INF-VRAM-001")
def test_inf_vram_001_budget_golden():
    assert budget_gb(total=16, safety=0.92, external=1.5, desktop=0.8) == pytest.approx(12.42, abs=0.01)


@pytest.mark.unit
@pytest.mark.tc("INF-VRAM-002")
def test_inf_vram_002_coload_decision():
    assert can_coload(need_large=8.5, resident_small=1.5, budget=14.7) == "ok"
    assert can_coload(need_large=13.9, resident_small=1.5, budget=14.7) == "sequential"
    assert can_coload(need_large=15.0, resident_small=1.5, budget=14.7) == "blocked"


@pytest.mark.unit
@pytest.mark.tc("INF-VRAM-003")
def test_inf_vram_003_lru_unload_respects_refcount_and_slots():
    clk = FakeClock()
    rm = ResourceManager(budget_gb=12.0, clock=clk)
    rm.load("emb", 1.5, Slot.SMALL)
    clk.advance(1)
    rm.load("A", 6.0, Slot.LARGE)
    rm.acquire("A")  # serving requests
    clk.advance(1)
    rm.load("B", 3.0, Slot.LARGE)
    clk.advance(1)
    evicted = rm.load("C", 3.0, Slot.LARGE)
    assert evicted == ["B"]
    assert set(rm.loaded) == {"emb", "A", "C"}
    rm.release("A")
    clk.advance(1)
    assert rm.load("D", 4.0, Slot.LARGE) == ["A"]  # large slot evicted before small even if older


@pytest.mark.unit
@pytest.mark.tc("INF-VRAM-004")
def test_inf_vram_004_idle_unload():
    clk = FakeClock()
    events = []
    rm = ResourceManager(budget_gb=12.0, clock=clk, idle_unload_s=600, on_event=events.append)
    rm.load("vlm", 6.0, Slot.LARGE)
    rm.load("emb", 1.5, Slot.SMALL)
    clk.advance(599)
    assert rm.sweep_idle() == []
    clk.advance(1)
    assert rm.sweep_idle() == ["vlm"]  # only the large slot is unloaded when idle
    assert events == [{"kind": "model.unloaded", "model": "vlm", "reason": "idle"}]
    assert set(rm.loaded) == {"emb"}


@pytest.mark.unit
@pytest.mark.tc("INF-CAL-001")
def test_inf_cal_001_temperature_fit():
    rng = np.random.default_rng(0)
    n, k = 4000, 6
    true_t = 0.07
    logits = rng.normal(0, 0.05, size=(n, k))
    labels = np.array([rng.choice(k, p=np.exp(row / true_t) / np.exp(row / true_t).sum()) for row in logits])
    t = fit_temperature(logits, labels)
    assert 0.06 <= t <= 0.08

    def probs(tt):
        z = logits / tt
        z = np.exp(z - z.max(1, keepdims=True))
        return z / z.sum(1, keepdims=True)

    p_fit, p_def = probs(t), probs(0.05)
    assert ece(p_fit.max(1), p_fit.argmax(1) == labels) < ece(p_def.max(1), p_def.argmax(1) == labels)


@pytest.mark.unit
@pytest.mark.tc("INF-CAL-002")
def test_inf_cal_002_platt_roundtrip():
    rng = np.random.default_rng(1)
    raw = rng.uniform(0, 1, 2000)
    y = (rng.uniform(0, 1, 2000) < 1 / (1 + np.exp(-(4 * raw - 2)))).astype(int)
    cal = fit_platt(raw, y)
    d = cal.to_dict()
    again = platt_from_dict(d)
    xs = np.linspace(0, 1, 11)
    assert np.allclose(cal(xs), again(xs))
    assert set(d) == {"kind", "a", "b"} and d["kind"] == "platt"


@pytest.mark.unit
@pytest.mark.tc("INF-CAS-008")
def test_inf_cas_008_prototype_alpha_and_medoids():
    assert alpha_for(0) == 1.0
    assert alpha_for(2) == pytest.approx(0.76)
    assert alpha_for(5) == 0.4 and alpha_for(8) == 0.4
    rng = np.random.default_rng(2)
    beach = rng.normal(0, 0.01, (4, 8)) + np.eye(8)[0]
    city = rng.normal(0, 0.01, (4, 8)) + np.eye(8)[1]
    ex = np.vstack([beach, city])
    ex /= np.linalg.norm(ex, axis=1, keepdims=True)
    img_beach = np.eye(8)[0]
    s = class_scores(img_beach, {"travel": np.eye(8)[2]}, {"travel": ex}, n_examples={"travel": 8})
    # with 8 examples two medoids are used: an image matching either mode scores high
    assert s["travel"] > 0.55


@pytest.mark.unit
@pytest.mark.tc("INF-CAS-009")
def test_inf_cas_009_negative_examples_lower_score():
    pos = np.array([[1.0, 0.2, 0, 0]])
    neg = np.array([[0.1, 1.0, 0, 0]])
    pos /= np.linalg.norm(pos, axis=1, keepdims=True)
    neg /= np.linalg.norm(neg, axis=1, keepdims=True)
    img = np.array([0.2, 1.0, 0, 0])
    img /= np.linalg.norm(img)
    txt = {"food": np.array([0.7, 0.7, 0, 0]) / math.sqrt(0.98)}
    with_neg = class_scores(img, txt, {"food": pos}, n_examples={"food": 1}, negatives={"food": neg})
    without = class_scores(img, txt, {"food": pos}, n_examples={"food": 1})
    assert with_neg["food"] < without["food"]
