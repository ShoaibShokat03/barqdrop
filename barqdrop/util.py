"""Small helpers shared across BarqDrop."""
from __future__ import annotations

import os
import socket
import string
import sys
import time
import uuid

_SIZE_UNITS = ("B", "KB", "MB", "GB", "TB", "PB")


def human_size(n: float) -> str:
    n = float(max(n, 0))
    for unit in _SIZE_UNITS:
        if n < 1024.0 or unit == _SIZE_UNITS[-1]:
            if unit == "B":
                return f"{int(n)} {unit}"
            return f"{n:.2f} {unit}"
        n /= 1024.0
    return f"{n:.2f} PB"


def human_rate(bps: float) -> str:
    return human_size(bps) + "/s"


def human_eta(seconds: float) -> str:
    if seconds is None or seconds != seconds or seconds < 0 or seconds > 86400 * 7:
        return "--:--"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def new_id() -> str:
    return uuid.uuid4().hex


def now() -> float:
    return time.monotonic()


_SAFE = set(string.ascii_letters + string.digits + " ._-()[]{}'!#$%&+,;=@^`~")


def sanitize_component(name: str) -> str:
    """Make a single path component safe to create on Windows."""
    name = name.replace("\\", "/").split("/")[-1].strip()
    cleaned = "".join(ch if ch in _SAFE else "_" for ch in name).strip(" .")
    if not cleaned:
        cleaned = "unnamed"
    stem = cleaned.split(".")[0].upper()
    reserved = {"CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "COM5",
                "COM6", "COM7", "COM8", "COM9", "LPT1", "LPT2", "LPT3", "LPT4",
                "LPT5", "LPT6", "LPT7", "LPT8", "LPT9"}
    if stem in reserved:
        cleaned = "_" + cleaned
    return cleaned[:180]


def sanitize_relpath(rel: str) -> str:
    """Sanitize an untrusted relative path from the network (no traversal)."""
    rel = rel.replace("\\", "/")
    parts = [p for p in rel.split("/") if p not in ("", ".", "..")]
    parts = [sanitize_component(p) for p in parts]
    if not parts:
        parts = ["unnamed"]
    return "/".join(parts[-40:])


def unique_path(path: str) -> str:
    """Return `path`, or `path (2)`, `path (3)`... if it already exists."""
    if not os.path.exists(path):
        return path
    root, ext = os.path.splitext(path)
    for i in range(2, 10000):
        cand = f"{root} ({i}){ext}"
        if not os.path.exists(cand):
            return cand
    return f"{root} ({new_id()[:8]}){ext}"


def local_ips() -> list[str]:
    """Best-effort list of this machine's IPv4 addresses."""
    ips: set[str] = set()
    try:
        import psutil  # type: ignore

        for addrs in psutil.net_if_addrs().values():
            for a in addrs:
                if a.family == socket.AF_INET and a.address and not a.address.startswith("169.254."):
                    ips.add(a.address)
    except Exception:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except Exception:
        pass
    ips.discard("0.0.0.0")
    return sorted(ips)


def primary_ip() -> str:
    """The IP that would be used to reach the local network."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def app_dir() -> str:
    """Per-user data directory for settings and state."""
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "BarqDrop")
    os.makedirs(d, exist_ok=True)
    return d


def default_downloads() -> str:
    d = os.path.join(os.path.expanduser("~"), "Downloads", "BarqDrop")
    return d


def resource_path(rel: str) -> str:
    """Resolve a bundled resource both in dev and inside a PyInstaller exe."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, rel)
