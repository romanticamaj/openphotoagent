import json
import logging

import pytest
from opagent_cli.paths import app_dirs


@pytest.mark.unit
@pytest.mark.tc("DESK-025")
def test_desk_025_os_paths_and_portable_override():
    win = app_dirs(
        "windows",
        {"LOCALAPPDATA": r"C:\Users\a\AppData\Local", "APPDATA": r"C:\Users\a\AppData\Roaming"},
        home="C:/Users/a",
    )
    assert win["data"].replace("\\", "/").endswith("AppData/Local/OpenPhotoAgent")
    assert win["config"].replace("\\", "/").endswith("AppData/Roaming/OpenPhotoAgent")
    assert win["models"].replace("\\", "/").endswith("AppData/Local/OpenPhotoAgent/models")
    assert win["logs"].replace("\\", "/").endswith("AppData/Local/OpenPhotoAgent/logs")
    mac = app_dirs("macos", {}, home="/Users/a")
    assert mac["data"] == "/Users/a/Library/Application Support/OpenPhotoAgent"
    assert mac["cache"] == "/Users/a/Library/Caches/OpenPhotoAgent"
    assert mac["logs"] == "/Users/a/Library/Logs/OpenPhotoAgent"
    lin = app_dirs("linux", {"XDG_DATA_HOME": "/x/data"}, home="/home/a")
    assert lin["data"] == "/x/data/openphotoagent"
    assert lin["config"] == "/home/a/.config/openphotoagent"
    assert lin["logs"] == "/home/a/.local/state/openphotoagent/logs"
    assert lin["backups"] == "/x/data/openphotoagent/backups" and lin["packs"] == "/x/data/openphotoagent/packs"
    port = app_dirs("linux", {"OPAGENT_HOME": "/media/usb/opa"}, home="/home/a")
    assert all(v.startswith("/media/usb/opa") for v in port.values())  # portable mode: everything inside
    for d in (win, mac, lin):  # never Roaming/Documents for data (08 §1.5)
        assert "Documents" not in d["data"] and "Roaming" not in d["data"]


@pytest.mark.unit
@pytest.mark.tc("DESK-026")
def test_desk_026_json_lines_rotation_and_redaction(tmp_path):
    from opagent_cli.logs import setup_logging

    secret = "k" * 64
    h = setup_logging(tmp_path, "api", token=secret, roots={"photos": "/home/a/Pictures"})
    log = logging.getLogger("opagent.test")
    log.warning("scanned /home/a/Pictures/2024/x.jpg with token %s", secret)
    h.flush()
    lines = (tmp_path / "api.log").read_text(encoding="utf-8").splitlines()
    rec = json.loads(lines[-1])
    assert rec["level"] == "WARNING" and rec["proc"] == "api" and "ts" in rec
    assert "photos:2024/x.jpg" in rec["msg"] and "/home/a" not in rec["msg"] and secret not in rec["msg"]
    assert h.maxBytes == 10 * 1024 * 1024 and h.backupCount == 5
