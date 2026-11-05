"""Windows side of opagent_secure: a protected DACL for the current user and SYSTEM (ADR-0024).

SYSTEM is the counterpart of root, which a POSIX 0600 file cannot keep out either. Administrators are left out: an
elevated admin can always take ownership, but an unelevated process in that group gets no access this way.
The SID comes from the process token, not the default owner, which an elevated token may set to Administrators.
"""

from __future__ import annotations

import ctypes
import msvcrt
import os
import sys
from ctypes import wintypes
from functools import lru_cache
from pathlib import Path

assert sys.platform == "win32"  # tells mypy to skip this module on the other OSes; only imported on Windows

GENERIC_WRITE = 0x40000000
READ_CONTROL = 0x00020000
DELETE = 0x00010000
CREATE_NEW = 1
FILE_ATTRIBUTE_NORMAL = 0x80
INVALID_HANDLE = wintypes.HANDLE(-1).value
SDDL_REVISION_1 = 1
SE_FILE_OBJECT = 1
DACL_INFO = 0x00000004
PROTECTED_DACL_INFO = 0x80000000
TOKEN_QUERY = 0x0008
TOKEN_USER = 1
FILE_PERSISTENT_ACLS = 0x00000008

_adv = ctypes.WinDLL("advapi32", use_last_error=True)
_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_LPVOID_P = ctypes.POINTER(wintypes.LPVOID)
_LPWSTR_P = ctypes.POINTER(wintypes.LPWSTR)
_DWORD_P = ctypes.POINTER(wintypes.DWORD)
_BOOL_P = ctypes.POINTER(wintypes.BOOL)


class _SecurityAttributes(ctypes.Structure):
    _fields_ = [
        ("nLength", wintypes.DWORD),
        ("lpSecurityDescriptor", wintypes.LPVOID),
        ("bInheritHandle", wintypes.BOOL),
    ]


_k32.GetCurrentProcess.restype = wintypes.HANDLE
_k32.GetCurrentProcess.argtypes = []
_k32.CloseHandle.argtypes = [wintypes.HANDLE]
_k32.LocalFree.restype = wintypes.LPVOID
_k32.LocalFree.argtypes = [wintypes.LPVOID]
_k32.CreateFileW.restype = wintypes.HANDLE
_k32.CreateFileW.argtypes = [
    wintypes.LPCWSTR,
    wintypes.DWORD,
    wintypes.DWORD,
    ctypes.POINTER(_SecurityAttributes),
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.HANDLE,
]
_k32.GetVolumePathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
_k32.GetVolumeInformationW.argtypes = [
    wintypes.LPCWSTR,
    wintypes.LPWSTR,
    wintypes.DWORD,
    _DWORD_P,
    _DWORD_P,
    _DWORD_P,
    wintypes.LPWSTR,
    wintypes.DWORD,
]
_adv.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
_adv.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD, _DWORD_P]
_adv.ConvertSidToStringSidW.argtypes = [wintypes.LPVOID, _LPWSTR_P]
_adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
    wintypes.LPCWSTR,
    wintypes.DWORD,
    _LPVOID_P,
    _DWORD_P,
]
_adv.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
    wintypes.LPVOID,
    wintypes.DWORD,
    wintypes.DWORD,
    _LPWSTR_P,
    _DWORD_P,
]
_adv.GetSecurityDescriptorDacl.argtypes = [wintypes.LPVOID, _BOOL_P, _LPVOID_P, _BOOL_P]
_adv.GetSecurityInfo.restype = wintypes.DWORD
_adv.GetSecurityInfo.argtypes = [
    wintypes.HANDLE,
    ctypes.c_int,
    wintypes.DWORD,
    _LPVOID_P,
    _LPVOID_P,
    _LPVOID_P,
    _LPVOID_P,
    _LPVOID_P,
]
_adv.SetNamedSecurityInfoW.restype = wintypes.DWORD
_adv.SetNamedSecurityInfoW.argtypes = [
    wintypes.LPWSTR,
    ctypes.c_int,
    wintypes.DWORD,
    wintypes.LPVOID,
    wintypes.LPVOID,
    wintypes.LPVOID,
    wintypes.LPVOID,
]


def _fail(what: str) -> OSError:
    return ctypes.WinError(ctypes.get_last_error(), what)


@lru_cache(maxsize=1)
def user_sid() -> str:
    tok = wintypes.HANDLE()
    if not _adv.OpenProcessToken(_k32.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(tok)):
        raise _fail("OpenProcessToken")
    try:
        need = wintypes.DWORD()
        _adv.GetTokenInformation(tok, TOKEN_USER, None, 0, ctypes.byref(need))
        buf = ctypes.create_string_buffer(need.value)
        if not _adv.GetTokenInformation(tok, TOKEN_USER, buf, need, ctypes.byref(need)):
            raise _fail("GetTokenInformation")
    finally:
        _k32.CloseHandle(tok)
    sid = ctypes.cast(buf, _LPVOID_P)[0]  # TOKEN_USER starts with SID_AND_ATTRIBUTES.Sid
    out = wintypes.LPWSTR()
    if not _adv.ConvertSidToStringSidW(sid, ctypes.byref(out)):
        raise _fail("ConvertSidToStringSidW")
    try:
        return str(out.value)
    finally:
        _k32.LocalFree(ctypes.cast(out, wintypes.LPVOID))


def _sddl(*, folder: bool) -> str:
    inherit = "OICI" if folder else ""  # a folder hands the same two entries to what is created inside it
    return f"D:P(A;{inherit};FA;;;{user_sid()})(A;{inherit};FA;;;SY)"


def _descriptor(sddl: str) -> wintypes.LPVOID:
    sd = wintypes.LPVOID()
    if not _adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, SDDL_REVISION_1, ctypes.byref(sd), None):
        raise _fail("ConvertStringSecurityDescriptorToSecurityDescriptorW")
    return sd


def has_acls(path: Path) -> bool:
    """False only when the volume says it keeps no ACLs. If it cannot be asked, assume it does and let the read-back
    decide: that fails closed."""
    root = ctypes.create_unicode_buffer(1024)
    if not _k32.GetVolumePathNameW(str(path), root, len(root)):
        return True
    flags = wintypes.DWORD()
    if not _k32.GetVolumeInformationW(root.value, None, 0, None, None, ctypes.byref(flags), None, 0):
        return True
    return bool(flags.value & FILE_PERSISTENT_ACLS)


def _dacl_of(handle: wintypes.HANDLE) -> str:
    sd = wintypes.LPVOID()
    err = _adv.GetSecurityInfo(handle, SE_FILE_OBJECT, DACL_INFO, None, None, None, None, ctypes.byref(sd))
    if err:
        raise ctypes.WinError(err, "GetSecurityInfo")
    out = wintypes.LPWSTR()
    try:
        ok = _adv.ConvertSecurityDescriptorToStringSecurityDescriptorW(
            sd, SDDL_REVISION_1, DACL_INFO, ctypes.byref(out), None
        )
        if not ok:
            raise _fail("ConvertSecurityDescriptorToStringSecurityDescriptorW")
        return str(out.value)
    finally:
        _k32.LocalFree(sd)
        if out:
            _k32.LocalFree(ctypes.cast(out, wintypes.LPVOID))


def create_private(path: Path) -> int:
    """CREATE_NEW with the descriptor in the create call itself, read back from the handle before any byte is written.
    Returns a CRT file descriptor that owns the handle."""
    sd = _descriptor(_sddl(folder=False))
    try:
        sa = _SecurityAttributes(ctypes.sizeof(_SecurityAttributes), sd, False)
        h = _k32.CreateFileW(
            str(path),
            GENERIC_WRITE | READ_CONTROL | DELETE,
            0,
            ctypes.byref(sa),
            CREATE_NEW,
            FILE_ATTRIBUTE_NORMAL,
            None,
        )
    finally:
        _k32.LocalFree(sd)
    if h == INVALID_HANDLE or h is None:
        raise _fail(f"CreateFileW {path}")
    try:
        got = _dacl_of(h)
        if not got.startswith("D:P") or user_sid() not in got:
            raise PermissionError(f"{path}: owner-only ACL did not stick ({got})")
        return msvcrt.open_osfhandle(h, os.O_WRONLY | os.O_BINARY)
    except BaseException:
        _k32.CloseHandle(h)
        path.unlink(missing_ok=True)
        raise


def restrict(path: Path) -> None:
    sd = _descriptor(_sddl(folder=path.is_dir()))
    try:
        present, defaulted = wintypes.BOOL(), wintypes.BOOL()
        dacl = wintypes.LPVOID()
        if not _adv.GetSecurityDescriptorDacl(sd, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted)):
            raise _fail("GetSecurityDescriptorDacl")
        err = _adv.SetNamedSecurityInfoW(
            str(path), SE_FILE_OBJECT, DACL_INFO | PROTECTED_DACL_INFO, None, None, dacl, None
        )
        if err:
            raise ctypes.WinError(err, f"SetNamedSecurityInfoW {path}")
    finally:
        _k32.LocalFree(sd)
