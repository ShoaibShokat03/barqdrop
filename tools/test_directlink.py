"""Exercise the direct-link invitation without touching any real network.

The hotspot and the `netsh` join are stubbed, so this checks the part that
can actually go wrong: who is allowed to ask, that the person being asked
gets the final say, and that a refusal explains itself.

    python tools/test_directlink.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from barqdrop import crypto, protocol                  # noqa: E402
from barqdrop import engine as engine_mod              # noqa: E402
from barqdrop.protocol import ControlLink, MSG_LINK, ROLE_CONTROL  # noqa: E402
from tools.selftest import Side, make_config           # noqa: E402

SSID = "BarqDrop-TEST"
PASSPHRASE = "test-passphrase"


class FakeHotspot:
    """Stands in for the Windows soft AP."""

    def __init__(self):
        self.active = False
        self.ssid = ""
        self.passphrase = ""
        self.method = "fake"
        self.stopped = False

    def start(self, ssid="", passphrase=""):
        self.active = True
        self.ssid = SSID
        self.passphrase = PASSPHRASE
        return True, "fake hotspot up"

    def stop(self):
        self.active = False
        self.stopped = True
        return True, "fake hotspot stopped"


def make_side(tmp, tag, port, joins):
    side = Side(make_config(tmp, tag, port))
    side.engine.direct = FakeHotspot()
    # Record what would have been joined instead of reconfiguring Wi-Fi.
    side.engine._join_direct = lambda ssid, passphrase, from_ssid: joins.append(
        (ssid, passphrase, from_ssid))
    side.invites = []
    side.auto_invite = None

    original_pump = side._pump

    def pump():
        while not side._stop.is_set():
            try:
                ev = side.events.get(timeout=0.2)
            except Exception:
                continue
            kind = ev.get("type")
            if kind == "link_invite":
                side.invites.append(ev["invite"])
                if side.auto_invite is not None:
                    side.engine.respond_link_invite(ev["invite"]["id"], side.auto_invite)
            elif kind == "job":
                side.jobs[ev["job"]["id"]] = ev["job"]
            elif kind == "offer":
                side.engine.respond_offer(ev["offer"]["id"], True, True)
            elif kind == "received":
                side.received.extend(ev["paths"])

    side._stop.set()          # stop the inherited pump
    time.sleep(0.25)
    side._stop = threading.Event()
    threading.Thread(target=pump, daemon=True).start()
    _ = original_pump
    return side


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="barqdrop-link-")
    joins = []
    ok = True
    try:
        src = os.path.join(tmp, "pair.bin")
        with open(src, "wb") as fh:
            fh.write(os.urandom(1 << 20))

        a = make_side(tmp, "host", 46151, joins)
        b = make_side(tmp, "guest", 46152, joins)
        a.engine.start(discovery=False)
        b.engine.start(discovery=False)
        peer = {"name": "guest", "ip": "127.0.0.1", "port": 46152, "id": "guest"}
        try:
            # 1. an unpaired device must not be able to move someone's network
            b.auto_invite = True
            accepted, message = a.engine.invite_direct_link(peer)
            assert not accepted, "an unpaired device was allowed to switch networks!"
            assert "not paired" in message, "unhelpful refusal: %s" % message
            assert not b.invites, "an unpaired invite still prompted the user"
            assert not joins, "an unpaired invite still changed the network"
            print("  ok  unpaired device is refused: %s" % message.splitlines()[0])

            # 1b. the same, but from a peer that skips its own trust check --
            # the guard that matters is the one on the machine being asked.
            import socket

            sock = socket.create_connection(("127.0.0.1", 46152), 8)
            protocol.greet(sock, ROLE_CONTROL)
            stranger = crypto.X25519PrivateKey.generate()
            keys, _pub = crypto.handshake_initiator(
                sock, stranger, protocol.send_all, protocol.recv_exact)
            raw = ControlLink(sock, crypto.Sealer(keys.c2s), crypto.Opener(keys.s2c))
            raw.send({"t": MSG_LINK, "device": "Attacker",
                      "ssid": "EvilNet", "pass": "hunter2hunter2"})
            reply = raw.recv(timeout=15.0)
            raw.close()
            assert not reply.get("ok"), "the receiver accepted an unpaired invite!"
            assert not b.invites, "an unpaired invite still prompted the user"
            assert not joins, "an unpaired invite still changed the network"
            print("  ok  receiver refuses a stranger directly: %s"
                  % str(reply.get("reason"))[:60])

            # 2. pair the two devices with a real transfer
            job = a.wait_job(a.engine.send_paths(peer, [src]))
            assert job["state"] == "done", "pairing transfer failed: %s" % job["error"]
            print("  ok  devices paired by a transfer")

            # 3. declining leaves the network alone
            b.auto_invite = False
            accepted, message = a.engine.invite_direct_link(peer)
            assert not accepted, "a declined invite reported success"
            assert b.invites, "the user was never asked"
            assert not joins, "a declined invite still changed the network"
            print("  ok  declining is honoured, nothing changed")

            # 4. accepting hands over the credentials and switches
            b.invites.clear()
            b.auto_invite = True
            accepted, message = a.engine.invite_direct_link(peer)
            assert accepted, "a paired, accepted invite failed: %s" % message
            deadline = time.time() + 10
            while not joins and time.time() < deadline:
                time.sleep(0.05)
            assert joins, "accepted invite never triggered the join"
            ssid, passphrase, from_ssid = joins[-1]
            assert ssid == SSID and passphrase == PASSPHRASE, (
                "wrong credentials handed over: %r" % (joins[-1],))
            invite = b.invites[-1]
            assert invite["ssid"] == SSID, "the prompt showed the wrong network"
            assert invite["peer"], "the prompt did not name the asking device"
            print("  ok  accepted invite hands over %s and switches" % ssid)

            # 5. restoring puts the hotspot away
            a.engine.restore_network()
            assert a.engine.direct.stopped, "restore left the hotspot running"
            print("  ok  restore stops hosting")
        finally:
            a.close()
            b.close()
    except AssertionError as exc:
        print("  FAIL %s" % exc)
        ok = False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("DIRECT LINK CHECKS PASSED" if ok else "DIRECT LINK CHECKS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
