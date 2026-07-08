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
| Model-output injection | Guard markers detect malformed tool markup and loop patterns before acceptance |
| Learned-skill poisoning | Learn adoption is staged, denied for sensitive paths, and review-gated |
| Public-doc overclaiming | Public docs are naming-audited and release-reviewed |
| Adapter drift | Launcher checks verify model alias, context, guard profile, and status contract |
| Stale delegation state | Manager sessions record heartbeat, stop condition, stop reason, and close metadata in local state |
| Unsafe agent tool use | Agent pre-tool gates deny recursive child spawn, read-only writes, conflicting write locks, and release/publish commands without approval |

## Denied Auto-Adopt Paths

- Hooks and hook config
- MCP config
- Credentials and shell profiles
- Adapter protocol code
- Security policy
- Public release claims
- `state/*`

Security-sensitive changes require GPT/Codex review and a receipt.

## Current Limits

The default config keeps `max_wall_time_seconds` and `max_tool_calls` at `-1`
because some local checks are intentionally long-running. Set finite values in a
bounded run when wall-clock or tool-call limits are required. Qwendex redacts
known secret-shaped strings, but receipts can still contain non-secret private
context in output snippets.

Tool capability manifests with per-tool network/write scopes are planned but not
yet enforced as a general permission engine. The public CLI does enforce the
Agent Management pre-tool gate for its managed events, including the
single-writer file-lock strategy; stock Codex tool-registry filtering remains a
separate patched-runtime integration boundary.
