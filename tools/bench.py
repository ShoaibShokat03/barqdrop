"""Find the throughput ceiling of each stage in the transfer pipeline.

Isolates disk, AES-GCM, raw sockets and the framing layer, so it is obvious
which one caps a transfer and whether the engine can saturate a given link.

    python tools/bench.py [size_mb]
"""
from __future__ import annotations

import os
import socket
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from barqdrop import protocol                       # noqa: E402
from barqdrop.crypto import Opener, Sealer          # noqa: E402
from barqdrop.util import human_rate, human_size    # noqa: E402

CHUNK = 4 << 20


def report(label, nbytes, seconds, note=""):
    rate = nbytes / max(seconds, 1e-9)
    print("  %-34s %10s  %s" % (label, "%.2fs" % seconds, human_rate(rate).rjust(12)), end="")
    print("   %s" % note if note else "")
    return rate


def bench_disk_read(path, total):
    buf = bytearray(CHUNK)
    view = memoryview(buf)
    start = time.perf_counter()
    with open(path, "rb", buffering=0) as fh:
        while fh.readinto(view):
            pass
    return report("disk read (readinto, 4 MB)", total, time.perf_counter() - start)


def bench_crypto(total):
    key = os.urandom(32)
    plain = bytes(os.urandom(CHUNK))
    rounds = max(1, total // CHUNK)

    sealer = Sealer(key)
    start = time.perf_counter()
    for _ in range(rounds):
        blob = sealer.seal(plain)
    enc = report("AES-GCM encrypt", rounds * CHUNK, time.perf_counter() - start)

    opener = Opener(key)
    sealer2 = Sealer(key)
    blobs = [sealer2.seal(plain) for _ in range(min(rounds, 16))]
    start = time.perf_counter()
    for i in range(rounds):
        opener2 = opener  # counters must stay in step; decrypt in order
        if i < len(blobs):
            opener2.open(blobs[i])
        else:
            break
    dec_bytes = min(rounds, len(blobs)) * CHUNK
    dec = report("AES-GCM decrypt", dec_bytes, time.perf_counter() - start)
    return enc, dec


def _serve_raw(sock, sink):
    conn, _ = sock.accept()
    protocol.tune_socket(conn, 4 << 20)
    buf = bytearray(CHUNK)
    view = memoryview(buf)
    got = 0
    while True:
        n = conn.recv_into(view, CHUNK)
        if not n:
            break
        got += n
    sink.append(got)
    conn.close()


def bench_raw_socket(total):
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    sink = []
    t = threading.Thread(target=_serve_raw, args=(srv, sink), daemon=True)
    t.start()

    payload = memoryview(bytearray(CHUNK))
    sock = socket.create_connection(("127.0.0.1", port))
    protocol.tune_socket(sock, 4 << 20)
    start = time.perf_counter()
    sent = 0
    while sent < total:
        sock.sendall(payload)
        sent += CHUNK
    sock.close()
    t.join(30)
    elapsed = time.perf_counter() - start
    srv.close()
    return report("raw TCP loopback (no crypto)", sent, elapsed)


def _serve_link(srv, encrypt, total, done):
    conn, _ = srv.accept()
    protocol.tune_socket(conn, 4 << 20)
    key = b"k" * 32
    link = protocol.DataLink(conn, CHUNK, opener=Opener(key) if encrypt else None)
    sink = open(os.devnull, "wb")
    try:
        while True:
            kind, _idx, offset, length = link.recv_header()
            if kind == protocol.KIND_END:
                break
            link.recv_payload(sink, 0, length, lambda n: None)
    except Exception:
        pass
    finally:
        sink.close()
        conn.close()
        done.set()


def bench_framed(path, total, encrypt):
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    done = threading.Event()
    t = threading.Thread(target=_serve_link, args=(srv, encrypt, total, done), daemon=True)
    t.start()

    key = b"k" * 32
    sock = socket.create_connection(("127.0.0.1", port))
    protocol.tune_socket(sock, 4 << 20)
    link = protocol.DataLink(sock, CHUNK, sealer=Sealer(key) if encrypt else None)
    fh = open(path, "rb", buffering=0)
    start = time.perf_counter()
    link.send_header(protocol.KIND_SEGMENT, 0, 0, total)
    link.send_payload(fh, 0, total, lambda n: None)
    link.send_header(protocol.KIND_END)
    done.wait(60)
    elapsed = time.perf_counter() - start
    fh.close()
    link.close()
    srv.close()
    label = "DataLink %s (1 stream)" % ("encrypted" if encrypt else "plain    ")
    return report(label, total, elapsed)


def main() -> int:
    size_mb = int(sys.argv[1]) if len(sys.argv) > 1 else 512
    total = size_mb << 20
    tmp = tempfile.mkdtemp(prefix="barqdrop-bench-")
    path = os.path.join(tmp, "payload.bin")
    print("BarqDrop pipeline benchmark  (%s per stage)" % human_size(total))
    print("")
    block = os.urandom(CHUNK)
    with open(path, "wb") as fh:
        for _ in range(total // CHUNK):
            fh.write(block)

    try:
        print("stage")
        bench_disk_read(path, total)          # warm the cache
        disk = bench_disk_read(path, total)   # measure warm
        bench_crypto(total)
        bench_raw_socket(total)
        bench_framed(path, total, False)
        bench_framed(path, total, True)
        print("")
        print("A gigabit link needs 125 MB/s; 2.5 GbE needs 312 MB/s.")
        print("Whichever stage above is slowest is the real ceiling.")
        _ = disk
    finally:
        try:
            os.remove(path)
            os.rmdir(tmp)
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
