# 3d-printing-utils

Utilities for using Bambu Lab 3D printers from networked Windows computers.

The goal is to use the computational power of one computer on the network for slicing, while
keeping it free of the other 3D printing tasks.

## Roles

There are two computers:

1. **Slicer** only slices. It never manages the printers. It's a powerful computer reserved
   for heavier computing work (including slicing).
2. **Manager** is where the user sits. It's connected to the printers, and it's where the user
   picks the file, printer, filament, and print profile.

The user prepares the plate in Bambu Studio on Manager. Slicer slices it. The result comes back
to Manager, where the user inspects it in Bambu Studio and prints it on the printer they chose.

See [issue #1](https://github.com/FatedHobbies/3d-printing-utils/issues/1) for the design
discussion.

## How it works

```
MANAGER                                             SLICER
Bambu Studio: prepare plate, Save Project
3dpu Manager app: "Send to Slicer"  ── HTTP ──►    3dpu-slicer service
                                                    bambu-studio.exe --slice N --export-3mf ...
3dpu Manager app downloads result  ◄── HTTP ──      result.gcode.3mf
Bambu Studio opens result: inspect, Print
```

* The saved project `.3mf` already holds the fully resolved printer, filament, and process
  settings (system and user presets, plus any last-minute changes), so no preset files are copied.
* Slicer slices exactly what was saved. It never passes `--arrange` or `--orient`.
* Bambu Studio on Manager still does the printing, so cloud and LAN printers both work as they do today.

> **Smart App Control / Application Control:** Windows may block the small unsigned launcher
> files a virtual environment creates (`pip.exe`, `3dpu-slicer.exe`, `3dpu-manager.exe`), with
> "An Application Control policy has blocked this file". Always run through the signed Python
> instead: `python.exe -m pip ...`, `python.exe -m threedpu.slicer ...`,
> `python.exe -m threedpu.manager`. If the venv's `python.exe` is blocked too, use the `py`
> launcher (`py -3 -m ...`) without a venv. The PyInstaller builds from `scripts\build.ps1` are
> unsigned too, so they may be blocked in the same way.

## Requirements

* Windows 10/11 on both PCs, on the same network.
* **The same Bambu Studio version on both PCs.** The app warns you when they differ.
* Python 3.11 or newer to install from source (or use the `.exe` files built with `scripts\build.ps1`).

## Setup

### Slicer PC

From an **elevated** PowerShell in a clone of this repo:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install-slicer.ps1
```

This script:

* installs the service into `C:\ProgramData\3dpu\venv`
* creates `C:\ProgramData\3dpu\slicer.json` with a random access token
* finds Bambu Studio (see below)
* opens TCP port 8765 in Windows Firewall, for **Private** networks only
* registers a scheduled task that starts the service, hidden, whenever you log on

At the end it prints the address and access token to enter on Manager.

**If Bambu Studio isn't found:** the CLI is `bambu-studio.exe`, in the same folder as
`BambuStudio.dll`. Discovery checks the config file, the `BAMBU_STUDIO_EXE` environment
variable, Program Files, per-user installs, the Windows registry, and `PATH`, in that order. To
set it by hand:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install-slicer.ps1 -BambuStudioPath "C:\Program Files\Bambu Studio"
# or edit "bambu_studio_path" in C:\ProgramData\3dpu\slicer.json, then check with:
C:\ProgramData\3dpu\venv\Scripts\python.exe -m threedpu.slicer find-bambu
```

### Manager PC

```powershell
py -3 -m venv "$env:USERPROFILE\3dpu\venv"
& "$env:USERPROFILE\3dpu\venv\Scripts\python.exe" -m pip install "<path to this repo>[manager]"
& "$env:USERPROFILE\3dpu\venv\Scripts\python.exe" -m threedpu.manager
```

On first start, the app asks for the Slicer address, made from the Slicer PC's computer name or IP (for example `http://RYAN-PC:8765`, or `http://127.0.0.1:8765` when both run on one PC), and the
access token. **Test connection** confirms both. You can also edit the printer list there. It
comes pre-filled with `P1S #1` to `#5` and `H2D #1`, `#2`.

### Trying both on one PC

You can run the service and the app on the same PC, for example to test without the Manager PC:

```powershell
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[slicer,manager]"
.venv\Scripts\python.exe -m threedpu.slicer show-token
.venv\Scripts\python.exe -m threedpu.slicer serve      # terminal 1
.venv\Scripts\python.exe -m threedpu.manager           # terminal 2, Slicer address http://127.0.0.1:8765
```

## Usage

1. On Manager, open the STL or 3MF in Bambu Studio. Choose the printer type, filaments, and
   process, then arrange and adjust the plate.
2. **Save Project** (Ctrl+S).
3. In 3dpu Manager, click **Send to Slicer** (or drag the `.3mf` onto the window). The app
   suggests the project you saved most recently in Bambu Studio. Pick the plate (or all plates)
   and the printer the job is for, then click **Send**.
4. The job list shows *Uploading → Queued → Slicing → Ready*.
5. When the job is ready, the sliced file opens in Bambu Studio. Inspect the preview.
6. Click **Print** in Bambu Studio and choose the printer shown on the job.

If the result isn't right, change the project in Bambu Studio, save, and send it again.
**Resend** re-slices a job from the list. If a slice fails, select the job to see the end of
Bambu Studio's log.

## Files

| Where | What |
|---|---|
| Manager `%USERPROFILE%\3dpu\jobs\<job id>\` | `job.json`, a copy of the project as sent, and `<name>.gcode.3mf` once sliced |
| Manager `%APPDATA%\3dpu\manager.json` | Manager settings |
| Slicer `C:\ProgramData\3dpu\slicer.json` | Slicer settings: port, token, Bambu Studio path, timeout, extra CLI flags |
| Slicer `C:\ProgramData\3dpu\work\<job id>\` | Upload, `slice.log`, and result. Deleted once Manager downloads the result, or after `keep_work_days` |
| Slicer `C:\ProgramData\3dpu\slicer.log` | Service log |

Use `extra_cli_args` in `slicer.json` to add flags to every slice, for example
`["--allow-newer-file"]`.

## Slicer HTTP API

All requests need the `X-3DPU-Token` header.

| Method and path | |
|---|---|
| `GET /health` | Service and Bambu Studio version |
| `POST /jobs` | Multipart: `job` (JSON: `job_id`, `plate`, `printer`, `source_name`, `manager_bambu_version`) and `file` (the `.3mf`) |
| `GET /jobs`, `GET /jobs/{id}` | Status: `queued`, `slicing`, `done`, `failed`, or `cancelled`, plus warnings and the log tail |
| `GET /jobs/{id}/result` | The sliced `.gcode.3mf` |
| `DELETE /jobs/{id}` | Cancels a queued or running job, or deletes a finished one |

## Development

```bash
python -m pip install -e ".[dev]"
python -m pytest -q          # set QT_QPA_PLATFORM=offscreen on headless machines
```

The tests use a fake `bambu-studio` executable, so they don't need Bambu Studio installed.
