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
- Added manager-mode policy with `Ctrl+Shift+M` shortcut declaration, subagent
  count, and stale-agent cleanup guidance.

Known limitations:

- Live local Qwen checks require the local stack to be running.
- Auto routing falls back to the configured primary seat when local Qwen is not
  visible; it does not make Qwen release authority.
- Qwen is not release authority.
- SkillOpt adoption remains staged and review-gated.
