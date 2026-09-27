# Start HouseAgent from source (production mode): serves the built frontend at http://127.0.0.1:8765.
# Native tools write progress to stderr; failures are detected via exit codes (Assert-Ok), not stderr.
$ErrorActionPreference = "Continue"
. "$PSScriptRoot\_env.ps1"
if (-not (Test-Path (Join-Path $Frontend "dist\index.html"))) {
    Use-Node
    Push-Location $Frontend
    try { npm run build; Assert-Ok "frontend build" } finally { Pop-Location }
}
Remove-Item Env:\HOUSEAGENT_ENV -ErrorAction SilentlyContinue
& $Py -m houseagent @args
