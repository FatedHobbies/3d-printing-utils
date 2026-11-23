"""Job model shared by the Slicer service and the Manager app."""

from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

JOB_ID_RE = re.compile(r"^[a-z0-9-]{6,64}$")


class JobStatus(str, Enum):
    # Manager-only states
    PENDING_UPLOAD = "pending_upload"
    UPLOADING = "uploading"
    # Shared states (reported by Slicer)
    QUEUED = "queued"
    SLICING = "slicing"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"
    # Manager-only state after the result has been downloaded
    READY = "ready"

    @property
    def finished(self) -> bool:
        return self in (JobStatus.DONE, JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.READY)


def new_job_id() -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{uuid.uuid4().hex[:6]}"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class JobRequest:
    """What Manager sends with the upload (as the 'job' form field, JSON)."""

    job_id: str
    plate: int = 0  # 0 = all plates
    printer: str = ""  # label of the physical printer, for display only
    source_name: str = ""  # original file name on Manager
    manager_bambu_version: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "JobRequest":
        job_id = str(data.get("job_id", ""))
        if not JOB_ID_RE.match(job_id):
            raise ValueError(f"invalid job_id: {job_id!r}")
        plate = int(data.get("plate", 0))
        if plate < 0:
            raise ValueError("plate must be >= 0")
        return cls(
            job_id=job_id,
            plate=plate,
            printer=str(data.get("printer", "")),
            source_name=str(data.get("source_name", "")),
            manager_bambu_version=data.get("manager_bambu_version"),
        )


@dataclass
class JobState:
    """What Slicer reports from GET /jobs/{id}."""

    job_id: str
    status: JobStatus
    plate: int = 0
    printer: str = ""
    source_name: str = ""
    created_at: str = field(default_factory=now_iso)
    started_at: str | None = None
    finished_at: str | None = None
    queue_position: int | None = None
    message: str = ""
    warnings: list[str] = field(default_factory=list)
    log_tail: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "JobState":
        data = dict(data)
        data["status"] = JobStatus(data["status"])
        allowed = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in data.items() if k in allowed})
