"""INF-OOM-005 (回歸): a RUNTIME fault really ends the process on every OS.

``signal.SIGKILL`` does not exist on Windows, so ``os.kill(pid, signal.SIGKILL)`` raised AttributeError inside the
request thread: the connection dropped but the "crashed" runtime lived on, and the crash-recovery path the adapters
are tested against (restart, halve ``--parallel``, next variant) was never exercised."""

import socket
import subprocess
import sys

import httpx
import pytest

SERVERS = {
    "fake_llama": ["opagent_testing.fake_llama_server"],
    "torch_sidecar": ["opagent_adapters.torch_server", "--model", "m", "--fake-pipeline"],
}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _exited(proc: subprocess.Popen[bytes], timeout: float) -> bool:
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        return False
    return True


def _wait_listening(proc: subprocess.Popen[bytes], url: str) -> None:
    for _ in range(600):
        try:
            if httpx.get(url + "/health", timeout=1).status_code < 500:
                return
        except httpx.TransportError:
            pass
        assert not _exited(proc, 0.1), "the server died while starting"
    raise AssertionError("the server never answered /health")


@pytest.mark.unit
@pytest.mark.tc("INF-OOM-005")
@pytest.mark.parametrize("server", SERVERS)
def test_inf_oom_005_runtime_fault_ends_the_process(server):
    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    proc = subprocess.Popen(
        [sys.executable, "-m", *SERVERS[server], "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_listening(proc, url)
        assert httpx.post(url + "/_fault", json={"RUNTIME": 1}, timeout=5).status_code < 300
        with pytest.raises(httpx.TransportError):  # no answer: the process is gone mid-request
            httpx.post(url + "/v1/chat/completions", json={"messages": []}, timeout=10)
        assert _exited(proc, 10), "the runtime survived its own crash fault"
    finally:
        proc.kill()
        proc.wait()
