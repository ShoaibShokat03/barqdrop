"""Partial-file bookkeeping so interrupted transfers resume instead of restart.

Each incoming file is written straight into a preallocated `<name>.barqpart`
next to its final destination. A sidecar JSON records which byte ranges have
landed, so a reconnecting sender is told exactly what is still missing.
"""
from __future__ import annotations

import json
import os
import threading
import time

PART_SUFFIX = ".barqpart"
META_SUFFIX = ".barqpart.json"


def merge_spans(spans):
    """Normalise [start, end] pairs into sorted, non-overlapping spans."""
    out = []
    for start, end in sorted((int(a), int(b)) for a, b in spans if int(b) > int(a)):
        if out and start <= out[-1][1]:
            if end > out[-1][1]:
                out[-1][1] = end
        else:
            out.append([start, end])
    return out


def merge_ranges(ranges):
    """Same, for [offset, length] input. Returns [start, end] spans."""
    return merge_spans([[int(o), int(o) + int(n)] for o, n in ranges if int(n) > 0])


def spans_length(spans) -> int:
    return sum(e - s for s, e in spans)


def missing_ranges(done_spans, size):
    """Complement of `done_spans` inside [0, size), as [offset, length] pairs."""
    result = []
    cursor = 0
    for start, end in merge_spans(done_spans):
        if start > cursor:
            result.append([cursor, start - cursor])
        cursor = max(cursor, end)
        if cursor >= size:
            break
    if cursor < size:
        result.append([cursor, size - cursor])
    return result


def split_ranges(ranges, segment):
    """Chop ranges into work units of at most `segment` bytes."""
    out = []
    for offset, length in ranges:
        pos, left = int(offset), int(length)
        while left > 0:
            take = min(segment, left)
            out.append((pos, take))
            pos += take
            left -= take
    return out


class PartFile:
    """A destination file being assembled from parallel streams."""

    def __init__(self, final_path: str, size: int, flush_interval: float = 2.0):
        self.final_path = final_path
        self.size = int(size)
        self.part_path = final_path + PART_SUFFIX
        self.meta_path = final_path + META_SUFFIX
        self._lock = threading.Lock()
        self._done = []            # [start, end] spans, always merged
        self._received = 0
        self._last_flush = 0.0
        self._flush_interval = flush_interval
        self._handles = []

    # ------------------------------------------------------------- setup
    def prepare(self) -> int:
        """Create/preallocate the part file. Returns bytes already present."""
        os.makedirs(os.path.dirname(self.part_path) or ".", exist_ok=True)
        if os.path.exists(self.part_path):
            self._done = self._load_meta()
        else:
            self._done = []
            open(self.part_path, "wb").close()
        # Preallocate so the filesystem lays the file out once, not per write.
        with open(self.part_path, "r+b") as fh:
            fh.truncate(self.size)
        self._received = spans_length(self._done)
        self._write_meta()
        return self._received

    def _load_meta(self):
        try:
            with open(self.meta_path, "r", encoding="utf-8") as fh:
                meta = json.load(fh)
            if int(meta.get("size", -1)) != self.size:
                return []
            spans = merge_ranges(meta.get("done", []))
            return [[s, min(e, self.size)] for s, e in spans if s < self.size]
        except Exception:
            return []

    def _write_meta(self) -> None:
        try:
            tmp = self.meta_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"size": self.size,
                           "done": [[s, e - s] for s, e in self._done],
                           "updated": time.time()}, fh)
            os.replace(tmp, self.meta_path)
        except Exception:
            pass
        self._last_flush = time.time()

    # ------------------------------------------------------------ handles
    def open_handle(self):
        """One independent file handle per stream (each seeks on its own)."""
        fh = open(self.part_path, "r+b", buffering=0)
        with self._lock:
            self._handles.append(fh)
        return fh

    def close_handles(self) -> None:
        with self._lock:
            handles, self._handles = self._handles, []
        for fh in handles:
            try:
                fh.close()
            except OSError:
                pass

    # ------------------------------------------------------------ progress
    def mark_done(self, offset: int, length: int) -> None:
        with self._lock:
            self._done = merge_spans(self._done + [[offset, offset + length]])
            self._received = spans_length(self._done)
            due = (time.time() - self._last_flush) >= self._flush_interval
            if due:
                self._write_meta()

    @property
    def received(self) -> int:
        with self._lock:
            return self._received

    def missing(self):
        with self._lock:
            return missing_ranges(self._done, self.size)

    def is_complete(self) -> bool:
        with self._lock:
            spans = self._done
            return self.size == 0 or (len(spans) == 1
                                      and spans[0][0] == 0
                                      and spans[0][1] >= self.size)

    # ------------------------------------------------------------ finalise
    def flush(self) -> None:
        with self._lock:
            self._write_meta()

    def finalize(self, unique):
        """Rename the part file into place. Returns the final path used."""
        self.close_handles()
        target = unique(self.final_path)
        for attempt in range(20):
            try:
                os.replace(self.part_path, target)
                break
            except PermissionError:
                time.sleep(0.1)
        else:
            raise OSError("could not finalize " + self.part_path)
        try:
            os.remove(self.meta_path)
        except OSError:
            pass
        return target

    def abandon(self) -> None:
        """Keep the partial data on disk for a later resume."""
        self.close_handles()
        self.flush()


class NullPart:
    """A destination that accepts bytes and throws them away.

    Used by the speed test: it exercises the exact network and crypto path a
    real transfer uses, with no disk on either end, so the number it reports
    is the link's ceiling rather than the storage's.
    """

    class _Sink:
        __slots__ = ()

        def seek(self, offset, whence=0):
            return offset

        def write(self, data):
            return len(data)

        def close(self):
            pass

    def __init__(self, size: int):
        self.size = int(size)
        self.final_path = ""
        self._lock = threading.Lock()
        self._received = 0

    def prepare(self) -> int:
        return 0

    def open_handle(self):
        return self._Sink()

    def close_handles(self) -> None:
        pass

    def mark_done(self, offset: int, length: int) -> None:
        with self._lock:
            self._received += length

    @property
    def received(self) -> int:
        with self._lock:
            return self._received

    def missing(self):
        return [[0, self.size]] if self.size else []

    def is_complete(self) -> bool:
        with self._lock:
            return self._received >= self.size

    def flush(self) -> None:
        pass

    def finalize(self, unique):
        return ""

    def abandon(self) -> None:
        pass
