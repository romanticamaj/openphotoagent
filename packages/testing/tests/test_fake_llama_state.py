"""INF-OOM-004 (回歸): the fake llama-server's cross-restart state survives concurrent requests and SIGKILL.

INF-OOM-001 failed ~1 in 12 runs: two in-flight requests both read "0 crashes", and the state file was rewritten
by truncate-then-write, so the SIGKILL from one request could land mid-write of the other. The restarted server
then read a broken file, dropped the connection, and the adapter counted a second crash."""

import json
import threading

import pytest
from opagent_testing import fake_llama_server as F


@pytest.fixture
def state(tmp_path, monkeypatch):
    f = tmp_path / "state.json"
    monkeypatch.setitem(F.STATE, "args", {**F.STATE.get("args", {}), "fake_state": str(f), "crash_first_n": 1})
    return f


@pytest.mark.unit
@pytest.mark.tc("INF-OOM-004")
def test_inf_oom_004_state_file_is_never_seen_half_written(state):
    stop = threading.Event()
    bad: list[str] = []

    def reader() -> None:
        while not stop.is_set():
            try:
                if state.exists():
                    json.loads(state.read_text(encoding="utf-8"))
            except ValueError as e:
                bad.append(str(e))

    t = threading.Thread(target=reader)
    t.start()
    writers = [
        threading.Thread(target=lambda: [F._bump_persistent("requests_seen") for _ in range(300)]) for _ in range(4)
    ]
    for w in writers:
        w.start()
    for w in writers:
        w.join()
    stop.set()
    t.join()
    assert bad == [], bad[:3]


@pytest.mark.unit
@pytest.mark.tc("INF-OOM-004")
def test_inf_oom_004_exactly_n_requests_claim_a_crash(state):
    for _ in range(20):
        state.unlink(missing_ok=True)
        barrier = threading.Barrier(16)
        claims: list[bool] = []

        def go(barrier: threading.Barrier = barrier, claims: list[bool] = claims) -> None:
            barrier.wait()
            claims.append(F._claim_crash())

        ts = [threading.Thread(target=go) for _ in range(16)]
        for x in ts:
            x.start()
        for x in ts:
            x.join()
        assert claims.count(True) == 1, claims.count(True)
