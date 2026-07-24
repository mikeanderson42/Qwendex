from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
QWENDEX = ROOT / "scripts" / "qwendex"


def load_docs():
    path = ROOT / "scripts" / "qwendex_docs.py"
    spec = importlib.util.spec_from_file_location("qwendex_docs_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def run_git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        capture_output=True,
        check=True,
    )


def init_repo(repo: Path) -> None:
    repo.mkdir(parents=True)
    run_git(repo, "init", "--quiet")
    run_git(repo, "config", "user.email", "docs-test@example.invalid")
    run_git(repo, "config", "user.name", "Docs Test")


def commit_all(repo: Path, message: str = "fixture") -> None:
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "--quiet", "-m", message)


def write_policy(
    repo: Path,
    *,
    repository_id: str = "fixture",
    required: tuple[str, ...] = ("README.md",),
    indexes: tuple[str, ...] = ("README.md",),
    startup: tuple[str, ...] = ("README.md",),
    public_roots: tuple[str, ...] = ("README.md", "docs"),
    extra: str = "",
) -> Path:
    policy = repo / "docs-policy.toml"
    required_toml = json.dumps(list(required))
    indexes_toml = json.dumps(list(indexes))
    startup_toml = json.dumps(list(startup))
    public_toml = json.dumps(list(public_roots))
    policy.write_text(
        f"""schema_version = "qwendex.docs.policy.v1"
repository_id = "{repository_id}"
documentation_roots = ["README.md", "docs"]
required_authority_documents = {required_toml}
index_documents = {indexes_toml}
startup_documents = {startup_toml}
public_documentation_roots = {public_toml}
local_documentation_roots = []
include = ["*.md", "**/*.md"]
exclude = ["docs/archive/**"]
important_documents = []
generated_private_patterns = ["build/**", "results/private/**"]
generated_inputs = []
deliverable_manifest_roots = []
forbidden_public_content_patterns = []

[responsibilities]
entrypoint = "README.md"

[responsibility_hierarchy]

[verification]

[severity_overrides]

[deliverable_evidence]
{extra}
""",
        encoding="utf-8",
    )
    return policy


def audit(repo: Path, policy: Path | None = None):
    docs = load_docs()
    return docs.audit_repository(
        repo,
        policy or repo / "docs-policy.toml",
        tool_version="test",
    )


def finding_ids(payload: dict) -> list[str]:
    return [str(item["rule_id"]) for item in payload["findings"]]


def test_tracked_discovery_excludes_ignored_generated_and_policy_excludes(tmp_path):
    repo = tmp_path / "repo"
    init_repo(repo)
    (repo / "docs").mkdir()
    (repo / "docs" / "archive").mkdir()
    (repo / "build").mkdir()
    (repo / ".gitignore").write_text("/build/\n", encoding="utf-8")
    (repo / "README.md").write_text("# Home\n\n[Guide](docs/guide.md)\n", encoding="utf-8")
    (repo / "docs" / "guide.md").write_text("# Guide\n", encoding="utf-8")
    (repo / "docs" / "archive" / "old.md").write_text("# Old\n", encoding="utf-8")
    (repo / "build" / "generated.md").write_text("# Generated\n", encoding="utf-8")
    policy = write_policy(repo)
    commit_all(repo)

    first = audit(repo, policy)

    assert first["status"] == "pass"
    assert first["dirty"] is False
    assert first["documentation_files"] == ["README.md", "docs/guide.md"]
    assert "build/generated.md" not in first["documentation_files"]
    assert "docs/archive/old.md" not in first["documentation_files"]

    (repo / "docs" / "guide.md").write_text("# Guide changed\n", encoding="utf-8")
    second = audit(repo, policy)
    assert second["status"] == "pass"
    assert second["dirty"] is True
    assert "docs/guide.md" in second["dirty_paths"]


def test_relative_links_duplicate_heading_anchors_and_fragments(tmp_path):
    repo = tmp_path / "repo"
    init_repo(repo)
    (repo / "docs").mkdir()
    (repo / "README.md").write_text(
        "# Home\n\n[Local](#home)\n[Second duplicate](docs/guide.md#repeat-1)\n",
        encoding="utf-8",
    )
    (repo / "docs" / "guide.md").write_text(
        "# Guide\n\n## Repeat\n\n## Repeat\n",
        encoding="utf-8",
    )
    policy = write_policy(repo)
    commit_all(repo)

    clean = audit(repo, policy)
    assert "DOC001" not in finding_ids(clean)

    (repo / "README.md").write_text(
        "# Home\n\n[Missing fragment](docs/guide.md#does-not-exist)\n",
        encoding="utf-8",
    )
    broken = audit(repo, policy)
    link_findings = [item for item in broken["findings"] if item["rule_id"] == "DOC001"]
    assert broken["status"] == "blocked"
    assert link_findings[0]["line"] == 3
    assert link_findings[0]["anchor"] == "does-not-exist"
    assert link_findings[0]["path"] == "README.md"


def test_link_path_traversal_and_policy_path_escape_are_rejected(tmp_path):
    repo = tmp_path / "repo"
    init_repo(repo)
    (repo / "README.md").write_text("# Home\n\n[Escape](../../outside.md)\n", encoding="utf-8")
    (repo / "docs").mkdir()
    policy = write_policy(repo)
    commit_all(repo)

    payload = audit(repo, policy)
    assert payload["status"] == "blocked"
    assert any("escapes the repository" in item["message"] for item in payload["findings"])

    policy.write_text(
        """schema_version = "qwendex.docs.policy.v1"
repository_id = "fixture"
documentation_roots = ["../outside"]
""",
        encoding="utf-8",
    )
    docs = load_docs()
    with pytest.raises(docs.PolicyError, match="repository-relative"):
        docs.load_policy(repo, policy)


def test_missing_authority_and_index_reachability_are_source_located(tmp_path):
    repo = tmp_path / "repo"
    init_repo(repo)
    (repo / "docs").mkdir()
    (repo / "README.md").write_text("# Home\n", encoding="utf-8")
    (repo / "docs" / "operations.md").write_text("# Operations\n", encoding="utf-8")
    policy = write_policy(repo, required=("README.md", "docs/operations.md", "docs/missing.md"))
    commit_all(repo)

    payload = audit(repo, policy)

    assert "DOC002" in finding_ids(payload)
    assert "DOC007" in finding_ids(payload)
    missing = next(item for item in payload["findings"] if item["rule_id"] == "DOC002")
    assert missing["path"] == "docs/missing.md"
    unindexed = next(item for item in payload["findings"] if item["rule_id"] == "DOC007")
    assert unindexed["path"] == "docs/operations.md"


def test_public_private_patterns_are_configurable_and_generic(tmp_path):
    repo = tmp_path / "repo"
    init_repo(repo)
    (repo / "docs").mkdir()
    (repo / "README.md").write_text(
        "# Home\n\nHost path: /home/example/private/place\nMarker: PRIVATE-CUSTOM-MARKER\n",
        encoding="utf-8",
    )
    policy = write_policy(repo)
    text = policy.read_text(encoding="utf-8").replace(
        "forbidden_public_content_patterns = []",
        'forbidden_public_content_patterns = ["PRIVATE-CUSTOM-MARKER"]',
    )
    policy.write_text(text, encoding="utf-8")
    commit_all(repo)

    payload = audit(repo, policy)
    boundary = [item for item in payload["findings"] if item["rule_id"] == "DOC004"]

    assert payload["status"] == "blocked"
    assert len(boundary) == 2
    assert {item["line"] for item in boundary} == {3, 4}


def test_findings_are_deterministic_and_narrow_suppression_expires(tmp_path):
    repo = tmp_path / "repo"
    init_repo(repo)
    (repo / "docs").mkdir()
    (repo / "README.md").write_text("# Home\n\n[Missing](docs/nope.md)\n", encoding="utf-8")
    policy = write_policy(repo)
    commit_all(repo)

    first = audit(repo, policy)
    second = audit(repo, policy)
    finding = first["findings"][0]

    assert first["schema_version"] == "qwendex.docs.audit.v1"
    assert first["deterministic_digest"] == second["deterministic_digest"]
    assert first["findings"] == second["findings"]

    policy.write_text(
        policy.read_text(encoding="utf-8")
        + f"""
[[suppressions]]
fingerprint = "{finding['fingerprint']}"
reason = "Temporary migration with a named owner."
owner = "docs-owner"
created_on = 2026-07-01
review_on = 2099-01-01
""",
        encoding="utf-8",
    )
    suppressed = audit(repo, policy)
    assert suppressed["status"] == "pass"
    assert suppressed["suppressed_count"] == 1

    policy.write_text(
        policy.read_text(encoding="utf-8").replace("review_on = 2099-01-01", "review_on = 2020-01-01"),
        encoding="utf-8",
    )
    expired = audit(repo, policy)
    assert expired["status"] == "blocked"
    assert expired["suppressed_count"] == 0


def test_conflicting_authority_and_warning_rules_are_source_located(tmp_path):
    repo = tmp_path / "repo"
    init_repo(repo)
    (repo / "docs").mkdir()
    (repo / "results" / "private").mkdir(parents=True)
    (repo / "README.md").write_text("# Home\n", encoding="utf-8")
    (repo / "docs" / "first.md").write_text("# Shared Authority\n", encoding="utf-8")
    (repo / "docs" / "second.md").write_text("# Shared Authority\n", encoding="utf-8")
    (repo / "docs" / "orphan.md").write_text("# Orphan\n", encoding="utf-8")
    (repo / "results" / "private" / "receipt.json").write_text("{}\n", encoding="utf-8")
    policy = write_policy(repo)
    policy.write_text(
        policy.read_text(encoding="utf-8")
        .replace(
            'entrypoint = "README.md"',
            'entrypoint = "README.md"\noperations = ["docs/first.md", "docs/second.md"]',
        )
        .replace(
            "important_documents = []",
            'important_documents = ["docs/orphan.md"]',
        )
        .replace(
            "[verification]\n",
            '[verification."README.md"]\nreviewed_on = 2020-01-01\ninterval_days = 1\n',
        ),
        encoding="utf-8",
    )
    commit_all(repo)

    payload = audit(repo, policy)
    findings = {item["rule_id"]: item for item in payload["findings"]}

    assert {"DOC005", "DOC008", "DOC009", "DOC010", "DOC011"} <= set(findings)
    assert findings["DOC008"]["path"] == "docs/orphan.md"
    assert findings["DOC009"]["path"] == "README.md"
    assert findings["DOC010"]["path"] == "docs/first.md"
    assert findings["DOC011"]["path"] == "results/private/receipt.json"


def test_publish_ready_generated_manifest_requires_evidence(tmp_path):
    repo = tmp_path / "repo"
    init_repo(repo)
    (repo / "docs").mkdir()
    (repo / "results").mkdir()
    (repo / ".gitignore").write_text("/results/\n", encoding="utf-8")
    (repo / "README.md").write_text("# Home\n", encoding="utf-8")
    policy = write_policy(repo)
    policy.write_text(
        policy.read_text(encoding="utf-8")
        .replace(
            "generated_inputs = []",
            'generated_inputs = ["results/quality.json"]',
        )
        .replace(
            "deliverable_manifest_roots = []",
            'deliverable_manifest_roots = ["results/quality.json"]',
        ),
        encoding="utf-8",
    )
    (repo / "results" / "quality.json").write_text(
        json.dumps(
            {
                "products": [
                    {
                        "product_id": "example-product",
                        "lifecycle_state": "publish_ready",
                        "evidence": {"inventory": True, "checksums": True, "qa": True},
                        "human_approval": {"present": False},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    commit_all(repo)

    blocked = audit(repo, policy)
    assert blocked["status"] == "blocked"
    assert "human_approval.present" in next(
        item["message"] for item in blocked["findings"] if item["rule_id"] == "DOC006"
    )

    manifest_path = repo / "results" / "quality.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["products"][0]["human_approval"]["present"] = True
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    passed = audit(repo, policy)
    assert passed["status"] == "pass"
    assert passed["deliverable_manifest_files"] == ["results/quality.json"]


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(QWENDEX), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_docs_cli_exit_codes_distinguish_findings_and_tool_errors(tmp_path):
    repo = tmp_path / "repo"
    init_repo(repo)
    (repo / "docs").mkdir()
    (repo / "README.md").write_text("# Home\n", encoding="utf-8")
    policy = write_policy(repo)
    commit_all(repo)

    passed = run_cli(
        "docs",
        "audit",
        "--repo",
        str(repo),
        "--policy",
        str(policy),
        "--json",
    )
    assert passed.returncode == 0
    assert json.loads(passed.stdout)["data"]["schema_version"] == "qwendex.docs.audit.v1"

    (repo / "README.md").write_text("# Home\n\n[Missing](docs/missing.md)\n", encoding="utf-8")
    blocked = run_cli(
        "docs",
        "audit",
        "--repo",
        str(repo),
        "--policy",
        str(policy),
        "--json",
    )
    assert blocked.returncode == 1
    assert json.loads(blocked.stdout)["status"] == "blocked"

    policy.write_text('schema_version = "wrong"\n', encoding="utf-8")
    failed = run_cli(
        "docs",
        "audit",
        "--repo",
        str(repo),
        "--policy",
        str(policy),
        "--json",
    )
    assert failed.returncode == 2
    assert json.loads(failed.stdout)["status"] == "fail"


def write_hub(repo: Path, *, duplicate: bool = False) -> Path:
    duplicate_entry = (
        """
[[repositories]]
id = "fixture"
path = "."
policy = "docs-policy.toml"
title = "Duplicate"
documents = ["README.md"]
"""
        if duplicate
        else ""
    )
    hub = repo / "hub.toml"
    hub.write_text(
        f"""schema_version = "qwendex.docs.hub.v1"
hub_id = "fixture-hub"
site_name = "Fixture Hub"
output_root = "var/qdx-docs"
nav_order = ["Fixture Documentation"]

[[repositories]]
id = "fixture"
path = "."
policy = "docs-policy.toml"
title = "Fixture"

[[repositories.sections]]
title = "Fixture Documentation"
documents = ["README.md", "docs"]
{duplicate_entry}

[mkdocs]
theme = "material"
use_directory_urls = false
""",
        encoding="utf-8",
    )
    return hub


def test_hub_rejects_duplicate_ids_and_requires_ignored_output(tmp_path):
    repo = tmp_path / "repo"
    init_repo(repo)
    (repo / "docs").mkdir()
    (repo / "README.md").write_text("# Home\n", encoding="utf-8")
    (repo / "docs" / "guide.md").write_text("# Guide\n", encoding="utf-8")
    write_policy(repo)
    hub = write_hub(repo, duplicate=True)
    commit_all(repo)
    docs = load_docs()

    with pytest.raises(docs.PolicyError, match="duplicate repository id"):
        docs.load_hub(hub)

    hub = write_hub(repo)
    with pytest.raises(docs.PolicyError, match="must be ignored"):
        docs.audit_hub(hub, tool_version="test")


def test_serve_rejects_non_loopback_and_invalid_port_before_build():
    docs = load_docs()

    with pytest.raises(docs.PolicyError, match="loopback"):
        docs.serve_hub(
            Path("not-read.toml"),
            tool_version="test",
            bind="0.0.0.0",
            port=8000,
        )
    with pytest.raises(docs.PolicyError, match="between 1 and 65535"):
        docs.serve_hub(
            Path("not-read.toml"),
            tool_version="test",
            bind="127.0.0.1",
            port=0,
        )


def test_audit_does_not_need_mkdocs_and_strict_build_uses_optional_binary(
    tmp_path, monkeypatch
):
    repo = tmp_path / "repo"
    init_repo(repo)
    (repo / "docs").mkdir()
    (repo / ".gitignore").write_text("/var/qdx-docs/\n", encoding="utf-8")
    (repo / "README.md").write_text("# Home\n\n[Guide](docs/guide.md)\n", encoding="utf-8")
    (repo / "docs" / "guide.md").write_text(
        "# Guide\n\n[Evidence](evidence.json)\n",
        encoding="utf-8",
    )
    (repo / "docs" / "evidence.json").write_text('{"status": "pass"}\n', encoding="utf-8")
    write_policy(repo)
    hub = write_hub(repo)
    commit_all(repo)
    docs = load_docs()

    monkeypatch.delenv("QWENDEX_MKDOCS_BIN", raising=False)
    monkeypatch.setattr(docs.shutil, "which", lambda _name: None)
    aggregate = docs.audit_hub(hub, tool_version="test")
    repeated = docs.audit_hub(hub, tool_version="test")
    assert aggregate["status"] == "pass"
    assert aggregate["deterministic_digest"] == repeated["deterministic_digest"]
    with pytest.raises(docs.DocsError, match="MkDocs is unavailable"):
        docs.build_hub(hub, tool_version="test", strict=True)

    fake = tmp_path / "fake-mkdocs"
    fake.write_text(
        """#!/usr/bin/env python3
import json
import pathlib
import sys

config = pathlib.Path(sys.argv[sys.argv.index("--config-file") + 1])
site_line = next(line for line in config.read_text(encoding="utf-8").splitlines() if line.startswith("site_dir: "))
site = pathlib.Path(json.loads(site_line.split(": ", 1)[1]))
site.mkdir(parents=True, exist_ok=True)
(site / "index.html").write_text("fixture", encoding="utf-8")
""",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    monkeypatch.setenv("QWENDEX_MKDOCS_BIN", str(fake))

    built = docs.build_hub(hub, tool_version="test", strict=True)

    assert built["status"] == "pass"
    assert built["strict"] is True
    assert built["files_staged"] == 3
    assert (
        Path(built["staging_path"]) / "docs" / "fixture" / "docs" / "evidence.json"
    ).is_file()
    assert any(
        item["kind"] == "linked_dependency"
        and item["source"] == "docs/evidence.json"
        for item in built["staged_files"]
    )
    assert (Path(built["output_path"]) / "index.html").is_file()
    assert Path(built["receipt_path"]).is_file()
