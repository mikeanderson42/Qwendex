---
name: qwendex-release-gate
description: Run Qwendex release verification and produce/interpret release readiness receipts.
---

# Qwendex Release Gate

Use this skill before claiming Qwendex is release-ready.

## Required Gates

```bash
qwendex-dev bootstrap
qwendex-dev doctor
qwendex-dev verify --tier full
qwendex-dev verify --tier release
qwendex-dev snapshot
```

Run `qwendex-dev verify --tier live` when the local stack is intentionally
running.

## Blockers

- static/lint/test failure
- invalid JSON config
- invalid receipt schema or digest
- local-off route selecting Qwen
- visible local-model tool markup
- local-model loop/truncation markers in release outputs
- dev state or ledgers outside `.qwendex-dev/`
- unsupported Codex version when patched TUI is claimed
- missing, stale, mutable, or failed per-run release receipts
- missing/mismatched remote CI attestation or receipt source binding
- failed online trusted-remote/CI-run/artifact verification
- dirty/non-default source, untrusted origin, version drift, or a non-annotated
  tag not pointing at HEAD
- tracked runtime/private material in the tagged source artifact

Read the isolated run's `release_validation_summary.json` before making any
release recommendation. The fixed
`.qwendex-dev/results/meta/release_validation_summary.json` path is a latest-copy
convenience only. Require schema `qwendex.dev.release_summary.v2`,
`recommendation: publish-ready`, `publish_ready: true`, clean default-branch and
trusted-origin/annotated-tag binding, a matching default-branch CI attestation,
passing run/command/source-bound gates, a passing full-tree artifact contract,
an unchanged source recheck, a valid whole-summary receipt digest, and no
blockers before publication. Run `python3 scripts/qwendex_release_gate.py
verify-summary --summary <path> --require-publish-ready` immediately before the
publish mutation.
