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

Decision: Qwendex computes `AgentPolicy` in the CLI facade from `--agent-use`,
`QWENDEX_AGENT_USE`, or `CODEX_AGENT_USE`, then exposes the policy hash and
subprocess env exports through `agent`, `manager`, `check`, and `doctor`. The
CLI also owns native `agent hook` gate evaluation for prompt context,
subagent final-report contracts, Manager stop gates, and pre-tool denials.
Native Codex tool-registry filtering and automatic/global hook installation
remain labeled integration boundaries until a patched Codex build proves them
end to end.

Reason: the current Qwendex product surface is the Python CLI and SQLite
manager ledger. Enforcing the selector and gates there gives operators a
connected, testable policy surface without making false claims about stock
Codex runtime tool filtering.

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
`scripts/qwendex agent hook-config` and writes it only to an explicit path with
`--approve`. Existing files require `--force`. The hook commands call the same
native `agent hook` evaluator used by tests and CLI smoke flows.

Reason: hook files should be easy to install for hardened local setups, but
runtime policy must remain authoritative and testable even if hooks are absent,
disabled, or scoped to a different Codex home.

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
