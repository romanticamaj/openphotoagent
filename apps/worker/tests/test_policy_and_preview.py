import random

import pytest
from opagent_core.preview import stratified_sample
from opagent_worker.policy import FakeGpu, FakeIdle, FakePower, ResourcePolicy


@pytest.mark.integration
@pytest.mark.tc("Q-026")
def test_q_026_battery_pause_and_resume():
    power = FakePower(on_ac=True)
    pol = ResourcePolicy(power=power, settings={"pause_on_battery": True})
    assert pol.paused_reason() is None
    power.on_ac = False
    assert pol.paused_reason() == "PAUSED_ON_BATTERY"
    assert pol.allows("search") and not pol.allows("job.label")  # search keeps working on battery
    power.on_ac = True
    assert pol.paused_reason() is None


@pytest.mark.integration
@pytest.mark.tc("Q-027")
def test_q_027_gpu_contention_and_cpu_override():
    gpu = FakeGpu.from_profile("game-running-5gb")
    pol = ResourcePolicy(gpu=gpu, settings={"pause_on_contention": True})
    assert gpu.external_used_mb >= 5000 and gpu.fullscreen
    assert pol.paused_reason() == "PAUSED_GPU_CONTENDED"
    pol.override("use_cpu")
    assert pol.paused_reason() is None and pol.device == "cpu"
    gpu2 = FakeGpu.from_profile("game-running-5gb")
    pol2 = ResourcePolicy(gpu=gpu2, settings={"pause_on_contention": True})
    gpu2.external_used_mb, gpu2.fullscreen = 0, False  # game closed -> resumes on its own
    assert pol2.paused_reason() is None


@pytest.mark.integration
@pytest.mark.tc("Q-028")
def test_q_028_only_when_idle():
    idle = FakeIdle(seconds=5)
    pol = ResourcePolicy(idle=idle, settings={"only_when_idle": True, "idle_after_s": 300})
    assert pol.paused_reason() == "PAUSED_NOT_IDLE"
    idle.seconds = 301
    assert pol.paused_reason() is None
    idle.seconds = 5
    pol.override("run_now")
    assert pol.paused_reason() is None


@pytest.mark.unit
@pytest.mark.tc("Q-023")
def test_q_023_stratified_preview_sample():
    rnd = random.Random(1)
    items = [
        {"id": f"i{i}", "folder": f"f{rnd.randint(0, 29)}", "month": f"2024-{rnd.randint(1, 12):02d}"}
        for i in range(1000)
    ]
    a = stratified_sample(items, k=10, seed=42)
    b = stratified_sample(items, k=10, seed=42)
    assert a == b and len(a) == 10 and len(set(a)) == 10
    chosen = [next(x for x in items if x["id"] == i) for i in a]
    assert len({c["folder"] for c in chosen}) == 10  # as many folders as possible
    assert len({c["month"] for c in chosen}) >= 8
    assert stratified_sample(items, k=10, seed=7) != a
