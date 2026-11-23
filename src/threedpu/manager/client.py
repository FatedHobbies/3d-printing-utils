"""HTTP client for the Slicer service."""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from threedpu.common.config import TOKEN_HEADER
from threedpu.common.jobs import JobRequest, JobState


class SlicerError(RuntimeError):
    pass


class SlicerClient:
    def __init__(self, base_url: str, token: str, timeout: float = 15.0):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(
            base_url=self.base_url,
            headers={TOKEN_HEADER: token},
            timeout=httpx.Timeout(timeout, read=300.0, write=300.0),
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "SlicerClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _check(self, resp: httpx.Response) -> httpx.Response:
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail", resp.text)
            except ValueError:
                detail = resp.text
            if resp.status_code == 401:
                detail = "Slicer rejected the access token. Check Settings."
            raise SlicerError(f"{detail} (HTTP {resp.status_code})")
        return resp

    def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        try:
            return self._check(self._client.request(method, url, **kwargs))
        except httpx.HTTPError as exc:
            raise SlicerError(f"Could not reach Slicer at {self.base_url}: {exc}") from exc

    def health(self) -> dict:
        return self._request("GET", "/health").json()

    def submit(self, request: JobRequest, project: Path) -> JobState:
        with project.open("rb") as fh:
            resp = self._request(
                "POST",
                "/jobs",
                data={"job": json.dumps(request.to_dict())},
                files={"file": (project.name, fh, "application/vnd.ms-package.3dmanufacturing-3dmodel+xml")},
            )
        return JobState.from_dict(resp.json())

    def status(self, job_id: str) -> JobState:
        return JobState.from_dict(self._request("GET", f"/jobs/{job_id}").json())

    def download_result(self, job_id: str, dest: Path) -> Path:
        tmp = dest.with_suffix(dest.suffix + ".part")
        try:
            with self._client.stream("GET", f"/jobs/{job_id}/result") as resp:
                if resp.status_code >= 400:
                    resp.read()
                    self._check(resp)
                with tmp.open("wb") as out:
                    for chunk in resp.iter_bytes(1024 * 1024):
                        out.write(chunk)
        except httpx.HTTPError as exc:
            tmp.unlink(missing_ok=True)
            raise SlicerError(f"Download failed: {exc}") from exc
        tmp.replace(dest)
        return dest

    def delete(self, job_id: str) -> None:
        self._request("DELETE", f"/jobs/{job_id}")
