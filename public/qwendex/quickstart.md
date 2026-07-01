# Quickstart

Run the offline surface check first:

```bash
scripts/qwendex check --json
```

Start the stack when you want live local Qwen runs:

```bash
scripts/qwendex up --json
```

Run the exact marker probe:

```bash
scripts/qwendex exec "Reply exactly QWENDEX_OK" --json
```

Check the token-saver route before a live task:

```bash
scripts/qwendex route --task-class exec --json
```

Select the local Qwen seat for a bounded task:

```bash
scripts/qwendex seat qwen --json
```

Run the offline eval gate:

```bash
scripts/qwendex eval --json
```

Stage learning proposals without live mutation:

```bash
scripts/qwendex learn dry-run --backend mock --json
```

Inspect the latest receipt:

```bash
scripts/qwendex receipt latest --json
```

## Example Workflows

Local Qwen coding run:

```bash
scripts/qwendex route --task-class exec --json
scripts/qwendex seat qwen --json
scripts/qwendex exec "Inspect scripts/qwendex_cli.py and suggest one bounded fix." --json
```

Read-only audit:

```bash
scripts/qwendex seat audit --json
scripts/qwendex eval --case review_current_changes --json
```

Queue workflow:

```bash
scripts/qwendex eval --case mcp_queue_workflow --json
```

Learning dry run:

```bash
scripts/qwendex learn dry-run --backend mock --json
```

Eval receipt:

```bash
scripts/qwendex eval --case exact_marker --json
scripts/qwendex receipt latest --json
```

Qwen output reviewed by GPT:

```bash
scripts/qwendex seat qwen --json
scripts/qwendex receipt latest --json
```

Then ask the GPT/Codex release seat to review the receipt before accepting the
Qwen output. `seat release` selects the authority lane; it does not review a
prior receipt by itself.

Manager mode:

```bash
scripts/qwendex manager --mode manager_only --json
```

Bind `Ctrl+Shift+M` in your host terminal or UI to that command if you want a
shortcut for full-time orchestration.
