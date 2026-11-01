"""Build packages/i18n/errors.{zh-TW,en}.json from the 06 §4.7 error-code catalogue (PF-078).

zh-TW text and both CTAs come from the spec table; English messages are maintained here (translations of the
zh-TW sentences, same {placeholders}). ``--check`` fails when the JSON files are out of date.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "docs" / "06-models-environment-operations.md"
OUT = ROOT / "packages" / "i18n"

EN = {
    "WORKER_OFFLINE": "Worker “{name}” has been offline for {duration}; features that need it are paused",
    "WORKER_VERSION_MISMATCH": "Worker version {w} is not compatible with server {s}",
    "GPU_NOT_FOUND": "No usable GPU was found; the CPU will be used (slower)",
    "GPU_DRIVER_TOO_OLD": "GPU driver {v} is too old; {min} or newer is required",
    "GPU_ARCH_UNSUPPORTED": "Your {gpu} ({sm}) needs a newer runtime pack",
    "GPU_BUSY_EXTERNAL": "Another program is using {gb} GB of VRAM ({process})",
    "GPU_DOCKER_NO_PASSTHROUGH": "The container cannot see the GPU; the NVIDIA runtime may not be enabled",
    "VRAM_INSUFFICIENT": "{model} needs about {need} GB of VRAM; your GPU has {total} GB",
    "VRAM_SEQUENTIAL": "Cannot load together with the embedding model; they will take turns (about {pct}% longer)",
    "RAM_INSUFFICIENT": "{need} GB of system memory is required; you have {total} GB",
    "PERF_CPU_ONLY_SLOW": "Only the CPU can run this; {n} photos will take about {eta}",
    "RUNTIME_PACK_MISSING": "The “{pack}” runtime pack is required ({size})",
    "RUNTIME_PACK_BROKEN": "“{pack}” cannot start: {reason}",
    "RUNTIME_UNSUPPORTED_PLATFORM": "{runtime} does not support {os} (vLLM supports Linux/WSL2 only)",
    "RUNTIME_CRASHED": "The inference process stopped unexpectedly (restarted automatically {n} times)",
    "RUNTIME_TIMEOUT": "The model timed out; retrying with lower concurrency",
    "ORT_EP_UNAVAILABLE": "{ep} acceleration is unavailable; faces and taggers will use the CPU",
    "WSL_GPU_UNAVAILABLE": "WSL2 cannot see the GPU, so vLLM cannot run",
    "OLLAMA_NOT_RUNNING": "Cannot reach Ollama ({url})",
    "EXTERNAL_ENDPOINT_UNREACHABLE": "Cannot reach the external model service {url}",
    "DECODE_HEIC_MISSING": "HEIC cannot be decoded; {n} iPhone photos will be skipped",
    "DECODE_RAW_MISSING": "RAW files cannot be read; {n} files will be skipped",
    "DECODE_FAILED": "{n} files are damaged or unsupported and were skipped",
    "FFMPEG_MISSING": "ffmpeg is missing; {n} videos will be skipped (no thumbnails)",
    "DISK_INSUFFICIENT_MODELS": "Not enough space for models: {req} needed, {free} left",
    "DISK_INSUFFICIENT_THUMBS": "Not enough space for thumbnails: about {req} needed, {free} left",
    "DISK_LOW_THUMBS": "The thumbnail folder has {free} left and will drop below {pct}% after processing",
    "FS_PERMISSION_DENIED": "No permission to read “{path}”",
    "FS_ROOT_NOT_ALLOWED": "“{path}” is not inside a folder the worker is allowed to use",
    "FS_ROOT_UNAVAILABLE": "“{root}” is not reachable right now (NAS not mounted?)",
    "FS_NOT_WRITABLE": "Cannot write to “{path}”",
    "NET_UNREACHABLE": "Cannot connect to {host}",
    "NET_TLS_INTERCEPTED": "The connection certificate is not trusted (company proxy?)",
    "HF_AUTH_REQUIRED": "This model needs a Hugging Face login token",
    "HF_GATED_ACCESS_PENDING": "You need to accept the terms of {model} on the Hugging Face website first",
    "LICENSE_CONSENT_REQUIRED": "You need to accept {license} before using {model}",
    "MODEL_NOT_INSTALLED": "{model} ({size}) is needed and not downloaded yet",
    "MODEL_DOWNLOADING": "{model} is downloading ({pct}%); this unlocks automatically when it finishes",
    "MODEL_FILES_MISSING": "The files of {model} are missing (was the external folder moved?)",
    "MODEL_CORRUPT": "{model} failed file verification",
    "MODEL_LOAD_FAILED": "Cannot load {model}: {reason}",
    "MODEL_OOM": "Out of GPU memory; retried automatically with a smaller batch",
    "MODEL_CAPABILITY_MISMATCH": "{model} does not support {task}",
    "MODEL_IN_USE": "{model} is used by {n} jobs",
    "DL_CHECKSUM_MISMATCH": "The downloaded file does not match the expected hash (already retried)",
    "DL_RATE_LIMITED": "The download source is rate-limiting; retrying automatically in {s} s",
    "DL_NOT_FOUND": "The remote file was not found (the model catalogue may be out of date)",
    "DL_UNVERIFIED_SOURCE": "Only a mirror's metadata is available; the official hash cannot be verified",
    "NET_PORT_IN_USE": "Port {port} is used by {process}",
    "TIME_SKEW": "The system clock differs from the server by {s} seconds",
    "JOB_NOTHING_TO_DO": "Every photo has already been processed (100% cache hits)",
    "JOB_CONFLICT_RUNNING": "A job “{job}” is already running on the same source",
    "SEARCH_INDEX_INCOMPLETE": "{n} photos have no search vectors yet; results may be incomplete",
    "SEARCH_MODEL_MISMATCH": "The search index was built with {old}, which differs from the current default {new}",
    "PLAN_STALE": "{n} files changed after the plan was created",
    "OUTPUT_TARGET_INSIDE_SOURCE": "The output folder is inside the source folder, which would process files twice",
    "UNDO_TARGET_MODIFIED": "{n} organized files were modified afterwards and cannot be restored safely",
    "EGRESS_POLICY_BLOCKS": "This workspace's privacy settings do not allow sending images to external services",
    "PERMISSION_DENIED_ROLE": "The {permission} permission is required",
    "MCP_DISABLED": "The MCP server is not enabled",
    "MCP_TOKEN_INVALID": "The MCP token is invalid or expired",
    "CHECK_CRASHED": "The environment check “{check}” itself failed",
    "PACK_SIGNATURE_INVALID": "The signature of the “{pack}” runtime pack is invalid; installation was refused",
    "PACK_INCOMPATIBLE": "“{pack}” version {v} is not compatible with this app (needs pack_api {range})",
    "FS_FILE_LOCKED": "“{path}” is in use by another program (retried {n} times)",
    "DL_REMOTE_CHANGED": "The remote file changed while resuming; it was downloaded again from the start",
    "DL_MIRROR_FAILED": "The mirror {mirror} failed; switched back to the official source",
    "VRAM_OFFLOAD": "Not enough VRAM to fit {model} on the GPU; some layers run on the CPU (about {pct}% slower)",
    "MODEL_NOT_PINNED": "This variant has not been hash-pinned yet, so it cannot be downloaded safely",
    "PACK_CHECKSUM_MISMATCH": "The “{pack}” runtime pack file does not match the signed index; it was rejected and the staging area cleaned",
    "PACK_UNSAFE_ARCHIVE": "The “{pack}” runtime pack contents are unsafe ({reason}); installation was refused",
}

ROW = re.compile(r"^\| (\d+) \| `([A-Z_]+)` \| ([^|]+) \| ([^|]+) \| ([^|]+) \| ([^|]+) \|$")


def rows() -> list[tuple[str, str, str, str, str]]:
    out = []
    for line in SPEC.read_text(encoding="utf-8").splitlines():
        m = ROW.match(line.strip())
        if m:
            out.append((m.group(2), m.group(3).strip(), m.group(4).strip(), m.group(5).strip(), m.group(6).strip()))
    return out


def _title(msg: str, seps: str) -> str:
    for s in seps:
        i = msg.find(s)
        if i > 0:
            return msg[:i]
    return msg


def build() -> dict[str, dict[str, dict[str, str]]]:
    zh, en = {}, {}
    for code, sev, msg_zh, cta_zh, cta_en in rows():
        msg_en = EN[code]
        zh[code] = {"title": _title(msg_zh, "，："), "message": msg_zh, "cta": cta_zh, "severity": sev}
        en[code] = {"title": _title(msg_en, ";:"), "message": msg_en, "cta": cta_en, "severity": sev}
    return {"zh-TW": zh, "en": en}


def main() -> int:
    data = build()
    bad = []
    for lang, d in data.items():
        text = json.dumps(d, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        p = OUT / f"errors.{lang}.json"
        if "--check" in sys.argv:
            if not p.exists() or p.read_text(encoding="utf-8") != text:
                bad.append(str(p))
        else:
            p.write_text(text, encoding="utf-8")
    if bad:
        print("out of date:", *bad)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
