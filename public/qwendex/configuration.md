# Configuration

Qwendex config lives in `config/qwendex/`.

- `qwendex.schema.json`: stable schema
- `qwendex.json`: repo-local default
- `profiles.json`: seat registry
- `model-catalog.json`: Qwen model metadata and guard markers
- `qwendex.sample.json`: copy-safe sample with no credentials

Precedence is:

1. CLI flags
2. Environment variables
3. Project config
4. Safe user config
5. Built-in defaults

Supported environment overrides:

```bash
QWENDEX_DEFAULT_SEAT=qwen
QWENDEX_RESULTS_ROOT=results/qwendex
QWENDEX_STATE_DB=~/.local/state/qwendex/qwendex.sqlite
QWENDEX_GUARD_PROFILE=max_safety
QWENDEX_LEARNING_MODE=stage_only
QWENDEX_ORCHESTRATION_MODE=auto
QWENDEX_MANAGER_MODE=heavy
QWENDEX_LOCAL_SUBAGENTS=on
QWENDEX_ESTIMATOR_MODEL=gpt-5.5
QWENDEX_ESTIMATOR_REASONING=medium
QWENDEX_ROUTING_MODE=token_saver
QWENDEX_PREFER_LOCAL_QWEN=1
QWENDEX_LOCAL_QWEN_PROBE_URL=http://127.0.0.1:1234/v1/models
QWENDEX_LOCAL_QWEN_MODEL=qwen-local
QWENDEX_FALLBACK_SEAT=primary
```

Do not place credentials in Qwendex config. Use the existing provider-specific
environment handling outside the public Qwendex config surface.

## LLMStack Config

LLMStack config lives in `config/local_llm_stack/`.

- `stack_manager.json`: public copy-safe default
- `stack_manager.sample.json`: same default for reset/comparison
- `profiles.example.json`: backend snippets to copy into local config
- `stack_manager.local.json`: ignored machine-local override
- `local_harness.env.sample`: environment override template

The stack loader prefers `stack_manager.local.json` when present. Use
`QWENDEX_LLMSTACK_CONFIG` to point at another file.

## State

`state.db` stores Qwendex task, manager-session, context snapshot, handoff,
evidence, and receipt-link tables. It is local operator state and should not
contain credentials.

Use a temporary DB for isolated probes:

```bash
QWENDEX_STATE_DB=/tmp/qwendex.sqlite scripts/qwendex task status --json
```

## Context Reminders

`context.reminder_tool_call_threshold`, `context.reminder_repeat_interval`, and
`context.phase_boundary_labels` tune advisory compaction reminders. Qwendex does
not compact a live Codex session itself; it tells the operator whether to keep
going, create a snapshot first, or build a compact plan.

```bash
scripts/qwendex context reminder --task-id task_... --tool-calls 55 --phase after-milestone --json
```

## Routing

`routing` controls cost-aware seat selection:

- `mode`: `token_saver`, `manual`, or `primary_only`
- `prefer_local_qwen_when_available`: default `true`
- `local_probe_url`: Codex-facing `/v1/models` endpoint
- `local_model`: default `qwen-local`
- `fallback_seat`: default `primary`
- `prefer_for_task_classes`: bounded work that may use local Qwen
- `primary_required_for_task_classes`: work that stays on GPT/Codex authority

Inspect the current decision with:

```bash
scripts/qwendex route --task-class exec --json
```

`scripts/qwendex exec` defaults to `--seat auto`. Auto routing chooses `qwen`
only when the local model alias is visible through the guarded Codex-facing
endpoint; otherwise it falls back to the configured primary seat.

## Orchestration

`orchestration` controls manager defaults:

- `mode`: `auto`, `lite`, `medium`, `heavy`, or `manager`
- `manager_deploy_policy`: `auto` by default; Manager Mode requires active
  registered agent lanes unless this is set to `disabled`
- `shortcut`: declared as `Alt+M`
- `shortcut_command`: `scripts/qwendex manager mode --toggle --json`
- `kaveman`: `Alt+K` toggle, persisted state, and terse-output directive
- `local_subagents`: `Alt+L` toggle and default Local state
- `mode_order`: `auto`, `lite`, `medium`, `heavy`, `manager`
- `mode_profiles`: label, offload target, and max subagents per mode; Manager
  Mode defaults to `max_subagents: 10`
- `estimator`: model, reasoning, skill, and token caps for Auto
- `local_qwen_eligibility`: task classes and max risk for local lanes
- `escalation_thresholds`: terms that move lanes to high or xhigh
- `stale_session_thresholds_minutes`: cleanup windows per mode
- `max_subagents`: default `4`; the Qwendex product ceiling is `10`
- `stale_after_minutes`: default `30`
- `close_stale_policy`: close completed agents after integration and idle
  read-only agents after the stale window

The CLI exposes these settings with:

```bash
scripts/qwendex manager --json
scripts/qwendex manager mode --set auto --json
scripts/qwendex manager kaveman --toggle --json
scripts/qwendex manager local --toggle --json
scripts/qwendex manager estimate --prompt "..." --json
```

Durable manager lifecycle commands write to `state.db`:

```bash
scripts/qwendex manager assign --agent-id reviewer-1 --lane review --task-id task_... --json
scripts/qwendex manager heartbeat --agent-id reviewer-1 --json
scripts/qwendex manager close-stale --stale-after-minutes 30 --json
```

`manager_only` remains a compatibility alias for `manager`.

`manager_deploy_policy` defaults to `auto`: when the selected mode is Manager
Mode, Qwendex requires at least one active registered agent lane and reports a
blocked manager status if no lane is active. Set `manager_deploy_policy` to
`disabled` to opt out of that requirement; explicit manual manager lifecycle
commands remain operator-directed.
