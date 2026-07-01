# Release Notes

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
