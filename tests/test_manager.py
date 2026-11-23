import json
import os
import socket
import threading
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from threedpu.common import config, threemf  # noqa: E402
from threedpu.common.jobs import JobStatus  # noqa: E402
from threedpu.manager.client import SlicerClient, SlicerError  # noqa: E402
from threedpu.manager.jobstore import JobStore, LocalJob, result_name_for  # noqa: E402
from threedpu.manager.recent import most_recent_project, recent_projects_from_text  # noqa: E402
from tests.conftest import make_project  # noqa: E402

TOKEN = "tok"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server(tmp_path, fake_bambu):
    import uvicorn

    from threedpu.slicer.jobqueue import JobQueue
    from threedpu.slicer.runner import SliceRunner
    from threedpu.slicer.service import create_app

    jobs = JobQueue(tmp_path / "slicer-work", lambda: SliceRunner(fake_bambu, 10, []), "2.3.0.70")
    port = free_port()
    srv = uvicorn.Server(uvicorn.Config(create_app(jobs, TOKEN, fake_bambu, "2.3.0.70"),
                                        host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    while not srv.started:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    t.join(5)


def test_result_name():
    assert result_name_for("Dragon Head.3mf") == "Dragon Head.gcode.3mf"
    assert result_name_for("x.gcode.3mf") == "x.gcode.3mf"


def test_recent_projects(tmp_path):
    old = make_project(tmp_path / "old.3mf")
    new = make_project(tmp_path / "new.3mf")
    os.utime(old, (1, 1))
    conf = tmp_path / "BambuStudio.conf"
    conf.write_text(json.dumps({"recent_projects": {"1": str(old), "2": str(new),
                                                    "3": str(tmp_path / "gone.3mf")}})
                    + "\n# MD5 checksum ABC\n")
    assert most_recent_project(conf) == new
    # Falls back to scanning the text if the file isn't valid JSON
    assert recent_projects_from_text('junk "C:\\\\a\\\\b.3mf" junk') == ["C:\\a\\b.3mf"]


def test_jobstore_roundtrip(tmp_path, project):
    store = JobStore(tmp_path / "jobs")
    job = store.create(LocalJob("job-abc-1", str(project), project.name, 1, "H2D #1"), project)
    assert store.snapshot_path(job).read_bytes() == project.read_bytes()
    job.status = JobStatus.QUEUED
    store.save(job)
    assert store.load_all()[0].status == JobStatus.QUEUED
    store.remove(job)
    assert store.load_all() == []


def test_client_errors(server):
    with SlicerClient(server, "wrong") as c, pytest.raises(SlicerError, match="token"):
        c.health()
    with SlicerClient("http://127.0.0.1:1", TOKEN, timeout=1) as c, pytest.raises(SlicerError):
        c.health()
    with SlicerClient("http://no-such-host.invalid:8765", TOKEN, timeout=2) as c, \
            pytest.raises(SlicerError, match="Can't find a computer named 'no-such-host.invalid'"):
        c.health()
    with SlicerClient("", TOKEN) as c, pytest.raises(SlicerError, match="isn't set"):
        c.health()


def test_app_end_to_end(server, tmp_path, project, monkeypatch):
    """Send a project from the Manager window and wait until the result is downloaded."""
    from PySide6.QtWidgets import QApplication

    from threedpu.manager import app as app_mod

    qt = QApplication.instance() or QApplication([])
    cfg_path = tmp_path / "manager.json"
    cfg = config.ManagerConfig(slicer_url=server, token=TOKEN, jobs_dir=str(tmp_path / "mjobs"),
                               poll_seconds=0.2, open_result_automatically=True,
                               bambu_studio_path=str(tmp_path / "missing"))
    config.save(cfg, cfg_path)
    opened = []
    monkeypatch.setattr(app_mod.MainWindow, "_open", lambda self, job: opened.append(job.job_id))

    win = app_mod.MainWindow(cfg_path)
    win._start_job(project, 2, "P1S #4")
    job = win.jobs[0]
    deadline = time.time() + 20
    while job.status != JobStatus.READY and time.time() < deadline:
        qt.processEvents()
        time.sleep(0.02)
    assert job.status == JobStatus.READY, (job.status, job.message)
    result = win.store.result_path(job)
    assert result.name == "cube.gcode.3mf"
    assert threemf.sliced_plates(result) == [2]
    assert opened == [job.job_id]
    assert win.table.rowCount() == 1
    assert len(opened) == 1  # uploaded and opened exactly once
    win.poll_timer.stop()
    win.health_timer.stop()
    win.close()


def test_interrupted_upload_resumes(server, tmp_path, project):
    """A job left 'uploading' when the app closed is sent again on the next start."""
    from PySide6.QtWidgets import QApplication

    from threedpu.manager import app as app_mod

    qt = QApplication.instance() or QApplication([])
    cfg_path = tmp_path / "manager.json"
    cfg = config.ManagerConfig(slicer_url=server, token=TOKEN, jobs_dir=str(tmp_path / "mjobs"),
                               poll_seconds=0.2, open_result_automatically=False,
                               bambu_studio_path=str(tmp_path / "missing"))
    config.save(cfg, cfg_path)
    store = JobStore(tmp_path / "mjobs")
    job = store.create(LocalJob("resume-job-1", str(project), project.name, 1, "P1S #1"), project)
    job.status = JobStatus.UPLOADING
    store.save(job)

    win = app_mod.MainWindow(cfg_path)
    deadline = time.time() + 20
    while win.jobs[0].status != JobStatus.READY and time.time() < deadline:
        qt.processEvents()
        time.sleep(0.02)
    assert win.jobs[0].status == JobStatus.READY, win.jobs[0].message
    win.poll_timer.stop()
    win.health_timer.stop()
    win.close()


def test_slicer_info_window(server, tmp_path):
    """The Slicer info window shows the address and token and sees the running service."""
    from PySide6.QtWidgets import QApplication

    from threedpu.slicer.infogui import InfoWindow, service_command

    qt = QApplication.instance() or QApplication([])
    port = int(server.rsplit(":", 1)[1])
    cfg_path = tmp_path / "slicer.json"
    config.save(config.SlicerConfig(port=port, token=TOKEN, bambu_studio_path=str(tmp_path / "x")),
                cfg_path)
    w = InfoWindow(cfg_path)
    assert w.token.text() == TOKEN
    deadline = time.time() + 10
    while "Running" not in w.status.text() and time.time() < deadline:
        qt.processEvents()
        time.sleep(0.02)
    assert "Running" in w.status.text()
    assert "Not found" in w.bambu_label.text()
    assert service_command()[-3:] == ["-m", "threedpu.slicer", "serve"]
    w.timer.stop()
    w.close()


def test_serve_log_file(tmp_path, fake_bambu):
    """serve --log-file works with no console and stops via /shutdown."""
    import subprocess
    import sys

    port = free_port()
    cfg_path = tmp_path / "slicer.json"
    config.save(config.SlicerConfig(port=port, token=TOKEN, bambu_studio_path=str(fake_bambu),
                                    work_dir=str(tmp_path / "work"), host="127.0.0.1"), cfg_path)
    log = tmp_path / "slicer.log"
    proc = subprocess.Popen([sys.executable, "-m", "threedpu.slicer", "--config", str(cfg_path),
                             "serve", "--log-file", str(log)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                with SlicerClient(f"http://127.0.0.1:{port}", TOKEN, timeout=1) as c:
                    c.health()
                    c.shutdown()
                break
            except SlicerError:
                time.sleep(0.2)
        assert proc.wait(timeout=15) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
    text = log.read_text()
    assert "Using Bambu Studio" in text and "/shutdown" in text
