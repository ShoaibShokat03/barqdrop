"""Headless end-to-end check of the BarqDrop transfer engine.

Spins up two engines in one process on different ports, sends a generated
file between them, and verifies the bytes that land on disk. Also exercises
the resume path by killing the first attempt part-way through.

    python tools/selftest.py [size_mb]
"""
from __future__ import annotations

import hashlib
import os
import queue
import shutil
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from barqdrop.config import Config          # noqa: E402
from barqdrop.engine import Engine          # noqa: E402
from barqdrop.util import human_rate, human_size  # noqa: E402


NL = chr(10)


def digest(path: str) -> str:
    h = hashlib.blake2b(digest_size=16)
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def first_diff(a_path, b_path):
    """Report where two files start to differ, to localise a bug."""
    size_a, size_b = os.path.getsize(a_path), os.path.getsize(b_path)
    print("  sizes: source=%d received=%d" % (size_a, size_b))
    bad = 0
    with open(a_path, "rb") as fa, open(b_path, "rb") as fb:
        pos = 0
        while True:
            x, y = fa.read(1 << 20), fb.read(1 << 20)
            if not x or not y:
                break
            if x != y:
                for i in range(min(len(x), len(y))):
                    if x[i] != y[i]:
                        if bad < 8:
                            print("  first diff in block at offset %d" % (pos + i))
                        bad += 1
                        break
            pos += len(x)
    print("  differing 1MB blocks: %d" % bad)


def make_config(tmp: str, tag: str, port: int, **extra) -> Config:
    cfg = Config(os.path.join(tmp, "%s.json" % tag))
    cfg.update({"device_name": tag, "port": port, "discovery_port": 45999,
                "save_dir": os.path.join(tmp, tag + "-inbox"), **extra})
    return cfg


class Side:
    """One engine plus the event bookkeeping a UI would normally do."""

    def __init__(self, cfg: Config, auto_accept=True):
        self.cfg = cfg
        self.events: queue.Queue = queue.Queue()
        self.engine = Engine(cfg, self.events.put)
        self.jobs = {}
        self.received = []
        self.auto_accept = auto_accept
        self.codes = []
        self._stop = threading.Event()
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        while not self._stop.is_set():
            try:
                ev = self.events.get(timeout=0.2)
            except queue.Empty:
                continue
            kind = ev.get("type")
            if kind == "job":
                self.jobs[ev["job"]["id"]] = ev["job"]
                if ev["job"]["code"]:
                    self.codes.append(ev["job"]["code"])
            elif kind == "offer":
                self.codes.append(ev["offer"]["code"])
                self.engine.respond_offer(ev["offer"]["id"], self.auto_accept, True)
            elif kind == "received":
                self.received.extend(ev["paths"])

    def close(self):
        self._stop.set()
        self.engine.stop()

    def wait_job(self, job_id, timeout=600):
        deadline = time.time() + timeout
        while time.time() < deadline:
            job = self.jobs.get(job_id)
            if job and job["state"] in ("done", "failed", "cancelled", "rejected"):
                return job
            time.sleep(0.05)
        raise TimeoutError("job %s did not finish" % job_id)


def scenario(name, size_mb, tmp, encrypt, streams, interrupt_at=None):
    print("\n=== %s ===" % name)
    src_dir = os.path.join(tmp, "src")
    os.makedirs(src_dir, exist_ok=True)
    src = os.path.join(src_dir, "payload-%s.bin" % name.replace(" ", "-"))
    if not os.path.exists(src):
        with open(src, "wb") as fh:
            block = os.urandom(1 << 20)
            for _ in range(size_mb):
                fh.write(block)
    want = digest(src)

    a = Side(make_config(tmp, "sender-" + name, 46101, encrypt_data=encrypt, streams=streams))
    b = Side(make_config(tmp, "receiver-" + name, 46102, encrypt_data=encrypt, streams=streams))
    a.engine.start(discovery=False)
    b.engine.start(discovery=False)
    peer = {"name": "receiver", "ip": "127.0.0.1", "port": 46102}
    try:
        if interrupt_at:
            job_id = a.engine.send_paths(peer, [src])
            target = size_mb * (1 << 20) * interrupt_at
            deadline = time.time() + 120
            while time.time() < deadline:
                job = a.jobs.get(job_id)
                if job and job["done"] >= target:
                    break
                if job and job["state"] in ("failed", "done"):
                    break
                time.sleep(0.02)
            a.engine.cancel_job(job_id)
            stopped = a.wait_job(job_id)
            time.sleep(0.5)
            partials = [f for f in os.listdir(b.cfg["save_dir"]) if f.endswith(".barqpart")]
            kept = sum(os.path.getsize(os.path.join(b.cfg["save_dir"], f))
                       for f in os.listdir(b.cfg["save_dir"]) if f.endswith(".barqpart.json"))
            print("  interrupted at %s (state=%s); partials on disk: %s"
                  % (human_size(stopped["done"]), stopped["state"], partials))
            assert stopped["state"] == "cancelled", "expected a cancelled first attempt"
            assert partials and kept, "expected a .barqpart + range map to survive"

        start = time.time()
        job_id = a.engine.send_paths(peer, [src])
        job = a.wait_job(job_id)
        elapsed = max(time.time() - start, 1e-6)
        assert job["state"] == "done", "transfer failed: %s" % job["error"]

        deadline = time.time() + 20
        while not b.received and time.time() < deadline:
            time.sleep(0.05)
        assert b.received, "receiver never reported a saved file"
        got = digest(b.received[-1])
        if got != want:
            first_diff(src, b.received[-1])
            raise AssertionError("CONTENT MISMATCH (%s != %s)" % (got, want))
        print("  %s in %.2fs  ->  %s  [encrypt=%s streams=%d]"
              % (human_size(size_mb << 20), elapsed,
                 human_rate((size_mb << 20) / elapsed), encrypt, streams))
        print("  checksum verified: %s" % got)
        if a.codes and b.codes:
            assert a.codes[0] == b.codes[0], "pairing codes differ!"
            print("  pairing code matched on both sides: %s" % a.codes[0])
        return True
    finally:
        a.close()
        b.close()
        time.sleep(0.3)


def folder_scenario(tmp):
    """Send a nested folder and verify every file lands byte-identical."""
    print(NL + "=== folder ===")
    root = os.path.join(tmp, "tree")
    layout = {"a.bin": 3, "docs/b.bin": 5, "docs/deep/c.bin": 2, "empty.bin": 0}
    want = {}
    for rel, mb in layout.items():
        path = os.path.join(root, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            for _ in range(mb):
                fh.write(os.urandom(1 << 20))
        want[rel] = digest(path)

    a = Side(make_config(tmp, "snd-folder", 46111))
    b = Side(make_config(tmp, "rcv-folder", 46112))
    a.engine.start(discovery=False)
    b.engine.start(discovery=False)
    try:
        job_id = a.engine.send_paths({"name": "r", "ip": "127.0.0.1", "port": 46112}, [root])
        job = a.wait_job(job_id)
        assert job["state"] == "done", "folder transfer failed: %s" % job["error"]
        deadline = time.time() + 20
        while len(b.received) < len(layout) and time.time() < deadline:
            time.sleep(0.05)
        inbox = os.path.join(b.cfg["save_dir"], "tree")
        for rel, want_hash in want.items():
            got_path = os.path.join(inbox, rel.replace("/", os.sep))
            assert os.path.exists(got_path), "missing %s" % rel
            assert digest(got_path) == want_hash, "content mismatch in %s" % rel
        print("  %d files across nested folders verified, structure preserved"
              % len(layout))
        return True
    finally:
        a.close()
        b.close()
        time.sleep(0.3)


def decline_scenario(tmp):
    """A declined offer must fail cleanly and write nothing."""
    print(NL + "=== declined offer ===")
    src = os.path.join(tmp, "decline.bin")
    with open(src, "wb") as fh:
        fh.write(os.urandom(1 << 20))
    a = Side(make_config(tmp, "snd-dec", 46121))
    b = Side(make_config(tmp, "rcv-dec", 46122), auto_accept=False)
    a.engine.start(discovery=False)
    b.engine.start(discovery=False)
    try:
        job_id = a.engine.send_paths({"name": "r", "ip": "127.0.0.1", "port": 46122}, [src])
        job = a.wait_job(job_id, timeout=60)
        assert job["state"] == "rejected", "expected rejection, got %s" % job["state"]
        leftovers = os.listdir(b.cfg["save_dir"]) if os.path.isdir(b.cfg["save_dir"]) else []
        assert not leftovers, "declined transfer left files behind: %s" % leftovers
        print("  sender saw: %s (%s); receiver wrote nothing" % (job["state"], job["error"]))
        return True
    finally:
        a.close()
        b.close()
        time.sleep(0.3)


def main() -> int:
    size_mb = int(sys.argv[1]) if len(sys.argv) > 1 else 128
    tmp = tempfile.mkdtemp(prefix="barqdrop-selftest-")
    print("workspace: %s" % tmp)
    ok = True
    try:
        ok &= scenario("encrypted", size_mb, tmp, True, 4)
        ok &= scenario("plain", size_mb, tmp, False, 4)
        ok &= scenario("single-stream", max(8, size_mb // 4), tmp, True, 1)
        ok &= scenario("resume", max(size_mb * 8, 512), tmp, True, 4, interrupt_at=0.25)
        ok &= folder_scenario(tmp)
        ok &= decline_scenario(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\nALL CHECKS PASSED" if ok else "\nFAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
