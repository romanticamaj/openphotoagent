"""The workspace face key (07 §6.3; ADR-0012): a 256-bit key in the OS keyring seals the optional face backup
(``bio.sealed``, AES-256-GCM, workspace id and format version authenticated). Purge destroys the key
(crypto-shredding), so every sealed file made before it stays closed — even after a new key is made."""

from __future__ import annotations

import base64
import os
import struct

SERVICE = "openphotoagent"
MAGIC = b"OPAB"
VERSION = 1


class KeyMissing(Exception):
    pass


class SealBroken(Exception):
    pass


def key_name(workspace_id: str) -> str:
    return f"face-dek:{workspace_id}"


def _get(workspace_id: str) -> bytes | None:
    import keyring

    v = keyring.get_password(SERVICE, key_name(workspace_id))
    return base64.b64decode(v) if v else None


def new_key(workspace_id: str) -> bytes:
    import keyring

    k = os.urandom(32)
    keyring.set_password(SERVICE, key_name(workspace_id), base64.b64encode(k).decode())
    return k


def destroy_key(workspace_id: str) -> bool:
    import keyring

    if _get(workspace_id) is None:
        return False
    keyring.delete_password(SERVICE, key_name(workspace_id))
    return _get(workspace_id) is None


def _aad(workspace_id: str) -> bytes:
    return MAGIC + struct.pack(">H", VERSION) + workspace_id.encode()


def seal(data: bytes, workspace_id: str) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = _get(workspace_id) or new_key(workspace_id)
    nonce = os.urandom(12)
    return MAGIC + struct.pack(">H", VERSION) + nonce + AESGCM(key).encrypt(nonce, data, _aad(workspace_id))


def unseal(blob: bytes, workspace_id: str) -> bytes:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if blob[:4] != MAGIC:
        raise SealBroken("not a sealed face backup")
    key = _get(workspace_id)
    if key is None:
        raise KeyMissing("the workspace face key was destroyed (purge) or never existed")
    nonce = blob[6:18]
    try:
        return AESGCM(key).decrypt(nonce, blob[18:], _aad(workspace_id))
    except InvalidTag as e:
        raise SealBroken("cannot be opened with this workspace's key") from e
