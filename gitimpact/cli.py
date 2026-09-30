from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

from . import __version__
from .core import PreviewError, compare_preview, git, git_env, preview
from .report import html_report, terminal


def demo_report(mode="hard"):
    """A deliberately messy, synthetic repository; never uses the user's repo."""
    with tempfile.TemporaryDirectory(prefix="git-impact-demo-") as temporary:
        root = Path(temporary) / "sample-project"
        root.mkdir()
        empty = Path(temporary) / "empty-template"
        empty.mkdir()
        def run(*args):
            return subprocess.run(["git", "-C", str(root), *args], check=True,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=git_env(True)).stdout
        run("init", "-q", "--template=" + str(empty), "-b", "feature/invoices")
        run("config", "user.name", "Demo Author")
        run("config", "user.email", "demo@example.invalid")
        run("config", "core.autocrlf", "false")
        run("config", "core.filemode", "false")
        run("config", "gc.auto", "0")
        run("config", "maintenance.auto", "false")
        (root / "src").mkdir()
        (root / "src/invoice.py").write_text("def total(items):\n    return sum(items)\n", encoding="utf-8")
        (root / "README.md").write_text("# Invoice service\n\nA tiny demonstration project.\n", encoding="utf-8")
        (root / "output").write_text("A committed export placeholder.\n", encoding="utf-8")
        run("add", ".")
        run("commit", "-qm", "Start invoice service")
        (root / "src/invoice.py").write_text("def total(items):\n    return round(sum(items), 2)\n", encoding="utf-8")
        (root / "src/export.py").write_text("def export(invoice):\n    return str(invoice)\n", encoding="utf-8")
        run("rm", "output")
        run("add", ".")
        run("commit", "-qm", "Add export and round invoice totals")
        (root / "src/invoice.py").write_text("def total(items, tax=0.17):\n    return round(sum(items) * (1 + tax), 2)\n", encoding="utf-8")
        (root / "src/draft.py").write_text("# Staged work that has never been committed\nDRAFT_LIMIT = 25\n", encoding="utf-8")
        run("add", ".")
        (root / "src/invoice.py").write_text("def total(items, tax=0.18):\n    # This newer version is not staged yet\n    return round(sum(items) * (1 + tax), 2)\n", encoding="utf-8")
        (root / "README.md").write_text("# Invoice service\n\nDraft deployment instructions, not staged yet.\n", encoding="utf-8")
        (root / "notes.txt").write_text("Untracked notes stay untouched by this reset.\n", encoding="utf-8")
        (root / "output").mkdir()
        (root / "output/local-export.csv").write_text("invoice,total\nLOCAL-DRAFT,118.00\n", encoding="utf-8")
        native = []
        for args, explanation in (
            (("diff", "--cached", "HEAD~1", "--name-only"), "Index compared with the target; --staged is a synonym for --cached."),
            (("diff", "HEAD~1", "--name-only"), "Tracked working files compared with the target; ordinary untracked content is omitted."),
            (("ls-files", "--others", "--exclude-standard"), "Untracked paths, including an unrelated note and the obstructing export.")):
            native.append({"command": "git " + " ".join(args), "explanation": explanation,
                           "output": git(root, *args, isolated=True).decode("utf-8", "replace").strip()})
        report = compare_preview(root, "HEAD~1", mode)
        report["native_git_demo"] = native
        return report


def main(argv=None):
    parser = argparse.ArgumentParser(prog="git-impact", description="Preview a Git reset without changing your repository.",
                                     epilog="Examples: git-impact reset --hard HEAD~1\n          git-impact demo --html demo.html",
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("reset", "demo"))
    parser.add_argument("target", nargs="?", default="HEAD", help="local reset target (default: HEAD)")
    group = parser.add_mutually_exclusive_group()
    for mode in ("soft", "mixed", "hard"):
        group.add_argument("--" + mode, dest="mode", action="store_const", const=mode)
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="repository directory (default: current directory)")
    parser.add_argument("--html", type=Path, help="write a standalone HTML report outside the source repository")
    parser.add_argument("--json", action="store_true", help="print the machine-readable report instead of the summary")
    parser.add_argument("--diff", action="store_true", help="include content diffs in terminal output")
    parser.add_argument("--compare-modes", action="store_true", help="simulate soft, mixed and hard for the same target (copies files for comparison)")
    parser.add_argument("--max-copy-mib", type=int, default=256, help="maximum snapshot size in MiB (default: 256)")
    parser.add_argument("--max-files", type=int, default=20000, help="maximum snapshot file count (default: 20000)")
    parser.add_argument("--version", action="version", version="Git Impact " + __version__)
    args = parser.parse_args(argv)
    try:
        if args.command == "demo":
            if args.target != "HEAD":
                raise PreviewError("The demo uses its own HEAD~1 target. Do not supply a target.")
            report = demo_report(args.mode or "hard")
        else:
            if args.html:
                # Reject before generating anything: writing a report into the
                # observed repo would violate the read-only contract.
                from .core import decode, git
                root = Path(decode(git(args.repo, "rev-parse", "--show-toplevel")).strip()).resolve()
                if args.html.resolve().is_relative_to(root):
                    raise PreviewError("Choose an HTML output path outside the source repository.")
            inspect = compare_preview if args.compare_modes else preview
            report = inspect(args.repo, args.target, args.mode or "mixed", args.max_copy_mib, args.max_files)
        if args.html:
            output = args.html.resolve()
            # Avoid silently replacing an existing artifact.
            with output.open("x", encoding="utf-8", errors="replace", newline="") as stream:
                stream.write(html_report(report))
            print("HTML report: " + str(output), file=sys.stderr)
        if args.json:
            print(json.dumps(report, ensure_ascii=True, indent=2))
        else:
            print(terminal(report, args.diff), end="")
        return 0
    except (PreviewError, OSError, subprocess.SubprocessError) as exc:
        print("git-impact: " + str(exc), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("git-impact: cancelled; the source repository was not modified.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
