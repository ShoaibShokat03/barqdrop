"""Render assets/barqdrop.ico from the icon drawn in the app itself.

Qt cannot write .ico, so the multi-resolution PNGs are packed into an ICO
container by hand (Vista+ reads PNG-compressed icon entries directly).
"""
from __future__ import annotations

import os
import struct
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QBuffer, QByteArray  # noqa: E402
from PySide6.QtWidgets import QApplication      # noqa: E402

from barqdrop.gui import make_icon              # noqa: E402

SIZES = (16, 24, 32, 48, 64, 128, 256)


def png_bytes(size: int) -> bytes:
    pixmap = make_icon(size).pixmap(size, size)
    store = QByteArray()          # must outlive the buffer that wraps it
    buf = QBuffer(store)
    buf.open(QBuffer.WriteOnly)
    pixmap.save(buf, "PNG")
    buf.close()
    return bytes(store)


def main() -> int:
    app = QApplication([])  # noqa: F841 - a QGuiApplication is required to paint
    images = [(s, png_bytes(s)) for s in SIZES]

    out_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "barqdrop.ico")

    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    entries, blobs = b"", b""
    for size, data in images:
        dim = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        blobs += data
        offset += len(data)
    with open(path, "wb") as fh:
        fh.write(header + entries + blobs)
    print("wrote %s (%d bytes, %d sizes)" % (path, os.path.getsize(path), len(images)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
