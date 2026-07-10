# Quickstart

## Install A Tagged Release

Qwendex is distributed as a source repository, not as a Python or npm package.
Clone it directly at the default runtime root, then pin the current release:

```bash
git clone https://github.com/mikeanderson42/Qwendex.git ~/qwendex-dev
cd ~/qwendex-dev
git fetch --tags origin
git switch --detach v0.5.1
git status --short
```

Stop if `git status --short` prints unexpected files. Install the
release-compatible dependencies, create isolated runtime wiring, and load the
generated environment:

Qwendex requires Bash 4 or newer and Python 3.11 or newer. The dependency check
fails closed when the active shell or interpreter is older.
System-package installation is best-effort: a distribution may not provide a
new enough Bash or Python under its default package name, so rerun the JSON
dependency check and upgrade those runtimes through the platform's supported
package channel if it remains blocked.

```bash
scripts/qwendex_install_deps --install
scripts/qwendex_dev_env sync
source ~/qwendex-dev/.qwendex-dev/env.sh
qwendex-dev bootstrap --check
qwendex-dev doctor
scripts/qwendex check --json
```

The dependency helper requires Codex CLI `0.144.0`, the version covered by this
release's native-patch contract, and installs that version when the active
binary differs. For intentional compatibility testing, set both
`QWENDEX_CODEX_NPM_SPEC` and `QWENDEX_CODEX_REQUIRED_VERSION`.

`sync` installs the tracked `scripts/qdex` wrapper into `~/.local/bin/qdex`.
Ensure `~/.local/bin` is on `PATH`; then `qdex --repo <project>` launches the
selected project through the Qwendex Codex wrapper. By default `qdex` supplies
`--dangerously-bypass-approvals-and-sandbox` (YOLO mode). That project becomes
the Qwendex manager target, execution directory, Codex add-dir, local-harness
trusted root, and MCP trusted root. This repo binding limits those Qwendex/MCP
scopes; YOLO mode is deliberately not OS-level filesystem confinement. The
generated isolated `CODEX_HOME` overrides an inherited caller value unless the
operator explicitly sets `QWENDEX_QDEX_PRESERVE_CODEX_HOME=1`. The release tag
pins the Qwendex source. Patched Codex footer/hotkey support remains a
separately built, version-checked integration.

## Upgrade Or Roll Back

Never upgrade across a dirty checkout:

```bash
cd ~/qwendex-dev
git status --short
git fetch --tags origin
git switch --detach <new-release-tag>
scripts/qwendex_dev_env sync
source ~/qwendex-dev/.qwendex-dev/env.sh
qwendex-dev bootstrap --check
qwendex-dev doctor
scripts/qwendex eval --all --json
```

For rollback between `0.5.x` and newer compatible releases, repeat the same
sequence with the prior tag. Do not reuse newer runtime state with an older
release unless its notes explicitly allow that migration.

Releases before `0.5.0` did not support the same-root checkout layout safely.
Run those tags from separate source and runtime roots instead of switching the
active `~/qwendex-dev` checkout backward:

```bash
git clone https://github.com/mikeanderson42/Qwendex.git ~/qwendex-v0.4-source
git -C ~/qwendex-v0.4-source switch --detach v0.4.0
git -C ~/qwendex-v0.4-source worktree add --detach ~/qwendex-v0.4-runtime v0.4.0
QWENDEX_DEV_SOURCE_ROOT=~/qwendex-v0.4-source \
QWENDEX_DEV_ROOT=~/qwendex-v0.4-runtime \
  ~/qwendex-v0.4-runtime/scripts/qwendex_dev_env sync
source ~/qwendex-v0.4-runtime/.qwendex-dev/env.sh
qwendex-dev bootstrap --check
qwendex-dev doctor
```

The detached runtime worktree keeps v0.4's git-worktree health contract intact.
The distinct roots also preserve current release state and avoid importing a
newer Codex home or SQLite ledger into the older runtime.

## Baseline Check

Run the offline surface check first:

```bash
scripts/qwendex_install_deps --install
scripts/qwendex_install_deps --check --json
```

```bash
scripts/qwendex check --json
```

Start the stack when you want live local Qwen runs:

```bash
scripts/qwendex up --json
```

Run a normal local-model marker request:

```bash
scripts/qwendex exec "Reply exactly QWENDEX_OK" --json
```

The command receipt is useful operator evidence, but the release-grade live
proof is the three-part live validator suite:

```bash
qwendex-dev verify --tier live
```

That tier checks the launcher and canonical bridge status, parses an exact
`QWENDEX_OK` assistant response, and performs a fresh-home Codex tool
round-trip while confirming the normal-home decoy is unchanged.

Check the token-saver route before a live task:

```bash
scripts/qwendex route --task-class exec --json
```

Inspect the configured local Qwen seat policy:

```bash
scripts/qwendex seat qwen --json
```

This does not select a persistent seat or probe availability. Choose a seat for
an execution with `scripts/qwendex exec ... --seat qwen` or use `--seat auto`.

Run the offline eval gate:

```bash
scripts/qwendex eval --json
```

Validate the built-in, non-mutating learning mock contract:

```bash
scripts/qwendex learn dry-run --backend mock --json
```

This generates and adopts no proposal. External `skillopt-sleep` is required
for learning status, harvest, and run actions.

Inspect the latest receipt:

```bash
scripts/qwendex receipt latest --json
```

## Dev Root

Contributors should use a named git branch/worktree at `~/qwendex-dev`, not a
generated standalone copy:

```bash
scripts/qwendex_dev_env sync
source ~/qwendex-dev/.qwendex-dev/env.sh
qwendex-dev bootstrap
qwendex-dev doctor
qwendex-dev status
```

From there, bare `qwendex-dev` starts Codex in `~/qwendex-dev` with the current
Codex yolo-equivalent flag, `--dangerously-bypass-approvals-and-sandbox`.
`qwendex-dev open` starts the same Qwendex dev wiring without yolo mode. The
dev-local `codex` wrapper uses a patched/dev Codex binary when one is configured
and falls back to the current main Codex install otherwise.

Development loop:

```bash
qwendex-dev verify --tier quick
qwendex-dev diff
qwendex-dev stage
```

For release-adjacent work, use:

```bash
qwendex-dev verify --tier full
qwendex-dev verify --tier release
qwendex-dev snapshot
```

## Visible Test Bench

To test Qwendex against a project folder with both local and full Codex panes:

```bash
scripts/qwendex_testbench init
scripts/qwendex_testbench codex-preflight
scripts/qwendex_testbench tmux
```

The tmux session starts a Qwendex console plus `qwendex-local` and
`qwendex-full` Codex panes. Each Codex pane launches with a visible banner:

```text
>_ OpenAI Codex (v0.144.0) /w Qwendex
```

`codex-preflight` detects the installed Codex CLI version and checks it against
the Qwendex TUI patch manifest before a patched footer/hotkey build is treated
as connected.

## Example Workflows

Local Qwen coding run:

```bash
scripts/qwendex route --task-class exec --json
scripts/qwendex seat qwen --json
scripts/qwendex exec "Inspect scripts/qwendex_cli.py and suggest one bounded fix." --json
```

The `seat` line inspects configuration only; the `exec` route chooses and runs
the effective seat.

Read-only audit:

```bash
scripts/qwendex seat audit --json
scripts/qwendex eval --case review_current_changes --json
```

`seat audit` inspects the configured policy. `review_current_changes` is an
offline classifier fixture, not a live GPT audit of the current worktree.

Queue workflow:

```bash
scripts/qwendex eval --case mcp_queue_workflow --json
```

Learning dry run:

```bash
scripts/qwendex learn dry-run --backend mock --json
```

The built-in mock validates the envelope without executing SkillOpt, generating
a proposal, or changing files.

Eval receipt:

```bash
scripts/qwendex eval --case exact_marker --json
scripts/qwendex receipt latest --json
```

`exact_marker` is an offline harness fixture. Use
`qwendex-dev verify --tier live` when live bridge and fresh-home Codex evidence
is required.

Qwen output reviewed by GPT:

```bash
scripts/qwendex exec "Inspect one named file and report a bounded finding." --seat qwen --json
scripts/qwendex receipt latest --json
```

Then ask the GPT/Codex release seat to review the receipt before accepting the
Qwen output. `seat release` only inspects that configured authority lane; it
does not review a prior receipt by itself. For live release evidence, run the
three-part `qwendex-dev verify --tier live` validator instead of treating a
seat receipt as proof.

Manager mode:

```bash
scripts/qwendex manager mode --toggle --json
scripts/qwendex manager local --toggle --json
```

Patched Codex TUI builds bind those toggles through the Qwendex patch contract.
Use `scripts/qwendex codex-patch apply --source /path/to/codex --json` only
against a supported Codex source checkout; unknown versions and moved anchors
block before writing.
