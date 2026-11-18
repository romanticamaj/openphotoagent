"""Results reach the server a few photos at a time within a leased batch (ETA-030): one report per 16 photos gave a
40-photo run three data points and no time left on screen."""

from types import SimpleNamespace

import pytest


@pytest.mark.unit
@pytest.mark.tc("ETA-030")
def test_eta_030_results_are_reported_every_few_photos(tmp_path, sandbox_tree):
    from opagent_worker.agent import WorkerAgent

    class Client:
        def __init__(self):
            self.posted = []

        def post(self, url, json=None, **kw):
            self.posted.append((url, json))
            if url.endswith("/leases"):
                items = [{"id": f"i{k}", "asset_id": f"a{k}"} for k in range(10)]
                return SimpleNamespace(status_code=200, json=lambda: {"items": items}, raise_for_status=lambda: None)
            return SimpleNamespace(status_code=200, json=lambda: {}, raise_for_status=lambda: None)

    c = Client()
    a = WorkerAgent(c, roots=sandbox_tree.roots, home=tmp_path / "w", worker_id="w1")
    a._infer = lambda items: [{"job_item_id": it["id"], "status": "ok"} for it in items]  # type: ignore[method-assign]
    a._send_faces = lambda reports: None  # type: ignore[method-assign]
    sent = []
    a._send_results = lambda reports: sent.append(len(reports))  # type: ignore[method-assign]
    assert a.process_items_once() == 10
    assert sent == [4, 4, 2]
