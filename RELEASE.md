# v0.0.2-rc2

Manager preflight and qdex orchestration release candidate.

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
