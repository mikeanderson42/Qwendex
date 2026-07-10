# Qwendex Release Runbook

Use this sequence for a public release. Candidate verification may run on a
release branch, but only a clean, tagged default-branch commit can receive a
`publish-ready` recommendation.

## Release Artifact Contract

Qwendex releases the exact tagged git tree. No wheel, npm package, model weight,
host program, or locally built Codex binary is a Qwendex release artifact.
GitHub generates source archives from the tag; optional attached receipts must
be reviewed separately before upload.

Candidate review must remove runtime state, receipts, credentials, logs,
transcripts, machine-local config, private model inventory, model weights,
absolute operator paths, unsafe symlinks, and downstream-project workflows.
The release gate scans every tracked blob, including NUL-containing binary
content, for mechanically detectable private paths and secret-shaped material.
It also rejects runtime/config credential files, private env/netrc material,
archives, model weights, forbidden artifact paths, and unsafe symlinks rather
than checking only public documentation. CI runs this contract against the
actual checked-out commit before emitting its attestation.

## Prepare The Candidate

1. Start from the current default branch and create a named release branch.
2. Keep unrelated operator changes out of the branch.
3. Update every version source together:

   - `scripts/qwendex_cli.py` (`VERSION`)
   - `config/qwendex/qwendex.json`
   - `config/qwendex/qwendex.sample.json`
   - `README.md` current-release text
   - `public/qwendex/release-notes.md`
   - `RELEASE.md`

4. Update public docs and the decision log for durable release/architecture
   changes.
5. Run the required local gates:

```bash
source ~/qwendex-dev/.qwendex-dev/env.sh
qwendex-dev bootstrap
qwendex-dev doctor
qwendex-dev verify --tier quick
qwendex-dev verify --tier full
qwendex-dev snapshot
```

When the local stack is intentionally part of acceptance, also run:

```bash
qwendex-dev verify --tier live
```

The live tier records three independent gates: `live_launcher` checks the
configured model plus the canonical bridge `/status`; `live_reliability`
parses JSON/SSE assistant output and requires exact normalized `QWENDEX_OK`;
and `live_codex_acceptance` uses a fresh isolated Codex home to prove one to
three bounded successful `TOOL_OK` shell commands, an exact final `TOOL_OK`,
and an unchanged normal-home/XDG decoy. Its executable digest and size must
match the same run's validated Codex build evidence.

The launcher must also bind Codex to the same normalized `<bridge-base>/v1`
endpoint that passed preflight; a conflicting inherited `CODEX_OSS_BASE_URL`
is a blocker. For downstream acceptance, qdex working directory, add-dir, and
MCP trust must resolve to the selected target repository, not implicitly to the
Qwendex source tree.

Push the candidate branch and open a pull request only after local gates pass.
The GitHub `CI` workflow must pass; a merge with zero remote checks is not
release evidence. Its final job emits `qwendex-ci-attestation-<commit>`, bound to
the checked commit/tree, default ref, workflow run, strict surface checks,
same-root downstream installation, and tracked-artifact manifest.

## Candidate Evidence

The reusable release gate accepts `--candidate` on a non-default branch. That
mode verifies current-run receipts and the tracked artifact but can report only
`candidate-ready`, never `publish-ready`:

```bash
python3 scripts/qwendex_release_gate.py \
  --repo-root "$QWENDEX_DEV_ROOT" \
  --meta-root <isolated-run-meta-root> \
  --results-root <isolated-run-results-root> \
  --dev-status <isolated-run-meta-root>/dev_status.json \
  --output <isolated-run-meta-root>/release_validation_summary.json \
  --tier release \
  --expected-version <version> \
  --expected-tag v<version> \
  --run-id <run-id> \
  --run-started-at <UTC-ISO-8601> \
  --default-branch main \
  --candidate
```

Normal `qwendex-dev verify --tier release` creates the isolated run directory
and invokes this contract. Each required local receipt is JSON, current to that
run, inside its run directory, and carries a
`qwendex.dev.receipt_binding.v1` object. The binding records the local run ID,
generation time, gate and stable command ID, exact Qwendex commit/tree, and the
canonical payload SHA-256. Native `check`/`doctor` command and strict-health
fields are cross-checked instead of trusting the envelope alone. Candidate mode
may omit the trusted-origin, annotated-tag, default-branch, and remote-CI
publish contracts; missing, stale, mislabeled, failed, or source-mismatched
local evidence still blocks it.

## Final Default-Branch Gate

After review and CI pass, merge the release branch. Update the local default
branch with a fast-forward-only operation, verify a clean tree, and create the
local annotated tag without pushing it yet:

```bash
git switch main
git pull --ff-only origin main
git status --short
git tag -a v<version> -m "Qwendex v<version>"
export QWENDEX_RELEASE_TRUSTED_ORIGIN=https://github.com/<owner>/<repository>.git
qwendex-dev bootstrap --check
qwendex-dev doctor
qwendex-dev verify --tier full
qwendex-dev verify --tier release
qwendex-dev snapshot
python3 scripts/qwendex_release_gate.py verify-summary \
  --summary .qwendex-dev/results/meta/release_validation_summary.json \
  --require-publish-ready
```

Strict release verification ignores `QWENDEX_CI_ATTESTATION`: it selects and
downloads the matching default-branch CI artifact with `gh`, verifies the run
and artifact against GitHub online, compares the downloaded bytes/report, and
queries the trusted remote branch both before and after the gate. Local CI-file
overrides are candidate diagnostics only and cannot produce `publish-ready`.

If live acceptance is required for the release:

```bash
QWENDEX_RELEASE_REQUIRE_LIVE=1 qwendex-dev verify --tier release
```

That setting makes `live_launcher`, `live_reliability`, and
`live_codex_acceptance` required, current-run, source-bound release receipts.
Missing any one of them blocks the summary; an offline eval or configured-seat
receipt cannot substitute for them.

Read the exact per-run receipt and confirm all of these fields:

- `schema_version` is `qwendex.dev.release_summary.v2`
- `status` is `pass`
- `recommendation` is `publish-ready`
- `publish_ready` is `true`
- `source.clean` is `true`
- `source.branch` equals `source.default_branch`
- `source.remote_default_matches_head` is `true`
- `source.origin_matches_trusted` and `source.tag_annotated` are `true`
- `source.tag_matches_head` is `true`
- every `source.version_sources.*.value` is the release version
- every required `gates.*.passed` is `true`
- every `gates.*.release_binding` check is `true`
- when live acceptance was required, `gates.live_launcher`,
  `gates.live_reliability`, and `gates.live_codex_acceptance` all passed their
  native semantic checks and release bindings
- `ci_attestation.passed` and `source_recheck.matches_initial_source` are `true`
- `ci_attestation.online_verification.passed` and
  `source.trusted_remote.matches_expected` are `true`
- `artifact_contract.status` and `marker_scan.status` are `pass`
- `blockers`, `evidence_blockers`, and `publish_blockers` are empty

The fixed `.qwendex-dev/results/meta/release_validation_summary.json` is only a
latest-copy convenience. The isolated run receipt and its hashes are the
release evidence.

`receipt_sha256` is an unkeyed whole-file corruption/tamper indicator, not a
signature. `verify-summary --require-publish-ready` therefore also re-reads the
run-scoped gate files, rescans the current tagged tree, checks local source/tag
state, re-queries the trusted default branch, and redownloads/revalidates the
authoritative GitHub Actions artifact immediately before publication.

## Publish

Publishing mutates external state and requires explicit release authority. Only
after the final gate passes:

```bash
python3 scripts/qwendex_release_gate.py verify-summary \
  --summary .qwendex-dev/results/meta/release_validation_summary.json \
  --require-publish-ready
git push origin main
git push origin v<version>
gh release create v<version> \
  --verify-tag \
  --title "Qwendex v<version>" \
  --notes-file <reviewed-release-body>
gh release view v<version>
```

Do not upload `.qwendex-dev/`, raw receipts, locally built Codex binaries,
private model inventory, logs, or snapshots. Confirm the published tag resolves
to the verified commit and the release is neither draft nor prerelease unless
that state was intentional.

## Stop Conditions

Stop publication for any static/lint/test/config failure, missing/stale/failed
or source-mismatched receipt, dirty or non-default branch, untrusted/missing
origin, missing/misdirected/lightweight tag, version drift, artifact-boundary
hit, unexpected guard marker (including timeout markers), unsupported Codex
build, missing/stale/mismatched CI attestation, source drift during the gate, or
mismatch between the verified commit and the remote tag.
