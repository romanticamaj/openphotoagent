"""Mini benchmark metrics (03 §6; 09b §6 BENCH): rule-based scores over a golden set, the coverage–accuracy curve,
an automatic threshold, and the golden-set minimum."""

import pytest
from opagent_core import bench as B

GOLD = {f"a{i}": ("cat" if i < 6 else "dog") for i in range(10)}


@pytest.mark.unit
@pytest.mark.tc("BENCH-001")
def test_bench_001_scores_and_curve():
    preds = {f"a{i}": ("cat", 0.9) for i in range(5)}  # 5 confident, right
    preds["a5"] = ("dog", 0.4)  # wrong, unsure
    preds |= {f"a{i}": ("dog", 0.8) for i in range(6, 9)}
    preds["a9"] = ("cat", 0.6)  # wrong
    m = B.classification(GOLD, preds, classes=["cat", "dog"])
    assert m["accuracy"] == pytest.approx(0.8)
    assert m["per_class"]["cat"] == pytest.approx({"precision": 5 / 6, "recall": 5 / 6, "f1": 5 / 6})
    assert m["per_class"]["dog"] == pytest.approx({"precision": 3 / 4, "recall": 3 / 4, "f1": 3 / 4})
    assert m["macro_f1"] == pytest.approx((5 / 6 + 3 / 4) / 2)
    assert m["confusion"] == {"cat": {"cat": 5, "dog": 1}, "dog": {"cat": 1, "dog": 3}}
    curve = {round(p["tau"], 2): p for p in m["coverage_curve"]}
    assert curve[0.0]["coverage"] == 1.0 and curve[0.0]["accuracy"] == pytest.approx(0.8)
    assert curve[0.7]["coverage"] == pytest.approx(0.8) and curve[0.7]["accuracy"] == 1.0  # auto 80 % at 100 %
    assert curve[0.95]["coverage"] == 0.0 and curve[0.95]["accuracy"] is None
    assert 0.0 <= m["ece"] <= 1.0
    missing = B.classification(GOLD, {"a0": ("cat", 0.9)}, classes=["cat", "dog"])
    assert missing["accuracy"] == pytest.approx(0.1) and missing["failed"] == 9  # no answer = wrong, counted


@pytest.mark.unit
@pytest.mark.tc("BENCH-001")
def test_bench_001_latency_and_recommendation():
    assert B.latency([10, 20, 30, 40, 100]) == {"p50_ms": 30, "p95_ms": 100, "mean_ms": 40.0}
    cands = [
        {"name": "a", "macro_f1": 0.80, "items_per_s": 10.0, "vram_mb": 9000},
        {"name": "b", "macro_f1": 0.90, "items_per_s": 1.0, "vram_mb": 15000},
        {"name": "c", "macro_f1": 0.95, "items_per_s": 5.0, "vram_mb": 20000},
    ]
    assert B.recommend(cands, vram_mb=16000) == "b"  # best F1 that fits
    assert B.recommend(cands, vram_mb=16000, items=10_000, within_s=3600) == "a"  # b needs 10,000 s
    assert B.recommend(cands, vram_mb=1000) is None


@pytest.mark.unit
@pytest.mark.tc("BENCH-002")
def test_bench_002_golden_minimum():
    ok, why = B.golden_ready({f"a{i}": "cat" for i in range(60)}, classes=["cat", "dog"])
    assert not ok and why == {"n": 60, "min": 50, "per_class": 5, "short": ["dog"]}
    items = {f"a{i}": "cat" for i in range(45)} | {f"b{i}": "dog" for i in range(5)}
    assert B.golden_ready(items, classes=["cat", "dog"]) == (True, None)
    ok, why = B.golden_ready({f"a{i}": ("cat" if i % 2 else "dog") for i in range(20)}, classes=["cat", "dog"])
    assert not ok and why["n"] == 20 and why["short"] == []


@pytest.mark.unit
@pytest.mark.tc("BENCH-003")
def test_bench_003_suggested_threshold():
    curve = [
        {"tau": 0.0, "coverage": 1.0, "accuracy": 0.80},
        {"tau": 0.5, "coverage": 0.9, "accuracy": 0.90},
        {"tau": 0.7, "coverage": 0.8, "accuracy": 0.96},
        {"tau": 0.9, "coverage": 0.4, "accuracy": 1.0},
        {"tau": 0.95, "coverage": 0.0, "accuracy": None},
    ]
    assert B.suggest_threshold(curve, target=0.95) == {"tau": 0.7, "coverage": 0.8, "accuracy": 0.96}
    assert B.suggest_threshold(curve, target=0.99) == {"tau": 0.9, "coverage": 0.4, "accuracy": 1.0}
    assert B.suggest_threshold(curve[:2], target=0.95) is None
