# Operations

## Install And Bootstrap

Install Qwendex from a clean tagged git checkout at `~/qwendex-dev`; the public
quickstart contains the exact clone, upgrade, and rollback sequence. Qwendex is
source-distributed and does not claim a Python/npm package installation path.

Qwendex includes a repo-owned dependency installer for that checkout:

```bash
scripts/qwendex_install_deps --install
scripts/qwendex_install_deps --check --json
```

The installer covers required host commands, exactly pinned validation Python
modules, Rust tooling for patched Codex builds, `ripgrep`, the Codex CLI when
npm is available, and testbench/GitHub helpers where the platform package
manager can provide them. Python packages remain user-scoped; the receipt
reports whether PEP 668 required pip's externally-managed override.
`qwendex-dev bootstrap` records the result in
`.qwendex-dev/results/meta/install_deps.json`.

`scripts/qwendex_dev_env sync` generates isolated runtime wiring and installs
`scripts/qdex` to `~/.local/bin/qdex`. It does not update source. Fetch and
select a release tag with git first, stop on a dirty checkout, then rerun sync,
bootstrap/doctor, and offline evals.

Sourcing the generated environment leaves ordinary upstream `codex` and the
caller's `CODEX_HOME` unchanged. Start managed work with `qdex` or
`qdex -C <repo>`. For a supervised live process, verify the trust binding with:

```bash
scripts/qwendex manager launch-status --pid "$PID" --repo-root "$REPO" --json
```

For manual operator navigation, `fd` + `fzf` can be configured in the user's
interactive shell:

```bash
if command -v fd >/dev/null 2>&1 && command -v fzf >/dev/null 2>&1; then
  export FZF_DEFAULT_COMMAND='fd --type f --strip-cwd-prefix --hidden --follow --exclude .git'
  export FZF_CTRL_T_COMMAND="$FZF_DEFAULT_COMMAND"
fi
```

Use that for ad hoc path picking only. Qwendex health, bootstrap, staging, and
release checks must keep using explicit commands and repo-owned manifests.
`plocate "filename"` is a useful optional indexed lookup when the host provides
and updates a plocate database; Qwendex does not require or manage that timer.

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

## Local Exploration Telemetry

Exploration telemetry is optional, local-only metadata capture. It is disabled
by default and is separate from Manager state. When a trusted Qwendex build is
configured with `performance.capture: "metadata"`, inspect only aggregate-safe
results with:

```bash
scripts/qwendex performance status --json
scripts/qwendex performance summary --json
scripts/qwendex performance summary --repo-root <path> --since-days 7 --json
scripts/qwendex performance runs --limit 20 --json
scripts/qwendex performance purge --approve --json
```

The output contains counts, timing distributions, classes, and local digest
comparisons rather than prompts, commands, paths, queries, or tool output.
`summary` and `runs` default to the canonical current repository; use
`summary --repo-root` to inspect another root. `summary` also enforces the
configured retention and event-count limits.
`purge` requires explicit approval and deletes the local telemetry data.
Unavailable metrics are explicitly `not_observed`, not reported as zero.

Run the isolated instrumentation check with:

```bash
scripts/qwendex performance benchmark --suite exploration --json
```

That benchmark proves synthetic capture coverage, local raw-sentinel absence,
and instrumentation timing only. It does not establish that Qwendex, search,
startup, validation, model behavior, or end-to-end tasks are faster.

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
The prompt is classified before routing; primary-authority security, release,
architecture/protocol, and public-doc-claim work cannot be forced to Qwen.
Audit-seat exec is read-only and omits user/local-harness MCP configuration.

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

Plain-output `exec` cannot observe a complete tool-call or touched-file event
stream, so those receipt fields are explicitly `not_observed`; an empty list is
not presented as evidence that no tools or files were used. Dry runs and
configured-seat receipts say `not_executed` and never claim availability.
Any configured guard marker in stdout or stderr makes the exec and receipt fail
even when the child process exits zero.

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

`scripts/qwendex up`, `down`, and `restart` delegate directly to the legacy
`scripts/llm` stack mutations. Inspection surfaces remain distinct:
`qwendex check` is a static product check, `qwendex llmstack check` is a static
public-config contract, and `llmstack status` is live service state. Older
internal commands may remain available, but they are not automatically
equivalent public evidence. Do not duplicate model, context, guard, or receipt
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

Manager Mode defaults to `max_subagents: 4`. Operators may configure a lower or
higher bounded value up to the conservative product ceiling of 8 concurrent
worker lanes; Codex V2 counts the root separately.

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
