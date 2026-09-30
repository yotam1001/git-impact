# Changelog

## 0.2.0 — 2026-09-30

- Distinguish affected staged/unstaged work and local deletions from target version changes; count affected paths once across staging and disk.
- Add `--compare-modes` with actual soft/mixed/hard simulations for one target and checks for detected changes between snapshots.
- Show an untracked export obstruction in the demo alongside native Git command output and an unrelated surviving note.
- Explain when native Git diffs are sufficient, what they omit, and which reset modes preserve staging or disk files.
- Keep local-work detection aware of Git line-ending normalization and file-mode settings.

## 0.1.0 — 2026-09-30

- Preview reset in soft, mixed, and hard modes to local refs.
- Separate effects on the staging area, working tree, and current history.
- Explain untracked files or directories that obstruct a hard reset.
- Produce terminal summaries, optional diffs, JSON, and standalone HTML reports.
- Include a synthetic demo that never uses the user's repository.
- Refuse unsupported states and detected concurrent edits.
- Provide a Windows native build, a portable Python zipapp, checksummed archives, and CI definitions for Windows, Linux, and macOS.
