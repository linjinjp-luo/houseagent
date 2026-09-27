# Development mode: backend on 127.0.0.1:8765 (API docs at /api/docs) + Vite dev server on 127.0.0.1:5173.
# The Vite proxy reads the session token from %LOCALAPPDATA%\HouseAgent\config\runtime.json.
param([switch]$HttpEngine)
# Native tools write progress to stderr; failures are detected via exit codes (Assert-Ok), not stderr.
$ErrorActionPreference = "Continue"
. "$PSScriptRoot\_env.ps1"
Use-Node

$env:HOUSEAGENT_ENV = "development"
if ($HttpEngine) { $env:HOUSEAGENT_BROWSER_ENGINE = "http" }
$backend = Start-Process -FilePath $Py -ArgumentList "-m", "houseagent", "--dev", "--no-browser", "--no-tray" `
    -WorkingDirectory $Backend -PassThru -NoNewWindow
try {
    Push-Location $Frontend
    npm run dev
} finally {
    Pop-Location
    if (-not $backend.HasExited) { Stop-Process -Id $backend.Id }
}
