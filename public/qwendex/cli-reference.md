# CLI Reference

Qwendex has four named surfaces. `qwendex` is the stable operator control
plane, `qdex` launches an isolated Codex session, `llmstack` manages an optional
local-model stack, and `qwendex-dev` provides source-install, build,
verification, and release tooling. Run `scripts/qwendex about --json` for the
machine-readable configured product and compatibility declaration. It is not a
runtime probe; build and release receipts prove the artifacts actually in use.

## Output Contract

Commands that accept `--json` return the `qwendex.cli.v1` envelope:

```json
{
  "schema_version": "qwendex.cli.v1",
  "status": "pass",
  "command": "about",
  "summary": "...",
  "data": {},
  "artifacts": [],
  "next_actions": [],
  "errors": [],
  "version": "0.7.0"
}
```

`pass` means the requested operation completed. `warning` carries usable
output with advisory findings. `blocked` or `fail` means the requested
operation did not satisfy its contract. Manager lifecycle warnings never grant
or remove Codex execution authority.

Global options are `--config <path>` and `--agent-use <mode>`. Command-specific
help is authoritative:

```bash
scripts/qwendex --help
scripts/qwendex manager --help
scripts/qwendex agent --help
```

## Command Groups

| Command | Purpose | Mutates state by default |
| --- | --- | --- |
| `about`, `version` | Product, version, surface, and compatibility contracts | No |
| `check`, `doctor` | Product and runtime diagnostics | No lifecycle reconciliation |
| `up`, `down`, `restart`, `llmstack` | Optional local-stack lifecycle and checks | Lifecycle actions do; checks do not |
| `exec`, `route`, `seat` | Resolve or execute hosted/local seats | `exec` writes a run receipt |
| `estimate` | Deterministic task estimate | No |
| `performance` | Default-off metadata telemetry status, aggregates, benchmark, purge | Only capture/purge paths |
| `search` | Repository search with stable JSON results | No |
| `docs` | Audit, build, or serve repository documentation | Audit is read-only; build/serve create local outputs |
| `agent`, `manager` | Advisory planning, policy, lifecycle, validation, and hooks | Explicit lifecycle actions only |
| `eval` | Offline or live Qwendex evaluation receipts | Writes receipts |
| `receipt` | Inspect and validate receipts | No |
| `task`, `context`, `handoff`, `evidence`, `queue` | Local task and evidence workflows | Action-dependent |
| `learn` | Non-mutating mock, staged proposals, or explicit external SkillOpt delegation | Explicit action-dependent |
| `runtime` | Inspect, validate, activate, or recover immutable generations | Activation/recovery actions only |
| `codex-status` | Render or explicitly write the Qwendex TUI status contract | Read-only unless `--write` is supplied |
| `codex-patch` | Preflight, inspect, or apply a supported Codex source patch | Only `apply` mutates the selected source checkout |

## Routing And Execution

```bash
scripts/qwendex route --seat auto --task-class exec --json
scripts/qwendex exec "Reply exactly QWENDEX_OK" --seat auto --json
scripts/qwendex exec "Inspect one file" --seat terra --dry-run --json
scripts/qwendex exec "Summarize this bounded input" --seat luna --dry-run --json
```

The main session remains user-selected. Native Manager V2 workers default to
Terra/high, with Terra/xhigh for reviewer and release-manager lanes. Luna/max
is an explicit one-shot `exec` seat; Codex 0.147 does not advertise Luna as a
V2 worker model. Local Qwen uses the separate `qwendex_exec` surface only when
the lane is allowlisted, the Local toggle is on, and the endpoint probe passes.

`token_saver_used` describes an execution-backed local route. A merely eligible
lane reports `local_qwen_eligible` without claiming that local execution
occurred.

## Qdex Launch Permissions

```bash
qdex -C <project>
qdex --qdex-permission-mode workspace-write -C <project>
qdex --qdex-permission-mode auto-review -C <project>
qdex --qdex-permission-mode yolo -C <project>
```

`auto-review` maps exclusively to Codex 0.147's `--approve-for-me` posture.
`workspace-write` uses the configured sandbox, while `yolo` explicitly bypasses
approvals and sandboxing. Qdex rejects caller-supplied native permission flags
and permission config; select the posture through Qdex. Codex
0.147 removed `exec --full-auto`; Qdex stops with migration guidance instead of
forwarding that obsolete option. Project trust remains Codex-native and is not
blanket-injected by Qdex.

## Manager And Agent Lifecycle

Read-only inspection:

```bash
scripts/qwendex manager status --json
scripts/qwendex manager decision --json
scripts/qwendex agent status --json
scripts/qwendex agent policy --json
scripts/qwendex agent plan --prompt "Map code and verify tests" --task-id task-map --json
```

Explicit lifecycle mutation:

```bash
scripts/qwendex manager assign --agent-id verifier-1 --lane verification --task-id task-map --owner verifier --write-surface read-only --stop-condition "return evidence" --json
scripts/qwendex manager heartbeat --agent-id verifier-1 --json
scripts/qwendex manager close --agent-id verifier-1 --reason integrated --json
scripts/qwendex manager close-stale --stale-after-minutes 30 --json
scripts/qwendex manager repair --safe --json
```

Evidence-backed validation:

```bash
scripts/qwendex manager validate --agent-id verifier-1 --receipt-path <receipt.json> --json
scripts/qwendex manager validate --agent-id verifier-1 --receipt-path <receipt.json> --sha256 <file-sha256> --json
scripts/qwendex manager validation-waive --agent-id verifier-1 --actor <operator-id> --reason "operator accepted non-release advisory result" --json
scripts/qwendex manager reconcile --pending-validation --json
```

`validate` rejects symlinks, oversized/non-JSON files, unsupported receipt
schemas, digest mismatches, and evidence whose repository, task, or agent does
not bind to the target session. Its file digest covers the exact parsed bytes.
A waiver requires an actor and reason and is explicit audited provenance, never
synthetic validation. Status and reconcile classify debt but do not silently
close, repair, or validate rows.

The supported Codex 0.147 V2 root collaboration tools are exactly
`spawn_agent`, `send_message`, `followup_task`, `wait_agent`,
`interrupt_agent`, and `list_agents`. Children receive no collaboration tools
under the canonical patch.

## Runtime And Verification

```bash
scripts/qwendex runtime status --json
scripts/qwendex runtime generations --json
scripts/qwendex codex-patch preflight --json
scripts/qwendex codex-patch apply --source /path/to/codex --dry-run --json
scripts/qwendex eval --all --json
qwendex-dev verify --tier quick
qwendex-dev verify --tier full
qwendex-dev verify --tier live
qwendex-dev verify --tier release --candidate
```

Release verification binds the canonical dev Codex binary and build receipt;
an ambient `QWENDEX_DEV_CODEX_BIN` cannot substitute a different binary for a
release gate.
