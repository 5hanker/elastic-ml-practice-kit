# Wrapper for `python setup.py`: finds Python >= 3.9 and passes all arguments through.
# Usage: .\setup.ps1 [command] [options]
$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

function Test-Python([string]$exe, [string[]]$pre) {
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { return $false }
    & $exe @pre -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" 2>$null
    return ($LASTEXITCODE -eq 0)
}

$candidates = @(
    @{ exe = 'py';      pre = @('-3') },
    @{ exe = 'python';  pre = @() },
    @{ exe = 'python3'; pre = @() }
)
foreach ($c in $candidates) {
    if (Test-Python $c.exe $c.pre) {
        & $c.exe @($c.pre) setup.py @args
        exit $LASTEXITCODE
    }
}

Write-Host "Python 3.9 or newer was not found on this machine." -ForegroundColor Yellow
Write-Host "Options:"
Write-Host "  - Install Python from https://www.python.org/downloads/ and re-run."
Write-Host "  - Or skip local setup: use the GitHub Actions workflow or a GitHub Codespace (see the README)."
exit 2
