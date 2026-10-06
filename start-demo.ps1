$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$Python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    & py -3.12 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "Python 3.12 environment creation failed." }
}
& $Python -m pip install -e ".[dev]"
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }
& $Python -m localmodel doctor
if ($LASTEXITCODE -ne 0) { throw "FFmpeg check failed." }
& $Python -m localmodel demo
if ($LASTEXITCODE -ne 0) { throw "Demo rendering failed." }
Invoke-Item (Join-Path $PSScriptRoot "workspace\demo\final.mp4")
