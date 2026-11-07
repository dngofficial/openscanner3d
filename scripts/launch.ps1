param([ValidateSet('capture', 'reconstruct')][string]$Mode)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path $PSScriptRoot -Parent
$pythonPath = Join-Path $repoRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Missing .venv Python. Follow the setup commands in README.md first.'
}
Push-Location $repoRoot
$previousMode = $env:SCANNER_MODE
try {
    if ($Mode) { $env:SCANNER_MODE = $Mode }
    & $pythonPath -m scripts.doctor
    if ($LASTEXITCODE -ne 0) { throw 'Environment check failed. Run scripts/setup.ps1.' }
    & $pythonPath -m backend.app.main
    if ($LASTEXITCODE -ne 0) { throw "Scanner exited with code $LASTEXITCODE" }
} finally {
    $env:SCANNER_MODE = $previousMode
    Pop-Location
}
