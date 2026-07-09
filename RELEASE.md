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
