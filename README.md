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

## Requirements

* Windows 10/11 (64-bit) on both PCs, on the same network.
* **The same Bambu Studio version on both PCs.** The app warns you when they differ.

## Install (recommended)

Download `3dpu-setup-<version>.exe` from the
[Releases page](https://github.com/FatedHobbies/3d-printing-utils/releases). Builds from pull
requests are attached to the **installer** workflow run on the Actions tab, as the
`3dpu-installer` artifact. Run the installer on each PC and pick a setup type:

* **Slicer PC**: installs the service, starts it now and whenever you sign in, and (if you
  leave the box ticked) lets Manager PCs on your home network reach it through Windows Firewall.
  At the end it opens the **3dpu Slicer** window, which shows the address and access token to
  enter on Manager, each with a Copy button. You can open that window again from the Start menu
  any time. It also shows whether the service is running, has Start and Stop buttons, and lets
  you pick `bambu-studio.exe` if it wasn't found automatically.
* **Manager PC**: installs the **3dpu Manager** app (Start menu, plus an optional desktop
  shortcut). On first start, it asks for the address and token shown in the 3dpu Slicer window.
* **Both on this PC**: for trying it out. Use `http://127.0.0.1:8765` as the Slicer address.

Uninstall from Windows Settings → Apps. Your settings and job history are kept.

> **Smart App Control:** the installer isn't code-signed yet, so on PCs with Smart App Control
> turned on, Windows blocks the installer itself. After installation, everything runs through
> the signed `python.exe`/`pythonw.exe` from python.org, so the installed app isn't blocked.
> On those PCs, install from source for now (below).

## Install from source (advanced)

> Windows may block the small unsigned launcher files that a virtual environment creates
> (`pip.exe`, `3dpu-slicer.exe`, `3dpu-manager.exe`) with "An Application Control policy has
> blocked this file". Always go through Python instead: `python.exe -m pip ...`,
> `python.exe -m threedpu.slicer ...`, `python.exe -m threedpu.manager`. If the venv's
> `python.exe` is blocked too, use the `py` launcher (`py -3 -m ...`) without a venv.

### Slicer PC

From an **elevated** PowerShell in a clone of this repo:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install-slicer.ps1
```

This installs the service into `C:\ProgramData\3dpu\venv`, creates
`C:\ProgramData\3dpu\slicer.json` with a random access token, finds Bambu Studio, opens TCP port
8765 in Windows Firewall for **Private** networks only, and registers a scheduled task that
starts the service whenever you sign in. At the end it prints the address and token to enter on
Manager. Open the Slicer window with `python.exe -m threedpu.slicer info`.

### Manager PC

```powershell
py -3 -m venv "$env:USERPROFILE\3dpu\venv"
& "$env:USERPROFILE\3dpu\venv\Scripts\python.exe" -m pip install "<path to this repo>[manager]"
& "$env:USERPROFILE\3dpu\venv\Scripts\python.exe" -m threedpu.manager
```

### Trying both on one PC

```powershell
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[slicer,manager]"
.venv\Scripts\python.exe -m threedpu.slicer serve      # terminal 1
.venv\Scripts\python.exe -m threedpu.manager           # terminal 2
```

## Connecting Manager to Slicer

In 3dpu Manager → Settings, enter the Slicer address, made from the Slicer PC's computer name or
IP address (for example `http://RYAN-PC:8765`, or `http://127.0.0.1:8765` when both run on one
PC), and the access token. The 3dpu Slicer window shows both. **Test connection** checks them.
You can also edit the printer list there. It comes pre-filled with `P1S #1` to `#5` and
`H2D #1`, `#2`.

**If Bambu Studio isn't found:** the CLI is `bambu-studio.exe`, in the same folder as
`BambuStudio.dll`. Discovery checks the config file, the `BAMBU_STUDIO_EXE` environment variable,
Program Files, per-user installs, the Windows registry, and `PATH`, in that order. To set it by
hand, use **Choose…** in the 3dpu Slicer window, or set `"bambu_studio_path"` in
`C:\ProgramData\3dpu\slicer.json` and restart the service.

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

## Building the installer

`installer\build.ps1`, run on Windows with Python 3.12 on `PATH`, downloads the embeddable
Python from python.org, installs 3dpu into it, and compiles `installer\3dpu.iss` with Inno
Setup. The **installer** GitHub Actions workflow does the same, then installs the result
silently, starts the service, checks `/health`, and uninstalls. When `main` gets a version in
`pyproject.toml` that hasn't been released yet, the workflow publishes the installer as GitHub
release `v<version>`. To release, bump the version and merge. Pushing a `v*` tag also works.

## Development

```bash
python -m pip install -e ".[dev]"
python -m pytest -q          # set QT_QPA_PLATFORM=offscreen on headless machines
```

The tests use a fake `bambu-studio` executable, so they don't need Bambu Studio installed.
