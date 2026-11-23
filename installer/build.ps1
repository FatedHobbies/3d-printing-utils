<#
.SYNOPSIS
  Build the 3dpu Windows installer: installer\Output\3dpu-setup-<version>.exe

.DESCRIPTION
  1. Downloads the official embeddable Python (signed by the Python Software Foundation).
  2. Installs 3dpu and its dependencies into it.
  3. Compiles installer\3dpu.iss with Inno Setup (installed with Chocolatey if missing).

  Needs a full Python of the same minor version on PATH as "python" to run pip.
#>
param(
    [string]$PythonVersion = "3.12.10"
)
$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
$repo = Split-Path -Parent $here
$build = Join-Path $here "build"
$runtime = Join-Path $build "runtime"
$wheels = Join-Path $build "wheels"

$minor = ($PythonVersion -split "\.")[0..1] -join ""
$hostMinor = (& python -c "import sys;print(f'{sys.version_info[0]}{sys.version_info[1]}')").Trim()
if ($hostMinor -ne $minor) { throw "python on PATH is $hostMinor, need $minor to match the embedded runtime" }

$version = (& python -c "import tomllib;print(tomllib.load(open(r'$repo\pyproject.toml','rb'))['project']['version'])").Trim()
Write-Host "Building 3dpu $version with Python $PythonVersion"

if (Test-Path $build) { Remove-Item -Recurse -Force $build }
New-Item -ItemType Directory -Force -Path $runtime, $wheels | Out-Null

# 1. Embeddable Python
$zip = Join-Path $build "python-embed.zip"
Invoke-WebRequest "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip" -OutFile $zip
Expand-Archive $zip -DestinationPath $runtime
Remove-Item $zip
# Enable site-packages (the ._pth file disables it by default).
$pth = Get-ChildItem $runtime -Filter "python$minor._pth" | Select-Object -First 1
(Get-Content $pth.FullName) -replace "^#\s*import site", "import site" | Set-Content $pth.FullName
Add-Content $pth.FullName "Lib\site-packages"

# 2. 3dpu + dependencies
& python -m pip wheel --no-deps -w $wheels $repo
if ($LASTEXITCODE) { throw "pip wheel failed" }
$wheel = (Get-ChildItem $wheels -Filter "threedpu-*.whl" | Select-Object -First 1).FullName
& python -m pip install --no-compile --only-binary ":all:" --target (Join-Path $runtime "Lib\site-packages") "$wheel[slicer,manager]"
if ($LASTEXITCODE) { throw "pip install failed" }
# Launcher .exe files pip creates are unsigned and unused; drop them.
Remove-Item -Recurse -Force (Join-Path $runtime "Lib\site-packages\bin") -ErrorAction SilentlyContinue

# Sanity check with the embedded interpreter.
& (Join-Path $runtime "python.exe") -c "import threedpu.manager.app, threedpu.slicer.service, threedpu.slicer.infogui; print('runtime ok')"
if ($LASTEXITCODE) { throw "embedded runtime check failed" }

# 3. Inno Setup
$iscc = Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"
if (-not (Test-Path $iscc)) {
    choco install innosetup -y --no-progress | Out-Null
}
& $iscc "/DAppVersion=$version" "/DPayload=$runtime" (Join-Path $here "3dpu.iss")
if ($LASTEXITCODE) { throw "ISCC failed" }

# Code signing goes here once a certificate is available, e.g. with signtool or Azure Trusted Signing.
Get-ChildItem (Join-Path $here "Output") -Filter "*.exe"
