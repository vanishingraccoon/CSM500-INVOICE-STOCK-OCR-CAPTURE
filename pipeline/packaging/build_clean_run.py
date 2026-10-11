"""Build the standalone Windows trial reset tool; no installed Python is needed to run it."""
import hashlib
import json
import os
from pathlib import Path
import subprocess


def main():
    source = Path(__file__).resolve().parent
    destination = source.parents[1] / "output" / "clean-run"
    destination.mkdir(parents=True, exist_ok=True)
    executable = destination / "Clean Run.exe"
    compiler = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    subprocess.run([str(compiler), "/nologo", "/target:winexe", "/platform:x64",
                    "/reference:System.Windows.Forms.dll", "/reference:System.Drawing.dll",
                    f"/resource:{source / 'clean-run.ps1'},clean-run.ps1",
                    f"/out:{executable}", str(source / "CleanRun.cs")], check=True)
    manifest = {str(p.relative_to(source.parents[1])).replace("\\", "/"):
                hashlib.sha256(p.read_bytes()).hexdigest()
                for p in (source / "clean-run.ps1", source / "CleanRun.cs", executable)}
    (destination / "build-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Built {executable}")


if __name__ == "__main__":
    main()
