# Qwendex Development Architecture Map

Qwendex is a Codex-native harness. Codex remains the execution plane; Qwendex
owns routing, seats, local-Qwen guardrails, receipts, evals, manager state,
LLMStack wiring, and Codex TUI patch contracts.

## Core Surfaces

- `scripts/qwendex` / `scripts/qwendex_cli.py`: public CLI, routing, manager
  mode, receipts, evals, Codex patch preflight, and LLMStack facade.
- `scripts/qwendex_dev_env`: development worktree lifecycle, bootstrap,
  doctor, verification tiers, Codex source patch/build lane, staging, snapshots.
- `scripts/run_local_qwen_codex.sh` and `scripts/local_qwen_bridge/`: guarded
  Codex-facing local-Qwen Responses bridge.
- `scripts/llm` and `scripts/local_qwen_harness_*.py`: compatibility harness
  gates, eval receipts, ledger indexing, launcher validation.
- `config/qwendex/` and `config/local_llm_stack/`: public policy/config,
  model catalog, local stack examples, prompt templates, and private-local
  override boundaries.

## Authority Model

- GPT/Codex seats own release acceptance, security, architecture, and public
  claims.
- Local Qwen can draft, summarize, inspect, run bounded commands, and produce
  receipts, but it is not release authority.
- `qwendex-dev verify --tier release` is the product gate; it must emit a
  durable summary before release claims are accepted.

## Runtime State

In the dev worktree, all mutable state must stay under `.qwendex-dev/`:

- Qwendex state DB
- Qwendex receipt ledger
- local harness ledger
- eval results
- Codex home
- Codex source/build outputs
- snapshots and status reports

No `.qwendex-dev/`, logs, private profiles, transcripts, credentials, model
weights, or generated caches belong in public release artifacts.
