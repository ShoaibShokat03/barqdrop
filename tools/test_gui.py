"""Offscreen UI smoke test: build the window, feed it events, render a PNG."""
import os
import queue
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication          # noqa: E402
from barqdrop.config import Config                  # noqa: E402
from barqdrop.engine import Engine                  # noqa: E402
from barqdrop.gui import MainWindow, OfferDialog, SettingsDialog  # noqa: E402
from barqdrop.directflow import BusyDialog, InviteDialog  # noqa: E402


def main():
    tmp = tempfile.mkdtemp(prefix="barqdrop-gui-")
    cfg = Config(os.path.join(tmp, "cfg.json"))
    cfg.update({"device_name": "Studio-PC", "save_dir": os.path.join(tmp, "inbox"),
                "port": 46301, "discovery_port": 46302})
    app = QApplication(sys.argv)
    from barqdrop import theme
    app.setStyleSheet(theme.QSS)

    events = queue.Queue()
    engine = Engine(cfg, events.put)
    engine.discovery.snapshot = lambda: [
        {"id": "1", "name": "Laptop-Zara", "ip": "192.168.1.42", "port": 45878, "fp": "a1", "os": "Windows"},
        {"id": "2", "name": "Office-Tower", "ip": "192.168.1.77", "port": 45878, "fp": "b2", "os": "Windows"},
    ]
    win = MainWindow(cfg, engine, events)
    win.resize(1040, 720)
    win.show()
    win._refresh_devices()

    events.put({"type": "job", "job": {
        "id": "j1", "direction": "send", "peer": "Laptop-Zara", "state": "running",
        "error": "", "code": "", "files": 1, "name": "ubuntu-24.04.iso",
        "total": 5_368_709_120, "done": 2_147_483_648, "rate": 118_000_000,
        "eta": 27.3, "elapsed": 18.2}})
    events.put({"type": "job", "job": {
        "id": "j2", "direction": "recv", "peer": "Office-Tower", "state": "done",
        "error": "", "code": "", "files": 12, "name": "12 items",
        "total": 734_003_200, "done": 734_003_200, "rate": 0, "eta": 0,
        "elapsed": 6.4}})
    events.put({"type": "job", "job": {
        "id": "j3", "direction": "send", "peer": "Laptop-Zara", "state": "verifying",
        "error": "", "code": "418902", "files": 3, "name": "3 items",
        "total": 91_000_000, "done": 0, "rate": 0, "eta": None, "elapsed": 1.0}})
    win.add_paths([os.path.abspath(__file__)])
    win._drain()
    app.processEvents()

    out = os.environ.get("BARQDROP_SHOT_DIR") or os.path.join(tmp, "shots")
    os.makedirs(out, exist_ok=True)
    main_png = os.path.join(out, "screenshot-main.png")
    win.grab().save(main_png)

    dlg = OfferDialog({"id": "o1", "peer": "Office-Tower", "ip": "192.168.1.77",
                       "files": [{"rel": "holiday-4k.mp4", "size": 4_100_000_000},
                                 {"rel": "notes/plan.pdf", "size": 240_000}],
                       "count": 2, "total": 4_100_240_000, "code": "418902",
                       "trusted": False}, win)
    dlg.resize(430, 470)
    dlg.grab().save(os.path.join(out, "screenshot-offer.png"))

    inv = InviteDialog({"id": "i1", "peer": "Office-Tower",
                        "ssid": "BarqDrop-AE32", "from_ssid": "JTech2-5Ghz"}, win)
    inv.resize(450, 320)
    inv.grab().save(os.path.join(out, "screenshot-invite.png"))

    busy = BusyDialog("Direct Wi-Fi link", "Measuring the current link...", win)
    busy.resize(430, 180)
    busy.grab().save(os.path.join(out, "screenshot-busy.png"))

    # the direct-link flow reads job results through these hooks
    assert win.last_job("j1") is not None, "job_state did not record a job"
    win.pump_events()

    sett = SettingsDialog(cfg, win)
    sett.resize(480, 560)
    sett.grab().save(os.path.join(out, "screenshot-settings.png"))

    # Buttons must survive being clicked (Qt passes a `checked` argument).
    from PySide6.QtWidgets import QPushButton
    clicked = 0
    for btn in win.findChildren(QPushButton):
        if btn.text() in ("Refresh", "Clear", "Clear finished"):
            btn.click()
            clicked += 1
    app.processEvents()
    assert clicked >= 3, "expected to exercise 3 buttons, clicked %d" % clicked
    win._drain()
    assert not win.rows, "Clear finished should have emptied the transfer list"
    assert not win.staged, "Clear should have emptied the staging list"

    # rebuild the rows for the screenshot assertions below
    for job in (
        {"id": "j1", "direction": "send", "peer": "Laptop-Zara", "state": "running",
         "error": "", "code": "", "files": 1, "name": "ubuntu-24.04.iso",
         "total": 5_368_709_120, "done": 2_147_483_648, "rate": 118_000_000,
         "eta": 27.3, "elapsed": 18.2},
        {"id": "j2", "direction": "recv", "peer": "Office-Tower", "state": "done",
         "error": "", "code": "", "files": 12, "name": "12 items",
         "total": 734_003_200, "done": 734_003_200, "rate": 0, "eta": 0, "elapsed": 6.4},
        {"id": "j3", "direction": "send", "peer": "Laptop-Zara", "state": "failed",
         "error": "Connection lost (partial data kept, resend to resume)", "code": "",
         "files": 3, "name": "3 items", "total": 91_000_000, "done": 40_000_000,
         "rate": 0, "eta": None, "elapsed": 12.0},
    ):
        events.put({"type": "job", "job": job})
    win.add_paths([os.path.abspath(__file__)])
    win._drain()
    app.processEvents()
    win.grab().save(main_png)

    assert len(win.rows) == 3, "expected 3 transfer rows, got %d" % len(win.rows)
    assert win.staged, "staging did not record the dropped file"
    print("UI built cleanly: %d device cards, %d transfer rows" %
          (sum(1 for i in range(win.device_lay.count())
               if win.device_lay.itemAt(i).widget().__class__.__name__ == "DeviceCard"),
           len(win.rows)))
    print("screenshots written to %s" % out)
    engine.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
