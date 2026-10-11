"""Exercise the standalone reset against isolated installations, never live trial data."""
import json
import os
from pathlib import Path
import subprocess

import pytest


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows packaging tool")
SCRIPT = Path(__file__).resolve().parents[1] / "packaging" / "clean-run.ps1"


@pytest.fixture
def installation(tmp_path):
    root = tmp_path / "EastgateInvoiceServer"
    app = root / "trial" / "app"
    app.mkdir(parents=True)
    (root / "current-install.json").write_text(json.dumps({"version": "v9", "environment": "v9"}))
    files = {
        "trial/app/shops.json": '{"shops": [{"id": "eastgate"}]}',
        "trial/app/invoices.db": "original database and device pairings",
        "trial/app/photos/invoice.jpg": "original photo",
        "trial/app/reports/report.csv": "original report",
        "trial/app/ocr_cache/result.json": "original OCR",
        "trial/data/pilot/stock.csv": "Eastgate baseline",
        "versions/v9/pipeline/app/serve.py": "installed service",
        "versions/v9/baseline/app/shops.json": '{"shops": []}',
        "e/v9/Scripts/python.exe": "installed runtime",
        "models/ocr.bin": "installed model",
    }
    for relative, contents in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents)
    return root


def run_clean(root):
    environment = {k: v for k, v in os.environ.items() if k.upper() != "PSMODULEPATH"}
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT),
         "-InstallRoot", str(root)],
        capture_output=True, text=True, env=environment, timeout=30,
    )


def snapshot(directory):
    return {str(p.relative_to(directory)): p.read_bytes() for p in directory.rglob("*") if p.is_file()}


def test_archives_trial_and_preserves_installation_and_baseline(installation):
    app = installation / "trial" / "app"
    before = snapshot(app)
    preserved = {name: snapshot(installation / name) for name in ("trial/data", "versions", "e", "models")}
    result = run_clean(installation)
    assert result.returncode == 0, result.stdout + result.stderr
    backups = list((installation / "clean-run-backups").iterdir())
    assert len(backups) == 1
    assert snapshot(backups[0] / "app") == before
    assert snapshot(app) == {"shops.json": before["shops.json"]}
    assert json.loads((backups[0] / "clean-run.json").read_text())["previous_app_present"] is True
    for name, contents in preserved.items():
        assert snapshot(installation / name) == contents


def test_repeated_runs_keep_both_backups(installation):
    assert run_clean(installation).returncode == 0
    app = installation / "trial" / "app"
    (app / "invoices.db").write_text("second trial")
    assert run_clean(installation).returncode == 0
    backups = list((installation / "clean-run-backups").iterdir())
    assert len(backups) == 2
    databases = {(backup / "app" / "invoices.db").read_text() for backup in backups}
    assert databases == {"original database and device pairings", "second trial"}
    assert not (app / "invoices.db").exists()


@pytest.mark.parametrize("filename", [".launcher.lock", "invoices.db", "invoices.db-wal", "invoices.db-shm"])
def test_open_server_files_refuse_reset(installation, filename):
    file = installation / "trial" / "app" / filename
    file.touch(exist_ok=True)
    before = snapshot(installation / "trial" / "app")
    with file.open("r+b"):
        result = run_clean(installation)
    assert result.returncode == 1
    assert "Close the Eastgate Invoice Server window" in result.stdout
    assert snapshot(installation / "trial" / "app") == before
    assert not (installation / "clean-run-backups").exists()


def test_setup_lock_refuses_reset(installation):
    lock = installation / ".setup.lock"
    lock.touch()
    before = snapshot(installation / "trial" / "app")
    with lock.open("r+b"):
        result = run_clean(installation)
    assert result.returncode == 1
    assert "Another installation or Clean Run" in result.stdout
    assert snapshot(installation / "trial" / "app") == before


def test_invalid_record_refuses_reset(installation):
    (installation / "current-install.json").write_text(json.dumps({"version": "../outside", "environment": "v9"}))
    before = snapshot(installation / "trial" / "app")
    result = run_clean(installation)
    assert result.returncode == 1
    assert "installation record is invalid" in result.stdout
    assert snapshot(installation / "trial" / "app") == before
    assert not (installation / "clean-run-backups").exists()


def test_missing_installation_creates_nothing(tmp_path):
    root = tmp_path / "not-installed"
    result = run_clean(root)
    assert result.returncode == 1
    assert "not installed" in result.stdout
    assert not root.exists()


def test_first_run_uses_baseline_when_app_folder_missing(installation):
    app = installation / "trial" / "app"
    retained = installation / "retained-fixture-app"
    app.rename(retained)
    result = run_clean(installation)
    assert result.returncode == 0, result.stdout + result.stderr
    assert snapshot(app) == snapshot(installation / "versions" / "v9" / "baseline" / "app")
    backup = next((installation / "clean-run-backups").iterdir())
    assert json.loads((backup / "clean-run.json").read_text())["previous_app_present"] is False
