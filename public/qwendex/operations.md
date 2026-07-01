# Operations

## Install And Bootstrap

Qwendex includes a repo-owned dependency installer:

```bash
scripts/qwendex_install_deps --install
scripts/qwendex_install_deps --check --json
```

The installer covers required host commands, validation Python modules, Rust
tooling for patched Codex builds, `ripgrep`, the Codex CLI when npm is
available, and testbench/GitHub helpers where the platform package manager can
provide them. `qwendex-dev bootstrap` records the result in
`.qwendex-dev/results/meta/install_deps.json`.

## Daily Checks

```bash
scripts/qwendex check --json
scripts/qwendex doctor --json
scripts/qwendex eval --json
```

Qwendex keeps the advisory/strict health split explicit. Advisory health can
emit Manager Mode warnings, repair hints, and high-value-add guidance without
blocking a daily operator loop. Strict health is the mode for staging and
release claims: missing required surface, public documentation audit failures,
or Manager Mode health issues such as stale writer lanes must fail the command.

## Stack Control

```bash
./llmstack
./llmstack status --json
scripts/qwendex up --json
scripts/qwendex down --json
scripts/qwendex restart --json
scripts/qwendex llmstack check --json
```

Use `--dry-run` before changing a running stack:

```bash
scripts/qwendex restart bridge --dry-run --json
scripts/qwendex llmstack restart bridge --dry-run --json
```

## Token-Saver Routing

`scripts/qwendex exec` defaults to `--seat auto`. Auto routing probes the
Codex-facing local Qwen model list and uses `qwen` for bounded task classes when
`qwen-local` is visible. If the stack is stopped or the alias is missing, it
falls back to the configured primary seat.

```bash
scripts/qwendex route --task-class exec --json
scripts/qwendex exec "Reply exactly QWENDEX_OK" --seat auto --json
```

## Receipts

Every Qwen run writes a receipt containing model, profile, task class, tool-call
summary, touched files, markers, eval result, effective guard/sandbox policy,
and review status. Receipts remain canonical JSON files on disk. Harness evals
also index compact metadata into the local ledger. Live-run receipts may include
redacted stdout/stderr snippets for debugging, so review before sharing them
outside the operator environment.

```bash
scripts/qwendex receipt latest --json
```

`receipt` verifies supported receipt schemas and SHA-256 digests before
returning data.

## Task And Context State

Use the Qwendex state plane to keep long runs resumable:

```bash
scripts/qwendex task create --title "Ship Qwendex route hardening" --priority P1 --owner main --phase build --json
scripts/qwendex context snapshot --task-id task_... --objective "..." --decision "..." --open-file scripts/qwendex_cli.py --next-action "run tests" --json
scripts/qwendex context reminder --task-id task_... --tool-calls 55 --phase after-milestone --json
scripts/qwendex handoff create --task-id task_... --status ready --next-action "review receipts" --json
scripts/qwendex evidence add --task-id task_... --claim "eval passed" --path results/qwendex/example.json --json
```

Use `context reminder` at phase transitions or when an external reminder fires.
It is advisory: it may recommend continuing, taking a snapshot first, or running
`context compact-plan` before a manual compact.

## Queue Facade

Qwendex exposes the existing `TASK_QUEUE.md` artifact queue through a narrow
CLI facade:

```bash
scripts/qwendex queue init --dir . --item one.md::"First artifact" --json
scripts/qwendex queue start --dir . --file one.md --json
scripts/qwendex queue done --dir . --file one.md --json
scripts/qwendex queue next --dir . --json
```

The queue delegate keeps one item in progress and returns `blocked` when blocked
items should stop the workflow.

## Compatibility

Older local stack scripts remain available and should delegate toward Qwendex as
the public boundary matures. Do not duplicate model, context, guard, or receipt
policy in wrapper scripts.

## Manager Mode

For complicated runs, use the adaptive manager controls:

```bash
scripts/qwendex manager mode --toggle --json
scripts/qwendex manager kaveman --toggle --json
scripts/qwendex manager local --toggle --json
scripts/qwendex manager estimate --prompt "..." --json
scripts/qwendex manager mode --set manager --json
scripts/qwendex manager status --json
```

In a patched Codex TUI, `Alt+M` cycles Agent Manager, `Alt+K` toggles Kaveman,
and `Alt+L` toggles Local. `Local: [Off]` means Qwendex will skip local
subagents even when the local model endpoint is healthy; `Local: [Unavailable]`
means intent is on but the probe could not confirm a usable local route.

Manager Mode defaults to `max_subagents: 10`, which is also the Qwendex product
ceiling for concurrent subagent lanes.

`manager_deploy_policy` defaults to `auto`: when the selected mode is Manager
Mode, Qwendex expects at least one active registered agent lane. Routine
advisory health reports no-lane Manager Mode as `standby`; strict release
health reports it as blocked. Set `manager_deploy_policy` to `disabled` to opt
out of that requirement; explicit manual manager lifecycle commands remain
operator-directed.

Use `scripts/qwendex manager close --agent-id ... --reason integrated --json`
after integrating or intentionally stopping a writer lane. `close-stale` only
auto-closes stale read-only lanes.

`scripts/qwendex manager repair --safe --json` is a bounded repair path for
safe manager-state issues. It closes stale read-only lanes and harmless empty
stale writer lanes, then leaves non-empty writer lanes open with an explicit
manual `manager close --agent-id ... --reason ... --json` command. Those
remaining stale writer lanes are advisory warnings during daily health and
blockers during strict health.

Every assigned lane records a context packet and remains advisory until the main
session reviews receipts, touched files, validation status, blockers, and
unresolved risk.
