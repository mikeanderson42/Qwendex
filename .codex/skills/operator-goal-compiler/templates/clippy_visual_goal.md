# Clippy Visual Goal Template

Use for Clippy renderer, SVG body/eyes/brows/paper assets, and proof images.

```text
GOAL: <visual polish objective>
TARGET_WORKSPACE: /home/tweak/repohome/jarvis
MODE: compile_goal | execute_goal | next_goal_from_result
EFFORT_BUDGET: micro | focused | standard
LANE: clippy_visual

OWNER_DOCS_TO_READ:
- AGENTS.md
- PROJECT_SESSION_BOOT.md
- docs/jarvis/CLIPPY_PERSONA_BRIEF.md
- docs/jarvis/CLIPPY_AVATAR_REDESIGN_PLAN.md
- docs/jarvis/reference/clippy_canonical_source_notes.md

SCOPE:
- Renderer/SVG/proof assets only.
- Use canonical geometry sources and visual proof workflow.

OUT_OF_SCOPE:
- DeskAgent/Qmsg package work.
- Service renames.
- Heavy live acceptance gates.

ARTIFACT_CONTRACT:
- changed visual source/assets
- rendered proof assets or proof command output
- visual review notes
- human_review_required when subjective approval remains

VALIDATION:
- python3 -m py_compile scripts/clippy_avatar.py
- python3 -m unittest tests.test_clippy_avatar tests.test_clippy_svg_import
- QT_QPA_PLATFORM=offscreen python3 tools/render_clippy_visual_proofs.py --json
- git diff --check

STOP_STATUSES:
- clippy_visual_review_ready
- clippy_visual_human_review_required
- clippy_visual_blocked_validation
```
