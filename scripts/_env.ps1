# Shared helpers: locate Python / Node. Uses PATH first, then the portable toolchain in ..\..\tools.
$Root = Split-Path -Parent $PSScriptRoot
$Backend = Join-Path $Root "backend"
$Frontend = Join-Path $Root "frontend"
$Tools = if ($env:HOUSEAGENT_TOOLS) { $env:HOUSEAGENT_TOOLS } else { Join-Path (Split-Path -Parent $Root) "tools" }
$Venv = Join-Path $Backend ".venv"
$Py = Join-Path $Venv "Scripts\python.exe"

function Find-BasePython {
    $portable = Join-Path $Tools "python\python.exe"
    if (Test-Path $portable) { return $portable }
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($null -ne $cmd) { return $cmd.Source }
    throw "Python 3.11+ not found. Install it or set HOUSEAGENT_TOOLS to a folder containing python\python.exe."
}

function Use-Node {
    $portable = Join-Path $Tools "node"
    if (Test-Path (Join-Path $portable "node.exe")) { $env:PATH = "$portable;$env:PATH" }
    if ($null -eq (Get-Command npm -ErrorAction SilentlyContinue)) {
        throw "Node.js 20+ not found. Install it or set HOUSEAGENT_TOOLS to a folder containing node\node.exe."
    }
}

function Assert-Ok($what) {
    # Throwing here stops the script regardless of $ErrorActionPreference.
    if ($LASTEXITCODE -ne 0) { throw "$what failed (exit code $LASTEXITCODE)" }
}
