import json
import time

import pytest
from fastapi.testclient import TestClient

from threedpu.common import threemf
from threedpu.common.config import TOKEN_HEADER
from threedpu.common.jobs import new_job_id
from threedpu.slicer.jobqueue import JobQueue
from threedpu.slicer.runner import SliceRunner, build_command
from threedpu.slicer.service import create_app
from tests.conftest import make_project

TOKEN = "secret-token"


@pytest.fixture
def client(tmp_path, fake_bambu):
    jobs = JobQueue(
        tmp_path / "work",
        lambda: SliceRunner(fake_bambu, timeout_seconds=10, extra_args=[]),
        slicer_bambu_version="2.3.0.70",
    )
    app = create_app(jobs, TOKEN, fake_bambu, "2.3.0.70")
    with TestClient(app, headers={TOKEN_HEADER: TOKEN}) as c:
        yield c


def submit(client, project, plate=1, **extra):
    job = {"job_id": new_job_id(), "plate": plate, "printer": "P1S #2",
           "source_name": project.name, **extra}
    with project.open("rb") as fh:
        return client.post("/jobs", data={"job": json.dumps(job)}, files={"file": (project.name, fh)})


def wait(client, job_id, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = client.get(f"/jobs/{job_id}").json()
        if state["status"] in ("done", "failed", "cancelled"):
            return state
        time.sleep(0.1)
    raise AssertionError("job did not finish")


def test_command_never_arranges_or_orients(tmp_path):
    cmd = build_command(tmp_path / "bambu-studio.exe", tmp_path / "in.3mf",
                        tmp_path / "out.gcode.3mf", 2, [])
    assert "--arrange" not in cmd and "--orient" not in cmd and "--load-settings" not in cmd
    assert cmd[1:3] == ["--slice", "2"]
    assert cmd[-1].endswith("in.3mf")


def test_requires_token(client):
    assert client.get("/health", headers={TOKEN_HEADER: "wrong"}).status_code == 401
    assert client.get("/health").json()["bambu_studio_version"] == "2.3.0.70"


def test_slice_and_download(client, project, tmp_path):
    resp = submit(client, project, plate=2)
    assert resp.status_code == 201, resp.text
    job_id = resp.json()["job_id"]
    state = wait(client, job_id)
    assert state["status"] == "done", state
    assert state["printer"] == "P1S #2"
    result = client.get(f"/jobs/{job_id}/result")
    assert result.status_code == 200
    out = tmp_path / "dl.gcode.3mf"
    out.write_bytes(result.content)
    assert threemf.sliced_plates(out) == [2]
    assert client.delete(f"/jobs/{job_id}").status_code == 200
    assert client.get(f"/jobs/{job_id}").status_code == 404


def test_failure_reports_log(client, project, monkeypatch):
    monkeypatch.setenv("FAKE_BAMBU_MODE", "fail")
    state = wait(client, submit(client, project).json()["job_id"])
    assert state["status"] == "failed"
    assert "code 3" in state["message"]
    assert "something broke" in state["log_tail"]


def test_no_result_is_failure(client, project, monkeypatch):
    monkeypatch.setenv("FAKE_BAMBU_MODE", "noresult")
    state = wait(client, submit(client, project).json()["job_id"])
    assert state["status"] == "failed"
    assert "no result" in state["message"]


def test_cancel_running(client, project, monkeypatch):
    monkeypatch.setenv("FAKE_BAMBU_MODE", "slow")
    job_id = submit(client, project).json()["job_id"]
    for _ in range(50):
        if client.get(f"/jobs/{job_id}").json()["status"] == "slicing":
            break
        time.sleep(0.1)
    assert client.delete(f"/jobs/{job_id}").status_code == 200
    assert wait(client, job_id)["status"] == "cancelled"


def test_bad_plate_and_bad_file(client, project, tmp_path):
    assert submit(client, project, plate=9).status_code == 400
    bad = tmp_path / "bad.3mf"
    bad.write_text("not a zip")
    assert submit(client, bad).status_code == 400


def test_version_mismatch_warns(client, tmp_path):
    p = make_project(tmp_path / "old.3mf", version="01.09.00.00")
    body = submit(client, p, manager_bambu_version="2.4.0.0").json()
    assert len(body["warnings"]) == 2


def test_duplicate_job_id(client, project):
    job = {"job_id": "dup-job-1", "plate": 1}
    for expected in (201, 409):
        with project.open("rb") as fh:
            r = client.post("/jobs", data={"job": json.dumps(job)}, files={"file": ("x.3mf", fh)})
        assert r.status_code == expected


def test_restart_marks_unfinished_failed(tmp_path, fake_bambu, project):
    work = tmp_path / "w"
    q = JobQueue(work, lambda: SliceRunner(fake_bambu, 10, []), None)
    from threedpu.common.jobs import JobRequest
    q.submit(JobRequest("restart-1", 1), lambda p: p.write_bytes(project.read_bytes()))
    # never started; simulate restart
    q2 = JobQueue(work, lambda: SliceRunner(fake_bambu, 10, []), None)
    assert q2.get("restart-1").status.value == "failed"
