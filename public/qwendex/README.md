# Qwendex

Qwendex is the public Codex-native harness for GPT-first operation with bounded
local Qwen support. It exposes one operator surface:

```bash
scripts/qwendex check
scripts/qwendex up
scripts/qwendex route
scripts/qwendex exec "Reply exactly QWENDEX_OK"
scripts/qwendex seat qwen
scripts/qwendex task create --title "..."
scripts/qwendex context snapshot --task-id task_...
scripts/qwendex learn dry-run
scripts/qwendex manager mode --cycle
scripts/qwendex manager local --toggle
scripts/qwendex codex-patch preflight
scripts/qwendex codex-patch apply --source /path/to/codex
scripts/qwendex_dev_env sync
./llmstack status
scripts/qwendex eval
```

Codex remains the execution plane. Token-saver routing can prefer local Qwen
when the Codex-facing bridge is healthy, but local Qwen can operate only through
Qwendex seats, guard profiles, bounded tools, eval receipts, and GPT review
gates.

## Start Here

- [Quickstart](quickstart.md)
- [Architecture](architecture.md)
- [LLMStack](llmstack.md)
- [Configuration](configuration.md)
- [Operations](operations.md)
- [Seat Handoff](seat-handoff.md)
- [Learning Loop](learning-loop.md)
- [Agent Management](agent-management.md)
- [Manager Mode](manager-mode.md)
- [Codex TUI Patching](codex-patching.md)
- [Dev Environment](dev-environment.md)
- [Test Bench](testbench.md)
- [Tool Server](tool-server.md)
- [Security](security.md)
- [Verification](verification.md)
- [Troubleshooting](troubleshooting.md)
- [Release Notes](release-notes.md)
- [Staging Receipt](staging-receipt.md)

## Compatibility

| Existing command | Qwendex command |
| --- | --- |
| `scripts/llm doctor --json` | `scripts/qwendex doctor --json` |
| `./llmstack status --json` | `scripts/qwendex llmstack check --json` |
| `scripts/llm start --json` | `scripts/qwendex up --json` |
| `scripts/llm stop --json` | `scripts/qwendex down --json` |
| `scripts/llm harness-eval --json` | `scripts/qwendex eval --json` |
| `scripts/llm skillopt dry-run --json` | `scripts/qwendex learn dry-run --json` |
| `scripts/run_local_qwen_codex.sh --check` | `scripts/qwendex check --json` |

Qwendex keeps those internal commands available as compatibility delegates.
