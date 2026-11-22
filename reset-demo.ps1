# Delete the demo database and uploaded file cache, then reseed on next start.
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$data = Join-Path $root "data"
if (Test-Path $data) {
  $resolved = (Resolve-Path $data).Path
  if ($resolved -ne (Join-Path $root "data")) {
    throw "Refusing to delete unexpected path: $resolved"
  }
  Remove-Item -LiteralPath $resolved -Recurse -Force
  Write-Host "Demo data removed. The next start seeds a clean workspace." -ForegroundColor Green
} else {
  Write-Host "No data directory found; nothing to reset." -ForegroundColor DarkGray
}
