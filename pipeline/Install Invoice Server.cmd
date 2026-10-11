@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
if errorlevel 1 (
    echo Installation failed. Read the error above.
) else (
    echo Installation complete. Open Launch Invoice Server.cmd.
)
pause
