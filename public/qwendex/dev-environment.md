# Qwendex Dev Environment

`scripts/qwendex_dev_env` manages runtime wiring for a dedicated Qwendex git
checkout/worktree at `~/qwendex-dev`. It does not clone or upgrade Qwendex.
Install from a tagged clone as described in the quickstart, or create a named
git worktree before using it. The current system Codex install remains
available as the fallback execution plane.

## Create Or Refresh Runtime Wiring

Install or check host dependencies first:

```bash
scripts/qwendex_install_deps --install
scripts/qwendex_install_deps --check --json
```

The installer is best-effort and non-interactive. It installs exactly pinned,
user-scope Python tools (`jsonschema`, `pytest`, `ruff`), Rust tooling through
`rustup`/`cargo`, `ripgrep`, and the Codex CLI through npm when available. On a
PEP 668 externally managed interpreter, pip's explicit managed-environment
override is used only together with `--user`; the receipt records that policy
and the installer never writes validation tools into the system site. It also
attempts system package installs for required host tools such as `git`, `rsync`,
`curl`, `python3`, and `tmux` when a supported package manager and
non-interactive sudo/root access are available. Remaining blockers are reported
in JSON instead of being hidden.

Optional interactive discovery tools are useful in a developer shell but are not
part of the Qwendex runtime contract. If `fd` and `fzf` are available, a local
operator can make fzf path picking use fd:

```bash
if command -v fd >/dev/null 2>&1 && command -v fzf >/dev/null 2>&1; then
  export FZF_DEFAULT_COMMAND='fd --type f --strip-cwd-prefix --hidden --follow --exclude .git'
  export FZF_CTRL_T_COMMAND="$FZF_DEFAULT_COMMAND"
fi
```

This enables `fzf` and, when shell key bindings are loaded, `Ctrl+T` path
insertion inside an unfinished command. Keep Qwendex scripts and validation
gates on explicit paths, `ripgrep`, and repo-owned checks. Use `plocate` only
as an optional indexed filename lookup tool; its database depends on the host's
updatedb timer or an intentional `updatedb` run.

```bash
scripts/qwendex_dev_env sync
```

In worktree mode, `sync` refreshes wrappers, env files, Codex home config, and
status files. It does not fetch, merge, check out, or overwrite source files.
The legacy generated-copy path is retained only through explicit repair
commands and is not a supported install or release workflow.

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
- `codex-main`

Sourcing `.qwendex-dev/env.sh` places this `bin` directory on `PATH` without
installing a `codex` command or exporting `CODEX_HOME`. Ordinary `codex`
therefore remains the upstream CLI with its normal home. `codex-main` is an
explicit alias for the captured upstream CLI.

The ignored internal `QWENDEX_CODEX_RUNTIME` first uses
`QWENDEX_DEV_CODEX_BIN` when set, then
`~/qwendex-dev/.qwendex-dev/codex-build/bin/codex` when present, and finally the
captured upstream Codex binary with an explicit fallback diagnostic. Only
`qdex` invokes this runtime and sets `QWENDEX_CODEX_HOME` as its child's
`CODEX_HOME`. A selected dev binary must
have an executable `codex-code-mode-host` companion in the same directory; the
wrapper blocks before launch when that Codex 0.144.0 runtime contract is
incomplete.

The isolated Qwendex Codex home links the operator's authentication file for
login continuity. Its version cache and installation identity are local copies,
so Qdex version checks cannot rewrite the upstream Codex home's cache.

The generated environment also exports a Codex-versioned
`QWENDEX_MODELS_CACHE_FILE`. The Qwendex source patch makes the active build use
that file inside the otherwise shared isolated home, so an older still-running
Codex client cannot replace the current build's model catalog. Stock Codex
ignores this extension; the patch preflight names the boundary. Release builds
strip unneeded symbols from both native binaries and record their unstripped
and packaged sizes in `codex_build.json`.

Qdex uses an isolated `CODEX_HOME` at
`~/qwendex-dev/.qwendex-dev/codex_home`. `qwendex-dev status-json` records the
active hook source count for that home and also reports whether global
`~/.codex/hooks.json` exists. If global hooks exist but the isolated dev home has
none, the dev status JSON includes a warning so hook behavior is not silently
lost after a TUI refresh.

Active `config.toml` stays stock-Codex compatible by default. Qwendex patched
TUI keymaps are written to `patched-tui.example.toml` as copy-only reference
config; they are appended to active config only when launching or syncing with
`QWENDEX_DEV_ENABLE_PATCHED_TUI_CONFIG=1` after selecting a patched Codex build.
The stock-compatible `[tui] status_line` entry remains in active config when a
dev Codex binary path is configured.

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

Unknown Codex versions or moved anchors block before writing. The build command
installs both of these sibling files:

```text
~/qwendex-dev/.qwendex-dev/codex-build/bin/codex
~/qwendex-dev/.qwendex-dev/codex-build/bin/codex-code-mode-host
```

For an external patched build, place `codex-code-mode-host` beside the binary
and export:

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
