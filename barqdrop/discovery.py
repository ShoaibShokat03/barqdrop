"""Zero-config LAN discovery over UDP broadcast + multicast.

No internet, no server, no mDNS dependency: every instance announces itself a
few times a second on the local segment and answers direct probes. Both
broadcast (per-interface + global) and a multicast group are used, because
some Wi-Fi drivers and Wi-Fi Direct links drop one but not the other.
"""
from __future__ import annotations

import json
import socket
import struct
import threading
import time

MCAST_GROUP = "239.77.66.55"
ANNOUNCE_INTERVAL = 2.0
PEER_TTL = 9.0

T_ANNOUNCE = "hello"
T_QUERY = "who"
T_BYE = "bye"


def _broadcast_targets() -> list[str]:
    """Per-interface broadcast addresses, plus the global fallback."""
    targets = {"255.255.255.255"}
    try:
        import psutil  # type: ignore

        for addrs in psutil.net_if_addrs().values():
            for a in addrs:
                if a.family != socket.AF_INET or not a.address:
                    continue
                if a.broadcast:
                    targets.add(a.broadcast)
                elif a.netmask:
                    try:
                        ip = struct.unpack("!I", socket.inet_aton(a.address))[0]
                        mask = struct.unpack("!I", socket.inet_aton(a.netmask))[0]
                        bcast = (ip & mask) | (~mask & 0xFFFFFFFF)
                        targets.add(socket.inet_ntoa(struct.pack("!I", bcast)))
                    except OSError:
                        pass
    except Exception:
        pass
    return sorted(targets)


class Discovery:
    """Announces this device and tracks the peers it hears from."""

    def __init__(self, config, identity_fp: str, on_change=None, on_log=None):
        self.config = config
        self.identity_fp = identity_fp
        self.on_change = on_change or (lambda: None)
        self.on_log = on_log or (lambda msg: None)
        self.peers: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._sock: socket.socket | None = None
        self._threads: list[threading.Thread] = []

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        port = int(self.config["discovery_port"])
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        try:
            sock.bind(("0.0.0.0", port))
        except OSError as exc:
            self.on_log("Discovery port %d unavailable: %s" % (port, exc))
            sock.close()
            return
        try:
            mreq = struct.pack("4sl", socket.inet_aton(MCAST_GROUP), socket.INADDR_ANY)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        except OSError:
            pass  # broadcast alone still works
        sock.settimeout(0.5)
        self._sock = sock
        self._stop.clear()
        for target in (self._listen_loop, self._announce_loop, self._prune_loop):
            t = threading.Thread(target=target, name=target.__name__, daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self) -> None:
        self._stop.set()
        if self._sock:
            try:
                self._send({"t": T_BYE, "id": self.config["device_id"]})
            except Exception:
                pass
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    # --------------------------------------------------------------- public
    def snapshot(self) -> list[dict]:
        with self._lock:
            return sorted(self.peers.values(), key=lambda p: p.get("name", "").lower())

    def refresh(self) -> None:
        """Actively probe for peers (used by the Refresh button)."""
        self._send({"t": T_QUERY, "id": self.config["device_id"]})
        self._send(self._announce_payload())

    # -------------------------------------------------------------- private
    def _announce_payload(self) -> dict:
        return {
            "t": T_ANNOUNCE,
            "id": self.config["device_id"],
            "name": self.config["device_name"],
            "port": int(self.config["port"]),
            "fp": self.identity_fp,
            "os": "Windows",
            "v": 1,
        }

    def _send(self, payload: dict, addr: tuple | None = None) -> None:
        sock = self._sock
        if sock is None:
            return
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        port = int(self.config["discovery_port"])
        if addr is not None:
            try:
                sock.sendto(data, addr)
            except OSError:
                pass
            return
        for target in _broadcast_targets() + [MCAST_GROUP]:
            try:
                sock.sendto(data, (target, port))
            except OSError:
                pass

    def _announce_loop(self) -> None:
        while not self._stop.is_set():
            if self.config.get("visible", True):
                try:
                    self._send(self._announce_payload())
                except Exception:
                    pass
            self._stop.wait(ANNOUNCE_INTERVAL)

    def _listen_loop(self) -> None:
        sock = self._sock
        me = self.config["device_id"]
        while not self._stop.is_set() and sock is not None:
            try:
                data, addr = sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                msg = json.loads(data.decode("utf-8"))
            except Exception:
                continue
            if not isinstance(msg, dict) or msg.get("id") == me:
                continue
            kind = msg.get("t")
            if kind == T_QUERY:
                if self.config.get("visible", True):
                    self._send(self._announce_payload(), addr)
            elif kind == T_ANNOUNCE:
                self._remember(msg, addr[0])
            elif kind == T_BYE:
                with self._lock:
                    existed = self.peers.pop(msg.get("id", ""), None)
                if existed:
                    self.on_change()

    def _remember(self, msg: dict, ip: str) -> None:
        pid = str(msg.get("id") or "")
        if not pid:
            return
        peer = {
            "id": pid,
            "name": str(msg.get("name") or ip)[:64],
            "ip": ip,
            "port": int(msg.get("port") or self.config["port"]),
            "fp": str(msg.get("fp") or "")[:32],
            "os": str(msg.get("os") or "")[:32],
            "last_seen": time.time(),
        }
        with self._lock:
            old = self.peers.get(pid)
            self.peers[pid] = peer
        if old is None or old.get("ip") != ip or old.get("name") != peer["name"]:
            self.on_change()

    def _prune_loop(self) -> None:
        while not self._stop.is_set():
            self._stop.wait(1.5)
            cutoff = time.time() - PEER_TTL
            dropped = False
            with self._lock:
                for pid in [p for p, v in self.peers.items() if v["last_seen"] < cutoff]:
                    self.peers.pop(pid, None)
                    dropped = True
            if dropped:
                self.on_change()
