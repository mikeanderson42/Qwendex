# DeskAgent Backend Goal Template

Use for DeskAgent package/config/CLI/process-registry/status/verify/docs work.

```text
GOAL: <component-scoped DeskAgent objective>
TARGET_WORKSPACE: /home/tweak/repohome/jarvis
MODE: compile_goal | execute_goal | next_goal_from_result
EFFORT_BUDGET: micro | focused | standard | heavy | max
LANE: deskagent_backend

OWNER_DOCS_TO_READ:
- AGENTS.md
- PROJECT_SESSION_BOOT.md
- docs/jarvis/DOCUMENTATION_INDEX.md
- docs/deskagent/README.md
- docs/deskagent/NAMING_STANDARD.md
- docs/deskagent/PROCESS_REGISTRY.md
- docs/deskagent/VALIDATION_TIERS.md

SCOPE:
- DeskAgent backend/package/config/CLI/docs/tests only.
- Preserve Jarvis/Clippy wrapper compatibility.

OUT_OF_SCOPE:
- Clippy visual/persona changes.
- Live voice or human acceptance gates.
- Qmsg service renames unless explicitly included.

ARTIFACT_CONTRACT:
- changed source/docs/tests list
- DeskAgent status/verify output when relevant
- process registry diff when registry behavior changes

VALIDATION:
- git diff --check
- python3 -m unittest discover -s tests -p '*deskagent*'
- deskagent status --json or deskagent verify --json when relevant

STOP_STATUSES:
- deskagent_backend_ready
- deskagent_backend_blocked_dirty_state
- deskagent_backend_blocked_validation
```
