"""BarqDrop transfer engine: discovery, listener, sender, parallel streams.

Throughput design
-----------------
* N independent TCP connections per transfer, each with large socket buffers
  and TCP_NODELAY, pulling from one shared work queue of large segments
  (default 32 MB) -- this both saturates a single link and self-balances when
  one stream stalls.
* Payload moves in large chunks (default 4 MB) through a preallocated
  `readinto`/`recv_into` buffer, so there is no per-chunk allocation.
* The receiver preallocates the destination file and gives every stream its
  own handle, so writes are plain positional writes with no coordination.
* Control traffic is always encrypted; payload encryption (AES-GCM, AES-NI
  accelerated) can be switched off for the last few percent of speed.
"""
from __future__ import annotations

import hmac
import os
import socket
import threading
import time
from collections import deque

from . import crypto, protocol
from .discovery import Discovery
from .protocol import (
    DataLink,
    ControlLink,
    KIND_END,
    KIND_SEGMENT,
    MSG_CANCEL,
    MSG_COMPLETE,
    MSG_DECISION,
    MSG_OFFER,
    MSG_RESULT,
    ROLE_CONTROL,
    ROLE_DATA,
)
from .resume import PartFile, split_ranges
from .util import new_id, now, sanitize_relpath, unique_path

CONNECT_TIMEOUT = 8.0
OFFER_TIMEOUT = 300.0
HANDSHAKE_TIMEOUT = 20.0
MAX_STREAMS = 16
RETRY_LIMIT = 6
RETRY_BACKOFF = 0.6


# --------------------------------------------------------------------- jobs
class Job:
    """Shared progress record for one transfer, in either direction."""

    def __init__(self, direction: str, peer_name: str, files, total: int):
        self.id = new_id()[:12]
        self.direction = direction          # "send" | "recv"
        self.peer_name = peer_name
        self.files = files                  # [{"rel":..., "size":...}]
        self.total = int(total)
        self.state = "connecting"
        self.error = ""
        self.code = ""                      # pairing code while verifying
        self.started = now()
        self.finished = 0.0
        self.cancel = threading.Event()
        self._lock = threading.Lock()
        self._confirmed = 0
        self._inflight = {}
        self._resumed = 0
        self._marks = deque(maxlen=12)      # (time, bytes) for the rate window

    # -- byte accounting --------------------------------------------------
    def set_resumed(self, n: int) -> None:
        with self._lock:
            self._resumed = int(n)

    def add_confirmed(self, n: int) -> None:
        with self._lock:
            self._confirmed += n

    def set_inflight(self, stream_id: int, n: int) -> None:
        with self._lock:
            self._inflight[stream_id] = n

    def bump_inflight(self, stream_id: int, n: int) -> None:
        with self._lock:
            self._inflight[stream_id] = self._inflight.get(stream_id, 0) + n

    @property
    def done_bytes(self) -> int:
        with self._lock:
            return self._resumed + self._confirmed + sum(self._inflight.values())

    def sample(self):
        """Return (rate_bytes_per_s, eta_seconds) over a short sliding window."""
        done = self.done_bytes
        t = now()
        with self._lock:
            self._marks.append((t, done))
            marks = list(self._marks)
        if len(marks) < 2:
            return 0.0, None
        dt = marks[-1][0] - marks[0][0]
        db = marks[-1][1] - marks[0][1]
        if dt <= 0:
            return 0.0, None
        rate = max(0.0, db / dt)
        remaining = max(0, self.total - done)
        eta = remaining / rate if rate > 1 else None
        return rate, eta

    def snapshot(self) -> dict:
        rate, eta = self.sample()
        done = self.done_bytes
        return {
            "id": self.id,
            "direction": self.direction,
            "peer": self.peer_name,
            "state": self.state,
            "error": self.error,
            "code": self.code,
            "files": len(self.files),
            "name": self.files[0]["rel"] if len(self.files) == 1 else "%d items" % len(self.files),
            "total": self.total,
            "done": min(done, self.total) if self.total else done,
            "rate": rate,
            "eta": eta,
            "elapsed": (self.finished or now()) - self.started,
        }


class Offer:
    """An inbound request awaiting the user's yes/no."""

    def __init__(self, peer_name, peer_ip, files, total, code, trusted, fingerprint):
        self.id = new_id()[:12]
        self.peer_name = peer_name
        self.peer_ip = peer_ip
        self.files = files
        self.total = total
        self.code = code
        self.trusted = trusted
        self.fingerprint = fingerprint
        self.decided = threading.Event()
        self.accepted = False
        self.remember = True


class Session:
    """Receiver-side state shared between the control link and data links."""

    def __init__(self, token, keys, encrypt, parts, files, job):
        self.token = token
        self.keys = keys
        self.encrypt = encrypt
        self.parts = parts          # index -> PartFile
        self.files = files
        self.job = job
        self.cancel = threading.Event()
        self.streams = 0
        self.lock = threading.Lock()


# ------------------------------------------------------------------- engine
class Engine:
    def __init__(self, config, emit):
        self.config = config
        self.emit = emit
        self.identity = crypto.load_or_create_identity(config)
        self.identity_pub = crypto.public_bytes(self.identity)
        self.fingerprint = crypto.fingerprint(self.identity_pub)
        self.discovery = Discovery(config, self.fingerprint,
                                   on_change=lambda: self.emit({"type": "peers"}),
                                   on_log=self.log)
        self.jobs: dict[str, Job] = {}
        self.offers: dict[str, Offer] = {}
        self.sessions: dict[bytes, Session] = {}
        self._jobs_lock = threading.Lock()
        self._sessions_lock = threading.Lock()
        self._server: socket.socket | None = None
        self._stop = threading.Event()
        self.listen_error = ""

    # ------------------------------------------------------------ lifecycle
    def start(self, discovery: bool = True) -> None:
        self._stop.clear()
        self._start_server()
        if discovery:
            self.discovery.start()
        threading.Thread(target=self._ticker, name="ticker", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        self.discovery.stop()
        for job in list(self.jobs.values()):
            job.cancel.set()
        if self._server:
            try:
                self._server.close()
            except OSError:
                pass
            self._server = None

    def log(self, text: str) -> None:
        self.emit({"type": "log", "text": text})

    def _ticker(self) -> None:
        """Push progress to the UI a few times a second."""
        while not self._stop.wait(0.25):
            with self._jobs_lock:
                active = [j for j in self.jobs.values()
                          if j.state in ("connecting", "verifying", "waiting", "running")]
            for job in active:
                self.emit({"type": "job", "job": job.snapshot()})

    def _publish(self, job: Job) -> None:
        self.emit({"type": "job", "job": job.snapshot()})

    def _register(self, job: Job) -> None:
        with self._jobs_lock:
            self.jobs[job.id] = job
        self._publish(job)

    def cancel_job(self, job_id: str) -> None:
        job = self.jobs.get(job_id)
        if job:
            job.cancel.set()

    def clear_finished(self) -> None:
        with self._jobs_lock:
            for jid in [j for j, v in self.jobs.items()
                        if v.state in ("done", "failed", "cancelled", "rejected")]:
                self.jobs.pop(jid, None)
        self.emit({"type": "jobs_cleared"})

    # =============================================================== server
    def _start_server(self) -> None:
        port = int(self.config["port"])
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            srv.bind(("0.0.0.0", port))
            srv.listen(64)
        except OSError as exc:
            self.listen_error = str(exc)
            self.log("Cannot listen on port %d: %s" % (port, exc))
            srv.close()
            return
        self._server = srv
        threading.Thread(target=self._accept_loop, name="accept", daemon=True).start()
        self.log("Listening on port %d as '%s'" % (port, self.config["device_name"]))

    def _accept_loop(self) -> None:
        srv = self._server
        while not self._stop.is_set() and srv is not None:
            try:
                sock, addr = srv.accept()
            except OSError:
                break
            protocol.tune_socket(sock, self.config.sock_buf)
            threading.Thread(target=self._handle_conn, args=(sock, addr),
                             name="conn", daemon=True).start()

    def _handle_conn(self, sock: socket.socket, addr) -> None:
        try:
            sock.settimeout(HANDSHAKE_TIMEOUT)
            role = protocol.read_greeting(sock)
            if role == ROLE_CONTROL:
                self._handle_control(sock, addr)
            elif role == ROLE_DATA:
                self._handle_data(sock)
            else:
                raise protocol.ProtocolError("unknown role %d" % role)
        except protocol.PeerClosed:
            pass
        except Exception as exc:
            self.log("Connection from %s failed: %s" % (addr[0], exc))
        finally:
            try:
                sock.close()
            except OSError:
                pass

    # -------------------------------------------------------- inbound offer
    def _handle_control(self, sock: socket.socket, addr) -> None:
        keys, peer_pub = crypto.handshake_responder(
            sock, self.identity, protocol.send_all, protocol.recv_exact)
        link = ControlLink(sock, crypto.Sealer(keys.s2c), crypto.Opener(keys.c2s))
        fp = crypto.fingerprint(peer_pub)
        trusted = self.config.is_trusted(fp)

        msg = link.recv(timeout=30.0)
        if msg.get("t") != MSG_OFFER:
            raise protocol.ProtocolError("expected an offer")

        raw_files = msg.get("files") or []
        files = self._validate_manifest(raw_files)
        total = sum(f["size"] for f in files)
        peer_name = str(msg.get("device") or addr[0])[:64]
        if not files or len(files) != len(raw_files):
            link.send({"t": MSG_DECISION, "accept": False,
                       "reason": "the file list was empty or malformed"})
            return

        offer = Offer(peer_name, addr[0], files, total, keys.sas, trusted, fp)
        auto = trusted and self.config.get("auto_accept_trusted", False)
        self.offers[offer.id] = offer
        if auto:
            offer.accepted = True
            offer.decided.set()
        else:
            self.emit({"type": "offer", "offer": {
                "id": offer.id, "peer": peer_name, "ip": addr[0],
                "files": [{"rel": f["rel"], "size": f["size"]} for f in files][:200],
                "count": len(files), "total": total,
                "code": keys.sas, "trusted": trusted}})

        if not offer.decided.wait(OFFER_TIMEOUT) or not offer.accepted:
            self.offers.pop(offer.id, None)
            self.emit({"type": "offer_closed", "id": offer.id})
            link.send({"t": MSG_DECISION, "accept": False, "reason": "declined"})
            return
        self.offers.pop(offer.id, None)
        self.emit({"type": "offer_closed", "id": offer.id})
        if offer.remember and not trusted:
            self.config.trust(fp, peer_name)

        job = Job("recv", peer_name, files, total)
        job.state = "running"
        self._register(job)

        encrypt = bool(msg.get("encrypt", True))
        token = crypto.random_token(16)
        parts: dict[int, PartFile] = {}
        missing = {}
        resumed = 0
        save_dir = self.config["save_dir"]
        try:
            for f in files:
                target = os.path.join(save_dir, os.path.normpath(f["rel"]))
                part = PartFile(target, f["size"])
                have = part.prepare()
                resumed += have
                parts[f["index"]] = part
                missing[str(f["index"])] = part.missing()
        except Exception as exc:
            job.state = "failed"
            job.error = "Cannot write to %s: %s" % (save_dir, exc)
            self._publish(job)
            link.send({"t": MSG_DECISION, "accept": False, "reason": job.error})
            return

        job.set_resumed(resumed)
        session = Session(token, keys, encrypt, parts, files, job)
        with self._sessions_lock:
            self.sessions[token] = session
        if resumed:
            self.log("Resuming: %s already on disk" % _fmt(resumed))

        link.send({"t": MSG_DECISION, "accept": True, "token": token.hex(),
                   "missing": missing, "encrypt": encrypt})

        try:
            self._receive_loop(link, session, job)
        finally:
            with self._sessions_lock:
                self.sessions.pop(token, None)

    def _receive_loop(self, link: ControlLink, session: Session, job: Job) -> None:
        try:
            while True:
                msg = link.recv(timeout=None)
                kind = msg.get("t")
                if kind == MSG_CANCEL:
                    session.cancel.set()
                    job.state = "cancelled"
                    job.error = str(msg.get("reason") or "cancelled by sender")[:200]
                    break
                if kind == MSG_COMPLETE:
                    self._finish_receive(link, session, job)
                    break
        except (protocol.PeerClosed, OSError, protocol.ProtocolError) as exc:
            if job.state == "running":
                job.state = "failed"
                job.error = "Connection lost: %s (partial data kept, resend to resume)" % exc
        finally:
            session.cancel.set()
            for part in session.parts.values():
                part.abandon()
            job.finished = now()
            self._publish(job)

    def _finish_receive(self, link: ControlLink, session: Session, job: Job) -> None:
        deadline = now() + 30.0
        while now() < deadline:
            with session.lock:
                busy = session.streams
            if not busy:
                break
            time.sleep(0.05)

        incomplete = [session.files[i]["rel"] for i, p in session.parts.items() if not p.is_complete()]
        if incomplete:
            job.state = "failed"
            job.error = "Incomplete: %s (resend to resume)" % ", ".join(incomplete[:3])
            link.send({"t": MSG_RESULT, "ok": False, "error": job.error})
            return
        saved = []
        try:
            for index in sorted(session.parts):
                saved.append(session.parts[index].finalize(unique_path))
        except Exception as exc:
            job.state = "failed"
            job.error = "Could not save file: %s" % exc
            link.send({"t": MSG_RESULT, "ok": False, "error": job.error})
            return
        session.parts = {}
        job.state = "done"
        job.finished = now()
        link.send({"t": MSG_RESULT, "ok": True, "saved": len(saved)})
        self.emit({"type": "received", "paths": saved, "peer": job.peer_name})

    def _validate_manifest(self, raw) -> list:
        files = []
        for i, item in enumerate(raw[:20000]):
            try:
                size = int(item.get("size", 0))
                rel = sanitize_relpath(str(item.get("rel") or "file"))
            except Exception:
                continue
            if size < 0:
                continue
            files.append({"index": i, "rel": rel, "size": size})
        return files

    # --------------------------------------------------------- inbound data
    def _handle_data(self, sock: socket.socket) -> None:
        head = protocol.recv_exact(sock, 20)
        token = head[:16]
        stream_id = int.from_bytes(head[16:18], "big")
        epoch = int.from_bytes(head[18:20], "big")
        with self._sessions_lock:
            session = None
            for tok, sess in self.sessions.items():
                if hmac.compare_digest(tok, token):
                    session = sess
                    break
        if session is None:
            raise protocol.ProtocolError("unknown session token")

        opener = crypto.Opener(session.keys.data_key(stream_id, epoch)) if session.encrypt else None
        link = DataLink(sock, self.config.chunk_bytes, opener=opener)
        sock.settimeout(120.0)
        with session.lock:
            session.streams += 1
        handles = {}
        job = session.job
        try:
            while not session.cancel.is_set():
                kind, index, offset, length = link.recv_header()
                if kind == KIND_END:
                    break
                if kind != KIND_SEGMENT:
                    raise protocol.ProtocolError("bad segment header")
                part = session.parts.get(index)
                if part is None or offset < 0 or length < 0 or offset + length > part.size:
                    raise protocol.ProtocolError("segment out of range")
                fh = handles.get(index)
                if fh is None:
                    fh = handles[index] = part.open_handle()
                job.set_inflight(stream_id, 0)
                link.recv_payload(fh, offset, length,
                                  lambda n: job.bump_inflight(stream_id, n))
                part.mark_done(offset, length)
                job.set_inflight(stream_id, 0)
                job.add_confirmed(length)
        finally:
            job.set_inflight(stream_id, 0)
            for fh in handles.values():
                try:
                    fh.close()
                except OSError:
                    pass
            with session.lock:
                session.streams -= 1

    # -------------------------------------------------------- offer replies
    def respond_offer(self, offer_id: str, accept: bool, remember: bool = True) -> None:
        offer = self.offers.get(offer_id)
        if not offer:
            return
        offer.accepted = accept
        offer.remember = remember
        offer.decided.set()

    # =============================================================== sending
    def send_paths(self, peer: dict, paths) -> str | None:
        items = build_items(paths)
        if not items:
            self.log("Nothing to send.")
            return None
        total = sum(i["size"] for i in items)
        job = Job("send", peer.get("name") or peer.get("ip", "?"), items, total)
        self._register(job)
        threading.Thread(target=self._send_worker, args=(job, peer, items),
                         name="send", daemon=True).start()
        return job.id

    def _send_worker(self, job: Job, peer: dict, items) -> None:
        link = None
        try:
            sock = socket.create_connection((peer["ip"], int(peer["port"])), CONNECT_TIMEOUT)
            protocol.tune_socket(sock, self.config.sock_buf)
            sock.settimeout(HANDSHAKE_TIMEOUT)
            protocol.greet(sock, ROLE_CONTROL)
            keys, peer_pub = crypto.handshake_initiator(
                sock, self.identity, protocol.send_all, protocol.recv_exact)
            link = ControlLink(sock, crypto.Sealer(keys.c2s), crypto.Opener(keys.s2c))
            fp = crypto.fingerprint(peer_pub)

            job.state = "verifying"
            job.code = keys.sas if not self.config.is_trusted(fp) else ""
            self._publish(job)

            encrypt = bool(self.config.get("encrypt_data", True))
            link.send({
                "t": MSG_OFFER,
                "device": self.config["device_name"],
                "encrypt": encrypt,
                "files": [{"rel": i["rel"], "size": i["size"]} for i in items],
            })

            job.state = "waiting"
            self._publish(job)
            decision = self._await_decision(link, job)
            if decision is None:
                job.state = "cancelled"
                job.error = "cancelled before the other device answered"
                try:
                    link.send({"t": MSG_CANCEL, "reason": "cancelled by sender"})
                except OSError:
                    pass
                job.finished = now()
                self._publish(job)
                return
            if decision.get("t") != MSG_DECISION or not decision.get("accept"):
                job.state = "rejected"
                job.error = str(decision.get("reason") or "declined by the other device")[:200]
                job.finished = now()
                self._publish(job)
                return
            self.config.trust(fp, job.peer_name)
            job.code = ""

            token = bytes.fromhex(decision.get("token", ""))
            encrypt = bool(decision.get("encrypt", encrypt))
            missing = decision.get("missing") or {}
            work, resumed = self._build_work(items, missing)
            job.set_resumed(resumed)
            job.state = "running"
            self._publish(job)

            if work:
                self._run_streams(job, peer, items, token, keys, encrypt, work)
            if job.cancel.is_set():
                job.state = "cancelled"
                job.error = job.error or "cancelled"
                try:
                    link.send({"t": MSG_CANCEL, "reason": "cancelled by sender"})
                except OSError:
                    pass
                job.finished = now()
                self._publish(job)
                return
            if job.state == "failed":
                try:
                    link.send({"t": MSG_CANCEL, "reason": job.error})
                except OSError:
                    pass
                job.finished = now()
                self._publish(job)
                return

            link.send({"t": MSG_COMPLETE})
            result = link.recv(timeout=120.0)
            if result.get("ok"):
                job.state = "done"
            else:
                job.state = "failed"
                job.error = str(result.get("error") or "receiver reported an error")[:300]
        except Exception as exc:
            if job.state not in ("cancelled", "rejected"):
                job.state = "failed"
                job.error = str(exc)[:300]
        finally:
            job.finished = now()
            self._publish(job)
            if link is not None:
                link.close()

    def _await_decision(self, link, job):
        """Wait for the receiver's answer, staying responsive to Cancel."""
        deadline = now() + OFFER_TIMEOUT + 30
        while now() < deadline:
            if job.cancel.is_set():
                return None
            if protocol.wait_readable(link.sock, 0.5):
                return link.recv(timeout=30.0)
        raise protocol.ProtocolError("the other device did not answer in time")

    def _build_work(self, items, missing):
        """Turn the receiver's missing-range report into a segment queue."""
        segment = self.config.segment_bytes
        work = []
        resumed = 0
        for index, item in enumerate(items):
            ranges = missing.get(str(index))
            if ranges is None:
                ranges = [[0, item["size"]]]
            have = item["size"] - sum(int(n) for _, n in ranges)
            resumed += max(0, have)
            for offset, length in split_ranges(ranges, segment):
                work.append((index, offset, length))
        # Interleave files so a multi-file transfer keeps every stream busy.
        work.sort(key=lambda w: (w[1], w[0]))
        return work, resumed

    def _run_streams(self, job, peer, items, token, keys, encrypt, work) -> None:
        queue = deque(work)
        qlock = threading.Lock()
        wanted = max(1, min(int(self.config.get("streams", 4)), MAX_STREAMS))
        # One stream per segment at most; tiny transfers do not need the fan-out.
        wanted = max(1, min(wanted, len(work)))
        errors = []

        def take():
            with qlock:
                return queue.popleft() if queue else None

        def give_back(unit):
            with qlock:
                queue.appendleft(unit)

        threads = []
        for sid in range(wanted):
            t = threading.Thread(target=self._stream_worker,
                                 args=(job, peer, items, token, keys, encrypt,
                                       sid, take, give_back, errors),
                                 name="stream-%d" % sid, daemon=True)
            t.start()
            threads.append(t)
        for t in threads:
            t.join()
        if errors and not job.cancel.is_set():
            job.state = "failed"
            job.error = errors[0]

    def _stream_worker(self, job, peer, items, token, keys, encrypt,
                       stream_id, take, give_back, errors) -> None:
        chunk = self.config.chunk_bytes
        handles = {}
        unit = None
        attempts = 0
        epoch = 0
        link = None
        try:
            while not job.cancel.is_set() and not errors:
                if link is None:
                    try:
                        sock = socket.create_connection(
                            (peer["ip"], int(peer["port"])), CONNECT_TIMEOUT)
                    except OSError as exc:
                        attempts += 1
                        if attempts > RETRY_LIMIT:
                            errors.append("Data connection failed: %s" % exc)
                            return
                        time.sleep(RETRY_BACKOFF * attempts)
                        continue
                    protocol.tune_socket(sock, self.config.sock_buf)
                    sock.settimeout(120.0)
                    protocol.greet(sock, ROLE_DATA)
                    sock.sendall(token + stream_id.to_bytes(2, "big")
                                 + epoch.to_bytes(2, "big"))
                    sealer = crypto.Sealer(keys.data_key(stream_id, epoch)) if encrypt else None
                    link = DataLink(sock, chunk, sealer=sealer)

                if unit is None:
                    unit = take()
                    if unit is None:
                        break
                index, offset, length = unit
                fh = handles.get(index)
                if fh is None:
                    fh = handles[index] = open(items[index]["path"], "rb", buffering=0)
                try:
                    job.set_inflight(stream_id, 0)
                    link.send_header(KIND_SEGMENT, index, offset, length)
                    link.send_payload(fh, offset, length,
                                      lambda n: job.bump_inflight(stream_id, n))
                except (OSError, protocol.ProtocolError) as exc:
                    # Whatever was mid-flight is not acknowledged: retry it whole.
                    job.set_inflight(stream_id, 0)
                    link.close()
                    link = None
                    epoch = (epoch + 1) & 0xFFFF
                    attempts += 1
                    if attempts > RETRY_LIMIT:
                        give_back(unit)
                        errors.append("Transfer stream failed: %s" % exc)
                        return
                    self.log("Stream %d retrying (%s)" % (stream_id, exc))
                    time.sleep(RETRY_BACKOFF * attempts)
                    continue
                job.set_inflight(stream_id, 0)
                job.add_confirmed(length)
                unit = None
                attempts = 0
            if unit is not None:
                give_back(unit)
        except Exception as exc:
            errors.append(str(exc)[:200])
            if unit is not None:
                give_back(unit)
        finally:
            job.set_inflight(stream_id, 0)
            for fh in handles.values():
                try:
                    fh.close()
                except OSError:
                    pass
            if link is not None:
                try:
                    if not job.cancel.is_set() and not errors:
                        link.send_header(KIND_END)
                except OSError:
                    pass
                link.close()


# ----------------------------------------------------------------- helpers
def build_items(paths):
    """Expand files and folders into [{path, rel, size}], preserving structure."""
    items = []
    for raw in paths:
        path = os.path.abspath(raw)
        if os.path.isdir(path):
            root_name = os.path.basename(path.rstrip("\\/")) or "folder"
            for base, _dirs, names in os.walk(path):
                for name in names:
                    full = os.path.join(base, name)
                    try:
                        size = os.path.getsize(full)
                    except OSError:
                        continue
                    rel = os.path.relpath(full, path).replace("\\", "/")
                    items.append({"path": full, "rel": root_name + "/" + rel, "size": size})
        elif os.path.isfile(path):
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            items.append({"path": path, "rel": os.path.basename(path), "size": size})
    return items


def _fmt(n):
    from .util import human_size
    return human_size(n)
