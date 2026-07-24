# Release Notes

## Unreleased

## 0.6.5

`v0.6.5` completes the Codex `0.145.0` Qdex memory boundary without enabling
any experimental retained-state capability.

- Disables `external_agent_memory_import` and `chronicle` in the generated
  home and trailing launch policy. The legacy Chronicle feature alias
  `telepathy` is covered by the same launch rejection.
- Rejects feature and config activation attempts before the internal runtime
  starts, alongside the existing history, memories, app-server, remote, and
  native-role controls.
- Clarifies that Qdex's explicit V2 session cap takes precedence over the
  upstream legacy `[agents].max_threads` fallback. Root-only V2 governance and
  the deferred native role contract are unchanged.

## 0.6.4

`v0.6.4` refreshes the canonical patched-Codex compatibility contract for
Codex `0.145.0` and makes the release's privacy and role boundary explicit.

- Pins `rust-v0.145.0`, its source commit, the full-index Qwendex patch digest,
  and the normalized Cargo.lock digest as one reproducible build contract.
- Keeps Qdex's explicit V2 session cap authoritative over Codex's legacy
  `[agents].max_threads` fallback without retaining the obsolete upstream
  configuration patch.
- Sets `history.persistence = "none"`, disables the `memories` feature, and
  disables memory generation, use, and dedicated tools in both generated and
  per-launch Qdex configuration.
- Does not activate native Codex role/profile configuration or role-driven
  model/reasoning overrides. Qdex rejects project-native role surfaces and
  app-server/remote access, and the canonical V2 patch keeps child settings
  inherited from the root; the existing Qwendex AgentPolicy and root-only
  lifecycle boundary remain the supported delegation surface.
- Requires `@openai/codex@0.145.0` in the dependency helper while retaining
  stock Codex for standalone Qwendex CLI and Off-mode recovery.

## 0.6.3

`v0.6.3` validates the supported Codex TUI controls for Agent Manager, Kaveman,
and Local routing against per-launch Qwendex state.

- Connects `Alt+M`, `Alt+K`, and `Alt+L` to the canonical Qwendex manager
  commands and refreshes the `qwendex-manager` footer after each toggle.
- Covers the complete Agent Manager duty cycle, Kaveman next-turn policy
  snapshots, Local fail-closed routing, per-launch isolation, and Local
  restart adoption in the smoke and release-contract suites.
- Shows an explicit Local restart marker when a running launch has a different
  native Local snapshot from the newly requested control state.
- Verifies the source-patched Codex `0.144.6` binary with an isolated PTY
  smoke, including all three hotkeys and their status-file updates.

## 0.6.2

`v0.6.2` refreshes the canonical patched-Codex compatibility contract for
Codex `0.144.6`.

- Pins `rust-v0.144.6`, its source commit, the full-index Qwendex patch digest,
  and the normalized Cargo.lock digest as one reproducible build contract.
- Rebuilds and validates the patched Codex binary and matching
  `codex-code-mode-host` companion against that contract.
- Requires `@openai/codex@0.144.6` in the dependency helper and updates public
  compatibility, runtime, and validation documentation accordingly.
- Keeps standalone Qwendex CLI, routing, receipts, checks, and Off-mode
  recovery supported with stock Codex.

## 0.6.1

`v0.6.1` is the stable release of the advisory Agent Management boundary
required for normal Qwendex operation.

- Reframed Agent Management as advisory delegation and lifecycle observability.
  Manager metadata no longer authorizes or blocks prompts, root tools,
  publish/release commands, or final responses.
- Preserved native `multi_agent_v2` capacity/depth/wait limits, root-only
  management, no recursive child management, explicitly read-only child lanes,
  Local routing, and Codex sandbox/Yolo selection.
- Suppressed Codex's expected unstable-feature startup warning in generated
  Qdex homes while keeping `multi_agent_v2` enabled.
- Removed process-environment release approval and unhooked-override variables
  from the supported workflow; explicit user intent and Codex/host permissions
  remain authoritative.

## 0.6.0-rc.4

`v0.6.0-rc.4` was the prerelease publication of the advisory Agent Management
boundary, superseded by the stable `v0.6.1` release.

## 0.6.0-rc.3

`v0.6.0-rc.3` updates the supported canonical Codex patch to `0.144.4` and
contains only corrective fixes needed for this release.

- Pins the official `rust-v0.144.4` source commit, full-index Qwendex patch
  digest, normalized Cargo.lock digest, dependency helper, and versioned model
  cache to the tested 0.144.4 contract.
- Revalidates the Qwendex TUI, hooks, root-only collaboration controls,
  bounded wait behavior, and model-cache patch against the 0.144.4 source.
- Fixes the Manager state-schema v2→v3 upgrade so preexisting databases gain
  Qdex permission-provenance columns before decision writes.
- Clears inherited stale runtime pins before a new top-level Qdex selector
  chooses its current immutable runtime generation.
- Keeps direct CLI smoke fixtures from inheriting an operator's Qdex permission
  posture, preserving published-contract coverage in managed environments.

## 0.6.0-rc.2

`v0.6.0-rc.2` is a corrective prerelease; the published `v0.6.0-rc.1` tag
remains immutable.

- Corrected the public Qdex permission-mode contract. `workspace-write` is the
  schema, published-config, and hard-fallback default; only an explicit CLI,
  environment, or ignored operator-local setting selects Yolo.
- Corrected the public launch wording and the legacy dry-run fallback so
  `workspace-write` never silently reports or appends Yolo's bypass flag.
- Preserved Mike's ignored operator-local Yolo configuration and the existing
  source/runtime/artifact boundary that keeps it out of public material.
- Retained rc.1's run-scoped release status, healthy Manager standby/direct-work
  wording, and the precise stock-Codex versus patched-Manager support boundary.

## 0.6.0-rc.1

The annotated `v0.6.0-rc.1` tag and GitHub prerelease are created after the
source-bound release gates and CI attestation pass; untagged source remains
candidate material.

- Made Qdex permission mode a published, schema-validated `workspace-write`
  default with explicit CLI, environment, and ignored operator-local Yolo
  overrides. Launch receipts snapshot the resolved mode and source; invalid
  explicit overrides fail before Codex starts.
- Made release verification's Codex status evidence run-scoped, so strict
  release checks cannot overwrite an operator's shared live status file.
- Made idle Manager Mode healthy standby and attached direct work healthy;
  missing/unresolved required lanes and stale writers now block consistently.
- Clarified the product boundary: Qwendex standalone CLI functions, checks,
  routing, receipts, and Off-mode recovery work with stock Codex. Enforced
  Heavy and Manager guarantees require the supported canonical patch.

- Added immutable runtime generations that bind Qwendex source, hooks, Codex
  patch and binary pair, config/schema, and state schema. Activation affects
  new sessions only; rollback works from a standalone shell recovery copy.
- Added `qwendex manager accept --profile offline|live|production` with explicit
  run IDs, source-bound evidence, repeated non-Ultra Manager trials, Ultra
  coexistence, concurrency, fault injection, security, migration, performance,
  fresh-install, v0.5.7-upgrade, rollback, and normal-Codex isolation gates.
- Reproduced the earlier Qdex self-edit failure: hooks were executing mutable
  source whose content identity changed during the active session. Runtime
  generations keep active hooks and binaries immutable while allowing a next
  generation to be built beside them.
- Kept mutable worker reports outside the sealed generation tree and made
  terminal validation classification explicit, preventing bounded workers from
  being trapped in repeated SubagentStop continuation.
- Certified enforced Manager guarantees only for the tested Linux/Codex
  `0.144.0` canonical patch. Stock Codex remains supported for Off-mode
  recovery and standalone Qwendex CLI functions.

## 0.5.7

- Fixed Manager Mode sessions becoming permanently blocked with
  `runtime_mismatch` after a valid in-place Qwendex source edit.
- Bound runtime identity to the canonical Qwendex runtime location and kept the
  existing process, launch, repository, state, policy, and verified-hook
  checks fail-closed.
- Made generated development hooks invoke the same runtime Qdex preflights;
  hook verification now rejects a stale source/dev runtime split before launch.
- Restored deterministic fresh Codex `0.144.0` build provenance by explicitly
  normalizing the release lockfile and using a full-index Qwendex patch digest,
  so builds no longer depend on stale local source state or Git abbreviation
  settings.
- Upgrade by exiting active Qdex sessions, running
  `scripts/qwendex_dev_env sync`, reinstalling and verifying managed hooks,
  then starting a new `qdex` session.

## 0.5.6

- Fixed upgrades where an older generated environment supplied the removed
  Qwendex `bin/codex` wrapper as `QWENDEX_MAIN_CODEX_BIN`, leaving the installed
  Qdex launcher with a dead internal fallback.
- Made sync rediscover the real upstream Codex before regenerating the isolated
  runtime and reinstalling `~/.local/bin/qdex`.
- Added an installed-launcher smoke that runs `qdex -C <other-repo>` after the
  legacy migration.

## 0.5.5

- Preserved ordinary upstream `codex` resolution and the caller's normal
  `CODEX_HOME` after sourcing the Qwendex environment.
- Moved Qwendex's patched-binary selector and labelled upstream fallback into
  an ignored internal runtime that is invoked only through `qdex`.
- Kept canonical `qdex -C <project>` and native Codex argument pass-through,
  including arguments after `--`, while adding yolo and hook-trust flags at
  most once.
- Added fail-fast Manager prompt validation for the live process/start,
  repository, preflight ledger, root identity, Codex home, hooks, and policy.
- Made untrusted Stop events non-blocking and non-mutating instead of attaching
  to a decision by repository.
- Added a sanitized generic `manager launch-status` process-health interface.

## 0.5.4

- Made plain `qdex` inherit the caller's working directory without adding a
  synthetic `-C`, matching normal Codex launch semantics.
- Made native `qdex -C <project>` the documented repository-selection form;
  Qdex continues to observe it for Manager/MCP scope while passing the exact
  option and value through to Codex.
- Retained Qdex-only `--repo` as a compatibility alias for existing wrappers.

## 0.5.3

- Made `qdex` preserve normal Codex CLI arguments, use `$PWD` outside git,
  align `-C`/`--cd` with Manager scope, and keep help/version calls free of
  status writes and Manager preflight state.
- Made the generated environment resolve bare `codex` to the Qwendex wrapper;
  `codex-main` remains the explicit upstream fallback.
- Added Codex's hook-trust bypass to Manager launches after Qwendex verifies
  the complete managed hook set during its required preflight.
- Made the canonical Qdex target a per-launch trusted Codex project, preventing
  persistent lanes from dropping their primer into the directory-trust prompt.
- Added a patched-runtime `QWENDEX_MODELS_CACHE_FILE` contract with a
  Codex-versioned cache filename, preventing older live Qwendex clients from
  repeatedly replacing the active model catalog.
- Added release-binary stripping plus pre/post-package size evidence to the
  Codex build receipt.

## 0.5.2

- Fixed the Codex `0.144.0` Manager hook contract by deriving root ownership
  from the trusted `qdex` preflight instead of requiring the root lifecycle
  event to carry an `agent_id` that Codex intentionally omits.
- Added per-tool root leases, successful `PostToolUse` cleanup, Stop fallback,
  dead-launch orphan recovery, strict registered worker identity/task/path
  checks, and lock-free goal/plan bookkeeping.
- Made managed hook installation idempotently upgrade Qwendex handlers while
  preserving unrelated hooks, and added safe inspection classification for
  `file`, Python version probes, and allowlisted read-only pipelines.
- Installed and receipts the Codex `codex-code-mode-host` companion alongside
  the patched CLI so code-mode and goal tools cannot launch with an incomplete
  runtime.

## 0.5.1

- Fixed the dependency installer's externally managed Python probe so PEP 668
  systems add pip's explicit override while retaining `--user` scope.
- Added an executable regression for the embedded probe and verified exact
  pinned Python tools in a clean isolated CachyOS home.

## 0.5.0

- Replaced the downstream-specific local-Qwen bridge monolith with a generic
  Responses-compatible v2 bridge, including bounded request parsing, correct
  JSON/SSE behavior, runtime-guard recovery, a versioned canonical `/status`
  readiness contract, and fresh-home Codex probes.
- Bound Codex execution to the exact preflighted bridge base, blocked conflicting
  inherited endpoint overrides, and scoped qdex working/add-dir/MCP trust to
  the selected target repository.
- Made Manager decisions repository- and turn-scoped, enforced local-off/GPT
  authority rules, bounded active agents, protected legacy locks, and required
  fresh verifier evidence for every edited turn.
- Added digest-verified manager receipts, atomic shared-state writes, honest
  full-ledger aggregates, and explicit migration boundaries for legacy state.
- Added an isolated, allowlisted Codex `0.144.0` source build with bound binary,
  source-patch, lockfile, toolchain, and preflight provenance.
- Added a fail-closed release-summary v2 contract, GitHub CI, same-root install
  acceptance, full tracked-artifact/privacy scanning, and version/tag/default-
  branch/remote evidence binding.
- Made the clean-install learning mock an explicit non-mutating contract check;
  external SkillOpt remains required for status, harvest, and run, while
  `adopt --approve` performs allowlist preflight only and never applies files.
- Aligned the public 32k backend, local seats, launcher fallback, and sample
  environment on a 32768/28672 context/compaction budget with cross-field
  validation.
- Removed machine-local launchers, downstream workflow templates, private
  inventory, and raw validation transcripts from the public source artifact.

## 0.4.0

- Added Codex CLI `0.144.0` / `rust-v0.144.0` to the supported TUI patch
  manifest.
- Updated the Qwendex dev source sync default so patched Codex rebuilds target
  the installed `0.144.0` CLI.
- Published release metadata as `0.4.0`.

## 0.3.2

- Promoted Kaveman into `AgentPolicy.output_policy`, including policy-hash
  participation and `QWENDEX_OUTPUT_POLICY` / `QWENDEX_KAVEMAN_*` exports.
- Threaded the same output policy through managed prompt hooks, subagent-start
  hooks, agent plans, manager preflight receipts, manager status, and
  `codex-status`.
- Updated release metadata to `0.3.2`.
- Added an optional native `open-webui-local.service` fallback for the local
  stack Open WebUI launcher when `powershell.exe` is unavailable.

## 0.3.1

- Kept explicit `--mode manager` preflight selection authoritative and exposed
  Qwendex model/reasoning assignments in spawn instructions and hook context.
- Injected model policy plus the Kaveman directive into managed hooks while
  keeping local token-saver context aligned with Local off/on.
- Hardened release guards for `gh release` option forms, `delete-asset`, `new`,
  and protected branch refspecs, while avoiding read-only search false
  positives in write detection.
- Kept dev Codex patched keymaps opt-in through
  `QWENDEX_DEV_ENABLE_PATCHED_TUI_CONFIG=1`.

## 0.3.0

- Published exact Qwendex release metadata for `0.3.0` and tag `v0.3.0`.
- Captured Manager Mode and agent orchestration verification as release-facing
  state, including manager status, active agent ledger posture, and final-report
  gating for the release lane.
- Preserved forced-local smoke test isolation by keeping local-Qwen availability
  tests on per-test `QWENDEX_STATE_DB` and `QWENDEX_RESULTS_ROOT` paths.

## 0.0.2-rc4

- Made Manager Stop hooks tolerant of qdex export loss by attaching to the
  latest compatible preflight ledger and treating repeated Stop hooks as
  idempotent after finalization.
- Added embedded runtime env to managed hook commands and resolved
  Codex-home path digests so Stop hooks still reach the intended Qwendex
  manager ledger when Codex drops state env vars or uses a symlinked dev path.
- Kept installed Codex hook output on the raw hook schema while preserving the
  diagnostic JSON envelope for manual Qwendex CLI inspection.
- Reworked PreToolUse write detection to distinguish shell comparisons from
  real redirects, keeping file-lock enforcement for actual writes.
- Updated the release metadata to `0.0.2-rc4`.

## 0.0.2-rc3

- Fixed managed Codex hook stdout for `UserPromptSubmit` and related lifecycle
  hooks. Installed hook commands now use `--codex-hook-output` so Codex sees the
  raw event schema, while manual `agent hook ... --json` keeps the diagnostic
  Qwendex envelope.
- Tightened hook verification so stale full `--json` hook configs are not
  accepted as Manager-ready.
- Updated the release metadata to `0.0.2-rc3`.

## 0.0.2-rc2

- Added `qdex` as the dev-worktree launch wrapper for selected repositories,
  including Manager preflight before Codex starts and exported manager ledger
  IDs only after a ready preflight.
- Added the Manager decision ledger and receipts for hook posture, prompt
  digest or interactive-prompt unknown state, routing reason, verifier
  requirement, validation plan, and STOP status.
- Hardened Manager launch and finalization gates so missing or partial hooks
  block by default, selected Manager Mode cannot be bypassed by env agent-use
  selectors, stale unhooked overrides are ignored when hooks verify, and
  `Validation: not run` does not close direct edit work.
- Added managed hook install/verify commands, qdex durability checks, Manager
  preflight smoke coverage, direct-work validation coverage, and public docs
  for the supported workflow.

## 0.0.2-rc1

- Fixed Agent Manager mode consistency so the selected `Alt+M` mode is the
  default backend `AgentPolicy` source for `agent`, `manager`, `check`,
  `doctor`, `codex-status`, and native `agent hook` gates.
- Added backend policy support for `Off` and `Auto`, including Off-mode
  automatic subagent spawn rejection.
- Added smoke coverage proving selected Manager Mode blocks finalization while
  required lanes are active, and selected Off mode blocks automatic subagent
  spawning.

## 0.1.0-rc.5

- Added Codex CLI `0.143.0` to the Qwendex TUI patch manifest.
- Updated the dev launcher default source ref to `rust-v0.143.0` so
  `qwendex-dev codex-source sync`, `patch`, and `build` target the new Codex
  update by default.
- Verified the dev launcher patch/build workflow against the 0.143.0 runtime.

## 0.1.0-rc.4

- Added first-class AgentPolicy diagnostics for `--agent-use`,
  `QWENDEX_AGENT_USE`, and `CODEX_AGENT_USE`, including policy hashes and
  subprocess env exports.
- Added `scripts/qwendex agent ...` aliases for policy, status, list, inspect,
  logs, wait, close, tombstone, profiles, and team inspection over the existing
  manager ledger.
- Added `scripts/qwendex agent plan --prompt ...` to turn the effective
  AgentPolicy and built-in team roster into direct-work exceptions or concrete
  `manager assign` commands.
- Added `scripts/qwendex agent metrics --json` for ledger counts,
  final-contract compliance, raw-output artifact counts, active writer counts,
  and managed hook/profile observability.
- Added native `scripts/qwendex agent hook ...` gate evaluation for prompt
  context, subagent final-report contracts, Manager stop gates, read-only write
  denial, child recursive-spawn denial, and release/publish command approval.
- Added SQLite-backed agent file locks with `scripts/qwendex agent locks --json`
  and first-release single-writer enforcement for base-worktree writes.
- Added Manager Mode raw-output preservation under ignored `.qwendex/runs/`
  artifacts, plus compact agent outcomes in `agent logs`, `manager status`,
  `context compact-plan`, and `context pack`.
- Added `scripts/qwendex agent hook-config` to render or approval-write
  managed hook wiring that invokes the native Qwendex agent gate evaluator.
- Documented the current CLI enforcement boundary for Agent Management while
  keeping native Codex tool-registry filtering and automatic/global hook
  installation labeled as future patched-runtime integration work.

## 0.1.0-rc.3

- Fixed route receipts so `local_subagents.local_state`, availability,
  usability, and indicator text reflect the same local-Qwen probe result used
  for the actual routing decision.
- Added smoke coverage that keeps CLI version, project config, sample config,
  README, and release notes aligned on the same release candidate.
- Updated the testbench startup banner to use `Local: [Ready]` instead of the
  legacy `Local: [Y]` label.

## 0.1.0-rc.2

- Tightened Manager Mode lifecycle checks so stale read-only lanes are
  reconciled during status refreshes, while stale writer lanes become daily
  advisory warnings and strict-health blockers until the operator integrates or
  explicitly stops them.
- Added `manager close --agent-id ... --reason ... --json` as the explicit
  stop path for active or stale writer lanes.
- Made `check`, `doctor`, `manager status`, and `codex-status` share the same
  stale-session contract instead of allowing leftover active lanes to make
  Manager Mode look healthy after a TUI refresh.
- Extended the Codex TUI patch manifest so Kaveman `[Y]` is connected beyond
  the footer: the patched TUI now reads `QWENDEX_CODEX_STATUS_FILE` and appends
  the Kaveman directive to developer instructions for thread start, resume, and
  fork flows.
- Added dev-environment hook visibility reporting. `qwendex-dev status-json`
  now records active isolated `CODEX_HOME` hook sources and warns when global
  `~/.codex/hooks.json` exists but the dev Codex home has none.
- Added smoke coverage for stale manager reconciliation, stale writer advisory
  and strict-health behavior, Kaveman TUI patch injection, and dev-hook
  visibility.

## 0.1.0-rc.1

- Added `scripts/qwendex` as the public CLI boundary.
- Added stable JSON envelope fields: `status`, `summary`, `version`,
  `artifacts`, `next_actions`, and `errors`.
- Added Qwendex config schema, profiles, model catalog, and sample config.
- Added Qwen seat receipts and exact marker exec receipt.
- Added top-level `scripts/qwendex estimate` as a supported alias for
  `scripts/qwendex manager estimate`.
- Added token-saver routing with `scripts/qwendex route` and `exec --seat auto`
  so bounded work can prefer local Qwen when the guarded bridge is healthy.
- Added SkillOpt-backed learning facade with safe dry-run defaults.
- Added public docs, naming audit, link audit, and secret scan.
- Added manager-mode policy with patched-TUI `Alt+M` / `Alt+K` / `Alt+L`
  toggle declarations, `manager_deploy_policy: auto` by default, explicit
  `disabled` opt-out, Kaveman terse-output state, product subagent ceiling of
  10, and stale-agent cleanup guidance.
- Added `scripts/qwendex codex-status`, `scripts/qwendex codex-patch
  preflight`, `scripts/qwendex codex-patch apply --source`, and a versioned
  Codex TUI patch manifest for native `qwendex-manager` footer/hotkey
  integration.
- Added `scripts/qwendex_dev_env` to create `~/qwendex-dev`, sync the public
  project surface there, isolate Qwendex/Codex state, and fall back to the
  current main Codex binary until a patched/dev Codex binary is configured.
- Added `qwendex-dev review`, `diff`, `promote`, `verify`, `stage`, and
  `snapshot` so the dev copy can act as a senior project-developer lane while
  staging only managed Qwendex source surfaces back in the tracked repo.
- Promoted `~/qwendex-dev` to a git-worktree product lane with `bootstrap`,
  `doctor`, tiered `verify`, `status-json`, `clean`, `codex-source`, and
  release-summary receipts under `.qwendex-dev/results/meta/`.
- Added a development knowledge pack and Qwendex-specific Codex skills for
  maintainer, release gate, local bridge triage, and Codex patch workflows.
- Added `scripts/qwendex_testbench` for a visible local sandbox with
  `qwendex-local` and `qwendex-full` panes, Qwendex receipt/status console, and
  launch banner `>_ OpenAI Codex (v...) /w Qwendex`.
- Added `scripts/qwendex exec --cwd` and Codex MCP overrides so bench runs can
  target a project folder without inheriting stale project-local harness paths.

Known limitations:

- Live local Qwen checks require the local stack to be running.
- Auto routing falls back to the configured primary seat when local Qwen is not
  visible; it does not make Qwen release authority.
- Qwen is not release authority.
- Qwendex does not adopt SkillOpt proposals; its approved adopt action is a
  path-allowlist preflight only.
