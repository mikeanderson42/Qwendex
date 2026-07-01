# Release Notes

## 0.1.0-rc.2

- Tightened Manager Mode lifecycle checks so stale read-only lanes are
  reconciled during status refreshes, while stale writer lanes remain blocked
  until the operator integrates or explicitly stops them.
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
- Added smoke coverage for stale manager reconciliation, stale writer blocking,
  Kaveman TUI patch injection, and dev-hook visibility.

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
- SkillOpt adoption remains staged and review-gated.
