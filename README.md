# Qwendex

Qwendex is a Codex-native operator harness for GPT-first work with bounded local
Qwen support. Codex remains the execution plane; local Qwen is available through
guarded seats, receipts, routing checks, and offline/live validation.

## Quick Start

```bash
scripts/qwendex_install_deps --install
scripts/qwendex check --json
scripts/qwendex doctor --json
scripts/qwendex route --seat auto --task-class exec --prefer-local --json
scripts/qwendex eval --all --json
```

Public docs live under [`public/qwendex`](public/qwendex/README.md):

- [Architecture](public/qwendex/architecture.md)
- [LLMStack](public/qwendex/llmstack.md)
- [Configuration](public/qwendex/configuration.md)
- [Operations](public/qwendex/operations.md)
- [Manager Mode](public/qwendex/manager-mode.md)
- [Security](public/qwendex/security.md)
- [Verification](public/qwendex/verification.md)
- [Release Notes](public/qwendex/release-notes.md)

## Release Candidate

This checkout is seeded as `v0.1.0-rc.2`. The latest max-depth validation
summary remains stored at
[`docs/validation/v0.1.0-rc.1-validation_summary.json`](docs/validation/v0.1.0-rc.1-validation_summary.json)
until the next release validation run is captured.
