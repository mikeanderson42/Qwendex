# Troubleshooting

## Manager Prompt Is Rejected As An Untrusted Launch

Use `qdex -C <repo>` rather than invoking Qwendex's internal runtime directly.
For a persistent process, inspect the generic binding with:

```bash
scripts/qwendex manager launch-status --pid "$PID" --repo-root "$REPO" --json
```

Reason codes distinguish missing or stale identity, repository mismatch,
policy drift, and hook trust. An untrusted process is allowed to exit on its
first Stop event; Qwendex does not attach it to a repository-matched decision.

Attachment diagnostics also distinguish `missing_launch_identity`,
`state_db_mismatch`, `ledger_db_mismatch`, `decision_not_found`,
`decision_ambiguous`, `session_mismatch`, `turn_unattached`, `turn_mismatch`,
`repo_mismatch`, `process_identity_mismatch`, `policy_mismatch`, and
`codex_home_mismatch`. Do not repair these by selecting the newest row or by
repository alone. Exit the affected session and run the reported `qdex -C`
recovery command from an external shell. Stop remains non-blocking when no
single trusted decision can be selected and performs no guessed mutation.

## Native `qdex -C` Is Duplicated

Run `type -a qdex` in a fresh shell. The normal result should begin with the
installed Qdex executable. A shell function or alias that inserts the current
directory can turn `qdex -C <repo>` into two native selectors. Remove that
shadow or make it a byte-for-byte argv pass-through; repository selection
belongs to Qdex/Codex, not shell startup configuration.

## `check` Fails

Run:

```bash
scripts/qwendex doctor --json
```

Repair missing files before running live probes.

## Local Qwen Is Unavailable

Use offline eval first:

```bash
scripts/qwendex eval --json
```

Then start the stack:

```bash
./llmstack
scripts/qwendex up --json
```

If the public sample does not match your machine, copy
`config/local_llm_stack/stack_manager.sample.json` to the ignored
`config/local_llm_stack/stack_manager.local.json` and set backend/model paths.

## Tool Markup Appears

Stop the local Qwen run, inspect the receipt, and rerun a smaller bounded task.
Do not continue with visible tool wrappers in normal output.

## Learning Proposal Looks Risky

Run:

```bash
scripts/qwendex learn audit --json
```

`audit` inspects the staging area; it does not apply anything. To check one
proposal's declared paths, run `learn adopt --proposal ... --approve --json`.
A pass is allowlist preflight only, so review and apply desired changes manually.
