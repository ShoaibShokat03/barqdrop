"""Wire protocol: socket tuning, framing, control + data channels.

Two connection roles share one listening port:

  ROLE_CONTROL  handshake -> encrypted JSON messages (offer, accept, resume...)
  ROLE_DATA     handshake-free; authenticated by the session token issued on
                the control channel. Carries the raw file payload.

Data framing (per connection):

    header  : "S" | file_index u32 | offset u64 | length u64      (21 bytes)
              "E" | 20 zero bytes                                 (end of stream)
    payload : `length` bytes following an "S" header

With encryption on, every header and every payload chunk is wrapped in an
AES-GCM record: u32 ciphertext length + ciphertext. With it off the bytes go
on the wire as-is, which is the fastest path available.
"""
from __future__ import annotations

import json
import select
import socket
import struct

MAGIC = b"BQD1"
VERSION = 1

ROLE_CONTROL = 1
ROLE_DATA = 2

HEADER_FMT = "!cIQQ"
HEADER_LEN = struct.calcsize(HEADER_FMT)  # 21
KIND_SEGMENT = b"S"
KIND_END = b"E"

MAX_CONTROL_MSG = 8 * 1024 * 1024
MAX_RECORD = 64 * 1024 * 1024
TAG_SLACK = 64          # room for the AES-GCM tag on top of a full chunk

# control message types
MSG_OFFER = "offer"
MSG_DECISION = "decision"
MSG_COMPLETE = "complete"
MSG_RESULT = "result"
MSG_CANCEL = "cancel"
MSG_PING = "ping"
MSG_LINK = "link"               # "join my direct Wi-Fi link"
MSG_LINK_RESULT = "link_result"


class ProtocolError(Exception):
    pass


class PeerClosed(ProtocolError):
    pass


# --------------------------------------------------------------- socket io
def tune_socket(sock: socket.socket, buf: int) -> None:
    """Apply the throughput-oriented socket options."""
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except OSError:
        pass
    for opt in (socket.SO_SNDBUF, socket.SO_RCVBUF):
        try:
            sock.setsockopt(socket.SOL_SOCKET, opt, buf)
        except OSError:
            pass
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    except OSError:
        pass


def wait_readable(sock: socket.socket, timeout: float) -> bool:
    """True when the socket has data ready within `timeout` seconds."""
    ready, _, _ = select.select([sock], [], [], timeout)
    return bool(ready)


def send_all(sock: socket.socket, data) -> None:
    sock.sendall(data)


def recv_exact(sock: socket.socket, n: int) -> bytes:
    """Read exactly n bytes or raise."""
    if n == 0:
        return b""
    buf = bytearray(n)
    view = memoryview(buf)
    got = 0
    while got < n:
        r = sock.recv_into(view[got:], n - got)
        if not r:
            raise PeerClosed("connection closed by peer")
        got += r
    return bytes(buf)


def write_all(fh, data) -> None:
    """Raw (unbuffered) handles may short-write; keep going until done."""
    view = memoryview(data)
    total = len(view)
    written = 0
    while written < total:
        n = fh.write(view[written:])
        if n is None:
            raise ProtocolError("file handle would block")
        if n == 0:
            raise ProtocolError("file write made no progress")
        written += n


def recv_into_exact(sock: socket.socket, view: memoryview, n: int) -> None:
    """Fill the first n bytes of `view` (no intermediate allocation)."""
    got = 0
    while got < n:
        r = sock.recv_into(view[got:n], n - got)
        if not r:
            raise PeerClosed("connection closed by peer")
        got += r


# ------------------------------------------------------------ control link
class ControlLink:
    """Length-prefixed, AES-GCM encrypted JSON messages."""

    def __init__(self, sock: socket.socket, sealer, opener):
        self.sock = sock
        self._sealer = sealer
        self._opener = opener

    def send(self, msg: dict) -> None:
        payload = json.dumps(msg, separators=(",", ":")).encode("utf-8")
        blob = self._sealer.seal(payload)
        self.sock.sendall(struct.pack("!I", len(blob)) + blob)

    def recv(self, timeout=None) -> dict:
        """Read one message. `timeout=None` blocks until it arrives.

        The timeout is always applied, never inherited: a socket that still
        carried the handshake timeout would otherwise abort a long transfer
        while the control channel is legitimately idle.
        """
        old = self.sock.gettimeout()
        self.sock.settimeout(timeout)
        try:
            (n,) = struct.unpack("!I", recv_exact(self.sock, 4))
            if n > MAX_CONTROL_MSG:
                raise ProtocolError("control message too large (%d bytes)" % n)
            blob = recv_exact(self.sock, n)
        finally:
            try:
                self.sock.settimeout(old)
            except OSError:
                pass
        msg = json.loads(self._opener.open(blob).decode("utf-8"))
        if not isinstance(msg, dict):
            raise ProtocolError("malformed control message")
        return msg

    def close(self) -> None:
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


# --------------------------------------------------------------- data link
class DataLink:
    """One parallel stream. Encryption is optional and per-connection."""

    def __init__(self, sock: socket.socket, chunk: int, sealer=None, opener=None):
        self.sock = sock
        self.chunk = chunk
        self._sealer = sealer
        self._opener = opener
        self.encrypted = sealer is not None or opener is not None
        self._buf = bytearray(chunk)
        self._view = memoryview(self._buf)
        # Ciphertext scratch, reused for every record: AES-GCM adds a 16-byte
        # tag, and headers are far smaller than a chunk.
        self._ct = bytearray(chunk + TAG_SLACK) if self.encrypted else b""
        self._ctview = memoryview(self._ct) if self.encrypted else None
        self._len = bytearray(4)

    # -- records ----------------------------------------------------------
    def _send_record(self, data) -> None:
        """Encrypt and frame one record.

        The length prefix goes out as its own tiny write rather than being
        concatenated with the ciphertext: joining them would copy the whole
        multi-megabyte payload again for the sake of four bytes.
        """
        blob = self._sealer.seal(data)
        struct.pack_into("!I", self._len, 0, len(blob))
        self.sock.sendall(self._len)
        self.sock.sendall(blob)

    def _recv_record(self):
        """Read one record into the reusable buffer and decrypt it."""
        (n,) = struct.unpack("!I", recv_exact(self.sock, 4))
        if n > MAX_RECORD:
            raise ProtocolError("record too large (%d bytes)" % n)
        if n <= len(self._ct):
            recv_into_exact(self.sock, self._ctview, n)
            return self._opener.open(self._ctview[:n])
        # Larger than our scratch (a peer with a bigger chunk size): fall back.
        return self._opener.open(recv_exact(self.sock, n))

    # -- headers ----------------------------------------------------------
    def send_header(self, kind, file_index=0, offset=0, length=0) -> None:
        head = struct.pack(HEADER_FMT, kind, file_index, offset, length)
        if self.encrypted:
            self._send_record(head)
        else:
            self.sock.sendall(head)

    def recv_header(self):
        head = self._recv_record() if self.encrypted else recv_exact(self.sock, HEADER_LEN)
        if len(head) != HEADER_LEN:
            raise ProtocolError("short header")
        return struct.unpack(HEADER_FMT, head)

    # -- payload ----------------------------------------------------------
    def send_payload(self, fh, offset: int, length: int, progress) -> None:
        """Stream `length` bytes of `fh` starting at `offset`."""
        fh.seek(offset)
        remaining = length
        view = self._view
        while remaining:
            want = min(self.chunk, remaining)
            got = fh.readinto(view[:want])
            if not got:
                raise ProtocolError("unexpected end of local file")
            if self.encrypted:
                self._send_record(view[:got])
            else:
                self.sock.sendall(view[:got])
            remaining -= got
            progress(got)

    def recv_payload(self, fh, offset: int, length: int, progress) -> None:
        """Receive `length` bytes and write them at `offset` in `fh`."""
        fh.seek(offset)
        remaining = length
        view = self._view
        while remaining:
            if self.encrypted:
                data = self._recv_record()
                if not data or len(data) > remaining:
                    raise ProtocolError("record size out of range")
                write_all(fh, data)
                n = len(data)
            else:
                n = min(self.chunk, remaining)
                recv_into_exact(self.sock, view, n)
                write_all(fh, view[:n])
            remaining -= n
            progress(n)

    def close(self) -> None:
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


# ------------------------------------------------------------ connect/greet
def greet(sock: socket.socket, role: int) -> None:
    sock.sendall(MAGIC + bytes([VERSION, role]))


def read_greeting(sock: socket.socket) -> int:
    blob = recv_exact(sock, len(MAGIC) + 2)
    if blob[: len(MAGIC)] != MAGIC:
        raise ProtocolError("not a BarqDrop connection")
    version, role = blob[-2], blob[-1]
    if version != VERSION:
        raise ProtocolError("unsupported protocol version %d" % version)
    return role
