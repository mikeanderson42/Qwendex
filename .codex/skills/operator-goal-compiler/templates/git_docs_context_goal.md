# Git / Docs Context Goal Template

Use for AGENTS, project instructions, ChatGPT Project guides, skills, branch
custody, ignored/private context, and documentation consolidation.

```text
GOAL: <docs/context/git custody objective>
TARGET_WORKSPACE: /home/tweak/repohome/qwendex-dev and/or /home/tweak/repohome/jarvis
MODE: compile_goal | execute_goal | next_goal_from_result
EFFORT_BUDGET: micro | focused | standard
LANE: git_docs_context

OWNER_DOCS_TO_READ:
- active repo AGENTS.md
- /home/tweak/repohome/jarvis/docs/jarvis/CHATGPT_PROJECT_GUIDE.md
- /home/tweak/repohome/jarvis/docs/jarvis/GIT_CUSTODY.md
- /home/tweak/repohome/qwendex-dev/AGENTS.md

SCOPE:
- Instructions, skills, docs, branch/tracking guidance.

OUT_OF_SCOPE:
- Runtime/source behavior changes unless required to validate docs.
- Public release claims from private machine facts.

ARTIFACT_CONTRACT:
- changed docs/skills
- branch and commit list
- ignored private artifacts identified
- remaining dirty-state classification

VALIDATION:
- git diff --check
- qwendex-dev doctor and quick verify if qwendex docs/skills changed
- docs-only grep/existence checks for referenced files

STOP_STATUSES:
- git_docs_context_ready
- git_docs_context_blocked_dirty_state
- git_docs_context_blocked_validation
```
