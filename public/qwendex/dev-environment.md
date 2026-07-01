# Qwendex Dev Environment

`scripts/qwendex_dev_env` creates a standalone development copy at
`~/qwendex-dev`. It is for working on Qwendex itself while keeping the current
system Codex install available as the fallback execution plane.

## Create Or Refresh

```bash
scripts/qwendex_dev_env sync
```

The sync copies the project files, including `public/`, `config/`, `scripts/`,
`tests/`, and the Windows `open.ps1` launcher, into `~/qwendex-dev`. It does
not copy `.git`, caches, prior results, or the generated dev state folder.

Generated state lives under:

```text
~/qwendex-dev/.qwendex-dev/
```

That folder contains isolated Qwendex state, receipts, Codex home, status file,
toolchain/build placeholders, and `env.sh`.

## Use It

```bash
source ~/qwendex-dev/.qwendex-dev/env.sh
qwendex-dev status
qwendex-dev open
```

Codex sessions launched with `qwendex-dev open` receive a dev-mode instruction
to act as a senior Qwendex project developer: keep edits scoped, verify before
staging, and keep generated `.qwendex-dev` state out of public artifacts.

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

## Developer Lifecycle

Work inside `~/qwendex-dev`, then use the dev commands to move changes through
review, verification, and staging:

```bash
qwendex-dev review
qwendex-dev verify
qwendex-dev diff
qwendex-dev promote
qwendex-dev stage
```

- `review` prints Qwendex status, promotable dev changes, and tracked source
  git status.
- `verify` runs Python compile, shell syntax checks, ruff when available,
  focused pytest smoke tests, Qwendex check, Qwendex doctor, Codex status, and
  Codex patch preflight.
- `diff` shows the managed file changes that would be promoted from
  `~/qwendex-dev` back to the tracked source repo.
- `promote` copies managed changes back to the source repo while excluding
  `.git`, caches, generated state, results, `bin/`, and the root `open.ps1`.
- `stage` promotes, verifies, then stages only managed Qwendex paths in the
  tracked source repo.
- `snapshot` writes a small state receipt under
  `~/qwendex-dev/.qwendex-dev/snapshots/<UTC>/`.

Use `stage --skip-verify` only when a verification result has already been
captured for the same promoted changes.

## Patch Codex Source

Use the normal Qwendex patch contract from inside the dev environment:

```bash
qwendex-dev patch-codex ~/qwendex-dev/.qwendex-dev/codex-source/codex
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
