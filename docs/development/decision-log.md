# Qwendex Development Decision Log

## Worktree Over Generated Copy

Decision: `~/qwendex-dev` is a git worktree.

Reason: native git status, diff, staging, and branch history make it safer for a
product-development lane than a generated rsync copy.

## Dev Runtime Isolation

Decision: mutable dev state lives under `.qwendex-dev/`.

Reason: development receipts, ledgers, Codex home, source builds, and snapshots
must not mix with normal user harness state or public release artifacts.

## Patched Codex Contract

Decision: Qwendex patches Codex source by versioned anchors, not by mutating the
installed binary.

Reason: npm-installed Codex ships a native binary; source patching is auditable,
repeatable, and can block safely when Codex versions change.

## Kaveman Control Boundary

Decision: Qwendex exposes Kaveman as persisted mode state and a terse-output
directive that the patched Codex TUI injects into developer instructions, not
as a vendored external Git package.

Reason: the harness needs a connected footer/CLI/TUI control with low
maintenance cost; projects that want the upstream Caveman package can install it
separately.

## Manager Session Reconciliation

Decision: status, doctor, and Codex status refreshes reconcile stale read-only
manager lanes automatically, but stale writer lanes remain blocked until the
operator integrates or explicitly stops them.

Reason: stale read-only audit lanes should not keep Manager Mode healthy after a
TUI refresh, while writer lanes may represent unintegrated changes and must
stay visible.

## Local Qwen Authority

Decision: local Qwen is useful but never release authority.

Reason: Qwendex is built around receipts, guard markers, and GPT review gates;
release, security, architecture, and public claims require GPT/Codex review.
