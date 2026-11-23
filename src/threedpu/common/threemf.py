"""Small read-only helpers for Bambu Studio 3MF project files.

A 3MF is a zip archive. The parts we care about:

* ``3D/3dmodel.model``: XML with ``<metadata name="Application">BambuStudio-..``.
* ``Metadata/model_settings.config``: XML with one ``<plate>`` element per plate.
* ``Metadata/plate_N.gcode``: present only in sliced files (``.gcode.3mf``).
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

_PLATE_FILE_RE = re.compile(r"^Metadata/plate_(\d+)\.(?:png|json|gcode)$")
_GCODE_RE = re.compile(r"^Metadata/plate_(\d+)\.gcode$")


class Not3MF(ValueError):
    pass


def _open(path: Path) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(path)
    except (zipfile.BadZipFile, FileNotFoundError) as exc:
        raise Not3MF(f"{path} is not a readable 3MF file: {exc}") from exc


def application_version(path: Path) -> str | None:
    """Return the 'Application' metadata, e.g. 'BambuStudio-02.03.00.70'."""
    with _open(path) as zf:
        try:
            data = zf.read("3D/3dmodel.model")
        except KeyError:
            return None
    # The model file can be large; only parse the head where metadata lives.
    head = data[:65536].decode("utf-8", errors="replace")
    m = re.search(r'<metadata\s+name="Application"\s*>([^<]*)</metadata>', head)
    return m.group(1).strip() if m else None


def plate_numbers(path: Path) -> list[int]:
    """Return the plate numbers (1-based) in a project, at least [1]."""
    with _open(path) as zf:
        names = zf.namelist()
        plates: set[int] = set()
        if "Metadata/model_settings.config" in names:
            try:
                root = ET.fromstring(zf.read("Metadata/model_settings.config"))
                for plate in root.iter("plate"):
                    for md in plate.iter("metadata"):
                        if md.get("key") == "plater_id":
                            try:
                                plates.add(int(md.get("value", "")))
                            except ValueError:
                                pass
                if not plates:
                    count = sum(1 for _ in root.iter("plate"))
                    plates.update(range(1, count + 1))
            except ET.ParseError:
                pass
        if not plates:
            for name in names:
                m = _PLATE_FILE_RE.match(name)
                if m:
                    plates.add(int(m.group(1)))
    return sorted(plates) or [1]


def sliced_plates(path: Path) -> list[int]:
    """Return plate numbers that have G-code inside the file."""
    with _open(path) as zf:
        return sorted(
            int(m.group(1)) for m in (_GCODE_RE.match(n) for n in zf.namelist()) if m
        )
