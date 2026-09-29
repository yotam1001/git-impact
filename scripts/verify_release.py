"""Verify checksums, archive contents, CLI exit codes and native/zipapp demos."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    archives = list((ROOT / "dist").glob("*.zip"))
    if not archives:
        raise RuntimeError("Build release archives first.")
    for archive in archives:
        expected = archive.with_suffix(".zip.sha256").read_text().split()[0]
        if hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
            raise RuntimeError("Checksum mismatch: " + archive.name)
        with tempfile.TemporaryDirectory(prefix="git-impact-release-qa-") as temporary:
            folder = Path(temporary)
            with zipfile.ZipFile(archive) as bundle:
                executable = "git-impact.pyz" if "-python.zip" in archive.name else "git-impact.exe" if "-windows-" in archive.name else "git-impact"
                if set(bundle.namelist()) != {executable, "README.md", "LICENSE", "docs/index.html", "docs/preview.png"}:
                    raise RuntimeError("Unexpected archive entries: " + archive.name)
                bundle.extractall(folder)
            path = folder / executable
            path.chmod(0o755)
            command = [sys.executable, str(path)] if path.suffix == ".pyz" else [str(path)]
            env = os.environ.copy()
            if os.name == "nt" and path.suffix == ".exe":
                # Verify native execution without a Python installation on PATH.
                git = shutil.which("git")
                if git is None:
                    raise RuntimeError("Git must be installed and available on PATH.")
                env["PATH"] = str(Path(git).parent) + os.pathsep + str(Path(os.environ["SystemRoot"]) / "System32")
            version = subprocess.run(command + ["--version"], cwd=folder, env=env, capture_output=True, text=True, check=True)
            if "Git Impact 0.1.0" not in version.stdout:
                raise RuntimeError("Version mismatch: " + archive.name)
            for mode in ("soft", "mixed", "hard"):
                result = subprocess.run(command + ["demo", "--" + mode, "--json"], cwd=folder,
                                        env=env, capture_output=True, text=True, check=True, timeout=60)
                report = json.loads(result.stdout)
                expected_counts = {"soft": (0, 0), "mixed": (3, 0), "hard": (3, 4)}[mode]
                if (len(report["index_changes"]), len(report["working_tree_changes"])) != expected_counts:
                    raise RuntimeError("Demo counts mismatch: " + archive.name + " " + mode)
            failure = subprocess.run(command + ["reset", "--repo", str(folder)], cwd=folder,
                                     env=env, capture_output=True, text=True)
            if failure.returncode != 2:
                raise RuntimeError("CLI error must return exit code 2: " + archive.name)
        print("Verified checksum, contents, version, three reset modes, and error exit: " + archive.name)


if __name__ == "__main__":
    main()
