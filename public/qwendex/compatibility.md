# Compatibility

This document is the human-readable counterpart to `scripts/qwendex about
--json`. Both describe the configured compatibility contract rather than
probing a running installation. Build and release receipts remain the authority
for the artifacts actually installed or published.

## Supported Baseline

| Component | Qwendex 0.7.0 contract |
| --- | --- |
| Distribution | Source checkout and matching annotated git tag; no Qwendex binary package |
| Codex CLI | `0.147.0` |
| Canonical Codex source | Official `rust-v0.147.0` source commit pinned by the release contract |
| Python | 3.11 or newer |
| Shell | Bash 4 or newer |
| Certified native integration | Linux plus the canonical Codex 0.147 patch/build pair |
| Stock Codex | Supported for standalone Qwendex CLI, receipts, routing, diagnostics, and recovery; it lacks Qwendex's native TUI/Manager patch guarantees |

Qwendex does not patch the npm-installed Codex binary in place. The optional
native integration is compiled from a separate pinned source checkout and
installed into an immutable Qwendex runtime generation with its matching
`codex-code-mode-host`.

## Hosted Models

| Model | Qwendex use | Codex 0.147 Manager V2 |
| --- | --- | --- |
| `gpt-5.6-sol` | User-selected/root authority where available | Supported by upstream model metadata; Qwendex does not force it for workers |
| `gpt-5.6-terra` | Default hosted worker at high; reviewer/release lanes at xhigh; explicit Terra seat | Supported |
| `gpt-5.6-luna` | Explicit one-shot Luna seat at max | Not supported as a V2 worker because the 0.147 catalog exposes Luna through the V1 family |
| `qwen-local` | Optional bounded local `qwendex_exec` lane | Not a native V2 model |

Actual model availability is account and backend dependent. Qwendex asks Codex
to validate native model/reasoning overrides and records requested execution
policy in its receipts.

## Codex 0.145–0.147 Feature Policy

| Upstream capability | Qwendex 0.7.0 policy |
| --- | --- |
| Portable plugins and skills | Native pass-through; Qwendex does not reimplement the catalog |
| V2 per-worker model/reasoning | Enabled for allowlisted Qwendex profiles |
| V2 child developer instructions | Enabled for the common bounded-worker contract |
| Native roles and role-driven child defaults | Deferred; passive project definitions are not activated by the Qwendex V2 schema |
| Child service-tier overrides | Deferred and sealed |
| `--approve-for-me` | Exposed as Qdex `auto-review` |
| `exec --full-auto` | Removed upstream; Qdex provides migration guidance |
| Project trust | Codex-native; Qdex does not blanket-mark the selected project trusted |
| MCP protocol `2026-07-28` | Experimental opt-in; legacy remains the supported default |
| MCP hosted Apps cached startup | Narrow degraded-ready behavior only for a non-empty account-scoped cache and non-auth transient failure |
| Token/rollout budgets | Upstream experimental behavior, not a Qwendex billing cap |
| Persistent history, memories, external import, Chronicle | Disabled in Qdex pending a retention/provenance/deletion contract |
| Conversation sections/import, Cursor sync, Bedrock search/compaction, audio/realtime | Native pass-through or out of Qwendex scope; no extra Qwendex claim |

The detailed maintainer review and rationale live in the tracked development
document `docs/development/codex-0.145-0.147-assessment.md`.

## Permission And Trust Boundary

Qdex permission modes are `workspace-write`, `auto-review`, and `yolo`.
Qdex rejects caller-supplied native permission flags and permission config so
the selected mode remains the effective reported posture. Qwendex reports
whether its generated `hooks.json` set is exact, but Qdex never enables the
global hook-trust bypass: Codex 0.147 applies it to project, config-layer, and
plugin hooks that this inventory cannot certify. Native hook and project trust
remain separate Codex-owned decisions.

## Evidence Required For A Release Claim

A supported release claim requires the matching source tree, canonical patched
Codex binary pair, config/schema parity, complete test and documentation gates,
privacy scanning, source-bound receipts, and the release-tier summary. Local
Qwen changes also require live bridge/fresh-home acceptance when the release
sets `QWENDEX_RELEASE_REQUIRE_LIVE=1`. See [Verification](verification.md).
