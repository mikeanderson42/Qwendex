# Qwendex Dev Environment

`scripts/qwendex_dev_env` manages the dedicated Qwendex development worktree at
`~/qwendex-dev`. It is for working on Qwendex itself while keeping the current
system Codex install available as the fallback execution plane.

## Create Or Refresh Runtime Wiring

Install or check host dependencies first:

```bash
scripts/qwendex_install_deps --install
scripts/qwendex_install_deps --check --json
```

The installer is best-effort and non-interactive. It installs user-scope Python
tools (`pytest`, `ruff`), Rust tooling through `rustup`/`cargo`, `ripgrep`, and
the Codex CLI through npm when available. It also attempts system package
installs for required host tools such as `git`, `rsync`, `curl`, `python3`, and
`tmux` when a supported package manager and non-interactive sudo/root access are
available. Remaining blockers are reported in JSON instead of being hidden.

```bash
scripts/qwendex_dev_env sync
```

In worktree mode, `sync` refreshes wrappers, env files, Codex home config, and
status files. It does not overwrite source files. The legacy generated-copy path
is retained only through explicit repair commands.

Generated state lives under:

```text
~/qwendex-dev/.qwendex-dev/
```

That folder contains isolated Qwendex state, receipts, Codex home, status file,
toolchain/build placeholders, and `env.sh`.

## Use It

```bash
source ~/qwendex-dev/.qwendex-dev/env.sh
qwendex-dev
qwendex-dev open
```

Bare `qwendex-dev` opens Codex in `~/qwendex-dev` with the current Codex
yolo-equivalent flag, `--dangerously-bypass-approvals-and-sandbox`. Use
`qwendex-dev open` for the same Qwendex dev wiring without yolo mode.

Codex sessions launched this way receive a dev-mode instruction to act as a
senior Qwendex project developer: keep edits scoped, verify before staging, and
keep generated `.qwendex-dev` state out of public artifacts. The instruction
also tells sessions to recover after context compaction from the newest user
request, re-run Qwendex posture checks, inspect existing diffs/state before
editing, and use `qwendex-dev snapshot` plus `scripts/qwendex context
snapshot|reminder|compact-plan` at phase boundaries or before manual
compaction. Manager Mode sessions must check Agent Manager/Kaveman/Local state
at the start of substantial tasks and use manager planning/preflight first.
Spawn bounded subagents only when current tool policy, task shape, and
write-surface separation make delegation safe. Keep critical-path
implementation local, avoid duplicate/conflicting writes, treat subagent output
as advisory until integrated and verified, record or state the direct-work
reason and validation path when subagents are not used, and close both spawned
agents and matching Qwendex manager sessions after integration.

The dev environment exposes these wrappers in `~/qwendex-dev/bin`:

- `qwendex`
- `qwendex-dev`
- `llmstack`
- `codex`
- `codex-main`

The `codex` wrapper first uses `QWENDEX_DEV_CODEX_BIN` when set, then
`~/qwendex-dev/.qwendex-dev/codex-build/bin/codex` when present, and finally the
current system Codex binary. This keeps the larger main Codex install available
while a patched/dev Codex build is being prepared.

The dev launcher uses an isolated `CODEX_HOME` at
`~/qwendex-dev/.qwendex-dev/codex_home`. `qwendex-dev status-json` records the
active hook source count for that home and also reports whether global
`~/.codex/hooks.json` exists. If global hooks exist but the isolated dev home has
none, the dev status JSON includes a warning so hook behavior is not silently
lost after a TUI refresh.

## Developer Lifecycle

Work inside `~/qwendex-dev`, then use the dev commands to move changes through
review, verification, and staging:

```bash
qwendex-dev bootstrap
qwendex-dev doctor
qwendex-dev status
qwendex-dev review
qwendex-dev verify --tier quick
qwendex-dev verify --tier full
qwendex-dev verify --tier release
qwendex-dev diff
qwendex-dev stage
qwendex-dev snapshot
```

- `bootstrap` runs `scripts/qwendex_install_deps --install --json`, writes
  `.qwendex-dev/results/meta/install_deps.json`, then writes
  `.qwendex-dev/results/meta/bootstrap.json`.
- `doctor` writes subsystem readiness to
  `.qwendex-dev/results/meta/dev_status.json`.
- `verify --tier quick|full|live|release` runs progressively stricter gates.
- `diff` shows native git status and diff information for the dev worktree.
- `stage` verifies quick gates, then stages managed Qwendex paths in the dev
  worktree.
- `snapshot` writes a small state receipt under
  `~/qwendex-dev/.qwendex-dev/snapshots/<UTC>/`.

Use `stage --skip-verify` only when a verification result has already been
captured for the same promoted changes.

Fallback copy commands are intentionally explicit:

```bash
qwendex-dev repair-copy --force
qwendex-dev export-to-source --dry-run
```

Do not use them as the normal worktree workflow.

## Patch Codex Source

Use the normal Qwendex patch contract from inside the dev environment:

```bash
qwendex-dev codex-source sync
qwendex-dev codex-source patch
qwendex-dev codex-source preflight
qwendex-dev codex-source build
```

Unknown Codex versions or moved anchors block before writing. After building a
patched Codex binary, either place it at:

```text
~/qwendex-dev/.qwendex-dev/codex-build/bin/codex
```

or export:

```bash
export QWENDEX_DEV_CODEX_BIN=/path/to/patched/codex
```

## Check Tooling

```bash
qwendex-dev tools
```

This checks `cargo`, `rustfmt`, and the active Codex wrapper.

The dependency manifest lives at `config/qwendex/dependencies.json`.

## Knowledge Pack

Development rules and release context live under `docs/development/`, and
Qwendex-specific Codex skills live under `.codex/skills/`.
