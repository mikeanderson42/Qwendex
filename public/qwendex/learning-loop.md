# Learning Loop

Qwendex Learn embeds SkillOpt as a staged self-improvement engine.

Commands:

```bash
scripts/qwendex learn status --json
scripts/qwendex learn harvest --json
scripts/qwendex learn dry-run --backend mock --json
scripts/qwendex learn run --backend mock --json
scripts/qwendex learn stage --json
scripts/qwendex learn audit --json
scripts/qwendex learn adopt --proposal proposal.json --json
scripts/qwendex learn adopt --proposal reviewed-proposal.json --approve --json
scripts/qwendex learn rollback --json
```

Default behavior is evidence and staging only. Real Codex-budget optimization
requires explicit approval. Adoption is denied by default for hooks, MCP config,
credentials, shell profiles, adapter protocol code, security policy, public
release claims, and `state/*`.

The adoption command without `--approve` is expected to return `blocked`. Use
`--approve` only after the proposal has reviewed path metadata and avoids denied
paths.

Allowed auto-adopt scope is intentionally narrow: focused skills, small eval
fixtures, and local harness guidance that passes deterministic checks plus a
council-style vote receipt.
