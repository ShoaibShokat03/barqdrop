"""Persistent settings + device identity."""
from __future__ import annotations

import json
import os
import socket
import threading

from .util import app_dir, default_downloads, new_id

CONFIG_PATH = os.path.join(app_dir(), "config.json")

DEFAULTS = {
    "device_id": "",
    "device_name": "",
    "save_dir": "",
    "port": 45878,
    "discovery_port": 45877,
    "streams": 4,               # parallel TCP data connections
    "chunk_mb": 4,              # per-read/record size
    "segment_mb": 32,           # work-queue granularity
    "sock_buf_mb": 4,           # SO_SNDBUF / SO_RCVBUF
    "encrypt_data": True,       # AES-GCM on the file payload
    "auto_accept_trusted": False,
    "visible": True,            # answer discovery probes
    "prefer_wifi_direct": True,
    "trusted": {},              # fingerprint -> {"name":...}
    "identity_key": "",         # hex X25519 private key
    "previous_ssid": "",        # network to rejoin after a direct link
}


class Config:
    def __init__(self, path: str = CONFIG_PATH):
        self.path = path
        self._lock = threading.RLock()
        self._data = dict(DEFAULTS)
        self.load()

    # ---------------------------------------------------------------- io
    def load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict):
                self._data.update({k: v for k, v in loaded.items() if k in DEFAULTS})
        except Exception:
            pass
        changed = False
        if not self._data["device_id"]:
            self._data["device_id"] = new_id()
            changed = True
        if not self._data["device_name"]:
            try:
                self._data["device_name"] = socket.gethostname()
            except Exception:
                self._data["device_name"] = "Windows PC"
            changed = True
        if not self._data["save_dir"]:
            self._data["save_dir"] = default_downloads()
            changed = True
        if changed:
            self.save()

    def save(self) -> None:
        with self._lock:
            tmp = self.path + ".tmp"
            try:
                os.makedirs(os.path.dirname(self.path), exist_ok=True)
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump(self._data, fh, indent=2)
                os.replace(tmp, self.path)
            except Exception:
                pass

    # ------------------------------------------------------------ access
    def __getitem__(self, key: str):
        with self._lock:
            return self._data[key]

    def __setitem__(self, key: str, value) -> None:
        with self._lock:
            self._data[key] = value

    def get(self, key: str, default=None):
        with self._lock:
            return self._data.get(key, default)

    def update(self, values: dict) -> None:
        with self._lock:
            self._data.update({k: v for k, v in values.items() if k in DEFAULTS})
        self.save()

    def as_dict(self) -> dict:
        with self._lock:
            return dict(self._data)

    # ------------------------------------------------------------- trust
    def is_trusted(self, fingerprint: str) -> bool:
        with self._lock:
            return fingerprint in self._data["trusted"]

    def trust(self, fingerprint: str, name: str) -> None:
        with self._lock:
            self._data["trusted"][fingerprint] = {"name": name}
        self.save()

    def untrust(self, fingerprint: str) -> None:
        with self._lock:
            self._data["trusted"].pop(fingerprint, None)
        self.save()

    # ----------------------------------------------------- derived sizes
    @property
    def chunk_bytes(self) -> int:
        return max(64 * 1024, int(self.get("chunk_mb", 4)) * 1024 * 1024)

    @property
    def segment_bytes(self) -> int:
        return max(self.chunk_bytes, int(self.get("segment_mb", 32)) * 1024 * 1024)

    @property
    def sock_buf(self) -> int:
        return max(256 * 1024, int(self.get("sock_buf_mb", 4)) * 1024 * 1024)
