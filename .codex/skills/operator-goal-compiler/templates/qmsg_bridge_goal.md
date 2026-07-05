# Qmsg Bridge Goal Template

Use for DeskAgent Qmsg bridge, qmsg CLI/status/verify/watchdog, and
compatibility work.

```text
GOAL: <component-scoped Qmsg bridge objective>
TARGET_WORKSPACE: /home/tweak/repohome/jarvis
MODE: compile_goal | execute_goal | next_goal_from_result
EFFORT_BUDGET: focused | standard | heavy
LANE: qmsg_bridge

OWNER_DOCS_TO_READ:
- AGENTS.md
- PROJECT_SESSION_BOOT.md
- docs/jarvis/QMSG_QWENDEX_RUNBOOK.md
- docs/jarvis/QMSG_COMMANDS.md
- docs/deskagent/qmsg/README.md

SCOPE:
- deskagent.messaging.qmsg, qmsg status/verify/watchdog, compatibility docs.
- Keep qmsg, qmsg-tmux, qmsg-watchdog, units, and qwendex-phone compatible.

OUT_OF_SCOPE:
- Avatar visuals.
- Wake/live voice.
- Service renames without explicit migration proof.

ARTIFACT_CONTRACT:
- qmsg status/verify JSON
- qmsg-tmux status
- compatibility receipt or doc note
- focused qmsg tests

VALIDATION:
- python3 -m unittest tests.test_qmsg_watchdog tests.test_deskagent_qmsg_bridge tests.test_deskagent_qmsg_protocol tests.test_deskagent_qmsg_watchdog
- qmsg status --json
- qmsg-tmux status
- git diff --check

STOP_STATUSES:
- qmsg_bridge_ready
- qmsg_bridge_blocked_runtime
- qmsg_bridge_blocked_validation
```
