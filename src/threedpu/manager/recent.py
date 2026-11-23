"""Suggest the project the user most likely just saved in Bambu Studio.

Bambu Studio keeps its recent projects in ``%APPDATA%\\BambuStudio\\BambuStudio.conf``.
The exact layout of that file is not documented and has changed between
versions, so we don't depend on it: we pull every ``.3mf`` path out of the
file and pick the one that exists and was modified most recently.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Iterator

_PATH_RE = re.compile(r'"([^"\r\n]+?\.3mf)"', re.IGNORECASE)


def bambu_conf_path() -> Path | None:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    return Path(appdata) / "BambuStudio" / "BambuStudio.conf"


def _walk_strings(obj) -> Iterator[str]:
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _walk_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_strings(v)


def recent_projects_from_text(text: str) -> list[str]:
    body = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    found: list[str] = []
    try:
        found = [s for s in _walk_strings(json.loads(body)) if s.lower().endswith(".3mf")]
    except ValueError:
        pass
    if not found:
        found = [m.group(1).replace("\\\\", "\\") for m in _PATH_RE.finditer(text)]
    return found


def most_recent_project(conf: Path | None = None) -> Path | None:
    conf = conf or bambu_conf_path()
    if conf is None or not conf.is_file():
        return None
    try:
        text = conf.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    best: tuple[float, Path] | None = None
    for raw in recent_projects_from_text(text):
        p = Path(raw)
        if p.name.lower().endswith(".gcode.3mf"):
            continue
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        if best is None or mtime > best[0]:
            best = (mtime, p)
    return best[1] if best else None
