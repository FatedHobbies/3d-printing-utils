"""Locate the Bambu Studio executable and read version information.

On Windows the CLI is the same ``bambu-studio.exe`` that launches the GUI. It
sits next to ``BambuStudio.dll`` in the install folder, which is not always
where people expect, so discovery tries several places in order:

1. An explicit path from config (``bambu_studio_path``).
2. The ``BAMBU_STUDIO_EXE`` environment variable.
3. Common install folders (Program Files, per-user installs).
4. The Windows registry (uninstall entries and App Paths).
5. ``PATH``.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path

EXE_NAMES = ("bambu-studio.exe", "BambuStudio.exe", "bambu-studio")
ENV_VAR = "BAMBU_STUDIO_EXE"


class BambuStudioNotFound(RuntimeError):
    pass


def _candidate_dirs() -> list[Path]:
    dirs: list[Path] = []
    for var in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
        base = os.environ.get(var)
        if base:
            dirs.append(Path(base) / "Bambu Studio")
            dirs.append(Path(base) / "BambuStudio")
    local = os.environ.get("LOCALAPPDATA")
    if local:
        dirs.append(Path(local) / "Programs" / "Bambu Studio")
        dirs.append(Path(local) / "Programs" / "BambuStudio")
    return dirs


def _registry_dirs() -> list[Path]:
    if sys.platform != "win32":
        return []
    import winreg  # type: ignore[import-not-found]

    found: list[Path] = []
    roots = (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER)
    uninstall_keys = (
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
        r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
    )
    for root in roots:
        for key_path in uninstall_keys:
            try:
                key = winreg.OpenKey(root, key_path)
            except OSError:
                continue
            with key:
                index = 0
                while True:
                    try:
                        sub_name = winreg.EnumKey(key, index)
                    except OSError:
                        break
                    index += 1
                    try:
                        with winreg.OpenKey(key, sub_name) as sub:
                            name, _ = winreg.QueryValueEx(sub, "DisplayName")
                            if "bambu studio" not in str(name).lower():
                                continue
                            for value in ("InstallLocation", "DisplayIcon"):
                                try:
                                    raw, _ = winreg.QueryValueEx(sub, value)
                                except OSError:
                                    continue
                                raw = str(raw).strip().strip('"').split(",")[0]
                                if raw:
                                    p = Path(raw)
                                    found.append(p if p.suffix.lower() != ".exe" else p.parent)
                    except OSError:
                        continue
        for exe in ("bambu-studio.exe", "BambuStudio.exe"):
            try:
                with winreg.OpenKey(
                    root, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}"
                ) as key:
                    raw, _ = winreg.QueryValueEx(key, "")
                    found.append(Path(str(raw).strip('"')).parent)
            except OSError:
                pass
    return found


def find_bambu_studio(configured: str | os.PathLike[str] | None = None) -> Path:
    """Return the path to the Bambu Studio executable, or raise BambuStudioNotFound."""
    tried: list[str] = []

    explicit = configured or os.environ.get(ENV_VAR)
    if explicit:
        p = Path(explicit)
        if p.is_dir():
            for name in EXE_NAMES:
                if (p / name).is_file():
                    return p / name
        elif p.is_file():
            return p
        raise BambuStudioNotFound(
            f"Configured Bambu Studio path does not exist or has no executable: {p}"
        )

    for d in [*_candidate_dirs(), *_registry_dirs()]:
        for name in EXE_NAMES:
            candidate = d / name
            tried.append(str(candidate))
            if candidate.is_file():
                return candidate

    for name in EXE_NAMES:
        on_path = shutil.which(name)
        if on_path:
            return Path(on_path)

    raise BambuStudioNotFound(
        "Could not find Bambu Studio. Set 'bambu_studio_path' in the config file "
        f"(or the {ENV_VAR} environment variable) to the folder containing "
        "bambu-studio.exe and BambuStudio.dll. Looked in:\n  " + "\n  ".join(tried)
    )


def exe_version(exe: Path) -> str | None:
    """Read the file version of a Windows executable, e.g. '2.3.0.70'. None elsewhere."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    ver = ctypes.WinDLL("version", use_last_error=True)
    size = ver.GetFileVersionInfoSizeW(str(exe), None)
    if not size:
        return None
    buf = ctypes.create_string_buffer(size)
    if not ver.GetFileVersionInfoW(str(exe), 0, size, buf):
        return None
    ptr = ctypes.c_void_p()
    length = wintypes.UINT()
    if not ver.VerQueryValueW(buf, "\\", ctypes.byref(ptr), ctypes.byref(length)):
        return None

    class VS_FIXEDFILEINFO(ctypes.Structure):
        _fields_ = [
            ("dwSignature", wintypes.DWORD),
            ("dwStrucVersion", wintypes.DWORD),
            ("dwFileVersionMS", wintypes.DWORD),
            ("dwFileVersionLS", wintypes.DWORD),
        ]

    info = VS_FIXEDFILEINFO.from_address(ptr.value)
    ms, ls = info.dwFileVersionMS, info.dwFileVersionLS
    return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"


_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)(?:\.(\d+))?")


def normalize_version(text: str | None) -> tuple[int, ...] | None:
    """Turn 'BambuStudio-02.03.00.70' or '2.3.0.70' into (2, 3, 0, 70)."""
    if not text:
        return None
    m = _VERSION_RE.search(text)
    if not m:
        return None
    return tuple(int(g) for g in m.groups() if g is not None)


def same_version(a: str | None, b: str | None) -> bool | None:
    """True/False if both versions are readable, None if either is unknown."""
    na, nb = normalize_version(a), normalize_version(b)
    if na is None or nb is None:
        return None
    return na[:3] == nb[:3]
