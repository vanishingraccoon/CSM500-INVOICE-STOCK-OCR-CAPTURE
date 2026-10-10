<#
Sets up the Python environment for invoice-pipeline. Run once, from inside this folder:

    .\setup.ps1               # everything needed to run the pipeline and the web portal
    .\setup.ps1 -WithHwr       # also set up .venv-hwr for the --hwr handwriting trial
#>
param(
    [switch]$WithHwr
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path ".venv")) {
    Write-Host "Creating .venv..."
    py -3.12 -m venv .venv
}
Write-Host "Installing requirements..."
.venv\Scripts\pip install -r requirements.txt

Write-Host "Installing numpy, opencv-python, paddlepaddle, paddleocr (needed by both pipeline.py and the web portal -- see README.md's Environments table)..."
.venv\Scripts\pip install numpy opencv-python paddlepaddle paddleocr
Write-Host "If paddleocr/paddlepaddle failed to install or crashes on first use, see the oneDNN note in README.md."

if ($WithHwr) {
    if (-not (Test-Path ".venv-hwr")) {
        Write-Host "Creating .venv-hwr..."
        py -3.12 -m venv .venv-hwr
    }
    Write-Host "Installing handwriting-trial requirements (torch, transformers)..."
    .venv-hwr\Scripts\pip install -r requirements.txt torch transformers
}

Write-Host "Done. Try '.\serve.ps1' to start the server, or '.venv\Scripts\python pipeline.py' to run the pipeline."
