# Documentation Quality

Qwendex provides a deterministic, repository-neutral documentation audit and
an optional local MkDocs view. The audit receipts are the product contract.
The generated site is only a navigation aid over tracked owner documents.

## Audit commands

Audit one repository:

```bash
scripts/qwendex docs audit \
  --repo . \
  --policy config/qwendex/docs-policy.toml \
  --json
```

Audit every repository declared by a private, repository-owned hub manifest:

```bash
scripts/qwendex docs audit --hub ../jarvis/qdex-hub.toml --json
```

The audit uses `git ls-files` by default. It does not need MkDocs and does not
walk ignored runtime state, dependency trees, caches, result archives, or
generated site output. A repository may opt a narrowly named generated
manifest into validation with `generated_inputs`.

Exit codes have stable meanings:

- `0`: audit completed without blocking findings;
- `1`: audit completed and found at least one blocking defect;
- `2`: policy, configuration, or execution failure.

JSON responses include timing, repository identity, branch and commit, dirty
state, policy path, file and severity counts, sorted source-located findings,
and a deterministic digest. Runtime timing is excluded from that digest, so
two unchanged runs have the same value.

## Policy contract

Policies use TOML schema `qwendex.docs.policy.v1`. The generic example at
`config/qwendex/docs-policy.example.toml` documents the supported fields:

- documentation roots, include/exclude patterns, and required authority;
- index/startup paths and explicitly important documents;
- public and local documentation boundaries;
- declared responsibilities and an optional authority hierarchy;
- explicit review dates for selected authority only;
- downstream-supplied forbidden public-content patterns;
- generated/private path patterns;
- narrow suppressions with an owner, reason, creation date, and review or
  expiry date;
- opt-in deliverable manifests and required evidence fields.

All paths are repository-relative. Path escapes, unknown fields, unsafe
private-tree inclusion, malformed patterns, and duplicate hub repository IDs
fail as tool/configuration errors. Qwendex supplies only generic secret and
host-path patterns; private repositories own their local boundary strings.

## Rules

| Rule | Severity | Checked surface | Acceptance |
| --- | --- | --- | --- |
| `DOC001` | blocking | active Markdown links and anchors | blocks |
| `DOC002` | blocking | required authority documents | blocks |
| `DOC003` | blocking | policy and hub manifests | blocks as a configuration error |
| `DOC004` | blocking | policy-declared public documentation | blocks |
| `DOC005` | blocking | canonical responsibility declarations | blocks |
| `DOC006` | blocking | opt-in publish-ready deliverable manifests | blocks |
| `DOC007` | warning | authority reachability from indexes/startup paths | does not block |
| `DOC008` | warning | policy-designated important documents | does not block |
| `DOC009` | warning | explicitly configured verification dates | does not block |
| `DOC010` | warning | active high-value titles/responsibilities | does not block |
| `DOC011` | warning by default | tracked generated/private patterns | policy-controlled |

Findings contain the rule, severity, message, path, line when available,
anchor or target when applicable, suggested fix, and stable fingerprint.
Qwendex does not generate a blanket debt baseline.

## Optional local hub

MkDocs and Material are isolated optional dependencies. Create a repository
local ignored environment explicitly:

```bash
python3 -m venv .qwendex-dev/venvs/docs
.qwendex-dev/venvs/docs/bin/python -m pip install \
  --requirement config/qwendex/docs-requirements.txt
export QWENDEX_MKDOCS_BIN="$PWD/.qwendex-dev/venvs/docs/bin/mkdocs"
```

Then build or serve a repository-owned hub:

```bash
scripts/qwendex docs build --hub ../jarvis/qdex-hub.toml --strict --json
scripts/qwendex docs serve --hub ../jarvis/qdex-hub.toml
```

The manifest uses schema `qwendex.docs.hub.v1`, portable sibling repository
paths, explicit tracked-document selections, and an ignored output root owned
by the manifest repository. A strict build audits all repositories first,
refuses blocking findings, stages configured tracked Markdown plus only the
tracked files reached by their local links, generates the
landing/workspace/audit-summary pages, invokes `mkdocs build --strict`, and
records source commits and the output path. It never copies an unfiltered
directory. `serve` accepts loopback addresses only and never installs or
enables a service.

An entry may run one explicit read-only repository quality command and include
its JSON result in the aggregate receipt. Domain state remains owned by that
repository; Qwendex never infers publication or revenue from documentation.

## Verification

The focused coverage is:

```bash
python3 -m pytest -q tests/smoke/test_qwendex_docs.py
```

`qwendex-dev verify --tier quick` runs the focused audit coverage and the
repository policy audit. MkDocs remains outside routine quick verification;
use a strict hub build when closing a cross-repository documentation baseline.
