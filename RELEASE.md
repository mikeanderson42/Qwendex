# v0.5.7

Qdex managed-session continuity hotfix.

- Binds Manager runtime identity to the canonical Qwendex runtime location,
  rather than mutable source-file contents, so a valid in-place Qwendex edit
  does not strand its attached Manager session.
- Makes generated development hooks invoke the same dedicated Qwendex runtime
  that Qdex preflights, and rejects a source/dev hook-runtime split before a
  Manager session starts.
- Restores fresh Codex `0.144.0` build reproducibility by explicitly
  normalizing its release lockfile and hashing its patch with full Git object
  identifiers before validating the pinned inputs.
- Adds end-to-end regression coverage for a source edit between managed hooks.
- Existing Qdex sessions must exit and relaunch after upgrade; reinstall and
  verify managed hooks after `scripts/qwendex_dev_env sync`.

# v0.5.6

Installed Qdex upgrade-path repair release.

- Rejects the removed Qwendex `bin/codex` wrapper as an upstream Codex
  fallback even when an older generated environment exports that path.
- Regenerates the isolated internal runtime with the real upstream Codex and
  reinstalls `~/.local/bin/qdex` during `qwendex-dev sync`.
- Adds a cross-repository regression that launches the installed `qdex -C`
  command after migrating a legacy wrapper environment.

# v0.5.5

Qdex launch-boundary and Manager trust repair release.

- Keeps ordinary upstream `codex` resolution and the caller's normal
  `CODEX_HOME` unchanged when the Qwendex environment is sourced.
- Moves the patched/upstream fallback selector to an ignored internal runtime
  launched only by `qdex`; `qdex -C <project>` remains the canonical explicit
  directory form and native arguments pass through unchanged.
- Rejects Manager prompts before model work when the live PID/start identity,
  repository, preflight ledger, root identity, or policy binding is absent or
  mismatched.
- Allows an untrusted process to stop without attaching to or mutating a
  Manager decision, preventing repository-only Stop recovery loops.
- Adds sanitized `manager launch-status --pid ... --repo-root ... --json`
  health for generic downstream process supervisors.

# v0.5.4

Codex-native working-directory pass-through release.

- Makes plain `qdex` inherit the caller's working directory without injecting
  a redundant `-C`, matching a normal `codex` launch.
- Makes `qdex -C <project>` the canonical explicit-directory form and preserves
  the exact native option/value passed to Codex while aligning Manager and MCP
  scope to the same canonical directory.
- Retains Qdex-only `--repo` as a compatibility alias for existing automation.

# v0.5.3

Codex-compatible launcher and mixed-version runtime isolation release.

- Preserves native Codex arguments such as `exec --json`, `-C`/`--cd`,
  `--add-dir`, directory-valued prompts, and non-git working directories while
  keeping Qwendex manager scope aligned with the effective Codex directory.
- Makes help/version inspection stateless and clean, and makes the sourced
  `codex` command select the Qwendex wrapper while retaining `codex-main` as
  the upstream escape hatch.
- Carries Qwendex's verified Manager preflight into Codex's hook-trust bypass
  so persistent lanes do not stop at a redundant review dialog.
- Passes the canonical `--repo` target as a per-launch trusted Codex project so
  automated lanes cannot lose their first prompt to directory onboarding.
- Gives the patched Codex build a version-specific model-cache file so older
  live clients cannot overwrite the active model catalog in a shared home.
- Strips unneeded symbols from packaged Codex and code-mode-host binaries and
  records pre/post-package byte counts in the build receipt.

# v0.5.2

Manager root ownership and Codex code-mode runtime contract hotfix.

- Derives Manager root ownership from the repository-scoped `qdex` preflight
  ledger, matching Codex `0.144.0` lifecycle events that omit root `agent_id`.
- Uses bounded per-tool root leases, successful `PostToolUse` cleanup, Stop
  fallback, and dead-launch reclamation without stealing a live launch lease.
- Keeps native workers strict to active registration, current task, repository,
  and registered path scope while leaving non-Manager root execution unchanged.
- Upgrades Qwendex-managed hooks in place, preserves unrelated handlers, and
  classifies goal/plan bookkeeping plus bounded inspections without false write
  locks.
- Installs and validates `codex-code-mode-host` beside the patched Codex binary.

# v0.5.1

PEP 668 clean-install hotfix.

- Fixes externally managed Python detection in the dependency installer so
  CachyOS, Arch, and other PEP 668 systems receive the required pip override.
- Keeps Python dependency writes restricted to the user site while installing
  the exact pinned jsonschema, pytest, and ruff versions.
- Adds a regression that executes the embedded PEP 668 probe against a
  deterministic managed-Python marker instead of mocking away its body.

# v0.5.0

Qwendex manager, bridge, and release-integrity hardening release.

- Ships the generic local-Qwen Responses bridge v2 with bounded parsing,
  correct streaming/non-streaming responses, guard recovery, and live
  fresh-home Codex acceptance.
- Binds three separate live proofs when requested: canonical `/status` launcher
  preflight (`live_launcher`), exact parsed assistant `QWENDEX_OK`
  (`live_reliability`), and a fresh isolated Codex home completing one to three
  bounded successful `TOOL_OK` commands while a normal-home decoy remains
  unchanged (`live_codex_acceptance`). The live binary digest and size must
  match the validated Codex build receipt.
- Binds Codex to the same normalized `<bridge-base>/v1` endpoint that passed
  preflight and scopes qdex execution/add-dir/MCP trust to the selected target
  repository instead of the Qwendex source checkout.
- Scopes Manager decisions, sessions, validation debt, and file locks by
  repository and by root turn so stale cross-project state or prior verifier
  evidence cannot satisfy a new edit.
- Enforces local-off and GPT authority, active-agent limits, atomic state and
  digest-verified receipts, and fail-closed legacy-state handling.
- Builds the patched Codex `0.144.0` binary from an isolated archive plus an
  exact allowlisted patch, with bound source, lockfile, toolchain, preflight,
  and binary evidence.
- Replaces the legacy release summary with per-run receipt binding, tracked-
  artifact/privacy scans, CI and install acceptance, and strict remote/default-
  branch/annotated-tag publication gates.
- Removes downstream-specific workflows, machine-local launchers, private
  model inventory, and raw historical transcripts from the public artifact.

# v0.4.0

Codex 0.144.0 patched launcher release.

- Adds Codex CLI `0.144.0` / `rust-v0.144.0` to the supported TUI patch
  manifest.
- Updates the Qwendex dev source sync default so patched Codex rebuilds target
  the installed `0.144.0` CLI.
- Publishes release metadata as `0.4.0` while retaining older Codex patch
  manifests for compatibility checks.

# v0.3.2

Kaveman policy enforcement release.

- Promotes Kaveman into the effective AgentPolicy output policy so the policy
  hash, env exports, manager status, and codex-status payloads change when
  terse output is enabled.
- Carries the Kaveman output policy through managed prompt/subagent hooks,
  agent plans, and manager preflight workflow receipts.
- Keeps the release metadata aligned at `0.3.2` and documents the connected
  policy/workflow enforcement path.
- Adds an optional native `open-webui-local.service` fallback for the local
  stack Open WebUI launcher when `powershell.exe` is unavailable.

# v0.3.1

Manager orchestration and compatibility hardening release.

- Keeps explicit `--mode manager` preflight selection authoritative and exposes
  Qwendex model/reasoning assignments in spawn instructions and hook context.
- Injects model policy plus the Kaveman directive into managed hooks while
  keeping local token-saver context aligned with Local off/on.
- Hardens release guards for `gh release` option forms, `delete-asset`, `new`,
  and protected branch refspecs, while avoiding read-only search false
  positives in write detection.
- Keeps dev Codex patched keymaps opt-in through
  `QWENDEX_DEV_ENABLE_PATCHED_TUI_CONFIG=1`.

# v0.3.0

Manager Mode orchestration verification release.

- Publishes exact Qwendex release metadata for `0.3.0` and tag `v0.3.0`.
- Carries forward Manager Mode and agent orchestration verification, including
  manager status, active agent ledger state, and release-lane final-report
  gating.
- Preserves forced-local smoke test isolation by keeping local-Qwen availability
  tests on per-test `QWENDEX_STATE_DB` and `QWENDEX_RESULTS_ROOT` paths.

# v0.0.2-rc4

Manager hook compatibility and rc4 readiness candidate.

- Keeps managed Codex hooks on raw `--codex-hook-output` responses so Stop,
  PreToolUse, UserPromptSubmit, and subagent lifecycle hooks do not leak the
  Qwendex diagnostic envelope to Codex.
- Makes Manager Stop finalization recover from the latest compatible preflight
  ledger when qdex wrapper exports are unavailable, and keeps repeated Stop
  hooks idempotent after a decision is closed.
- Generates managed hook commands with embedded Qwendex runtime env and
  resolves Codex-home path digests so Stop hooks still reach the intended
  state DB, ledger DB, and receipt root when the host drops exported state
  variables or invokes hooks through a symlinked dev path.
- Tightens PreToolUse write detection so shell comparisons inside quoted
  commands are not mistaken for file redirects, while real redirects still
  require agent/file-lock metadata.
- Refreshes release metadata to `0.0.2-rc4` and preserves the rc3 qdex
  preflight orchestration hardening.

# v0.0.2-rc3

Manager preflight and qdex orchestration release candidate.

- Fixes managed Codex hook stdout so installed Qwendex hooks emit the raw
  Codex event schema instead of the diagnostic Qwendex CLI envelope.
- Adds the tracked `scripts/qdex` launch wrapper for selected repositories.
- Records Manager preflight decisions before Codex launch, including hook
  posture, routing reason, verifier requirement, validation plan, and STOP
  status.
- Blocks Manager launches with missing or partial Qwendex Codex hooks unless an
  explicit unhooked override is used.
- Preserves the selected Manager Mode preflight contract even when env
  agent-use selectors change the effective `AgentPolicy`.
- Tightens direct-work finalization so `Validation: not run` cannot close an
  edit path.

# v0.1.0-rc.5

Codex 0.143.0 launcher release candidate.

- Updates the default patched Codex source target to `rust-v0.143.0`.
- Adds Qwendex patch-manifest support for Codex CLI `0.143.0`.
- Keeps the dev launcher wired to prefer the rebuilt patched Codex binary.

# v0.1.0-rc.4

Agent Management release candidate.

- Adds runtime-enforced AgentPolicy selection for Lite, Medium, Heavy, and
  Manager modes.
- Adds manager-ledger aliases, lifecycle gates, bounded close/tombstone paths,
  raw-output preservation, write locks, team planning, managed hook config, and
  agent metrics.
- Keeps stock Codex tool-registry filtering and automatic/global hook
  installation labeled as integration boundaries.

# v0.1.0-rc.1

Initial Qwendex release candidate.

- Adds the public Qwendex CLI, manager mode, routing, eval, receipt, and estimate
  surfaces.
- Includes local-Qwen bridge parser recovery, runtime guard, marker suppression,
  and local stack launchers.
- Ships public docs and a sanitized max-depth validation summary.
- Keeps machine-local paths in `local_harness.env.sample` and runtime state out
  of git.
