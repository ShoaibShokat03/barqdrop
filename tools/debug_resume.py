"""Interrupt a transfer, then audit the partial file against its range map."""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.selftest import Side, make_config  # noqa: E402


def main() -> int:
    size_mb = 256
    tmp = tempfile.mkdtemp(prefix="barqdrop-dbg-")
    src = os.path.join(tmp, "payload.bin")
    with open(src, "wb") as fh:
        block = os.urandom(1 << 20)
        for _ in range(size_mb):
            fh.write(block)

    a = Side(make_config(tmp, "snd", 46201))
    b = Side(make_config(tmp, "rcv", 46202))
    a.engine.start(discovery=False)
    b.engine.start(discovery=False)
    try:
        job_id = a.engine.send_paths({"name": "r", "ip": "127.0.0.1", "port": 46202}, [src])
        target = size_mb * (1 << 20) * 0.3
        while True:
            job = a.jobs.get(job_id)
            if job and (job["done"] >= target or job["state"] in ("done", "failed")):
                break
            time.sleep(0.01)
        a.engine.cancel_job(job_id)
        a.wait_job(job_id)
        time.sleep(1.0)

        inbox = b.cfg["save_dir"]
        meta_path = os.path.join(inbox, "payload.bin.barqpart.json")
        part_path = os.path.join(inbox, "payload.bin.barqpart")
        meta = json.load(open(meta_path, encoding="utf-8"))
        done = meta["done"]
        print("claimed done ranges: %d, bytes: %d of %d"
              % (len(done), sum(n for _, n in done), meta["size"]))

        bad = 0
        with open(src, "rb") as fa, open(part_path, "rb") as fb:
            for offset, length in done:
                pos, left = offset, length
                while left:
                    take = min(1 << 20, left)
                    fa.seek(pos)
                    fb.seek(pos)
                    if fa.read(take) != fb.read(take):
                        print("  MISMATCH inside claimed range at offset %d" % pos)
                        bad += 1
                        if bad > 8:
                            return 1
                    pos += take
                    left -= take
        print("range map is HONEST" if not bad else "range map OVER-CLAIMS (%d bad)" % bad)
        return 0 if not bad else 1
    finally:
        a.close()
        b.close()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
