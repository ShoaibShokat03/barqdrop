"""Physical-link awareness and Wi-Fi Direct style ad-hoc links.

BarqDrop never needs the internet: it only needs the two machines to share an
IP subnet. This module reports what that link currently is (so the UI can tell
the user what speed to expect) and can create a direct device-to-device link
when there is no shared network at all.

Direct-link strategies, in order of preference:

1. **Windows Mobile Hotspot** (`NetworkOperatorTetheringManager`, WinRT) --
   this is the modern, supported soft-AP; on Wi-Fi Direct capable adapters
   Windows implements it as a Wi-Fi Direct autonomous GO. Needs the optional
   `winsdk` package.
2. **Legacy hosted network** (`netsh wlan start hostednetwork`) -- still
   present on some drivers.

If neither is available we say so plainly instead of pretending: the user can
join any common Wi-Fi/hotspot and BarqDrop discovers peers there just the same.
"""
from __future__ import annotations

import re
import secrets
import subprocess
import threading
import time

_NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW, keeps console flashes away


def _run(args, timeout=12):
    try:
        proc = subprocess.run(args, capture_output=True, timeout=timeout,
                              creationflags=_NO_WINDOW)
    except Exception as exc:
        return 1, "", str(exc)
    decode = lambda b: b.decode("utf-8", "replace") if b else ""
    return proc.returncode, decode(proc.stdout), decode(proc.stderr)


# --------------------------------------------------------------- link state
def wifi_status() -> dict:
    """Parse `netsh wlan show interfaces` for the active Wi-Fi link."""
    info = {"connected": False, "ssid": "", "rx_mbps": 0, "tx_mbps": 0,
            "radio": "", "signal": ""}
    code, out, _ = _run(["netsh", "wlan", "show", "interfaces"])
    if code != 0 or not out:
        return info
    for line in out.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key, value = key.strip().lower(), value.strip()
        if key == "ssid" and value:
            info["ssid"] = value
            info["connected"] = True
        elif key == "state":
            info["connected"] = value.lower().startswith("connect")
        elif key.startswith("receive rate"):
            info["rx_mbps"] = _to_int(value)
        elif key.startswith("transmit rate"):
            info["tx_mbps"] = _to_int(value)
        elif key.startswith("radio type"):
            info["radio"] = value
        elif key == "signal":
            info["signal"] = value
    return info


def _to_int(value: str) -> int:
    match = re.search(r"\d+", value.replace(",", "."))
    return int(match.group()) if match else 0


def link_summary() -> dict:
    """Describe the fastest usable interface, for the status bar."""
    best = {"kind": "unknown", "name": "", "mbps": 0, "detail": ""}
    try:
        import psutil  # type: ignore

        stats = psutil.net_if_stats()
        addrs = psutil.net_if_addrs()
        for name, st in stats.items():
            if not st.isup or name.lower().startswith("loopback"):
                continue
            if not any(getattr(a, "address", "") and a.family.name == "AF_INET"
                       for a in addrs.get(name, [])):
                continue
            speed = int(st.speed or 0)
            if speed > best["mbps"]:
                lowered = name.lower()
                kind = "ethernet" if ("ethernet" in lowered or "lan" in lowered) else "wifi"
                best = {"kind": kind, "name": name, "mbps": speed, "detail": ""}
    except Exception:
        pass

    wifi = wifi_status()
    if wifi["connected"]:
        rate = max(wifi["rx_mbps"], wifi["tx_mbps"])
        if best["kind"] != "ethernet" or rate > best["mbps"]:
            best = {"kind": "wifi", "name": wifi["ssid"], "mbps": rate or best["mbps"],
                    "detail": wifi["radio"]}
    return best


_cache = {"text": "Checking link...", "at": 0.0}
_cache_lock = threading.Lock()


def cached_link_description(max_age: float = 10.0) -> str:
    """Non-blocking link description for the UI.

    `netsh` can take a second or two, so the probe runs on a worker thread and
    the caller always gets the last known answer immediately.
    """
    with _cache_lock:
        text, age = _cache["text"], time.time() - _cache["at"]
        if age < max_age:
            return text
        _cache["at"] = time.time()

    def worker():
        try:
            result = describe_link()
        except Exception:
            result = "Local network"
        with _cache_lock:
            _cache["text"] = result
            _cache["at"] = time.time()

    threading.Thread(target=worker, name="link-probe", daemon=True).start()
    return text


def describe_link() -> str:
    link = link_summary()
    if link["mbps"] <= 0:
        return "Local network"
    where = link["name"] or ("Ethernet" if link["kind"] == "ethernet" else "Wi-Fi")
    label = "Ethernet" if link["kind"] == "ethernet" else "Wi-Fi"
    return "%s - %s - %d Mbps" % (label, where, link["mbps"])


# ----------------------------------------------------------- direct linking
class DirectLink:
    """Creates a router-free device-to-device Wi-Fi link when possible."""

    def __init__(self):
        self.active = False
        self.method = ""
        self.ssid = ""
        self.passphrase = ""
        self._tethering = None

    # -- capability -------------------------------------------------------
    @staticmethod
    def winrt_available() -> bool:
        try:
            import winsdk.windows.networking.networkoperators  # noqa: F401
            return True
        except Exception:
            return False

    @staticmethod
    def hostednetwork_supported() -> bool:
        code, out, _ = _run(["netsh", "wlan", "show", "drivers"])
        if code != 0:
            return False
        for line in out.splitlines():
            if "hosted network" in line.lower():
                return line.strip().lower().endswith("yes")
        return False

    def capabilities(self) -> dict:
        return {"winrt_hotspot": self.winrt_available(),
                "hosted_network": self.hostednetwork_supported()}

    # -- start / stop -----------------------------------------------------
    def start(self, ssid: str = "", passphrase: str = "") -> tuple[bool, str]:
        """Bring up a direct link. Returns (ok, human readable message)."""
        if self.active:
            return True, "Direct link already running (%s)" % self.ssid
        self.ssid = ssid or ("BarqDrop-" + secrets.token_hex(2).upper())
        self.passphrase = passphrase or secrets.token_urlsafe(9)

        ok, msg = self._start_winrt()
        if ok:
            return True, msg
        ok2, msg2 = self._start_hostednetwork()
        if ok2:
            return True, msg2
        return False, ("Wi-Fi Direct could not be started on this adapter.\n"
                       "%s\n%s\n\n"
                       "Connect both devices to any shared Wi-Fi or phone hotspot "
                       "instead - BarqDrop still transfers directly, peer to peer, "
                       "with no internet involved." % (msg, msg2))

    def _start_winrt(self) -> tuple[bool, str]:
        try:
            from winsdk.windows.networking.networkoperators import (
                NetworkOperatorTetheringManager,
                TetheringOperationalState,
                TetheringOperationStatus,
            )
            from winsdk.windows.networking.connectivity import NetworkInformation
        except Exception:
            return False, "- Mobile Hotspot API unavailable (optional 'winsdk' package not installed)."

        try:
            profile = NetworkInformation.get_internet_connection_profile()
            if profile is None:
                profiles = NetworkInformation.get_connection_profiles()
                profile = profiles[0] if profiles else None
            if profile is None:
                return False, "- Mobile Hotspot needs an active network adapter to share."
            manager = NetworkOperatorTetheringManager.create_from_connection_profile(profile)
            config = manager.get_current_access_point_configuration()
            config.ssid = self.ssid
            config.passphrase = self.passphrase
            manager.configure_access_point_async(config).get()
            if manager.tethering_operational_state == TetheringOperationalState.ON:
                manager.stop_tethering_async().get()
            result = manager.start_tethering_async().get()
            if result.status != TetheringOperationStatus.SUCCESS:
                return False, "- Mobile Hotspot refused to start (status %s)." % result.status
            self._tethering = manager
            self.active = True
            self.method = "Windows Mobile Hotspot (Wi-Fi Direct soft AP)"
            return True, ("Direct Wi-Fi link is up.\n\nNetwork: %s\nPassword: %s\n\n"
                          "Join it from the other device, then both appear in BarqDrop."
                          % (self.ssid, self.passphrase))
        except Exception as exc:
            return False, "- Mobile Hotspot failed: %s" % exc

    def _start_hostednetwork(self) -> tuple[bool, str]:
        if not self.hostednetwork_supported():
            return False, "- Legacy hosted network is not supported by this Wi-Fi driver."
        code, out, err = _run(["netsh", "wlan", "set", "hostednetwork", "mode=allow",
                               "ssid=%s" % self.ssid, "key=%s" % self.passphrase])
        if code != 0:
            return False, "- netsh configuration failed: %s" % (err or out).strip()
        code, out, err = _run(["netsh", "wlan", "start", "hostednetwork"])
        if code != 0 or "started" not in out.lower():
            return False, "- netsh could not start the hosted network: %s" % (err or out).strip()
        self.active = True
        self.method = "Windows hosted network"
        return True, ("Direct Wi-Fi link is up.\n\nNetwork: %s\nPassword: %s\n\n"
                      "Join it from the other device, then both appear in BarqDrop."
                      % (self.ssid, self.passphrase))

    def stop(self) -> tuple[bool, str]:
        messages = []
        if self._tethering is not None:
            try:
                self._tethering.stop_tethering_async().get()
                messages.append("Mobile Hotspot stopped.")
            except Exception as exc:
                messages.append("Could not stop Mobile Hotspot: %s" % exc)
            self._tethering = None
        code, out, err = _run(["netsh", "wlan", "stop", "hostednetwork"])
        if code == 0 and "stopped" in out.lower():
            messages.append("Hosted network stopped.")
        self.active = False
        self.ssid = self.passphrase = self.method = ""
        return True, " ".join(messages) or "Direct link stopped."
