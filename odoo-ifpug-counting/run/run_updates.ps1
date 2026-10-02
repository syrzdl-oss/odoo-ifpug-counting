# run_updates.ps1 - materialise the IFPUG functions in a GraphDB repository.
# Sends sparql/update/*.ru in name order to the repository's statements endpoint.
# Usage (from the repository root):
#   powershell -ExecutionPolicy Bypass -File .\run\run_updates.ps1
#   powershell -ExecutionPolicy Bypass -File .\run\run_updates.ps1 -Endpoint http://localhost:7200/repositories/odoo_fsm/statements
param(
    [string]$Endpoint = "http://localhost:7200/repositories/odoo_fsm/statements",
    [int]$TimeoutSec = 3600
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$files = Get-ChildItem -Path (Join-Path $root "sparql\update") -Filter "*.ru" | Sort-Object Name
foreach ($f in $files) {
    $body = [System.IO.File]::ReadAllText($f.FullName, [System.Text.Encoding]::UTF8)
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    try {
        Invoke-RestMethod -Uri $Endpoint -Method Post -ContentType "application/sparql-update; charset=utf-8" `
            -Body ([System.Text.Encoding]::UTF8.GetBytes($body)) -TimeoutSec $TimeoutSec | Out-Null
        Write-Host ("OK    {0,-28} {1,6:N1} s" -f $f.Name, $sw.Elapsed.TotalSeconds)
    } catch {
        Write-Host ("FAIL  {0}" -f $f.Name) -ForegroundColor Red
        if ($_.ErrorDetails) { Write-Host $_.ErrorDetails.Message -ForegroundColor Red } else { Write-Host $_ -ForegroundColor Red }
        exit 1
    }
}
Write-Host "All updates done. Run sparql/query/S1.rq ... S10.rq to check the results."
