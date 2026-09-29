from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from gitimpact.cli import main
from gitimpact.core import PreviewError, git_env, index_entries, preview
from gitimpact.report import html_report


class Repo:
    def __init__(self, root):
        self.root = root
        root.mkdir()
        self.run("init", "-q", "--template=", "-b", "feature")
        for key, value in (("user.name", "Test"), ("user.email", "test@example.invalid"),
                           ("core.autocrlf", "false"), ("core.filemode", "false"),
                           ("gc.auto", "0"), ("maintenance.auto", "false")):
            self.run("config", key, value)

    def run(self, *args):
        return subprocess.run(["git", "--no-optional-locks", "-C", str(self.root), *args],
                              env=git_env(True), check=True, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE).stdout

    def write(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data.encode() if isinstance(data, str) else data)

    def commit(self, message):
        self.run("add", "-A")
        self.run("commit", "-qm", message)

    def files(self, metadata=False):
        result = {}
        for path in self.root.rglob("*"):
            name = path.relative_to(self.root).as_posix()
            if path.is_file() and (metadata or not name.startswith(".git/")):
                result[name] = (hashlib.sha256(path.read_bytes()).hexdigest(),
                                stat.S_IMODE(path.stat().st_mode))
        return result


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="git-impact-test-")
        self.addCleanup(self.temp.cleanup)
        self.repo = Repo(Path(self.temp.name) / "repo")

    def setup_versions(self):
        r = self.repo
        r.write("code.py", "old\n")
        r.write("keep.txt", "keep\n")
        r.commit("first")
        r.write("code.py", "committed\n")
        r.write("new.txt", "second commit\n")
        r.commit("second")
        r.write("code.py", "staged\n")
        r.write("draft.txt", "staged addition\n")
        r.run("add", "-A")
        r.write("code.py", "unstaged\n")
        r.write("untracked.txt", "leave me alone\n")

    def assert_matches_git(self, mode, target="HEAD~1"):
        r = self.repo
        pristine = r.files(metadata=True)
        before_index = index_entries(r.root)
        before_files = r.files()
        report = preview(r.root, target, mode)
        self.assertEqual(pristine, r.files(metadata=True), "Preview changed source bytes/modes")
        r.run("reset", "--" + mode, target)
        after_index = index_entries(r.root)
        after_files = r.files()
        index_paths = {p for p in before_index.keys() | after_index.keys()
                       if before_index.get(p) != after_index.get(p)}
        work_paths = {p for p in before_files.keys() | after_files.keys()
                      if before_files.get(p) != after_files.get(p)}
        self.assertEqual(index_paths, {c["path"] for c in report["index_changes"]})
        self.assertEqual(work_paths, {c["path"] for c in report["working_tree_changes"]})
        self.assertEqual(r.run("rev-parse", "HEAD").decode().strip(), report["head_after"])
        for change in report["index_changes"]:
            self.assertEqual(after_index.get(change["path"]), change["after"])
        for change in report["working_tree_changes"]:
            actual = after_files.get(change["path"])
            expected = change["after"]
            self.assertEqual(actual[0] if actual else None, expected["sha256"] if expected else None)
        return report

    def test_soft_reset_matches_git_and_preserves_index_and_files(self):
        self.setup_versions()
        report = self.assert_matches_git("soft")
        self.assertEqual([], report["index_changes"])
        self.assertEqual([], report["working_tree_changes"])
        self.assertEqual(0, report["copied_bytes"])
        self.assertEqual(1, report["commits_removed_from_current_history"])

    def test_mixed_reset_matches_git_and_preserves_files(self):
        self.setup_versions()
        report = self.assert_matches_git("mixed")
        self.assertEqual([], report["working_tree_changes"])
        self.assertIn("-staged", next(c["diff"] for c in report["index_changes"] if c["path"] == "code.py"))

    def test_hard_reset_separates_staged_and_unstaged_content(self):
        self.setup_versions()
        report = self.assert_matches_git("hard")
        index_diff = next(c["diff"] for c in report["index_changes"] if c["path"] == "code.py")
        work_diff = next(c["diff"] for c in report["working_tree_changes"] if c["path"] == "code.py")
        self.assertIn("-staged", index_diff)
        self.assertIn("-unstaged", work_diff)
        self.assertEqual("leave me alone\n", (self.repo.root / "untracked.txt").read_text())

    def test_hard_reset_same_head_discards_uncommitted_changes(self):
        self.setup_versions()
        report = self.assert_matches_git("hard", "HEAD")
        self.assertEqual(0, report["commits_removed_from_current_history"])
        self.assertIn("draft.txt", {c["path"] for c in report["working_tree_changes"]})

    def test_hard_reset_replaces_untracked_file_obstructing_directory(self):
        r = self.repo
        r.write("dir/item.txt", "restore me\n")
        r.write("keep.txt", "keep\n")
        r.commit("first")
        r.run("rm", "-r", "dir")
        r.commit("remove directory")
        r.write("dir", "untracked obstruction\n")
        report = self.assert_matches_git("hard")
        self.assertFalse(next(c["was_tracked"] for c in report["working_tree_changes"] if c["path"] == "dir"))

    def test_hard_reset_replaces_ignored_directory_obstructing_file(self):
        r = self.repo
        r.write("block", "restore me\n")
        r.write("keep.txt", "keep\n")
        r.commit("first")
        r.run("rm", "block")
        r.write(".gitignore", "block/\n")
        r.commit("remove file")
        r.write("block/sub/secret.txt", "ignored content\n")
        report = self.assert_matches_git("hard")
        self.assertIn("block/sub/secret.txt", {c["path"] for c in report["working_tree_changes"]})

    def test_deleted_working_file_is_recreated(self):
        self.setup_versions()
        (self.repo.root / "keep.txt").unlink()
        self.assert_matches_git("hard", "HEAD")

    def test_unicode_spaces_binary_and_missing_final_newline(self):
        r = self.repo
        r.write("שלום name.txt", "old")
        r.write("data.bin", b"\0old")
        r.commit("first")
        r.write("שלום name.txt", "new")
        r.write("data.bin", b"\0new")
        r.commit("second")
        report = self.assert_matches_git("hard")
        self.assertIn("No newline at end of file", next(c["diff"] for c in report["working_tree_changes"] if c["path"].endswith(".txt")))
        self.assertEqual("Binary content differs.", next(c["diff"] for c in report["working_tree_changes"] if c["path"] == "data.bin"))

    def test_line_ending_attributes_match_git(self):
        r = self.repo
        r.write(".gitattributes", "*.txt text eol=crlf\n")
        r.write("text.txt", b"old\r\n")
        r.commit("first")
        r.write("text.txt", b"new\r\n")
        r.commit("second")
        self.assert_matches_git("hard")

    def test_autocrlf_preserves_clean_lf_files_on_same_head_reset(self):
        r = self.repo
        r.run("config", "core.autocrlf", "true")
        r.write("file.txt", b"hello\n")
        r.commit("base")
        report = self.assert_matches_git("hard", "HEAD")
        self.assertEqual([], report["working_tree_changes"])
        self.assertEqual(b"hello\n", (r.root / "file.txt").read_bytes())

    def test_detached_head(self):
        self.setup_versions()
        self.repo.run("checkout", "--detach", "-q", "HEAD")
        report = self.assert_matches_git("hard")
        self.assertEqual("detached HEAD", report["branch"])

    def test_intent_to_add_matches_git(self):
        self.setup_versions()
        self.repo.write("intent.txt", "not fully staged\n")
        self.repo.run("add", "-N", "intent.txt")
        self.assert_matches_git("hard")

    @unittest.skipIf(os.name == "nt", "Executable file modes are not meaningful on Windows")
    def test_executable_mode_changes(self):
        r = self.repo
        r.run("config", "core.filemode", "true")
        r.write("script.sh", "echo hi\n")
        r.commit("first")
        (r.root / "script.sh").chmod(0o755)
        r.commit("make executable")
        self.assert_matches_git("hard")

    def test_linked_worktree_uses_shared_objects_and_private_index(self):
        self.setup_versions()
        linked = Path(self.temp.name) / "linked"
        self.repo.run("worktree", "add", "--detach", "-q", str(linked), "HEAD")
        before = (linked / ".git").read_bytes()
        report = preview(linked, "HEAD~1", "hard")
        self.assertEqual(before, (linked / ".git").read_bytes())
        self.assertEqual(2, len(report["working_tree_changes"]))

    def test_large_diff_omitted_but_change_detected(self):
        r = self.repo
        r.write("large.txt", b"a" * 70000)
        r.commit("first")
        r.write("large.txt", b"b" * 70000)
        r.commit("second")
        report = self.assert_matches_git("hard")
        self.assertIn("Diff omitted", report["working_tree_changes"][0]["diff"])

    def test_destination_size_checked_before_hard_reset(self):
        r = self.repo
        r.write("large.txt", b"a" * (1024 * 1024 + 1))
        r.commit("first")
        r.run("rm", "large.txt")
        r.write("keep.txt", "small\n")
        r.commit("second")
        before = r.files(metadata=True)
        with self.assertRaisesRegex(PreviewError, "Destination tree exceeds"):
            preview(r.root, "HEAD~1", "hard", max_copy_mib=1)
        self.assertEqual(before, r.files(metadata=True))

    def test_source_reference_transaction_hook_never_runs_in_preview(self):
        import shlex
        self.setup_versions()
        r = self.repo
        marker = Path(self.temp.name) / "hook-called"
        r.write(".git/hooks/reference-transaction", "#!/bin/sh\nprintf called >> " + shlex.quote(marker.as_posix()) + "\n")
        (r.root / ".git/hooks/reference-transaction").chmod(0o755)
        preview(r.root, "HEAD~1", "hard")
        self.assertFalse(marker.exists())
        r.run("reset", "--hard", "HEAD~1")
        self.assertTrue(marker.exists(), "Control operation must demonstrate the hook is executable")

    def test_terminal_diff_escapes_control_sequences(self):
        from gitimpact.report import terminal
        self.setup_versions()
        self.repo.write("code.py", "\x1b[2Jmalicious terminal sequence\n")
        text = terminal(preview(self.repo.root, "HEAD~1", "hard"), show_diff=True)
        self.assertNotIn("\x1b", text)
        self.assertIn("\\x1b[2J", text)

    def test_rejects_current_filter_without_running_it(self):
        r = self.repo
        r.write("file.txt", "content\n")
        r.commit("first")
        r.write(".gitattributes", "*.txt filter=evil\n")
        r.run("config", "filter.evil.smudge", "echo DO-NOT-EXECUTE")
        before = r.files(metadata=True)
        with self.assertRaisesRegex(PreviewError, "filters"):
            preview(r.root, "HEAD", "hard")
        self.assertEqual(before, r.files(metadata=True))

    def test_rejects_filter_introduced_by_target(self):
        r = self.repo
        r.write(".gitattributes", "*.txt filter=lfs\n")
        r.write("file.txt", "content\n")
        r.commit("first")
        r.run("rm", ".gitattributes")
        r.commit("remove attributes")
        with self.assertRaisesRegex(PreviewError, "filters"):
            preview(r.root, "HEAD~1", "hard")

    def test_rejects_skip_worktree_and_assume_unchanged(self):
        self.setup_versions()
        r = self.repo
        for flag in ("skip-worktree", "assume-unchanged"):
            r.run("update-index", "--" + flag, "keep.txt")
            with self.assertRaisesRegex(PreviewError, "index entries"):
                preview(r.root)
            r.run("update-index", "--no-" + flag, "keep.txt")

    def test_rejects_sparse_checkout(self):
        self.setup_versions()
        self.repo.run("config", "core.sparseCheckout", "true")
        with self.assertRaisesRegex(PreviewError, "Sparse"):
            preview(self.repo.root)

    def test_rejects_partial_clone(self):
        self.setup_versions()
        self.repo.run("config", "extensions.partialClone", "origin")
        with self.assertRaisesRegex(PreviewError, "Partial"):
            preview(self.repo.root)

    def test_rejects_unmerged_index(self):
        r = self.repo
        r.write("file.txt", "base\n")
        r.commit("base")
        r.run("checkout", "-qb", "other")
        r.write("file.txt", "other\n")
        r.commit("other")
        r.run("checkout", "-q", "feature")
        r.write("file.txt", "ours\n")
        r.commit("ours")
        with self.assertRaises(subprocess.CalledProcessError):
            r.run("merge", "other")
        with self.assertRaisesRegex(PreviewError, "Unmerged"):
            preview(r.root)

    def test_rejects_copy_limits_and_locked_index(self):
        self.setup_versions()
        with self.assertRaisesRegex(PreviewError, "copy limit"):
            preview(self.repo.root, max_files=1)
        (self.repo.root / ".git/index.lock").write_bytes(b"")
        with self.assertRaisesRegex(PreviewError, "locked"):
            preview(self.repo.root)

    def test_rejects_case_only_rename(self):
        r = self.repo
        r.write("Name.txt", "content\n")
        r.commit("first")
        r.run("mv", "Name.txt", "name.txt")
        r.commit("rename")
        with self.assertRaisesRegex(PreviewError, "Case-colliding"):
            preview(r.root, "HEAD~1", "hard")

    def test_rejects_nested_repository_obstruction(self):
        r = self.repo
        r.write("block", "content\n")
        r.commit("first")
        r.run("rm", "block")
        r.write("keep.txt", "keep\n")
        r.commit("remove block")
        Repo(r.root / "block")
        with self.assertRaisesRegex(PreviewError, "nested repository"):
            preview(r.root, "HEAD~1", "hard")

    def test_refuses_changed_source_during_simulation(self):
        self.setup_versions()
        import gitimpact.core as core
        actual_git = core.git
        def changing_git(root, *args, **kwargs):
            result = actual_git(root, *args, **kwargs)
            if args and args[0] == "reset":
                self.repo.write("code.py", "concurrent edit\n")
            return result
        with patch.object(core, "git", side_effect=changing_git):
            with self.assertRaisesRegex(PreviewError, "changed during"):
                preview(self.repo.root, "HEAD~1", "hard")

    def test_refuses_index_changed_during_initial_inspection(self):
        self.setup_versions()
        import gitimpact.core as core
        actual_entries = core.index_entries
        def changing_entries(root, *args, **kwargs):
            result = actual_entries(root, *args, **kwargs)
            if Path(root).resolve() == self.repo.root.resolve():
                self.repo.run("add", "code.py")
            return result
        with patch.object(core, "index_entries", side_effect=changing_entries):
            with self.assertRaisesRegex(PreviewError, "changed during"):
                preview(self.repo.root, "HEAD~1", "hard")

    def test_refuses_branch_changed_to_same_commit_during_preview(self):
        self.setup_versions()
        self.repo.run("branch", "other")
        import gitimpact.core as core
        actual_git = core.git
        def changing_git(root, *args, **kwargs):
            result = actual_git(root, *args, **kwargs)
            if args and args[0] == "reset":
                self.repo.run("symbolic-ref", "HEAD", "refs/heads/other")
            return result
        with patch.object(core, "git", side_effect=changing_git):
            with self.assertRaisesRegex(PreviewError, "changed during"):
                preview(self.repo.root, "HEAD~1", "hard")

    def test_cli_json_and_html_rejects_output_in_source(self):
        self.setup_versions()
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(["reset", "HEAD~1", "--hard", "--repo", str(self.repo.root), "--json"])
        self.assertEqual(0, code, stderr.getvalue())
        self.assertEqual("hard", json.loads(stdout.getvalue())["mode"])
        with contextlib.redirect_stderr(io.StringIO()):
            code = main(["reset", "--repo", str(self.repo.root), "--html", str(self.repo.root / "report.html")])
        self.assertEqual(2, code)
        self.assertFalse((self.repo.root / "report.html").exists())

    def test_html_escapes_file_content_and_no_external_resources(self):
        self.setup_versions()
        self.repo.write("code.py", '<script>alert("private")</script>\n')
        report = preview(self.repo.root, "HEAD~1", "hard")
        rendered = html_report(report)
        self.assertNotIn('<script>alert("private")</script>', rendered)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertIn("default-src 'none'", rendered)
        self.assertNotIn("https://", rendered)

    def test_no_valid_repo_or_target_returns_actionable_error(self):
        self.setup_versions()
        for path, target in ((self.repo.root, "missing-ref"), (Path(self.temp.name), "HEAD")):
            with self.assertRaises(PreviewError):
                preview(path, target)


if __name__ == "__main__":
    unittest.main()
