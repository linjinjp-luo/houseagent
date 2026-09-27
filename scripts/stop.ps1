# Stop a running HouseAgent gracefully: new runs are refused, running searches stop at a safe point,
# then the server exits. Falls back to ending the process only if the service does not answer.
$ErrorActionPreference = "Stop"
$dataDir = if ($env:HOUSEAGENT_DATA_DIR) { $env:HOUSEAGENT_DATA_DIR } else { Join-Path $env:LOCALAPPDATA "HouseAgent" }
$runtime = Join-Path $dataDir "config\runtime.json"
if (-not (Test-Path $runtime)) { Write-Host "HouseAgent is not running."; exit 0 }
$info = Get-Content $runtime -Raw | ConvertFrom-Json
try {
    Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$($info.port)/api/v1/system/shutdown" `
        -Headers @{ "X-HouseAgent-Token" = $info.token } -ContentType "application/json" -Body "{}" | Out-Null
    Write-Host "Shutdown requested; HouseAgent will exit after running searches stop safely."
} catch {
    Write-Host "Service did not answer; ending process $($info.pid)."
    Stop-Process -Id $info.pid -ErrorAction SilentlyContinue
}
