import json
from pathlib import Path

import pytest

from threedpu.common import bambu, config, threemf
from threedpu.common.jobs import JobRequest, JobState, JobStatus, new_job_id
from tests.conftest import make_project


def test_application_version_and_plates(tmp_path):
    p = make_project(tmp_path / "a.3mf", plates=3, version="02.04.00.10")
    assert threemf.application_version(p) == "BambuStudio-02.04.00.10"
    assert threemf.plate_numbers(p) == [1, 2, 3]
    assert threemf.sliced_plates(p) == []


def test_sliced_plates(tmp_path):
    p = make_project(tmp_path / "a.gcode.3mf", plates=2, sliced=(2,))
    assert threemf.sliced_plates(p) == [2]


def test_not_a_3mf(tmp_path):
    bad = tmp_path / "bad.3mf"
    bad.write_text("nope")
    with pytest.raises(threemf.Not3MF):
        threemf.plate_numbers(bad)


def test_version_compare():
    assert bambu.normalize_version("BambuStudio-02.03.00.70") == (2, 3, 0, 70)
    assert bambu.same_version("BambuStudio-02.03.00.70", "2.3.0.71") is True
    assert bambu.same_version("2.3.0.70", "2.4.0.1") is False
    assert bambu.same_version(None, "2.4.0.1") is None


def test_find_bambu_configured_dir(tmp_path):
    d = tmp_path / "Bambu Studio"
    d.mkdir()
    (d / "bambu-studio.exe").write_text("")
    (d / "BambuStudio.dll").write_text("")
    assert bambu.find_bambu_studio(d) == d / "bambu-studio.exe"
    assert bambu.find_bambu_studio(d / "bambu-studio.exe") == d / "bambu-studio.exe"


def test_find_bambu_program_files(tmp_path, monkeypatch):
    d = tmp_path / "Bambu Studio"
    d.mkdir()
    (d / "bambu-studio.exe").write_text("")
    monkeypatch.delenv(bambu.ENV_VAR, raising=False)
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    assert bambu.find_bambu_studio() == d / "bambu-studio.exe"


def test_find_bambu_missing(tmp_path, monkeypatch):
    for var in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)", "LOCALAPPDATA", bambu.ENV_VAR):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(bambu, "_registry_dirs", lambda: [])  # a real install may be registered
    with pytest.raises(bambu.BambuStudioNotFound):
        bambu.find_bambu_studio()
    with pytest.raises(bambu.BambuStudioNotFound):
        bambu.find_bambu_studio(tmp_path / "nope")


def test_slicer_config_creates_token(tmp_path):
    path = tmp_path / "slicer.json"
    cfg = config.load_slicer_config(path)
    assert cfg.token and path.is_file()
    assert config.load_slicer_config(path).token == cfg.token


def test_config_ignores_unknown_keys(tmp_path):
    path = tmp_path / "m.json"
    path.write_text(json.dumps({"slicer_url": "http://x:1", "bogus": 1}))
    assert config.load_manager_config(path).slicer_url == "http://x:1"


def test_job_request_validation():
    good = JobRequest.from_dict({"job_id": new_job_id(), "plate": 2})
    assert good.plate == 2
    with pytest.raises(ValueError):
        JobRequest.from_dict({"job_id": "../../etc"})
    with pytest.raises(ValueError):
        JobRequest.from_dict({"job_id": new_job_id(), "plate": -1})


def test_job_state_roundtrip():
    s = JobState("abc123", JobStatus.SLICING, warnings=["w"])
    assert JobState.from_dict(s.to_dict()) == s
