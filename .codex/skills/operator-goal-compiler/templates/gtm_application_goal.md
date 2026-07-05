# GTM Application Goal Template

Use for GTM framework goals involving row admission, source gaps, application
harnesses, parser/schema repair, lineage, queues, and patch-safe application
rules.

```text
GOAL: <GTM application objective>
TARGET_WORKSPACE: <active GTM repo from project context>
MODE: compile_goal | execute_goal | next_goal_from_result
EFFORT_BUDGET: focused | standard | heavy | max
LANE: gtm_application

OWNER_DOCS_TO_READ:
- repo AGENTS.md or equivalent project instructions
- current transfer/closeout/result from prior run
- admission/schema/source-contract docs named by the project

SCOPE:
- One frontier: admission review, source-gap repair, parser/schema repair,
  application harness output consumption, lineage, or patch-safe rows.

OUT_OF_SCOPE:
- Rebuilding an existing harness without using its outputs.
- Broad discovery when a queue/blocker frontier exists.

ARTIFACT_CONTRACT:
- input rows/contracts/artifacts read
- admitted/rejected rows or blocker clusters
- source-gap repair plan or executed contracts
- schema/parser diffs when changed
- lineage and application-boundary notes

VALIDATION:
- project-specific focused tests
- schema/parser checks for changed contracts
- admission/robustness receipt when rows are staged

STOP_STATUSES:
- gtm_application_ready
- gtm_source_gap_repair_needed
- gtm_admission_review_ready
- gtm_blocked_machine_contracts
```
