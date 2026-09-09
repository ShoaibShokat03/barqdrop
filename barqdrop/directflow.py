"""UI flow for moving two devices onto a private Wi-Fi link.

Hosting a soft AP on the same radio that is already a Wi-Fi client is a
gamble: it removes the router hop (good) but makes the radio time-share and
often narrows the channel (bad). Which effect wins depends entirely on the
adapter, so this flow refuses to assert an outcome. It measures the link
before switching, measures it again afterwards, shows both numbers, and
offers to put the network back.
"""
from __future__ import annotations

import threading
import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QMessageBox, QProgressBar, QPushButton,
    QVBoxLayout,
)

from . import theme
from .util import human_rate

BASELINE_MB = 64          # short enough not to annoy, long enough to be stable
SETTLE_SECONDS = 45       # how long to wait for the peer to reappear


class BusyDialog(QDialog):
    """A modal 'working on it' box with a live status line."""

    def __init__(self, title: str, message: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(420)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(12)
        head = QLabel(title)
        head.setObjectName("Title")
        lay.addWidget(head)
        self.status = QLabel(message)
        self.status.setObjectName("Muted")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        bar = QProgressBar()
        bar.setRange(0, 0)
        bar.setTextVisible(False)
        lay.addWidget(bar)

    def say(self, text: str) -> None:
        self.status.setText(text)


class InviteDialog(QDialog):
    """Shown on the machine being asked to switch networks."""

    def __init__(self, invite: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Direct Wi-Fi link")
        self.setModal(True)
        self.setMinimumWidth(440)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(12)

        title = QLabel("%s wants to move this PC onto a direct link"
                       % invite["peer"])
        title.setObjectName("Title")
        title.setWordWrap(True)
        lay.addWidget(title)

        body = QLabel(
            "Your Wi-Fi will switch from <b>%s</b> to <b>%s</b>, a private "
            "network hosted by that device.<br><br>"
            "Internet keeps working through the other PC while it shares its "
            "connection, but it may be slower or drop out. Either machine can "
            "put the network back with <b>Restore network</b>."
            % (invite.get("from_ssid") or "your current network", invite["ssid"]))
        body.setWordWrap(True)
        body.setTextFormat(Qt.RichText)
        lay.addWidget(body)

        note = QLabel("Only devices you have already paired with can ask this.")
        note.setObjectName("Muted")
        note.setWordWrap(True)
        lay.addWidget(note)

        buttons = QHBoxLayout()
        decline = QPushButton("Stay on this network")
        accept = QPushButton("Switch")
        accept.setObjectName("Primary")
        accept.setDefault(True)
        decline.clicked.connect(self.reject)
        accept.clicked.connect(self.accept)
        buttons.addStretch(1)
        buttons.addWidget(decline)
        buttons.addWidget(accept)
        lay.addLayout(buttons)


def confirm_host(parent, peer_name: str) -> bool:
    box = QMessageBox(parent)
    box.setWindowTitle("Direct Wi-Fi link")
    box.setIcon(QMessageBox.Question)
    box.setText("Move this PC and %s onto a private Wi-Fi link?" % peer_name)
    box.setInformativeText(
        "This PC will host the link and %s will be asked to join it.\n\n"
        "Whether it is faster depends on your Wi-Fi adapter: it removes the "
        "router hop, but a laptop with one radio has to share airtime between "
        "hosting and staying online, and Windows often narrows the channel.\n\n"
        "BarqDrop measures the link before and after and offers to switch "
        "back if it turns out slower." % peer_name)
    box.setStandardButtons(QMessageBox.Cancel | QMessageBox.Ok)
    box.button(QMessageBox.Ok).setText("Measure and switch")
    box.setDefaultButton(QMessageBox.Ok)
    return box.exec() == QMessageBox.Ok


class DirectLinkFlow:
    """Drives measure -> host -> invite -> re-measure -> keep or revert."""

    def __init__(self, window, engine, peer):
        self.window = window
        self.engine = engine
        self.peer = peer
        self.before = 0.0
        self.after = 0.0

    # -- helpers ---------------------------------------------------------
    def _measure(self, dialog, label) -> float:
        """Run a speed test and return bytes/s, or 0 if it did not finish."""
        dialog.say(label)
        job_id = self.engine.speed_test(self.peer, BASELINE_MB)
        deadline = time.time() + 180
        while time.time() < deadline:
            self.window.pump_events()
            job = self.window.last_job(job_id)
            if job and job["state"] in ("done", "failed", "cancelled", "rejected"):
                if job["state"] != "done":
                    return 0.0
                return job["total"] / max(job["elapsed"], 1e-6)
            time.sleep(0.05)
        return 0.0

    def _wait_for_peer(self, dialog) -> bool:
        """Wait for the peer to reappear after both sides change network."""
        peer_id = self.peer.get("id")
        deadline = time.time() + SETTLE_SECONDS
        while time.time() < deadline:
            remaining = int(deadline - time.time())
            dialog.say("Waiting for %s to appear on the direct link... (%ds)"
                       % (self.peer.get("name") or "the other device", remaining))
            self.window.pump_events()
            for found in self.engine.discovery.snapshot():
                if found.get("id") == peer_id:
                    self.peer = found          # its address has changed
                    return True
            time.sleep(0.5)
        return False

    # -- the flow --------------------------------------------------------
    def run(self) -> None:
        name = self.peer.get("name") or self.peer.get("ip")
        if not confirm_host(self.window, name):
            return

        dialog = BusyDialog("Direct Wi-Fi link",
                            "Measuring the current link...", self.window)
        dialog.show()
        self.window.pump_events()
        try:
            self.before = self._measure(dialog, "Measuring the current link...")

            dialog.say("Starting the direct link and inviting %s..." % name)
            self.window.pump_events()
            ok, message = self._invite(dialog)
            if not ok:
                dialog.close()
                QMessageBox.warning(self.window, "Direct Wi-Fi link", message)
                self.engine.restore_network()
                return

            if not self._wait_for_peer(dialog):
                dialog.close()
                self._offer_revert(
                    "%s did not reappear on the direct link within %d seconds.\n\n"
                    "It may still be joining, or it could not reach the network."
                    % (name, SETTLE_SECONDS))
                return

            self.after = self._measure(dialog, "Measuring the direct link...")
        finally:
            dialog.close()
        self._report()

    def _invite(self, dialog):
        """Host + invite runs off the UI thread; netsh and WinRT both block."""
        result = {}
        done = threading.Event()

        def worker():
            try:
                result["value"] = self.engine.invite_direct_link(self.peer)
            except Exception as exc:                      # pragma: no cover
                result["value"] = (False, str(exc))
            finally:
                done.set()

        threading.Thread(target=worker, name="direct-invite", daemon=True).start()
        while not done.wait(0.05):
            self.window.pump_events()
        return result.get("value", (False, "the invitation did not complete"))

    def _report(self) -> None:
        if not self.after:
            self._offer_revert("The direct link is up, but the speed test on it "
                               "did not complete.")
            return
        faster = self.after > self.before * 1.05
        lines = ["Before:  %s" % (human_rate(self.before) if self.before else "not measured"),
                 "Direct:  %s" % human_rate(self.after)]
        if self.before:
            lines.append("")
            lines.append("That is %.2fx %s." % (
                (self.after / self.before) if faster else (self.before / self.after),
                "faster" if faster else "slower"))
        verdict = ("Keeping the direct link." if faster else
                   "The direct link is not an improvement on this hardware.")
        self._offer_revert("\n".join(lines) + "\n\n" + verdict,
                           default_revert=not faster,
                           title="Faster" if faster else "Not faster")

    def _offer_revert(self, message, default_revert=True, title="Direct Wi-Fi link"):
        box = QMessageBox(self.window)
        box.setWindowTitle("Direct Wi-Fi link")
        box.setIcon(QMessageBox.Information)
        box.setText(title)
        box.setInformativeText(message + "\n\nPut both machines back on the "
                                         "previous network?")
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.button(QMessageBox.Yes).setText("Restore network")
        box.button(QMessageBox.No).setText("Stay on the direct link")
        box.setDefaultButton(QMessageBox.Yes if default_revert else QMessageBox.No)
        if box.exec() == QMessageBox.Yes:
            ok, note = self.engine.restore_network()
            QMessageBox.information(self.window, "Direct Wi-Fi link", note)
