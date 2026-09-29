"""Build a Python zipapp or native executable and its verifiable archive."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import zipapp
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from gitimpact import __version__


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--native", action="store_true", help="requires PyInstaller in the build environment")
    args = parser.parse_args()
    output = ROOT / "dist"
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="git-impact-build-") as temporary:
        scratch = Path(temporary)
        if args.native:
            launcher = scratch / "launcher.py"
            launcher.write_text("from gitimpact.cli import main\nraise SystemExit(main())\n", encoding="utf-8")
            subprocess.run([sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm",
                            "--onefile", "--name", "git-impact", "--paths", str(ROOT),
                            "--distpath", str(output), "--workpath", str(scratch / "work"),
                            "--specpath", str(scratch), str(launcher)], check=True, cwd=ROOT)
            artifact = output / ("git-impact.exe" if platform.system() == "Windows" else "git-impact")
            target = platform.system().lower() + "-" + platform.machine().lower()
        else:
            source = scratch / "source"
            source.mkdir()
            shutil.copytree(ROOT / "gitimpact", source / "gitimpact", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            (source / "__main__.py").write_text("from gitimpact.cli import main\nraise SystemExit(main())\n", encoding="utf-8")
            artifact = output / "git-impact.pyz"
            zipapp.create_archive(source, artifact, interpreter="/usr/bin/env python3",
                                  compressed=True)
            target = "python"
        archive = output / f"git-impact-{__version__}-{target}.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            for path in (artifact, ROOT / "README.md", ROOT / "LICENSE", ROOT / "docs/index.html", ROOT / "docs/preview.png"):
                bundle.write(path, path.relative_to(ROOT).as_posix() if path.parent.name == "docs" else path.name)
        checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
        archive.with_suffix(".zip.sha256").write_text(checksum + "  " + archive.name + "\n", encoding="ascii")
        print(archive)
        print("SHA-256 " + checksum)


if __name__ == "__main__":
    main()
