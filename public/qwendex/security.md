# Security

Qwendex treats networked and stateful tools as read-only unless an operator asks
for a scoped change.

## Threat Model

| Threat | Control |
| --- | --- |
| Credential exposure | Public config and docs are secret-scanned; credentials stay outside Qwendex config |
| Shell execution | Local Qwen routes through guarded launchers; finite run budgets must be configured when needed |
| Network tools | Search and inspect are read-only by default |
| Receipt leakage | Receipt reads verify schema and digest; receipts may still contain redacted output snippets, so do not publish without review |
| Telemetry leakage | Exploration telemetry is default-off, local-only metadata in a separate database; raw prompts, commands, paths, queries, tool output, and transcripts are discarded before persistence |
| Model-output injection | Guard markers detect malformed tool markup and loop patterns before acceptance |
| Learned-skill poisoning | Qwendex never auto-applies proposals; its adopt command only performs a denied-path allowlist preflight |
| Public-doc overclaiming | Public docs are naming-audited and release-reviewed |
| Release artifact leakage | The release gate scans every tracked blob, path, and symlink for runtime/private material and binds the result to the tagged tree |
| Adapter drift | Launcher checks verify model alias, context, guard profile, and status contract |
| Stale delegation state | Manager sessions record heartbeat, stop condition, stop reason, and close metadata in local state |
| Delegation scope | Native capacity/depth/wait limits, root-only management tools, no recursive child management, and explicitly read-only child lanes bound subagent use; Qwendex does not gate root tools |

## Learning Preflight Denials

- Hooks and hook config
- MCP config
- Credentials and shell profiles
- Adapter protocol code
- Security policy
- Public release claims
- `state/*`

Passing `learn adopt --approve` means only that declared paths passed this
allowlist preflight. It never writes or applies proposal content.
Security-sensitive changes still require GPT/Codex review and normal verified
development changes.

## Current Limits

The default config keeps `max_wall_time_seconds` and `max_tool_calls` at `-1`
because some local checks are intentionally long-running. Set finite values in a
bounded run when wall-clock or tool-call limits are required. Qwendex redacts
known secret-shaped strings, but receipts can still contain non-secret private
context in output snippets.

Exploration-performance telemetry is not a transcript or generic tool-call
recorder. Its hook adapter observes only accepted lifecycle events,
uses local HMAC digests for correlation and duplicate comparison, and exposes
aggregate-safe summaries only. Events not accepted for observation produce no
telemetry, storage failure cannot affect execution, and `QWENDEX_PERFORMANCE_DB` controls
only the local store location. Operators should keep that database local and
use `performance purge --approve` when its aggregate history is no longer
needed.

Qwendex Agent Management is not a general permission engine. It does not
authorize or deny prompts, root tools, file writes, release/publish commands, or
final responses. Explicit user intent, Codex permissions, the selected
sandbox/Yolo posture, credentials, network policy, and host controls remain the
authority boundary.

The patched native delegation surface still applies capacity, depth and wait
limits, hides management tools from children, prevents recursive child
management, and preserves explicitly read-only child lanes. Manager hooks and
ledgers add advisory context and observability; missing or mismatched identity,
reports, validation, or hook wiring is reported without blocking root work.

## Certified Boundary

The production-hardening claim is deliberately limited to the tested Linux and
Codex `0.145.0` canonical-patch matrix. Qwendex orchestration policy is not an
operating-system sandbox: normal Qdex defaults to `workspace-write`, while
Yolo is an explicit CLI, environment, or ignored operator-local opt-in that
adds Codex's bypass flag once. A Manager preflight may snapshot the resolved
mode and source for diagnostics; it does not grant or revoke permission.
`qwendex-dev` bare-launch bypass mode is development-only. Stock Codex
and its normal home remain independent and provide the Off-mode recovery path.

For Qdex launches, Codex 0.145 history persistence and experimental memories
are disabled at the generated-home and per-launch boundaries. Native role and
profile configuration is deferred because it can load role-specific instructions
or model settings outside the reviewed Manager lifecycle. Qdex rejects
project-native role configuration, app-server/remote access, and caller
history/memory activation attempts; the canonical V2 patch also keeps child
model, reasoning, and service-tier settings inherited from the root. These
safeguards do not apply to direct stock-Codex invocation outside Qdex.
Qwendex shares the operator's authentication file intentionally, but keeps a
generation-local copy of Codex's volatile `version.json` cache and installation
identity. Acceptance compares the normal home's stable config, hooks, and
installation identity and separately compares complete isolated decoy homes.

Runtime source, patch identity, binary pair, config/schema, and state
schema are bound into one validated generation for each Qdex process. Mutable
agent reports are written outside the sealed tree. Child threads lack root
collaboration tools in the canonical patch, recursively managed children are
disabled, and explicitly read-only lanes remain constrained. These controls do
not make Qwendex a root-tool or publication gate and do not make claims about
unsupported platforms or Codex versions.
