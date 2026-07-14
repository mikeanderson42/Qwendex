# Qwendex Development Decision Log

## Worktree Over Generated Copy

Decision: `~/qwendex-dev` is a git worktree.

Reason: native git status, diff, staging, and branch history make it safer for a
product-development lane than a generated rsync copy.

## Dev Runtime Isolation

Decision: mutable dev state lives under `.qwendex-dev/`.

Reason: development receipts, ledgers, Codex home, source builds, and snapshots
must not mix with normal user harness state or public release artifacts.

## Exploration Telemetry Data Plane

Decision: exploration-performance telemetry uses a separate local SQLite
database, is disabled by default, accepts only privacy-minimized metadata after
an existing hook decision passes, and exposes aggregate-only CLI summaries.

Reason: high-frequency measurement must not contend with the correctness-critical
Manager ledger, and event input may contain prompts, commands, paths, outputs,
or credentials. Qwendex derives only repository-scope and locally HMACed
correlation digests, bounded classes/counts/timings, and in-memory output sizes.
It never persists raw event content or exports a telemetry stream. A blocked
hook produces no event and telemetry failure cannot alter Manager safety.

The Phase 1 benchmark is intentionally synthetic and isolated. Its timing and
privacy scan validate instrumentation only; search, startup, validation, model,
and end-to-end performance claims require a later paired evaluation.

## Optimization Lab And Search Evidence Compaction

Decision: v0.6.0 uses a frozen, isolated paired-evaluation lab before any
optimization claim. Its first candidate, `search_evidence_compaction_v1`, is
default-off, uses current-worktree live ripgrep rather than an index/cache, and
keeps full raw evidence only in ignored local artifacts. The metadata telemetry
database remains content-free. A controlled search-evidence runner may reject
or hold a candidate, but cannot promote it without separate live-model and
Manager-binding evidence.

Reason: byte reduction alone can conceal missing relevant regions, stale or
untracked-file misses, privacy leaks, Manager regressions, or model-context
cost. Separating the candidate from normal search behavior retains a reversible
experimental boundary while reproducible gates establish the next measured
frontier.

## Search Evidence Compaction V2 Recall Contract

Decision: retain `search_evidence_compaction_v2` as an explicit, default-off
experimental candidate. V2 uses definition-aware, cross-file coverage with a
snapshot-bound cursor, explicit completeness states, stale-cursor rejection,
and conservative baseline fallback. Full raw search and live-agent traces stay
only in ignored artifacts; telemetry remains metadata-only.

Reason: v1 reduced bytes but omitted a required broad-definition region. V2
must prove recall through direct inclusion, deterministic cursor retrieval, or
fallback before it can claim any efficiency benefit. Controlled regression and
repair evidence can establish that contract, but a live sample with invalid
pairs cannot reject or promote the candidate. V2 remains held until a frozen
live workload completes enough valid paired tasks; it must never become the
default as a consequence of this decision.

## Live Optimization Runtime Supervisor

Decision: live optimization-lab sessions use a private,
`qwendex.live_runtime_profile.v1` progress-aware supervisor rather than one
opaque `communicate()` wall-clock timeout. Calibration first replays the frozen
legacy wall with profiling only. A subsequent paired run accepts one
canonicalized, hash-bound policy with separate startup/preflight,
first-model-activity, inactivity, hard-wall, graceful-termination,
process-group cleanup, and pipe-draining ceilings. Both arms must use the same
policy identity; raw byte arrival, warnings, and noisy descendants do not reset
the inactivity clock.

Reason: the prior held-out pilot had structured activity but no trustworthy
timestamps or process-state samples at its 180-second boundary. Treating that
absence as either model success or candidate failure would be unsound. The
profile stores only ignored, metadata-only diagnostics—safe phase offsets,
event counts, PID/PGID process state/CPU/RSS buckets, pipe byte counts, timeout
class, and sanitized Manager counts—and excludes prompts, commands, queries,
task paths, tool content, stdout/stderr, transcripts, credentials, and tokens.
The larger hard wall is an evidence-gathering ceiling, not a performance claim
or permission to enable Search V2 by default.

Update: native parent JSONL can become quiet while a child agent continues
performing real work during a collaboration wait. For a fresh metadata-capture
arm, the supervisor therefore consumes only completed allowlisted tool and
subagent lifecycle categories from that arm's isolated performance database
after the Codex root starts. It records fixed counts only; raw hook fields and
identifiers remain excluded. A pending lifecycle entry, including a native
wait, never resets inactivity. Because concurrent hook transactions can commit
out of row-ID order, the private reader keeps an arm-local safe state map
instead of assuming a monotonic database cursor, so an in-place `pending` to
`completed` transition is not lost. It never records identifiers or payloads.
The timing reader cannot change any hook or Manager safety decision.

Update: when an emitted hook exposes an accepted collaboration wait, Qwendex
derives only a fixed `timeout_ms` bucket and carries its count into the ignored
runtime profile. The local telemetry database stores no raw tool input or
number, public aggregates omit the bucket, and the observation cannot reset
inactivity or change a budget. Native collaboration lifecycle items can omit
arguments and have no correlated hook input; an empty bucket is therefore
explicitly `not_observed`, not a default timeout or proof that the native wait
should have completed. Distinguishing an explicit long wait from an upstream
pre-deadline stall remains a separately scoped Codex observability/handler
frontier.

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

For immutable runtime generations, the selected source tree remains sealed
read-only. Qdex therefore pins hook and implementation paths to that tree while
exporting the Manager artifact root as the writable operator-local
`$QWENDEX_DEV_ROOT/.qwendex`. This keeps per-session raw and compact lifecycle
artifacts mutable without weakening generation integrity or writing into a
downstream project worktree.

## Immutable Runtime Generation Boundary

Decision: Qdex selects a validated, read-only runtime generation for each new
process. A generation binds the Qwendex source snapshot, managed hooks, Codex
`0.144.0` patch and binary pair, public config/schema, and Manager state schema.
Candidate build and validation occur beside the selected generation; atomic
selector replacement is the only activation step. Active processes retain
their inherited generation, safe prune preserves selected/known-good/ledger-
referenced generations, and a sync-installed standard-library recovery copy
performs status, activation, and rollback without Qdex.

Reason: the pre-v0.5.7 Manager runtime identity hashed mutable
`qwendex_cli.py`, so an accepted self-edit changed the active identity and the
next hook rejected the session. v0.5.7's path identity stopped that rejection
but still allowed later hooks to execute changed bytes. Side-by-side immutable
trees are required to keep source, hooks, binaries, and config coherent across
self-hosted edits and interrupted activation.

## Manager Production Acceptance And Claim Ceiling

Decision: `manager accept` has offline, live, and production profiles with
explicit run IDs and exact source/config/schema/runtime binding. Production
runs self-hosting before repeated live trials and adds a clean pinned-Codex
build, v0.5.7 upgrade, shell rollback, normal-Codex isolation, security,
persistence, routing, fault, soak, and performance evidence. Historical or
unbound artifacts remain visible but cannot satisfy the current gate.

Reason: a single successful live delegation or an ambiguous newest artifact is
not production evidence. The public claim is limited to the tested Linux and
Codex `0.144.0` canonical patch. Stock Codex continues to support Off-mode
recovery and non-native Qwendex CLI functions but is not an equivalent enforced
Manager runtime. Candidate version `0.6.0-rc.1` is prepared without tagging or
publishing.

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

Update: when a generated development runtime is available, default managed hook
commands target `$QWENDEX_DEV_ROOT/scripts/qwendex`, the same runtime used by
Qdex preflight. Verification rejects a source/dev command split before launch
and names the mismatched lifecycle events, so an operator can explicitly
reinstall the managed entries instead of entering a session that will fail at
its first hook.

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

Update: the immutable launch hash also seals Local enabled state and the
launch-relevant local routing and eligibility configuration. Prompt admission
uses the recorded launch availability rather than re-reading or probing the
global Local setting. Manager, Kaveman, or Local changes therefore appear as
desired policy drift while the active session remains valid under its original
snapshot.

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
`.qwendex-dev/state/release_verify_qwendex.sqlite` for strict release checks,
and writes its Codex status evidence to the release run's metadata directory
rather than the shared operator status file.

Reason: release readiness should validate the source checkout and packaged
contracts, not fail because an operator previously toggled Manager Mode in the
normal dev state without an active lane. Release verification must also not
rewrite the live TUI/status surface while producing receipts.

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

## Stable Manager Runtime Identity

Decision: Qdex preflight and managed hooks bind a Manager runtime identity to
the canonical resolved location of `qwendex_cli.py`, represented as a path
digest. They do not bind it to that mutable file's content digest. Default
generated hooks use the same dedicated runtime that Qdex preflight uses. An
attached session may legitimately edit Qwendex itself; later hook processes
must keep using the same runtime location rather than rejecting that in-place
edit as a runtime substitution. The launch PID/start ticks/nonce/key,
repository, Codex-home, state/ledger locations, policy, and verified-hook
checks remain fail-closed.

Reason: a file-content identity changes after a valid Qwendex self-edit. That
made the next hook report `runtime_mismatch` and blocked every remaining tool,
including the checks needed to finish the edit. A resolved runtime-location
identity still rejects a different launcher/runtime path while preserving the
expected managed-session workflow.

## Reproducible Codex Build Inputs

Decision: the Codex `0.144.0` build contract explicitly runs `cargo metadata`
with an empty Cargo home to normalize the release workspace package versions in
`Cargo.lock`, then pins that deterministic lock digest and a `git diff
--full-index` Qwendex patch digest. The only permitted lockfile change is that
normalization; all Qwendex source changes remain confined to the declared TUI
and model-cache files. Before a dev Codex binary exists,
`qwendex-dev codex-source patch` and `preflight` use the verified main Codex
binary for version detection, then the build produces the dedicated binary.

Reason: the prior metadata probe used `--no-deps`, so it never performed the
normalization represented by the pinned lock digest. Its patch hash also used
abbreviated Git object IDs, which varied by checkout configuration despite
identical source files. Fresh release builds then failed before compilation
while old local receipts masked the drift. The explicit metadata step and
full-index diff restore a reproducible, fail-closed build boundary and keep
unexpected lockfile mutation blocked.

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

## Qdex Permission Resolution And Snapshot

Decision: the public and sample Qwendex config declare
`qdex.permission_mode: workspace-write`. Qdex resolves permission mode in this
order: its explicit CLI option, `QWENDEX_QDEX_PERMISSION_MODE`, the ignored
operator-local Qdex JSON, the published config, then a hard
`workspace-write` fallback. Only explicit `yolo` adds Codex's approval/sandbox
bypass flag, and it adds it exactly once. Invalid explicit CLI, environment,
or operator-local values fail before Codex begins. A Manager preflight records
the resolved mode and source in its decision and receipt; hook-time identity
comparison blocks a session if that snapshot changes.

Reason: published source must be safe and reproducible while an operator still
needs a deliberate local Yolo choice. Treating the operator file as runtime
input would leak machine policy into generated source or release artifacts;
treating permission as a mutable environment value after preflight would let an
active session change authority without a new launch receipt.

## Prompt-Aware Manager Health

Decision: Manager health is based on the attached turn rather than idle worker
capacity. A Manager Mode session with no attached prompt is healthy `standby`,
and an attached direct/trivial turn is healthy without a lane. An attached
complex turn blocks only when its required lanes are missing or unresolved.
Stale writer sessions block in every health mode until integration or explicit
operator closure.

Reason: a persistent requirement for an idle worker made an otherwise safe
Manager control plane report false failures. Conversely, missing required work
or an abandoned writer is an actual lifecycle risk and must not downgrade to an
advisory warning merely because a status command is non-release.

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

## Installed Qdex Upgrade Trust Anchor

Decision: `qwendex-dev sync` never accepts the deprecated generated
`$QWENDEX_DEV_ROOT/bin/codex` path as `QWENDEX_MAIN_CODEX_BIN`. This applies
even when that value is inherited explicitly from an older generated
environment or wrapper. Sync instead rediscovers upstream Codex from `PATH`,
regenerates the ignored internal runtime, and reinstalls the public
`~/.local/bin/qdex` launcher. The release smoke invokes that installed launcher
from a different repository with native `-C` syntax.

Reason: an upgrade can execute new source through an older generated wrapper.
Environment inheritance must not let a removed Qwendex wrapper masquerade as
the upstream recovery binary, because sync would delete the wrapper after
embedding its path and leave installed Qdex unable to start.

## Immutable Native Manager Delegation Runtime

Decision: Agent Manager duty is orthogonal to model reasoning. Every non-Off
Qdex launch runs prompt-aware preflight and seals one content-hashed policy;
Lite and Medium treat missing managed hooks as advisory, while Heavy and
Manager fail closed. A deterministic classifier and planner select bounded
read-only worker lanes, the root remains the sole default writer, exact Codex
V2 task/parent identities bind native workers to planned ledger rows, and Stop
closes only after required final reports and post-edit validation. Global mode
changes mark a live session drifted and restart-required without changing its
authority. Ultra retains native proactive delegation while Qwendex retains the
cap, lifecycle, root-only management, and duplicate-start controls.

The supported Codex patch exposes exact SubagentStart task and parent identity,
hides management tools from V2 children, and makes V2 ignore the legacy
`agents.max_threads` setting in favor of its own ceiling. Qdex stores status and
preflight handoff files per launch, so concurrent repositories cannot overwrite
one another's authority metadata. Stock Codex remains an explicit Off-mode
recovery boundary and does not carry these Manager guarantees.

The native Ultra proactive source is sealed before both `codex-status` and
Manager preflight hashing. Qdex omits its custom mode hint only after those two
surfaces agree on the same Ultra-aware policy hash; disagreement fails the
launch boundary.

The release Codex-build allowlist names the complete canonical patch surface,
including the core configuration, hook runtime, tool-spec, and hook-schema
files used by Manager delegation. A successful build is not release evidence
when that surface is absent from or exceeds the source-bound allowlist.

Reason: mutable launch policy, prompt-free planning, guessed native identities,
shared preflight files, and child-visible management tools each permit a live
session to diverge from the policy the operator approved. Sealing authority,
keeping planning deterministic and privacy-minimized, and requiring lifecycle
evidence makes delegation auditable without conflating reasoning effort with
the decision to delegate.

## Native Wait Bounds And Deterministic Patch Bytes

Decision: Qdex maps the sealed `AgentPolicy.wait_timeout_ms` into final Codex
V2 minimum, default, and maximum wait settings after caller configuration. The
Manager maximum is 60 seconds and the non-Off product ceiling is 120 seconds;
Off sets all three values to zero. A fresh pinned Codex source apply must also
reproduce the canonical binary full-index patch digest after isolated
`Cargo.lock` normalization. Patch templates therefore preserve the exact
canonical byte form even when an alternate Rust layout would be semantically
equivalent.

Reason: a repeated production live trial reached its 900-second outer timeout
inside one native wait after every receiver had already terminated. The
Manager ledger was healthy, but its lifecycle budget had not been connected to
Codex's independently configurable wait ceiling. The same production run found
that two formatting-only patch-template changes produced a noncanonical fresh
source digest and correctly blocked installation. Binding native waits to the
sealed policy and restoring byte-identical patch output closes both gaps
without changing the canonical Codex patch or binary contract.

## Install Acceptance Runtime Authority

Decision: fresh-install and upgrade acceptance resolve the selected generation
through the sync-installed shell recovery command's canonical runtime status
validator. They require the selected generation to be integrity-valid with
`status: validated` and a sealed manifest whose result is `pass`; the harness
does not maintain a parallel manifest-validity flag.

Reason: production install acceptance built and activated a valid generation
but then consulted a removed `generation.json.validated` boolean. The stale
harness predicate falsely reported that no selected generation existed after a
successful fresh build. Reusing the same standard-library validator as runtime
status and rollback keeps install evidence aligned with the activation trust
boundary and prevents schema drift between product and acceptance code.

## Legacy Upgrade Fixture Bootstrap

Decision: the isolated v0.5.7 upgrade fixture bootstraps that release's pinned
user dependencies with `qwendex_install_deps --install --no-system --json`
before its first sync and preflight. It then follows the v0.5.7 public workflow
by explicitly installing and verifying managed hooks against the generated
isolated `QWENDEX_CODEX_HOME` (the legacy environment variable that Qdex maps
to `CODEX_HOME`). The candidate still performs no system package writes, and
upgrade evidence records each legacy bootstrap command.

Reason: an empty isolated home is not an already installed v0.5.7 environment.
Checking it before running the old installer falsely blocked on the expected
absence of the legacy user-scoped Python pins. Running the public installer
creates the realistic old baseline whose state, preflight, candidate migration,
and rollback the acceptance gate is intended to validate. Hook installation is
equally part of that baseline: v0.5.7 intentionally blocks Manager launches
without verified lifecycle hooks and does not install them implicitly on sync.
After candidate sync, an empty isolated Manager state is healthy when status is
`standby`; acceptance additionally requires Manager mode, no errors, and ready
single-writer safety instead of treating the absence of sessions as a failure.

## Normal Codex Version-Cache Isolation

Decision: validated Qwendex generations and the dev seed home share only the
authentication file by symlink. They copy `version.json` and `installation_id`
into the isolated Codex home. Normal-home acceptance hashes stable upstream
control files (`config.toml`, `hooks.json`, and `installation_id`); full
isolated decoy trees still include and protect their version caches and all
sentinel files.

Reason: Codex may rewrite `~/.codex/version.json` during an ordinary concurrent
version check. Sharing that volatile cache let a Qwendex runtime affect the
normal home and also made a long isolation comparison fail because an unrelated
normal Codex process refreshed it. A generation-local copy removes the write
path, while excluding the volatile host cache from the cross-process snapshot
preserves a deterministic control-file claim. Authentication remains shared
intentionally so the isolated runtime can use the operator's login.

## Bounded Verifier Revalidation And Empty-Wait Termination

Decision: when a required verifier returns failed or pending evidence and the
Manager root subsequently changes the workspace, the root may issue exactly one
`followup_task` to that same verifier for a final-state check. The existing
agent and ledger identity is reused; a duplicate verification lane is not
spawned. If the revalidation does not pass, the turn remains blocked with the
remaining risk disclosed. Qdex root guidance requires `list_agents` before a
wait and after a timeout. The canonical Codex patch makes `wait_agent` return
immediately when no child is pending or running and explicitly forbids an empty
retry loop.

Reason: a production live verifier correctly found a defect, then terminated
with pending validation. After the root repaired and tested the defect, strict
duplicate-lane admission prevented a replacement worker, while the model
repeated mailbox waits for agents that did not exist until the 900-second outer
trial expired. Reusing the native follow-up turn preserves independent
final-state verification without weakening the verifier gate or duplicating a
lane. The native no-running-agent check closes the control-flow hole at the
runtime boundary while retaining the sealed 10/30/60-second Manager wait
budget.
