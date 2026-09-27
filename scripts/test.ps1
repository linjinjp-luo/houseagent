# Pre-commit checks: format + lint, type checks, unit/integration tests, migration tests, frontend tests.
# Native tools write progress to stderr; failures are detected via exit codes (Assert-Ok), not stderr.
$ErrorActionPreference = "Continue"
. "$PSScriptRoot\_env.ps1"
$ruff = Join-Path $Venv "Scripts\ruff.exe"
$mypy = Join-Path $Venv "Scripts\mypy.exe"

Push-Location $Backend
try {
    & $ruff format --check houseagent tests; Assert-Ok "ruff format"
    & $ruff check houseagent tests; Assert-Ok "ruff check"
    & $mypy; Assert-Ok "mypy"
    & $Py -m pytest -q -p no:logging; Assert-Ok "pytest (incl. migrations)"
} finally { Pop-Location }

Use-Node
Push-Location $Frontend
try {
    npx tsc -b --noEmit; Assert-Ok "tsc"
    npx vitest run; Assert-Ok "vitest"
} finally { Pop-Location }
Write-Host "All checks passed."
