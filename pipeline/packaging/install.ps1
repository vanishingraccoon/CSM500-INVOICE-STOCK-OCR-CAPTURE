param(
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'EastgateInvoiceServer'),
    [switch]$PrepareOnly,
    [string]$ShortcutRoot
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$taskInstall = [System.IO.Path]::GetFullPath($InstallRoot)
$taskPayload = Join-Path $PSScriptRoot 'payload.zip'
$taskPayloadHash = (Get-FileHash -LiteralPath $taskPayload -Algorithm SHA256).Hash.ToLowerInvariant()
$taskVersion = Join-Path $taskInstall ('versions\' + $taskPayloadHash.Substring(0, 16))
$taskRuntime = Join-Path $taskInstall 'python'
$taskData = Join-Path $taskInstall 'trial'
$taskLog = Join-Path $taskInstall 'installation.log'
New-Item -ItemType Directory -Path $taskInstall -Force | Out-Null
try {
    $taskInstallLock = [IO.File]::Open((Join-Path $taskInstall '.setup.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
} catch {
    Write-Host 'Another Eastgate setup is running. Wait for it to finish before retrying.'
    exit 1
}
Start-Transcript -Path $taskLog -Append | Out-Null

function Invoke-SetupPython {
    param([string]$Executable, [string[]]$Arguments, [string]$Stage)
    $taskStdout = Join-Path $taskInstall ($Stage + '.stdout.log')
    $taskStderr = Join-Path $taskInstall ($Stage + '.stderr.log')
    New-Item -ItemType Directory -Path $taskInstall -Force | Out-Null
    [IO.File]::WriteAllText($taskStdout, '')
    [IO.File]::WriteAllText($taskStderr, '')
    $taskProcess = Start-Process -FilePath $Executable -ArgumentList $Arguments -WindowStyle Hidden -Wait -PassThru `
        -RedirectStandardOutput $taskStdout -RedirectStandardError $taskStderr
    foreach ($taskOutput in @($taskStdout, $taskStderr)) {
        Get-Content -LiteralPath $taskOutput -Tail 80 | ForEach-Object { Write-Host $_ }
    }
    if ($taskProcess.ExitCode -ne 0) {
        throw "$Stage failed (exit $($taskProcess.ExitCode)). Details: $taskStdout and $taskStderr"
    }
}
function Test-SetupVersion {
    param([string]$Directory)
    try {
        $taskStoredManifest = Get-Content -LiteralPath (Join-Path $Directory 'manifest.json') -Raw | ConvertFrom-Json
        if (($taskStoredManifest | ConvertTo-Json -Compress) -ne ($taskManifest | ConvertTo-Json -Compress)) { return $false }
        foreach ($taskEntry in $taskManifest.PSObject.Properties) {
            if ((Get-FileHash -LiteralPath (Join-Path $Directory $taskEntry.Name) -Algorithm SHA256).Hash -ne $taskEntry.Value) {
                return $false
            }
        }
        $taskAllowed = @($taskManifest.PSObject.Properties.Name) + @('manifest.json', '.payload.sha256', 'python-installer.exe', 'vc_redist.x64.exe')
        foreach ($taskFile in Get-ChildItem -LiteralPath $Directory -File -Recurse -Force) {
            $taskRelative = $taskFile.FullName.Substring($Directory.Length).TrimStart('\').Replace('\', '/')
            $taskCacheSource = $taskRelative -replace '/__pycache__/([^/]+)\.cpython-\d+(?:\.opt-\d+)?\.pyc$', '/$1.py'
            if ($taskRelative -notin $taskAllowed -and $taskCacheSource -notin $taskAllowed) { return $false }
        }
        return $true
    } catch { return $false }
}
function Get-UnusedSetupDirectory {
    param([string]$Parent, [string]$Hash)
    $taskCandidate = Join-Path $Parent $Hash.Substring(0, 16)
    while (Test-Path -LiteralPath $taskCandidate) {
        $taskCandidate = Join-Path $Parent ($Hash.Substring(0, 8) + [Guid]::NewGuid().ToString('N').Substring(0, 8))
    }
    return $taskCandidate
}
function Invoke-SetupRuntimeInstaller {
    param([switch]$Repair)
    $taskDownload = Join-Path $taskVersion 'python-installer.exe'
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -UseBasicParsing -Uri 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe' -OutFile $taskDownload
    $taskSignature = Get-AuthenticodeSignature -LiteralPath $taskDownload
    if ($taskSignature.Status -ne 'Valid' -or $taskSignature.SignerCertificate.Subject -notmatch 'Python Software Foundation') {
        throw 'The downloaded Python installer could not be verified.'
    }
    $taskArguments = @('/quiet', 'InstallAllUsers=0', "TargetDir=`"$taskRuntime`"", 'Include_launcher=0',
        'Include_test=0', 'Include_doc=0', 'Include_pip=1', 'Include_tcltk=1', 'PrependPath=0', 'Shortcuts=0', 'AssociateFiles=0')
    if ($Repair) { $taskArguments = @('/repair') + $taskArguments }
    $taskProcess = Start-Process -FilePath $taskDownload -ArgumentList $taskArguments -WindowStyle Hidden -Wait -PassThru
    if ($taskProcess.ExitCode -eq 3010) {
        Write-Host 'Python runtime installed/repaired successfully. Restart Windows, then rerun setup.'
        exit 3010
    }
    if ($taskProcess.ExitCode -ne 0) { throw "Python runtime installation/repair failed: $($taskProcess.ExitCode)." }
}
try {
    if (-not [Environment]::Is64BitOperatingSystem) { throw 'This installer requires 64-bit Windows.' }
    $taskLock = Join-Path $taskData 'app\.launcher.lock'
    if (Test-Path -LiteralPath $taskLock) {
        try {
            $taskHandle = [System.IO.File]::Open($taskLock, 'Open', 'ReadWrite', 'None')
            $taskHandle.Dispose()
        } catch { throw 'Close the Eastgate server window before installing an update.' }
    }
    Write-Host 'Preparing Eastgate Invoice Server...'
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $taskArchive = [IO.Compression.ZipFile]::OpenRead($taskPayload)
    try {
        $taskReader = New-Object IO.StreamReader($taskArchive.GetEntry('manifest.json').Open())
        try { $taskManifest = $taskReader.ReadToEnd() | ConvertFrom-Json }
        finally { $taskReader.Dispose() }
    } finally { $taskArchive.Dispose() }
    $taskVersions = Join-Path $taskInstall 'versions'
    $taskCandidates = @($taskVersion)
    if (Test-Path -LiteralPath $taskVersions) {
        foreach ($taskFolder in Get-ChildItem -LiteralPath $taskVersions -Directory) {
            $taskMarker = Join-Path $taskFolder.FullName '.payload.sha256'
            if ((Test-Path -LiteralPath $taskMarker) -and ((Get-Content -LiteralPath $taskMarker -Raw).Trim() -eq $taskPayloadHash)) {
                $taskCandidates += $taskFolder.FullName
            }
        }
    }
    $taskPrepared = $false
    foreach ($taskCandidate in $taskCandidates | Select-Object -Unique) {
        if (Test-SetupVersion $taskCandidate) {
            $taskVersion = $taskCandidate
            $taskPrepared = $true
            break
        }
    }
    if ($taskPrepared) { Write-Host 'Reusing the verified application files.' }
    else {
        $taskVersion = Get-UnusedSetupDirectory $taskVersions $taskPayloadHash
        Write-Host 'Preparing a clean application folder; previous attempts are retained separately.'
        Expand-Archive -LiteralPath $taskPayload -DestinationPath $taskVersion
        if (-not (Test-SetupVersion $taskVersion)) { throw 'Package verification failed after extraction.' }
    }
    [IO.File]::WriteAllText((Join-Path $taskVersion '.payload.sha256'), $taskPayloadHash)
    $taskPipeline = Join-Path $taskVersion 'pipeline'
    $taskRequirements = ((Get-FileHash (Join-Path $taskPipeline 'requirements.txt')).Hash +
                         (Get-FileHash (Join-Path $taskPipeline 'requirements-ocr.txt')).Hash)
    $taskDigest = [Security.Cryptography.SHA256]::Create()
    try {
        $taskDependencyHash = [BitConverter]::ToString($taskDigest.ComputeHash([Text.Encoding]::UTF8.GetBytes($taskRequirements))).Replace('-', '').ToLowerInvariant()
    } finally { $taskDigest.Dispose() }
    # Keep dependency paths short and reuse only a checked environment for these requirements.
    $taskEnvironmentName = $taskDependencyHash.Substring(0, 16)
    $taskEnvironment = Join-Path $taskInstall ('e\' + $taskEnvironmentName)
    $taskDependencyStamp = Join-Path $taskEnvironment '.requirements.sha256'
    $taskSeed = Join-Path $taskVersion 'baseline'
    foreach ($taskFile in Get-ChildItem -LiteralPath $taskSeed -File -Recurse) {
        $taskRelative = $taskFile.FullName.Substring($taskSeed.Length).TrimStart('\')
        $taskDestination = Join-Path $taskData $taskRelative
        if (-not (Test-Path -LiteralPath $taskDestination)) {
            New-Item -ItemType Directory -Path (Split-Path $taskDestination) -Force | Out-Null
            Copy-Item -LiteralPath $taskFile.FullName -Destination $taskDestination
        }
    }
    if ($PrepareOnly) {
        Write-Host "Prepared files in $taskVersion; Python and shortcuts were not installed."
        return
    }
    $taskPython = Join-Path $taskRuntime 'python.exe'
    if (-not (Test-Path -LiteralPath $taskPython) -and (Get-Command py.exe -ErrorAction SilentlyContinue)) {
        try {
            $taskExisting = & py.exe -3.12 -c 'import sys, tkinter; print(sys.executable)' 2>$null
            if ($LASTEXITCODE -eq 0 -and (Test-Path -LiteralPath $taskExisting)) { $taskPython = $taskExisting }
        } catch { Write-Host 'A suitable existing Python runtime was not found.' }
    }
    if (-not (Test-Path -LiteralPath $taskPython)) {
        Write-Host 'Installing the private Python runtime...'
        Invoke-SetupRuntimeInstaller
        if (-not (Test-Path -LiteralPath $taskPython)) {
            Write-Host 'Repairing the registered Python runtime after its files were removed...'
            Invoke-SetupRuntimeInstaller -Repair
            if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Python runtime files are unavailable after repair.' }
        }
    }
    $taskRuntimeProbe = @('-c', '"import sys, tkinter, venv, ensurepip; assert sys.version_info[:2] == (3, 12)"')
    try { Invoke-SetupPython $taskPython $taskRuntimeProbe 'runtime-check' }
    catch {
        if ($taskPython -ne (Join-Path $taskRuntime 'python.exe')) { throw }
        Write-Host 'Repairing the incomplete private Python runtime...'
        Invoke-SetupRuntimeInstaller -Repair
        Invoke-SetupPython $taskPython $taskRuntimeProbe 'runtime-check'
    }
    try {
        Invoke-SetupPython $taskPython @('-c', '"import ctypes; ctypes.WinDLL(''vcomp140.dll'')"') 'native-runtime-check'
    } catch {
        Write-Host 'Preparing the Microsoft Visual C++ runtime required by OCR. Approve the Windows permission prompt if shown.'
        $taskRedist = Join-Path $taskVersion 'vc_redist.x64.exe'
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -UseBasicParsing -Uri 'https://aka.ms/vc14/vc_redist.x64.exe' -OutFile $taskRedist
        $taskSignature = Get-AuthenticodeSignature -LiteralPath $taskRedist
        if ($taskSignature.Status -ne 'Valid' -or $taskSignature.SignerCertificate.Subject -notmatch 'Microsoft Corporation') {
            throw 'The downloaded Microsoft runtime installer could not be verified.'
        }
        $taskRedistLog = Join-Path $taskInstall 'visual-c-runtime.log'
        $taskRedistProcess = Start-Process -FilePath $taskRedist -ArgumentList @('/install', '/quiet', '/norestart',
            '/log', ('"' + $taskRedistLog + '"')) -Verb RunAs -WindowStyle Hidden -Wait -PassThru
        if ($taskRedistProcess.ExitCode -eq 3010) {
            Write-Host 'Microsoft runtime installed successfully. Restart Windows, then rerun this setup to finish.'
            exit 3010
        }
        if ($taskRedistProcess.ExitCode -notin @(0, 1638)) {
            throw "Microsoft runtime installation failed (exit $($taskRedistProcess.ExitCode)). See $taskRedistLog"
        }
        Invoke-SetupPython $taskPython @('-c', '"import ctypes; ctypes.WinDLL(''vcomp140.dll'')"') 'native-runtime-check'
    }
    $taskEnvironments = Join-Path $taskInstall 'e'
    $taskCandidates = @($taskEnvironment)
    if (Test-Path -LiteralPath $taskEnvironments) {
        $taskCandidates += @(Get-ChildItem -LiteralPath $taskEnvironments -Directory | ForEach-Object { $_.FullName })
    }
    $taskReuse = $false
    foreach ($taskCandidate in $taskCandidates | Select-Object -Unique) {
        $taskCandidatePython = Join-Path $taskCandidate 'Scripts\python.exe'
        $taskCandidateStamp = Join-Path $taskCandidate '.requirements.sha256'
        if (-not (Test-Path -LiteralPath $taskCandidatePython) -or -not (Test-Path -LiteralPath $taskCandidateStamp)) { continue }
        if ((Get-Content -LiteralPath $taskCandidateStamp -Raw).Trim() -ne $taskDependencyHash) { continue }
        try {
            Invoke-SetupPython $taskCandidatePython @('-m', 'pip', 'check') 'dependency-check'
            $taskEnvironment = $taskCandidate
            $taskReuse = $true
            break
        } catch { Write-Host 'A previous environment failed verification; retaining it separately.' }
    }
    if ($taskReuse) { Write-Host 'Reusing the checked Python environment; dependency installation skipped.' }
    else {
        $taskEnvironment = Get-UnusedSetupDirectory $taskEnvironments $taskDependencyHash
    }
    $taskEnvironmentName = Split-Path $taskEnvironment -Leaf
    $taskAppPython = Join-Path $taskEnvironment 'Scripts\python.exe'
    $taskDependencyStamp = Join-Path $taskEnvironment '.requirements.sha256'
    if (-not $taskReuse) {
        Write-Host 'Installing OCR and application dependencies. This may take several minutes...'
        Invoke-SetupPython $taskPython @('-m', 'venv', ('"' + $taskEnvironment + '"')) 'environment'
        Invoke-SetupPython $taskAppPython @('-m', 'pip', 'install', '--upgrade', 'pip', '--disable-pip-version-check') 'pip-update'
        $taskDependencyLog = Join-Path $taskInstall 'dependencies.log'
        Invoke-SetupPython $taskAppPython @('-m', 'pip', 'install', '--disable-pip-version-check', '--log',
            ('"' + $taskDependencyLog + '"'), '-r', ('"' + (Join-Path $taskPipeline 'requirements-ocr.txt') + '"')) 'dependencies'
        Invoke-SetupPython $taskAppPython @('-m', 'pip', 'check') 'dependency-check'
        [IO.File]::WriteAllText($taskDependencyStamp, $taskDependencyHash)
    }
    $env:INVOICE_DATA = Join-Path $taskData 'data'
    $env:INVOICE_APP_DATA = Join-Path $taskData 'app'
    $env:INVOICE_SUPPLIERS_FILE = Join-Path $taskData 'data\pilot\additional-suppliers.json'
    $env:PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK = 'True'
    Push-Location -LiteralPath $taskPipeline
    try {
        Write-Host 'Downloading and checking OCR models...'
        Invoke-SetupPython $taskAppPython @('-c', '"from ocr.paddle import PaddleEngine; PaddleEngine()._ocr; import tkinter; from app.server import app"') 'ocr-startup'
    } finally { Pop-Location }
    $taskLaunch = Join-Path $taskInstall 'Start-Eastgate.ps1'
    $taskLaunchText = @'
$ErrorActionPreference = 'Stop'
$taskRoot = $PSScriptRoot
$taskCurrent = Get-Content -LiteralPath (Join-Path $taskRoot 'current-install.json') -Raw | ConvertFrom-Json
$taskPipeline = Join-Path $taskRoot ('versions\' + $taskCurrent.version + '\pipeline')
$env:INVOICE_PYTHON = Join-Path $taskRoot ('e\' + $taskCurrent.environment + '\Scripts\python.exe')
$env:INVOICE_DATA = Join-Path $taskRoot 'trial\data'
$env:INVOICE_APP_DATA = Join-Path $taskRoot 'trial\app'
$env:INVOICE_SUPPLIERS_FILE = Join-Path $taskRoot 'trial\data\pilot\additional-suppliers.json'
& (Join-Path $taskPipeline 'launch.ps1')
'@
    [IO.File]::WriteAllText($taskLaunch, $taskLaunchText.Replace("`r`n", "`n").Replace("`n", "`r`n"))
    $taskShell = New-Object -ComObject WScript.Shell
    $taskDesktop = if ($ShortcutRoot) { Join-Path $ShortcutRoot 'Desktop' } else { [Environment]::GetFolderPath('Desktop') }
    $taskPrograms = if ($ShortcutRoot) { Join-Path $ShortcutRoot 'Programs' } else { [Environment]::GetFolderPath('Programs') }
    $taskMenu = Join-Path $taskPrograms 'Eastgate Invoice Server'
    New-Item -ItemType Directory -Path $taskDesktop -Force | Out-Null
    New-Item -ItemType Directory -Path $taskMenu -Force | Out-Null
    foreach ($taskShortcutPath in @((Join-Path $taskDesktop 'Eastgate Invoice Server.lnk'),
                                    (Join-Path $taskMenu 'Eastgate Invoice Server.lnk'))) {
        $taskShortcut = $taskShell.CreateShortcut($taskShortcutPath)
        $taskShortcut.TargetPath = Join-Path $PSHOME 'powershell.exe'
        $taskShortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$taskLaunch`""
        $taskShortcut.WorkingDirectory = $taskInstall
        $taskShortcut.Description = 'Start the Eastgate invoice portal and OCR readers'
        $taskShortcut.Save()
    }
    $taskCurrentJson = @{ version = (Split-Path $taskVersion -Leaf); environment = $taskEnvironmentName } | ConvertTo-Json
    $taskCurrentTemp = Join-Path $taskInstall ('current-install-' + $taskEnvironmentName + '.tmp')
    [IO.File]::WriteAllText($taskCurrentTemp, $taskCurrentJson)
    Move-Item -LiteralPath $taskCurrentTemp -Destination (Join-Path $taskInstall 'current-install.json') -Force
    [IO.File]::WriteAllText((Join-Path $taskInstall 'current-version.txt'), (Split-Path $taskVersion -Leaf))
    Copy-Item -LiteralPath (Join-Path $taskVersion 'phone\invoice-app-eastgate-debug.apk') -Destination (Join-Path $taskInstall 'Samsung-App.apk')
    Write-Host 'Installed. Open the Eastgate Invoice Server desktop shortcut.'
    Write-Host "Samsung APK: $(Join-Path $taskInstall 'Samsung-App.apk')"
} catch {
    Write-Host "Installation failed: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Log: $taskLog"
    exit 1
} finally {
    Stop-Transcript | Out-Null
    $taskInstallLock.Dispose()
}
