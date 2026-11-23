"""HTTP API for the Slicer machine."""

from __future__ import annotations

import hmac
import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse

import threedpu
from threedpu.common import threemf
from threedpu.common.config import TOKEN_HEADER
from threedpu.common.jobs import JobRequest
from threedpu.slicer.jobqueue import JobQueue

MAX_UPLOAD_BYTES = 1024 * 1024 * 1024  # 1 GiB


def create_app(jobs: JobQueue, token: str, bambu_path: Path, bambu_version: str | None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        jobs.start()
        yield
        jobs.stop()

    app = FastAPI(title="3dpu Slicer", version=threedpu.__version__, lifespan=lifespan)

    def check_token(x_3dpu_token: str | None = Header(default=None, alias=TOKEN_HEADER)) -> None:
        if not token or not x_3dpu_token or not hmac.compare_digest(x_3dpu_token, token):
            raise HTTPException(status_code=401, detail="Missing or wrong access token")

    auth = [Depends(check_token)]

    @app.get("/health", dependencies=auth)
    def health() -> dict:
        return {
            "ok": True,
            "service_version": threedpu.__version__,
            "bambu_studio_path": str(bambu_path),
            "bambu_studio_version": bambu_version,
        }

    @app.post("/jobs", status_code=201, dependencies=auth)
    def submit(job: str = Form(...), file: UploadFile = File(...)) -> dict:
        try:
            request = JobRequest.from_dict(json.loads(job))
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=f"Bad job description: {exc}") from exc

        def write_input(dest: Path) -> None:
            written = 0
            with dest.open("wb") as out:
                while chunk := file.file.read(1024 * 1024):
                    written += len(chunk)
                    if written > MAX_UPLOAD_BYTES:
                        raise HTTPException(status_code=413, detail="Upload too large")
                    out.write(chunk)

        try:
            state = jobs.submit(request, write_input)
        except FileExistsError as exc:
            raise HTTPException(status_code=409, detail=f"Job {exc} already exists") from exc
        except threemf.Not3MF as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return state.to_dict()

    @app.get("/jobs", dependencies=auth)
    def list_jobs() -> list[dict]:
        return [s.to_dict() for s in jobs.list()]

    @app.get("/jobs/{job_id}", dependencies=auth)
    def get_job(job_id: str) -> dict:
        state = jobs.get(job_id)
        if state is None:
            raise HTTPException(status_code=404, detail="No such job")
        return state.to_dict()

    @app.get("/jobs/{job_id}/result", dependencies=auth)
    def get_result(job_id: str) -> FileResponse:
        path = jobs.result_path(job_id)
        if path is None:
            raise HTTPException(status_code=404, detail="No result for this job")
        return FileResponse(path, media_type="application/octet-stream", filename=path.name)

    @app.delete("/jobs/{job_id}", dependencies=auth)
    def delete_job(job_id: str) -> dict:
        if not jobs.cancel_or_delete(job_id):
            raise HTTPException(status_code=404, detail="No such job")
        return {"ok": True}

    return app
