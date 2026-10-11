param([switch]$Console)
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
$taskPython = if ($env:INVOICE_PYTHON) { $env:INVOICE_PYTHON } else { Join-Path $PSScriptRoot '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $taskPython)) { throw "Run setup.ps1 first." }
& $taskPython -c "import flask, numpy, cv2, rapidfuzz; from pathlib import Path; import tkinter"
if ($LASTEXITCODE -ne 0) { throw "The Python environment is incomplete. Run setup.ps1 and check its errors." }
if ($Console) {
    & $taskPython -m app.launcher
} else {
    $taskPythonWindow = Join-Path (Split-Path $taskPython) 'pythonw.exe'
    Start-Process -FilePath $taskPythonWindow -ArgumentList '-m', 'app.launcher' -WorkingDirectory $PSScriptRoot -WindowStyle Hidden
}
