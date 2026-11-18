"""Default output folder: ``<Pictures>/OpenPhotoAgent 整理結果`` (04 §8, 08 §6; ORG-003).

Windows asks the shell for FOLDERID_Pictures (follows OneDrive / folder redirection); Linux asks
``xdg-user-dir PICTURES``; macOS uses ``~/Pictures``. Providers are injectable so every branch is testable on
any OS; the real ones are used when none is given.
"""

from __future__ import annotations

import ntpath
import posixpath
import subprocess
import sys
from collections.abc import Callable

FOLDERID_PICTURES = "{33E28130-4E1E-4676-835A-98395C3BC3BB}"
NAME = {"zh-TW": "OpenPhotoAgent 整理結果", "en": "OpenPhotoAgent Sorted"}


def _win_known_folder(fid: str) -> str:  # pragma: no cover - Windows only (CI OS matrix)
    if sys.platform != "win32":  # also tells mypy that windll exists below, on every checking OS
        raise OSError("known folders are a Windows API")
    import ctypes
    import uuid
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("d1", wintypes.DWORD), ("d2", wintypes.WORD), ("d3", wintypes.WORD), ("d4", ctypes.c_ubyte * 8)]

    u = uuid.UUID(fid)
    g = GUID(u.fields[0], u.fields[1], u.fields[2], (ctypes.c_ubyte * 8)(*u.bytes[8:]))
    out = ctypes.c_wchar_p()
    shell32 = ctypes.windll.shell32
    if shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(out)) != 0:
        raise OSError("SHGetKnownFolderPath failed")
    try:
        return str(out.value)
    finally:
        ctypes.windll.ole32.CoTaskMemFree(out)


def _run(cmd: list[str]) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=5, check=True).stdout


def default_output_dir(
    system: str,
    lang: str,
    *,
    home: str,
    known_folder: Callable[[str], str] | None = None,
    run: Callable[[list[str]], str] | None = None,
) -> str:
    name = NAME.get(lang, NAME["en"])
    if system == "windows":
        try:
            pics = (known_folder or _win_known_folder)(FOLDERID_PICTURES)
        except OSError:
            pics = ntpath.join(home, "Pictures")
        return ntpath.join(pics, name)
    if system == "linux":
        pics = ""
        try:
            pics = (run or _run)(["xdg-user-dir", "PICTURES"]).strip()
        except (OSError, subprocess.SubprocessError):
            pics = ""
        if not pics or posixpath.normpath(pics) == posixpath.normpath(home):
            pics = posixpath.join(home, "Pictures")
        return posixpath.join(pics, name)
    return posixpath.join(home, "Pictures", name)
