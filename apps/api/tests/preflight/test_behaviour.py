import pytest

from .test_catalog import EMB16, Q4, Q5


@pytest.mark.unit
@pytest.mark.tc("PF-070")
def test_pf_070_root_cause_first(env):
    env.worker()
    env.clock.advance(120)
    pf = env.preflight("job.start", env.job_params(models={"embed": EMB16, "vlm": Q5}))
    assert [b["code"] for b in pf["blockers"]] == ["WORKER_OFFLINE"]
    gates = {g["id"]: g for g in pf["gates"]}
    assert gates["model"]["status"] == "waiting" and gates["model"]["depends_on"] == "worker"


@pytest.mark.unit
@pytest.mark.tc("PF-072")
def test_pf_072_merged_download_fix(env):
    env.worker()
    pf = env.preflight("job.start", env.job_params(models={"embed": EMB16, "vlm": Q5}))
    codes = [b["code"] for b in pf["blockers"]]
    assert codes.count("MODEL_NOT_INSTALLED") == 2 and "RUNTIME_PACK_MISSING" in codes
    merged = pf["merged_fix"]
    assert merged["type"] == "download" and merged["count"] == 3
    assert {i["variant"] for i in merged["items"] if "variant" in i} == {EMB16, Q5}
    assert merged["bytes"] > 0 and merged["label_key"] == "fix.prepare_all"


@pytest.mark.integration
@pytest.mark.tc("PF-073")
def test_pf_073_server_side_enforcement(env):
    env.worker()
    params = env.job_params(models={"embed": EMB16, "vlm": Q5})
    pf = env.preflight("job.start", params)
    r = env.client.post("/api/v1/jobs", json=params)
    assert r.status_code == 409 and r.headers["content-type"].startswith("application/problem+json")
    assert [b["code"] for b in r.json()["blockers"]] == [b["code"] for b in pf["blockers"]]


@pytest.mark.unit
@pytest.mark.tc("PF-074")
def test_pf_074_fingerprint_skips_recompute(env):
    env.worker()
    params = env.job_params(preset="by-date")
    a = env.preflight("job.start", params)
    r = env.client.post(
        "/api/v1/preflight", json={"action": "job.start", "params": params, "fingerprint": a["fingerprint"]}
    ).json()
    assert r["cached"] is True and r["fingerprint"] == a["fingerprint"]
    env.clock.advance(120)  # state changed (worker went offline)
    r2 = env.client.post(
        "/api/v1/preflight", json={"action": "job.start", "params": params, "fingerprint": a["fingerprint"]}
    ).json()
    assert r2["cached"] is False and r2["fingerprint"] != a["fingerprint"]


@pytest.mark.unit
@pytest.mark.tc("PF-081")
def test_pf_081_inference_error_mapping():
    from opagent_doctor.codes import CATALOG
    from opagent_inference.types import ERROR_CODE_MAP, InferenceError

    assert ERROR_CODE_MAP["OOM"] == "MODEL_OOM" and ERROR_CODE_MAP["TIMEOUT"] == "RUNTIME_TIMEOUT"
    assert ERROR_CODE_MAP["BAD_INPUT"] == "DECODE_FAILED" and ERROR_CODE_MAP["RUNTIME"] in (
        "RUNTIME_CRASHED",
        "MODEL_LOAD_FAILED",
    )
    assert InferenceError("RUNTIME", True, "load", phase="load").error_code == "MODEL_LOAD_FAILED"
    assert InferenceError("RUNTIME", True).error_code == "RUNTIME_CRASHED"
    assert all(c in CATALOG for c in ERROR_CODE_MAP.values())


@pytest.mark.contract
@pytest.mark.tc("PF-082")
def test_pf_082_every_error_is_problem_json(env):
    env.worker()
    paths = env.client.app.openapi()["paths"]  # every public endpoint (included routers are nested objects)
    hits = 0
    for path, ops in paths.items():
        if not path.startswith("/api/v1"):
            continue
        url = path
        for seg in [s for s in path.split("/") if s.startswith("{")]:
            url = url.replace(seg, "does-not-exist")
        for m in sorted(k.upper() for k in ops):
            if m == "GET" and "{" not in path:
                continue
            r = env.client.request(m, url, content=b"{not json", headers={"content-type": "application/json"})
            if r.status_code >= 400:
                hits += 1
                assert r.headers["content-type"].startswith("application/problem+json"), (
                    m,
                    url,
                    r.status_code,
                    r.text[:200],
                )
                body = r.json()
                assert body["status"] == r.status_code and body["code"]
    assert hits > 20


@pytest.mark.integration
@pytest.mark.tc("PF-083")
@pytest.mark.parametrize(
    "profile,models,alt_key",
    [
        ("nvidia-drv535", {"embed": EMB16, "vlm": Q4, "pack": "llamacpp-cuda13"}, "vulkan"),
        ("blackwell-cuda12pack", {"embed": EMB16, "vlm": Q5, "pack": "llamacpp-cuda12"}, "llamacpp_vulkan"),
    ],
)
def test_pf_083_alternatives_lead_to_ready(env, profile, models, alt_key):
    env.worker(profile)
    env.install(EMB16, models["vlm"])
    env.install("llamacpp-vulkan", kind="pack")
    params = env.job_params(models=models)
    pf = env.preflight("job.start", params)
    fix = next(f for b in pf["blockers"] for f in b["fixes"] if f["id"] == alt_key)
    assert fix["type"] == "choose_alternative" and fix["payload"]["patch"]
    patched = {**params, "models": {**models, **fix["payload"]["patch"]}}
    pf2 = env.preflight("job.start", patched)
    assert pf2["ready"] is True, pf2["blockers"]


@pytest.mark.integration
@pytest.mark.perf
@pytest.mark.tc("PF-075")
def test_pf_075_cached_preflight_p95(env):
    import statistics
    import time

    env.worker()
    params = env.job_params(models={"embed": EMB16, "vlm": Q5})
    first = env.preflight("job.start", params)
    durs = []
    for _ in range(1000):
        t0 = time.perf_counter()
        env.client.post(
            "/api/v1/preflight", json={"action": "job.start", "params": params, "fingerprint": first["fingerprint"]}
        )
        durs.append(time.perf_counter() - t0)
    p95 = statistics.quantiles(durs, n=100)[94]
    assert p95 < 0.050, p95


@pytest.mark.integration
@pytest.mark.tc("PF-076")
def test_pf_076_worker_rpc_gate_timeout(env):
    env.worker()  # registered but its agent loop is not running: the readability RPC never answers
    pf = env.client.post(
        "/api/v1/preflight", json={"action": "scan", "params": {"source_id": env.source(), "probe": True}}
    ).json()
    gate = next(g for g in pf["gates"] if g["id"] == "source_readable")
    assert gate["status"] == "unknown" and gate["after_s"] == 2
    assert [f["type"] for f in gate["fixes"]] == ["retry"]
    assert pf["ready"] is False or pf["blockers"] == []


@pytest.mark.integration
@pytest.mark.tc("PF-084")
def test_pf_084_by_date_is_not_blocked_by_gpu_findings(env, tmp_path):
    """Regression (E2E-A-002): an old NVIDIA driver must not block organizing by date — it needs no model or GPU —
    while a by-content job on the same machine still shows the driver problem with its fixes."""
    from opagent_worker.agent import WorkerAgent

    from .test_catalog import EMB16, Q4

    # a real worker registration: hardware facts plus its doctor findings (each with the actions it affects)
    WorkerAgent(env.client, roots=env.roots, home=tmp_path / "w", worker_id="w1", hw_profile="nvidia-drv535").register()
    env.install(EMB16, Q4)
    date = env.client.post(
        "/api/v1/preflight", json={"action": "job.start", "params": env.job_params(preset="by-date")}
    )
    assert [b["code"] for b in date.json()["blockers"]] == []
    content = env.client.post(
        "/api/v1/preflight",
        json={
            "action": "job.start",
            "params": env.job_params(models={"embed": EMB16, "vlm": Q4, "pack": "llamacpp-cuda13"}),
        },
    )
    assert "GPU_DRIVER_TOO_OLD" in [b["code"] for b in content.json()["blockers"]]


@pytest.mark.contract
@pytest.mark.tc("PF-085")
def test_pf_085_detail_fields_named_like_problem_members():
    """Regression: a detail carrying `status` (PLAN_NOT_APPLIED with the plan's status) crashed the handler (500)."""
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient
    from opagent_api.problems import install_problem_handlers

    app = FastAPI()
    install_problem_handlers(app)

    @app.get("/x")
    def x():
        raise HTTPException(409, detail={"code": "PLAN_NOT_APPLIED", "status": "applying", "title": "t", "type": "y"})

    r = TestClient(app).get("/x")
    assert r.status_code == 409 and r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    assert body["code"] == "PLAN_NOT_APPLIED" and body["status"] == 409
    assert body["params"] == {"status": "applying", "title": "t", "type": "y"}  # what ProblemCard interpolates
