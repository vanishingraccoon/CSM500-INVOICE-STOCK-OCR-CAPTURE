"""Replay installer stage outcomes without downloads, elevation or system changes."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import zipfile

import pytest


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows installer replay")


@pytest.fixture
def installer(tmp_path):
    source = Path(__file__).resolve().parents[1] / "packaging" / "install.ps1"
    (tmp_path / "install.ps1").write_bytes(source.read_bytes())
    files = {
        "pipeline/requirements.txt": b"flask==3.1.3\n",
        "pipeline/requirements-ocr.txt": b"-r requirements.txt\npaddleocr==3.7.0\n",
        "baseline/app/shops.json": b"[]\n",
        "phone/invoice-app-eastgate-debug.apk": b"synthetic apk fixture",
    }
    with zipfile.ZipFile(tmp_path / "payload.zip", "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
        archive.writestr("manifest.json", json.dumps({name: hashlib.sha256(content).hexdigest()
                                                     for name, content in files.items()}))
    script = r'''param([string]$Mode)
$ErrorActionPreference = 'Stop'
Import-Module Microsoft.PowerShell.Archive
$taskRoot = Join-Path $PSScriptRoot 'installation'
if ($Mode -notlike 'fresh*') {
    New-Item -ItemType Directory -Path (Join-Path $taskRoot 'python') -Force | Out-Null
    [IO.File]::WriteAllText((Join-Path $taskRoot 'python/python.exe'), 'controlled stand-in')
}
function Get-Command { param($Name, $ErrorAction)
    if ($Name -ne 'py.exe') { Microsoft.PowerShell.Core\Get-Command -Name $Name -ErrorAction $ErrorAction }
}
$global:taskBreakCheck = $Mode -eq 'broken'
$global:taskBreakRuntime = $Mode -eq 'fresh-damaged'
function Start-Process {
    param($FilePath, $ArgumentList, $WindowStyle, [switch]$Wait, [switch]$PassThru, $RedirectStandardOutput, $RedirectStandardError, $Verb)
    if ($FilePath -like '*python-installer.exe') {
        Add-Content (Join-Path $taskRoot 'stages.log') 'python-install'
        if ($Mode -ne 'fresh-deleted' -or $ArgumentList -contains '/repair') {
            New-Item -ItemType Directory -Path (Join-Path $taskRoot 'python') -Force | Out-Null
            [IO.File]::WriteAllText((Join-Path $taskRoot 'python/python.exe'), 'controlled stand-in')
        }
        if ($ArgumentList -contains '/repair') { Add-Content (Join-Path $taskRoot 'stages.log') 'python-repair' }
        return [PSCustomObject]@{ ExitCode = 0 }
    }
    if ($Verb -eq 'RunAs') { return [PSCustomObject]@{ ExitCode = 3010 } }
    if (-not (Test-Path -LiteralPath $FilePath)) { throw 'Cannot find Python executable' }
    $stage = [IO.Path]::GetFileName($RedirectStandardOutput).Replace('.stdout.log','')
    Add-Content -LiteralPath (Join-Path $taskRoot 'stages.log') -Value $stage
    [IO.File]::WriteAllText($RedirectStandardOutput, '')
    [IO.File]::WriteAllText($RedirectStandardError, '')
    if ($stage -eq 'environment') {
        $dir = $ArgumentList[2].Trim('"')
        New-Item -ItemType Directory -Path (Join-Path $dir 'Scripts') -Force | Out-Null
        [IO.File]::WriteAllText((Join-Path $dir 'Scripts/python.exe'), 'controlled stand-in')
    }
    $code = if (($stage -eq 'ocr-startup' -and $Mode -notlike 'fresh*') -or ($stage -eq 'native-runtime-check' -and $Mode -eq 'restart')) { 1 } else { 0 }
    if ($stage -eq 'dependency-check' -and $global:taskBreakCheck) { $global:taskBreakCheck = $false; $code = 1 }
    if ($stage -eq 'runtime-check' -and $global:taskBreakRuntime) { $global:taskBreakRuntime = $false; $code = 1 }
    return [PSCustomObject]@{ ExitCode = $code }
}
function Invoke-WebRequest { param([switch]$UseBasicParsing, $Uri, $OutFile) }
function Get-AuthenticodeSignature { param($LiteralPath)
    $signer = if ($LiteralPath -like '*python-installer.exe') { 'Python Software Foundation' } else { 'Microsoft Corporation' }
    return [PSCustomObject]@{ Status='Valid'; SignerCertificate=[PSCustomObject]@{ Subject=$signer } }
}
& (Join-Path $PSScriptRoot 'install.ps1') -InstallRoot $taskRoot -ShortcutRoot (Join-Path $taskRoot 'shortcuts')
exit $LASTEXITCODE
'''
    (tmp_path / "replay.ps1").write_text(script, encoding="utf-8", newline="\r\n")
    return tmp_path


def replay(installer, mode="ocr-failure"):
    env = {name: value for name, value in os.environ.items() if name.upper() != "PSMODULEPATH"}
    return subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                           str(installer / "replay.ps1"), "-Mode", mode],
                          capture_output=True, text=True, timeout=30, env=env)


def test_successful_runtime_requiring_restart_keeps_exit_code(installer):
    result = replay(installer, "restart")
    assert result.returncode == 3010, result.stdout + result.stderr
    assert "installed successfully" in result.stdout
    assert "Installation failed:" not in result.stdout


def test_retry_reuses_checked_dependencies_after_ocr_failure(installer):
    for _ in range(2):
        assert replay(installer).returncode == 1
    stages = (installer / "installation/stages.log").read_text().splitlines()
    assert stages.count("environment") == stages.count("dependencies") == 1
    assert stages.count("dependency-check") == stages.count("ocr-startup") == 2
    assert len(list((installer / "installation/versions").iterdir())) == 1


def test_failed_reuse_check_repairs_environment(installer):
    assert replay(installer).returncode == 1
    result = replay(installer, "broken")
    assert result.returncode == 1
    stages = (installer / "installation/stages.log").read_text().splitlines()
    assert stages.count("environment") == stages.count("dependencies") == 2
    assert stages.count("dependency-check") == 3


def test_first_install_creates_logs_runtime_and_active_launcher(installer):
    result = replay(installer, "fresh")
    assert result.returncode == 0, result.stdout + result.stderr
    root = installer / "installation"
    assert (root / "installation.log").is_file()
    assert (root / "python/python.exe").is_file()
    assert (root / "runtime-check.stdout.log").is_file()
    assert (root / "Start-Eastgate.ps1").is_file()
    active = json.loads((root / "current-install.json").read_text())
    assert (root / "e" / active["environment"] / "Scripts/python.exe").is_file()
    assert (root / "Samsung-App.apk").is_file()
    assert len(list((root / "shortcuts").rglob("*.lnk"))) == 2


def test_removed_registered_python_is_repaired_before_launch(installer):
    result = replay(installer, "fresh-deleted")
    assert result.returncode == 0, result.stdout + result.stderr
    stages = (installer / "installation/stages.log").read_text().splitlines()
    assert "python-repair" in stages
    assert stages.index("python-repair") < stages.index("runtime-check")


@pytest.mark.parametrize("damage", ["missing", "corrupt", "extra", "manifest"])
def test_damaged_source_is_replaced_without_touching_data(installer, damage):
    assert replay(installer, "fresh").returncode == 0
    root = installer / "installation"
    previous = json.loads((root / "current-install.json").read_text())
    version = root / "versions" / previous["version"]
    requirements = version / "pipeline/requirements.txt"
    if damage == "missing":
        requirements.unlink()
    elif damage == "corrupt":
        requirements.write_text("broken source")
    elif damage == "extra":
        (version / "pipeline/stale_module.py").write_text("leftover module")
    else:
        (version / "manifest.json").write_text("broken manifest")
    database = root / "trial/app/invoices.sqlite3"
    database.write_bytes(b"existing invoices")
    result = replay(installer, "fresh")
    assert result.returncode == 0, result.stdout + result.stderr
    current = json.loads((root / "current-install.json").read_text())
    assert current["version"] != previous["version"]
    clean = root / "versions" / current["version"]
    assert (clean / "pipeline/requirements.txt").read_bytes() == b"flask==3.1.3\n"
    assert not (clean / "pipeline/stale_module.py").exists()
    assert database.read_bytes() == b"existing invoices"
    assert version.is_dir()
    assert replay(installer, "fresh").returncode == 0
    assert json.loads((root / "current-install.json").read_text()) == current


def test_partial_environment_is_kept_out_of_clean_retry(installer):
    assert replay(installer).returncode == 1
    root = installer / "installation"
    old = next((root / "e").iterdir())
    (old / ".requirements.sha256").unlink()
    (old / "stale-package.txt").write_text("interrupted install")
    result = replay(installer, "fresh")
    assert result.returncode == 0, result.stdout + result.stderr
    current = json.loads((root / "current-install.json").read_text())
    assert current["environment"] != old.name
    assert (old / "stale-package.txt").is_file()
    assert not (root / "e" / current["environment"] / "stale-package.txt").exists()


def test_failed_update_preserves_active_pair_and_environment(installer):
    assert replay(installer, "fresh").returncode == 0
    root = installer / "installation"
    active = (root / "current-install.json").read_bytes()
    old = root / "e" / json.loads(active)["environment"]
    (old / "keep.txt").write_text("working installation")
    result = replay(installer, "broken")
    assert result.returncode == 1
    assert (root / "current-install.json").read_bytes() == active
    assert (old / "keep.txt").read_text() == "working installation"
    assert len(list((root / "e").iterdir())) == 2


def test_existing_incomplete_private_runtime_is_repaired(installer):
    assert replay(installer, "fresh").returncode == 0
    result = replay(installer, "fresh-damaged")
    assert result.returncode == 0, result.stdout + result.stderr
    stages = (installer / "installation/stages.log").read_text().splitlines()
    assert stages.count("python-repair") == 1
    assert stages.count("runtime-check") == 3
