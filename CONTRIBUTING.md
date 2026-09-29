# Contributing

Start with a small disposable repository that reproduces the missing or incorrect preview. Include the Git version, operating system, reset mode, expected affected paths, and actual affected paths. Use synthetic content.

Run `python -m unittest discover -s tests -v`. Changes to preview semantics should include a test comparing the report with Git's behavior in a disposable fixture, plus a check that preview leaves source files and Git metadata unchanged.

Keep the read-only boundary: source commands only inspect, mutations only run in a temporary sandbox, custom executable configuration is not inherited, and unsupported cases produce an error instead of a reassuring approximation. Keep reports self-contained and escape repository-controlled content in HTML and terminal output.

The first release is deliberately scoped to reset modes. Propose additional commands before expanding the parser.
