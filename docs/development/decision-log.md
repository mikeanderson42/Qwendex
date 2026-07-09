# Qwendex Development Decision Log

## Worktree Over Generated Copy

Decision: `~/qwendex-dev` is a git worktree.

Reason: native git status, diff, staging, and branch history make it safer for a
product-development lane than a generated rsync copy.

## Dev Runtime Isolation

Decision: mutable dev state lives under `.qwendex-dev/`.

Reason: development receipts, ledgers, Codex home, source builds, and snapshots
must not mix with normal user harness state or public release artifacts.

## Patched Codex Contract

Decision: Qwendex patches Codex source by versioned anchors, not by mutating the
installed binary.

Reason: npm-installed Codex ships a native binary; source patching is auditable,
repeatable, and can block safely when Codex versions change.

## Codex 0.143.0 Launcher Target

Decision: Qwendex `0.1.0-rc.5` targets Codex CLI `0.143.0` and
`rust-v0.143.0` as the default patched launcher source while retaining older
0.142.x patch manifests for compatibility checks.

Reason: the operator runtime moved to Codex 0.143.0, so the default dev
launcher, manifest preflight, docs, and release validation must agree on the
same supported Codex source tag.

## Kaveman Control Boundary

Decision: Qwendex exposes Kaveman as persisted mode state and a terse-output
directive that the patched Codex TUI injects into developer instructions, not
as a vendored external Git package.

Reason: the harness needs a connected footer/CLI/TUI control with low
maintenance cost; projects that want the upstream Caveman package can install it
separately.

## Manager Session Reconciliation

Decision: status, doctor, and Codex status refreshes reconcile stale read-only
manager lanes automatically, but stale writer lanes remain blocked until the
operator integrates or explicitly stops them.

Reason: stale read-only audit lanes should not keep Manager Mode healthy after a
TUI refresh, while writer lanes may represent unintegrated changes and must
stay visible.

## AgentPolicy Facade Boundary

Decision: Qwendex computes `AgentPolicy` in the CLI facade from explicit
`--agent-use`, `QWENDEX_AGENT_USE`, or `CODEX_AGENT_USE` selectors, falling
back to the selected Agent Manager mode when no explicit selector is present.
It then exposes the policy hash and subprocess env exports through `agent`,
`manager`, `check`, `doctor`, and `codex-status`. The CLI also owns native
`agent hook` gate evaluation for prompt context, subagent final-report
contracts, Manager stop gates, and pre-tool denials. Native Codex tool-registry
filtering and automatic/global hook installation remain labeled integration
boundaries until a patched Codex build proves them end to end.

Reason: the current Qwendex product surface is the Python CLI and SQLite
manager ledger. Enforcing the selector and gates there gives operators a
connected, testable policy surface without making false claims about stock
Codex runtime tool filtering.

## Selected Manager Mode As Policy Source

Decision: when no explicit `--agent-use`, `QWENDEX_AGENT_USE`, or
`CODEX_AGENT_USE` selector is present, Qwendex resolves `AgentPolicy` from the
persisted Agent Manager mode selected by `scripts/qwendex manager mode ...` or
the patched TUI `Alt+M` shortcut.

Reason: the visible Agent Manager footer and backend enforcement must agree.
Manager Mode should activate manager stop gates, and Off mode should block
automatic subagent spawning, without requiring a separate environment variable
that can drift from the UI state.

## Agent Write Safety

Decision: the first Agent Management release uses a single-writer strategy for
the base worktree. Qwendex records write ownership in
`qwendex_agent_file_locks`, blocks a second active writer through the native
`PreToolUse` gate, and releases locks when the owner reaches a terminal status,
is closed, is tombstoned, or is reconciled by safe stale repair.

Reason: multiple writer agents need either worktree isolation or more mature
merge/conflict handling. A conservative single-writer rule is simpler to
explain, easier to verify, and prevents accidental same-worktree corruption
while leaving worktree-per-writer as a later enhancement.

## Agent Raw Output Preservation

Decision: Manager Mode stores child final output as ignored local artifacts
under `.qwendex/runs/`, records raw and compact artifact paths on the existing
agent ledger row, and exposes compact outcomes through context pack and compact
plan commands.

Reason: the root agent needs durable evidence without flooding its working
context with raw logs or rewritten worker transcripts. Keeping raw output local
and ignored preserves reviewability while maintaining the public/private
artifact boundary.

## Managed Agent Hook Config

Decision: Qwendex generates managed hook wiring through
`scripts/qwendex agent hook-config` and writes it only through an explicit
operator action: either `--write ... --approve` for an arbitrary path or
`--install --codex-home ...` for a Codex home. Existing files require `--force`.
The hook commands call the same native `agent hook` evaluator used by tests and
CLI smoke flows.

Reason: hook files should be easy to install for hardened local setups, but
runtime policy must remain authoritative and testable even if hooks are
disabled or scoped to a different Codex home. Missing hooks in Manager Mode are
now a launch-time block unless the operator sets an explicit unhooked override,
which is recorded in the manager decision ledger.

Update: managed hook commands use `agent hook ... --codex-hook-output` for
Codex lifecycle execution. Manual `agent hook ... --json` remains the stable
Qwendex diagnostic envelope, while `--codex-hook-output` emits only fields
accepted by Codex's per-event hook stdout schemas. Hook verification treats
stale Qwendex lifecycle commands without `--codex-hook-output` as incompatible,
even if every managed event is present.

## Manager Preflight Session Contract

Decision: normal `qdex` launches in Manager Mode must run
`scripts/qwendex manager preflight` before launching Codex. Preflight writes a
`manager_decision` ledger record and receipt with policy hash, hook status,
local/cloud availability, prompt digest or interactive-unknown marker, selected
route, routing reason, verifier requirement, validation plan, and STOP status.
The `qdex` wrapper exports the manager session id, ledger id, and policy hash
to Codex only after the preflight record exists. Manager Stop gates require that
ledger and close either a managed-lane completion or a direct-work exception
with validation evidence.

Reason: Manager Mode is an execution contract, not only a selected label. The
operator should not be able to start write-capable direct Codex work in Manager
Mode without a recorded route decision, hook posture, verifier expectation, and
finalization path. Interactive prompts can still start before the task text is
known, but that path is explicitly recorded as
`interactive_prompt_unknown_prelaunch`.

## Agent Team Planning

Decision: Qwendex exposes deterministic team routing through
`scripts/qwendex agent plan --prompt ...`. The planner returns direct-work
exceptions for trivial/Lite cases and concrete `manager assign` commands for
selected built-in profiles in Medium, Heavy, and Manager modes.

Reason: profile metadata alone does not enforce delegation. A visible planning
surface makes routing decisions inspectable, smoke-testable, and connected to
the existing manager ledger without requiring Codex-native spawning to be
available in stock builds.

## Agent Metrics

Decision: Qwendex exposes read-only Agent Management observability through
`scripts/qwendex agent metrics --json`, derived from the same SQLite ledger,
file-lock table, managed hook map, and built-in profile registry used by the
runtime gates.

Reason: release checks need compact numbers for active lanes, terminal lanes,
required incomplete work, final-contract compliance, raw-output artifacts, and
active writers. Metrics should summarize proven state without claiming release
acceptance by themselves.

## Release Verification State Isolation

Decision: `qwendex-dev verify --tier release` resets and uses
`.qwendex-dev/state/release_verify_qwendex.sqlite` for strict release checks.

Reason: release readiness should validate the source checkout and packaged
contracts, not fail because an operator previously toggled Manager Mode in the
normal dev state without an active lane.

## Local Qwen Authority

Decision: local Qwen is useful but never release authority.

Reason: Qwendex is built around receipts, guard markers, and GPT review gates;
release, security, architecture, and public claims require GPT/Codex review.
