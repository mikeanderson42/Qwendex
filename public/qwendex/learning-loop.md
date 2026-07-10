# Learning Loop

Qwendex Learn is a staging and inspection facade. A clean installation can
validate its built-in mock contract without installing or running SkillOpt:

```bash
scripts/qwendex learn dry-run --backend mock --json
```

That built-in result is deliberately non-mutating. It performs no external
execution, generates no proposal, adopts no files, and reports those boundaries
in its JSON payload. It is contract evidence, not evidence that an optimization
run occurred.

The staging-area inspection commands are also built in and non-mutating:

```bash
scripts/qwendex learn stage --json
scripts/qwendex learn audit --json
scripts/qwendex learn proposal-summary --json
```

`status`, `harvest`, and `run` require the external `skillopt-sleep` executable.
They block when it is unavailable. A real Codex-budget run additionally requires
the explicit budget approval flag:

```bash
scripts/qwendex learn status --json
scripts/qwendex learn harvest --json
scripts/qwendex learn run --backend mock --json
scripts/qwendex learn run --backend codex --allow-codex-budget --json
```

Qwendex does not auto-adopt or apply proposals. `learn adopt` is an allowlist
preflight only: without `--approve` it blocks before preflight; with `--approve`
it validates proposal path metadata and returns pass or blocked, but never
writes files. Apply any reviewed proposal manually through the normal
development workflow.

```bash
scripts/qwendex learn adopt --proposal proposal.json --json
scripts/qwendex learn adopt --proposal reviewed-proposal.json --approve --json
```

The preflight rejects hooks, MCP config, credentials, shell profiles, adapter
protocol code, security policy, public release claims, and `state/*`. `learn
rollback` is unavailable because Qwendex has no adoption operation to reverse.
