# Validation Evidence

The historical Manager-production evidence for `0.6.0-rc.1` is:

- [`0.6.0-rc.1-manager-production-summary.json`](0.6.0-rc.1-manager-production-summary.json), the machine-readable public summary and accepted receipt digests.
- [`0.6.0-rc.1-manager-production-summary.md`](0.6.0-rc.1-manager-production-summary.md), the human-readable result and claim ceiling.

The historical prerelease is bound by the annotated `v0.6.0-rc.1` tag: its
`^{commit}` and `^{tree}` expressions resolve to the exact final source in the
sealed per-run release receipt. These tracked files contain sanitized results
only. Raw live output, command logs, local databases, generated homes, and
complete acceptance receipts remain under the ignored development results root.
