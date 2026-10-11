param([string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'EastgateInvoiceServer'))
$ErrorActionPreference = 'Stop'
$taskRoot = [IO.Path]::GetFullPath($InstallRoot).TrimEnd('\')
$taskSetupLock = $null
$taskMoved = $false
$taskArchiveApp = $null

function Test-CleanPath {
    param([string]$Path)
    $taskFull = [IO.Path]::GetFullPath($Path)
    if (-not $taskFull.StartsWith($taskRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'The clean-run path is outside the Eastgate installation.'
    }
    $taskCheck = $taskFull
    while ($taskCheck -and $taskCheck.Length -ge $taskRoot.Length) {
        if (Test-Path -LiteralPath $taskCheck) {
            if ((Get-Item -LiteralPath $taskCheck -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw 'Clean Run cannot use redirected folders or linked files.'
            }
        }
        $taskCheck = Split-Path -Parent $taskCheck
    }
    return $taskFull
}

function Test-CleanServerClosed {
    param([string]$Directory)
    foreach ($taskName in @('.launcher.lock', 'invoices.db', 'invoices.db-wal', 'invoices.db-shm')) {
        $taskFile = Test-CleanPath (Join-Path $Directory $taskName)
        if (Test-Path -LiteralPath $taskFile) {
            try {
                $taskHandle = [IO.File]::Open($taskFile, 'Open', 'ReadWrite', 'None')
                $taskHandle.Dispose()
            } catch { throw 'Close the Eastgate Invoice Server window before running Clean Run.' }
        }
    }
}

try {
    $taskCurrentFile = Test-CleanPath (Join-Path $taskRoot 'current-install.json')
    if (-not (Test-Path -LiteralPath $taskCurrentFile)) {
        throw 'Eastgate Invoice Server is not installed for this Windows user. Install it first.'
    }
    $taskCurrent = Get-Content -LiteralPath $taskCurrentFile -Raw | ConvertFrom-Json
    if ($taskCurrent.version -notmatch '^[a-zA-Z0-9_-]+$' -or
        $taskCurrent.environment -notmatch '^[a-zA-Z0-9_-]+$') {
        throw 'The installation record is invalid. Run the Eastgate installer again.'
    }
    $taskSetupLockPath = Test-CleanPath (Join-Path $taskRoot '.setup.lock')
    try { $taskSetupLock = [IO.File]::Open($taskSetupLockPath, 'OpenOrCreate', 'ReadWrite', 'None') }
    catch { throw 'Another installation or Clean Run is running. Wait for it to finish.' }

    $taskApp = Test-CleanPath (Join-Path $taskRoot 'trial\app')
    Test-CleanServerClosed $taskApp
    $taskId = (Get-Date -Format 'yyyy-MM-dd_HH-mm-ss') + '-' + [Guid]::NewGuid().ToString('N').Substring(0, 8)
    $taskArchive = Test-CleanPath (Join-Path $taskRoot ('clean-run-backups\' + $taskId))
    $taskArchiveApp = Test-CleanPath (Join-Path $taskArchive 'app')
    $taskStage = Test-CleanPath (Join-Path $taskRoot ('clean-run-stage-' + $taskId))
    New-Item -ItemType Directory -Path $taskStage -ErrorAction Stop | Out-Null
    $taskShop = Test-CleanPath (Join-Path $taskApp 'shops.json')
    if (-not (Test-Path -LiteralPath $taskShop)) {
        $taskShop = Test-CleanPath (Join-Path $taskRoot ('versions\' + $taskCurrent.version + '\baseline\app\shops.json'))
    }
    if (-not (Test-Path -LiteralPath $taskShop)) { throw 'The Eastgate shop configuration is missing. Reinstall first.' }
    Get-Content -LiteralPath $taskShop -Raw | ConvertFrom-Json | Out-Null
    Copy-Item -LiteralPath $taskShop -Destination (Join-Path $taskStage 'shops.json')

    New-Item -ItemType Directory -Path $taskArchive -ErrorAction Stop | Out-Null
    $taskManifest = @{ run_id = $taskId; version = $taskCurrent.version;
        environment = $taskCurrent.environment; previous_app_present = (Test-Path -LiteralPath $taskApp);
        preserved = 'Installed application, Python, OCR models and trial/data/Pilot exports';
        reset = 'Batches, photos, reports, OCR cache, learned aliases and device pairings' } | ConvertTo-Json
    [IO.File]::WriteAllText((Join-Path $taskArchive 'clean-run.json'), $taskManifest)

    if (Test-Path -LiteralPath $taskApp) {
        # Both absolute endpoints were checked before moving the original trial intact.
        [IO.Directory]::Move($taskApp, $taskArchiveApp)
        $taskMoved = $true
        Test-CleanServerClosed $taskArchiveApp
    }
    [IO.Directory]::Move($taskStage, $taskApp)
    Write-Output 'Clean run ready. Your previous trial was archived, not deleted.'
    Write-Output "Backup: $taskArchive"
    Write-Output 'The installed application and Eastgate baseline key are unchanged.'
    Write-Output 'Open Eastgate Invoice Server, pair the phone again and start a new batch.'
    try {
        $taskLog = Test-CleanPath (Join-Path $taskRoot 'clean-run.log')
        [IO.File]::AppendAllText($taskLog, $taskId + ' ready; backup=' + $taskArchive + [Environment]::NewLine)
    } catch { Write-Output 'The clean run is ready, but its summary log could not be written.' }
} catch {
    $taskFailure = $_.Exception.Message
    if ($taskMoved -and -not (Test-Path -LiteralPath $taskApp)) {
        try {
            [IO.Directory]::Move((Test-CleanPath $taskArchiveApp), (Test-CleanPath $taskApp))
            Write-Output 'The original trial was restored.'
        } catch { Write-Output "The previous trial is retained at $taskArchiveApp. Do not start a new trial until it is restored." }
    }
    Write-Output "Clean Run did not complete: $taskFailure"
    exit 1
} finally {
    if ($taskSetupLock) { $taskSetupLock.Dispose() }
}
