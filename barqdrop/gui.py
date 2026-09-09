"""BarqDrop desktop UI (PySide6)."""
from __future__ import annotations

import os
import queue
import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap, QPolygonF
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QMainWindow, QMessageBox, QProgressBar, QPushButton, QScrollArea,
    QSizePolicy, QSpinBox, QVBoxLayout, QWidget,
)

from . import theme
from .config import Config
from .engine import Engine, build_items
from .link import DirectLink, cached_link_description
from .util import human_eta, human_rate, human_size, primary_ip, resource_path

APP_TITLE = "BarqDrop"
SPEEDTEST_MB = 256      # generated payload for the link speed test


# ------------------------------------------------------------------- icon
def make_icon(size: int = 256) -> QIcon:
    """A lightning bolt on a dark rounded tile, drawn at runtime."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(theme.PANEL_ALT))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(0, 0, size, size, size * 0.22, size * 0.22)
    s = size / 100.0
    bolt = QPolygonF([QPointF(56 * s, 8 * s), QPointF(26 * s, 56 * s),
                      QPointF(46 * s, 56 * s), QPointF(40 * s, 92 * s),
                      QPointF(74 * s, 42 * s), QPointF(53 * s, 42 * s),
                      QPointF(63 * s, 8 * s)])
    p.setBrush(QColor(theme.ACCENT))
    p.drawPolygon(bolt)
    p.end()
    return QIcon(pm)


def app_icon() -> QIcon:
    """The packaged .ico when present, otherwise the drawn fallback."""
    path = resource_path(os.path.join("assets", "barqdrop.ico"))
    if os.path.exists(path):
        icon = QIcon(path)
        if not icon.isNull():
            return icon
    return make_icon()


def _panel() -> QFrame:
    f = QFrame()
    f.setObjectName("Panel")
    return f


def _head(text: str) -> QLabel:
    lbl = QLabel(text.upper())
    lbl.setObjectName("SectionHead")
    return lbl


# ------------------------------------------------------------ device card
class DeviceCard(QFrame):
    """One discovered peer; also a drop target for files."""

    def __init__(self, peer: dict, on_send, on_drop, on_speedtest=None):
        super().__init__()
        self.peer = peer
        self.on_send = on_send
        self.on_drop = on_drop
        self.on_speedtest = on_speedtest
        self.setObjectName("Panel")
        self.setAcceptDrops(True)
        self.setCursor(Qt.PointingHandCursor)

        row = QHBoxLayout(self)
        row.setContentsMargins(14, 12, 12, 12)
        row.setSpacing(12)

        avatar = QLabel(peer["name"][:1].upper() or "?")
        avatar.setFixedSize(38, 38)
        avatar.setAlignment(Qt.AlignCenter)
        avatar.setStyleSheet(
            "background: %s; color: #201700; border-radius: 19px; font-weight: 700;"
            " font-size: 16px;" % theme.ACCENT)
        row.addWidget(avatar)

        col = QVBoxLayout()
        col.setSpacing(2)
        name = QLabel(peer["name"])
        name.setStyleSheet("font-weight: 600; font-size: 14px;")
        name.setToolTip(peer["name"])
        sub = QLabel("%s  -  port %s" % (peer["ip"], peer["port"]))
        sub.setObjectName("Muted")
        for lbl in (name, sub):
            lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        col.addWidget(name)
        col.addWidget(sub)
        row.addLayout(col, 1)

        if self.on_speedtest is not None:
            self.speed_btn = QPushButton("Speed")
            self.speed_btn.setObjectName("Ghost")
            self.speed_btn.setCursor(Qt.PointingHandCursor)
            self.speed_btn.setToolTip(
                "Measure the real link to this device.\nSends generated data "
                "that is discarded on arrival - nothing touches either disk.")
            self.speed_btn.clicked.connect(lambda: self.on_speedtest(self.peer))
            row.addWidget(self.speed_btn)

        self.send_btn = QPushButton("Send")
        self.send_btn.setObjectName("Primary")
        self.send_btn.setCursor(Qt.PointingHandCursor)
        self.send_btn.clicked.connect(lambda: self.on_send(self.peer))
        row.addWidget(self.send_btn)

    # drag files straight onto a device
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.setStyleSheet("QFrame#Panel { border: 1px solid %s; }" % theme.ACCENT)

    def dragLeaveEvent(self, event):
        self.setStyleSheet("")

    def dropEvent(self, event):
        self.setStyleSheet("")
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.on_drop(self.peer, paths)
            event.acceptProposedAction()


# ---------------------------------------------------------- transfer row
STATE_LABELS = {
    "connecting": ("Connecting", theme.MUTED),
    "verifying": ("Verify the code", theme.ACCENT),
    "waiting": ("Waiting for the other device", theme.MUTED),
    "running": ("Transferring", theme.ACCENT),
    "done": ("Completed", theme.GOOD),
    "failed": ("Failed", theme.BAD),
    "cancelled": ("Cancelled", theme.BAD),
    "rejected": ("Declined", theme.BAD),
}


class TransferRow(QFrame):
    def __init__(self, job: dict, on_cancel):
        super().__init__()
        self.job_id = job["id"]
        self.on_cancel = on_cancel
        self.setObjectName("Panel")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 11, 14, 12)
        outer.setSpacing(7)

        top = QHBoxLayout()
        top.setSpacing(10)
        self.arrow = QLabel("^" if job["direction"] == "send" else "v")
        self.arrow.setStyleSheet("color: %s; font-weight: 700;" % theme.ACCENT)
        self.title = QLabel()
        self.title.setStyleSheet("font-weight: 600;")
        self.title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.state = QLabel()
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setObjectName("Ghost")
        self.cancel_btn.setCursor(Qt.PointingHandCursor)
        self.cancel_btn.clicked.connect(lambda: self.on_cancel(self.job_id))
        top.addWidget(self.arrow)
        top.addWidget(self.title, 1)
        top.addWidget(self.state)
        top.addWidget(self.cancel_btn)
        outer.addLayout(top)

        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        outer.addWidget(self.bar)

        self.detail = QLabel()
        self.detail.setObjectName("Muted")
        self.detail.setWordWrap(True)
        outer.addWidget(self.detail)

        self.update_job(job)

    def update_job(self, job: dict) -> None:
        direction = "to" if job["direction"] == "send" else "from"
        self.title.setText("%s  %s  %s" % (job["name"], direction, job["peer"]))
        label, colour = STATE_LABELS.get(job["state"], (job["state"], theme.MUTED))
        self.state.setText(label)
        self.state.setStyleSheet("color: %s; font-weight: 600;" % colour)

        total, done = job["total"], job["done"]
        pct = (done / total) if total else (1.0 if job["state"] == "done" else 0.0)
        if job["state"] == "done":
            pct = 1.0
        self.bar.setValue(int(max(0.0, min(1.0, pct)) * 1000))

        finished = job["state"] in ("done", "failed", "cancelled", "rejected")
        self.cancel_btn.setVisible(not finished)
        chunk = theme.GOOD if job["state"] == "done" else (
            theme.BAD if job["state"] in ("failed", "cancelled", "rejected") else theme.ACCENT)
        self.bar.setStyleSheet("QProgressBar::chunk { background: %s; border-radius: 5px; }" % chunk)

        if job["state"] == "verifying" and job["code"]:
            self.detail.setText("Pairing code %s - it must match on the other device."
                                % job["code"])
        elif job["state"] == "running":
            self.detail.setText("%s of %s  -  %s  -  %s left" % (
                human_size(done), human_size(total),
                human_rate(job["rate"]), human_eta(job["eta"])))
        elif job["state"] == "done":
            elapsed = max(job["elapsed"], 0.001)
            summary = "%s in %s  -  average %s" % (
                human_size(total), human_eta(elapsed), human_rate(total / elapsed))
            if job.get("speedtest"):
                mbps = (total / elapsed) * 8 / 1_000_000
                summary += "   (%.0f Mbps link)" % mbps
            self.detail.setText(summary)
        elif job["error"]:
            self.detail.setText(job["error"])
        else:
            self.detail.setText("%s  -  %d file(s)" % (human_size(total), job["files"]))


# ------------------------------------------------------------ drop area
class DropArea(QFrame):
    def __init__(self, on_paths):
        super().__init__()
        self.on_paths = on_paths
        self.setObjectName("Panel")
        self.setAcceptDrops(True)
        self.setMinimumHeight(120)
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignCenter)
        lay.setSpacing(4)
        bolt = QLabel("+")
        bolt.setAlignment(Qt.AlignCenter)
        bolt.setStyleSheet("font-size: 30px; color: %s;" % theme.ACCENT)
        title = QLabel("Drop files or folders here")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-weight: 600; font-size: 14px;")
        hint = QLabel("or use the buttons below - drop straight onto a device to send instantly")
        hint.setAlignment(Qt.AlignCenter)
        hint.setObjectName("Muted")
        lay.addWidget(bolt)
        lay.addWidget(title)
        lay.addWidget(hint)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.setStyleSheet("QFrame#Panel { border: 1px dashed %s; }" % theme.ACCENT)

    def dragLeaveEvent(self, event):
        self.setStyleSheet("")

    def dropEvent(self, event):
        self.setStyleSheet("")
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.on_paths(paths)
            event.acceptProposedAction()


# --------------------------------------------------------- incoming offer
class OfferDialog(QDialog):
    def __init__(self, offer: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Incoming transfer")
        self.setMinimumWidth(420)
        self.setModal(True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(12)

        title = QLabel("%s wants to send you %d item(s)" % (offer["peer"], offer["count"]))
        title.setObjectName("Title")
        title.setWordWrap(True)
        lay.addWidget(title)

        sub = QLabel("%s  -  from %s" % (human_size(offer["total"]), offer["ip"]))
        sub.setObjectName("Muted")
        lay.addWidget(sub)

        names = QListWidget()
        names.setMaximumHeight(120)
        for f in offer["files"][:50]:
            names.addItem("%s   (%s)" % (f["rel"], human_size(f["size"])))
        if offer["count"] > 50:
            names.addItem("... and %d more" % (offer["count"] - 50))
        lay.addWidget(names)

        if not offer["trusted"]:
            box = _panel()
            bl = QVBoxLayout(box)
            bl.setContentsMargins(14, 12, 14, 12)
            cap = QLabel("Confirm this code matches the sender's screen")
            cap.setObjectName("Muted")
            cap.setAlignment(Qt.AlignCenter)
            code = QLabel(offer["code"])
            code.setObjectName("Code")
            code.setAlignment(Qt.AlignCenter)
            bl.addWidget(cap)
            bl.addWidget(code)
            lay.addWidget(box)
        else:
            known = QLabel("This device is already paired with you.")
            known.setStyleSheet("color: %s;" % theme.GOOD)
            lay.addWidget(known)

        self.remember = QCheckBox("Remember this device (skip the code next time)")
        self.remember.setChecked(True)
        lay.addWidget(self.remember)

        buttons = QHBoxLayout()
        decline = QPushButton("Decline")
        accept = QPushButton("Accept")
        accept.setObjectName("Primary")
        accept.setDefault(True)
        decline.clicked.connect(self.reject)
        accept.clicked.connect(self.accept)
        buttons.addStretch(1)
        buttons.addWidget(decline)
        buttons.addWidget(accept)
        lay.addLayout(buttons)


# -------------------------------------------------------------- settings
class SettingsDialog(QDialog):
    def __init__(self, config: Config, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("BarqDrop settings")
        self.setMinimumWidth(470)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        form = QFormLayout()
        form.setSpacing(10)

        self.name = QLineEdit(config["device_name"])

        row = QHBoxLayout()
        self.save_dir = QLineEdit(config["save_dir"])
        browse = QPushButton("Browse")
        browse.clicked.connect(self._pick_dir)
        row.addWidget(self.save_dir, 1)
        row.addWidget(browse)
        wrap = QWidget()
        wrap.setLayout(row)
        row.setContentsMargins(0, 0, 0, 0)

        self.streams = QSpinBox()
        self.streams.setRange(1, 16)
        self.streams.setValue(int(config["streams"]))
        self.chunk = QComboBox()
        for mb in (1, 2, 4, 8, 16):
            self.chunk.addItem("%d MB" % mb, mb)
        self.chunk.setCurrentIndex(max(0, [1, 2, 4, 8, 16].index(int(config["chunk_mb"]))
                                       if int(config["chunk_mb"]) in (1, 2, 4, 8, 16) else 2))
        self.segment = QComboBox()
        for mb in (8, 16, 32, 64, 128):
            self.segment.addItem("%d MB" % mb, mb)
        seg = int(config["segment_mb"])
        self.segment.setCurrentIndex([8, 16, 32, 64, 128].index(seg) if seg in (8, 16, 32, 64, 128) else 2)
        self.sockbuf = QSpinBox()
        self.sockbuf.setRange(1, 32)
        self.sockbuf.setSuffix(" MB")
        self.sockbuf.setValue(int(config["sock_buf_mb"]))
        self.port = QSpinBox()
        self.port.setRange(1024, 65535)
        self.port.setValue(int(config["port"]))

        self.encrypt = QCheckBox("Encrypt file data (AES-GCM). Turn off only on a trusted link.")
        self.encrypt.setToolTip(
            "On: every chunk is encrypted and authenticated, so corruption or "
            "tampering fails the transfer.\nOff: slightly faster, but integrity "
            "then rests on TCP checksums alone.")
        self.encrypt.setChecked(bool(config["encrypt_data"]))
        self.auto = QCheckBox("Auto-accept from remembered devices")
        self.auto.setChecked(bool(config["auto_accept_trusted"]))
        self.visible = QCheckBox("Be discoverable by nearby devices")
        self.visible.setChecked(bool(config["visible"]))

        form.addRow("Device name", self.name)
        form.addRow("Save received files to", wrap)
        form.addRow("Parallel streams", self.streams)
        form.addRow("Chunk size", self.chunk)
        form.addRow("Segment size", self.segment)
        form.addRow("Socket buffers", self.sockbuf)
        form.addRow("Listening port", self.port)
        lay.addLayout(form)
        lay.addSpacing(6)
        lay.addWidget(self.encrypt)
        lay.addWidget(self.auto)
        lay.addWidget(self.visible)

        note = QLabel("Port and device-name changes take effect after a restart.")
        note.setObjectName("Muted")
        note.setWordWrap(True)
        lay.addSpacing(6)
        lay.addWidget(note)

        forget = QPushButton("Forget all paired devices (%d)" % len(config["trusted"]))
        forget.setObjectName("Danger")
        forget.clicked.connect(self._forget)
        lay.addWidget(forget)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _pick_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Choose where to save received files",
                                             self.save_dir.text())
        if d:
            self.save_dir.setText(d)

    def _forget(self):
        self.config["trusted"] = {}
        self.config.save()
        QMessageBox.information(self, "BarqDrop", "All paired devices have been forgotten.")

    def values(self) -> dict:
        return {
            "device_name": self.name.text().strip() or self.config["device_name"],
            "save_dir": self.save_dir.text().strip() or self.config["save_dir"],
            "streams": self.streams.value(),
            "chunk_mb": self.chunk.currentData(),
            "segment_mb": self.segment.currentData(),
            "sock_buf_mb": self.sockbuf.value(),
            "port": self.port.value(),
            "encrypt_data": self.encrypt.isChecked(),
            "auto_accept_trusted": self.auto.isChecked(),
            "visible": self.visible.isChecked(),
        }


# ------------------------------------------------------------ main window
class MainWindow(QMainWindow):
    def __init__(self, config: Config, engine: Engine, events: queue.Queue):
        super().__init__()
        self.config = config
        self.engine = engine
        self.events = events
        self.direct = DirectLink()
        self.staged: list[str] = []
        self._pumping = False
        self.rows: dict[str, TransferRow] = {}
        self.open_offers: dict[str, OfferDialog] = {}

        self.setWindowTitle(APP_TITLE)
        self.setWindowIcon(app_icon())
        self.resize(1040, 720)
        self.setMinimumSize(880, 600)
        self.setAcceptDrops(True)
        self._build()

        self.pump = QTimer(self)
        self.pump.timeout.connect(self._drain)
        self.pump.start(80)

        self.slow = QTimer(self)
        self.slow.timeout.connect(self._refresh_status)
        self.slow.start(5000)

        self._refresh_devices()
        self._refresh_status()

    # ----------------------------------------------------------- layout
    def _build(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_header())

        body = QWidget()
        body_lay = QHBoxLayout(body)
        body_lay.setContentsMargins(16, 14, 16, 16)
        body_lay.setSpacing(16)
        body_lay.addWidget(self._build_left(), 4)
        body_lay.addWidget(self._build_right(), 6)
        outer.addWidget(body, 1)

    def _build_header(self) -> QWidget:
        head = QFrame()
        head.setObjectName("Header")
        lay = QHBoxLayout(head)
        lay.setContentsMargins(18, 12, 16, 12)
        lay.setSpacing(12)

        bolt = QLabel("\u26A1")
        bolt.setObjectName("Bolt")
        lay.addWidget(bolt)

        col = QVBoxLayout()
        col.setSpacing(1)
        title = QLabel(APP_TITLE)
        title.setObjectName("Title")
        self.status = QLabel()
        self.status.setObjectName("Subtitle")
        col.addWidget(title)
        col.addWidget(self.status)
        lay.addLayout(col)
        lay.addStretch(1)

        self.direct_btn = QPushButton("Start direct Wi-Fi link")
        self.direct_btn.clicked.connect(self._toggle_direct)
        lay.addWidget(self.direct_btn)

        settings = QPushButton("Settings")
        settings.clicked.connect(self._open_settings)
        lay.addWidget(settings)
        return head

    def _build_left(self) -> QWidget:
        wrap = QWidget()
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        row = QHBoxLayout()
        row.addWidget(_head("Nearby devices"))
        row.addStretch(1)
        refresh = QPushButton("Refresh")
        refresh.setObjectName("Ghost")
        refresh.clicked.connect(self.engine.discovery.refresh)
        row.addWidget(refresh)
        lay.addLayout(row)

        self.device_area = QScrollArea()
        self.device_area.setWidgetResizable(True)
        self.device_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.device_area.setMinimumWidth(280)
        self.device_host = QWidget()
        self.device_lay = QVBoxLayout(self.device_host)
        self.device_lay.setContentsMargins(0, 0, 6, 0)
        self.device_lay.setSpacing(8)
        self.device_lay.addStretch(1)
        self.device_area.setWidget(self.device_host)
        lay.addWidget(self.device_area, 1)

        self.empty_hint = QLabel(
            "Looking for devices...\n\nOpen BarqDrop on the other machine and make sure\n"
            "both are on the same Wi-Fi, Ethernet or direct link.")
        self.empty_hint.setObjectName("Muted")
        self.empty_hint.setAlignment(Qt.AlignCenter)
        self.empty_hint.setWordWrap(True)
        self.device_lay.insertWidget(0, self.empty_hint)
        return wrap

    def _build_right(self) -> QWidget:
        wrap = QWidget()
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        lay.addWidget(_head("Send"))
        self.drop = DropArea(self.add_paths)
        lay.addWidget(self.drop)

        self.staged_label = QLabel("Nothing selected")
        self.staged_label.setObjectName("Muted")
        lay.addWidget(self.staged_label)

        buttons = QHBoxLayout()
        add_files = QPushButton("Add files")
        add_files.clicked.connect(self._pick_files)
        add_folder = QPushButton("Add folder")
        add_folder.clicked.connect(self._pick_folder)
        clear = QPushButton("Clear")
        clear.setObjectName("Ghost")
        clear.clicked.connect(lambda: self.add_paths([], replace=True))
        buttons.addWidget(add_files)
        buttons.addWidget(add_folder)
        buttons.addWidget(clear)
        buttons.addStretch(1)
        lay.addLayout(buttons)

        head_row = QHBoxLayout()
        head_row.addWidget(_head("Transfers"))
        head_row.addStretch(1)
        clear_done = QPushButton("Clear finished")
        clear_done.setObjectName("Ghost")
        clear_done.clicked.connect(self.engine.clear_finished)
        head_row.addWidget(clear_done)
        lay.addSpacing(6)
        lay.addLayout(head_row)

        self.transfer_area = QScrollArea()
        self.transfer_area.setWidgetResizable(True)
        self.transfer_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.transfer_host = QWidget()
        self.transfer_lay = QVBoxLayout(self.transfer_host)
        self.transfer_lay.setContentsMargins(0, 0, 6, 0)
        self.transfer_lay.setSpacing(8)
        self.transfer_lay.addStretch(1)
        self.transfer_area.setWidget(self.transfer_host)
        lay.addWidget(self.transfer_area, 1)

        self.no_transfers = QLabel("No transfers yet.")
        self.no_transfers.setObjectName("Muted")
        self.no_transfers.setAlignment(Qt.AlignCenter)
        self.transfer_lay.insertWidget(0, self.no_transfers)
        return wrap

    # ------------------------------------------------------------ staging
    def add_paths(self, paths, replace: bool = False) -> None:
        if replace:
            self.staged = []
        for p in paths:
            if p and p not in self.staged:
                self.staged.append(p)
        self._update_staged()

    def _update_staged(self) -> None:
        if not self.staged:
            self.staged_label.setText("Nothing selected")
            return
        items = build_items(self.staged)
        total = sum(i["size"] for i in items)
        self.staged_label.setText("%d file(s) selected  -  %s  -  pick a device to send"
                                  % (len(items), human_size(total)))

    def _pick_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Choose files to send")
        if files:
            self.add_paths(files)

    def _pick_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose a folder to send")
        if folder:
            self.add_paths([folder])

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.add_paths(paths)

    # ------------------------------------------------------------ sending
    def _send_to(self, peer: dict) -> None:
        if not self.staged:
            QMessageBox.information(self, APP_TITLE,
                                    "Add some files first, or drag them onto the device.")
            return
        self.engine.send_paths(peer, list(self.staged))
        self.add_paths([], replace=True)

    def _drop_on_device(self, peer: dict, paths) -> None:
        self.engine.send_paths(peer, paths)

    def _speed_test(self, peer: dict) -> None:
        self.engine.speed_test(peer, SPEEDTEST_MB)

    # ------------------------------------------------------------ devices
    def _refresh_devices(self) -> None:
        peers = self.engine.discovery.snapshot()
        while self.device_lay.count() > 1:
            item = self.device_lay.takeAt(0)
            w = item.widget()
            if w and w is not self.empty_hint:
                w.deleteLater()
        self.empty_hint.setParent(None)
        if not peers:
            self.device_lay.insertWidget(0, self.empty_hint)
            self.empty_hint.show()
            return
        for i, peer in enumerate(peers):
            card = DeviceCard(peer, self._send_to, self._drop_on_device,
                              self._speed_test)
            self.device_lay.insertWidget(i, card)

    # ------------------------------------------------------------- status
    def _refresh_status(self) -> None:
        bits = ["%s  -  %s" % (self.config["device_name"], primary_ip())]
        bits.append(cached_link_description())
        if self.direct.active:
            bits.append("direct link: %s" % self.direct.ssid)
        if self.engine.listen_error:
            bits.append("PORT ERROR: %s" % self.engine.listen_error)
        self.status.setText("   |   ".join(bits))

    def _toggle_direct(self) -> None:
        if self.direct.active:
            ok, msg = self.direct.stop()
            self.direct_btn.setText("Start direct Wi-Fi link")
            QMessageBox.information(self, APP_TITLE, msg)
        else:
            self.direct_btn.setEnabled(False)
            self.direct_btn.setText("Starting...")
            QApplication.processEvents()
            ok, msg = self.direct.start()
            self.direct_btn.setEnabled(True)
            self.direct_btn.setText("Stop direct Wi-Fi link" if ok else "Start direct Wi-Fi link")
            (QMessageBox.information if ok else QMessageBox.warning)(self, APP_TITLE, msg)
        self._refresh_status()

    def _open_settings(self) -> None:
        dlg = SettingsDialog(self.config, self)
        if dlg.exec() == QDialog.Accepted:
            self.config.update(dlg.values())
            self._refresh_status()

    # -------------------------------------------------------- event pump
    def _drain(self) -> None:
        if self._pumping:      # a modal dialog is running a nested event loop
            return
        self._pumping = True
        try:
            self._drain_once()
        finally:
            self._pumping = False

    def _drain_once(self) -> None:
        handled = 0
        while handled < 200:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            handled += 1
            self._on_event(event)

    def _on_event(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "peers":
            self._refresh_devices()
        elif kind == "job":
            self._on_job(event["job"])
        elif kind == "offer":
            self._on_offer(event["offer"])
        elif kind == "offer_closed":
            dlg = self.open_offers.pop(event["id"], None)
            if dlg:
                dlg.done(QDialog.Rejected)
        elif kind == "jobs_cleared":
            for row in list(self.rows.values()):
                row.setParent(None)
                row.deleteLater()
            self.rows.clear()
            self.no_transfers.setVisible(True)
        elif kind == "received":
            self.statusBar().showMessage(
                "Saved %d file(s) from %s" % (len(event["paths"]), event["peer"]), 8000)
        elif kind == "log":
            self.statusBar().showMessage(event["text"], 6000)

    def _on_job(self, job: dict) -> None:
        row = self.rows.get(job["id"])
        if row is None:
            row = TransferRow(job, self.engine.cancel_job)
            self.rows[job["id"]] = row
            self.transfer_lay.insertWidget(0, row)
            self.no_transfers.setVisible(False)
        else:
            row.update_job(job)

    def _on_offer(self, offer: dict) -> None:
        dlg = OfferDialog(offer, self)
        self.open_offers[offer["id"]] = dlg
        self.raise_()
        self.activateWindow()
        accepted = dlg.exec() == QDialog.Accepted
        self.open_offers.pop(offer["id"], None)
        self.engine.respond_offer(offer["id"], accepted, dlg.remember.isChecked())

    # -------------------------------------------------------------- close
    def closeEvent(self, event):
        self.engine.stop()
        event.accept()


# ----------------------------------------------------------------- runner
def run() -> int:
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    app.setWindowIcon(app_icon())
    app.setStyleSheet(theme.QSS)

    config = Config()
    events: queue.Queue = queue.Queue()
    engine = Engine(config, events.put)

    window = MainWindow(config, engine, events)
    window.show()
    engine.start()
    if engine.listen_error:
        QMessageBox.warning(window, APP_TITLE,
                            "BarqDrop could not listen on port %s:\n%s\n\n"
                            "Change the port in Settings, then restart."
                            % (config["port"], engine.listen_error))
    code = app.exec()
    engine.stop()
    return code
