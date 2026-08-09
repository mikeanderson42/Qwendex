# Codex CLI 0.145–0.147 Assessment

Assessment date: 2026-08-09. Primary references are the official Codex
[changelog](https://developers.openai.com/codex/changelog) and
[model documentation](https://learn.chatgpt.com/docs/models).

The supplied review notes were directionally useful, but their 0.146 and 0.147
dates were not authoritative. The official sequence is 0.145.0 on July 21,
0.146.0 on July 29, 0.146.1 on August 5, and 0.147.0 on August 7, 2026.

## Decision Matrix

| Capability | Decision | Qwendex treatment |
| --- | --- | --- |
| Portable Agent Plugins and expanded skill catalogs | Native pass-through | Preserve Codex discovery/catalog behavior; add no duplicate Qwendex catalog |
| V2 per-child model/reasoning | Adopt with allowlist | Expose only model/reasoning, use Terra high/xhigh profiles, let Codex validate compatibility |
| Native roles and service tiers | Defer | Keep project definitions passive and omit role/service-tier control from the Qwendex V2 contract |
| Native child developer instructions | Adopt | Put common bounded-worker invariants in the native field; keep lane scope in the spawn message |
| `--approve-for-me` | Adopt | Qdex `auto-review`; reject caller-native permission overrides so the recorded mode is effective |
| Removed `exec --full-auto` | Adopt migration | Fail early with the supported replacement guidance |
| Hook/project trust changes | Redesign | Preserve native hook trust across home, project, config-layer, and plugin discovery; use exact generated-set checks only as diagnostics |
| MCP `2026-07-28` | Experimental opt-in | Keep legacy default until pagination, cancellation, reconnect, auth, and isolation evidence is complete |
| Hosted Apps cache recovery | Retain narrow patch | Degraded-ready only with non-empty account cache and non-auth transient failure; continue reconnect |
| Token and rollout budgets | Observe experimentally | Do not market as a cost/billing cap |
| Secret sanitization | Adopt parity | Redact OpenAI/AWS/bearer/common token forms in Qwendex-owned receipts and diagnostics |
| History sections/import, Cursor sync, Bedrock, audio/realtime | Native/out of scope | No Qwendex patch or public guarantee unless a later product contract requires one |
| Persistent memories/import/Chronicle | Defer | Continue disabling in Qdex until retention, deletion, provenance, and receipt semantics exist |

## Model Policy

The 0.147 account model catalog inspected for this release exposes Sol and
Terra through the V2 family and Luna through V1. Therefore Terra is the bounded
native worker choice, while Luna/max is used only through one-shot `codex exec`.
Qwendex does not relabel Luna as V2 or claim that every account exposes the same
models.

## Patch Impact

The 0.147 canonical patch retains the previously released footer/hotkeys,
root-only V2 management, child policy, wait behavior, versioned model cache,
and hosted Apps cached recovery. Version-specific additions restore controlled
model/reasoning schema fields, preserve native child developer instructions,
pass the primary environment to model validation, keep native roles and service
tiers sealed, strengthen patch signatures, and test the new behavior against a
pristine official source checkout.

## Explicitly Rejected Claims

- Qwendex does not bundle the best features of unrelated coding CLIs.
- It does not claim that Terra, Luna, Sol, or local Qwen is universally
  available or optimal.
- It does not claim MCP 2026, retained memories, native roles, or service-tier
  overrides as supported merely because Codex contains them.
- It does not treat a synthetic receipt, planning label, or stale lifecycle row
  as execution or release evidence.
