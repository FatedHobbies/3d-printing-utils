"""System tray icon for the Slicer PC.

    python -m threedpu.slicer tray

Shows a pizza-cutter icon in the notification area. A green dot means the Slicer
server is running; grey means it's stopped. The menu starts and stops the server,
shows the connection info (address and token for Manager) and opens the log.

The tray starts the server when it launches (the installer puts the tray in the
Startup folder). Only one tray runs at a time: launching it again (for example
from the Start menu) shows the connection info window of the running one.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, QThreadPool, QTimer, QUrl
from PySide6.QtGui import QAction, QBrush, QColor, QDesktopServices, QIcon, QPainter, QPen, QPixmap
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QStyleFactory, QSystemTrayIcon

from threedpu.common import config
from threedpu.manager.app import Task
from threedpu.manager.client import SlicerClient
from threedpu.slicer.infogui import InfoWindow, start_service_detached

INSTANCE_NAME = "3dpu-slicer-tray"
RUNNING, STOPPED, UNKNOWN = "running", "stopped", "unknown"


def pizza_cutter_icon(state: str = UNKNOWN, size: int = 64) -> QIcon:
    """Draw the tray icon: a pizza-cutter wheel with a handle, plus a status dot."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    s = size / 64.0

    # Blade: a steel wheel with a bright cutting edge, low on the right.
    p.setPen(QPen(QColor("#eef3f6"), 3 * s))
    p.setBrush(QBrush(QColor("#b8c4cd")))
    p.drawEllipse(QRectF(20 * s, 20 * s, 40 * s, 40 * s))
    p.setPen(QPen(QColor("#5f6b75"), 1.5 * s))
    p.setBrush(Qt.NoBrush)
    p.drawEllipse(QRectF(18.5 * s, 18.5 * s, 43 * s, 43 * s))

    # Guard: a metal cover over the top-left of the wheel, which the handle attaches to.
    p.setPen(QPen(QColor("#3d464e"), 7 * s, Qt.SolidLine, Qt.FlatCap))
    p.drawArc(QRectF(23 * s, 23 * s, 34 * s, 34 * s), 95 * 16, 80 * 16)
    p.setPen(QPen(QColor("#3d464e"), 6 * s, Qt.SolidLine, Qt.RoundCap))
    p.drawLine(QPointF(40 * s, 40 * s), QPointF(27 * s, 27 * s))

    # Handle, up and to the left.
    p.setPen(QPen(QColor("#c0392b"), 11 * s, Qt.SolidLine, Qt.RoundCap))
    p.drawLine(QPointF(25 * s, 25 * s), QPointF(8 * s, 8 * s))

    # Hub.
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(QColor("#3d464e")))
    p.drawEllipse(QRectF(35 * s, 35 * s, 10 * s, 10 * s))

    # Status dot.
    color = {"running": "#2ea043", "stopped": "#8b949e"}.get(state)
    if color:
        p.setPen(QPen(QColor("white"), 3 * s))
        p.setBrush(QBrush(QColor(color)))
        p.drawEllipse(QRectF(42 * s, 42 * s, 21 * s, 21 * s))
    p.end()
    return QIcon(pm)


def notify_running_instance(message: str) -> bool:
    """Send a message to an already running tray. True if one was running."""
    sock = QLocalSocket()
    sock.connectToServer(INSTANCE_NAME)
    if not sock.waitForConnected(500):
        return False
    sock.write(message.encode() + b"\n")
    sock.flush()
    sock.waitForBytesWritten(1000)
    # Let the running tray read the message and hang up first; closing our end right
    # away can drop the message on Windows named pipes.
    sock.waitForDisconnected(1000)
    sock.abort()
    return True


class SlicerTray:
    def __init__(self, cfg_path: Path | None = None, start_server: bool = True):
        self.cfg_path = cfg_path or config.slicer_config_path()
        self.cfg = config.load_slicer_config(self.cfg_path)
        self.state = UNKNOWN
        self._task: Task | None = None
        self._info: InfoWindow | None = None
        self._start_pending = start_server

        self.tray = QSystemTrayIcon(pizza_cutter_icon(UNKNOWN))
        self.tray.setToolTip("3dpu Slicer")
        menu = QMenu()
        self.status_action = QAction("Checking…")
        self.status_action.setEnabled(False)
        self.start_action = QAction("Start Slicer server")
        self.start_action.triggered.connect(self.start)
        self.stop_action = QAction("Stop Slicer server")
        self.stop_action.triggered.connect(self.stop)
        info_action = QAction("Connection info…")
        info_action.triggered.connect(self.show_info)
        log_action = QAction("Open log")
        log_action.triggered.connect(self.open_log)
        quit_action = QAction("Quit (stops the server)")
        quit_action.triggered.connect(self.quit)
        self._actions = [self.status_action, self.start_action, self.stop_action,
                         info_action, log_action, quit_action]
        menu.addAction(self.status_action)
        menu.addSeparator()
        menu.addAction(self.start_action)
        menu.addAction(self.stop_action)
        menu.addSeparator()
        menu.addAction(info_action)
        menu.addAction(log_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        self.menu = menu
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._activated)
        self.tray.show()

        self._conns: list[QLocalSocket] = []
        self.ipc = QLocalServer()
        QLocalServer.removeServer(INSTANCE_NAME)
        self.ipc.listen(INSTANCE_NAME)
        self.ipc.newConnection.connect(self._ipc_message)

        self.timer = QTimer()
        self.timer.timeout.connect(self.check)
        self.timer.start(3000)
        QTimer.singleShot(0, self.check)

    # ----- state -------------------------------------------------------
    def _set_state(self, state: str, detail: str = "") -> None:
        changed = state != self.state
        self.state = state
        self.tray.setIcon(pizza_cutter_icon(state))
        text = {"running": "Slicer server is running", "stopped": "Slicer server is stopped"}.get(
            state, "Checking…")
        self.status_action.setText(text + (f" ({detail})" if detail else ""))
        self.tray.setToolTip(f"3dpu Slicer: {text.split(' is ')[-1]}")
        self.start_action.setEnabled(state == STOPPED)
        self.stop_action.setEnabled(state == RUNNING)
        if changed and state == STOPPED and self._start_pending:
            self._start_pending = False
            self.start()

    def check(self) -> None:
        if self._task is not None:
            return
        url, token = f"http://127.0.0.1:{self.cfg.port}", self.cfg.token

        def run():
            with SlicerClient(url, token, timeout=2) as c:
                return c.health()

        def done(h):
            self._task = None
            self._start_pending = False
            self._set_state(RUNNING, f"Bambu Studio {h.get('bambu_studio_version') or '?'}")

        def failed(err):
            self._task = None
            self._set_state(STOPPED, "port in use by another program" if "token" in err else "")

        task = Task(run)
        task.setAutoDelete(False)
        task.signals.done.connect(done)
        task.signals.failed.connect(failed)
        self._task = task
        QThreadPool.globalInstance().start(task)

    # ----- actions -----------------------------------------------------
    def start(self) -> None:
        try:
            start_service_detached()
        except OSError as exc:
            self.tray.showMessage("3dpu Slicer", f"Couldn't start the server: {exc}",
                                  QSystemTrayIcon.Warning)
            return
        self.status_action.setText("Starting…")
        self.start_action.setEnabled(False)
        QTimer.singleShot(1500, self.check)

    def stop(self) -> bool:
        try:
            with SlicerClient(f"http://127.0.0.1:{self.cfg.port}", self.cfg.token, timeout=3) as c:
                c.shutdown()
        except Exception as exc:  # noqa: BLE001
            self.tray.showMessage("3dpu Slicer", f"Couldn't stop the server: {exc}",
                                  QSystemTrayIcon.Warning)
            return False
        self.status_action.setText("Stopping…")
        self.stop_action.setEnabled(False)
        QTimer.singleShot(1500, self.check)
        return True

    def show_info(self) -> None:
        if self._info is None:
            self._info = InfoWindow(self.cfg_path)
            self._info.setWindowIcon(pizza_cutter_icon(RUNNING))
        self._info.show()
        self._info.raise_()
        self._info.activateWindow()

    def open_log(self) -> None:
        log = self.cfg_path.parent / "slicer.log"
        if log.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(log)))
        else:
            QMessageBox.information(None, "3dpu Slicer", "There's no log yet.")

    def quit(self) -> None:
        if self.state == RUNNING:
            self.stop()
        self.timer.stop()
        QThreadPool.globalInstance().waitForDone(5000)
        self.tray.hide()
        QApplication.quit()

    def _activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.show_info()

    def _ipc_message(self) -> None:
        conn = self.ipc.nextPendingConnection()
        if conn is None:
            return
        self._conns.append(conn)

        def read() -> None:
            if not conn.canReadLine():
                return
            msg = bytes(conn.readLine()).decode(errors="replace").strip()
            conn.disconnectFromServer()
            if conn in self._conns:
                self._conns.remove(conn)
            if msg == "quit":
                self.quit()
            elif msg == "show":
                self.show_info()

        conn.readyRead.connect(read)
        read()  # the message may already be here


def main(cfg_path: Path | None = None, show_info: bool = False) -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("3dpu Slicer")
    app.setQuitOnLastWindowClosed(False)  # closing the info window keeps the tray running
    for style in ("windows11", "windowsvista"):
        if style in [s.lower() for s in QStyleFactory.keys()]:
            app.setStyle(style)
            break
    if notify_running_instance("show" if show_info else "noop"):
        return 0
    tray = SlicerTray(cfg_path)
    if show_info or not QSystemTrayIcon.isSystemTrayAvailable():
        # No notification area (unusual on Windows): the info window has Start/Stop too.
        QTimer.singleShot(0, tray.show_info)
    code = app.exec()
    if tray._info is not None:
        tray._info.deleteLater()
    app.processEvents()
    del tray
    return code


if __name__ == "__main__":
    sys.exit(main())
