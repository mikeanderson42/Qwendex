# Configuration

Qwendex config lives in `config/qwendex/`.

- `qwendex.schema.json`: stable schema
- `qwendex.json`: repo-local default
- `profiles.json`: non-authoritative seat reference metadata
- `model-catalog.json`: non-authoritative model/guard reference metadata
- `qwendex.sample.json`: copy-safe sample with no credentials

Runtime policy comes from `qwendex.json` plus the documented override layers.
The two smaller catalog files are reference material only; editing them does
not change routing or launcher behavior.

Validate the two published configs against `qwendex.schema.json` with:

```bash
python3 scripts/validate_qwendex_config.py
```

The validator uses the exactly pinned `jsonschema` Draft 2020-12 implementation
and also requires matching schema identifiers, SemVer product versions, and
version parity between the default, sample, and current release heading.

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
QWENDEX_PERFORMANCE_CAPTURE=metadata
QWENDEX_PERFORMANCE_DB=~/.local/state/qwendex/qwendex-performance.sqlite
QWENDEX_LEARNING_MODE=stage_only
QWENDEX_ORCHESTRATION_MODE=auto
QWENDEX_MANAGER_MODE=heavy
QWENDEX_LOCAL_SUBAGENTS=on
QWENDEX_ROUTING_MODE=token_saver
QWENDEX_PREFER_LOCAL_QWEN=1
QWENDEX_LOCAL_QWEN_PROBE_URL=http://127.0.0.1:1234/v1/models
QWENDEX_LOCAL_QWEN_MODEL=qwen-local
QWENDEX_FALLBACK_SEAT=primary
QWENDEX_QDEX_PERMISSION_MODE=workspace-write
```

Do not place credentials in Qwendex config. Use the existing provider-specific
environment handling outside the public Qwendex config surface.

## Qdex Launch Permission

Published `qwendex.json` and `qwendex.sample.json` both set:

```json
{
  "qdex": {
    "permission_mode": "workspace-write"
  }
}
```

Qdex resolves this one launch setting in a deliberately narrow order:

1. `--qdex-permission-mode workspace-write|yolo`
2. `QWENDEX_QDEX_PERMISSION_MODE`
3. `${XDG_CONFIG_HOME:-$HOME/.config}/qwendex/qdex.json`
4. published `qdex.permission_mode`
5. hard fallback `workspace-write`

The operator-local file is intentionally ignored and is not an input to
runtime-generation or release artifacts. For example, a local Yolo opt-in is
only `{ "permission_mode": "yolo" }`. Invalid explicit CLI, environment, or
operator-local values fail before Qdex invokes Codex. `yolo` appends
`--dangerously-bypass-approvals-and-sandbox` exactly once; `workspace-write`
does not append it. Preflight, dry-run, status, and Manager receipt JSON expose
`qdex_permission_mode` and `qdex_permission_source`; an active Manager session
uses its snapshotted values until it is relaunched.

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

Manager state schema version 2 uses transactional migration, a pre-migration
backup, WAL, and a bounded busy timeout. Each Manager decision and worker row
records its runtime and hook generation; historical acceptance is visible but
cannot satisfy a current source-bound gate.

`QWENDEX_RUNTIME_ROOT` contains side-by-side immutable generations plus the
atomically replaced `current.json` selector. `QWENDEX_RUNTIME_GENERATION_ID`,
`QWENDEX_HOOK_GENERATION`, and the runtime contract digest are process-pinned
outputs, not supported knobs for rewriting a live session. Mutable child
reports use `QWENDEX_AGENT_ARTIFACT_ROOT`, which Qdex fixes to the writable
operator-local `.qwendex` root outside the sealed source tree.

## Exploration Performance Telemetry

The optional `performance` object controls a separate local telemetry database;
it is not part of `state.db` or the Manager ledger:

```json
{
  "performance": {
    "capture": "off",
    "retention_days": 14,
    "max_events": 50000,
    "query_fingerprints": true
  }
}
```

`capture` is either `off` or `metadata`, and defaults to `off`. Metadata mode
never enables a raw-content mode or a network exporter. It retains only safe
event classes, timing/count fields, repository-scope digests, and locally
HMACed correlation/query fingerprints; it does not retain prompts, commands,
paths, tool input/output, stdout, stderr, or transcripts.

`retention_days` and `max_events` bound local storage. Retention runs during an
aggregate summary, never on every hook event.
Older v1 config documents may omit `performance`; the built-in safe defaults
continue to apply.

`QWENDEX_PERFORMANCE_CAPTURE=metadata` is the scoped runtime opt-in for a
Qdex or evaluation launch. `QWENDEX_PERFORMANCE_DB` overrides only the local
database path; it does not enable capture. The development environment resolves
this database under its isolated `.qwendex-dev/state/` directory; normal
installs default to `~/.local/state/qwendex/qwendex-performance.sqlite`.

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
`exec` infers its task class from the prompt before routing; security,
architecture/protocol, release, and public-doc-claim prompts cannot be forced
onto the local Qwen seat. Use the validated `--task-class` option when the
prompt alone does not identify the lane.

Local routing separates intent from availability. `QWENDEX_LOCAL_SUBAGENTS=on`
or `Local: [Ready]` means Qwendex may consider local subagent lanes and the
probe has confirmed `local_model`. It still probes `local_probe_url` before
choosing the `qwen` seat. `Local: [Off]` or `QWENDEX_LOCAL_SUBAGENTS=off` is
operator intent to skip local lanes even when the endpoint is healthy. If local
intent is on but the probe cannot confirm the alias, the state is
`Local: [Unavailable]` and Qwendex falls back to `fallback_seat`.

`fallback_seat` must be a GPT/Codex authority seat (`primary`, `audit`, or
`release`). `--prefer-local` and an explicit `--seat qwen` do not override
`primary_required_for_task_classes`, and Local-Off also prevents explicit local
seat routing. These are release-blocking routing invariants, not cost hints.

The global `sandbox.mode` applies to `exec` commands. The `audit` and isolated
`sandbox` seats always use `read-only`, ignore user MCP/config surfaces, and do
not inject the local artifact-queue MCP. Local-Qwen minimal exec also clears MCP
configuration and passes the selected `read-only` or `workspace-write` mode
through its launcher. Results from either local seat (`qwen` or `sandbox`)
require GPT/Codex review.
Seat guard profiles, wall/tool budgets, context/compaction limits, and tool
output limits are carried into the child command/environment and recorded in
the receipt execution policy. `max_output_tokens` is recorded as a declared,
not-enforced value until the launcher/runtime consumes it; the receipt's
`enforcement` map names this boundary.
The published local seats and 32k backend profile use a `32768` context window
and a `28672` auto-compact limit. Every seat must keep its effective compact
limit below its context window; config validation rejects an inverted budget.

`local_probe_url` must end in `/v1/models` without a query or fragment. Qwendex
derives the launcher base by removing that suffix and passes both the derived
`LOCAL_QWEN_BASE` and configured `LOCAL_QWEN_MODEL` to the child, so the
endpoint/model that passed routing is the one execution uses. The launcher
normalizes that base and exports `CODEX_OSS_BASE_URL=<base>/v1` for Codex. A
conflicting inherited `CODEX_OSS_BASE_URL` blocks before execution instead of
silently sending the run to an endpoint different from the one that passed
preflight.

Run `qdex` from the desired directory or use Codex's native
`qdex -C <project>` form. The selected directory becomes the manager target,
execution working directory, Codex add-dir, local-harness trusted root, and MCP
trusted root. `qdex` always sets the generated isolated `CODEX_HOME` for its
child while the caller's environment and ordinary upstream `codex` remain
unchanged. By default, `qdex` uses the resolved `workspace-write` permission
posture. Only an explicit CLI, environment, or operator-local selection of
`yolo` adds
`--dangerously-bypass-approvals-and-sandbox`; the repo binding is a
Qwendex/MCP routing boundary, not OS-level filesystem confinement. Without an
explicit native directory option, Qdex inherits `$PWD` even outside git and
does not synthesize `-C`. Native Codex `-C`/`--cd` selects both the Codex
working directory and Qwendex manager scope and is forwarded unchanged. The
older Qdex-only `--repo` option remains a compatibility alias. Other native
arguments are forwarded unchanged. Help and version calls
do not create Manager decisions or rewrite status state. Qdex passes the same
canonical target as a per-launch Codex trusted-project override so an
automation primer cannot be consumed by directory onboarding.
For direct `exec --cwd`, the default artifact-queue MCP trusted write root is
only that execution directory; adding any other root requires an explicit
`QWENDEX_MCP_TRUSTED_ROOTS` override. Qwendex source is not included in those
repo-bound MCP roots merely because its launcher is providing the command.

The exact `QWENDEX_OK` probe executes normally by default. `--synthetic` is an
explicit offline-only shortcut for that exact marker; its receipt says
`synthetic_not_evidence` and cannot establish model, sandbox, tool, or endpoint
availability. `scripts/qwendex seat <name>` likewise confirms only that a seat
is configured; it does not probe availability.

## Orchestration

`orchestration` controls manager defaults:

- `mode`: `off`, `auto`, `lite`, `medium`, `heavy`, or `manager`
- `manager_deploy_policy`: `auto` by default; enables advisory lane planning
  and observability, while `disabled` turns off deployment recommendations
- `kaveman`: persisted enabled state and the enforced terse-output directive
- `local_subagents`: default Local enabled state
- `mode_profiles`: status label and native worker capacity per mode:
  Off 0, Auto 4, Lite 1, Medium 2, Heavy 3, and Manager 4; configured capacity
  is bounded by the conservative hard ceiling of 8
- `local_qwen_eligibility`: deterministic classifier classes and max risk for
  Local lanes; the shipped allowlist covers repository mapping, read-heavy
  investigation, single-file reads, small edits, and test/regression lanes,
  while cross-cutting, security/protocol, release, and live-acceptance classes
  stay denied
- `escalation_thresholds`: terms that move lanes to high or xhigh
- `stale_session_thresholds_minutes`: cleanup windows per mode

The canonical cycle order and patched-TUI hotkeys are code/keymap contracts,
not mutable Qwendex configuration. `Alt+M`, `Alt+K`, and `Alt+L` may be rebound
through the Codex TUI keymap. `manager estimate` is a deterministic CLI
heuristic; it does not invoke a model or skill and has no model-budget config.
The selected mode profile's `max_subagents` also supplies
`AgentPolicy.max_threads`, so status, ledger capacity, and backend policy share
one limit. Codex V2 counts the root thread separately, so Qdex supplies a
native per-session ceiling of `max_subagents + 1`.

The selected delegation policy is sealed into each non-Off Qdex launch. The
snapshot covers native capacity, depth and wait limits, mode guidance, Kaveman,
and Local routing. Current global state is retained separately as
`desired_global_policy_hash`; changing it affects the next launch and can be
reported as drift without blocking the current session. Lifecycle planning
continues with the recorded launch availability and local-routing snapshot.

`QWENDEX_MANAGER_MODE` and `QWENDEX_ORCHESTRATION_MODE` override the configured
default mode for a fresh state DB. Once `scripts/qwendex manager mode ...` or
`Alt+M` persists a selected mode, that local state is the active mode source
until it is changed again.

Agent-use selectors are session-level runtime policy inputs. When no explicit
selector is set, the selected Agent Manager mode is the backend `AgentPolicy`
source:

```bash
scripts/qwendex manager mode --set manager --json
scripts/qwendex --agent-use Manager agent policy --json
QWENDEX_AGENT_USE=Heavy scripts/qwendex agent status --json
CODEX_AGENT_USE=Lite scripts/qwendex check --json
```

Precedence is CLI `--agent-use`, then `QWENDEX_AGENT_USE`, then
`CODEX_AGENT_USE`, then the selected Agent Manager mode. If no mode has been
persisted, `orchestration.mode` is the default. Invalid explicit values fall
back to `Medium` with a warning unless `QWENDEX_AGENT_USE_STRICT=1` is set.

The CLI exposes these settings with:

```bash
scripts/qwendex manager --json
scripts/qwendex agent policy --json
scripts/qwendex agent status --json
scripts/qwendex manager mode --set auto --json
scripts/qwendex manager kaveman --toggle --json
scripts/qwendex manager local --toggle --json
scripts/qwendex manager estimate --prompt "..." --json
```

Durable manager lifecycle commands write to `state.db`:

```bash
scripts/qwendex manager assign --agent-id reviewer-1 --lane review --task-id task_... --json
scripts/qwendex manager heartbeat --agent-id reviewer-1 --json
scripts/qwendex manager close --agent-id reviewer-1 --reason integrated --json
scripts/qwendex manager close-stale --stale-after-minutes 30 --json
```

`manager_only` remains a compatibility alias for `manager`.

`manager_deploy_policy` defaults to `auto`. It enables advisory lane planning
and lifecycle visibility; direct work remains valid, and missing, unresolved,
or stale lanes do not block prompts, tools, publication, final responses, or
health checks. Set it to `disabled` to turn off deployment recommendations;
explicit manual lifecycle commands remain operator-directed.

`manager repair --safe` is the public manager-state reconciliation path. The
safe boundary is to reconcile read-only stale state and harmless empty stale
writer lanes while keeping non-empty writer lanes open for operator review.
Those remaining rows are advisory warnings in both daily and strict health.
