"""Run the Bambu Studio CLI on one project file."""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from threedpu.common import threemf

RESULT_NAME = "result.gcode.3mf"
LOG_NAME = "slice.log"


@dataclass
class SliceResult:
    ok: bool
    result_path: Path | None
    returncode: int | None
    message: str
    log_tail: str


def build_command(
    exe: Path, input_path: Path, result_path: Path, plate: int, extra_args: list[str]
) -> list[str]:
    """The slice command.

    The project's own embedded settings are used as-is: no --load-settings, and
    never --arrange or --orient, so the plate is sliced exactly as the user saved it.
    """
    return [
        str(exe),
        "--slice",
        str(plate),
        *extra_args,
        "--export-3mf",
        str(result_path),
        str(input_path),
    ]


def _tail(path: Path, max_chars: int = 4000) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text[-max_chars:]


class SliceRunner:
    def __init__(self, exe: Path, timeout_seconds: int, extra_args: list[str]):
        self.exe = exe
        self.timeout_seconds = timeout_seconds
        self.extra_args = list(extra_args)
        self._proc: subprocess.Popen[bytes] | None = None
        self._lock = threading.Lock()
        self._cancelled = False

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True
            if self._proc and self._proc.poll() is None:
                self._proc.kill()

    def run(self, workdir: Path, input_path: Path, plate: int) -> SliceResult:
        result_path = workdir / RESULT_NAME
        log_path = workdir / LOG_NAME
        result_path.unlink(missing_ok=True)
        cmd = build_command(self.exe, input_path, result_path, plate, self.extra_args)

        creationflags = 0
        if sys.platform == "win32":
            creationflags = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]

        start = time.monotonic()
        with log_path.open("wb") as log:
            log.write(("$ " + subprocess.list2cmdline(cmd) + "\n\n").encode())
            log.flush()
            with self._lock:
                if self._cancelled:
                    return SliceResult(False, None, None, "Cancelled", "")
                # cwd = workdir: the CLI writes large temp files to its working dir.
                self._proc = subprocess.Popen(
                    cmd,
                    cwd=workdir,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    creationflags=creationflags,
                )
            try:
                returncode = self._proc.wait(timeout=self.timeout_seconds)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()
                return SliceResult(
                    False,
                    None,
                    None,
                    f"Slicing timed out after {self.timeout_seconds} s",
                    _tail(log_path),
                )
            finally:
                with self._lock:
                    self._proc = None

        elapsed = time.monotonic() - start
        tail = _tail(log_path)
        if self._cancelled:
            return SliceResult(False, None, returncode, "Cancelled", tail)
        if returncode != 0:
            return SliceResult(
                False, None, returncode, f"Bambu Studio exited with code {returncode}", tail
            )
        if not result_path.is_file():
            return SliceResult(
                False, None, returncode, "Bambu Studio finished but wrote no result file", tail
            )
        try:
            plates = threemf.sliced_plates(result_path)
        except threemf.Not3MF as exc:
            return SliceResult(False, None, returncode, str(exc), tail)
        if not plates:
            return SliceResult(
                False, None, returncode, "Result file contains no G-code", tail
            )
        names = ", ".join(str(p) for p in plates)
        return SliceResult(
            True, result_path, returncode, f"Sliced plate(s) {names} in {elapsed:.0f} s", tail
        )
