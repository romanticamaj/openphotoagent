"""PF-001..066 (09a §8.2) with the shared PF-ASSERT checks (09a §8.1)."""

import json
import shutil
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from opagent_api.app import create_app
from opagent_db.schema import engine_for
from opagent_doctor.probes import load_profile
from opagent_registry.loader import BUILTIN_DIR
from opagent_testing.clock import FakeClock
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[4]
GOLDEN = yaml.safe_load((ROOT / "fixtures/golden/preflight_catalog.yaml").read_text())["rows"]
I18N = {lang: json.loads((ROOT / f"packages/i18n/errors.{lang}.json").read_text()) for lang in ("zh-TW", "en")}
EMB8 = "siglip2-so400m-384@onnx-int8"
EMB16 = "siglip2-so400m-384@onnx-fp16"
Q5, Q4, Q8 = "qwen3-vl-8b-instruct@q5_k_m", "qwen3-vl-8b-instruct@q4_k_m", "qwen3-vl-8b-instruct@q8_0"


class Env:
    def __init__(self, dsn, roots, tmp):
        reg = tmp / "registry"
        shutil.copytree(BUILTIN_DIR, reg)
        shutil.copy(ROOT / "fixtures/registry/models/fixture-nc-vlm.yaml", reg / "models")
        pins = []
        for m in (
            "siglip2-so400m-384",
            "qwen3-vl-8b-instruct",
            "qwen3-vl-4b-instruct",
            "qwen3-vl-2b-instruct",
            "fixture-nc-vlm",
        ):
            spec = yaml.safe_load((reg / "models" / f"{m}.yaml").read_text())
            for v in spec["variants"]:
                pins.append(
                    {
                        "model": m,
                        "variant": v["id"],
                        "revision": "a" * 40,
                        "files": [{"path": f["path"], "size": 1_000_000, "sha256": "b" * 64} for f in v["files"]],
                    }
                )
        (reg / "pins.lock").write_text(json.dumps({"schema_version": 1, "pins": pins}))
        self.clock = FakeClock()
        self.client = TestClient(create_app(dsn=dsn, clock=self.clock, registry_dir=reg))
        self.dsn, self.roots = dsn, roots
        self.eng = engine_for(dsn)

    def worker(self, profile="rtx5070ti-win11", **caps):
        from dataclasses import asdict

        body = {
            "worker_id": "w1",
            "name": "desktop",
            "roots": self.roots,
            "models": {},
            "hw": asdict(load_profile(profile)),
        }
        body.update(caps)
        r = self.client.post("/worker-api/v1/register", json=body)
        assert r.status_code == 200, r.text

    def sql(self, q, **p):
        with self.eng.begin() as c:
            return c.execute(text(q), p)

    def install(self, *keys, kind="model", status="installed"):
        for k in keys:
            self.sql(
                "insert into installation(id, worker_id, kind, key, status, size_bytes, files) values (:i,'w1',:k2,:k,:s,1000,'[]')",
                i=f"in-{k}" if kind == "model" else f"pk-{k}",
                k=k,
                k2=kind,
                s=status,
            )

    def source(self, sub=""):
        return self.client.post("/api/v1/sources", json={"root_alias": "photos", "sub_path": sub}).json()["id"]

    def job_params(self, **kw):
        p = {
            "kind": "organize",
            "preset": "by-content-12",
            "source_id": self.source(),
            "target": {"root_alias": "sorted", "sub_path": ""},
        }
        p.update(kw)
        return p

    def preflight(self, action, params):
        r = self.client.post("/api/v1/preflight", json={"action": action, "params": params})
        assert r.status_code == 200, r.text
        return r.json()


@pytest.fixture
def env(embedded_pg, sandbox_tree, tmp_path):
    e = Env(embedded_pg.dsn, sandbox_tree.roots, tmp_path)
    yield e
    e.eng.dispose()


def by_content(e, models):
    return e.job_params(models=models)


# ---- arrangements: each returns (action, params, endpoint|None) ----------------------------------------------------
def a_worker_offline(e):
    e.worker()
    e.clock.advance(91)
    return "job.start", e.job_params(preset="by-date"), ("POST", "/api/v1/jobs")


def a_version(e):
    e.worker(protocol_version="2.0")
    return "job.start", e.job_params(preset="by-date"), ("POST", "/api/v1/jobs")


def a_gpu_not_found(e):
    e.worker("no-gpu-linux")
    e.install(EMB8)
    return "job.start", by_content(e, {"embed": EMB8, "vlm": None}), None


def a_driver(e):
    e.worker("nvidia-drv535")
    e.install(EMB16, Q4)
    return "job.start", by_content(e, {"embed": EMB16, "vlm": Q4, "pack": "llamacpp-cuda13"}), ("POST", "/api/v1/jobs")


def a_arch(e):
    e.worker("blackwell-cuda12pack")
    e.install(EMB16, Q5)
    return "job.start", by_content(e, {"embed": EMB16, "vlm": Q5, "pack": "llamacpp-cuda12"}), ("POST", "/api/v1/jobs")


def a_busy(e):
    e.worker("game-running-5gb")
    e.install(Q5)
    return "model.load", {"variant": Q5}, None


def a_docker(e):
    e.worker("docker-no-passthrough", deployment="docker", gpu_requested=True)
    e.install(EMB8)
    return "job.start", by_content(e, {"embed": EMB8, "vlm": None}), ("POST", "/api/v1/jobs")


def a_vram(e):
    e.worker("m3-16")
    e.install(EMB16, Q8)
    return "job.start", by_content(e, {"embed": EMB16, "vlm": Q8}), ("POST", "/api/v1/jobs")


def a_sequential(e):
    e.worker("rtx4060-laptop")
    e.install(EMB16, Q4)
    e.install("llamacpp-cuda13", kind="pack")
    return "job.start", by_content(e, {"embed": EMB16, "vlm": Q4}), None


def a_ram(e):
    e.worker("m1-8")
    e.install(EMB16, Q8)
    return "job.start", by_content(e, {"embed": EMB16, "vlm": Q8}), ("POST", "/api/v1/jobs")


def a_cpu_slow(e):
    e.worker("no-gpu-linux")
    e.install(EMB8, "qwen3-vl-2b-instruct@q4_k_m")
    return "job.start", by_content(e, {"embed": EMB8, "vlm": "qwen3-vl-2b-instruct@q4_k_m"}), None


def a_pack_missing(e):
    e.worker()
    e.install(EMB16, Q5)
    return "job.start", by_content(e, {"embed": EMB16, "vlm": Q5}), ("POST", "/api/v1/jobs")


def a_pack_broken(e):
    e.worker()
    e.install(EMB16, Q5)
    e.install("llamacpp-cuda13", kind="pack", status="broken")
    return "job.start", by_content(e, {"embed": EMB16, "vlm": Q5}), ("POST", "/api/v1/jobs")


def a_platform(e):
    e.worker("m3-16")
    e.install(EMB16)
    return "job.start", by_content(e, {"embed": EMB16, "vlm": "fixture-nc-vlm@q4"}), ("POST", "/api/v1/jobs")


def doctor(code, check, status="fail", affects=("job.start",), **params):
    return {"id": check, "status": status, "code": code, "params": params, "affects": list(affects)}


def a_doctor(code, check, action="job.start", status="fail", profile="rtx5070ti-win11", **params):
    def arrange(e):
        e.worker(profile, doctor=[doctor(code, check, status, (action,), **params)])
        if action == "job.start":
            e.install(EMB16)
            return (  # doctor findings for job.start concern model jobs (09a PF-017/019/020: "job.start（embed…）")
                action,
                by_content(e, {"embed": EMB16, "vlm": None}),
                ("POST", "/api/v1/jobs") if status == "fail" and code not in WARN else None,
            )
        if action == "scan":
            return action, {"source_id": e.source()}, None
        return action, {"variant": EMB8} if action == "model.download" else {}, None

    return arrange


def a_crashed(e):
    e.worker()
    e.install(EMB16, Q5)
    e.install("llamacpp-cuda13", kind="pack")
    for _ in range(3):
        e.sql(
            "insert into event(kind, payload, at) values ('runtime.crashed', :p, :t)",
            p=json.dumps({"worker_id": "w1", "variant": Q5}),
            t=e.clock.now(),
        )
    return "job.start", by_content(e, {"embed": EMB16, "vlm": Q5}), ("POST", "/api/v1/jobs")


def prev_job_failures(code):
    def arrange(e):
        e.worker()
        e.sql(
            "insert into job(id, workspace_id, kind, status, params) values ('jprev','default','organize','completed','{}')"
        )
        e.sql("insert into asset(id, workspace_id, content_hash, size_bytes) values ('a1','default','h1',1)")
        e.sql(
            "insert into job_item(id, job_id, asset_id, root_alias, rel, task, seq, status, error_code) values ('ji1','jprev','a1','photos','x.jpg','classify',0,'failed',:c)",
            c=code,
        )
        return "job.retry_failed", {"job_id": "jprev"}, None

    return arrange


def a_decode_failed(e):
    e.worker()
    src = e.source()
    e.sql(
        "insert into worker_task(id, kind, payload, status, result, worker_id) values ('t-scan','scan',:p,'done',:r,'w1')",
        p=json.dumps({"source_id": src}),
        r=json.dumps({"decode_failed": 7}),
    )
    return (
        "job.start",
        {"kind": "organize", "preset": "by-date", "source_id": src, "target": {"root_alias": "sorted", "sub_path": ""}},
        None,
    )


def a_disk_models(e):
    e.worker(disk_free={"models": 500_000})
    return "model.download", {"variant": Q5}, ("POST", "/api/v1/workers/w1/installs")


def a_disk_thumbs(free=10_000, pct=False):
    def arrange(e2):
        e2.worker(disk_free={"thumbs": free if not pct else 1_200_000_000, "thumbs_total": 10_000_000_000})
        src = e2.source()
        e2.sql(
            "insert into worker_task(id, kind, payload, status, result, worker_id) values ('t-s','scan',:p,'done',:r,'w1')",
            p=json.dumps({"source_id": src}),
            r=json.dumps({"files": 5000}),
        )
        return "scan", {"source_id": src}, None

    return arrange


def a_root(status):
    def arrange(e):
        e.worker(roots_status={"photos": status})
        return "scan", {"source_id": e.source()}, None

    return arrange


def a_root_not_allowed(e):
    e.worker()
    return "scan", {"root_alias": "nas", "sub_path": ""}, None


def a_not_writable(e):
    roots = [dict(r, mode="ro") if r["alias"] == "sorted" else r for r in e.roots]
    e.roots = roots
    e.worker()
    return "output.apply", {"target": {"root_alias": "sorted"}}, None


def last_download_failed(code, variant=Q5):
    def arrange(e):
        e.worker()
        e.sql(
            "insert into operation(id, type, status, subject, seq, updated_at, heartbeat_at, result) values ('opd','model.download','failed',:s,1,:t,:t,:r)",
            s=json.dumps({"kind": "variant", "id": variant}),
            t=e.clock.now(),
            r=json.dumps({"error_code": code}),
        )
        return (
            "model.download",
            {"variant": variant},
            ("POST", "/api/v1/workers/w1/installs") if code not in WARN else None,
        )

    return arrange


def a_license(e):
    e.worker("rtx5070ti-win11")
    return "model.download", {"variant": "fixture-nc-vlm@q4"}, ("POST", "/api/v1/workers/w1/installs")


def a_not_installed(e):
    e.worker()
    e.install(EMB16, Q4)
    e.install("llamacpp-cuda13", kind="pack")
    return "job.start", by_content(e, {"embed": EMB16, "vlm": Q5}), ("POST", "/api/v1/jobs")


def a_downloading(e):
    e.worker()
    e.install(EMB16)
    e.install("llamacpp-cuda13", kind="pack")
    e.sql(
        "insert into operation(id, type, status, subject, seq, updated_at, heartbeat_at, progress) values ('opx','model.download','running',:s,1,:t,:t,:p)",
        s=json.dumps({"kind": "variant", "id": Q5}),
        t=e.clock.now(),
        p=json.dumps({"done": 72, "total": 100}),
    )
    return "job.start", by_content(e, {"embed": EMB16, "vlm": Q5}), ("POST", "/api/v1/jobs")


def a_install_status(status):
    def arrange(e):
        e.worker()
        e.install(Q5, status=status)
        e.install("llamacpp-cuda13", kind="pack")
        return "model.load", {"variant": Q5}, None

    return arrange


def a_load_failed(e):
    e.worker()
    e.install(Q5)
    e.install("llamacpp-cuda13", kind="pack")
    e.sql(
        "insert into event(kind, payload, at) values ('model.load_failed', :p, :t)",
        p=json.dumps({"worker_id": "w1", "variant": Q5, "reason": "bad gguf"}),
        t=e.clock.now(),
    )
    return "model.load", {"variant": Q5}, None


def a_capability(e):
    e.worker()
    e.install(EMB16)
    return "job.start", by_content(e, {"embed": EMB16, "vlm": EMB16}), ("POST", "/api/v1/jobs")


def a_in_use(e):
    e.worker()
    e.install(Q5)
    e.sql(
        "insert into job(id, workspace_id, kind, status, params) values ('jr','default','organize','running',:p)",
        p=json.dumps({"models": [Q5]}),
    )
    return "model.delete", {"installation_id": f"in-{Q5}"}, ("DELETE", f"/api/v1/workers/w1/installations/in-{Q5}")


def a_nothing(e):
    e.worker()
    return "job.start", {"kind": "label", "rerun_of": "none", "task_config": {}, "fingerprint": "x"}, None


def a_conflict(e):
    e.worker()
    p = e.job_params(preset="by-date")
    e.client.post("/api/v1/jobs", json=p)
    return "job.start", p, ("POST", "/api/v1/jobs")


def a_plan(field, action):
    def arrange(e):
        e.worker()
        e.sql(
            "insert into job(id, workspace_id, kind, status, params) values ('jp','default','organize','awaiting_approval','{}')"
        )
        e.sql(
            "insert into output_plan(id, job_id, status, summary) values ('pl','jp','draft',:s)",
            s=json.dumps({field: 3 if field == "stale" else 2}),
        )
        return action, {"plan_id": "pl"}, None

    return arrange


def a_target_inside(e):
    e.worker()
    return (
        "job.start",
        {
            "kind": "organize",
            "preset": "by-date",
            "source_id": e.source(),
            "target": {"root_alias": "photos", "sub_path": "sorted"},
        },
        ("POST", "/api/v1/jobs"),
    )


def a_locked(e):
    e.worker()
    e.sql("insert into job(id, workspace_id, kind, status, params) values ('ja','default','organize','completed','{}')")
    e.sql("insert into output_plan(id, job_id, status, summary) values ('pa','ja','partially_applied','{}')")
    e.sql("insert into asset(id, workspace_id, content_hash, size_bytes) values ('a9','default','h9',1)")
    e.sql(
        "insert into output_action(id, plan_id, seq, asset_id, action, src_alias, src_rel, dst_alias, dst_rel, src_hash, status, error) values ('oa','pa',0,'a9','copy','photos','x','sorted','y','h9','failed','FS_FILE_LOCKED')"
    )
    return "output.apply", {"plan_id": "pa", "target": {"root_alias": "sorted"}}, None


def a_pack_index(code):
    def arrange(e):
        e.worker(packs_status={"llamacpp-cuda13": {"error": code, "pack_api": 9}})
        return "runtime.install", {"pack": "llamacpp-cuda13"}, None

    return arrange


def a_offload(e):
    e.worker("rtx4060-laptop")
    e.install(EMB16, Q5)
    e.install("llamacpp-cuda13", kind="pack")
    return "job.start", by_content(e, {"embed": EMB16, "vlm": Q5}), None


WARN = {
    "GPU_NOT_FOUND",
    "GPU_BUSY_EXTERNAL",
    "VRAM_SEQUENTIAL",
    "PERF_CPU_ONLY_SLOW",
    "RUNTIME_TIMEOUT",
    "ORT_EP_UNAVAILABLE",
    "DECODE_HEIC_MISSING",
    "DECODE_RAW_MISSING",
    "DECODE_FAILED",
    "FFMPEG_MISSING",
    "DISK_LOW_THUMBS",
    "MODEL_OOM",
    "DL_RATE_LIMITED",
    "DL_UNVERIFIED_SOURCE",
    "JOB_NOTHING_TO_DO",
    "SEARCH_INDEX_INCOMPLETE",
    "SEARCH_MODEL_MISMATCH",
    "CHECK_CRASHED",
    "FS_FILE_LOCKED",
    "DL_REMOTE_CHANGED",
    "VRAM_OFFLOAD",
}

ARRANGE = {
    "PF-001": a_worker_offline,
    "PF-002": a_version,
    "PF-003": a_gpu_not_found,
    "PF-004": a_driver,
    "PF-005": a_arch,
    "PF-006": a_busy,
    "PF-007": a_docker,
    "PF-008": a_vram,
    "PF-009": a_sequential,
    "PF-010": a_ram,
    "PF-011": a_cpu_slow,
    "PF-012": a_pack_missing,
    "PF-013": a_pack_broken,
    "PF-014": a_platform,
    "PF-015": a_crashed,
    "PF-016": prev_job_failures("RUNTIME_TIMEOUT"),
    "PF-017": a_doctor("ORT_EP_UNAVAILABLE", "ort.ep", profile="linux-rtx3090", ep="CUDA"),
    "PF-019": a_doctor("OLLAMA_NOT_RUNNING", "runtime.ollama", url="http://127.0.0.1:11434"),
    "PF-020": a_doctor("EXTERNAL_ENDPOINT_UNREACHABLE", "runtime.endpoint", url="http://192.168.1.20:1234"),
    "PF-021": a_doctor("DECODE_HEIC_MISSING", "decode.heic", action="scan", n=12),
    "PF-022": a_doctor("DECODE_RAW_MISSING", "decode.raw", action="scan", n=120),
    "PF-023": a_decode_failed,
    "PF-024": a_doctor("FFMPEG_MISSING", "decode.video", action="scan", n=3),
    "PF-025": a_disk_models,
    "PF-026": a_disk_thumbs(),
    "PF-027": a_disk_thumbs(pct=True),
    "PF-028": a_root("denied"),
    "PF-029": a_root_not_allowed,
    "PF-030": a_root("unavailable"),
    "PF-031": a_not_writable,
    "PF-032": last_download_failed("NET_UNREACHABLE"),
    "PF-033": last_download_failed("NET_TLS_INTERCEPTED"),
    "PF-034": last_download_failed("HF_AUTH_REQUIRED"),
    "PF-035": last_download_failed("HF_GATED_ACCESS_PENDING"),
    "PF-036": a_license,
    "PF-037": a_not_installed,
    "PF-038": a_downloading,
    "PF-039": a_install_status("files_missing"),
    "PF-040": a_install_status("corrupt"),
    "PF-041": a_load_failed,
    "PF-042": prev_job_failures("MODEL_OOM"),
    "PF-043": a_capability,
    "PF-044": a_in_use,
    "PF-045": last_download_failed("DL_CHECKSUM_MISMATCH"),
    "PF-046": last_download_failed("DL_RATE_LIMITED"),
    "PF-047": last_download_failed("DL_NOT_FOUND"),
    "PF-048": last_download_failed("DL_UNVERIFIED_SOURCE"),
    "PF-049": a_doctor("NET_PORT_IN_USE", "net.port", action="app.start", port=52731, process="other.exe"),
    "PF-050": a_doctor("TIME_SKEW", "sys.clock", action="model.download", s=360),
    "PF-051": a_nothing,
    "PF-052": a_conflict,
    "PF-055": a_plan("stale", "output.apply"),
    "PF-056": a_target_inside,
    "PF-057": a_plan("undo_conflicts", "output.undo"),
    "PF-062": a_doctor("CHECK_CRASHED", "gpu.nvml", status="crashed", name="gpu.nvml"),
    "PF-063": a_pack_index("PACK_SIGNATURE_INVALID"),
    "PF-064": a_pack_index("PACK_INCOMPATIBLE"),
    "PF-065": a_locked,
}
SUBCASES = {
    "PF-066": {
        "DL_REMOTE_CHANGED": last_download_failed("DL_REMOTE_CHANGED"),
        "DL_MIRROR_FAILED": last_download_failed("DL_MIRROR_FAILED"),
        "VRAM_OFFLOAD": a_offload,
    }
}
CASES = [
    pytest.param(r["id"], r["codes"][0], r, marks=pytest.mark.tc(r["id"]), id=f"{r['id']}-{r['codes'][0]}")
    for r in GOLDEN
    if r["version"].startswith("v0.1") and r["id"] in ARRANGE
]
CASES += [
    pytest.param(pid, code, next(r for r in GOLDEN if r["id"] == pid), marks=pytest.mark.tc(pid), id=f"{pid}-{code}")
    for pid, d in SUBCASES.items()
    for code in d
]


def expected_types(row, deployment="desktop", os_="windows"):
    out = []
    for f in row["fixes"]:
        if "one_of" in f:
            pick = next((x for x in f["one_of"] if x.get("deployment") in (deployment, os_)), f["one_of"][-1])
            out.append(pick["type"])
        else:
            out.append(f["type"])
    return out


@pytest.mark.integration
@pytest.mark.parametrize("pid,code,row", CASES)
def test_pf_catalog(env, pid, code, row):
    arrange = SUBCASES[pid][code] if pid in SUBCASES else ARRANGE[pid]
    action, params, endpoint = arrange(env)
    pf = env.preflight(action, params)
    warn = code in WARN
    lst = pf["warnings"] if warn else pf["blockers"]
    hit = [p for p in lst if p["code"] == code]
    assert hit, f"{code} not in {'warnings' if warn else 'blockers'}: {pf}"
    p = hit[0]
    # (2) fix type sequence, first is primary
    if pid != "PF-066":
        dep = "docker" if code == "GPU_DOCKER_NO_PASSTHROUGH" else "desktop"
        os_ = {"PF-017": "linux"}.get(pid, "windows")
        assert [f["type"] for f in p["fixes"]] == expected_types(row, dep, os_)
    assert p["fixes"] and p["fixes"][0]["primary"] is True and all(not f["primary"] for f in p["fixes"][1:])
    # (3) every choose_alternative has a consequence
    assert all(f.get("consequence") for f in p["fixes"] if f["type"] == "choose_alternative")
    # transient → retryable
    if "T" in row["severity"].split("／")[0] and code not in ("DL_MIRROR_FAILED",):
        assert p["retryable"] is True
    # (4) the real endpoint enforces the same preflight
    if endpoint is not None and not warn:
        method, path = endpoint
        body = {"variant": params["variant"]} if path.endswith("/installs") else params
        r = env.client.request(method, path, json=body if method != "DELETE" else None)
        assert r.status_code == 409 and r.headers["content-type"].startswith("application/problem+json")
        assert code in [b["code"] for b in r.json()["blockers"]]
    # (5) i18n
    for lang in ("zh-TW", "en"):
        assert {"title", "message", "cta"} <= set(I18N[lang][code]), (lang, code)
    if not warn:
        assert pf["ready"] is False


@pytest.mark.contract
@pytest.mark.tc("PF-071")
def test_pf_071_catalogue_rules():
    from opagent_doctor.codes import CATALOG, problem

    assert set(CATALOG) == set(I18N["zh-TW"])
    for code in CATALOG:
        for dep in ("desktop", "docker"):
            p = problem(code, {}, deployment=dep)
            assert p["fixes"], code
            assert p["fixes"][0]["primary"] and sum(f["primary"] for f in p["fixes"]) == 1
            assert all(f.get("consequence") for f in p["fixes"] if f["type"] == "choose_alternative"), code
    for r in GOLDEN:  # the product catalogue matches the spec table (09a §8.1, CI consistency)
        if r["id"] == "PF-066":
            continue
        for code in r["codes"]:
            assert [f["type"] for f in problem(code, {})["fixes"]] == expected_types(r), code


@pytest.mark.contract
@pytest.mark.tc("PF-078")
def test_pf_078_i18n_complete_with_same_placeholders():
    import re

    ph = re.compile(r"\{(\w+)\}")
    assert set(I18N["zh-TW"]) == set(I18N["en"])
    for code, zh in I18N["zh-TW"].items():
        en = I18N["en"][code]
        for k in ("title", "message", "cta"):
            assert zh[k] and en[k], (code, k)
        assert set(ph.findall(zh["message"])) == set(ph.findall(en["message"])), code
