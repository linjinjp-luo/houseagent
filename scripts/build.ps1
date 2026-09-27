# Build the Windows distribution: frontend bundle + PyInstaller one-folder app in dist\HouseAgent.
# Native tools write progress to stderr; failures are detected via exit codes (Assert-Ok), not stderr.
$ErrorActionPreference = "Continue"
. "$PSScriptRoot\_env.ps1"
Use-Node
Push-Location $Frontend
try { npm run build; Assert-Ok "frontend build" } finally { Pop-Location }

$dist = Join-Path $Root "dist"
& $Py -m PyInstaller --noconfirm --clean --distpath $dist --workpath (Join-Path $Root "build\pyinstaller") `
    (Join-Path $PSScriptRoot "houseagent.spec"); Assert-Ok "PyInstaller"
Copy-Item (Join-Path $Root "installer\uninstall.ps1") (Join-Path $dist "HouseAgent\uninstall.ps1") -Force
Write-Host "Built: $dist\HouseAgent\HouseAgent.exe"
