"""Let Git perform the operation in a disposable, isolated working tree."""

from __future__ import annotations

import difflib
import hashlib
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import tempfile

from . import __version__


class PreviewError(Exception):
    """The operation could not be previewed reliably."""


def git_env(isolated=False):
    # Explicit --repo wins over inherited Git directory/index/config overrides.
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    env.update(GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0", GIT_NO_LAZY_FETCH="1")
    if isolated:
        env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    return env


def git(root, *args, data=None, isolated=False, check=True, env_extra=None):
    env = git_env(isolated)
    env.update(env_extra or {})
    command = ["git", "--no-lazy-fetch", "--no-optional-locks", "-c", "core.fsmonitor=false",
               "-c", "core.untrackedCache=false", "-c", "core.hooksPath=" + os.devnull,
               "-C", str(root), *args]
    try:
        result = subprocess.run(command, input=data, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env=env, timeout=60)
    except FileNotFoundError as exc:
        raise PreviewError("Git was not found on PATH.") from exc
    except subprocess.TimeoutExpired as exc:
        raise PreviewError("A Git read or sandbox operation exceeded 60 seconds.") from exc
    if check and result.returncode:
        message = result.stderr.decode("utf-8", "replace").strip()
        if "unknown option: --no-lazy-fetch" in message:
            raise PreviewError("Update Git: this version lacks --no-lazy-fetch, required for a preview without fetching objects.")
        raise PreviewError(message or "Git could not complete this operation.")
    return result.stdout if check else result


def decode(data):
    return data.decode("utf-8", "surrogateescape")


def resolve(root, ref):
    return decode(git(root, "rev-parse", "--verify", "--end-of-options", ref + "^{commit}")).strip()


def config(root):
    result = {}
    for item in git(root, "config", "--null", "--list").split(b"\0"):
        if item:
            key, _, value = decode(item).partition("\n")
            result[key.lower()] = value
    return result


def index_entries(root, isolated=False):
    entries = {}
    for item in git(root, "ls-files", "--stage", "-z", isolated=isolated).split(b"\0"):
        if not item:
            continue
        metadata, path = item.split(b"\t", 1)
        mode, oid, stage = decode(metadata).split()
        if stage != "0":
            raise PreviewError("Unmerged index entries are not supported. Resolve the merge first.")
        entries[decode(path)] = {"mode": mode, "oid": oid}
    return entries


def tree_entries(root, target):
    result = {}
    for item in git(root, "ls-tree", "-r", "-z", target).split(b"\0"):
        if item:
            metadata, path = item.split(b"\t", 1)
            mode, kind, oid = decode(metadata).split()
            result[decode(path)] = {"mode": mode, "oid": oid}
    return result


def safe_path(name):
    parts = PurePosixPath(name).parts
    if not parts or PurePosixPath(name).is_absolute() or any(
            p in ("..", ".git") or "\\" in p or ":" in p for p in parts):
        raise PreviewError("Unsupported repository path: " + repr(name))
    return parts


def file_digest(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_reparse(info):
    return bool(getattr(info, "st_file_attributes", 0) & 0x400)


def inventory(root, names, max_bytes, max_files):
    """Capture tracked files and whole untracked obstructions, without following links."""
    result = {}
    total = 0

    def visit(path):
        nonlocal total
        try:
            info = path.lstat()
        except FileNotFoundError:
            return
        if stat.S_ISLNK(info.st_mode) or is_reparse(info):
            raise PreviewError("Symlinks and junctions are not supported: " + str(path.relative_to(root)))
        if stat.S_ISDIR(info.st_mode):
            if (path / ".git").exists():
                raise PreviewError("A nested repository would be affected; preview refused.")
            for child in sorted(path.iterdir()):
                visit(child)
            return
        if not stat.S_ISREG(info.st_mode):
            raise PreviewError("Special files are not supported: " + str(path.relative_to(root)))
        name = path.relative_to(root).as_posix()
        if name in result:
            return
        total += info.st_size
        if total > max_bytes or len(result) >= max_files:
            raise PreviewError("Snapshot exceeds the copy limit. Increase --max-copy-mib or --max-files if appropriate.")
        result[name] = {"sha256": file_digest(path), "size": info.st_size,
                        "permissions": stat.S_IMODE(info.st_mode)}

    for name in sorted(names):
        path = root
        parts = safe_path(name)
        for n, part in enumerate(parts):
            path = path / part
            try:
                info = path.lstat()
            except FileNotFoundError:
                break
            if stat.S_ISLNK(info.st_mode) or is_reparse(info):
                raise PreviewError("Symlinks and junctions are not supported: " + str(path.relative_to(root)))
            if n == len(parts) - 1 or not stat.S_ISDIR(info.st_mode):
                visit(path)
                break
    return result


def attr_paths(root):
    result = []
    for variable in ("GIT_ATTR_GLOBAL", "GIT_ATTR_SYSTEM"):
        value = decode(git(root, "var", variable)).strip()
        result.append(Path(value).expanduser() if value else Path(os.devnull))
    info = Path(decode(git(root, "rev-parse", "--path-format=absolute", "--git-path", "info/attributes")).strip())
    result.append(info)
    return result


def attr_snapshot(paths):
    return [(str(p), p.read_bytes() if p.is_file() else None) for p in paths]


def validate_attrs(root, names, isolated=False, cached=False):
    if not names:
        return
    data = b"\0".join(n.encode("utf-8", "surrogateescape") for n in sorted(names)) + b"\0"
    args = ["check-attr", "-z", "--all"]
    if cached:
        args.append("--cached")
    args.append("--stdin")
    records = git(root, *args, data=data, isolated=isolated).split(b"\0")
    for i in range(0, len(records) - 1, 3):
        name, attribute, value = records[i:i + 3]
        if attribute in (b"filter", b"working-tree-encoding") and value not in (b"unset", b"unspecified"):
            raise PreviewError("Custom checkout filters/LFS and working-tree-encoding are not supported: " + decode(name))


def diff_text(before, after, name, limit=65536):
    if before == after:
        return "Content is unchanged; only the file mode changes."
    if len(before) > limit or len(after) > limit:
        return "Diff omitted: content exceeds 64 KiB. Hashes and sizes were compared."
    if b"\0" in before or b"\0" in after:
        return "Binary content differs."
    try:
        old = before.decode("utf-8").splitlines(keepends=True)
        new = after.decode("utf-8").splitlines(keepends=True)
    except UnicodeDecodeError:
        return "Non-UTF-8 content differs."
    lines = list(difflib.unified_diff(old, new, fromfile="before/" + name,
                                    tofile="after/" + name, n=3))
    # Ensure changes to missing final newlines remain visible.
    text = "".join(line.rstrip("\r\n") + "\n" + ("" if line.endswith("\n")
                   else "\\ No newline at end of file\n") for line in lines)
    if before.count(b"\r\n") != after.count(b"\r\n") and before.replace(b"\r\n", b"\n") == after.replace(b"\r\n", b"\n"):
        text = "Line endings change between CRLF and LF.\n" + text
    if len(text) > limit:
        return text[:limit] + "\n[Diff truncated at 64 KiB]\n"
    return text or "Byte content differs (for example line endings)."


def blob(root, entry):
    if entry is None:
        return b""
    size = int(git(root, "cat-file", "-s", entry["oid"]))
    if size > 65536:
        return None
    return git(root, "cat-file", "blob", entry["oid"])


def change_kind(old, new):
    return "created" if old is None else "deleted" if new is None else "modified"


def index_changes(root, before, after):
    changes = []
    for name in sorted(before.keys() | after.keys()):
        old, new = before.get(name), after.get(name)
        if old == new:
            continue
        old_data, new_data = blob(root, old), blob(root, new)
        changes.append({"path": name, "kind": change_kind(old, new), "before": old,
                        "after": new, "diff": diff_text(old_data, new_data, name)
                        if old_data is not None and new_data is not None
                        else "Diff omitted: content exceeds 64 KiB. Object IDs were compared."})
    return changes


def working_changes(root, sandbox, before, after, tracked):
    changes = []
    for name in sorted(before.keys() | after.keys()):
        old, new = before.get(name), after.get(name)
        if old == new:
            continue
        old_data = (root / name).read_bytes() if old and old["size"] <= 65536 else b"" if not old else None
        new_data = (sandbox / name).read_bytes() if new and new["size"] <= 65536 else b"" if not new else None
        changes.append({"path": name, "kind": change_kind(old, new),
                        "was_tracked": name in tracked, "before": old, "after": new,
                        "diff": diff_text(old_data, new_data, name)
                        if old_data is not None and new_data is not None
                        else "Diff omitted: content exceeds 64 KiB. Hashes and sizes were compared."})
    return changes


def preview(repo, target="HEAD", mode="mixed", max_copy_mib=256, max_files=20000):
    if mode not in ("soft", "mixed", "hard"):
        raise PreviewError("Supported reset modes: soft, mixed, hard.")
    if max_copy_mib <= 0 or max_files <= 0:
        raise PreviewError("Copy limits must be positive.")
    location = Path(repo).resolve()
    if not location.is_dir():
        raise PreviewError("The repository directory does not exist.")
    if decode(git(location, "rev-parse", "--is-bare-repository")).strip() == "true":
        raise PreviewError("Bare repositories are not supported.")
    root = Path(decode(git(location, "rev-parse", "--show-toplevel")).strip()).resolve()
    settings = config(root)
    if settings.get("core.sparsecheckout", "false").lower() == "true":
        raise PreviewError("Sparse checkouts are not supported.")
    if "extensions.partialclone" in settings or any(
            key.startswith("remote.") and key.endswith(".promisor") and value.lower() == "true"
            for key, value in settings.items()):
        raise PreviewError("Partial clones are not supported; no objects will be fetched.")
    head = resolve(root, "HEAD")
    destination = resolve(root, target)
    index_path = Path(decode(git(root, "rev-parse", "--path-format=absolute", "--git-path", "index")).strip())
    if index_path.with_name(index_path.name + ".lock").exists():
        raise PreviewError("The repository index is locked by another operation. Try again when it finishes.")
    index_bytes = index_path.read_bytes() if index_path.exists() else None
    if any(item and (item.startswith(b"S ") or item[:1].islower())
           for item in git(root, "ls-files", "-v", "-z").split(b"\0")):
        raise PreviewError("Skip-worktree and assume-unchanged index entries are not supported.")
    before_index = index_entries(root)
    target_tree = tree_entries(root, destination)
    for entry in list(before_index.values()) + list(target_tree.values()):
        if entry["mode"] not in ("100644", "100755"):
            raise PreviewError("Submodules, symlinks, and sparse directory entries are not supported.")
    names = set(before_index) | set(target_tree)
    folded = {}
    for name in names:
        # A sandbox may be on a filesystem with different case sensitivity.
        # Refuse ambiguous paths rather than predicting a case-only rename.
        for n in range(1, len(PurePosixPath(name).parts) + 1):
            prefix = "/".join(PurePosixPath(name).parts[:n])
            previous = folded.setdefault(prefix.casefold(), prefix)
            if previous != prefix:
                raise PreviewError("Case-colliding paths or case-only renames are not supported: " + repr(name))
    for name in list(names):
        parts = safe_path(name)
        names.update("/".join((*parts[:n], ".gitattributes")) for n in range(len(parts)))
    attributes = attr_paths(root)
    attribute_bytes = attr_snapshot(attributes)
    validate_attrs(root, names)
    # Soft reset cannot touch the working tree; avoid copying files in this mode.
    before_work = inventory(root, names, max_copy_mib * 1024 * 1024, max_files) if mode != "soft" else {}
    branch_result = git(root, "symbolic-ref", "--short", "-q", "HEAD", check=False)
    branch = decode(branch_result.stdout).strip() if branch_result.returncode == 0 else "detached HEAD"
    removed_count = int(git(root, "rev-list", "--count", destination + ".." + head))
    removed_lines = decode(git(root, "log", "--max-count=12", "--format=%H%x09%s", destination + ".." + head)).splitlines()
    commits = [dict(zip(("oid", "subject"), line.split("\t", 1))) for line in removed_lines]
    object_dir = Path(decode(git(root, "rev-parse", "--path-format=absolute", "--git-path", "objects")).strip()).resolve()
    if "\n" in str(object_dir) or "\r" in str(object_dir):
        raise PreviewError("An object directory containing a newline is not supported.")
    object_format = decode(git(root, "rev-parse", "--show-object-format")).strip()
    if mode == "hard" and target_tree:
        if len(target_tree) > max_files:
            raise PreviewError("Destination tree exceeds --max-files.")
        oids = [entry["oid"] for entry in target_tree.values()]
        sizes = git(root, "cat-file", "--batch-check=%(objectsize)",
                    data=("\n".join(oids) + "\n").encode("ascii")).splitlines()
        try:
            target_bytes = sum(int(size) for size in sizes)
        except ValueError as exc:
            raise PreviewError("A destination object is missing; no objects will be fetched.") from exc
        if target_bytes > max_copy_mib * 1024 * 1024:
            raise PreviewError("Destination tree exceeds the copy limit. Increase --max-copy-mib if appropriate.")

    with tempfile.TemporaryDirectory(prefix="git-impact-") as temporary:
        sandbox = Path(temporary) / "repo"
        sandbox.mkdir()
        empty = Path(temporary) / "empty-template"
        empty.mkdir()
        git(sandbox, "init", "--quiet", "--template=" + str(empty),
            "--object-format=" + object_format, isolated=True)
        git(sandbox, "config", "core.logAllRefUpdates", "false", isolated=True)
        git(sandbox, "config", "gc.auto", "0", isolated=True)
        git(sandbox, "config", "maintenance.auto", "false", isolated=True)
        for key in ("core.autocrlf", "core.eol", "core.filemode", "core.ignorecase",
                    "core.protectntfs", "core.protecthfs", "core.symlinks", "core.checkstat",
                    "core.trustctime"):
            if key in settings:
                git(sandbox, "config", key, settings[key], isolated=True)
        # Source objects are read through alternates. No source hooks, remotes,
        # aliases, filters, credentials, or executable config enter the sandbox.
        (sandbox / ".git/objects/info/alternates").write_bytes(object_dir.as_posix().encode("utf-8") + b"\n")
        (sandbox / ".git/HEAD").write_bytes(head.encode("ascii") + b"\n")
        global_attrs = Path(temporary) / "global-attributes"
        global_attrs.write_bytes(attribute_bytes[0][1] or b"")
        git(sandbox, "config", "core.attributesFile", str(global_attrs), isolated=True)
        if attribute_bytes[-1][1] is not None:
            (sandbox / ".git/info/attributes").write_bytes(attribute_bytes[-1][1])
        # Inspect attributes from the destination tree too: a reset can introduce
        # LFS/encoding attributes that are absent from the current working tree.
        git(sandbox, "read-tree", destination, isolated=True)
        validate_attrs(sandbox, names, isolated=True, cached=True)
        scratch_index = sandbox / ".git/index"
        if index_bytes is None:
            scratch_index.unlink(missing_ok=True)
        else:
            scratch_index.write_bytes(index_bytes)
        for name, metadata in before_work.items():
            source, output = root / name, sandbox / name
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, output)
            output.chmod(metadata["permissions"])
            if file_digest(output) != metadata["sha256"]:
                raise PreviewError("A source file changed while the snapshot was copied. Try again.")
        if mode != "soft":
            # Copied files have fresh inode/ctime/stat data. Without refreshing
            # the *sandbox* cache, Git can unnecessarily re-checkout clean files
            # (e.g. LF files with autocrlf enabled) that it leaves alone in the
            # source. Refresh updates stat data, never staged object IDs.
            refresh = git(sandbox, "update-index", "--refresh", isolated=True, check=False)
            if refresh.returncode not in (0, 1):
                raise PreviewError(decode(refresh.stderr).strip() or "Could not refresh the sandbox stat cache.")
        git(sandbox, "reset", "--" + mode, destination, isolated=True)
        after_index = index_entries(sandbox, isolated=True)
        after_work = inventory(sandbox, names, max_copy_mib * 1024 * 1024, max_files) if mode != "soft" else {}
        result = {"schema_version": 1, "version": __version__, "repository": root.name,
                  "command": "git reset --" + mode + " " + target,
                  "mode": mode, "branch": branch, "head_before": head, "head_after": destination,
                  "commits_removed_from_current_history": removed_count, "commits": commits,
                  "index_changes": index_changes(sandbox, before_index, after_index),
                  "working_tree_changes": working_changes(root, sandbox, before_work, after_work, before_index),
                  "copied_files": len(before_work), "copied_bytes": sum(x["size"] for x in before_work.values()),
                  "notes": ["This previews one observed state. Re-run if files, refs, or the index change.",
                            "Commits removed from this history may remain reachable elsewhere or in the reflog.",
                            "Only paths relevant to this reset are copied; unrelated untracked/ignored files are unchanged."]}
        # Detect concurrent writes, including newly created obstructing files.
        current_index = index_path.read_bytes() if index_path.exists() else None
        current_work = inventory(root, names, max_copy_mib * 1024 * 1024, max_files) if mode != "soft" else {}
        if (current_index != index_bytes or current_work != before_work or
                resolve(root, "HEAD") != head or resolve(root, target) != destination or
                git(root, "symbolic-ref", "--short", "-q", "HEAD", check=False).stdout != branch_result.stdout or
                config(root) != settings or attr_snapshot(attributes) != attribute_bytes):
            raise PreviewError("The repository changed during the preview. No result was published; try again.")
        return result
