"""Regression test: a transfer must survive a long-idle control channel.

The control channel carries nothing while the data streams work. If the
control socket keeps a short timeout from the handshake, the receiver aborts
mid-transfer -- which is invisible in a fast test and fatal on a real network.

This shrinks the handshake timeout to a fraction of a second, so any transfer
that takes longer than that reproduces the failure immediately.

    python tools/test_longtransfer.py [size_mb]
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from barqdrop import engine as engine_mod          # noqa: E402
from tools.selftest import Side, digest, make_config  # noqa: E402


def main() -> int:
    size_mb = int(sys.argv[1]) if len(sys.argv) > 1 else 256
    # Any transfer slower than this must still succeed.
    engine_mod.HANDSHAKE_TIMEOUT = 0.75

    tmp = tempfile.mkdtemp(prefix="barqdrop-long-")
    try:
        src = os.path.join(tmp, "long.bin")
        with open(src, "wb") as fh:
            block = os.urandom(1 << 20)
            for _ in range(size_mb):
                fh.write(block)
        want = digest(src)

        a = Side(make_config(tmp, "snd-long", 46141))
        b = Side(make_config(tmp, "rcv-long", 46142))
        a.engine.start(discovery=False)
        b.engine.start(discovery=False)
        try:
            started = time.time()
            job_id = a.engine.send_paths(
                {"name": "r", "ip": "127.0.0.1", "port": 46142}, [src])
            job = a.wait_job(job_id, timeout=900)
            elapsed = time.time() - started
            assert elapsed > engine_mod.HANDSHAKE_TIMEOUT * 2, (
                "transfer finished in %.2fs - too fast to exercise an idle "
                "control channel; rerun with a larger size" % elapsed)
            assert job["state"] == "done", (
                "transfer failed after %.1fs: %s" % (elapsed, job["error"]))

            deadline = time.time() + 30
            while not b.received and time.time() < deadline:
                time.sleep(0.05)
            assert b.received, "receiver never reported a saved file"
            assert digest(b.received[-1]) == want, "content mismatch"

            recv_jobs = [j for j in b.jobs.values() if j["direction"] == "recv"]
            assert recv_jobs and all(j["state"] == "done" for j in recv_jobs), (
                "receiver did not report success: %s"
                % [(j["state"], j["error"]) for j in recv_jobs])

            print("control channel stayed idle for %.1fs (handshake timeout %.2fs) "
                  "and the transfer still completed" % (elapsed, engine_mod.HANDSHAKE_TIMEOUT))
            print("LONG-TRANSFER CHECK PASSED")
            return 0
        finally:
            a.close()
            b.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
