<#
Sets up the Python environment for invoice-pipeline. Run once, from inside this folder:

    .\setup.ps1               # everything needed to run the pipeline and the web portal
    .\setup.ps1 -WithHwr       # also set up .venv-hwr for the --hwr handwriting trial
#>
param(
    [switch]$WithHwr
)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

if (-not (Test-Path ".venv")) {
    Write-Host "Creating .venv..."
    py -3.12 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "Python 3.12 is required; install it with the Python launcher and rerun setup.ps1." }
}
& .venv\Scripts\python.exe -c "import sys; assert sys.version_info[:2] == (3, 12)"
if ($LASTEXITCODE -ne 0) {
    throw "The .venv Python is missing or incompatible. Install Python 3.12, then run py -3.12 -m venv .venv and rerun setup.ps1."
}
Write-Host "Installing application and OCR requirements..."
& .venv\Scripts\python.exe -m pip install -r requirements-ocr.txt
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed. Check the output above." }

Write-Host "If paddleocr/paddlepaddle failed to install or crashes on first use, see the oneDNN note in README.md."

if ($WithHwr) {
    if (-not (Test-Path ".venv-hwr")) {
        Write-Host "Creating .venv-hwr..."
        py -3.12 -m venv .venv-hwr
        if ($LASTEXITCODE -ne 0) { throw "Could not create the handwriting environment." }
    }
    Write-Host "Installing handwriting-trial requirements (torch, transformers)..."
    & .venv-hwr\Scripts\python.exe -m pip install -r requirements.txt torch transformers
    if ($LASTEXITCODE -ne 0) { throw "Handwriting dependencies failed to install." }
}

Write-Host "Done. Try '.\launch.ps1' to start the server, or '.venv\Scripts\python pipeline.py' to run the pipeline."
