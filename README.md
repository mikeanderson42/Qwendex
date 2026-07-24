# Qwendex

Qwendex is a Codex-native operator harness for GPT-first work with bounded local
Qwen support. Codex remains the execution plane; Qwendex adds routing, local
runtime checks, receipts, manager state, validation gates, and optional Codex TUI
integration around that plane.

This repository is the public Qwendex build surface. It does not ship model
weights, private runtime state, credentials, logs, transcripts, or host-specific
model paths.

## What Qwendex Is

Qwendex is for operators who want Codex as the authority for execution and
review, while using local Qwen only for bounded, evidence-backed work. Local
Qwen can assist through configured seats and guarded routes, but GPT/Codex
remains authority for release, security, architecture, protocol, and public
claims.

The public contract is intentionally narrow:

- `scripts/qwendex` is the primary CLI.
- JSON commands return a stable envelope with status, summary, artifacts,
  next actions, errors, and version.
- Local Qwen routes are advisory and receipt-backed.
- Manager Mode adds advisory delegation guidance and lifecycle observability
  through the supported patched Codex runtime; it does not become an authority
  gate over prompts, root tools, publication, or final responses.
- Public behavior is backed by smoke tests, evals, and release gates.

## What Is Included

- Public `scripts/qwendex` CLI for checks, routing, seats, receipts, evals,
  state, queue, learning, manager controls, and Codex patch preflight.
- Local Qwen routing through `primary`, `qwen`, `audit`, `release`, and
  `sandbox` seats.
- LLMStack local runtime facade for optional backend launchers, LiteLLM, bridge
  checks, and stack receipts.
- Guard markers, parser recovery, receipt validation, eval summaries, and local
  ledger metadata.
- Manager Mode duty levels: `Off`, `Auto`, `Lite`, `Medium`, `Heavy`, and
  `Manager Mode`.
- Kaveman terse-output state enforced through AgentPolicy output policy,
  managed hooks, and manager workflow receipts.
- Local routing toggle so Qwendex can skip local subagents even when the local
  endpoint is healthy.
- Codex TUI patch contract for a Qwendex footer item and `Alt+M`, `Alt+K`, and
  `Alt+L` hotkeys.
- Qwendex development worktree tooling through `scripts/qwendex_dev_env` and
  the `qwendex-dev` wrapper.
- Visible testbench and tiered validation gates for quick, full, live, and
  release-adjacent checks.

## How It Works

Codex remains the execution plane. Qwendex owns the surrounding control plane:
configuration, routing decisions, local model availability probes, manager lane
state, receipt writing, guard checks, and validation.

For bounded work, `scripts/qwendex route` and `scripts/qwendex exec --seat auto`
can prefer local Qwen when the configured bridge is healthy and the task class is
eligible. If the local alias is unavailable, routing falls back to the configured
primary seat. When local routing is disabled, Qwendex skips local Qwen even if
the endpoint is visible.

Manager routing is per lane. The main Codex session keeps the user's selected
model and reasoning. Low-risk bounded lanes may use local Qwen; high-risk lanes
escalate to GPT/Codex authority.

Qwendex `0.6.5` installs validated runtime generations side by side. Each
Qdex process is pinned to one immutable source/binary/config contract;
activation affects only new sessions, and shell recovery can restore the prior
known-good generation without invoking Qdex. Stock Codex supports Qwendex's
standalone CLI, routing, receipts, checks, and Off-mode recovery. The supported
patch adds native delegation capacity, depth, wait, child-tool-surface, and
lifecycle-observability integration without turning Manager metadata into an
execution authority.

## How It Is Built

Qwendex is primarily Python, shell, and JSON:

- Python and shell implement `scripts/qwendex`, local bridge helpers, evals,
  receipts, runtime guards, and wrappers.
- `config/qwendex/` contains public Qwendex config, schema, profiles, model
  catalog, and dependency metadata.
- `config/local_llm_stack/` contains optional sample local-stack wiring. Real
  machine paths and local model profiles belong in ignored local config.
- `llmstack` and related scripts provide the optional local runtime facade.
- The Codex TUI integration is a source patch for supported Codex versions and
  is built with the normal Codex Rust toolchain.
- Runtime and development state for Qwendex product work lives under the
  ignored `.qwendex-dev/` directory in the dedicated dev worktree.

## Quick Start

Clone a published tagged release into the default Qwendex root:

```bash
git clone https://github.com/mikeanderson42/Qwendex.git ~/qwendex-dev
cd ~/qwendex-dev
git fetch --tags origin
git switch --detach <published-release-tag>
```

The annotated `v0.6.5` tag is the publication boundary for this stable
release; untagged source remains candidate material until the release gates
create and push that tag.

Qwendex is currently distributed as source; GitHub source archives and the
matching git tag are the release artifact. It does not install as a Python or
npm package.

No open-source license is included in this release. Public source visibility
does not grant reuse, modification, or redistribution rights; the repository
owner can add an explicit license in a later release.

Install or check dependencies:

```bash
scripts/qwendex_install_deps --install
scripts/qwendex_install_deps --check --json
```

The supported runtime baseline is Bash 4+ and Python 3.11+; the dependency
receipt blocks older interpreters instead of failing later in Qwendex startup.

The installer requires `@openai/codex@0.145.0`, matching this release's
native-patch compatibility contract. For intentional compatibility testing,
override both `QWENDEX_CODEX_NPM_SPEC` and
`QWENDEX_CODEX_REQUIRED_VERSION`.

Rollback to a pre-`0.5.0` tag requires separate source and runtime roots; see
the [public quickstart](public/qwendex/quickstart.md#upgrade-or-roll-back).

Run baseline checks:

```bash
scripts/qwendex check --json
scripts/qwendex doctor --json
```

Create the isolated runtime wiring and expose `qdex` in `~/.local/bin`:

```bash
scripts/qwendex_dev_env sync
source ~/qwendex-dev/.qwendex-dev/env.sh
qwendex-dev doctor
```

Run sync again after selecting a newer release tag. It replaces the installed
Qdex launcher, regenerates the internal runtime, and rejects the removed
Qwendex `bin/codex` wrapper as an upstream fallback during migration.

Sourcing the environment does not replace bare `codex` or the caller's normal
`CODEX_HOME`; upstream Codex remains the recovery path. `codex-main` is an
explicit alias for the captured upstream binary. Run `qdex` from the desired
directory or use Codex's native `qdex -C <project>` form to select Qwendex's
isolated home, internal runtime, and Manager preflight. Native options such as
`exec --json`, `-C`/`--cd`, `--add-dir`, and arguments after `--` pass through
unchanged; the older `--repo` form remains a compatibility alias.

Qdex defaults to `workspace-write` permission mode from the published Qwendex
config. An explicit `--qdex-permission-mode`,
`QWENDEX_QDEX_PERMISSION_MODE`, or ignored operator-local Qdex config may
select `yolo`; invalid explicit values stop before Codex starts. See
[Configuration](public/qwendex/configuration.md#qdex-launch-permission) for
the exact precedence and local-config boundary.

Qwendex separates health output into advisory and strict modes for `check` and
`doctor`. Manager Mode warnings and repair hints remain advisory in both modes
and do not block unrelated work. Strict mode still fails for product-integrity
problems such as missing public surface or public-doc audit failures.

Inspect routing and run the offline harness:

```bash
scripts/qwendex route --seat auto --task-class exec --prefer-local --json
scripts/qwendex eval --all --json
```

Run an exact-marker local probe when the local stack is intentionally available:

```bash
scripts/qwendex exec "Reply exactly QWENDEX_OK" --seat auto --json
```

Inspect and validate the immutable runtime and Manager acceptance surfaces:

```bash
scripts/qwendex runtime status --json
scripts/qwendex runtime generations --json
scripts/qwendex manager accept --profile offline --run-id <run-id> --json
```

The `live` profile runs fresh real-model sessions; `production` additionally
runs self-hosting, fresh install, v0.5.7 upgrade, shell rollback, security,
performance, and normal-Codex isolation gates. Raw live receipts stay ignored.

## Manager Mode And Local Routing

Manager Mode is additive to normal Codex operation. It helps the model use
bounded subagents outside Ultra and records advisory context, lifecycle, and
validation metadata. It does not authorize or block prompts, root tools,
release commands, or final responses.

Common commands:

```bash
scripts/qwendex manager mode --toggle --json
scripts/qwendex manager mode --set manager --json
scripts/qwendex manager local --toggle --json
scripts/qwendex manager kaveman --toggle --json
scripts/qwendex manager estimate --prompt "..." --json
scripts/qwendex manager status --json
scripts/qwendex --agent-use Manager agent policy --json
scripts/qwendex agent status --json
```

`manager_deploy_policy` defaults to `auto`, which lets Qwendex recommend and
observe useful lanes. Missing, unresolved, or stale lanes are reported as
advisory state; `disabled` turns off those deployment recommendations without
changing Codex authority.

Manager status semantics are:

- `standby`: manager delegation is off, not required, or waiting for an
  operator-selected lane.
- `warning`: advisory issues exist, including missing, unresolved, or stale
  lifecycle rows that may merit cleanup.
- `blocked`: reserved for invalid Manager CLI requests or state operations, not
  for prompts, root tools, releases, or ordinary Codex finalization.

Connected public recovery commands are `manager close`, `manager close-stale`,
`manager repair --safe`, and `manager status`. `manager repair --safe` closes
stale read-only lanes and harmless empty stale writer lanes; a remaining stale
writer lane remains an advisory cleanup item until an operator integrates it or
explicitly closes it with `manager close --agent-id ... --reason ... --json`.

Local routing also separates intent from availability. `Local: [Ready]` means
local subagents may be considered and the configured `qwen-local` alias is
available. `Local: [Off]` means local subagents are intentionally off even if
the endpoint is healthy. `Local: [Unavailable]` means intent remains on, but the
probe did not confirm a usable local route; Qwendex falls back to the configured
primary seat.

Agent Management defaults to the selected Agent Manager mode from `Alt+M` or
`scripts/qwendex manager mode ...`. Explicit selectors are also available
through `--agent-use`, `QWENDEX_AGENT_USE`, and `CODEX_AGENT_USE`. The resolved
mode computes a session `AgentPolicy`, policy hash, Kaveman output policy,
subprocess env exports, and root/child management tool-surface metadata. See
[Agent Management](public/qwendex/agent-management.md) for the public
`qwendex agent ...` commands.

Qdex TUI controls are per-launch: `codex-status` distinguishes requested,
launch-effective, and accepted-turn policy. Kaveman changes the next root turn;
mode or Local changes that need different native capacity are shown as
restart-required rather than silently changing an active process.

## Codex TUI Integration

Qwendex standalone CLI functions, checks, routing, and receipts work with stock
Codex. The supported canonical patch adds native delegation capacity, depth,
wait, child-tool-surface, and lifecycle-observability integration.

The runtime contract is:

```bash
scripts/qwendex codex-status --json
scripts/qwendex codex-patch preflight --json
scripts/qwendex codex-patch apply --source /path/to/codex --json
```

The patched TUI can show:

```text
{Qwendex} Agent Manager: [Manager Mode] | Kaveman: [N] | Local: [Ready] (Alt+M/K/L)
```

Unknown Codex versions or moved source anchors block preflight instead of
guessing. The npm-installed Codex binary is not modified in place.

## Development Workflow

For Qwendex product work, use the dedicated dev worktree convention:

```bash
source ~/qwendex-dev/.qwendex-dev/env.sh
qwendex-dev status-json
qwendex-dev doctor
qwendex-dev review
```

Before staging product changes from that worktree:

```bash
qwendex-dev doctor
qwendex-dev verify --tier quick
```

Use `qwendex-dev verify --tier full` for docs, routing, manager mode,
bridge/parser behavior, shared contracts, or release-adjacent changes. Use
`qwendex-dev verify --tier release` before making release-readiness claims.

`qwendex-dev verify --tier quick` runs lint, smoke tests, `scripts/qwendex
check`, `scripts/qwendex doctor`, Codex status writing, and Codex patch
preflight. `full` adds JSON syntax plus published Draft 2020-12 schema and
version-parity validation, the offline Qwendex eval suite, and local harness
eval/gate receipts. `release` uses strict checks with an
isolated release state DB and writes the release summary. Run `live`, or set
`QWENDEX_RELEASE_REQUIRE_LIVE=1` for `release`, only when the local stack is
intentionally available.

## Verification And Release Gates

Targeted local verification:

```bash
python3 -m py_compile scripts/*.py scripts/local_qwen_bridge/*.py
python3 -m ruff check scripts tests --ignore E501
python3 -m json.tool config/qwendex/qwendex.json
scripts/qwendex check --json
scripts/qwendex doctor --json
scripts/qwendex eval --all --json
```

Development verification:

```bash
qwendex-dev verify --tier quick
qwendex-dev verify --tier full
```

Live gates require the local stack to be intentionally running. Public release
claims require GPT/Codex review and the appropriate Qwendex verification tier.

## Documentation Map

- [Public docs index](public/qwendex/README.md)
- [Quickstart](public/qwendex/quickstart.md)
- [Architecture](public/qwendex/architecture.md)
- [Operations](public/qwendex/operations.md)
- [LLMStack](public/qwendex/llmstack.md)
- [Configuration](public/qwendex/configuration.md)
- [Manager Mode](public/qwendex/manager-mode.md)
- [Codex TUI Patching](public/qwendex/codex-patching.md)
- [Dev Environment](public/qwendex/dev-environment.md)
- [Test Bench](public/qwendex/testbench.md)
- [Security](public/qwendex/security.md)
- [Verification](public/qwendex/verification.md)
- [Troubleshooting](public/qwendex/troubleshooting.md)
- [Release Notes](public/qwendex/release-notes.md)
- [0.6.0-rc.1 Manager production evidence](docs/validation/0.6.0-rc.1-manager-production-summary.md)

## Current Release / Known Limits

This checkout is seeded as `v0.6.5` and supports Codex `0.145.0`.
It includes the supported-Codex update,
state-schema/runtime isolation fixes, advisory Agent Management boundary, and
validated Agent Manager/Kaveman/Local TUI controls described in the release
notes. Its
source-bound Manager production validation summary is generated only after the
offline, live, self-hosting, fresh-install, upgrade, rollback, and release
tiers pass. The annotated tag and GitHub prerelease are created only after the
publication gates pass.

Known limits:

- Live local Qwen checks require the local stack to be running.
- Local Qwen is not release authority.
- The built-in learning mock is non-mutating; external SkillOpt is required for
  status, harvest, and run actions, and `learn adopt --approve` is only an
  allowlist preflight that never applies files.
- Tool capability manifests with per-tool network/write scopes are planned but
  are not yet a general permission engine in the public CLI.
- Patched Codex footer and hotkeys depend on a supported source checkout and a
  rebuilt Codex binary.
- Native Manager delegation integration is certified only on the tested Linux /
  Codex `0.145.0` canonical patch combination. Stock Codex remains supported for
  standalone Qwendex CLI functions without that patched capacity, depth, wait,
  child-tool-surface, or lifecycle-observability integration.
- Qdex keeps Codex 0.145 history persistence, memories, external-agent memory
  import, and passive-screen Chronicle memory disabled; it rejects app-server,
  remote, and project-native role configuration and does not activate
  role-driven child controls. Qwendex AgentPolicy remains the supported
  delegation contract.
