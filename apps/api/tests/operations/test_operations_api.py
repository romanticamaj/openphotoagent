import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from opagent_api.app import create_app
from opagent_api.events import ProgressThrottle
from opagent_api.operations import (
    OperationWriter,
    complete_operation,
    ensure_operation,
    set_op_status,
)
from opagent_db.schema import (
    asset,
    engine_for,
    eta_calibration,
    job,
    job_item,
    operation,
    operation_event,
    perf_observation,
    result,
)
from opagent_testing.clock import FakeClock
from opagent_worker.agent import WorkerAgent
from sqlalchemy import func, insert, select, text

FX = Path(__file__).resolve().parents[4] / "fixtures/photos/basic"


@pytest.fixture
def env(embedded_pg):
    class E:
        clk = FakeClock(datetime(2026, 11, 1, tzinfo=UTC))
        app = create_app(dsn=embedded_pg.dsn, clock=clk)
        client = TestClient(app)
        eng = engine_for(embedded_pg.dsn)

    yield E
    E.eng.dispose()


@pytest.mark.contract
@pytest.mark.tc("OP-001")
@pytest.mark.parametrize("op_type", ["scan", "output.plan", "output.apply", "output.undo"])
def test_op_001_long_actions_are_operations(env, sandbox_tree, tmp_path, op_type):
    for i in range(3):
        shutil.copyfile(FX / f"IMG_{i + 1:04d}.jpg", sandbox_tree.photos / f"{i}.jpg")
    w = WorkerAgent(env.client, roots=sandbox_tree.roots, home=tmp_path / "w")
    w.register()
    src = env.client.post("/api/v1/sources", json={"root_alias": "photos", "sub_path": ""}).json()
    j = env.client.post(
        "/api/v1/jobs",
        json={
            "kind": "organize",
            "source_id": src["id"],
            "preset": "by-date",
            "target": {"root_alias": "sorted", "sub_path": ""},
        },
    ).json()
    w.run_until_idle()
    plan_id = env.client.get(f"/api/v1/jobs/{j['id']}").json()["plan_id"]
    env.client.post(f"/api/v1/output-plans/{plan_id}/approve", json={})
    w.run_until_idle()
    env.client.post(f"/api/v1/output-plans/{plan_id}/undo")
    w.run_until_idle()
    ops = env.client.get("/api/v1/operations?status=recent").json()
    mine = [o for o in ops if o["type"] == op_type]
    assert mine, [o["type"] for o in ops]
    with env.eng.begin() as c:
        kinds = [
            r[0]
            for r in c.execute(select(operation_event.c.kind).where(operation_event.c.operation_id == mine[0]["id"]))
        ]
    assert kinds[0] == "operation.created"
    assert mine[0]["status"] == "succeeded"


@pytest.mark.contract
@pytest.mark.tc("OP-001")
def test_op_001_label_job_operation(env):
    with env.eng.begin() as c:
        c.execute(insert(job).values(id="L1", workspace_id="default", kind="label", status="queued", params={}))
        ensure_operation(c, env.clk, "L1", "job.label", {"kind": "job", "id": "L1"})
    assert env.client.get("/api/v1/operations/L1").json()["type"] == "job.label"


@pytest.mark.integration
@pytest.mark.tc("OP-005")
def test_op_005_persistence_throttle(env):
    with env.eng.begin() as c:
        ensure_operation(c, env.clk, "op", "scan")
        set_op_status(c, env.clk, "op", "running")
    w = OperationWriter(env.eng, env.clk)
    writes = 0
    for i in range(100):  # 10 s of 100 ms progress ticks
        writes += w.progress("op", fraction=i / 1000)
        env.clk.advance(0.1)
    assert writes <= 6
    w.status("op", "pausing")  # status changes are written immediately
    with env.eng.begin() as c:
        assert c.execute(select(operation.c.status).where(operation.c.id == "op")).scalar_one() == "pausing"
        n_progress = c.execute(
            select(func.count())
            .select_from(operation_event)
            .where(operation_event.c.operation_id == "op", operation_event.c.kind == "progress.sample")
        ).scalar_one()
    assert n_progress <= 1  # one sample per minute, never every tick


@pytest.mark.integration
@pytest.mark.tc("OP-007")
def test_op_007_retention(env):
    with env.eng.begin() as c:
        for oid, st in (("ok", "succeeded"), ("bad", "failed")):
            ensure_operation(c, env.clk, oid, "scan")
            set_op_status(c, env.clk, oid, "running")
            set_op_status(c, env.clk, oid, st)
    env.clk.advance(timedelta(days=31).total_seconds())
    assert env.client.post("/api/v1/operations/sweep").json()["deleted"] == 1
    env.clk.advance(timedelta(days=60).total_seconds())
    assert env.client.post("/api/v1/operations/sweep").json()["deleted"] == 1
    with env.eng.begin() as c:
        assert c.execute(select(func.count()).select_from(operation)).scalar_one() == 0
        for i in range(510):
            ensure_operation(c, env.clk, f"o{i}", "scan")
            set_op_status(c, env.clk, f"o{i}", "running")
            set_op_status(c, env.clk, f"o{i}", "succeeded")
    env.client.post("/api/v1/operations/sweep")
    with env.eng.begin() as c:
        assert c.execute(select(func.count()).select_from(operation)).scalar_one() == 500


@pytest.mark.integration
@pytest.mark.tc("OP-008")
def test_op_008_sse_replay_and_reset(env):
    with env.eng.begin() as c:
        ensure_operation(c, env.clk, "op", "scan")
        set_op_status(c, env.clk, "op", "running")
    first = env.client.get("/api/v1/events?once=1").text
    last_id = [ln.split(": ")[1] for ln in first.splitlines() if ln.startswith("id: ")][-1]
    env.clk.advance(30)
    with env.eng.begin() as c:
        set_op_status(c, env.clk, "op", "succeeded")
    replay = env.client.get("/api/v1/events?once=1", headers={"Last-Event-ID": last_id}).text
    assert "operation.status" in replay and f"id: {last_id}\n" not in replay
    env.clk.advance(11 * 60)
    stale = env.client.get("/api/v1/events?once=1", headers={"Last-Event-ID": last_id}).text
    assert "event: reset" in stale  # beyond the 10 min outbox: client refetches GET /operations?status=active


@pytest.mark.integration
@pytest.mark.tc("OP-010")
def test_op_010_push_throttle(env):
    t = ProgressThrottle(env.clk)
    sent = 0
    for i in range(100):
        sent += t.should_send("op", fraction=i * 0.001, visible=True)
        env.clk.advance(0.1)
    assert 9 <= sent <= 11  # <= 1 Hz
    assert t.should_send("op", fraction=0.2, visible=True)  # >= 1 % jump goes out immediately
    hidden = 0
    for i in range(100):
        hidden += t.should_send("op2", fraction=i * 0.001, visible=False)
        env.clk.advance(0.1)
    assert hidden <= 3  # 0.2 Hz while the page is hidden
    assert t.should_send_status("op2") is True


@pytest.mark.integration
@pytest.mark.tc("OP-011")
def test_op_011_control_runs_preflight(env):
    with env.eng.begin() as c:
        ensure_operation(c, env.clk, "apply1", "output.apply")
        set_op_status(c, env.clk, "apply1", "running")
        set_op_status(c, env.clk, "apply1", "waiting", "DISK_INSUFFICIENT_OUTPUT")
        c.execute(
            operation.update()
            .where(operation.c.id == "apply1")
            .values(progress={"required_bytes": 5_000_000_000, "target_alias": "sorted", "worker_id": "w1"})
        )
    env.client.post("/worker-api/v1/register", json={"worker_id": "w1", "roots": [{"alias": "sorted", "mode": "rw"}]})
    env.client.post(
        "/worker-api/v1/heartbeat", json={"worker_id": "w1", "lease_ids": [], "disk_free": {"sorted": 10**6}}
    )
    r = env.client.post("/api/v1/operations/apply1/resume")
    assert r.status_code == 409
    assert r.json()["blockers"][0]["code"] == "DISK_INSUFFICIENT_OUTPUT"
    env.client.post(
        "/worker-api/v1/heartbeat", json={"worker_id": "w1", "lease_ids": [], "disk_free": {"sorted": 10**11}}
    )
    assert env.client.post("/api/v1/operations/apply1/resume").status_code == 200


@pytest.mark.integration
@pytest.mark.tc("OP-015")
def test_op_015_completion_writes_observations(env):
    with env.eng.begin() as c:
        ensure_operation(c, env.clk, "j", "job.label")
        set_op_status(c, env.clk, "j", "running")
        complete_operation(
            c,
            env.clk,
            "j",
            worker_id="w1",
            variant="qwen3-vl-8b@q4",
            items=1000,
            seconds=400.0,
            predicted_p50=380.0,
            predicted_p10=300.0,
            predicted_p90=460.0,
        )
        obs = c.execute(select(perf_observation)).first()
        cal = c.execute(select(eta_calibration)).first()
    assert obs.items_per_s == pytest.approx(2.5) and obs.variant == "qwen3-vl-8b@q4"
    assert cal.ratio == pytest.approx(400 / 380) and cal.covered is True


@pytest.mark.integration
@pytest.mark.tc("OP-016")
def test_op_016_reorder_same_lane_only(env):
    with env.eng.begin() as c:
        for oid, lane in (("a", "gpu"), ("b", "gpu"), ("c", "download")):
            ensure_operation(c, env.clk, oid, "job.label")
            c.execute(operation.update().where(operation.c.id == oid).values(worker_id="w1", progress={"lane": lane}))
    ok = env.client.post(
        "/api/v1/operations/reorder", json={"worker_id": "w1", "lane": "gpu", "ordered_ids": ["b", "a"]}
    )
    assert ok.status_code == 200 and ok.json()["order"] == ["b", "a"]
    bad = env.client.post(
        "/api/v1/operations/reorder", json={"worker_id": "w1", "lane": "gpu", "ordered_ids": ["c", "a"]}
    )
    assert bad.status_code == 400


@pytest.mark.integration
@pytest.mark.tc("ETA-018")
def test_eta_018_estimate_before_start(env):
    with env.eng.begin() as c:
        c.execute(
            insert(asset),
            [
                {"id": f"a{i}", "workspace_id": "default", "content_hash": f"h{i}", "size_bytes": 1}
                for i in range(10_234)
            ],
        )
        c.execute(
            insert(result),
            [
                {
                    "id": f"r{i}",
                    "asset_id": f"a{i}",
                    "task_config_hash": "CFG",
                    "model_fingerprint": "FP",
                    "task_type": "classify",
                }
                for i in range(3_150)
            ],
        )
    r = env.client.post(
        "/api/v1/estimates",
        json={
            "action": "job.start",
            "params": {
                "kind": "label",
                "asset_ids": [f"a{i}" for i in range(10_234)],
                "unsupported": 312,
                "task_config_hash": "CFG",
                "fingerprint": "FP",
                "download_bytes": 10_600_000_000,
            },
        },
    ).json()
    assert r["items"] == {"total": 10_234 + 312, "cached": 3_150, "to_process": 7_084, "skipped_unsupported": 312}
    assert [b["phase"] for b in r["breakdown"]] == ["model_download", "model_load", "embed", "infer", "postprocess"]
    dl = r["breakdown"][0]
    assert dl["basis"] == "none" and dl["hint"]["zh-TW"] == "100 Mbps 約 15 分鐘 · 1 Gbps 約 1.5 分鐘"


@pytest.mark.integration
@pytest.mark.tc("DESK-020")
def test_desk_020_tray_pauses_and_resumes_every_heavy_operation(env):
    with env.eng.begin() as c:
        for oid, typ, status in [
            ("S1", "scan", "running"),
            ("T1", "thumbnail", "queued"),
            ("D1", "model.download", "running"),  # not heavy: downloads keep going (06 §5)
            ("X1", "scan", "succeeded"),
        ]:
            ensure_operation(c, env.clk, oid, typ)
            if status != "queued":
                set_op_status(c, env.clk, oid, "running")
            if status == "succeeded":
                set_op_status(c, env.clk, oid, "succeeded")
    r = env.client.post("/api/v1/operations/pause-all")
    assert r.status_code == 200 and sorted(r.json()["paused"]) == ["S1", "T1"]
    with env.eng.begin() as c:
        rows = {o.id: (o.status, o.waiting_reason) for o in c.execute(select(operation)).all()}
    assert rows["S1"] == ("paused", "PAUSED_BY_USER") and rows["T1"] == ("paused", "PAUSED_BY_USER")
    assert rows["D1"][0] == "running" and rows["X1"][0] == "succeeded"
    r = env.client.post("/api/v1/operations/resume-all")
    assert sorted(r.json()["resumed"]) == ["S1", "T1"]
    with env.eng.begin() as c:
        assert {o.id: o.status for o in c.execute(select(operation)).all()}["S1"] == "running"


def _task(env, tid, kind="download"):
    with env.eng.begin() as c:
        c.execute(
            text("insert into worker_task(id, kind, payload, status, worker_id) values (:i, :k, '{}', 'leased', 'w1')"),
            {"i": tid, "k": kind},
        )
        ensure_operation(c, env.clk, tid, "model.download")
        set_op_status(c, env.clk, tid, "running")


def _report(env, tid, done, total=1000, unit="bytes"):
    env.client.post(
        f"/worker-api/v1/tasks/{tid}/progress", json={"progress": {"done": done, "total": total, "unit": unit}}
    )


@pytest.mark.integration
@pytest.mark.tc("ETA-022")
def test_eta_022_a_running_task_shows_its_remaining_time(env):
    """回歸 (invariant 9): nothing ever wrote operation.eta, so every operation ran without an ETA."""
    _task(env, "t1")
    for i in range(11):
        _report(env, "t1", i * 20)
        env.clk.advance(2)
    op = env.client.get("/api/v1/operations/t1").json()
    assert op["eta"]["state"] in ("rough", "precise")
    assert op["eta"]["p50_s"] == pytest.approx(78, rel=0.05)
    assert not [k for k in op["eta"] if k.startswith("_")]  # working state stays on the server


@pytest.mark.integration
@pytest.mark.tc("ETA-022")
def test_eta_022_a_labelling_job_reports_progress_and_eta(env):
    """回歸: labelling results never reached the Operation: its bar stayed at 0 until it finished."""
    with env.eng.begin() as c:
        c.execute(insert(job).values(id="j1", workspace_id="default", kind="label", status="running", params={}))
        ensure_operation(c, env.clk, "j1", "job.label")
        set_op_status(c, env.clk, "j1", "running")
        c.execute(
            insert(asset),
            [{"id": f"a{i}", "workspace_id": "default", "content_hash": f"h{i}", "size_bytes": 1} for i in range(10)],
        )
        c.execute(
            insert(job_item),
            [
                {
                    "id": f"i{i}",
                    "job_id": "j1",
                    "asset_id": f"a{i}",
                    "root_alias": "photos",
                    "rel": f"{i}.jpg",
                    "status": "leased",
                }
                for i in range(10)
            ],
        )
    env.client.post("/worker-api/v1/register", json={"id": "w1"})
    items = [{"job_item_id": f"i{i}", "status": "ok", "output": {}} for i in range(4)]
    assert env.client.post("/worker-api/v1/results", json={"worker_id": "w1", "items": items}).status_code == 200
    op = env.client.get("/api/v1/operations/j1").json()
    assert op["progress"]["done"] == 4 and op["progress"]["total"] == 10
    assert op["progress"]["fraction"] == pytest.approx(0.4)


@pytest.mark.integration
@pytest.mark.tc("ETA-023")
def test_eta_023_success_records_what_was_predicted(env):
    """回歸: complete_operation had no caller outside tests, so eta_calibration stayed empty and V7 had no data."""
    _task(env, "t2")
    for i in range(11):
        _report(env, "t2", i * 50)
        env.clk.advance(2)
    env.clk.advance(18)
    env.client.post("/worker-api/v1/tasks/t2/complete", json={"ok": True, "result": {}})
    with env.eng.begin() as c:
        cal = c.execute(select(eta_calibration)).all()
        obs = c.execute(select(perf_observation)).all()
    assert len(cal) == 1 and cal[0].op_type == "model.download"
    assert cal[0].actual_s == pytest.approx(40)
    assert cal[0].predicted_p50 == pytest.approx(40, rel=0.1)
    assert cal[0].covered is True
    assert obs[0].bytes_per_s == pytest.approx(500 / 40, rel=0.1) and obs[0].items_per_s is None


@pytest.mark.integration
@pytest.mark.tc("ETA-023")
def test_eta_023_a_paused_stretch_is_left_out(env):
    _task(env, "t3")
    for i in range(6):
        _report(env, "t3", i * 100)
        env.clk.advance(2)
    with env.eng.begin() as c:
        set_op_status(c, env.clk, "t3", "pausing")
        set_op_status(c, env.clk, "t3", "paused", "PAUSED_BY_USER")
    env.clk.advance(1000)
    with env.eng.begin() as c:
        set_op_status(c, env.clk, "t3", "running")
    _report(env, "t3", 600)
    env.clk.advance(2)
    env.client.post("/worker-api/v1/tasks/t3/complete", json={"ok": True, "result": {}})
    with env.eng.begin() as c:
        assert c.execute(select(eta_calibration.c.actual_s)).scalar() == pytest.approx(14)


@pytest.mark.integration
@pytest.mark.tc("ETA-023")
def test_eta_023_a_failure_is_not_a_calibration_sample(env):
    _task(env, "t4")
    for i in range(6):
        _report(env, "t4", i * 100)
        env.clk.advance(2)
    env.client.post("/worker-api/v1/tasks/t4/complete", json={"ok": False, "result": {"error": "x"}})
    with env.eng.begin() as c:
        assert c.execute(select(func.count()).select_from(eta_calibration)).scalar() == 0


@pytest.mark.integration
@pytest.mark.tc("ETA-024")
def test_eta_024_export_calibration_rows(env, embedded_pg):
    """The input M0-V7 reads; the command existed only in the experiment's README."""
    from opagent_api.operations import export_calibration

    _task(env, "t5")
    for i in range(11):
        _report(env, "t5", i * 50)
        env.clk.advance(2)
    env.client.post("/worker-api/v1/tasks/t5/complete", json={"ok": True, "result": {}})
    with env.eng.begin() as c:
        rows = export_calibration(c)
    assert len(rows) == 1
    assert set(rows[0]) == {"op_type", "predicted_p10_s", "predicted_p50_s", "predicted_p90_s", "actual_s"}
    assert rows[0]["op_type"] == "model.download"


@pytest.mark.integration
@pytest.mark.tc("ETA-024")
def test_eta_024_the_command_prints_json(env, embedded_pg, capsys):
    import json

    from opagent_cli.main import main

    _task(env, "t6")
    for i in range(11):
        _report(env, "t6", i * 50)
        env.clk.advance(2)
    env.client.post("/worker-api/v1/tasks/t6/complete", json={"ok": True, "result": {}})
    main(["ops", "export-calibration", "--dsn", embedded_pg.dsn])
    assert json.loads(capsys.readouterr().out)[0]["op_type"] == "model.download"


def _by_date_job(env, sandbox_tree, tmp_path, n=3):
    for i in range(n):
        shutil.copyfile(FX / f"IMG_{i + 1:04d}.jpg", sandbox_tree.photos / f"{i}.jpg")
    w = WorkerAgent(env.client, roots=sandbox_tree.roots, home=tmp_path / "w")
    w.register()
    src = env.client.post("/api/v1/sources", json={"root_alias": "photos", "sub_path": ""}).json()
    body = {"kind": "organize", "source_id": src["id"], "target": {"root_alias": "sorted", "sub_path": ""}}
    return w, body


@pytest.mark.integration
@pytest.mark.tc("ETA-027")
def test_eta_027_a_scan_reports_its_progress(env, sandbox_tree, tmp_path):
    """回歸 (invariant 9, seen on the reference machine): a scan showed no count at all while it ran."""
    w, body = _by_date_job(env, sandbox_tree, tmp_path)
    assert env.client.post("/api/v1/jobs", json={**body, "preset": "by-date"}).status_code == 201
    w.run_until_idle()
    op = next(o for o in env.client.get("/api/v1/operations?status=recent").json() if o["type"] == "scan")
    assert op["progress"]["done"] == 3 and op["progress"]["total"] == 3


@pytest.mark.integration
@pytest.mark.tc("ORG-011")
def test_org_011_an_unknown_preset_is_refused_up_front(env, sandbox_tree, tmp_path):
    """回歸 (seen on the reference machine): 201, then a 500 when the scan ended; the job ran forever, could not be
    cancelled, and blocked every later job on that folder with JOB_CONFLICT_RUNNING."""
    _w, body = _by_date_job(env, sandbox_tree, tmp_path)
    r = env.client.post("/api/v1/jobs", json={**body, "preset": "no-such-preset"})
    assert r.status_code == 404
    assert r.json()["code"] == "PRESET_NOT_FOUND"
    with env.eng.begin() as c:
        assert c.execute(select(func.count()).select_from(job)).scalar() == 0


@pytest.mark.integration
@pytest.mark.tc("ORG-011")
def test_org_011_a_preset_gone_during_the_scan_fails_the_job(env, sandbox_tree, tmp_path):
    """Imported presets can be deleted while a job scans: it fails, it does not hang."""
    w, body = _by_date_job(env, sandbox_tree, tmp_path)
    jid = env.client.post("/api/v1/jobs", json={**body, "preset": "by-date"}).json()["id"]
    with env.eng.begin() as c:
        c.execute(text('update job set params = params || \'{"preset": "gone"}\' where id = :i'), {"i": jid})
    w.run_until_idle()
    j = env.client.get(f"/api/v1/jobs/{jid}").json()
    assert j["status"] == "failed"
    assert env.client.get(f"/api/v1/operations/{jid}").json()["status"] == "failed"


@pytest.mark.contract
@pytest.mark.tc("OP-025")
def test_op_025_an_organize_says_whether_it_uses_ai(env, sandbox_tree, tmp_path):
    """回歸 (audit round 72): 依日期（不使用 AI） showed 「AI 分析 · 準備中：載入 AI 模型…」 in the progress card: every
    organize is a job.label Operation, and nothing told the card that this one asks no model."""
    WorkerAgent(env.client, roots=sandbox_tree.roots, home=tmp_path / "w").register()
    src = env.client.post("/api/v1/sources", json={"root_alias": "photos", "sub_path": ""}).json()
    j = env.client.post(
        "/api/v1/jobs",
        json={
            "kind": "organize",
            "source_id": src["id"],
            "preset": "by-date",
            "target": {"root_alias": "sorted", "sub_path": ""},
        },
    ).json()
    assert "id" in j, j
    ops = env.client.get("/api/v1/operations?status=all&type=job.label").json()
    op = next(o for o in ops if o["id"] == j["id"])
    assert op["subject"]["uses_ai"] is False  # an analysis, or no flag at all, is AI (the card's default)
