# Qwendex Product Goal Template

Use for Qwendex manager mode, local-Qwen bridge, parser/runtime guards,
receipts, evals, Codex patching, and release-facing docs.

```text
GOAL: <Qwendex product objective>
TARGET_WORKSPACE: /home/tweak/repohome/qwendex-dev
MODE: compile_goal | execute_goal | next_goal_from_result
EFFORT_BUDGET: focused | standard | heavy | max
LANE: qwendex_product

OWNER_DOCS_TO_READ:
- AGENTS.md
- QWENDEX_STARTUP.md
- relevant docs/development/*
- relevant public/qwendex/*
- relevant .codex/skills/qwendex-*/SKILL.md

SCOPE:
- Qwendex product/package surfaces only.
- Connectedness chain for visible controls or claims.

OUT_OF_SCOPE:
- Private Jarvis runtime details in public docs.
- Local secrets, transcripts, model weights, generated caches.

ARTIFACT_CONTRACT:
- receipt/eval output when applicable
- connectedness proof for visible controls/status/claims
- docs update
- quick/full/release verification result as appropriate

VALIDATION:
- qwendex-dev doctor
- qwendex-dev verify --tier quick
- qwendex-dev verify --tier full for shared contracts/release-adjacent work
- git diff --check

STOP_STATUSES:
- qwendex_product_ready
- qwendex_product_blocked_stop_the_line
- qwendex_product_blocked_validation
```
