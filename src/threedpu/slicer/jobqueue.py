"""In-process job queue: one slice at a time, state persisted per job folder."""

from __future__ import annotations

import json
import logging
import queue
import shutil
import threading
import time
from pathlib import Path
from typing import Callable

from threedpu.common import bambu, threemf
from threedpu.common.jobs import JobRequest, JobState, JobStatus, now_iso
from threedpu.slicer.runner import RESULT_NAME, SliceRunner

log = logging.getLogger(__name__)

INPUT_NAME = "job.3mf"
STATE_NAME = "state.json"

RunnerFactory = Callable[[], SliceRunner]


class JobQueue:
    def __init__(
        self,
        work_dir: Path,
        runner_factory: RunnerFactory,
        slicer_bambu_version: str | None,
        keep_work_days: int = 3,
    ):
        self.work_dir = work_dir
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.runner_factory = runner_factory
        self.slicer_bambu_version = slicer_bambu_version
        self.keep_work_days = keep_work_days
        self._jobs: dict[str, JobState] = {}
        self._order: list[str] = []  # queued job ids in order
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._lock = threading.RLock()
        self._current: tuple[str, SliceRunner] | None = None
        self._thread: threading.Thread | None = None
        self._load_existing()
        self.cleanup_old()

    # ----- persistence -------------------------------------------------
    def job_dir(self, job_id: str) -> Path:
        return self.work_dir / job_id

    def _save(self, state: JobState) -> None:
        path = self.job_dir(state.job_id) / STATE_NAME
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state.to_dict(), indent=2), encoding="utf-8")
        tmp.replace(path)

    def _load_existing(self) -> None:
        for d in sorted(self.work_dir.iterdir()) if self.work_dir.is_dir() else []:
            state_file = d / STATE_NAME
            if not state_file.is_file():
                continue
            try:
                state = JobState.from_dict(json.loads(state_file.read_text(encoding="utf-8")))
            except (ValueError, KeyError, TypeError):
                continue
            if not state.status.finished:
                state.status = JobStatus.FAILED
                state.message = "Slicer service restarted before this job finished"
                state.finished_at = now_iso()
                self._save(state)
            self._jobs[state.job_id] = state

    def cleanup_old(self) -> None:
        cutoff = time.time() - self.keep_work_days * 86400
        with self._lock:
            for job_id, state in list(self._jobs.items()):
                d = self.job_dir(job_id)
                if state.status.finished and d.is_dir() and d.stat().st_mtime < cutoff:
                    shutil.rmtree(d, ignore_errors=True)
                    del self._jobs[job_id]

    # ----- public API --------------------------------------------------
    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._worker, name="slicer", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._queue.put(None)
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    def submit(self, request: JobRequest, write_input: Callable[[Path], None]) -> JobState:
        with self._lock:
            if request.job_id in self._jobs:
                raise FileExistsError(request.job_id)
            d = self.job_dir(request.job_id)
            d.mkdir(parents=True, exist_ok=False)
        input_path = d / INPUT_NAME
        try:
            write_input(input_path)
            app_version = threemf.application_version(input_path)
            plates = threemf.plate_numbers(input_path)
        except Exception:
            shutil.rmtree(d, ignore_errors=True)
            raise

        warnings: list[str] = []
        if request.plate and request.plate not in plates:
            shutil.rmtree(d, ignore_errors=True)
            raise ValueError(
                f"Plate {request.plate} does not exist in this project (plates: {plates})"
            )
        if threemf.sliced_plates(input_path):
            warnings.append("The uploaded project already contains G-code; it will be re-sliced.")
        for label, other in (
            ("Manager's Bambu Studio", request.manager_bambu_version),
            ("The project file", app_version),
        ):
            if bambu.same_version(other, self.slicer_bambu_version) is False:
                warnings.append(
                    f"{label} ({other}) differs from Slicer's Bambu Studio "
                    f"({self.slicer_bambu_version}). Results may differ from the GUI."
                )

        state = JobState(
            job_id=request.job_id,
            status=JobStatus.QUEUED,
            plate=request.plate,
            printer=request.printer,
            source_name=request.source_name,
            warnings=warnings,
        )
        with self._lock:
            self._jobs[state.job_id] = state
            self._order.append(state.job_id)
            self._save(state)
        self._queue.put(state.job_id)
        return self.get(state.job_id)  # type: ignore[return-value]

    def get(self, job_id: str) -> JobState | None:
        with self._lock:
            state = self._jobs.get(job_id)
            if state is None:
                return None
            if state.status == JobStatus.QUEUED and job_id in self._order:
                state.queue_position = self._order.index(job_id) + (1 if self._current else 0)
            else:
                state.queue_position = None
            return JobState.from_dict(state.to_dict())

    def list(self) -> list[JobState]:
        with self._lock:
            ids = list(self._jobs)
        return [s for s in (self.get(i) for i in ids) if s is not None]

    def result_path(self, job_id: str) -> Path | None:
        state = self.get(job_id)
        if state is None or state.status != JobStatus.DONE:
            return None
        p = self.job_dir(job_id) / RESULT_NAME
        return p if p.is_file() else None

    def cancel_or_delete(self, job_id: str) -> bool:
        with self._lock:
            state = self._jobs.get(job_id)
            if state is None:
                return False
            if state.status == JobStatus.QUEUED:
                if job_id in self._order:
                    self._order.remove(job_id)
                state.status = JobStatus.CANCELLED
                state.finished_at = now_iso()
                self._save(state)
                return True
            if state.status == JobStatus.SLICING and self._current and self._current[0] == job_id:
                self._current[1].cancel()
                return True
            # finished: delete files
            del self._jobs[job_id]
        shutil.rmtree(self.job_dir(job_id), ignore_errors=True)
        return True

    # ----- worker ------------------------------------------------------
    def _worker(self) -> None:
        while True:
            job_id = self._queue.get()
            if job_id is None:
                return
            with self._lock:
                state = self._jobs.get(job_id)
                if state is None or state.status != JobStatus.QUEUED:
                    continue
                if job_id in self._order:
                    self._order.remove(job_id)
                runner = self.runner_factory()
                self._current = (job_id, runner)
                state.status = JobStatus.SLICING
                state.started_at = now_iso()
                self._save(state)
            try:
                d = self.job_dir(job_id)
                result = runner.run(d, d / INPUT_NAME, state.plate)
                ok, message, tail = result.ok, result.message, result.log_tail
            except Exception as exc:  # noqa: BLE001 - report any failure to Manager
                log.exception("slice failed")
                ok, message, tail = False, f"Slicer error: {exc}", ""
            with self._lock:
                self._current = None
                cancelled = message == "Cancelled"
                state.status = (
                    JobStatus.DONE if ok else JobStatus.CANCELLED if cancelled else JobStatus.FAILED
                )
                state.message = message
                state.log_tail = tail
                state.finished_at = now_iso()
                self._save(state)
            self.cleanup_old()
