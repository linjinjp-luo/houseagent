# Development setup: backend virtualenv + pinned dependencies, frontend packages.
# Native tools write progress to stderr; failures are detected via exit codes (Assert-Ok), not stderr.
$ErrorActionPreference = "Continue"
. "$PSScriptRoot\_env.ps1"

if (-not (Test-Path $Py)) {
    $base = Find-BasePython
    Write-Host "Creating virtualenv with $base"
    & $base -m venv $Venv; Assert-Ok "venv"
}
& $Py -m pip install --upgrade pip; Assert-Ok "pip upgrade"
& $Py -m pip install -r (Join-Path $Backend "requirements-dev.txt"); Assert-Ok "pip install"
& $Py -m pip install -e $Backend --no-deps; Assert-Ok "pip install -e"

Use-Node
Push-Location $Frontend
try { npm ci; Assert-Ok "npm ci" } finally { Pop-Location }
Write-Host "Setup complete. Run scripts\dev.ps1 to start in development mode."
