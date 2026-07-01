# Seat Handoff

Qwendex seats define who can do what.

| Seat | Use | Release authority |
| --- | --- | --- |
| `primary` | General GPT/Codex work | Yes |
| `qwen` | Bounded local Qwen work | No |
| `audit` | Read-only review and security review | Yes |
| `release` | Public release acceptance | Yes |
| `sandbox` | Isolated local probes | No |

Qwen is allowed for read-heavy audits, docs drafts, queue work, bounded patches,
smoke probes, and artifact summaries. GPT/Codex review is required for release
acceptance, architecture, security, public docs claims, and protocol changes.

Auto routing is available for cost control:

```bash
scripts/qwendex route --task-class exec --json
scripts/qwendex exec "Reply exactly QWENDEX_OK" --seat auto --json
```

The auto route chooses `qwen` only when the configured local model is visible
through the guarded Codex-facing endpoint. It falls back to `primary` when local
Qwen is unavailable or when the task class requires GPT/Codex authority.

Run:

```bash
scripts/qwendex seat qwen --json
```

Then inspect the receipt:

```bash
scripts/qwendex receipt latest --json
```
