"""Diagnostics (06 §3.7): a redacted Markdown report and a redacted zip of log tails + doctor results.

Redaction: user names in paths → ``~``, host name → stable hash, PAT/Bearer tokens and IP addresses removed. Binary
image content is never bundled (magic-number filter), so photo bytes cannot leak (DIAG-001).
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from pathlib import Path
from typing import Any

_PATHS = [
    (re.compile(r"/Users/[^/\s\"']+"), "~"),
    (re.compile(r"/home/[^/\s\"']+"), "~"),
    (re.compile(r"[A-Za-z]:\\\\?Users\\\\?[^\\\s\"']+", re.I), "~"),
]
_TOKENS = [
    (re.compile(r"opagent_pat_[A-Za-z0-9_\-]+"), "[token]"),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-~+/=]+"), "[token]"),
    (re.compile(r"(?i)\b(hf_[A-Za-z0-9]{8,})"), "[token]"),
]
_IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
_IPV6 = re.compile(r"(?<![0-9A-Fa-f:])(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}(?![0-9A-Fa-f:])")
IMAGE_MAGIC = (
    b"\xff\xd8\xff",
    b"\x89PNG",
    b"GIF8",
    b"RIFF",
    b"II*\x00",
    b"MM\x00*",
    b"\x00\x00\x00\x18ftyp",
    b"\x00\x00\x00\x1cftyp",
)
TAIL_BYTES = 256 * 1024


def host_hash(hostname: str) -> str:
    return "host-" + hashlib.sha256(hostname.encode()).hexdigest()[:8]


def redact(text: str, hostname: str | None = None) -> str:
    for rx, rep in _TOKENS:
        text = rx.sub(rep, text)
    for rx, rep in _PATHS:
        text = rx.sub(rep, text)
    text = _IPV4.sub("[ip]", text)
    text = _IPV6.sub(
        lambda m: "[ip]" if m.group(0).count(":") >= 2 and any(c.isalnum() for c in m.group(0)) else m.group(0), text
    )
    if hostname:
        text = text.replace(hostname, host_hash(hostname))
    return text


def _redact_obj(o: Any, hostname: str | None) -> Any:
    if isinstance(o, str):
        return redact(o, hostname)
    if isinstance(o, list):
        return [_redact_obj(x, hostname) for x in o]
    if isinstance(o, dict):
        return {k: _redact_obj(v, hostname) for k, v in o.items()}
    return o


def _has_image(data: bytes) -> bool:
    return any(m in data for m in IMAGE_MAGIC)


def markdown_report(
    meta: dict[str, Any],
    hw: dict[str, Any],
    doctor: list[dict[str, Any]],
    failed_ops: list[dict[str, Any]],
    hostname: str = "",
) -> str:
    lines = [
        "## OpenPhotoAgent diagnostics",
        "",
        f"- Version: {meta.get('version')}",
        f"- Deployment: {meta.get('deployment')}",
        f"- Host: {host_hash(hostname) if hostname else '-'}",
        f"- OS: {hw.get('os')} {hw.get('arch', '')}".rstrip(),
        f"- GPUs: {', '.join(g.get('name', '?') for g in hw.get('gpus') or []) or 'none'}",
        "",
        "### Checks that are not ok",
        "",
    ]
    for d in doctor:
        if d.get("status") not in ("ok", "skip"):
            lines.append(f"- `{d['check_id']}`: {d.get('status')} {d.get('code') or ''}".rstrip())
    lines += ["", "### Recent failed operations (latest 20)", ""]
    for op in failed_ops[:20]:
        lines.append(f"- {op.get('type')}: {op.get('error_code')}")
    return redact("\n".join(lines) + "\n", hostname or None)


def build_bundle(
    logs_dir: Path,
    doctor: list[dict[str, Any]],
    hostname: str,
    meta: dict[str, Any],
    hw: dict[str, Any] | None = None,
    failed_ops: list[dict[str, Any]] | None = None,
) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(Path(logs_dir).glob("*.log")):
            data = p.read_bytes()[-TAIL_BYTES:]
            if _has_image(data):  # drop binary chunks entirely; logs are text
                data = b"\n".join(ln for ln in data.split(b"\n") if not _has_image(ln))
            z.writestr(f"logs/{p.name}", redact(data.decode("utf-8", "replace"), hostname))
        z.writestr("doctor.json", json.dumps(_redact_obj(doctor, hostname), ensure_ascii=False, indent=2))
        z.writestr("report.md", markdown_report(meta, hw or {}, doctor, failed_ops or [], hostname))
    return buf.getvalue()
