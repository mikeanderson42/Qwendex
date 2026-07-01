# Qwendex Release Runbook

Use this before claiming a Qwendex build is release-ready.

## Required Commands

```bash
qwendex-dev bootstrap
qwendex-dev doctor
qwendex-dev verify --tier quick
qwendex-dev verify --tier full
scripts/run_local_qwen_codex.sh --check
```

When the live stack is intentionally running:

```bash
qwendex-dev verify --tier live
```

Before publishing:

```bash
qwendex-dev verify --tier release
qwendex-dev snapshot
```

## Acceptance

- Static gates pass.
- Focused smoke tests pass.
- Qwendex check and doctor pass.
- Offline Qwendex evals pass.
- Harness gate and harness eval pass.
- Dev state and ledgers are under `.qwendex-dev/`.
- Codex patch preflight supports the installed Codex version.
- No private state, logs, model weights, credentials, or generated runtime
  artifacts are staged.

## Release Summary

The release gate writes:

```text
.qwendex-dev/results/meta/release_validation_summary.json
```

Use that file as the source of truth for release recommendation.
