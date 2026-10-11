"""Build the private Eastgate trial installer from explicit source and baseline files."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path
import zipfile


PACKAGES = ("app", "checks", "eval", "hwr", "ingest", "normaliser", "ocr", "parser", "tools", "docs")
FILES = ("config.py", "models.py", "refs.py", "requirements.txt", "requirements-ocr.txt", "launch.ps1", "README.md")


def build_payload(workspace: Path, destination: Path) -> dict:
    pipeline = workspace / "invoice-pipeline"
    baseline = workspace / "trial-prep/eastgate/run-2026-09-28-baseline"
    selected = [(pipeline / name, "pipeline/" + name) for name in FILES]
    for package in PACKAGES:
        for path in sorted((pipeline / package).rglob("*")):
            if path.is_file() and not any(part.startswith(".") or part == "__pycache__" for part in path.relative_to(pipeline).parts):
                selected.append((path, "pipeline/" + path.relative_to(pipeline).as_posix()))
    for name in ("app/shops.json", "data/pilot/stock_valuation_eastgate_2026-09-20.csv",
                 "data/pilot/purchasing_report_eastgate_2026-09-27.csv", "data/pilot/additional-suppliers.json"):
        selected.append((baseline / name, "baseline/" + name))
    selected.append((workspace / "trial-prep/eastgate/invoice-app-eastgate-debug.apk", "phone/invoice-app-eastgate-debug.apk"))
    manifest = {name: hashlib.sha256(path.read_bytes()).hexdigest() for path, name in selected}
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, name in selected:
            archive.write(path, name)
        archive.writestr("manifest.json", json.dumps(manifest, indent=2))
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    manifest = build_payload(args.workspace.resolve(), args.output / "payload.zip")
    installer_source = Path(__file__).with_name("install.ps1")
    (args.output / "install.ps1").write_bytes(installer_source.read_bytes())
    executable = args.output / "Eastgate-Invoice-Server-Setup.exe"
    compiler = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    subprocess.run([
        str(compiler), "/nologo", "/target:winexe", "/platform:x64",
        "/reference:System.Windows.Forms.dll", "/reference:System.Drawing.dll",
        f"/resource:{args.output / 'install.ps1'},install.ps1",
        f"/resource:{args.output / 'payload.zip'},payload.zip",
        f"/out:{executable}", str(Path(__file__).with_name("Setup.cs")),
    ], check=True)
    build = {
        "files": manifest,
        "executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        "payload_sha256": hashlib.sha256((args.output / "payload.zip").read_bytes()).hexdigest(),
    }
    (args.output / "build-manifest.json").write_text(json.dumps(build, indent=2) + "\n", encoding="utf-8")
    print(f"Built {executable} with {len(manifest)} payload files.")


if __name__ == "__main__":
    main()
