"""JSON config files for the Slicer service and the Manager app."""

from __future__ import annotations

import json
import os
import secrets
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, TypeVar

DEFAULT_PORT = 8765
TOKEN_HEADER = "X-3DPU-Token"

T = TypeVar("T")


def _windows_dir(env: str, fallback: Path) -> Path:
    value = os.environ.get(env)
    return Path(value) if value else fallback


def slicer_config_path() -> Path:
    override = os.environ.get("THREEDPU_SLICER_CONFIG")
    if override:
        return Path(override)
    if sys.platform == "win32":
        return _windows_dir("PROGRAMDATA", Path("C:/ProgramData")) / "3dpu" / "slicer.json"
    return Path.home() / ".config" / "3dpu" / "slicer.json"


def manager_config_path() -> Path:
    override = os.environ.get("THREEDPU_MANAGER_CONFIG")
    if override:
        return Path(override)
    if sys.platform == "win32":
        return _windows_dir("APPDATA", Path.home() / "AppData" / "Roaming") / "3dpu" / "manager.json"
    return Path.home() / ".config" / "3dpu" / "manager.json"


def _default_slicer_workdir() -> str:
    if sys.platform == "win32":
        return str(_windows_dir("PROGRAMDATA", Path("C:/ProgramData")) / "3dpu" / "work")
    return str(Path.home() / ".local" / "share" / "3dpu" / "work")


def _default_jobs_dir() -> str:
    return str(Path.home() / "3dpu" / "jobs")


@dataclass
class SlicerConfig:
    host: str = "0.0.0.0"
    port: int = DEFAULT_PORT
    token: str = ""
    bambu_studio_path: str = ""
    work_dir: str = field(default_factory=_default_slicer_workdir)
    slice_timeout_seconds: int = 1800
    # Extra CLI flags appended to every slice, e.g. ["--allow-newer-file"].
    extra_cli_args: list[str] = field(default_factory=list)
    # Work folders older than this are deleted on startup and after each job.
    keep_work_days: int = 3


@dataclass
class ManagerConfig:
    slicer_url: str = ""  # e.g. http://<slicer PC name or IP>:8765, set in Settings
    token: str = ""
    bambu_studio_path: str = ""
    jobs_dir: str = field(default_factory=_default_jobs_dir)
    printers: list[str] = field(
        default_factory=lambda: [f"P1S #{i}" for i in range(1, 6)] + ["H2D #1", "H2D #2"]
    )
    open_result_automatically: bool = True
    poll_seconds: float = 2.0


def _from_dict(cls: type[T], data: dict[str, Any]) -> T:
    known = {f.name for f in fields(cls)}  # type: ignore[arg-type]
    return cls(**{k: v for k, v in data.items() if k in known})


def load(cls: type[T], path: Path) -> T:
    if path.is_file():
        with path.open(encoding="utf-8") as fh:
            return _from_dict(cls, json.load(fh))
    return cls()


def save(config: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(asdict(config), fh, indent=2)
    tmp.replace(path)


def load_slicer_config(path: Path | None = None) -> SlicerConfig:
    """Load the Slicer config, creating it (with a fresh token) on first run."""
    path = path or slicer_config_path()
    cfg = load(SlicerConfig, path)
    if not cfg.token or not path.is_file():
        cfg.token = cfg.token or secrets.token_urlsafe(24)
        save(cfg, path)
    return cfg


def load_manager_config(path: Path | None = None) -> ManagerConfig:
    return load(ManagerConfig, path or manager_config_path())
