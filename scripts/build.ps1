<#
.SYNOPSIS
  Build standalone .exe files with PyInstaller (no Python needed on the target PC).
  Output: dist\3dpu-manager.exe and dist\3dpu-slicer.exe
#>
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Push-Location $repo
try {
    py -3 -m pip install -e ".[slicer,manager]" pyinstaller
    py -3 -m PyInstaller --noconfirm --onefile --windowed --name 3dpu-manager `
        --paths src src\threedpu\manager\__main__.py
    py -3 -m PyInstaller --noconfirm --onefile --console --name 3dpu-slicer `
        --paths src --collect-submodules uvicorn src\threedpu\slicer\__main__.py
} finally {
    Pop-Location
}
