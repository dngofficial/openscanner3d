param([string]$Python = 'py')
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path $PSScriptRoot -Parent
Push-Location $repoRoot
try {
    $versionArgs = @()
    if ($Python -eq 'py') { $versionArgs = @('-3.11') }
    # No quotes inside the Python code: Windows PowerShell 5.1 strips them from native-command arguments.
    & $Python @versionArgs -c 'import sys; sys.exit(0 if sys.version_info[:2] == (3, 11) else 1)'
    if ($LASTEXITCODE -ne 0) { throw 'Install Python 3.11 or pass -Python with its executable path.' }
    $venvPython = Join-Path $repoRoot '.venv/Scripts/python.exe'
    if (-not (Test-Path -LiteralPath $venvPython)) {
        & $Python @versionArgs -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw 'Creating the virtual environment failed.' }
    }
    & $venvPython -c 'import sys; sys.exit(0 if sys.version_info[:2] == (3, 11) else 1)'
    if ($LASTEXITCODE -ne 0) { throw 'Use a Python 3.11 virtual environment.' }
    & $venvPython -m pip --version *> $null
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
