"""Diagnose why BarqDrop cannot connect to a peer.

    python tools/probe.py                 check this machine only
    python tools/probe.py 192.168.18.146  also test reaching that device

Checks, in the order they can fail:

  1. this machine's addresses and whether BarqDrop is listening here
  2. Windows Firewall rules that mention BarqDrop
  3. a plain TCP connect to the peer's transfer port
  4. the BarqDrop greeting and encrypted handshake with that peer

Each step reports what it means, so the failing layer is obvious.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from barqdrop import crypto, protocol           # noqa: E402
from barqdrop.config import Config              # noqa: E402
from barqdrop.util import local_ips, primary_ip  # noqa: E402

OK, BAD, WARN = "[ ok ]", "[FAIL]", "[warn]"


def run(args):
    try:
        p = subprocess.run(args, capture_output=True, timeout=15,
                           creationflags=0x08000000)
        return p.returncode, (p.stdout or b"").decode("utf-8", "replace")
    except Exception as exc:
        return 1, str(exc)


def section(title):
    print("")
    print("-- %s %s" % (title, "-" * max(0, 62 - len(title))))


def check_local(cfg):
    section("this machine")
    print("     device name : %s" % cfg["device_name"])
    print("     addresses   : %s" % ", ".join(local_ips() or ["none found"]))
    print("     primary IP  : %s" % primary_ip())
    print("     ports       : TCP %s (transfers), UDP %s (discovery)"
          % (cfg["port"], cfg["discovery_port"]))

    code, out = run(["netstat", "-ano", "-p", "TCP"])
    listening = [l for l in out.splitlines()
                 if ":%s " % cfg["port"] in l and "LISTENING" in l]
    if listening:
        print("%s BarqDrop is listening on TCP %s here" % (OK, cfg["port"]))
    else:
        print("%s nothing is listening on TCP %s here - start BarqDrop on this "
              "machine before asking anyone to send to it" % (WARN, cfg["port"]))


def check_firewall():
    section("windows firewall")
    code, out = run(["netsh", "advfirewall", "firewall", "show", "rule",
                     "name=BarqDrop (in)"])
    if code == 0 and "BarqDrop" in out:
        enabled = [l.strip() for l in out.splitlines() if l.lower().startswith("enabled")]
        print("%s an inbound rule named 'BarqDrop (in)' exists  %s"
              % (OK, enabled[0] if enabled else ""))
    else:
        print("%s no inbound firewall rule named 'BarqDrop (in)' on this machine."
              % WARN)
        print("       If senders time out reaching you, right-click "
              "Allow-Firewall.bat")
        print("       and choose 'Run as administrator'.")

    code, out = run(["netsh", "advfirewall", "show", "currentprofile"])
    for line in out.splitlines():
        if line.lower().startswith("state"):
            print("     current profile firewall state : %s" % line.split()[-1])
            break
    code, out = run(["powershell", "-NoProfile", "-Command",
                     "(Get-NetConnectionProfile).NetworkCategory -join ', '"])
    if code == 0 and out.strip():
        cats = out.strip()
        print("     network category               : %s" % cats)
        if "Public" in cats:
            print("%s a network is set to Public. Windows blocks device-to-device"
                  % WARN)
            print("       traffic there - set it to Private in Windows settings.")


def check_peer(ip, port, cfg):
    section("reaching %s:%s" % (ip, port))
    started = time.time()
    try:
        sock = socket.create_connection((ip, port), 8.0)
    except socket.timeout:
        print("%s TCP connect timed out after %.1fs." % (BAD, time.time() - started))
        print("       Packets are being dropped, not refused - that is a firewall")
        print("       on the RECEIVING machine (%s). On that PC run" % ip)
        print("       Allow-Firewall.bat as administrator, or allow BarqDrop.exe")
        print("       (or pythonw.exe) through Windows Defender Firewall for")
        print("       Private networks.")
        return False
    except OSError as exc:
        code = getattr(exc, "winerror", None) or getattr(exc, "errno", None)
        print("%s TCP connect failed: %s" % (BAD, exc))
        if code in (10061, 111):
            print("       The host answered but nothing is listening on port %d."
                  % port)
            print("       BarqDrop is not running there, or its port differs.")
        elif code in (10065, 10051, 113):
            print("       No route to that address - the two machines are not on")
            print("       the same network segment.")
        return False

    print("%s TCP connect succeeded in %.0f ms" % (OK, (time.time() - started) * 1000))
    try:
        sock.settimeout(10.0)
        protocol.tune_socket(sock, 1 << 20)
        protocol.greet(sock, protocol.ROLE_CONTROL)
        identity = crypto.load_or_create_identity(cfg)
        keys, peer_pub = crypto.handshake_initiator(
            sock, identity, protocol.send_all, protocol.recv_exact)
        print("%s BarqDrop handshake completed" % OK)
        print("     peer fingerprint : %s" % crypto.fingerprint(peer_pub))
        print("     pairing code     : %s  (this changes every connection)" % keys.sas)
        print("     trusted already  : %s"
              % ("yes" if cfg.is_trusted(crypto.fingerprint(peer_pub)) else "no"))
        print("")
        print("     Everything below the application layer is fine. If a real")
        print("     transfer still fails, the message shown in the transfer row")
        print("     is the actual cause.")
        return True
    except socket.timeout:
        print("%s connected, but the peer never answered the handshake." % BAD)
        print("       Something is listening on port %d that is not this version"
              % port)
        print("       of BarqDrop. Check both sides are running the same build.")
        return False
    except Exception as exc:
        print("%s handshake failed: %s" % (BAD, exc))
        return False
    finally:
        try:
            sock.close()
        except OSError:
            pass


def main() -> int:
    cfg = Config()
    print("BarqDrop connectivity probe")
    check_local(cfg)
    check_firewall()
    ok = True
    if len(sys.argv) > 1:
        ip = sys.argv[1]
        port = int(sys.argv[2]) if len(sys.argv) > 2 else int(cfg["port"])
        ok = check_peer(ip, port, cfg)
    else:
        section("peer")
        print("     No peer given. Re-run with the IP shown on the device card,")
        print("     for example:  Run.bat --probe 192.168.18.146")
    print("")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
