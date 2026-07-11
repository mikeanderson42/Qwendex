# Architecture

Qwendex has one public boundary and several internal parts.

Codex launch identity is deliberately split three ways:

- upstream `codex` is unchanged and uses the caller's normal Codex home;
- public `qdex` selects Qwendex's isolated home, permissions, repository scope,
  and Manager preflight;
- the ignored `qwendex-codex-runtime` selects the supported patched build or a
  labelled upstream fallback and is not a public launch entrypoint.

Only Qdex creates Manager launch authority. The internal runtime and repository
scope alone are insufficient.

| Layer | Role |
| --- | --- |
| Runtime | Public CLI, JSON contract, config precedence, receipts |
| LLMStack | Optional local runtime control plane for backend launchers, LiteLLM, bridge checks, and local receipts |
| Launcher | Starts, stops, checks, and delegates to the local stack manager |
| Adapter | Translates Codex-compatible local model traffic and reports status contracts |
| Tool server | Delegates bounded MCP-backed workflows with trusted-root checks |
| Guard | Detects duplicate-read, loop, malformed markup, and configured budget issues |
| Evidence | Writes receipts, indexes ledger metadata, and exposes eval summaries |
| Learn | Validates a built-in non-mutating mock contract, inspects staged proposals, and delegates explicit external runs to SkillOpt |
| Manager | Coordinates subagent lanes, shortcut policy, and stale-agent cleanup |
| Seats | Defines `primary`, `qwen`, `audit`, `release`, and `sandbox` authority |

The adapter is not a hidden second agent. It translates protocol, streams
responses, parses tool envelopes, delegates guard checks, and emits status. Task
behavior belongs in tools, skills, eval fixtures, explicit workflows, or reviewed
operator prompts.

Manager routing is per lane. The main Codex session keeps the user's selected
model and reasoning. Local Qwen is used only when the Local toggle is on, the
lane is low-risk and bounded, and availability is confirmed. Higher reasoning is
reserved for the lane that needs it.

## Authority Model

`primary`, `audit`, and `release` are GPT/Codex authority seats. `qwen` and
`sandbox` are bounded local seats. Qwen receipts must be reviewed before public
release acceptance, architecture changes, security policy changes, or protocol
changes.
