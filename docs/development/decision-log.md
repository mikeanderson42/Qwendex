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

## Codex Code-Mode Companion

Decision: the patched Codex build installs and receipts both `codex` and the
sibling `codex-code-mode-host`, and the dev wrapper blocks before launch if the
selected dev runtime is missing that executable companion.

Reason: Codex 0.144.0 enables code mode by default and resolves its host beside
the running executable. Installing only `codex` allows the TUI to start but
leaves execution and goal tools unusable, which can also prevent Manager Mode
from recording or closing its decision ledger.

## Codex 0.143.0 Launcher Target

Decision: Qwendex `0.1.0-rc.5` targets Codex CLI `0.143.0` and
`rust-v0.143.0` as the default patched launcher source while retaining older
0.142.x patch manifests for compatibility checks.

Reason: the operator runtime moved to Codex 0.143.0, so the default dev
launcher, manifest preflight, docs, and release validation must agree on the
same supported Codex source tag.

Update for `0.4.0`: the default patched launcher source is Codex CLI
`0.144.0` / `rust-v0.144.0`. Older `0.142.x` and `0.143.0` manifests remain
available for compatibility checks, but release-facing patched TUI claims now
target the installed `0.144.0` runtime.

## Kaveman Control Boundary

Decision: Qwendex exposes Kaveman as persisted mode state and a terse-output
directive that the patched Codex TUI injects into developer instructions, not
as a vendored external Git package.

Reason: the harness needs a connected footer/CLI/TUI control with low
maintenance cost; projects that want the upstream Caveman package can install it
separately.

Update for `0.3.2`: Kaveman is also part of the resolved `AgentPolicy` as an
`output_policy`. Enabling it changes the policy hash, hook context, manager
workflow receipts, and exported launch environment, so terse-output mode is not
only a visual status flag.

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

## Fail-Closed Managed Shell Hooks

Decision: managed `PreToolUse` events for read-only profiles accept shell
execution only when a quote-aware parser proves that every command-list or
pipeline segment is in the public inspection allowlist. Unknown commands,
interpreters, wrappers, expansion, redirects, unquoted globs, and unparseable
syntax are denied. Git, ripgrep, and find receive command-specific option gates
that exclude output files, external helpers, preprocessors, execution actions,
and deletion. For write-capable profiles, any managed shell command outside the
same inspection allowlist is presumed to write and must provide agent identity
and explicit target paths before the existing lock gate runs.

Reason: recognizing a short blacklist of write-shaped strings cannot establish
that a shell command is read-only. Common mutators, interpreter snippets, shell
wrappers, option-driven output, and expansion can all bypass such a detector.
A deliberately small positive grammar keeps read-only exploration useful while
making ambiguity a blocking result. Presuming ambiguous writer commands to
write also prevents an endless mutation blacklist from becoming a lock bypass.
The contract remains a managed-hook classifier layered with, not a replacement
for, the host sandbox and stock runtime tool permissions.

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
The `qdex` wrapper exports the manager session id, ledger id, launcher-derived
root ownership id, and policy hash to Codex only after the preflight record
exists. Manager Stop gates require that ledger and close either a managed-lane
completion or a direct-work exception with validation evidence.

Reason: Manager Mode is an execution contract, not only a selected label. The
operator should not be able to start write-capable direct Codex work in Manager
Mode without a recorded route decision, hook posture, verifier expectation, and
finalization path. Interactive prompts can still start before the task text is
known, but that path is explicitly recorded as
`interactive_prompt_unknown_prelaunch`.

## Launcher-Derived Manager Root Ownership

Decision: Codex root hook events remain identity-less as defined by the native
hook schema. In Manager Mode, Qwendex derives a stable root owner only from the
matching `qdex` preflight ledger, rejects stale or mismatched launch exports,
and gives opaque root writes a repository-wide per-tool lease. `PostToolUse`
releases the exact tool lease and Manager `Stop` releases the launch family.
Native subagents remain strict:
their top-level runtime id must be actively registered for the same repository
and current manager task, with paths resolved from hook metadata or registered
scope. Goal and plan bookkeeping is outside the filesystem lock plane.

Reason: requiring a root `agent_id` contradicts Codex's hook contract, and
putting `agent_id=main` in prompt or tool input cannot populate or authenticate
the lifecycle envelope. A trusted launcher identity preserves the single-writer
boundary without blocking the root on every tool, while short per-tool leases
release promptly after successful tools. Codex does not emit `PostToolUse` for
an aborted tool, so that lease intentionally remains blocking until `Stop`;
after an abrupt process exit, a later launcher reclaims it only when the
recorded PID and process-start identity are no longer live.

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

## Public Source And Release Evidence Boundary

Decision: the public Qwendex release artifact is the exact tagged git tree.
Operator recovery history, downstream assistant workflows, absolute machine
paths, local model inventory, runtime state, and locally built Codex binaries
do not belong in tracked Qwendex files or attached release assets.

Reason: GitHub source archives include every tracked blob. Labeling a file as
developer instructions does not make its private or downstream-specific content
non-public. Qwendex must keep reusable product rules tracked and move
operator-local context to ignored overlays or the owning downstream repo.

## Immutable Release Gate

Decision: release evidence is generated in a unique per-run directory. Every
local receipt is bound to the run, stable command/gate identity, exact commit and
tree, strict mode where required, generation time, and canonical payload digest.
Publish readiness additionally requires a clean default branch, trusted
configured origin, annotated local tag, source-bound remote CI attestation,
isolated Codex build proof, tracked-artifact scan, all-file guard-marker scan,
and an unchanged-source recheck. A fixed latest-summary path is not authoritative
because later commands can overwrite the files it references.

Reason: a green mutable summary can otherwise outlive or drift away from the
commands and source it claims to validate. Candidate branches may produce
candidate evidence, but only the clean tagged default-branch commit can be
`publish-ready`.

## Remote Pull Request Gate

Decision: GitHub Actions runs full Python compile/lint, tracked JSON syntax and
published-config schema validation, Bash syntax, the full pytest suite, strict
public surface checks, the artifact contract against actual HEAD, and the
documented same-root installation path on pushes and pull requests. A
successful run emits a commit/tree/ref-bound CI
attestation for the local publish gate. Release publication remains a deliberate
operator action.

Reason: local receipts and remote review are complementary. A public release
branch with no remote checks provides no independent evidence that the pushed
tree matches a passing build.

## Release Command Approval Provenance

Decision: the managed pre-tool gate recognizes direct and path-qualified
release commands through common shell wrappers, substitutions, pipelines, and
newline-separated commands. Publication approval is accepted only when
`QWENDEX_RELEASE_APPROVED` already exists in the managed hook process
environment. Inline assignments, `env`, `export`, and agent-controlled event
JSON cannot create that authority.

Reason: parsing command text as both the requested action and its authorization
lets an agent self-approve publication. Static recognition still cannot prove
arbitrary interpreter-generated commands, so credentials, network policy, and
the execution sandbox remain the ultimate publication boundary.

## Repository-Scoped Manager State

Decision: new manager sessions, decisions, active limits, and file locks record
the canonical target repository. Stop gates join agents by both manager task id
and repository. Operational health is scoped, while full-ledger validation debt
is counted truthfully with bounded samples and explicit legacy-unscoped counts.
Legacy rows are not silently migrated or marked validated; active unscoped
write locks remain conservative blockers until an owning session is explicitly
re-registered or reviewed.

Reason: a shared dev SQLite file previously allowed unrelated downstream agent
history to block or satisfy Qwendex gates, hid debt behind status limits, and
made independent repositories contend for one writer slot. Scope metadata
preserves one inspectable ledger without treating cross-project state as proof
for the current run.

## Interactive Manager Attachment And Receipts

Decision: each root `UserPromptSubmit` attaches the real prompt to a turn
decision under the exported preflight launch ledger, ignoring Codex's generic
thread `session_id`, and returns the deterministic plan plus runtime-id
registration instructions. Later Codex `turn_id` values create additive child
decision rows and distinct `agent_task_id` validation scopes without changing
the parent process environment.
Manager decision receipts use `qwendex.manager_decision.v1`, a self-digest, and
atomic replacement so the normal receipt verifier and concurrent readers see a
complete artifact.

Reason: interactive `qdex` launches do not know the prompt during preflight.
Without hook-time attachment the persisted direct-work exception could diverge
from an actual manager task, and without a digest the newest manager receipt
caused `qwendex receipt latest` to fail its own verification contract.

## Published Configuration Schema Gate

Decision: Qwendex validates `qwendex.json` and `qwendex.sample.json` with the
exactly pinned `jsonschema` 4.26.0 `Draft202012Validator`. The published schema
must itself pass the Draft 2020-12 meta-schema, use its canonical schema ID and
local fragment references only, and agree with both configs on the schema
version. Config product versions must be SemVer values that match each other
and the current `RELEASE.md` heading. The full dev gate and both supported CI
Python versions run this validator, and remote release evidence requires the
named CI schema check. The `learning` object rejects undeclared keys; the prior
`auto_harvest` and `auto_stage_safe_proposals` placeholders remain absent until
they have connected runtime behavior and proof.

Reason: JSON syntax alone cannot detect a valid-looking configuration that has
drifted from its published contract. Pinning the implementation and checking
schema, semantic, release-version, and bounded-reference invariants makes the
same failure reproducible locally, on Python 3.11, and in publication evidence.

## Live Codex Binary And Home Isolation Binding

Decision: live Codex acceptance hashes the launcher and Codex executable before
and after execution, requires every successful command event to identify the
requested `printf TOOL_OK` command, rejects non-JSON stdout, and hashes a
controlled HOME/CODEX_HOME/XDG decoy tree. Release validation requires the live
executable digest and size to equal the validated Codex build receipt from the
same source-bound run.

Reason: a tool transcript alone does not prove which executable produced it,
and checking only an explicit CODEX_HOME misses regressions that fall back to
HOME or XDG state. Binding both executable identity and all normal-home roots
closes those substitution and isolation gaps without retaining raw transcripts.

## Canonical Responses Bridge Status Contract

Decision: `/status` is the canonical local-Qwen Responses bridge readiness
endpoint and returns `qwendex.responses_bridge.status.v1` with `status: ok`.
Launcher and stack startup validate that payload rather than accepting any HTTP
200. `/__tabby_proxy_status` remains an identical legacy alias.

Reason: a configured status URL is not useful evidence if arbitrary content or
a different service can satisfy it. A small versioned contract makes bridge
identity and readiness testable while preserving existing local integrations.

## Canonical Local Context Budget

Decision: the public 32k backend profile, Qwendex local seats, launcher
fallback, and sample environment share a 32768-token context window and a
28672-token auto-compact limit. Runtime and published-config validation reject
any effective compact limit at or above its seat context. Capability catalogs
may describe a model's larger theoretical maximum, but remain explicitly
non-authoritative for the active runtime.

Reason: advertising 65k in the seat policy while the default and operator
backend serve 32k makes direct local execution fail its own bridge preflight;
an isolated sandbox seat also inherited a 56k compact limit above its 32k
window. One conservative runtime budget keeps clean installs and the current
operator stack aligned without increasing model memory pressure.

## Local Endpoint And Target-Repository Binding

Decision: the local Codex launcher derives one canonical bridge base from the
routing contract, exports exactly `<base>/v1` as `CODEX_OSS_BASE_URL`, and
blocks a conflicting inherited endpoint. `qdex --repo` uses the selected
repository as the manager scope, execution directory, Codex add-dir, and MCP
trusted root while retaining the generated isolated `CODEX_HOME` unless the
operator explicitly opts into preserving a caller home.

Reason: probing one endpoint and executing against another invalidates live
evidence. Likewise, launching Qwendex for a downstream repository must not make
the Qwendex product checkout an implicit manager, execution, local-harness, or
MCP capability root. This routing boundary does not claim filesystem
confinement for the deliberately unsandboxed `qdex` process.

## Learning And Descriptive-Config Boundary

Decision: the built-in mock learning dry-run is a non-mutating contract check.
External SkillOpt remains required for status, harvest, and run actions.
`learn adopt --approve` performs only a proposal-path allowlist preflight and
never applies files. The unused top-level `mcp_tools` catalog and Qwen-seat
`prompt_template` key are removed from built-in and published configuration;
the MCP server's callable tool list and the launcher/runtime instruction sources
remain authoritative instead.

Reason: an approval-shaped command must not imply a mutation that does not
exist, and descriptive config keys with no runtime reader violate the
connectedness contract. Removing them prevents users from editing inert values
or relying on nonexistent MCP status, receipt, eval, and learning tools.

## Codex-Compatible Launch And Versioned Model Cache

Decision: the generated environment resolves bare `codex` to the Qwendex
wrapper and retains `codex-main` as the upstream escape hatch. `qdex` owns only
its explicit repository/preflight-rendering options, defaults manager scope to
`$PWD`, preserves native Codex arguments, and treats help/version as stateless
inspection. The patched Codex model manager honors
`QWENDEX_MODELS_CACHE_FILE`; Qwendex exports a filename containing the pinned
Codex version while leaving the rest of the isolated home shared. Packaged
release binaries are stripped with their pre/post sizes receipted.

Reason: a launcher presented as the Codex entrypoint cannot consume native
`exec --json`, split `-C` from its value, redirect non-git work to the Qwendex
source tree, or mutate Manager state during `--version`. A single unversioned
model cache also lets older live clients repeatedly replace a newer catalog,
making newly available models appear and disappear. Versioning only that cache
preserves authentication, history, hooks, and sessions while removing the
cross-version writer race; stripping closes the avoidable native build-size
overhead.

## Codex-Native Qdex Working Directory

Decision: plain `qdex` inherits the caller's working directory without adding a
synthetic `-C`. Codex-native `-C`/`--cd` is the canonical explicit-directory
form: Qdex observes its value only to align Manager, MCP, and trust scope, then
passes the original option and value through unchanged. The older Qdex-only
`--repo` option remains a compatibility alias and may inject `-C` for existing
callers.

Reason: Qdex is presented as a Codex-compatible entrypoint. Its default argv
should therefore match `codex` rather than contain a redundant wrapper-created
directory option, while the control-plane scope must still follow an explicit
native Codex working root end to end.

For Manager launches, the same verified preflight authorizes Codex's explicit
hook-trust bypass. This avoids a redundant interactive review dialog while
keeping non-Manager launches on Codex's normal trust behavior.
The canonical target is also passed as a per-launch trusted project; this trust
is bounded to the same explicit Qdex repository and prevents an automation
primer's first Enter from being consumed by Codex onboarding.

## Upstream Codex, Qdex, And Internal Runtime Trust Boundary

Decision: ordinary `codex` remains the upstream installation and retains the
caller's normal `CODEX_HOME`. Sourcing Qwendex exports neither a replacement
`codex` command nor `CODEX_HOME`. `qdex` is the public, intentionally
permissive launcher: it selects Qwendex's isolated home, runs exactly one
Manager preflight when required, and invokes an ignored internal runtime that
chooses the supported patched binary or emits a labelled upstream-fallback
diagnostic. `codex-main` remains an explicit captured-upstream alias.

Manager trust uses one canonical validator for prompt hooks, root writes, Stop,
and `manager launch-status`. The binding includes live PID/start ticks,
repository, preflight ledger/session, derived root identity, isolated Codex
home, hook trust, decision state/route, and current policy. An invalid root
`UserPromptSubmit` blocks before model work. An invalid Stop is allowed to
terminate without attaching to or mutating a Manager decision.

Reason: repository and state paths identify a scope, not a launching process.
A direct internal-runtime process must not acquire Qdex authority merely by
sharing that scope, and Stop cannot safely repair missing identity by guessing
the latest repository decision. Keeping upstream Codex outside this boundary
also preserves a recovery path when Qwendex runtime or Manager state is broken.
