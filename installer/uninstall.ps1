# HouseAgent uninstall (spec 10.3 / 15.10).
# Default: removes only the program folder. User data (database, browser profiles, backups, logs, config)
# under %LOCALAPPDATA%\HouseAgent is kept, so reinstalling or upgrading keeps everything.
# -DeleteUserData additionally removes all user data after a typed confirmation.
param(
    [string]$ProgramDir = $PSScriptRoot,
    [switch]$DeleteUserData
)
$ErrorActionPreference = "Stop"
$dataDir = if ($env:HOUSEAGENT_DATA_DIR) { $env:HOUSEAGENT_DATA_DIR } else { Join-Path $env:LOCALAPPDATA "HouseAgent" }

$runtime = Join-Path $dataDir "config\runtime.json"
if (Test-Path $runtime) {
    Write-Host "HouseAgent appears to be running. Quit it from the tray icon (or scripts\stop.ps1) first."
    exit 1
}

if ($DeleteUserData) {
    Write-Host "This will permanently delete ALL HouseAgent user data in:" -ForegroundColor Yellow
    Write-Host "  $dataDir"
    Write-Host "(database, browser sign-in profiles, backups, logs and settings)"
    $answer = Read-Host "Type DELETE to confirm"
    if ($answer -ne "DELETE") { Write-Host "Cancelled. Nothing was removed."; exit 1 }
    Remove-Item -Recurse -Force $dataDir -ErrorAction SilentlyContinue
    Write-Host "User data removed."
}

$exe = Join-Path $ProgramDir "HouseAgent.exe"
if (Test-Path $exe) {
    Write-Host "Removing program files in $ProgramDir"
    Get-ChildItem $ProgramDir | Where-Object { $_.Name -ne "uninstall.ps1" } | Remove-Item -Recurse -Force
    Write-Host "Program removed. You can delete this folder."
} else {
    Write-Host "No HouseAgent.exe in $ProgramDir; program files not touched."
}
if (-not $DeleteUserData) { Write-Host "User data kept in $dataDir" }
