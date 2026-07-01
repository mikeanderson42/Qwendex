# Troubleshooting

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

Do not adopt proposals that touch denied paths.
