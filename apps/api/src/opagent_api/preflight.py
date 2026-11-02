"""Preflight engine (06 §4): one function per action, reading only cached state (worker heartbeat and reported
hardware/doctor/disk/roots, installations, operations, jobs, plans) and producing structured problems from the
catalogue (``opagent_doctor.codes``). Every action endpoint runs the same engine before acting (PF-073).
"""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from typing import Any

from opagent_core.config_hash import config_hash
from opagent_db.schema import (
    event,
    installation,
    job,
    job_item,
    license_acceptance,
    operation,
    output_action,
    output_plan,
    result,
    source,
    worker,
    worker_task,
)
from opagent_doctor.codes import problem
from sqlalchemy import func, select

PROTOCOL_MAJOR = 1
OFFLINE_AFTER_S = 90
THUMB_BYTES_PER_PHOTO = 60_000  # t256 + t1024 webp, rough average (06 §3.3 disk.thumbs)
ACTIVE_JOBS = ("queued", "running", "paused", "planning", "applying", "cancelling", "awaiting_review")
DOWNLOAD_FAILURE_CODES = {
    "NET_UNREACHABLE",
    "NET_TLS_INTERCEPTED",
    "HF_AUTH_REQUIRED",
    "HF_GATED_ACCESS_PENDING",
    "DL_CHECKSUM_MISMATCH",
    "DL_RATE_LIMITED",
    "DL_NOT_FOUND",
    "DL_UNVERIFIED_SOURCE",
    "DL_REMOTE_CHANGED",
    "DL_MIRROR_FAILED",
}
RPC_TIMEOUT_S = 2.0
COMPAT_WARNINGS = {"VRAM_SEQUENTIAL", "VRAM_OFFLOAD", "PERF_CPU_ONLY_SLOW"}


DOWNLOAD_ACTIVE = ("queued", "running", "waiting", "paused")


class Pre:
    def __init__(self, c: Any, st: Any, action: str, params: dict[str, Any]) -> None:
        self.c, self.st, self.action, self.p = c, st, action, params
        self.blockers: list[dict[str, Any]] = []
        self.warnings: list[dict[str, Any]] = []
        self.gates: list[dict[str, Any]] = []
        self.revalidate: list[str] = ["doctor.changed"]
        self.models: list[dict[str, Any]] = []  # compat of the models this action would use (MDL-005)
        self.w: Any = None
        self.caps: dict[str, Any] = {}
        self.deployment = "desktop"
        self.os = "windows"
        self._reg: Any = None

    # ---- helpers ----
    @property
    def reg(self) -> Any:
        if self._reg is None:
            self._reg = self.st.registry()
        return self._reg

    def add(self, code: str, params: dict[str, Any] | None = None, gate: str = "", **kw: Any) -> None:
        p = problem(code, params or {}, deployment=self.deployment, os=self.os, **kw)
        if code == "TIME_SKEW" and abs(float((params or {}).get("s", 0))) <= 300:
            p["severity"] = "warning"  # > 60 s warns, > 5 min blocks (06 §4.7 #50)
        (self.warnings if p["severity"] == "warning" else self.blockers).append(p)
        if gate:
            self.gates.append({"id": gate, "status": "warn" if p["severity"] == "warning" else "blocked", "code": code})

    def _source(self) -> Any:
        sid = self.p.get("source_id")
        return self.c.execute(select(source).where(source.c.id == sid)).first() if sid else None

    def _worker_row(self) -> Any:
        wid = self.p.get("worker_id")
        if not wid:
            s = self._source()
            wid = s.worker_id if s is not None else None
        q = select(worker)
        q = q.where(worker.c.id == wid) if wid else q.order_by(worker.c.last_heartbeat_at.desc().nulls_last())
        return self.c.execute(q.limit(1)).first()

    def hw(self) -> Any:
        from .routes_models import hw_of

        return hw_of(self.caps)

    def _installs(self, kind: str) -> dict[str, Any]:
        return {
            r.key: r
            for r in self.c.execute(
                select(installation).where(installation.c.worker_id == self.w.id, installation.c.kind == kind)
            )
        }

    # ---- gates ----
    def worker_gate(self) -> bool:
        self.w = self._worker_row()
        if self.w is None:
            self.add("WORKER_OFFLINE", {"name": "?", "duration": "∞"}, "worker")
            return False
        self.caps = dict(self.w.capabilities or {})
        self.deployment = str(self.caps.get("deployment") or "desktop")
        self.os = str((self.caps.get("hw") or {}).get("os") or "windows")
        self.revalidate.append(f"worker.status:{self.w.id}")
        now = self.st.clock.now()
        hb = self.w.last_heartbeat_at
        if hb is not None and hb.tzinfo is None:
            from datetime import UTC

            hb = hb.replace(tzinfo=UTC)
        if hb is None or now - hb > timedelta(seconds=OFFLINE_AFTER_S):
            secs = int((now - hb).total_seconds()) if hb else None
            self.add("WORKER_OFFLINE", {"name": self.w.name, "duration": f"{secs} s" if secs else "?"}, "worker")
            for g in ("source", "model", "runtime", "vram", "disk"):  # root cause first (PF-070)
                self.gates.append({"id": g, "status": "waiting", "depends_on": "worker"})
            return False
        ver = str(self.caps.get("protocol_version") or f"{PROTOCOL_MAJOR}.0")
        if int(ver.split(".")[0]) != PROTOCOL_MAJOR:
            self.add("WORKER_VERSION_MISMATCH", {"w": ver, "s": f"{PROTOCOL_MAJOR}.0"}, "worker")
            return False
        self.gates.append({"id": "worker", "status": "ok"})
        return True

    def doctor_gate(self) -> None:
        # job.start findings come from model/GPU features; a job that uses no model (by date) is unaffected (PF-084)
        no_models = (
            self.action == "job.start" and not self.p.get("models") and self.p.get("preset", "by-date") == "by-date"
        )
        for d in self.caps.get("doctor") or []:
            if self.action not in (d.get("affects") or []) or no_models:
                continue
            if d.get("status") == "crashed":
                self.add("CHECK_CRASHED", {"check": d.get("id")}, "doctor")
            elif d.get("status") in ("fail", "warn") and d.get("code"):
                self.add(d["code"], dict(d.get("params") or {}), "doctor")

    def model_gates(self, models: dict[str, Any]) -> None:
        from opagent_registry.compat import compat, pack_failure

        hw = self.hw()
        if self.caps.get("gpu_requested") and hw.in_container and not hw.gpus:
            self.add("GPU_DOCKER_NO_PASSTHROUGH", {}, "runtime")
            return
        installs = self._installs("model")
        packs = self._installs("pack")
        consents = self._consents()
        if not hw.gpus:
            self.add("GPU_NOT_FOUND", {}, "runtime")
        for role in ("embed", "vlm"):
            key = models.get(role)
            if not key:
                continue
            mid, _, vid = str(key).partition("@")
            m = self.reg.models.get(mid)
            if m is None:
                continue
            need_cap = "embed" if role == "embed" else "classify"
            if need_cap not in m.spec.capabilities or (role == "vlm" and m.spec.kind == "embedding"):
                self.add("MODEL_CAPABILITY_MISMATCH", {"model": key, "task": need_cap}, "model")
                continue
            pack = models.get("pack") if role == "vlm" else None
            if pack and pack in self.reg.runtimes:
                code = pack_failure(self.reg.runtimes[pack], hw, self.reg.gpu_classes)
                if code in ("GPU_DRIVER_TOO_OLD", "GPU_ARCH_UNSUPPORTED"):
                    g = hw.primary_gpu
                    alt = {"patch": {"pack": "llamacpp-vulkan"}}
                    self.add(
                        code,
                        {"v": g.driver if g else None, "gpu": g.name if g else None, "pack": pack},
                        "runtime",
                        vulkan=alt,
                        llamacpp_vulkan=alt,
                        cpu={"patch": {"pack": "llamacpp-cpu"}},
                        llamacpp_cuda13={"pack": "llamacpp-cuda13"},
                    )
                    continue
            cp = compat(
                m,
                vid,
                hw,
                self.reg.runtimes,
                gpu_classes=self.reg.gpu_classes,
                installed=list(installs),
                consents=consents,
                pack=pack,
            )
            for r in cp.reasons:
                if r["code"] == "GPU_NOT_FOUND" and not hw.gpus:
                    continue
                self.add(r["code"], {**r["params"], "model": key}, "vram")
            if cp.level == "blocked":
                continue
            self._prep(key, cp, packs)
            if self._recent_crashes(key) >= 3:
                self.add("RUNTIME_CRASHED", {"n": self._recent_crashes(key)}, "runtime")

    def _prep(self, key: str, cp: Any, packs: dict[str, Any]) -> None:
        for pr in cp.prep:
            code = pr["code"]
            if code == "RUNTIME_PACK_MISSING":
                inst = packs.get(pr["params"]["pack"])
                if inst is None:
                    pk = pr["params"]["pack"]
                    self.add(
                        code,
                        {"pack": pk, "size": pr["params"].get("size_bytes")},
                        "runtime",
                        **{pk.replace("-", "_"): {"pack": pk}},
                    )
                    self.revalidate.append(f"runtime.installed:{pr['params']['pack']}")
                elif inst.status == "broken":
                    self.add("RUNTIME_PACK_BROKEN", {"pack": inst.key, "reason": "self-test failed"}, "runtime")
            elif code == "MODEL_NOT_INSTALLED":
                pct = self._downloading(key)
                self.revalidate.append(f"model.installed:{key}")
                if pct is not None:
                    self.add("MODEL_DOWNLOADING", {"model": key, "pct": pct}, "model")
                else:
                    self.add(
                        code, {"model": key, "size": pr["params"].get("size_bytes")}, "model", variant={"variant": key}
                    )
            elif code == "LICENSE_CONSENT_REQUIRED":
                self.add(code, {"model": key, "license": pr["params"].get("license")}, "model")

    def _consents(self) -> list[str]:
        from .routes_models import license_hash

        acc = {r.model_id: r.license_hash for r in self.c.execute(select(license_acceptance))}
        return [m for m, h in acc.items() if m in self.reg.models and h == license_hash(self.reg.models[m])]

    def _downloading(self, key: str) -> int | None:
        r = self.c.execute(
            select(operation.c.progress).where(
                operation.c.type == "model.download",
                operation.c.status.in_(DOWNLOAD_ACTIVE),
                operation.c.subject["id"].astext == key,
            )
        ).first()
        if r is None:
            return None
        p = r.progress or {}
        return int(100 * p.get("done", 0) / p["total"]) if p.get("total") else 0

    def _active_downloads(self) -> list[str]:
        return [
            str(k)
            for (k,) in self.c.execute(
                select(operation.c.subject["id"].astext).where(
                    operation.c.type == "model.download", operation.c.status.in_(DOWNLOAD_ACTIVE)
                )
            )
        ]

    def _recent_crashes(self, key: str) -> int:
        since = self.st.clock.now() - timedelta(minutes=5)
        rows = self.c.execute(
            select(event.c.payload).where(event.c.kind == "runtime.crashed", event.c.at >= since)
        ).all()
        return sum(1 for (p,) in rows if p.get("worker_id") == self.w.id and p.get("variant") in (key, None))

    # ---- actions ----
    def job_start(self) -> None:
        p = self.p
        if p.get("kind") == "label" and p.get("rerun_of"):
            self._nothing_to_do()
            return
        s = self._source()
        if s is not None:
            self._root_status(s.root_alias)
            running = self.c.execute(
                select(job.c.id).where(
                    job.c.params["source_id"].astext == s.id, job.c.status.in_(ACTIVE_JOBS), job.c.kind == "organize"
                )
            ).scalar()
            if running and not p.get("queue_after"):
                self.add("JOB_CONFLICT_RUNNING", {"job": running}, "job")
            tgt = p.get("target") or {}
            if tgt.get("root_alias") == s.root_alias:
                src_sub, dst_sub = (s.sub_path or "").strip("/"), str(tgt.get("sub_path") or "").strip("/")
                inside = bool(dst_sub) and (not src_sub or dst_sub == src_sub or dst_sub.startswith(src_sub + "/"))
                insist = bool((p.get("advanced") or {}).get("allow_target_inside_source"))
                if inside and not insist:  # SCAN-024: when the user insists, the scanner excludes the subtree
                    self.add("OUTPUT_TARGET_INSIDE_SOURCE", {"path": dst_sub}, "target")
            failed = self._last_scan(s.id).get("decode_failed")
            if failed:
                self.add("DECODE_FAILED", {"n": failed}, "source")
        models = p.get("models")
        if models:
            self.model_gates(models)
        elif p.get("preset", "by-date") != "by-date":
            from .routes_models import compat_for, job_vlm_key

            key = job_vlm_key(self.c, self.st, self.w.id)
            if key:  # the same compat object the model list and cards show (MDL-005)
                cp = compat_for(self.c, self.st, self.w.id, key)
                if cp is not None:
                    self.models.append({"role": "vlm", "key": key, "compat": cp})
            caps_models = self.caps.get("models") or {}
            if "classify" not in caps_models:
                pct = self._downloading(key) if key else None
                self.revalidate.append("model.installed")
                if pct is not None:  # unlocks by itself when the download finishes (PF-077)
                    self.add("MODEL_DOWNLOADING", {"model": key, "pct": pct}, "model")
                else:
                    self.add("MODEL_NOT_INSTALLED", {"model": "classify"}, "model")

    def _nothing_to_do(self) -> None:
        p = self.p
        assets = [r[0] for r in self.c.execute(select(job_item.c.asset_id).where(job_item.c.job_id == p["rerun_of"]))]
        have = self.c.execute(
            select(func.count())
            .select_from(result)
            .where(
                result.c.asset_id.in_(assets),
                result.c.task_config_hash == config_hash(p.get("task_config") or {}),
                result.c.model_fingerprint == p.get("fingerprint"),
            )
        ).scalar_one()
        if (assets and have == len(assets)) or (p.get("rerun_of") and not assets):
            self.add("JOB_NOTHING_TO_DO", {}, "job")

    def _last_scan(self, source_id: str) -> dict[str, Any]:
        r = self.c.execute(
            select(worker_task.c.result)
            .where(
                worker_task.c.kind == "scan",
                worker_task.c.payload["source_id"].astext == source_id,
                worker_task.c.status == "done",
            )
            .order_by(worker_task.c.created_at.desc())
            .limit(1)
        ).scalar()
        return dict(r or {})

    def _root_status(self, alias: str) -> bool:
        roots = {r["alias"]: r for r in (self.w.roots or [])}
        if alias not in roots:
            self.add("FS_ROOT_NOT_ALLOWED", {"path": alias}, "source")
            return False
        st = (self.caps.get("roots_status") or {}).get(alias)
        if st == "denied":
            self.add("FS_PERMISSION_DENIED", {"path": alias}, "source")
            return False
        if st == "unavailable":
            self.add("FS_ROOT_UNAVAILABLE", {"root": alias}, "source")
            return False
        return True

    def scan(self) -> None:
        s = self._source()
        alias = s.root_alias if s is not None else self.p.get("root_alias")
        if alias is None or not self._root_status(str(alias)):
            return
        if self.p.get("probe"):
            self._probe_readable(str(alias), s.sub_path if s is not None else "")
        if s is not None:
            files = int(self._last_scan(s.id).get("files") or 0)
            disk = self.caps.get("disk_free") or (self.w.disk_free or {})
            free, total = disk.get("thumbs"), disk.get("thumbs_total")
            need = files * THUMB_BYTES_PER_PHOTO
            if free is not None and need > free:
                self.add("DISK_INSUFFICIENT_THUMBS", {"req": need, "free": free}, "disk")
            elif free is not None and total and (free - need) / total < 0.10:
                self.add("DISK_LOW_THUMBS", {"free": free, "pct": 10}, "disk")

    def _probe_readable(self, alias: str, sub: str) -> None:
        """Live gate via a Worker RPC; after 2 s without an answer the gate is ``unknown`` with a retry (PF-076)."""
        import time
        import uuid

        tid = "rpc-" + uuid.uuid4().hex[:12]
        with self.st.engine.begin() as c2:
            c2.execute(
                worker_task.insert().values(
                    id=tid,
                    kind="probe_root",
                    lane="interactive",
                    worker_id=self.w.id,
                    payload={"root_alias": alias, "sub_path": sub},
                )
            )
        deadline = time.monotonic() + RPC_TIMEOUT_S  # noqa: TID251 - waits on a real worker over the wire
        res = None
        while time.monotonic() < deadline:  # noqa: TID251
            with self.st.engine.begin() as c2:
                row = c2.execute(
                    select(worker_task.c.status, worker_task.c.result).where(worker_task.c.id == tid)
                ).first()
            if row is not None and row.status in ("done", "failed"):
                res = row.result or {}
                break
            time.sleep(0.05)  # noqa: TID251
        if res is None:
            with self.st.engine.begin() as c2:  # nobody will read a late answer
                c2.execute(worker_task.delete().where(worker_task.c.id == tid, worker_task.c.status == "pending"))
            self.gates.append(
                {
                    "id": "source_readable",
                    "status": "unknown",
                    "after_s": int(RPC_TIMEOUT_S),
                    "fixes": [{"id": "retry", "type": "retry", "primary": True, "label_key": "fix.retry"}],
                }
            )
            return
        state = res.get("state", "ok")
        if state in ("EPERM", "EACCES"):
            self.add("FS_PERMISSION_DENIED", {"path": alias}, "source_readable")
        elif state == "ENOENT":
            self.add("FS_ROOT_UNAVAILABLE", {"root": alias}, "source_readable")
        else:
            self.gates.append({"id": "source_readable", "status": "ok"})

    def model_download(self) -> None:
        key = str(self.p.get("variant") or "")
        mid, _, vid = key.partition("@")
        m = self.reg.models.get(mid)
        if m is None:
            return
        if m.pin_state.get(vid) != "pinned":
            self.add("MODEL_NOT_PINNED", {"variant": key}, "model")
        if m.spec.license.requires_acceptance and mid not in self._consents():
            self.add("LICENSE_CONSENT_REQUIRED", {"model": key, "license": m.spec.license.spdx}, "model")
        files = m.pinned_files(vid)
        size = sum(f.size or 0 for f in files)
        disk = self.caps.get("disk_free") or (self.w.disk_free or {})
        free = disk.get("models")
        if free is not None and size * 1.05 > free:
            self.add("DISK_INSUFFICIENT_MODELS", {"req": int(size * 1.05), "free": free}, "disk")
        last = self.c.execute(
            select(operation.c.result)
            .where(
                operation.c.type == "model.download",
                operation.c.status == "failed",
                operation.c.subject["id"].astext == key,
            )
            .order_by(operation.c.updated_at.desc())
            .limit(1)
        ).scalar()
        code = (last or {}).get("error_code")
        if code in DOWNLOAD_FAILURE_CODES:
            self.add(code, {"model": key}, "network")

    def model_load(self) -> None:
        key = str(self.p.get("variant") or "")
        mid, _, vid = key.partition("@")
        m = self.reg.models.get(mid)
        if m is None:
            return
        inst = self._installs("model").get(key)
        if inst is not None and inst.status == "files_missing":
            self.add("MODEL_FILES_MISSING", {"model": key}, "model")
            return
        if inst is not None and inst.status == "corrupt":
            self.add("MODEL_CORRUPT", {"model": key}, "model")
            return
        failed = self.c.execute(
            select(event.c.payload).where(event.c.kind == "model.load_failed").order_by(event.c.id.desc()).limit(1)
        ).scalar()
        if failed and failed.get("variant") == key and failed.get("worker_id") == self.w.id:
            self.add("MODEL_LOAD_FAILED", {"model": key, "reason": failed.get("reason")}, "model")
        from opagent_registry.compat import compat

        cp = compat(m, vid, self.hw(), self.reg.runtimes, gpu_classes=self.reg.gpu_classes, installed=[key])
        if cp.fits_now is False:
            g = self.hw().primary_gpu
            procs = list(getattr(g, "processes", ()) or [])
            self.add(
                "GPU_BUSY_EXTERNAL",
                {"gb": round(g.vram_used_external_gb, 1) if g else None, "process": ", ".join(procs)},
                "vram",
                details={"processes": procs},
            )

    def model_delete(self) -> None:
        inst = self.c.execute(select(installation).where(installation.c.id == self.p.get("installation_id"))).first()
        if inst is None:
            return
        using = [
            r.id
            for r in self.c.execute(select(job.c.id, job.c.params).where(job.c.status.in_(ACTIVE_JOBS)))
            if inst.key in ((r.params or {}).get("models") or [])
        ]
        if using:
            self.add("MODEL_IN_USE", {"model": inst.key, "n": len(using)}, "model", details={"jobs": using})

    def job_retry_failed(self) -> None:
        codes = {
            r[0]
            for r in self.c.execute(
                select(job_item.c.error_code).where(
                    job_item.c.job_id == self.p.get("job_id"), job_item.c.status == "failed"
                )
            )
        }
        if "RUNTIME_TIMEOUT" in codes:
            self.add("RUNTIME_TIMEOUT", {}, "job")
        if "MODEL_OOM" in codes:
            self.add("MODEL_OOM", {}, "job")

    def output_apply(self) -> None:
        tgt = (self.p.get("target") or {}).get("root_alias")
        roots = {r["alias"]: r for r in (self.w.roots or [])}
        if tgt and roots.get(tgt, {}).get("mode") == "ro":
            self.add("FS_NOT_WRITABLE", {"path": tgt}, "target")
        pid = self.p.get("plan_id")
        if pid:
            summ = self.c.execute(select(output_plan.c.summary).where(output_plan.c.id == pid)).scalar() or {}
            if summ.get("stale"):
                self.add("PLAN_STALE", {"n": summ["stale"]}, "plan")
            locked = self.c.execute(
                select(func.count())
                .select_from(output_action)
                .where(output_action.c.plan_id == pid, output_action.c.error == "FS_FILE_LOCKED")
            ).scalar_one()
            if locked:
                self.add("FS_FILE_LOCKED", {"path": "…", "n": locked}, "target")

    def output_undo(self) -> None:
        summ = (
            self.c.execute(select(output_plan.c.summary).where(output_plan.c.id == self.p.get("plan_id"))).scalar()
            or {}
        )
        if summ.get("undo_conflicts"):
            self.add("UNDO_TARGET_MODIFIED", {"n": summ["undo_conflicts"]}, "plan")

    def search(self) -> None:
        from .search import index_status

        if "embed" not in (self.caps.get("models") or {}):  # SigLIP2 not loaded yet (04 §8.2 W6)
            key = self._downloading_embed()
            if key:
                self.add("MODEL_DOWNLOADING", {"model": key[0], "pct": key[1]}, "model")
            else:
                self.add("MODEL_NOT_INSTALLED", {"model": "embed"}, "model")
            self.revalidate.append("model.installed")
            return
        st = index_status(self.c)
        space = st["space"]
        if space is None:
            return
        embed_fp = (self.caps.get("models") or {}).get("embed")
        if embed_fp and embed_fp != space["fingerprint"]:
            self.add("SEARCH_MODEL_MISMATCH", {"old": space["model_key"], "new": embed_fp}, "search")
        missing = st["total"] - st["searchable"]
        if missing > 0:
            self.add(
                "SEARCH_INDEX_INCOMPLETE",
                {"n": missing, "searchable": st["searchable"], "total": st["total"]},
                "search",
            )

    def _downloading_embed(self) -> tuple[str, int] | None:
        for key in sorted(self._active_downloads()):
            m = self.reg.models.get(key.split("@")[0])
            if m is not None and m.spec.kind == "embedding":
                return key, int(self._downloading(key) or 0)
        return None

    def runtime_install(self) -> None:
        st = (self.caps.get("packs_status") or {}).get(self.p.get("pack")) or {}
        if st.get("error") in ("PACK_SIGNATURE_INVALID", "PACK_INCOMPATIBLE"):
            self.add(
                st["error"],
                {"pack": self.p.get("pack"), "v": st.get("version"), "range": st.get("pack_api")},
                "runtime",
            )

    def run(self) -> dict[str, Any]:
        if self.action == "job.start" and self.p.get("kind") == "label" and self.p.get("rerun_of"):
            self._nothing_to_do()  # 100 % cache hits: pure control-plane work, no worker needed
            if self.warnings:
                return self._response()
        if self.worker_gate():
            self.doctor_gate()
            handler = {
                "job.start": self.job_start,
                "scan": self.scan,
                "model.download": self.model_download,
                "model.load": self.model_load,
                "model.delete": self.model_delete,
                "model.move": self.model_delete,
                "job.retry_failed": self.job_retry_failed,
                "output.apply": self.output_apply,
                "output.undo": self.output_undo,
                "runtime.install": self.runtime_install,
                "search": self.search,
            }.get(self.action)
            if handler:
                handler()
        return self._response()

    def merged_fix(self) -> dict[str, Any] | None:
        """PF-072: when ≥ 2 blockers can be solved by downloads, offer one "prepare everything" fix."""
        items, total = [], 0
        for b in self.blockers:
            if b["code"] == "MODEL_NOT_INSTALLED" and "@" in str(b["params"].get("model")):
                items.append({"variant": b["params"]["model"]})
                total += int(b["params"].get("size") or 0)
            elif b["code"] == "RUNTIME_PACK_MISSING":
                items.append({"pack": b["params"]["pack"]})
                total += int(b["params"].get("size") or 0)
        if len(items) < 2:
            return None
        return {
            "fix_id": "prepare_all",
            "id": "prepare_all",
            "type": "download",
            "primary": True,
            "label_key": "fix.prepare_all",
            "count": len(items),
            "items": items,
            "bytes": total,
        }

    def state_token(self) -> str:
        """Cheap summary of everything preflight reads (PF-074): when unchanged, the cached answer stands."""
        ev = self.c.execute(select(func.max(event.c.id))).scalar() or 0
        ins = self.c.execute(select(func.count()).select_from(installation)).scalar() or 0
        ops = self.c.execute(select(func.max(operation.c.updated_at))).scalar()
        hb = self.c.execute(select(func.max(worker.c.last_heartbeat_at))).scalar()
        online = bool(
            hb
            and self.st.clock.now() - (hb if hb.tzinfo else hb.replace(tzinfo=__import__("datetime").UTC))
            <= timedelta(seconds=OFFLINE_AFTER_S)
        )
        return f"{ev}|{ins}|{ops}|{online}"

    def _response(self) -> dict[str, Any]:
        fp = hashlib.sha256(
            json.dumps(
                [self.action, self.p, [b["code"] for b in self.blockers], [w["code"] for w in self.warnings]],
                sort_keys=True,
                default=str,
            ).encode()
        ).hexdigest()[:16]
        return {
            "action": self.action,
            "ready": not self.blockers,
            "checked_at": self.st.clock.now().isoformat(),
            "fingerprint": f"pf_{fp}",
            "gates": self.gates,
            "blockers": self.blockers,
            "warnings": self.warnings,
            "revalidate_on": sorted(set(self.revalidate)),
            "ttl_s": 60,
            "merged_fix": self.merged_fix(),
            "models": self.models,
            "cached": False,
        }


_CACHE: dict[str, dict[str, Any]] = {}


def run_preflight(
    c: Any, st: Any, action: str, params: dict[str, Any], fingerprint: str | None = None
) -> dict[str, Any]:
    pre = Pre(c, st, action, params)
    key = hashlib.sha256(
        json.dumps([action, params, pre.state_token()], sort_keys=True, default=str).encode()
    ).hexdigest()[:16]
    if fingerprint and fingerprint in _CACHE and _CACHE[fingerprint].get("_key") == key:
        hit = {k: v for k, v in _CACHE[fingerprint].items() if k != "_key"}
        return {**hit, "cached": True}  # state unchanged since the client's fingerprint: skip recompute
    res = pre.run()
    res["fingerprint"] = f"pf_{key}"
    _CACHE[res["fingerprint"]] = {**res, "_key": key}
    if len(_CACHE) > 2000:
        _CACHE.pop(next(iter(_CACHE)))
    return res


def enforce(c: Any, st: Any, action: str, params: dict[str, Any]) -> dict[str, Any]:
    """Server-side enforcement (06 §4.2, PF-073): same engine, 409 problem+json with the same blockers."""
    from fastapi import HTTPException

    res = run_preflight(c, st, action, params)
    if res["blockers"]:
        raise HTTPException(
            409, detail={"code": "PREFLIGHT_BLOCKED", "blockers": res["blockers"], "warnings": res["warnings"]}
        )
    return res
