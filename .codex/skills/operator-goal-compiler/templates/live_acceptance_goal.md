# Live Acceptance Goal Template

Use only for explicit live gates such as wake readiness, live voice, remote
keyboard receipt, restart survival, or human visual approval.

```text
GOAL: <specific live acceptance gate>
TARGET_WORKSPACE: /home/tweak/repohome/jarvis
MODE: compile_goal | execute_goal | next_goal_from_result
EFFORT_BUDGET: focused | standard | heavy
LANE: live_acceptance

OWNER_DOCS_TO_READ:
- AGENTS.md
- PROJECT_SESSION_BOOT.md
- docs/jarvis/CURRENT_CONTEXT.md
- docs/jarvis/VOICE_ASSISTANT_RUNBOOK.md
- docs/jarvis/VOICE_DEVICE_CONFIG.md
- docs/jarvis/REMOTE_ACCESS_RUNBOOK.md when remote input is involved

SCOPE:
- One explicit live gate and its receipt/proof path.

OUT_OF_SCOPE:
- Package refactors.
- Broad visual redesign.
- Treating live failures as package failures.

ARTIFACT_CONTRACT:
- live receipt/proof command output
- service status before/after when changed
- deferred gates list

VALIDATION:
- gate-specific runbook command
- systemctl/user status for touched services
- git diff --check when files change

STOP_STATUSES:
- live_acceptance_gate_ready
- live_acceptance_deferred_operator_action
- live_acceptance_blocked_runtime
```
