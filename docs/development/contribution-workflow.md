# Qwendex Contribution Workflow

Work in `~/qwendex-dev`, the dedicated git worktree.

## Normal Loop

```bash
source ~/qwendex-dev/.qwendex-dev/env.sh
qwendex-dev doctor
qwendex-dev verify --tier quick
qwendex-dev review
qwendex-dev stage
```

## Rules

- Stage from the dev worktree, not from generated runtime folders.
- Keep `.qwendex-dev/` ignored and out of public artifacts.
- Add or update tests for changed public behavior.
- Use local Qwen for bounded drafting and inspection only.
- Use GPT/Codex for release, security, architecture, and public-claim review.
- Capture snapshots after significant verification runs.

## Fallback Copy Commands

The old rsync flow is available only for repair:

```bash
qwendex-dev repair-copy --force
qwendex-dev export-to-source --dry-run
```

Do not use those commands as the normal development flow.
