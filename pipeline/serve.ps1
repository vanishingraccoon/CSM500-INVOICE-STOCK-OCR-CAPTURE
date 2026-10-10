<#
Starts the laptop web portal. Run from inside this folder.

    .\serve.ps1                # for a real phone on the office Wi-Fi (portal + phone API + readers)
    .\serve.ps1 -Emulator      # for the Android emulator (sets INVOICE_PUBLIC_URL to 10.0.2.2)
    .\serve.ps1 -Workers 1     # one reader instead of two
    .\serve.ps1 -Port 5050     # use a different port
    .\serve.ps1 -PagesOnly     # just the web pages, no readers, no phone uploads processed
#>
param(
    [switch]$Emulator,
    [int]$Workers,
    [int]$Port,
    [switch]$PagesOnly
)

$env:INVOICE_HOST = "0.0.0.0"
if ($Emulator) { $env:INVOICE_PUBLIC_URL = "http://10.0.2.2:5000" }
if ($Workers)  { $env:INVOICE_WORKERS = $Workers }
if ($Port)     { $env:INVOICE_PORT = $Port }

if ($PagesOnly) {
    Write-Host "Starting web pages only (no readers) -- photos will queue unread until you run this without -PagesOnly."
    .venv\Scripts\python -m app.server
} else {
    .venv\Scripts\python -m app.serve
}
