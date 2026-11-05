"""What Linux tests read from /proc, on Windows: process command lines and sockets (test evidence only)."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Any


def processes(pid: int) -> list[dict[str, Any]]:
    """``pid`` and its children: a venv's python.exe is a launcher, and the interpreter is its child."""
    ps = (
        "[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
        f"Get-CimInstance Win32_Process -Filter 'ProcessId={pid} or ParentProcessId={pid}'"
        " | Select-Object ProcessId,CommandLine | ConvertTo-Json"
    )
    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps], capture_output=True, encoding="utf-8", check=True, timeout=60
    )
    got = json.loads(r.stdout)
    return got if isinstance(got, list) else [got]


@dataclass(frozen=True)
class Socket:
    proto: str
    local: str
    remote: str
    pid: int

    @property
    def listening(self) -> bool:
        # the state word is localized; a listener's remote end is not. UDP has no peer at all, like a bound socket.
        return self.remote.rsplit(":", 1)[-1] in ("0", "*")


def sockets() -> list[Socket]:
    r = subprocess.run(["netstat", "-ano"], capture_output=True, encoding="oem", check=True, timeout=60)
    out = []
    for line in r.stdout.splitlines():
        cols = line.split()
        if len(cols) >= 4 and cols[0] in ("TCP", "UDP") and cols[-1].isdigit():
            out.append(Socket(cols[0], cols[1], cols[2], int(cols[-1])))
    return out


def listening_hosts(port: int) -> list[str]:
    """Same hex form as /proc/net/tcp for IPv4 (``0100007F``)."""
    out = []
    for s in sockets():
        if s.proto == "TCP" and s.listening and s.local.endswith(f":{port}"):
            host = s.local.rsplit(":", 1)[0]
            out.append(host if host.startswith("[") else "".join(f"{int(b):02X}" for b in reversed(host.split("."))))
    return out
