import io
import ipaddress
import json
import re
import zipfile

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from opagent_doctor.diagnostics import build_bundle, markdown_report, redact

SECRETS = [
    "/Users/alice/Pictures/2024/IMG_1.jpg",
    r"C:\Users\bob\AppData\Roaming\opagent\log.txt",
    "/home/carol/photos/beach.heic",
    "opagent_pat_abcdEFGH12345678",
    "Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig",
    "192.168.1.20",
    "fe80::1ff:fe23:4567:890a",
    "desktop-amy",
]
BAD = re.compile(r"alice|bob|carol|opagent_pat_|Bearer\s+\S|192\.168\.1\.20|fe80::|desktop-amy")
JPEG, PNG = b"\xff\xd8\xff", b"\x89PNG"


@pytest.mark.security
@pytest.mark.tc("DIAG-001")
def test_diag_001_bundle_has_no_secrets(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "api.log").write_text("\n".join(f"line {i}: {s} done" for i, s in enumerate(SECRETS)), encoding="utf-8")
    (logs / "thumb.webp").write_bytes(JPEG + b"\0" * 100)  # stray image in the log dir must never be bundled
    (logs / "worker.log").write_bytes(b"ok\n" + PNG + b"binary")
    doctor = [{"check_id": "fs.roots", "status": "fail", "params": {"path": "/Users/alice/Pictures"}}]
    blob = build_bundle(logs, doctor=doctor, hostname="desktop-amy", meta={"version": "0.1.0", "deployment": "desktop"})
    z = zipfile.ZipFile(io.BytesIO(blob))
    names = z.namelist()
    assert "doctor.json" in names and "report.md" in names and not any(n.endswith(".webp") for n in names)
    for n in names:
        data = z.read(n)
        assert JPEG not in data and PNG not in data, n
        text = data.decode("utf-8", "replace")
        assert not BAD.search(text), (n, BAD.search(text))
    api = z.read("logs/api.log").decode()
    assert "~/Pictures" in api and "host-" in z.read("report.md").decode()
    assert json.loads(z.read("doctor.json"))[0]["params"]["path"] == "~/Pictures"


ipv4 = st.builds(lambda a, b, c, d: f"{a}.{b}.{c}.{d}", *(st.integers(1, 254) for _ in range(4)))
ipv6 = st.integers(1, 2**128 - 1).map(lambda n: str(ipaddress.IPv6Address(n)))
user = st.from_regex(r"[a-z][a-z0-9]{2,10}", fullmatch=True).filter(lambda u: u not in ("users", "home"))
path = st.one_of(
    user.map(lambda u: f"/Users/{u}/x"), user.map(lambda u: f"/home/{u}/x"), user.map(lambda u: f"C:\\Users\\{u}\\x")
)
token = st.from_regex(r"opagent_pat_[A-Za-z0-9]{16,32}", fullmatch=True)
bearer = st.from_regex(r"[A-Za-z0-9._-]{12,40}", fullmatch=True).map(lambda t: f"Bearer {t}")
plain = st.sampled_from(["scan finished", "lease renewed", "job 42 done", "plan approved"])


@pytest.mark.property
@pytest.mark.tc("DIAG-002")
@settings(max_examples=300, deadline=None)
@given(prefix=plain, secret=st.one_of(ipv4, ipv6, path, token, bearer), suffix=plain)
def test_diag_002_redactor_property(prefix, secret, suffix):
    out = redact(f"{prefix} {secret} {suffix}")
    core = (
        secret.split("/")[2]
        if secret.startswith(("/Users/", "/home/"))
        else secret.split("\\")[2]
        if secret.startswith("C:")
        else secret
    )
    assert core not in out
    assert out.startswith(prefix) and out.endswith(suffix)


@pytest.mark.unit
@pytest.mark.tc("DIAG-003")
def test_diag_003_markdown_report():
    doctor = [
        {"check_id": "gpu.driver", "status": "fail", "code": "GPU_DRIVER_TOO_OLD", "params": {}},
        {"check_id": "decode.heic", "status": "ok", "code": None, "params": {}},
        {"check_id": "time.skew", "status": "warn", "code": "TIME_SKEW", "params": {}},
    ]
    failed = [{"id": f"op{i}", "type": "model.download", "error_code": "NET_UNREACHABLE"} for i in range(25)]
    md = markdown_report(
        {"version": "0.1.0", "deployment": "desktop"},
        {"os": "windows", "gpus": [{"name": "RTX 5070 Ti"}]},
        doctor,
        failed,
        hostname="desktop-amy",
    )
    assert "0.1.0" in md and "desktop" in md and "RTX 5070 Ti" in md
    assert "gpu.driver" in md and "time.skew" in md and "decode.heic" not in md
    assert md.count("NET_UNREACHABLE") == 20
    assert "desktop-amy" not in md
