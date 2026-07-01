# Staging Receipt

## Scope

Qwendex 0.1.0-rc.1 stages a public boundary over the existing Codex/local-Qwen
harness.

## Added

- `scripts/qwendex`
- `scripts/qwendex_cli.py`
- `config/qwendex/`
- `public/qwendex/`
- `tests/smoke/test_qwendex_cli.py`

## Acceptance Checklist

- `scripts/qwendex check --json`
- `scripts/qwendex doctor --json`
- `scripts/qwendex exec "Reply exactly QWENDEX_OK" --json`
- `scripts/qwendex seat qwen --json`
- `scripts/qwendex eval --json`
- `scripts/qwendex task status --json`
- `scripts/qwendex manager status --json`
- `scripts/qwendex queue status --json`
- `scripts/qwendex learn dry-run --backend mock --json`
- `scripts/qwendex manager mode --set manager --json`
- Public docs naming, link, and secret scans

## Review Status

Staged for GPT/Codex review. Qwen receipts are advisory until reviewed.
