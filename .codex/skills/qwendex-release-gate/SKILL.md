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

Read `.qwendex-dev/results/meta/release_validation_summary.json` before making
any release recommendation.
