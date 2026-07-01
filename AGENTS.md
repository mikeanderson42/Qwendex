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
