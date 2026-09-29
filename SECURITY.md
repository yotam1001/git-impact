# Security

The current release is 0.1.x. Security fixes will be provided in the latest release.

Report vulnerabilities privately through [GitHub's vulnerability reporting form](https://github.com/yotam1001/git-impact/security/advisories/new). Include the Git Impact version, Git version, operating system, and a small synthetic reproduction. Do not attach private repositories, credentials, or reports containing sensitive file contents.

Git Impact is designed to inspect the source repository and run resets only in a disposable sandbox. A source write, execution of repository-controlled hooks or filters, or unsafe rendering of repository-controlled text is a security concern. Unsupported repository states should fail explicitly.

HTML and JSON reports can contain filenames and source contents. Store them outside the inspected repository, and review them before sharing. The preview is an observation of current state, so re-run it after editing or changing repository state.
