# Manager Modes

Qwendex behaves like normal Codex by default. Manager orchestration is additive:
the main session keeps the user's selected model and reasoning, while Qwendex
routes only specific subagent lanes to local Qwen, GPT-5.5 low/medium, or
high/xhigh reasoning when the lane actually needs it.

Public modes are ordered:

```text
Auto -> Lite -> Medium -> Heavy -> Manager Mode
```

`Ctrl+Shift+M` cycles that order in the host terminal or UI:

```bash
scripts/qwendex manager mode --cycle --json
```

Visible indicators:

```text
(Ctrl+Shift+M) Subagent Management: [ Auto ]
(Ctrl+Shift+L) Local: [Y]
```

`Ctrl+Shift+L` toggles whether local subagents may be used:

```bash
scripts/qwendex manager local --toggle --json
scripts/qwendex manager local --set off --json
```

When Local is `[N]`, Qwendex skips local Qwen even if the endpoint is healthy.

## Mode Meaning

- `Auto`: deterministic checks plus the bounded estimator pick a mode.
- `Lite`: target 10-20% subagent offload.
- `Medium`: target 25-45% subagent offload.
- `Heavy`: target 50-75% subagent offload.
- `Manager Mode`: target 85-95% offload; the main session coordinates,
  reviews, and validates.

Legacy compatibility remains:

```bash
scripts/qwendex manager --mode manager_only --json
```

The legacy spelling maps to `Manager Mode`.

## Auto Estimator

Auto uses deterministic signals first. If the task is ambiguous, it calls the
`qwendex-auto-manager-estimator` skill with a small prompt. The estimator
defaults to GPT-5.5 medium and returns bounded JSON: complexity, risk, likely
file scope, validation depth, subagent usefulness, recommended mode, confidence,
and any lane that needs high/xhigh reasoning.

High/xhigh is reserved for specific architecture, security, release, protocol,
credential, or migration lanes. The main session is never escalated by Auto.

## Context Packets

Every assigned lane records a context packet:

- objective
- task class
- allowed scope
- exact files or directories
- needed docs
- stop condition
- expected artifact
- receipt path
- context budget
- model/reasoning assignment
- review requirement

Subagent output is advisory until reviewed and backed by artifacts or tests.

## Lifecycle

Default manager settings:

- `max_subagents`: mode-specific, 2 to 8.
- `stale_after_minutes`: mode-specific, 15 to 45.
- Close completed agents after findings are integrated.
- Close idle read-only agents after the stale window.
- Do not close an active writer until its changes are integrated or stopped.

Durable lifecycle commands:

```bash
scripts/qwendex manager assign --agent-id reviewer-1 --lane review --task-id task_... --owner Rawls --write-surface read-only --stop-condition "return findings" --json
scripts/qwendex manager heartbeat --agent-id reviewer-1 --json
scripts/qwendex manager status --json
scripts/qwendex manager close-stale --stale-after-minutes 30 --json
```

The CLI records `agent_id`, lane, task, owner, write surface, stop condition,
artifacts, context packet, heartbeat time, validation status, stop reason, and
close receipt metadata in the local Qwendex state DB. It does not forcibly
interrupt an external process.

## High-Value Add

`check`, `doctor`, `manager status`, and `eval` include one or two compact
high-value-add lines, such as:

```text
High-value add: run qwendex eval --live --json before release; local Qwen is available.
High-value add: escalate only the security-review lane to high; main session can stay user-selected.
```

## Pattern Sources

Qwendex keeps Codex CLI subagents and project roles as the runtime base. It
borrows patterns from LangGraph persistence/memory, AutoGen teams/termination,
Anthropic effective agents/contextual retrieval, SWE-agent trajectories,
OpenHands-style eval harnesses, SWE-bench Verified and tau-bench methodology,
MCP security guidance, and Berkeley function-calling/tool-call eval ideas.
