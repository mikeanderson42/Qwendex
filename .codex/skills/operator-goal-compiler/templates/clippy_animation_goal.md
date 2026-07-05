# Clippy Animation Goal Template

Use for animation catalog, idle behavior, and behavior-controller work.

```text
GOAL: <animation/personality behavior objective>
TARGET_WORKSPACE: /home/tweak/repohome/jarvis
MODE: compile_goal | execute_goal | next_goal_from_result
EFFORT_BUDGET: focused | standard
LANE: clippy_animation

OWNER_DOCS_TO_READ:
- AGENTS.md
- PROJECT_SESSION_BOOT.md
- docs/jarvis/CLIPPY_ANIMATION_CATALOG.md
- docs/jarvis/CLIPPY_PERSONA_BRIEF.md
- docs/jarvis/CURRENT_CONTEXT.md

SCOPE:
- Animation states, catalog config, behavior controller, focused tests.

OUT_OF_SCOPE:
- Geometry redesign unless explicitly required.
- DeskAgent/Qmsg package cleanup.

ARTIFACT_CONTRACT:
- animation catalog/config changes
- behavior controller changes
- focused test output
- visual/live approval boundary if needed

VALIDATION:
- python3 -m py_compile scripts/clippy_animation_catalog.py scripts/clippy_behavior_controller.py
- python3 -m unittest tests.test_clippy_behavior_controller
- git diff --check

STOP_STATUSES:
- clippy_animation_catalog_ready
- clippy_animation_review_required
- clippy_animation_blocked_validation
```
