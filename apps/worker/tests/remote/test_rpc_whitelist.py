"""The worker accepts a fixed set of RPC kinds from the control plane (02 §6.3; 09b §8.4 MU-SEC-004)."""

import pytest
from opagent_worker.agent import RPC_KINDS, WorkerAgent


class Rec:
    def __init__(self, tasks):
        self.tasks, self.posts = list(tasks), []

    def post(self, path, json=None, **kw):
        self.posts.append((path, json))

        class R:
            status_code = 204 if path.endswith("/lease") and not self.tasks else 200

            def json(_s):
                return self.tasks.pop(0)

            def raise_for_status(_s):
                return None

        return R()


@pytest.mark.security
@pytest.mark.tc("MU-SEC-004")
def test_sec_004_rpc_whitelist(tmp_path, caplog):
    handlers = {m[4:] for m in dir(WorkerAgent) if m.startswith("_do_")}
    assert set(RPC_KINDS) == handlers  # every handler is listed on purpose, nothing else is reachable
    dangerous = ("exec", "shell", "eval", "run", "command", "cmd", "spawn", "python", "subprocess")
    assert not [k for k in RPC_KINDS if any(d == k or k.startswith(d + "_") for d in dangerous)]
    hostile = ["exec", "shell", "__class__", "__init__", "_scan", "register", "run_once", "../scan", "scan;rm"]
    rec = Rec([{"id": f"t{i}", "kind": k, "payload": {"cmd": "rm -rf /"}} for i, k in enumerate(hostile)])
    w = WorkerAgent(rec, roots=[], home=tmp_path, worker_id="w1")
    with caplog.at_level("WARNING", logger="opagent.worker"):
        while w.run_once():
            pass
    done = [(p, b) for p, b in rec.posts if p.endswith("/complete")]
    assert len(done) == len(hostile)
    for _, body in done:
        assert body["ok"] is False and body["result"]["error"] == "UNKNOWN_RPC"
    assert sum("UNKNOWN_RPC" in r.getMessage() for r in caplog.records) == len(hostile)
