# Qwendex Workspace Rules

This repository is scoped to the Qwendex/local-Qwen harness:

- Qwendex CLI routing, seats, manager mode, estimates, receipts, and evals
- Codex Responses bridge compatibility, parser recovery, runtime guards, and
  marker suppression
- Local model stack launchers, bridge checks, and operator-console wiring
- Public Qwendex docs and release validation summaries

Avoid adding project-specific research workflows, private workspace paths, or
domain-specific queue logic to this repo. Downstream projects should integrate
Qwendex through wrappers or environment configuration.

For targeted verification prefer:

```bash
python3 -m py_compile scripts/*.py scripts/local_qwen_bridge/*.py
python3 -m ruff check scripts tests --ignore E501
python3 -m json.tool config/qwendex/qwendex.json
scripts/qwendex check --json
scripts/qwendex doctor --json
scripts/qwendex eval --all --json
```

## Qwendex Development Lane

Use `~/qwendex-dev` as the primary development worktree for Qwendex product
work. Runtime state, receipts, Codex home, snapshots, Codex source checkouts,
and build outputs belong under `~/qwendex-dev/.qwendex-dev/` and must remain
untracked.

### Current Operator Recovery Context

The operator is currently using CachyOS and needs help restoring local
configuration after a difficult install path. Virtualization requirements and
BIOS setting changes left the first install unstable enough that CachyOS would
not be chosen again under the same constraints, so the environment was
reinstalled on a side partition and the existing working environment needs to
be copied/reconnected there.

For this recovery lane, prioritize replugging Qwendex into the operator shell
so typing `qdex` opens Qwendex in YOLO mode for a selected repository. Keep
this as operator-local setup work: do not turn the machine-specific reinstall
history, partition layout, private paths, or host-program details into public
Qwendex documentation or release claims.

Fast pickup for Clippy/Jarvis requests: if the task mentions Clippy avatar,
Qmsg, voice, tray, wake, `clippy-*` wrappers, or Jarvis operator-local services,
go straight to `/home/tweak/repohome/jarvis`, read its `AGENTS.md`,
`PROJECT_SESSION_BOOT.md`, and `docs/jarvis/DOCUMENTATION_INDEX.md`, then work
from the Jarvis repo. Do not spend broad Qwendex discovery time looking for
those files here unless the request explicitly asks for Qwendex product changes.

Workspace split going forward: use `/home/tweak/repohome/jarvis` for local PC
help, personal-assistant operations, Clippy/Jarvis/Qmsg runtime behavior,
remote desktop, voice/tray/wake work, and operator recovery. Use this
Qwendex-dev worktree for Qwendex product/package changes, new reusable package
work, manager-mode behavior, local-Qwen bridge compatibility, Codex patching,
receipts/evals, and release-facing docs. The private bridge context for
ChatGPT-project prompt generation lives under
`.qwendex-dev/context/deskagent-qwendex-project-context.md` and must remain
untracked. The tracked Jarvis-side prompt guide that can be uploaded or pasted
into a ChatGPT Project is
`/home/tweak/repohome/jarvis/docs/jarvis/CHATGPT_PROJECT_GUIDE.md`.

Fast pickup for operator backup requests: if the operator asks to back up, save
the system, snapshot the PC, create a restore point, create a backup, or make a
snapshot, use the local `snapper-system-backup` skill unless the operator
explicitly asks for a different backup tool. This machine's default restore
path is Snapper on Btrfs with grub-btrfs bootloader visibility; treat it as a
local bootable restore point, not an off-disk backup. Current known setup:
root is Btrfs on `/dev/nvme1n1p3[/@]`, Snapper root config is
`/etc/snapper/configs/root`, grub-btrfs is wired through
`/etc/grub.d/41_snapshots-btrfs`, and the helper lives at
`~/qwendex-dev/.qwendex-dev/codex_home/skills/snapper-system-backup/scripts/snapper_backup.py`.
After using this lane, do a brief retrospective and update the operator-local
skill reference if newly confirmed hardware, settings, package versions, auth
constraints, or recovery steps would make future backup runs faster or safer.

At the start of a fresh dev session, establish posture before editing:

```bash
source ~/qwendex-dev/.qwendex-dev/env.sh
qwendex-dev status-json
qwendex-dev doctor
scripts/qwendex manager status --json
git status --short
```

Before staging product changes from the dev worktree, run:

```bash
qwendex-dev doctor
qwendex-dev verify --tier quick
```

For durable package/product work, use a named task branch, track new source,
tests, docs, configs, and scripts intentionally, keep generated/private
artifacts ignored, split commits by lane, and do not push unless explicitly
requested.

For release-adjacent changes, run `qwendex-dev verify --tier full`; use
`qwendex-dev verify --tier release` before making release-readiness claims.
Local Qwen can assist with bounded drafting and inspection, but GPT/Codex review
is required for release, security, architecture, and public claims.

### Connectedness Rule

No Qwendex-facing control, status label, hotkey, wrapper command, config key, or
public doc claim should be visible unless it is connected end to end:

- a canonical state source or config field
- a CLI/API command path that reads or mutates it
- at least one smoke test or receipt proving the behavior
- public or dev docs that name the supported workflow

If a feature is only a mock, placeholder, planned patch, or depends on a custom
Codex build, label that boundary explicitly in docs and status output.

### Product Guardrails

- Keep private machine paths, credentials, local logs, transcripts, model
  weights, and host-program installs out of public release artifacts.
- Prefer sample configs and wiring instructions over bundling external hosting
  programs.
- Update `docs/development/decision-log.md` when making durable architecture,
  release, patching, or public/private-boundary decisions.
- Stop and repair before continuing if Local-off routes to Qwen, fresh-home
  checks write to the normal safe-home, receipts fail schema/digest validation,
  manager state shows unexpected active/stale sessions, or live output contains
  local-model guard markers or visible tool markup.
