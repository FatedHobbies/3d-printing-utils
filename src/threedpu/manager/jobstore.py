"""Manager's local job history: one folder per job under the jobs dir.

    <jobs_dir>/<job_id>/job.json           what we know about the job
    <jobs_dir>/<job_id>/<name>.3mf         snapshot of the project that was sent
    <jobs_dir>/<job_id>/<name>.gcode.3mf   the sliced result, once downloaded
"""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path

from threedpu.common.jobs import JobStatus, now_iso

RECORD_NAME = "job.json"


@dataclass
class LocalJob:
    job_id: str
    source_path: str
    source_name: str
    plate: int
    printer: str
    status: JobStatus = JobStatus.PENDING_UPLOAD
    message: str = ""
    warnings: list[str] = field(default_factory=list)
    log_tail: str = ""
    created_at: str = field(default_factory=now_iso)
    snapshot_name: str = ""
    result_name: str = ""

    @property
    def plate_label(self) -> str:
        return "All" if self.plate == 0 else str(self.plate)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "LocalJob":
        data = dict(data)
        data["status"] = JobStatus(data["status"])
        allowed = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in data.items() if k in allowed})


def result_name_for(source_name: str) -> str:
    stem = source_name
    for suffix in (".gcode.3mf", ".3mf", ".stl"):
        if stem.lower().endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    return f"{stem}.gcode.3mf"


class JobStore:
    def __init__(self, jobs_dir: Path):
        self.jobs_dir = jobs_dir
        self.jobs_dir.mkdir(parents=True, exist_ok=True)

    def folder(self, job: LocalJob | str) -> Path:
        job_id = job if isinstance(job, str) else job.job_id
        return self.jobs_dir / job_id

    def snapshot_path(self, job: LocalJob) -> Path:
        return self.folder(job) / job.snapshot_name

    def result_path(self, job: LocalJob) -> Path | None:
        if not job.result_name:
            return None
        p = self.folder(job) / job.result_name
        return p if p.is_file() else None

    def create(self, job: LocalJob, source: Path) -> LocalJob:
        """Copy the saved project into the job folder so later edits don't change the job."""
        folder = self.folder(job)
        folder.mkdir(parents=True, exist_ok=False)
        job.snapshot_name = source.name
        shutil.copy2(source, folder / job.snapshot_name)
        self.save(job)
        return job

    def save(self, job: LocalJob) -> None:
        path = self.folder(job) / RECORD_NAME
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(job.to_dict(), indent=2), encoding="utf-8")
        tmp.replace(path)

    def load_all(self) -> list[LocalJob]:
        jobs: list[LocalJob] = []
        for record in self.jobs_dir.glob(f"*/{RECORD_NAME}"):
            try:
                jobs.append(LocalJob.from_dict(json.loads(record.read_text(encoding="utf-8"))))
            except (ValueError, KeyError, TypeError):
                continue
        jobs.sort(key=lambda j: j.created_at, reverse=True)
        return jobs

    def remove(self, job: LocalJob) -> None:
        shutil.rmtree(self.folder(job), ignore_errors=True)
