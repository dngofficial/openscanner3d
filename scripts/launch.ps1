$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path $PSScriptRoot -Parent
$pythonPath = Join-Path $repoRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Missing .venv Python. Follow the setup commands in README.md first.'
}
Push-Location $repoRoot
try {
    & $pythonPath -m backend.app.main
    if ($LASTEXITCODE -ne 0) { throw "Scanner exited with code $LASTEXITCODE" }
} finally {
    Pop-Location
}
