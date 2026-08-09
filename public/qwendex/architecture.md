# Architecture

Qwendex has one public repository boundary and four named surfaces:

- `qwendex`: operator control plane and stable JSON CLI;
- `qdex`: isolated Codex launcher;
- optional `llmstack`: local-model lifecycle facade;
- `qwendex-dev`: source-install, build, verification, and release tooling.

Codex launch identity is deliberately split three ways:

- upstream `codex` is unchanged and uses the caller's normal Codex home;
- public `qdex` selects Qwendex's isolated home, permissions, repository scope,
  and Manager preflight;
- the ignored `qwendex-codex-runtime` selects the supported patched build or a
  labelled upstream fallback and is not a public launch entrypoint.

Qdex is the public entrypoint for complete Manager lifecycle association. The
internal runtime and repository scope alone are insufficient for those
diagnostics, but lifecycle association is advisory and does not grant or revoke
Codex authority.

| Layer | Role |
| --- | --- |
| Runtime | Public CLI, JSON contract, config precedence, receipts |
| Generations | Immutable Qwendex source/config/hooks and optional patched Codex binary pairs; atomic activation for new sessions |
| Qdex | Isolated Codex home, launch permission posture, native project trust, Manager preflight, and runtime selection |
| LLMStack | Optional local runtime control plane for backend launchers, LiteLLM, bridge checks, and local receipts |
| Launcher | Starts, stops, checks, and delegates to the local stack manager |
| Adapter | Translates Codex-compatible local model traffic and reports status contracts |
| Tool server | Delegates bounded MCP-backed workflows with trusted-root checks |
| Guard | Detects duplicate-read, loop, malformed markup, and configured budget issues |
| Evidence | Writes receipts, indexes ledger metadata, and exposes eval summaries |
| Learn | Validates a built-in non-mutating mock contract, inspects staged proposals, and delegates explicit external runs to SkillOpt |
| Manager | Advises on subagent lanes and exposes lifecycle, validation evidence, explicit waivers, and stale-agent cleanup |
| Seats | Defines `primary`, `qwen`, `audit`, `release`, `luna`, `terra`, and `sandbox` execution policy |

The adapter is not a hidden second agent. It translates protocol, streams
responses, parses tool envelopes, delegates guard checks, and emits status. Task
behavior belongs in tools, skills, eval fixtures, explicit workflows, or reviewed
operator prompts.

Manager routing is per lane. The main Codex session keeps the user's selected
model and reasoning. Native hosted workers default to Terra/high and use
Terra/xhigh for risk-review/release lanes. Luna/max is a one-shot seat rather
than a Codex 0.147 V2 worker. Local Qwen uses the separate `qwendex_exec`
surface only when the Local toggle is on, the profile is allowlisted, the lane
is low-risk and bounded, and availability is confirmed.

## Authority Model

`primary`, `audit`, and `release` are GPT/Codex authority seats. `luna` and
`terra` are bounded hosted execution seats, while `qwen` and `sandbox` are
bounded local seats. Only the authority seats may accept public release,
architecture, security-policy, or protocol changes; other receipts require
GPT/Codex review.
