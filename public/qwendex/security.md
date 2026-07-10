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
| Learned-skill poisoning | Qwendex never auto-applies proposals; its adopt command only performs a denied-path allowlist preflight |
| Public-doc overclaiming | Public docs are naming-audited and release-reviewed |
| Release artifact leakage | The release gate scans every tracked blob, path, and symlink for runtime/private material and binds the result to the tagged tree |
| Adapter drift | Launcher checks verify model alias, context, guard profile, and status contract |
| Stale delegation state | Manager sessions record heartbeat, stop condition, stop reason, and close metadata in local state |
| Unsafe agent tool use | Agent pre-tool gates deny recursive child spawn, fail closed on non-allowlisted read-only shell events, reject conflicting write locks, and require approval for release/publish commands |

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

Tool capability manifests with per-tool network/write scopes are planned but not
yet enforced as a general permission engine. The public CLI does enforce the
Agent Management pre-tool gate for its managed events, including the
single-writer file-lock strategy; stock Codex tool-registry filtering remains a
separate patched-runtime integration boundary.

The release-command gate recognizes direct and path-qualified release commands,
common `command`/`env` wrappers, shell `-c` and `eval` payloads, command
substitutions, pipelines, and newline-separated commands. It is not a general
shell sandbox: an arbitrary interpreter such as Python or `xargs` can construct
commands the static recognizer cannot prove. Publication credentials, network
egress controls, and the external execution sandbox therefore remain the final
authority boundary. Only approval inherited by the managed hook process is
trusted; command text and event JSON are untrusted inputs. Direct `gh api`
POST, PUT, PATCH, and DELETE forms, plus body/field forms that imply POST, are
approval-gated for every REST or GraphQL endpoint. An explicit GET remains a
read-only request even when fields are supplied as query parameters.

The managed read-only shell gate allows only the bare inspection commands and
safe Git subcommands documented in [Agent Management](agent-management.md#write-safety).
It parses quoted command lists and pipelines, then rejects unknown programs,
interpreters, wrappers, shell expansion, redirection, external-output options,
and unparseable input. This fail-closed classifier is effective only when the
managed `PreToolUse` hook is installed and verified; OS sandboxing and command
side effects outside that syntax-level contract remain separate controls.

For writer profiles, the same classifier treats every non-allowlisted managed
shell event as write-capable. Native subagents must use their top-level Codex
`agent_id` and match an active registration, repository, current task, and
declared write scope. Codex root events intentionally omit `agent_id`; in
Manager Mode Qwendex accepts only the root owner derived from the matching
`qdex` preflight and ignores identity claims in prompt or tool input. Opaque
writes take the conservative repository lease, so arbitrary shell commands
never bypass ownership merely because their side effects are difficult to
infer. Per-tool root leases are released on `PostToolUse`, with Manager `Stop`
providing turn-boundary cleanup. An aborted tool remains locked rather than
weakening the single-writer boundary. After an abrupt launcher exit, orphan
reclamation requires the recorded PID/process-start identity to be dead; a
still-live prior launcher remains blocking.
