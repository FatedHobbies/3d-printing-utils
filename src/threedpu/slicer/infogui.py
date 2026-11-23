"""Small window on the Slicer PC: is the service running, and what to enter on Manager.

Opened from the Start menu ("3dpu Slicer"), so nobody needs a terminal to find the
address or the access token.
"""

from __future__ import annotations

import socket
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QThreadPool, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QFont, QGuiApplication
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QStyleFactory,
    QVBoxLayout,
    QWidget,
)

import threedpu
from threedpu.common import bambu, config
from threedpu.manager.app import Task
from threedpu.manager.client import SlicerClient


def this_pc_addresses() -> list[str]:
    names = [socket.gethostname()]
    try:
        _, _, ips = socket.gethostbyname_ex(socket.gethostname())
        names += [ip for ip in ips if not ip.startswith("127.")]
    except OSError:
        pass
    return names


def service_command() -> list[str]:
    """Command that starts the service without a console window."""
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    if pythonw.is_file():
        exe = pythonw
    return [str(exe), "-m", "threedpu.slicer", "serve"]


def start_service_detached() -> None:
    log = config.slicer_config_path().parent / "slicer.log"
    cmd = service_command() + ["--log-file", str(log)]
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    subprocess.Popen(cmd, creationflags=flags, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class InfoWindow(QWidget):
    def __init__(self, cfg_path: Path | None = None):
        super().__init__()
        self.cfg_path = cfg_path or config.slicer_config_path()
        self.cfg = config.load_slicer_config(self.cfg_path)
        self._task: Task | None = None
        self.setWindowTitle(f"3dpu Slicer {threedpu.__version__}")
        self.setMinimumWidth(560)

        mono = QFont("Consolas")
        mono.setStyleHint(QFont.Monospace)

        # --- status
        self.status = QLabel("Checking…")
        bold = self.status.font()
        bold.setBold(True)
        self.status.setFont(bold)
        self.start_btn = QPushButton("Start service")
        self.start_btn.clicked.connect(self._start)
        self.stop_btn = QPushButton("Stop service")
        self.stop_btn.clicked.connect(self._stop)
        status_row = QHBoxLayout()
        status_row.addWidget(self.status, 1)
        status_row.addWidget(self.start_btn)
        status_row.addWidget(self.stop_btn)
        status_box = QGroupBox("Service")
        status_box.setLayout(status_row)

        # --- what to enter on Manager
        grid = QGridLayout()
        row = 0
        grid.addWidget(QLabel("Slicer address:"), row, 0)
        addresses = [f"http://{a}:{self.cfg.port}" for a in this_pc_addresses()]
        for i, addr in enumerate(addresses):
            field = QLineEdit(addr)
            field.setReadOnly(True)
            field.setFont(mono)
            grid.addWidget(field, row, 1)
            grid.addWidget(self._copy_button(field), row, 2)
            if i == 0 and len(addresses) > 1:
                row += 1
                grid.addWidget(QLabel("or by IP address:"), row, 0)
            row += 1
        grid.addWidget(QLabel("Access token:"), row, 0)
        self.token = QLineEdit(self.cfg.token)
        self.token.setReadOnly(True)
        self.token.setFont(mono)
        grid.addWidget(self.token, row, 1)
        grid.addWidget(self._copy_button(self.token), row, 2)
        manager_box = QGroupBox("Enter these in 3dpu Manager → Settings")
        manager_box.setLayout(grid)

        # --- Bambu Studio
        self.bambu_label = QLabel()
        self.bambu_label.setWordWrap(True)
        self.bambu_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        choose = QPushButton("Choose…")
        choose.clicked.connect(self._choose_bambu)
        bambu_row = QHBoxLayout()
        bambu_row.addWidget(self.bambu_label, 1)
        bambu_row.addWidget(choose)
        bambu_box = QGroupBox("Bambu Studio")
        bambu_box.setLayout(bambu_row)

        # --- footer
        log_btn = QPushButton("Open log")
        log_btn.clicked.connect(lambda: self._open(self.cfg_path.parent / "slicer.log"))
        folder_btn = QPushButton("Open settings folder")
        folder_btn.clicked.connect(lambda: self._open(self.cfg_path.parent))
        footer = QHBoxLayout()
        footer.addWidget(log_btn)
        footer.addWidget(folder_btn)
        footer.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addWidget(status_box)
        layout.addWidget(manager_box)
        layout.addWidget(bambu_box)
        layout.addLayout(footer)

        self._refresh_bambu()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._check)
        self.timer.start(3000)
        QTimer.singleShot(0, self._check)

    def _copy_button(self, field: QLineEdit) -> QPushButton:
        btn = QPushButton("Copy")
        btn.clicked.connect(lambda: QGuiApplication.clipboard().setText(field.text()))
        return btn

    def _open(self, path: Path) -> None:
        if path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _refresh_bambu(self) -> None:
        try:
            exe = bambu.find_bambu_studio(self.cfg.bambu_studio_path or None)
            ver = bambu.exe_version(exe) or "unknown version"
            self.bambu_label.setText(f"{exe}\n(version {ver})")
        except bambu.BambuStudioNotFound:
            self.bambu_label.setText(
                "⚠ Not found. Click Choose… and select bambu-studio.exe "
                "(in the folder that contains BambuStudio.dll)."
            )

    def _choose_bambu(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Find bambu-studio.exe", "C:/Program Files", "bambu-studio.exe (bambu-studio.exe);;Programs (*.exe)"
        )
        if not path:
            return
        self.cfg.bambu_studio_path = path
        try:
            config.save(self.cfg, self.cfg_path)
        except OSError as exc:
            QMessageBox.warning(self, "Couldn't save", str(exc))
            return
        self._refresh_bambu()
        if self.stop_btn.isEnabled():
            QMessageBox.information(self, "Restart needed",
                                    "Stop and start the service to use the new Bambu Studio path.")

    def _check(self) -> None:
        if self._task is not None:
            return
        url, token = f"http://127.0.0.1:{self.cfg.port}", self.cfg.token

        def run():
            with SlicerClient(url, token, timeout=2) as c:
                return c.health()

        def done(h):
            self._task = None
            self.status.setText(f"✔ Running (Bambu Studio {h.get('bambu_studio_version') or '?'})")
            self.start_btn.setEnabled(False)
            self.stop_btn.setEnabled(True)

        def failed(err):
            self._task = None
            if "token" in err:
                self.status.setText("⚠ Something else is answering on this port")
            else:
                self.status.setText("✘ Not running")
            self.start_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)

        task = Task(run)
        task.setAutoDelete(False)
        task.signals.done.connect(done)
        task.signals.failed.connect(failed)
        self._task = task
        QThreadPool.globalInstance().start(task)

    def _start(self) -> None:
        try:
            start_service_detached()
        except OSError as exc:
            QMessageBox.warning(self, "Couldn't start the service", str(exc))
            return
        self.status.setText("Starting…")
        self.start_btn.setEnabled(False)

    def _stop(self) -> None:
        try:
            with SlicerClient(f"http://127.0.0.1:{self.cfg.port}", self.cfg.token, timeout=3) as c:
                c.shutdown()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Couldn't stop the service", str(exc))
            return
        self.status.setText("Stopping…")
        self.stop_btn.setEnabled(False)


def main(cfg_path: Path | None = None) -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("3dpu Slicer")
    for style in ("windows11", "windowsvista"):
        if style in [s.lower() for s in QStyleFactory.keys()]:
            app.setStyle(style)
            break
    w = InfoWindow(cfg_path)
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
