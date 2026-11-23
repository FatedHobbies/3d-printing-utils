"""3dpu Manager: the desktop app on the Manager PC."""

from __future__ import annotations

import os
import subprocess
from datetime import datetime
import sys
import traceback
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QStyleFactory,
    QTableWidget,
    QTableWidgetItem,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

import threedpu
from threedpu.common import bambu, config, threemf
from threedpu.common.jobs import JobRequest, JobStatus, new_job_id
from threedpu.manager.client import SlicerClient, SlicerError
from threedpu.manager.jobstore import JobStore, LocalJob, result_name_for
from threedpu.manager.recent import most_recent_project

STATUS_TEXT = {
    JobStatus.PENDING_UPLOAD: "Preparing",
    JobStatus.UPLOADING: "Uploading",
    JobStatus.QUEUED: "Queued on Slicer",
    JobStatus.SLICING: "Slicing",
    JobStatus.DONE: "Downloading",
    JobStatus.FAILED: "Failed",
    JobStatus.CANCELLED: "Cancelled",
    JobStatus.READY: "Ready",
}


# ---------------------------------------------------------------------------
# Background tasks
# ---------------------------------------------------------------------------
class _Signals(QObject):
    done = Signal(object)
    failed = Signal(str)


class Task(QRunnable):
    """Run fn() on the thread pool; deliver its result or error on the UI thread."""

    def __init__(self, fn: Callable[[], Any]):
        super().__init__()
        self.fn = fn
        self.signals = _Signals()

    def run(self) -> None:
        try:
            result = self.fn()
        except SlicerError as exc:
            self.signals.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001
            self.signals.failed.emit(f"{exc}\n\n{traceback.format_exc()}")
        else:
            self.signals.done.emit(result)


# ---------------------------------------------------------------------------
# Dialogs
# ---------------------------------------------------------------------------
class SendDialog(QDialog):
    def __init__(self, parent: QWidget, printers: list[str], last_printer: str):
        super().__init__(parent)
        self.setWindowTitle("Send to Slicer")
        self.setMinimumWidth(560)

        self.path_edit = QLineEdit()
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        path_row = QHBoxLayout()
        path_row.addWidget(self.path_edit, 1)
        path_row.addWidget(browse)

        self.plate_combo = QComboBox()
        self.printer_combo = QComboBox()
        self.printer_combo.setEditable(True)
        self.printer_combo.addItems(printers)
        if last_printer:
            self.printer_combo.setCurrentText(last_printer)

        self.info = QLabel()
        self.info.setWordWrap(True)
        self.info.setStyleSheet("color: palette(placeholder-text);")

        form = QFormLayout()
        form.addRow("Project (.3mf):", path_row)
        form.addRow("Plate:", self.plate_combo)
        form.addRow("Printer:", self.printer_combo)
        form.addRow("", self.info)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("Send")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.buttons)

        self.path_edit.textChanged.connect(self._refresh)
        suggestion = most_recent_project()
        if suggestion:
            self.path_edit.setText(str(suggestion))
        else:
            self._refresh()

    def _browse(self) -> None:
        start = self.path_edit.text() or str(Path.home())
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose the saved Bambu Studio project", start, "Bambu Studio project (*.3mf)"
        )
        if path:
            self.path_edit.setText(path)

    def _refresh(self) -> None:
        self.plate_combo.clear()
        ok = self.buttons.button(QDialogButtonBox.Ok)
        p = Path(self.path_edit.text().strip().strip('"'))
        if not p.is_file():
            self.info.setText("Save your project in Bambu Studio (Ctrl+S), then pick it here.")
            ok.setEnabled(False)
            return
        try:
            plates = threemf.plate_numbers(p)
        except threemf.Not3MF as exc:
            self.info.setText(str(exc))
            ok.setEnabled(False)
            return
        if len(plates) > 1:
            self.plate_combo.addItem(f"All plates ({len(plates)})", 0)
        for n in plates:
            self.plate_combo.addItem(f"Plate {n}", n)
        saved = datetime.fromtimestamp(p.stat().st_mtime).strftime("%a %H:%M:%S")
        self.info.setText(f"Last saved {saved}. Slicer uses the settings stored in this file.")
        ok.setEnabled(True)

    @property
    def project(self) -> Path:
        return Path(self.path_edit.text().strip().strip('"'))

    @property
    def plate(self) -> int:
        return int(self.plate_combo.currentData() or 0)

    @property
    def printer(self) -> str:
        return self.printer_combo.currentText().strip()


class SettingsDialog(QDialog):
    def __init__(self, parent: QWidget, cfg: config.ManagerConfig):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(560)
        self.cfg = cfg

        self.url = QLineEdit(cfg.slicer_url)
        self.token = QLineEdit(cfg.token)
        self.token.setEchoMode(QLineEdit.PasswordEchoOnEdit)
        self.bambu_path = QLineEdit(cfg.bambu_studio_path)
        self.bambu_path.setPlaceholderText("Auto-detect")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse_bambu)
        detect = QPushButton("Detect")
        detect.clicked.connect(self._detect_bambu)
        bambu_row = QHBoxLayout()
        bambu_row.addWidget(self.bambu_path, 1)
        bambu_row.addWidget(browse)
        bambu_row.addWidget(detect)

        self.jobs_dir = QLineEdit(cfg.jobs_dir)
        self.printers = QPlainTextEdit("\n".join(cfg.printers))
        self.printers.setFixedHeight(120)
        self.auto_open = QCheckBox("Open the sliced file in Bambu Studio when it's ready")
        self.auto_open.setChecked(cfg.open_result_automatically)

        self.test_result = QLabel()
        test = QPushButton("Test connection")
        test.clicked.connect(self._test)

        form = QFormLayout()
        form.addRow("Slicer address:", self.url)
        form.addRow("Access token:", self.token)
        form.addRow("", test)
        form.addRow("", self.test_result)
        form.addRow("Bambu Studio:", bambu_row)
        form.addRow("Jobs folder:", self.jobs_dir)
        form.addRow("Printers (one per line):", self.printers)
        form.addRow("", self.auto_open)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _browse_bambu(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Find bambu-studio.exe", self.bambu_path.text() or "C:/Program Files",
            "Bambu Studio (bambu-studio.exe BambuStudio.exe);;Programs (*.exe)",
        )
        if path:
            self.bambu_path.setText(path)

    def _detect_bambu(self) -> None:
        try:
            self.bambu_path.setText(str(bambu.find_bambu_studio()))
        except bambu.BambuStudioNotFound as exc:
            QMessageBox.warning(self, "Bambu Studio not found", str(exc))

    def _test(self) -> None:
        self.test_result.setText("Testing…")
        url, token = self.url.text().strip(), self.token.text().strip()

        def run():
            with SlicerClient(url, token, timeout=5) as c:
                return c.health()

        task = Task(run)
        task.signals.done.connect(
            lambda h: self.test_result.setText(
                f"✔ Connected. Slicer runs Bambu Studio {h.get('bambu_studio_version') or '(unknown)'}."
            )
        )
        task.signals.failed.connect(lambda e: self.test_result.setText(f"✘ {e.splitlines()[0]}"))
        task.setAutoDelete(False)
        self._test_task = task
        QThreadPool.globalInstance().start(task)

    def apply(self) -> config.ManagerConfig:
        self.cfg.slicer_url = self.url.text().strip()
        self.cfg.token = self.token.text().strip()
        self.cfg.bambu_studio_path = self.bambu_path.text().strip()
        self.cfg.jobs_dir = self.jobs_dir.text().strip() or self.cfg.jobs_dir
        self.cfg.printers = [l.strip() for l in self.printers.toPlainText().splitlines() if l.strip()]
        self.cfg.open_result_automatically = self.auto_open.isChecked()
        return self.cfg


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------
COLUMNS = ["Sent", "Project", "Plate", "Printer", "Status", "Details"]


class MainWindow(QMainWindow):
    def __init__(self, cfg_path: Path | None = None):
        super().__init__()
        self.cfg_path = cfg_path or config.manager_config_path()
        self.cfg = config.load_manager_config(self.cfg_path)
        self.store = JobStore(Path(self.cfg.jobs_dir))
        self.jobs: list[LocalJob] = self.store.load_all()
        self.pool = QThreadPool.globalInstance()
        self._in_flight: set[str] = set()
        self._tasks: set[Task] = set()
        self._last_printer = ""

        self.setWindowTitle(f"3dpu Manager {threedpu.__version__}")
        self.resize(980, 560)
        self._build_ui()
        self._refresh_table()

        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self._poll)
        self.poll_timer.start(int(self.cfg.poll_seconds * 1000))
        self.health_timer = QTimer(self)
        self.health_timer.timeout.connect(self._check_health)
        self.health_timer.start(15000)
        QTimer.singleShot(0, self._check_health)
        QTimer.singleShot(0, self._resume_interrupted)

        if not self.cfg.token:
            QTimer.singleShot(200, self._first_run)

    # ----- UI ----------------------------------------------------------
    def _build_ui(self) -> None:
        tb = QToolBar("Main")
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.addToolBar(tb)

        def action(text: str, slot, shortcut: str | None = None) -> QAction:
            a = QAction(text, self)
            if shortcut:
                a.setShortcut(shortcut)
            a.triggered.connect(slot)
            tb.addAction(a)
            return a

        self.send_action = action("Send to Slicer…", self._send, "Ctrl+N")
        tb.addSeparator()
        self.open_action = action("Open in Bambu Studio", self._open_selected, "Ctrl+O")
        self.folder_action = action("Show in folder", self._show_folder)
        self.resend_action = action("Resend", self._resend)
        self.remove_action = action("Remove", self._remove, "Del")
        tb.addSeparator()
        action("Settings…", self._settings)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(5, QHeaderView.Stretch)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.doubleClicked.connect(lambda _: self._open_selected())

        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setPlaceholderText("Select a job to see warnings and the slicer log.")

        split = QSplitter(Qt.Vertical)
        split.addWidget(self.table)
        split.addWidget(self.details)
        split.setSizes([380, 140])
        self.setCentralWidget(split)

        self.conn_label = QLabel("Slicer: checking…")
        self.statusBar().addPermanentWidget(self.conn_label)
        self.setAcceptDrops(True)
        self._selection_changed()

    def _refresh_table(self) -> None:
        selected = self._selected()
        self.table.setRowCount(len(self.jobs))
        for row, job in enumerate(self.jobs):
            sent = job.created_at.replace("T", " ")[:16]
            details = job.message
            if job.warnings:
                details = f"⚠ {details}" if details else "⚠ see warnings below"
            values = [sent, job.source_name, job.plate_label, job.printer,
                      STATUS_TEXT.get(job.status, job.status.value), details]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.UserRole, job.job_id)
                self.table.setItem(row, col, item)
        if selected:
            for row, job in enumerate(self.jobs):
                if job.job_id == selected.job_id:
                    self.table.selectRow(row)
        self._selection_changed()

    def _selected(self) -> LocalJob | None:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return None
        job_id = self.table.item(rows[0].row(), 0).data(Qt.UserRole)
        return next((j for j in self.jobs if j.job_id == job_id), None)

    def _selection_changed(self) -> None:
        job = self._selected()
        has_result = bool(job and self.store.result_path(job))
        self.open_action.setEnabled(has_result)
        self.folder_action.setEnabled(job is not None)
        self.resend_action.setEnabled(job is not None and job.status.finished)
        self.remove_action.setEnabled(job is not None)
        if not job:
            self.details.clear()
            return
        parts = [f"Job {job.job_id}", f"Project: {job.source_path}"]
        if job.printer:
            parts.append(f"Print on: {job.printer}")
        if job.message:
            parts.append(job.message)
        parts += [f"⚠ {w}" for w in job.warnings]
        if job.log_tail:
            parts += ["", "--- Slicer log (end) ---", job.log_tail]
        self.details.setPlainText("\n".join(parts))

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        urls = event.mimeData().urls()
        if urls and urls[0].toLocalFile().lower().endswith(".3mf"):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        self._send(Path(event.mimeData().urls()[0].toLocalFile()))

    # ----- helpers -----------------------------------------------------
    def _client(self) -> SlicerClient:
        return SlicerClient(self.cfg.slicer_url, self.cfg.token)

    def _run(self, fn, on_done=None, on_fail=None) -> None:
        task = Task(fn)
        # Keep a Python reference until the task reports back, otherwise its
        # signal object can be garbage-collected before the result is delivered.
        self._tasks.add(task)
        task.setAutoDelete(False)
        task.signals.done.connect(lambda _r: self._tasks.discard(task))
        task.signals.failed.connect(lambda _e: self._tasks.discard(task))
        if on_done:
            task.signals.done.connect(on_done)
        if on_fail:
            task.signals.failed.connect(on_fail)
        self.pool.start(task)

    def _update(self, job: LocalJob, **changes) -> None:
        for k, v in changes.items():
            setattr(job, k, v)
        self.store.save(job)
        self._refresh_table()

    def _bambu_exe(self) -> Path | None:
        try:
            return bambu.find_bambu_studio(self.cfg.bambu_studio_path or None)
        except bambu.BambuStudioNotFound:
            return None

    # ----- actions -----------------------------------------------------
    def _first_run(self) -> None:
        QMessageBox.information(
            self, "Welcome",
            "Enter the Slicer's address and access token to get started.\n\n"
            "On the Slicer PC, run:  3dpu-slicer show-token",
        )
        self._settings()

    def _settings(self) -> None:
        dlg = SettingsDialog(self, self.cfg)
        if dlg.exec() == QDialog.Accepted:
            self.cfg = dlg.apply()
            config.save(self.cfg, self.cfg_path)
            if Path(self.cfg.jobs_dir) != self.store.jobs_dir:
                self.store = JobStore(Path(self.cfg.jobs_dir))
                self.jobs = self.store.load_all()
                self._refresh_table()
            self._check_health()

    def _send(self, preset: Path | None = None) -> None:
        dlg = SendDialog(self, self.cfg.printers, self._last_printer)
        if isinstance(preset, Path):
            dlg.path_edit.setText(str(preset))
        if dlg.exec() != QDialog.Accepted:
            return
        self._last_printer = dlg.printer
        self._start_job(dlg.project, dlg.plate, dlg.printer)

    def _resend(self) -> None:
        job = self._selected()
        if not job:
            return
        source = Path(job.source_path)
        if not source.is_file():
            source = self.store.snapshot_path(job)
        self._start_job(source, job.plate, job.printer, original_path=job.source_path)

    def _start_job(self, project: Path, plate: int, printer: str,
                   original_path: str | None = None) -> None:
        job = LocalJob(
            job_id=new_job_id(),
            source_path=original_path or str(project),
            source_name=project.name,
            plate=plate,
            printer=printer,
        )
        try:
            self.store.create(job, project)
        except OSError as exc:
            QMessageBox.critical(self, "Could not read project", str(exc))
            return
        self.jobs.insert(0, job)
        self._upload(job)

    def _upload(self, job: LocalJob) -> None:
        self._update(job, status=JobStatus.UPLOADING, message="")
        self._in_flight.add(job.job_id)
        snapshot = self.store.snapshot_path(job)
        exe = self._bambu_exe()
        manager_version = bambu.exe_version(exe) if exe else None
        request = JobRequest(job.job_id, job.plate, job.printer, job.source_name, manager_version)

        def run():
            with self._client() as c:
                return c.submit(request, snapshot)

        def done(state):
            self._in_flight.discard(job.job_id)
            self._update(job, status=state.status, warnings=state.warnings, message=state.message)

        def failed(err):
            self._in_flight.discard(job.job_id)
            self._update(job, status=JobStatus.FAILED, message=f"Upload failed: {err.splitlines()[0]}")

        self._run(run, done, failed)

    def _resume_interrupted(self) -> None:
        """Jobs that never reached Slicer (e.g. the app closed mid-upload) are retried."""
        for job in self.jobs:
            if job.status in (JobStatus.PENDING_UPLOAD, JobStatus.UPLOADING):
                self._upload(job)

    def _poll(self) -> None:
        active = [j for j in self.jobs
                  if j.status in (JobStatus.QUEUED, JobStatus.SLICING, JobStatus.DONE)
                  and j.job_id not in self._in_flight]
        for job in active:
            self._in_flight.add(job.job_id)
            self._run(*self._poll_task(job))

    def _poll_task(self, job: LocalJob):
        result_name = result_name_for(job.source_name)
        dest = self.store.folder(job) / result_name

        def run():
            with self._client() as c:
                state = c.status(job.job_id)
                if state.status == JobStatus.DONE:
                    c.download_result(job.job_id, dest)
                    try:
                        c.delete(job.job_id)  # free space on Slicer
                    except SlicerError:
                        pass
                return state

        def done(state):
            self._in_flight.discard(job.job_id)
            changes = dict(message=state.message, warnings=state.warnings, log_tail=state.log_tail)
            if state.status == JobStatus.DONE:
                changes.update(status=JobStatus.READY, result_name=result_name)
            elif state.status == JobStatus.QUEUED and state.queue_position:
                changes.update(status=state.status,
                               message=f"{state.queue_position} job(s) ahead")
            else:
                changes.update(status=state.status)
            self._update(job, **changes)
            if job.status == JobStatus.READY:
                self.statusBar().showMessage(f"{job.source_name} is sliced and ready.", 10000)
                QApplication.alert(self)
                if self.cfg.open_result_automatically:
                    self._open(job)

        def failed(err):
            self._in_flight.discard(job.job_id)
            if "No such job" in err:
                self._update(job, status=JobStatus.FAILED,
                             message="Slicer no longer has this job. Use Resend.")
            # Otherwise: transient network error, try again on the next poll.

        return run, done, failed

    def _check_health(self) -> None:
        def done(h):
            ver = h.get("bambu_studio_version") or "unknown version"
            text = f"Slicer: connected · Bambu Studio {ver}"
            exe = self._bambu_exe()
            local = bambu.exe_version(exe) if exe else None
            if bambu.same_version(local, h.get("bambu_studio_version")) is False:
                text += f" · ⚠ this PC has {local}"
            self.conn_label.setText(text)

        def failed(err):
            self.conn_label.setText("Slicer: not reachable")
            self.conn_label.setToolTip(err.splitlines()[0])

        def run():
            with self._client() as c:
                return c.health()

        self._run(run, done, failed)

    def _open_selected(self) -> None:
        job = self._selected()
        if job:
            self._open(job)

    def _open(self, job: LocalJob) -> None:
        result = self.store.result_path(job)
        if not result:
            return
        exe = self._bambu_exe()
        try:
            if exe:
                subprocess.Popen([str(exe), str(result)], close_fds=True)
            elif sys.platform == "win32":
                os.startfile(result)  # type: ignore[attr-defined]
            else:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(result)))
        except OSError as exc:
            QMessageBox.warning(self, "Could not open Bambu Studio", str(exc))
            return
        if job.printer:
            self.statusBar().showMessage(
                f"Opened {result.name}. Print it on {job.printer}.", 15000
            )

    def _show_folder(self) -> None:
        job = self._selected()
        if not job:
            return
        folder = self.store.folder(job)
        target = self.store.result_path(job) or self.store.snapshot_path(job)
        if sys.platform == "win32" and target.exists():
            subprocess.Popen(["explorer", "/select,", str(target)])
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _remove(self) -> None:
        job = self._selected()
        if not job:
            return
        if QMessageBox.question(
            self, "Remove job", f"Remove {job.source_name} from the list and delete its files?"
        ) != QMessageBox.Yes:
            return
        if not job.status.finished:
            job_id = job.job_id

            def cancel():
                with self._client() as c:
                    c.delete(job_id)

            self._run(cancel)
        self.store.remove(job)
        self.jobs.remove(job)
        self._refresh_table()


def main(argv: list[str] | None = None) -> int:
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("3dpu Manager")
    for style in ("windows11", "windowsvista"):
        if style in [s.lower() for s in QStyleFactory.keys()]:
            app.setStyle(style)
            break
    window = MainWindow()
    window.show()
    return app.exec()
