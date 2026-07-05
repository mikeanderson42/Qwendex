# Doctor / Verifier Goal Template

Use for separating package health from live gates, classifying doctor failures,
and making verifier output actionable.

```text
GOAL: <doctor/verifier boundary objective>
TARGET_WORKSPACE: /home/tweak/repohome/jarvis
MODE: compile_goal | execute_goal | next_goal_from_result
EFFORT_BUDGET: focused | standard
LANE: doctor_verifier

OWNER_DOCS_TO_READ:
- AGENTS.md
- PROJECT_SESSION_BOOT.md
- docs/jarvis/DOCUMENTATION_INDEX.md
- docs/jarvis/OPERATIONS.md
- docs/deskagent/VALIDATION_TIERS.md

SCOPE:
- doctor/verify code, docs, tests, failure classification.

OUT_OF_SCOPE:
- Completing live gates unless explicitly requested.
- Avatar redesign or service migrations.

ARTIFACT_CONTRACT:
- classified failure list
- package failures list
- deferred live gates list
- verifier/doctor command outputs

VALIDATION:
- focused verifier/doctor tests
- jarvis-doctor --json or deskagent-doctor --json as relevant
- jarvis-verify --read-only or deskagent verify --json as relevant
- git diff --check

STOP_STATUSES:
- doctor_verifier_boundary_ready
- doctor_failure_classified_deferred_live_gate
- doctor_verifier_blocked_validation
```
