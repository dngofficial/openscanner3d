param([string]$Python = 'py')
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path $PSScriptRoot -Parent
Push-Location $repoRoot
try {
    $versionArgs = @()
    if ($Python -eq 'py') { $versionArgs = @('-3.11') }
    & $Python @versionArgs -c 'import sys; assert sys.version_info[:2] == (3, 11), "Python 3.11 is required"'
    if ($LASTEXITCODE -ne 0) { throw 'Install Python 3.11 or pass -Python with its executable path.' }
    $venvPython = Join-Path $repoRoot '.venv/Scripts/python.exe'
    if (-not (Test-Path -LiteralPath $venvPython)) {
        & $Python @versionArgs -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw 'Creating the virtual environment failed.' }
    }
    & $venvPython -c 'import sys; assert sys.version_info[:2] == (3, 11), "Existing .venv must use Python 3.11"'
    if ($LASTEXITCODE -ne 0) { throw 'Use a Python 3.11 virtual environment.' }
    & $venvPython -c 'import importlib.util, sys; sys.exit(0 if importlib.util.find_spec("pip") else 1)'
    if ($LASTEXITCODE -ne 0) {
        & $venvPython -m ensurepip
        if ($LASTEXITCODE -ne 0) { throw 'Bootstrapping pip failed.' }
    }
    & $venvPython -m pip install -r backend/requirements-dev.txt
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
    if (-not (Test-Path -LiteralPath config.local.toml)) {
        Copy-Item -LiteralPath config.example.toml -Destination config.local.toml
    }
    & $venvPython -m tests.check_capture
    if ($LASTEXITCODE -ne 0) { throw 'Capture smoke check failed.' }
    & $venvPython -m unittest discover -s tests -p 'test_*.py'
    if ($LASTEXITCODE -ne 0) { throw 'Backend tests failed.' }
    & $venvPython -m scripts.doctor --output data/doctor.json
    if ($LASTEXITCODE -ne 0) { throw 'Environment check failed; inspect data/doctor.json.' }
    Write-Host 'Capture setup verified. GPU reconstruction still requires the partner-machine smoke test.'
} finally {
    Pop-Location
}
