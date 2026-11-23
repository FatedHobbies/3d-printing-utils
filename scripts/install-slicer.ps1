<#
.SYNOPSIS
  Set up the 3dpu Slicer service on this PC (run once, from an elevated PowerShell).

.DESCRIPTION
  - Creates a Python virtual environment in C:\ProgramData\3dpu\venv and installs the service.
  - Creates the config file (with an access token) and checks that Bambu Studio can be found.
  - Opens the service port in Windows Firewall for Private networks only.
  - Registers a scheduled task that starts the service hidden when you log on.

  The service runs at logon (not at boot) because Bambu Studio expects a normal
  user session and profile folder.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\install-slicer.ps1
  powershell -ExecutionPolicy Bypass -File scripts\install-slicer.ps1 -BambuStudioPath "D:\Apps\Bambu Studio"
#>
param(
    [int]$Port = 8765,
    [string]$BambuStudioPath = "",
    [string]$Python = "py"
)
$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent $PSScriptRoot
$root = Join-Path $env:ProgramData "3dpu"
$venv = Join-Path $root "venv"
$exe = Join-Path $venv "Scripts\3dpu-slicer.exe"
$log = Join-Path $root "slicer.log"
$configPath = Join-Path $root "slicer.json"

New-Item -ItemType Directory -Force -Path $root | Out-Null

if (-not (Test-Path (Join-Path $venv "Scripts\python.exe"))) {
    Write-Host "Creating virtual environment in $venv"
    & $Python -3 -m venv $venv
}
& (Join-Path $venv "Scripts\python.exe") -m pip install --upgrade pip | Out-Null
& (Join-Path $venv "Scripts\python.exe") -m pip install "$repo[slicer]"

# Create config + token on first run.
& $exe config | Out-Null
if ($BambuStudioPath -or $Port -ne 8765) {
    $cfg = Get-Content $configPath -Raw | ConvertFrom-Json
    if ($BambuStudioPath) { $cfg.bambu_studio_path = $BambuStudioPath }
    $cfg.port = $Port
    $cfg | ConvertTo-Json -Depth 5 | Set-Content -Encoding UTF8 $configPath
}

Write-Host ""
& $exe find-bambu
if ($LASTEXITCODE -ne 0) {
    Write-Warning "Bambu Studio was not found. Re-run with -BambuStudioPath pointing at the folder that contains bambu-studio.exe and BambuStudio.dll."
}

$ruleName = "3dpu Slicer ($Port)"
if (-not (Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Protocol TCP -LocalPort $Port `
        -Action Allow -Profile Private | Out-Null
    Write-Host "Firewall: allowed TCP $Port on Private networks."
}

$taskName = "3dpu Slicer"
$cmd = "& '$exe' serve *>> '$log'"
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -Command `"$cmd`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -AllowStartIfOnBatteries
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings `
    -Description "Slices Bambu Studio projects sent from the 3dpu Manager app." -Force | Out-Null
Start-ScheduledTask -TaskName $taskName

Write-Host ""
Write-Host "Done. The Slicer service is running and will start whenever you log on."
Write-Host "Log file: $log"
Write-Host ""
Write-Host "In the Manager app's Settings, enter:"
Write-Host "  Slicer address: http://$($env:COMPUTERNAME):$Port"
Write-Host "  Access token:   $(& $exe show-token)"
