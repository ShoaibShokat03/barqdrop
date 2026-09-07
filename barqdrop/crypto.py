"""Authenticated key agreement + AES-GCM record layer.

Handshake (Noise-XX flavoured, both sides hold a persistent X25519 identity):

    initiator -> responder :  e_i_pub || s_i_pub
    responder -> initiator :  e_r_pub || s_r_pub
    secret = X25519(e_i,e_r) || X25519(e_i,s_r) || X25519(s_i,e_r)
    salt   = the four public keys, in transcript order

A 6-digit SAS (short authentication string) is derived from the same
transcript; users compare it once per device pair, which pins both identity
keys and rules out an active man-in-the-middle. After that the peer's
identity fingerprint is remembered and the code is no longer required.
"""
from __future__ import annotations

import hashlib
import os

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

KEY_LEN = 32
PUB_LEN = 32
TAG_LEN = 16
NONCE_LEN = 12


# --------------------------------------------------------------- identity
def load_or_create_identity(config) -> X25519PrivateKey:
    hexkey = config.get("identity_key") or ""
    try:
        if hexkey:
            return X25519PrivateKey.from_private_bytes(bytes.fromhex(hexkey))
    except Exception:
        pass
    key = X25519PrivateKey.generate()
    raw = key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    config["identity_key"] = raw.hex()
    config.save()
    return key


def public_bytes(key: X25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def fingerprint(pub: bytes) -> str:
    """Short, human-comparable identity fingerprint."""
    return hashlib.sha256(b"barqdrop-id" + pub).hexdigest()[:16]


# --------------------------------------------------------------- key mgmt
def _hkdf(secret: bytes, salt: bytes, info: bytes, length: int = KEY_LEN) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=length,
        salt=salt,
        info=info,
    ).derive(secret)


class SessionKeys:
    """All symmetric material derived from one handshake."""

    def __init__(self, secret: bytes, salt: bytes):
        self.secret = secret
        self.salt = salt
        self.c2s = _hkdf(secret, salt, b"barqdrop/control/c2s")
        self.s2c = _hkdf(secret, salt, b"barqdrop/control/s2c")
        self._data_base = _hkdf(secret, salt, b"barqdrop/data")
        sas_bytes = _hkdf(secret, salt, b"barqdrop/sas", 8)
        self.sas = f"{int.from_bytes(sas_bytes, 'big') % 1_000_000:06d}"

    def data_key(self, stream_id: int, epoch: int = 0) -> bytes:
        """Per-connection payload key.

        `epoch` increments every time a stream reconnects, so a retried
        connection never reuses an AES-GCM (key, nonce) pair.
        """
        label = "%d/%d" % (int(stream_id), int(epoch))
        return _hkdf(self._data_base, self.salt,
                     b"barqdrop/data/stream/" + label.encode())


def handshake_initiator(sock, static: X25519PrivateKey, send_all, recv_exact) -> tuple[SessionKeys, bytes]:
    """Run the initiator side. Returns (keys, peer_static_pub)."""
    eph = X25519PrivateKey.generate()
    e_i, s_i = public_bytes(eph), public_bytes(static)
    send_all(sock, e_i + s_i)
    blob = recv_exact(sock, PUB_LEN * 2)
    e_r, s_r = blob[:PUB_LEN], blob[PUB_LEN:]
    secret = (
        eph.exchange(X25519PublicKey.from_public_bytes(e_r))
        + eph.exchange(X25519PublicKey.from_public_bytes(s_r))
        + static.exchange(X25519PublicKey.from_public_bytes(e_r))
    )
    return SessionKeys(secret, e_i + s_i + e_r + s_r), s_r


def handshake_responder(sock, static: X25519PrivateKey, send_all, recv_exact) -> tuple[SessionKeys, bytes]:
    """Run the responder side. Returns (keys, peer_static_pub)."""
    blob = recv_exact(sock, PUB_LEN * 2)
    e_i, s_i = blob[:PUB_LEN], blob[PUB_LEN:]
    eph = X25519PrivateKey.generate()
    e_r, s_r = public_bytes(eph), public_bytes(static)
    send_all(sock, e_r + s_r)
    secret = (
        eph.exchange(X25519PublicKey.from_public_bytes(e_i))
        + static.exchange(X25519PublicKey.from_public_bytes(e_i))
        + eph.exchange(X25519PublicKey.from_public_bytes(s_i))
    )
    return SessionKeys(secret, e_i + s_i + e_r + s_r), s_i


# ------------------------------------------------------------ record layer
class Sealer:
    """AES-GCM with a monotonic counter nonce (one instance per direction)."""

    __slots__ = ("_aead", "_counter")

    def __init__(self, key: bytes):
        self._aead = AESGCM(key)
        self._counter = 0

    def seal(self, plaintext) -> bytes:
        nonce = self._counter.to_bytes(NONCE_LEN, "big")
        self._counter += 1
        return self._aead.encrypt(nonce, plaintext, None)


class Opener:
    __slots__ = ("_aead", "_counter")

    def __init__(self, key: bytes):
        self._aead = AESGCM(key)
        self._counter = 0

    def open(self, ciphertext) -> bytes:
        nonce = self._counter.to_bytes(NONCE_LEN, "big")
        self._counter += 1
        return self._aead.decrypt(nonce, ciphertext, None)


def random_token(n: int = 16) -> bytes:
    return os.urandom(n)
