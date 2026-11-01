import hashlib
import json
import urllib.request

from tests.support.hfserver import HfFile, HfServer


def test_ok_scenario_serves_metadata_and_ranges():
    srv = HfServer("org/model", [HfFile("w.onnx", 4096)]).start()
    try:
        meta = json.loads(urllib.request.urlopen(f"{srv.url}/api/models/org/model").read())
        sha = meta["siblings"][0]["lfs"]["sha256"]
        full = urllib.request.urlopen(f"{srv.url}/org/model/resolve/main/w.onnx").read()
        assert hashlib.sha256(full).hexdigest() == sha and len(full) == 4096
        req = urllib.request.Request(f"{srv.url}/org/model/resolve/main/w.onnx", headers={"Range": "bytes=1000-"})
        r = urllib.request.urlopen(req)
        assert r.status == 206 and r.read() == full[1000:]
        assert srv.requests[-1]["range"] == "bytes=1000-"
    finally:
        srv.stop()
