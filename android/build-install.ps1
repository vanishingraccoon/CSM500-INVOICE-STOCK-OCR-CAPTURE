<#
Builds the debug APK and installs it on whatever device/emulator adb currently sees.
Run from inside this folder, with the emulator running or a phone plugged in.

    .\build-install.ps1
#>
$ErrorActionPreference = "Stop"

$env:JAVA_HOME = 'C:\Program Files\Zulu\zulu-21'
Write-Host "Building debug APK..."
.\gradlew.bat assembleDebug

$adb = "$env:LOCALAPPDATA\Android\Sdk\platform-tools\adb.exe"
Write-Host "Connected devices:"
& $adb devices

Write-Host "Installing..."
& $adb install -r app\build\outputs\apk\debug\app-debug.apk
Write-Host "Installed. Open the app and pair it from http://localhost:5000/pair on the laptop."
