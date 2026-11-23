import stat
import sys
import zipfile
from pathlib import Path

import pytest

MODEL = """<?xml version="1.0" encoding="UTF-8"?>
<model unit="millimeter" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">
 <metadata name="Application">BambuStudio-{version}</metadata>
 <resources/><build/>
</model>"""

SETTINGS = """<?xml version="1.0" encoding="UTF-8"?>
<config>
{plates}
</config>"""

PLATE = """  <plate>
    <metadata key="plater_id" value="{n}"/>
    <metadata key="plater_name" value=""/>
  </plate>"""


def make_project(path: Path, plates: int = 1, version: str = "02.03.00.70", sliced=()) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("3D/3dmodel.model", MODEL.format(version=version))
        zf.writestr(
            "Metadata/model_settings.config",
            SETTINGS.format(plates="\n".join(PLATE.format(n=i) for i in range(1, plates + 1))),
        )
        zf.writestr("Metadata/project_settings.config", "{}")
        for n in sliced:
            zf.writestr(f"Metadata/plate_{n}.gcode", "; fake gcode\n")
    return path


FAKE_BAMBU = '''#!{python}
"""Stand-in for bambu-studio: copies the input and adds G-code for the plate(s)."""
import shutil, sys, time, zipfile, os
args = sys.argv[1:]
plate = int(args[args.index("--slice") + 1])
out = args[args.index("--export-3mf") + 1]
src = args[-1]
mode = os.environ.get("FAKE_BAMBU_MODE", "ok")
print("fake bambu", args)
if mode == "fail":
    print("error: something broke"); sys.exit(3)
if mode == "slow":
    time.sleep(5)
if mode == "noresult":
    sys.exit(0)
shutil.copy(src, out)
with zipfile.ZipFile(out, "a") as zf:
    for n in ([plate] if plate else [1, 2]):
        zf.writestr(f"Metadata/plate_{{n}}.gcode", "; sliced\\n")
'''


@pytest.fixture
def fake_bambu(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = FAKE_BAMBU.format(python=sys.executable)
    if sys.platform == "win32":
        (bin_dir / "fake_bambu.py").write_text(script)
        exe = bin_dir / "bambu-studio.cmd"
        exe.write_text(f'@"{sys.executable}" "%~dp0fake_bambu.py" %*\r\n')
        return exe
    exe = bin_dir / "bambu-studio"
    exe.write_text(script)
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return exe


@pytest.fixture
def project(tmp_path: Path) -> Path:
    return make_project(tmp_path / "cube.3mf", plates=2)
