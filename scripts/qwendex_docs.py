#!/usr/bin/env python3
"""Deterministic documentation audit and optional local MkDocs hub support.

The routine audit path is standard-library only. MkDocs is discovered only
when a caller explicitly requests ``docs build`` or ``docs serve``.
"""

from __future__ import annotations

import fnmatch
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import time
from collections import deque
from datetime import UTC, date, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Pattern, Sequence
from urllib.parse import unquote, urlsplit

import tomllib

POLICY_SCHEMA = "qwendex.docs.policy.v1"
HUB_SCHEMA = "qwendex.docs.hub.v1"
AUDIT_SCHEMA = "qwendex.docs.audit.v1"
HUB_AUDIT_SCHEMA = "qwendex.docs.hub_audit.v1"
BUILD_SCHEMA = "qwendex.docs.build.v1"

RULE_CATALOG: dict[str, dict[str, Any]] = {
    "DOC001": {
        "severity": "blocking",
        "surface": "active Markdown links and anchors",
        "description": "Broken internal Markdown link or local anchor.",
        "blocks_acceptance": True,
    },
    "DOC002": {
        "severity": "blocking",
        "surface": "policy-declared authority documents",
        "description": "Missing required authority document.",
        "blocks_acceptance": True,
    },
    "DOC003": {
        "severity": "blocking",
        "surface": "policy and hub manifests",
        "description": "Malformed or unsafe policy.",
        "blocks_acceptance": True,
    },
    "DOC004": {
        "severity": "blocking",
        "surface": "policy-declared public documentation",
        "description": "Public/private boundary violation.",
        "blocks_acceptance": True,
    },
    "DOC005": {
        "severity": "blocking",
        "surface": "canonical responsibility declarations",
        "description": "Conflicting canonical authority.",
        "blocks_acceptance": True,
    },
    "DOC006": {
        "severity": "blocking",
        "surface": "opt-in deliverable manifests",
        "description": "Publish-ready deliverable lacks required evidence.",
        "blocks_acceptance": True,
    },
    "DOC007": {
        "severity": "warning",
        "surface": "authority index reachability",
        "description": "Unindexed authority document.",
        "blocks_acceptance": False,
    },
    "DOC008": {
        "severity": "warning",
        "surface": "policy-designated important documents",
        "description": "Orphaned high-value document.",
        "blocks_acceptance": False,
    },
    "DOC009": {
        "severity": "warning",
        "surface": "explicit review dates",
        "description": "Explicit verification date exceeded.",
        "blocks_acceptance": False,
    },
    "DOC010": {
        "severity": "warning",
        "surface": "active high-value titles and responsibilities",
        "description": "Duplicate active title or responsibility.",
        "blocks_acceptance": False,
    },
    "DOC011": {
        "severity": "warning",
        "surface": "tracked generated/private paths",
        "description": "Tracked generated or private artifact.",
        "blocks_acceptance": False,
    },
}

SEVERITY_ORDER = {"blocking": 0, "warning": 1, "info": 2}
POLICY_FIELDS = {
    "schema_version",
    "repository_id",
    "documentation_roots",
    "required_authority_documents",
    "index_documents",
    "startup_documents",
    "public_documentation_roots",
    "local_documentation_roots",
    "include",
    "exclude",
    "important_documents",
    "generated_private_patterns",
    "generated_inputs",
    "deliverable_manifest_roots",
    "forbidden_public_content_patterns",
    "responsibilities",
    "responsibility_hierarchy",
    "verification",
    "severity_overrides",
    "suppressions",
    "deliverable_evidence",
}
HUB_FIELDS = {
    "schema_version",
    "hub_id",
    "site_name",
    "output_root",
    "nav_order",
    "repositories",
    "mkdocs",
}
HUB_REPOSITORY_FIELDS = {
    "id",
    "path",
    "policy",
    "title",
    "documents",
    "nav_group",
    "sections",
    "quality_command",
    "quality_receipt",
}
HUB_SECTION_FIELDS = {"title", "documents"}
DEFAULT_DENIED_DOC_PARTS = {
    ".git",
    ".qwendex",
    ".qwendex-dev",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
}
GENERIC_PUBLIC_PATTERNS: tuple[tuple[str, Pattern[str], str], ...] = (
    (
        "private-key",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
        "Remove private-key material from tracked public documentation.",
    ),
    (
        "operator-home",
        re.compile(r"(?<![A-Za-z0-9_.-])/(?:home|Users)/[A-Za-z0-9_.-]+/"),
        "Replace host-specific absolute paths with portable examples or downstream policy.",
    ),
    (
        "credential-assignment",
        re.compile(
            r"""(?ix)
            \b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|
            passphrase|webhook[_-]?secret|private[_-]?key)\b
            \s*[:=]\s*
            ["']?
            (?!<|\[|\$|\{|\*|example\b|sample\b|redacted\b|replace\b)
            [A-Za-z0-9_./+=:-]{12,}
            """
        ),
        "Replace credential-shaped values with an unmistakable placeholder.",
    ),
)

ATX_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")
SETEXT_RE = re.compile(r"^\s{0,3}(=+|-+)\s*$")
HTML_ANCHOR_RE = re.compile(r"""<a\s+(?:name|id)=["']([^"']+)["']""", re.IGNORECASE)
REFERENCE_DEFINITION_RE = re.compile(r"^\s{0,3}\[([^\]]+)\]:\s*(\S+)")
REFERENCE_LINK_RE = re.compile(r"!?\[[^\]]+\]\[([^\]]+)\]")
REPOSITORY_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


class DocsError(RuntimeError):
    """Execution failure for the documentation tooling."""


class PolicyError(DocsError):
    """Invalid or unsafe policy input."""


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _run(
    command: Sequence[str],
    *,
    cwd: Path,
    timeout: float = 30,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            [str(part) for part in command],
            cwd=cwd,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
            env=dict(env) if env is not None else None,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DocsError(f"command failed to execute: {' '.join(command)}: {exc}") from exc


def _git(repo_root: Path, *args: str, timeout: float = 30) -> str:
    result = _run(["git", "-C", str(repo_root), *args], cwd=repo_root, timeout=timeout)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise DocsError(f"git {' '.join(args)} failed for {repo_root}: {detail}")
    return result.stdout


def git_tracked_files(repo_root: Path) -> list[str]:
    raw = _git(repo_root, "ls-files", "-z")
    return sorted(item for item in raw.split("\0") if item)


def git_metadata(repo_root: Path) -> dict[str, Any]:
    branch = _git(repo_root, "branch", "--show-current").strip()
    commit = _git(repo_root, "rev-parse", "HEAD").strip()
    status_lines = [
        line
        for line in _git(repo_root, "status", "--short", "--untracked-files=all").splitlines()
        if line.strip()
    ]
    return {
        "git_branch": branch,
        "git_commit": commit,
        "dirty": bool(status_lines),
        "dirty_paths": [line[3:] if len(line) > 3 else line for line in status_lines],
    }


def _load_toml(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise PolicyError(f"cannot read TOML file {path}: {exc}") from exc
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise PolicyError(f"invalid TOML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise PolicyError(f"TOML root must be a table: {path}")
    return data


def _string_list(value: Any, field: str, *, allow_empty: bool = True) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise PolicyError(f"{field} must be an array of non-empty strings")
    result = [item.strip() for item in value]
    if not allow_empty and not result:
        raise PolicyError(f"{field} must not be empty")
    return result


def _relative_path(value: str, field: str, *, allow_dot: bool = True) -> str:
    path = PurePosixPath(value.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts:
        raise PolicyError(f"{field} must remain repository-relative: {value!r}")
    normalized = path.as_posix()
    if normalized in {"", "."}:
        if allow_dot:
            return "."
        raise PolicyError(f"{field} must name a file: {value!r}")
    if any(part in DEFAULT_DENIED_DOC_PARTS for part in path.parts):
        raise PolicyError(f"{field} includes an ignored/private tree: {value!r}")
    return normalized


def _generated_relative_path(value: str, field: str) -> str:
    path = PurePosixPath(value.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or path.as_posix() in {"", "."}:
        raise PolicyError(f"{field} must name one repository-relative generated file: {value!r}")
    if any(character in value for character in "*?[]"):
        raise PolicyError(f"{field} must be an exact path, not a broad pattern: {value!r}")
    return path.as_posix()


def _resolve_inside(root: Path, relative: str, field: str) -> Path:
    canonical_root = root.resolve()
    candidate = (canonical_root / relative).resolve(strict=False)
    try:
        candidate.relative_to(canonical_root)
    except ValueError as exc:
        raise PolicyError(f"{field} escapes repository root: {relative!r}") from exc
    return candidate


def _parse_iso_date(value: Any, field: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise PolicyError(f"{field} must be an ISO date: {value!r}") from exc
    raise PolicyError(f"{field} must be an ISO date")


def _normalize_responsibilities(value: Any, field: str) -> dict[str, list[str]]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise PolicyError(f"{field} must be a table")
    result: dict[str, list[str]] = {}
    for responsibility, raw_paths in value.items():
        if not isinstance(responsibility, str) or not responsibility.strip():
            raise PolicyError(f"{field} contains an invalid responsibility name")
        paths = [raw_paths] if isinstance(raw_paths, str) else _string_list(raw_paths, f"{field}.{responsibility}")
        result[responsibility] = [
            _relative_path(path, f"{field}.{responsibility}", allow_dot=False) for path in paths
        ]
    return result


def load_policy(repo_root: Path, policy_path: Path) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    policy_path = policy_path.resolve()
    try:
        policy_path.relative_to(repo_root)
    except ValueError as exc:
        raise PolicyError("policy file must be inside the audited repository") from exc
    data = _load_toml(policy_path)
    unknown = sorted(set(data) - POLICY_FIELDS)
    if unknown:
        raise PolicyError(f"unsupported policy fields: {', '.join(unknown)}")
    if data.get("schema_version") != POLICY_SCHEMA:
        raise PolicyError(f"schema_version must be {POLICY_SCHEMA!r}")
    repository_id = data.get("repository_id")
    if not isinstance(repository_id, str) or not REPOSITORY_ID_RE.fullmatch(repository_id):
        raise PolicyError("repository_id must be a lowercase portable identifier")

    path_list_fields = (
        "documentation_roots",
        "required_authority_documents",
        "index_documents",
        "startup_documents",
        "public_documentation_roots",
        "local_documentation_roots",
        "important_documents",
    )
    normalized: dict[str, Any] = dict(data)
    for field in path_list_fields:
        values = _string_list(data.get(field), field, allow_empty=field != "documentation_roots")
        normalized[field] = [
            _relative_path(value, field, allow_dot=field.endswith("_roots")) for value in values
        ]
    if not normalized["documentation_roots"]:
        raise PolicyError("documentation_roots must not be empty")

    normalized["include"] = _string_list(data.get("include"), "include") or ["*.md", "**/*.md"]
    normalized["exclude"] = _string_list(data.get("exclude"), "exclude")
    normalized["generated_private_patterns"] = _string_list(
        data.get("generated_private_patterns"), "generated_private_patterns"
    )
    normalized["forbidden_public_content_patterns"] = _string_list(
        data.get("forbidden_public_content_patterns"), "forbidden_public_content_patterns"
    )
    for pattern in normalized["forbidden_public_content_patterns"]:
        if pattern.startswith("regex:"):
            try:
                re.compile(pattern.removeprefix("regex:"))
            except re.error as exc:
                raise PolicyError(f"invalid forbidden public regex {pattern!r}: {exc}") from exc

    normalized["generated_inputs"] = [
        _generated_relative_path(value, "generated_inputs")
        for value in _string_list(data.get("generated_inputs"), "generated_inputs")
    ]
    normalized["deliverable_manifest_roots"] = []
    for value in _string_list(data.get("deliverable_manifest_roots"), "deliverable_manifest_roots"):
        path = PurePosixPath(value.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts or path.as_posix() in {"", "."}:
            raise PolicyError(f"deliverable_manifest_roots must remain repository-relative: {value!r}")
        normalized["deliverable_manifest_roots"].append(path.as_posix())

    normalized["responsibilities"] = _normalize_responsibilities(
        data.get("responsibilities"), "responsibilities"
    )
    normalized["responsibility_hierarchy"] = _normalize_responsibilities(
        data.get("responsibility_hierarchy"), "responsibility_hierarchy"
    )

    verification = data.get("verification") or {}
    if not isinstance(verification, dict):
        raise PolicyError("verification must be a table keyed by document path")
    normalized_verification: dict[str, dict[str, Any]] = {}
    for raw_path, raw_record in verification.items():
        path = _relative_path(str(raw_path), "verification", allow_dot=False)
        if not isinstance(raw_record, dict):
            raise PolicyError(f"verification.{raw_path} must be a table")
        unknown_record = sorted(set(raw_record) - {"reviewed_on", "interval_days"})
        if unknown_record:
            raise PolicyError(
                f"verification.{raw_path} has unsupported fields: {', '.join(unknown_record)}"
            )
        reviewed_on = _parse_iso_date(raw_record.get("reviewed_on"), f"verification.{raw_path}.reviewed_on")
        interval = raw_record.get("interval_days")
        if not isinstance(interval, int) or isinstance(interval, bool) or interval < 1:
            raise PolicyError(f"verification.{raw_path}.interval_days must be a positive integer")
        normalized_verification[path] = {
            "reviewed_on": reviewed_on,
            "interval_days": interval,
        }
    normalized["verification"] = normalized_verification

    overrides = data.get("severity_overrides") or {}
    if not isinstance(overrides, dict):
        raise PolicyError("severity_overrides must be a table")
    normalized_overrides: dict[str, str] = {}
    for rule_id, severity in overrides.items():
        if rule_id not in RULE_CATALOG:
            raise PolicyError(f"severity_overrides names an unsupported rule: {rule_id}")
        if severity not in SEVERITY_ORDER:
            raise PolicyError(f"invalid severity override for {rule_id}: {severity!r}")
        normalized_overrides[rule_id] = str(severity)
    normalized["severity_overrides"] = normalized_overrides

    suppressions = data.get("suppressions") or []
    if not isinstance(suppressions, list):
        raise PolicyError("suppressions must be an array of tables")
    normalized_suppressions: list[dict[str, Any]] = []
    for index, record in enumerate(suppressions):
        prefix = f"suppressions[{index}]"
        if not isinstance(record, dict):
            raise PolicyError(f"{prefix} must be a table")
        unknown_record = sorted(
            set(record)
            - {
                "fingerprint",
                "rule_id",
                "path",
                "reason",
                "owner",
                "created_on",
                "expires_on",
                "review_on",
            }
        )
        if unknown_record:
            raise PolicyError(f"{prefix} has unsupported fields: {', '.join(unknown_record)}")
        fingerprint = str(record.get("fingerprint") or "").strip()
        rule_id = str(record.get("rule_id") or "").strip()
        path_scope = str(record.get("path") or "").strip()
        if not fingerprint and not (rule_id and path_scope):
            raise PolicyError(f"{prefix} needs an exact fingerprint or narrow rule_id/path scope")
        if rule_id and rule_id not in RULE_CATALOG:
            raise PolicyError(f"{prefix}.rule_id is unsupported: {rule_id}")
        if path_scope:
            _relative_path(path_scope, f"{prefix}.path")
        reason = str(record.get("reason") or "").strip()
        owner = str(record.get("owner") or "").strip()
        if not reason or not owner:
            raise PolicyError(f"{prefix} requires reason and owner")
        created_on = _parse_iso_date(record.get("created_on"), f"{prefix}.created_on")
        expires_raw = record.get("expires_on")
        review_raw = record.get("review_on")
        if expires_raw is None and review_raw is None:
            raise PolicyError(f"{prefix} requires expires_on or review_on")
        normalized_suppressions.append(
            {
                "fingerprint": fingerprint,
                "rule_id": rule_id,
                "path": path_scope,
                "reason": reason,
                "owner": owner,
                "created_on": created_on,
                "expires_on": _parse_iso_date(expires_raw, f"{prefix}.expires_on")
                if expires_raw is not None
                else None,
                "review_on": _parse_iso_date(review_raw, f"{prefix}.review_on")
                if review_raw is not None
                else None,
            }
        )
    normalized["suppressions"] = normalized_suppressions

    deliverable = data.get("deliverable_evidence") or {}
    if not isinstance(deliverable, dict):
        raise PolicyError("deliverable_evidence must be a table")
    unknown_deliverable = sorted(
        set(deliverable) - {"publish_states", "required_fields", "state_field", "products_field"}
    )
    if unknown_deliverable:
        raise PolicyError(
            f"deliverable_evidence has unsupported fields: {', '.join(unknown_deliverable)}"
        )
    normalized["deliverable_evidence"] = {
        "publish_states": _string_list(deliverable.get("publish_states"), "deliverable_evidence.publish_states")
        or ["publish_ready", "published", "sale_verified"],
        "required_fields": _string_list(deliverable.get("required_fields"), "deliverable_evidence.required_fields")
        or [
            "evidence.inventory",
            "evidence.checksums",
            "evidence.qa",
            "human_approval.present",
        ],
        "state_field": str(deliverable.get("state_field") or "lifecycle_state"),
        "products_field": str(deliverable.get("products_field") or "products"),
    }

    public_roots = set(normalized["public_documentation_roots"])
    local_roots = set(normalized["local_documentation_roots"])
    overlap = sorted(public_roots & local_roots)
    if overlap:
        raise PolicyError(f"public and local documentation roots overlap: {', '.join(overlap)}")

    for field in path_list_fields:
        for value in normalized[field]:
            _resolve_inside(repo_root, value, field)
    for value in normalized["generated_inputs"]:
        _resolve_inside(repo_root, value, "generated_inputs")
    for value in normalized["deliverable_manifest_roots"]:
        _resolve_inside(repo_root, value, "deliverable_manifest_roots")
    normalized["policy_path"] = policy_path
    normalized["repository_root"] = repo_root
    return normalized


def _path_under(path: str, root: str) -> bool:
    if root == ".":
        return True
    return path == root or path.startswith(root.rstrip("/") + "/")


def _matches(path: str, patterns: Sequence[str]) -> bool:
    pure = PurePosixPath(path)
    return any(fnmatch.fnmatchcase(path, pattern) or pure.match(pattern) for pattern in patterns)


def discover_documentation(tracked: Sequence[str], policy: Mapping[str, Any]) -> list[str]:
    roots = policy["documentation_roots"]
    include = policy["include"]
    exclude = policy["exclude"]
    return sorted(
        path
        for path in tracked
        if path.lower().endswith(".md")
        and any(_path_under(path, root) for root in roots)
        and _matches(path, include)
        and not _matches(path, exclude)
    )


def _strip_heading_markup(value: str) -> tuple[str, str | None]:
    explicit: str | None = None
    explicit_match = re.search(r"\s*\{#([A-Za-z0-9_.:-]+)\}\s*$", value)
    if explicit_match:
        explicit = explicit_match.group(1)
        value = value[: explicit_match.start()]
    value = re.sub(r"`([^`]*)`", r"\1", value)
    value = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", value)
    value = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", value)
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value)
    value = re.sub(r"[*_~]", "", value)
    return value.strip(), explicit


def github_slug(value: str) -> str:
    value, _explicit = _strip_heading_markup(value)
    value = value.casefold()
    value = re.sub(r"[^\w\-\s]", "", value, flags=re.UNICODE)
    value = re.sub(r"\s+", "-", value.strip())
    return value


def _inline_link_destinations(line: str) -> list[str]:
    destinations: list[str] = []
    cursor = 0
    while True:
        marker = line.find("](", cursor)
        if marker < 0:
            break
        index = marker + 2
        depth = 1
        escaped = False
        angle = False
        while index < len(line):
            character = line[index]
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == "<" and depth == 1:
                angle = True
            elif character == ">" and angle:
                angle = False
            elif not angle and character == "(":
                depth += 1
            elif not angle and character == ")":
                depth -= 1
                if depth == 0:
                    raw = line[marker + 2 : index].strip()
                    if raw.startswith("<") and ">" in raw:
                        target = raw[1 : raw.index(">")]
                    else:
                        target = raw.split(maxsplit=1)[0] if raw else ""
                    if target:
                        destinations.append(target)
                    index += 1
                    break
            index += 1
        cursor = max(index, marker + 2)
    return destinations


def markdown_model(text: str) -> dict[str, Any]:
    lines = text.splitlines()
    references: dict[str, str] = {}
    fenced = False
    fence_marker = ""
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith(("```", "~~~")):
            marker = stripped[:3]
            if not fenced:
                fenced = True
                fence_marker = marker
            elif marker == fence_marker:
                fenced = False
            continue
        if fenced:
            continue
        match = REFERENCE_DEFINITION_RE.match(line)
        if match:
            references[match.group(1).strip().casefold()] = match.group(2).strip("<>")

    headings: list[dict[str, Any]] = []
    anchors: set[str] = set()
    slug_counts: dict[str, int] = {}
    links: list[dict[str, Any]] = []
    fenced = False
    fence_marker = ""
    previous_line = ""
    previous_line_number = 0
    title = ""
    for line_number, line in enumerate(lines, start=1):
        stripped = line.lstrip()
        if stripped.startswith(("```", "~~~")):
            marker = stripped[:3]
            if not fenced:
                fenced = True
                fence_marker = marker
            elif marker == fence_marker:
                fenced = False
            previous_line = ""
            previous_line_number = 0
            continue
        if fenced:
            continue

        heading_text = ""
        explicit_anchor: str | None = None
        heading_line = line_number
        match = ATX_HEADING_RE.match(line)
        if match:
            heading_text, explicit_anchor = _strip_heading_markup(match.group(2))
        elif previous_line and SETEXT_RE.match(line):
            heading_text, explicit_anchor = _strip_heading_markup(previous_line)
            heading_line = previous_line_number
        if heading_text:
            base = explicit_anchor or github_slug(heading_text)
            count = slug_counts.get(base, 0)
            anchor = explicit_anchor or (base if count == 0 else f"{base}-{count}")
            slug_counts[base] = count + 1
            anchors.add(anchor)
            headings.append({"text": heading_text, "anchor": anchor, "line": heading_line})
            if not title:
                title = heading_text

        for explicit in HTML_ANCHOR_RE.findall(line):
            anchors.add(explicit)
        for target in _inline_link_destinations(line):
            links.append({"target": target, "line": line_number})
        for reference in REFERENCE_LINK_RE.findall(line):
            target = references.get(reference.strip().casefold())
            if target:
                links.append({"target": target, "line": line_number})
        if line.strip() and not match:
            previous_line = line
            previous_line_number = line_number
        else:
            previous_line = ""
            previous_line_number = 0
    return {"anchors": anchors, "headings": headings, "links": links, "title": title}


def _finding(
    policy: Mapping[str, Any],
    rule_id: str,
    message: str,
    *,
    path: str = "",
    line: int | None = None,
    anchor: str = "",
    target: str = "",
    suggested_fix: str,
) -> dict[str, Any]:
    severity = policy["severity_overrides"].get(rule_id, RULE_CATALOG[rule_id]["severity"])
    stable = {
        "rule_id": rule_id,
        "message": message,
        "path": path,
        "line": line,
        "anchor": anchor,
        "target": target,
    }
    return {
        **stable,
        "severity": severity,
        "suggested_fix": suggested_fix,
        "fingerprint": sha256_json(stable),
    }


def _sort_findings(findings: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        findings,
        key=lambda item: (
            SEVERITY_ORDER.get(str(item.get("severity")), 99),
            str(item.get("rule_id")),
            str(item.get("path")),
            int(item.get("line") or 0),
            str(item.get("message")),
        ),
    )


def _active_suppression(finding: Mapping[str, Any], suppression: Mapping[str, Any]) -> bool:
    today = datetime.now(UTC).date()
    expires_on = suppression.get("expires_on")
    review_on = suppression.get("review_on")
    if isinstance(expires_on, date) and today > expires_on:
        return False
    if isinstance(review_on, date) and today > review_on:
        return False
    fingerprint = str(suppression.get("fingerprint") or "")
    if fingerprint:
        return fingerprint == finding.get("fingerprint")
    if suppression.get("rule_id") != finding.get("rule_id"):
        return False
    scope = str(suppression.get("path") or "")
    finding_path = str(finding.get("path") or "")
    return bool(scope) and (
        finding_path == scope
        or fnmatch.fnmatchcase(finding_path, scope)
        or PurePosixPath(finding_path).match(scope)
    )


def _read_markdown(
    repo_root: Path,
    path: str,
    cache: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if path in cache:
        return cache[path]
    absolute = repo_root / path
    try:
        text = absolute.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise DocsError(f"cannot read Markdown file {path}: {exc}") from exc
    model = markdown_model(text)
    model["text"] = text
    cache[path] = model
    return model


def _normalize_link_target(
    repo_root: Path,
    source_path: str,
    raw_target: str,
) -> tuple[str, str, str]:
    target = html.unescape(raw_target.strip())
    split = urlsplit(target)
    if split.scheme or split.netloc or target.startswith(("mailto:", "tel:", "data:")):
        return "", "", "external"
    fragment = unquote(split.fragment)
    raw_path = unquote(split.path)
    if not raw_path:
        return source_path, fragment, "local"
    source = PurePosixPath(source_path)
    if raw_path.startswith("/"):
        relative = PurePosixPath(raw_path.lstrip("/"))
    else:
        relative = source.parent / raw_path
    normalized = Path(os.path.normpath(relative.as_posix())).as_posix()
    if normalized == ".":
        normalized = source_path
    absolute = (repo_root / normalized).resolve(strict=False)
    try:
        absolute.relative_to(repo_root.resolve())
    except ValueError:
        return normalized, fragment, "escape"
    return normalized, fragment, "local"


def _link_findings(
    repo_root: Path,
    documents: Sequence[str],
    tracked: set[str],
    policy: Mapping[str, Any],
    cache: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, set[str]]]:
    findings: list[dict[str, Any]] = []
    graph: dict[str, set[str]] = {path: set() for path in documents}
    for source in documents:
        try:
            model = _read_markdown(repo_root, source, cache)
        except DocsError as exc:
            findings.append(
                _finding(
                    policy,
                    "DOC001",
                    str(exc),
                    path=source,
                    suggested_fix="Store active Markdown as readable UTF-8 text.",
                )
            )
            continue
        for link in model["links"]:
            raw_target = str(link["target"])
            target, anchor, kind = _normalize_link_target(repo_root, source, raw_target)
            if kind == "external":
                continue
            if kind == "escape":
                findings.append(
                    _finding(
                        policy,
                        "DOC001",
                        f"Internal link escapes the repository: {raw_target}",
                        path=source,
                        line=int(link["line"]),
                        target=raw_target,
                        suggested_fix="Use a repository-relative link to a tracked target.",
                    )
                )
                continue
            absolute = repo_root / target
            target_exists = target in tracked and absolute.exists()
            if not target_exists and absolute.is_dir():
                directory_prefix = target.rstrip("/") + "/"
                target_exists = any(item.startswith(directory_prefix) for item in tracked)
            if not target_exists:
                findings.append(
                    _finding(
                        policy,
                        "DOC001",
                        f"Internal link target is missing or untracked: {raw_target}",
                        path=source,
                        line=int(link["line"]),
                        target=raw_target,
                        suggested_fix="Repair the relative target or track the intended file.",
                    )
                )
                continue
            if target in graph and target.lower().endswith(".md"):
                graph[source].add(target)
            if anchor and target.lower().endswith(".md"):
                try:
                    target_model = _read_markdown(repo_root, target, cache)
                except DocsError as exc:
                    findings.append(
                        _finding(
                            policy,
                            "DOC001",
                            str(exc),
                            path=source,
                            line=int(link["line"]),
                            target=raw_target,
                            suggested_fix="Store the linked Markdown target as readable UTF-8 text.",
                        )
                    )
                    continue
                if anchor not in target_model["anchors"]:
                    findings.append(
                        _finding(
                            policy,
                            "DOC001",
                            f"Local anchor does not exist: #{anchor}",
                            path=source,
                            line=int(link["line"]),
                            anchor=anchor,
                            target=raw_target,
                            suggested_fix="Update the fragment to a current heading anchor.",
                        )
                    )
    return findings, graph


def _roots_match(path: str, roots: Sequence[str]) -> bool:
    return any(_path_under(path, root) for root in roots)


def _public_boundary_findings(
    repo_root: Path,
    documents: Sequence[str],
    policy: Mapping[str, Any],
    cache: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    configured: list[tuple[str, Pattern[str] | None, str]] = []
    for index, raw in enumerate(policy["forbidden_public_content_patterns"], start=1):
        if raw.startswith("regex:"):
            configured.append((f"configured-{index}", re.compile(raw.removeprefix("regex:")), raw))
        else:
            configured.append((f"configured-{index}", None, raw))
    for path in documents:
        if not _roots_match(path, policy["public_documentation_roots"]):
            continue
        model = _read_markdown(repo_root, path, cache)
        for line_number, line in enumerate(model["text"].splitlines(), start=1):
            for pattern_id, pattern, fix in GENERIC_PUBLIC_PATTERNS:
                if pattern.search(line):
                    findings.append(
                        _finding(
                            policy,
                            "DOC004",
                            f"Public documentation matches private-content guard {pattern_id}.",
                            path=path,
                            line=line_number,
                            target=pattern_id,
                            suggested_fix=fix,
                        )
                    )
            for pattern_id, pattern, raw in configured:
                matched = bool(pattern.search(line)) if pattern is not None else raw.casefold() in line.casefold()
                if matched:
                    findings.append(
                        _finding(
                            policy,
                            "DOC004",
                            f"Public documentation matches configured private-content guard {pattern_id}.",
                            path=path,
                            line=line_number,
                            target=pattern_id,
                            suggested_fix="Move private detail to the downstream local documentation root.",
                        )
                    )
    return findings


def _reachable(graph: Mapping[str, set[str]], starts: Sequence[str]) -> set[str]:
    queue: deque[str] = deque(path for path in starts if path in graph)
    reached = set(queue)
    while queue:
        current = queue.popleft()
        for target in sorted(graph.get(current, set())):
            if target not in reached:
                reached.add(target)
                queue.append(target)
    return reached


def _authority_findings(
    repo_root: Path,
    documents: Sequence[str],
    tracked: set[str],
    graph: Mapping[str, set[str]],
    policy: Mapping[str, Any],
    cache: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    required = list(policy["required_authority_documents"])
    for path in required:
        if path not in tracked or not (repo_root / path).is_file():
            findings.append(
                _finding(
                    policy,
                    "DOC002",
                    f"Required authority document is missing or untracked: {path}",
                    path=path,
                    suggested_fix="Restore and track the policy-declared authority document.",
                )
            )
    for responsibility, paths in sorted(policy["responsibilities"].items()):
        if len(paths) <= 1:
            continue
        hierarchy = policy["responsibility_hierarchy"].get(responsibility, [])
        if hierarchy != paths:
            findings.append(
                _finding(
                    policy,
                    "DOC005",
                    f"Responsibility {responsibility!r} has multiple canonical documents without an exact hierarchy.",
                    target=", ".join(paths),
                    suggested_fix="Declare one canonical document or an explicit ordered responsibility_hierarchy.",
                )
            )

    starts = [*policy["index_documents"], *policy["startup_documents"]]
    reached = _reachable(graph, starts)
    authority = set(required)
    for paths in policy["responsibilities"].values():
        authority.update(paths)
    for path in sorted(authority):
        if path in tracked and path not in reached and path not in starts:
            findings.append(
                _finding(
                    policy,
                    "DOC007",
                    f"Authority document is not reachable from an index or startup document: {path}",
                    path=path,
                    suggested_fix="Link the authority document from a configured index or startup path.",
                )
            )
    for path in sorted(set(policy["important_documents"]) - authority):
        if path in tracked and path not in reached and path not in starts:
            findings.append(
                _finding(
                    policy,
                    "DOC008",
                    f"Important document is not reachable from a configured index: {path}",
                    path=path,
                    suggested_fix="Add one deliberate index link or remove the important-document designation.",
                )
            )

    titles: dict[str, list[str]] = {}
    high_value = authority | set(policy["important_documents"])
    for path in sorted(high_value):
        if path not in tracked or path not in documents:
            continue
        title = str(_read_markdown(repo_root, path, cache).get("title") or "").strip()
        if title:
            titles.setdefault(title.casefold(), []).append(path)
    path_responsibilities: dict[str, set[str]] = {}
    for responsibility, paths in policy["responsibilities"].items():
        for path in paths:
            path_responsibilities.setdefault(path, set()).add(responsibility)
    for paths in titles.values():
        if len(paths) > 1:
            declared_roles = [path_responsibilities.get(path, set()) for path in paths]
            if all(declared_roles) and all(
                left.isdisjoint(right)
                for index, left in enumerate(declared_roles)
                for right in declared_roles[index + 1 :]
            ):
                continue
            findings.append(
                _finding(
                    policy,
                    "DOC010",
                    f"High-value documents share an active title: {', '.join(paths)}",
                    path=paths[0],
                    target=", ".join(paths[1:]),
                    suggested_fix="Clarify titles or designate the superseding authority explicitly.",
                )
            )
    return findings


def _staleness_findings(policy: Mapping[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    today = datetime.now(UTC).date()
    for path, record in sorted(policy["verification"].items()):
        deadline = record["reviewed_on"] + timedelta(days=int(record["interval_days"]))
        if today > deadline:
            findings.append(
                _finding(
                    policy,
                    "DOC009",
                    f"Explicit review interval expired on {deadline.isoformat()}.",
                    path=path,
                    suggested_fix="Review the authority content and update its explicit reviewed_on date.",
                )
            )
    return findings


def _tracked_private_findings(tracked: Sequence[str], policy: Mapping[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for path in tracked:
        if _matches(path, policy["generated_private_patterns"]):
            findings.append(
                _finding(
                    policy,
                    "DOC011",
                    f"Generated/private path is tracked: {path}",
                    path=path,
                    suggested_fix="Remove the artifact from tracking and keep it in an ignored generated location.",
                )
            )
    return findings


def _get_dotted(record: Any, dotted: str) -> Any:
    current = record
    for part in dotted.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def _manifest_files(
    repo_root: Path,
    roots: Sequence[str],
    tracked: set[str],
    generated_inputs: set[str],
) -> tuple[list[str], list[str]]:
    files: set[str] = set()
    missing: list[str] = []
    known = tracked | generated_inputs
    for root in roots:
        absolute = repo_root / root
        if absolute.is_file():
            files.add(root)
            continue
        if absolute.is_dir():
            prefix = root.rstrip("/") + "/"
            files.update(path for path in known if path.startswith(prefix) and path.lower().endswith(".json"))
            continue
        missing.append(root)
    return sorted(files), missing


def _deliverable_findings(
    repo_root: Path,
    tracked: set[str],
    policy: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    roots = policy["deliverable_manifest_roots"]
    if not roots:
        return [], []
    generated = set(policy["generated_inputs"])
    manifest_files, missing_roots = _manifest_files(repo_root, roots, tracked, generated)
    findings: list[dict[str, Any]] = []
    for root in missing_roots:
        findings.append(
            _finding(
                policy,
                "DOC006",
                f"Required deliverable manifest root is missing: {root}",
                path=root,
                suggested_fix="Generate the declared evidence receipt before claiming publish readiness.",
            )
        )
    settings = policy["deliverable_evidence"]
    for path in manifest_files:
        try:
            payload = json.loads((repo_root / path).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            findings.append(
                _finding(
                    policy,
                    "DOC006",
                    f"Deliverable manifest is unreadable or invalid JSON: {exc}",
                    path=path,
                    suggested_fix="Regenerate a valid machine-readable deliverable manifest.",
                )
            )
            continue
        products_raw = payload.get(settings["products_field"]) if isinstance(payload, dict) else None
        if isinstance(products_raw, dict):
            products = [
                {"product_id": product_id, **record}
                for product_id, record in products_raw.items()
                if isinstance(record, dict)
            ]
        elif isinstance(products_raw, list):
            products = [record for record in products_raw if isinstance(record, dict)]
        elif isinstance(payload, dict) and payload.get("product_id"):
            products = [payload]
        else:
            findings.append(
                _finding(
                    policy,
                    "DOC006",
                    "Deliverable manifest does not expose a product record collection.",
                    path=path,
                    suggested_fix=f"Provide {settings['products_field']!r} records or one product_id record.",
                )
            )
            continue
        for product in products:
            state = str(_get_dotted(product, settings["state_field"]) or "")
            if state not in settings["publish_states"]:
                continue
            missing_fields = [
                field for field in settings["required_fields"] if not _get_dotted(product, field)
            ]
            if missing_fields:
                product_id = str(product.get("product_id") or product.get("id") or "unknown")
                findings.append(
                    _finding(
                        policy,
                        "DOC006",
                        f"{product_id} is {state} without required evidence: {', '.join(missing_fields)}",
                        path=path,
                        target=product_id,
                        suggested_fix="Produce deterministic QA, inventory/checksum, and human-approval evidence.",
                    )
                )
    return findings, manifest_files


def deterministic_audit_digest(payload: Mapping[str, Any]) -> str:
    excluded = {"started_at", "completed_at", "duration_ms", "deterministic_digest"}

    def without_runtime_fields(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {
                key: without_runtime_fields(item)
                for key, item in value.items()
                if key not in excluded
            }
        if isinstance(value, list):
            return [without_runtime_fields(item) for item in value]
        return value

    stable = without_runtime_fields(payload)
    return sha256_json(stable)


def audit_repository(
    repo_root: Path,
    policy_path: Path,
    *,
    tool_version: str,
) -> dict[str, Any]:
    started_at = utc_now()
    started = time.monotonic()
    repo_root = repo_root.resolve()
    if not (repo_root / ".git").exists() and not _run(
        ["git", "-C", str(repo_root), "rev-parse", "--git-dir"], cwd=repo_root
    ).returncode == 0:
        raise DocsError(f"repository root is not a Git worktree: {repo_root}")
    policy = load_policy(repo_root, policy_path)
    tracked_list = git_tracked_files(repo_root)
    tracked = set(tracked_list)
    documents = discover_documentation(tracked_list, policy)
    cache: dict[str, dict[str, Any]] = {}

    findings, graph = _link_findings(repo_root, documents, tracked, policy, cache)
    findings.extend(_public_boundary_findings(repo_root, documents, policy, cache))
    findings.extend(
        _authority_findings(repo_root, documents, tracked, graph, policy, cache)
    )
    findings.extend(_staleness_findings(policy))
    findings.extend(_tracked_private_findings(tracked_list, policy))
    deliverable_findings, manifest_files = _deliverable_findings(repo_root, tracked, policy)
    findings.extend(deliverable_findings)

    suppressed: list[dict[str, Any]] = []
    active: list[dict[str, Any]] = []
    for finding in _sort_findings(findings):
        matching = next(
            (
                suppression
                for suppression in policy["suppressions"]
                if _active_suppression(finding, suppression)
            ),
            None,
        )
        if matching is None:
            active.append(finding)
        else:
            suppressed.append(
                {
                    "fingerprint": finding["fingerprint"],
                    "rule_id": finding["rule_id"],
                    "path": finding["path"],
                    "owner": matching["owner"],
                    "reason": matching["reason"],
                }
            )

    counts = {
        severity: sum(1 for finding in active if finding["severity"] == severity)
        for severity in ("blocking", "warning", "info")
    }
    metadata = git_metadata(repo_root)
    completed_at = utc_now()
    payload: dict[str, Any] = {
        "schema_version": AUDIT_SCHEMA,
        "tool_version": tool_version,
        "started_at": started_at,
        "completed_at": completed_at,
        "duration_ms": round((time.monotonic() - started) * 1000, 3),
        "repository_id": policy["repository_id"],
        "repository_root": str(repo_root),
        **metadata,
        "policy_path": str(policy["policy_path"]),
        "files_scanned": len(set(documents) | set(manifest_files)),
        "documentation_files": documents,
        "deliverable_manifest_files": manifest_files,
        "rules_run": [
            {
                "rule_id": rule_id,
                **record,
            }
            for rule_id, record in RULE_CATALOG.items()
        ],
        "blocking_count": counts["blocking"],
        "warning_count": counts["warning"],
        "info_count": counts["info"],
        "suppressed_count": len(suppressed),
        "status": "blocked" if counts["blocking"] else "pass",
        "findings": active,
        "suppressions_applied": suppressed,
    }
    payload["deterministic_digest"] = deterministic_audit_digest(payload)
    return payload


def _safe_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _hub_relative_path(base: Path, value: Any, field: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise PolicyError(f"{field} must be a non-empty path string")
    path = Path(value)
    if path.is_absolute():
        raise PolicyError(f"{field} must be portable and relative to the hub manifest")
    return (base / path).resolve()


def _hub_documents(entry: Mapping[str, Any]) -> list[str]:
    documents = list(entry.get("documents") or [])
    for section in entry.get("sections") or []:
        documents.extend(section["documents"])
    return list(dict.fromkeys(documents))


def load_hub(hub_path: Path) -> dict[str, Any]:
    hub_path = hub_path.resolve()
    data = _load_toml(hub_path)
    unknown = sorted(set(data) - HUB_FIELDS)
    if unknown:
        raise PolicyError(f"unsupported hub fields: {', '.join(unknown)}")
    if data.get("schema_version") != HUB_SCHEMA:
        raise PolicyError(f"hub schema_version must be {HUB_SCHEMA!r}")
    hub_id = data.get("hub_id")
    if not isinstance(hub_id, str) or not REPOSITORY_ID_RE.fullmatch(hub_id):
        raise PolicyError("hub_id must be a lowercase portable identifier")
    site_name = data.get("site_name")
    if not isinstance(site_name, str) or not site_name.strip():
        raise PolicyError("site_name must be a non-empty string")
    base = hub_path.parent
    output_root = _hub_relative_path(base, data.get("output_root"), "output_root")
    try:
        output_root.relative_to(base.resolve())
    except ValueError as exc:
        raise PolicyError("output_root must remain inside the hub-owner repository") from exc

    entries = data.get("repositories")
    if not isinstance(entries, list) or not entries:
        raise PolicyError("repositories must be a non-empty array of tables")
    normalized_entries: list[dict[str, Any]] = []
    repository_ids: set[str] = set()
    for index, entry in enumerate(entries):
        prefix = f"repositories[{index}]"
        if not isinstance(entry, dict):
            raise PolicyError(f"{prefix} must be a table")
        unknown_entry = sorted(set(entry) - HUB_REPOSITORY_FIELDS)
        if unknown_entry:
            raise PolicyError(f"{prefix} has unsupported fields: {', '.join(unknown_entry)}")
        repository_id = entry.get("id")
        if not isinstance(repository_id, str) or not REPOSITORY_ID_RE.fullmatch(repository_id):
            raise PolicyError(f"{prefix}.id must be a lowercase portable identifier")
        if repository_id in repository_ids:
            raise PolicyError(f"duplicate repository id in hub: {repository_id}")
        repository_ids.add(repository_id)
        repo_root = _hub_relative_path(base, entry.get("path"), f"{prefix}.path")
        policy_path = _hub_relative_path(repo_root, entry.get("policy"), f"{prefix}.policy")
        title = entry.get("title")
        if not isinstance(title, str) or not title.strip():
            raise PolicyError(f"{prefix}.title must be a non-empty string")
        documents = [
            _relative_path(value, f"{prefix}.documents", allow_dot=False)
            for value in _string_list(entry.get("documents"), f"{prefix}.documents")
        ]
        sections_raw = entry.get("sections") or []
        if not isinstance(sections_raw, list):
            raise PolicyError(f"{prefix}.sections must be an array of tables")
        sections: list[dict[str, Any]] = []
        for section_index, section in enumerate(sections_raw):
            section_prefix = f"{prefix}.sections[{section_index}]"
            if not isinstance(section, dict):
                raise PolicyError(f"{section_prefix} must be a table")
            unknown_section = sorted(set(section) - HUB_SECTION_FIELDS)
            if unknown_section:
                raise PolicyError(
                    f"{section_prefix} has unsupported fields: {', '.join(unknown_section)}"
                )
            section_title = section.get("title")
            if not isinstance(section_title, str) or not section_title.strip():
                raise PolicyError(f"{section_prefix}.title must be a non-empty string")
            section_documents = [
                _relative_path(value, f"{section_prefix}.documents", allow_dot=False)
                for value in _string_list(
                    section.get("documents"), f"{section_prefix}.documents", allow_empty=False
                )
            ]
            sections.append({"title": section_title.strip(), "documents": section_documents})
        quality_command = _string_list(entry.get("quality_command"), f"{prefix}.quality_command")
        quality_receipt_raw = entry.get("quality_receipt")
        quality_receipt = (
            _relative_path(str(quality_receipt_raw), f"{prefix}.quality_receipt", allow_dot=False)
            if quality_receipt_raw
            else ""
        )
        normalized_entries.append(
            {
                "id": repository_id,
                "path": repo_root,
                "policy": policy_path,
                "title": title.strip(),
                "documents": documents,
                "nav_group": str(entry.get("nav_group") or title).strip(),
                "sections": sections,
                "quality_command": quality_command,
                "quality_receipt": quality_receipt,
            }
        )

    mkdocs = data.get("mkdocs") or {}
    if not isinstance(mkdocs, dict):
        raise PolicyError("mkdocs must be a table")
    unknown_mkdocs = sorted(set(mkdocs) - {"theme", "use_directory_urls"})
    if unknown_mkdocs:
        raise PolicyError(f"mkdocs has unsupported fields: {', '.join(unknown_mkdocs)}")
    normalized = {
        "schema_version": HUB_SCHEMA,
        "hub_id": hub_id,
        "site_name": site_name.strip(),
        "output_root": output_root,
        "nav_order": _string_list(data.get("nav_order"), "nav_order"),
        "repositories": normalized_entries,
        "mkdocs": {
            "theme": str(mkdocs.get("theme") or "material"),
            "use_directory_urls": bool(mkdocs.get("use_directory_urls", False)),
        },
        "hub_path": hub_path,
        "base": base.resolve(),
    }
    if len(normalized["nav_order"]) != len(set(normalized["nav_order"])):
        raise PolicyError("nav_order must not contain duplicate section titles")
    return normalized


def _require_ignored_output(hub: Mapping[str, Any]) -> None:
    output_root = Path(hub["output_root"])
    base = Path(hub["base"])
    try:
        relative = output_root.relative_to(base).as_posix()
    except ValueError as exc:
        raise PolicyError("generated output must remain inside the hub-owner repository") from exc
    probe = relative.rstrip("/") + "/.qwendex-ignore-probe"
    result = _run(
        ["git", "-C", str(base), "check-ignore", "--no-index", "-q", probe],
        cwd=base,
    )
    if result.returncode != 0:
        raise PolicyError(f"hub output_root must be ignored before use: {relative}")


def _quality_result(entry: Mapping[str, Any]) -> dict[str, Any] | None:
    command = list(entry["quality_command"])
    if not command:
        return None
    result = _run(command, cwd=Path(entry["path"]), timeout=180)
    if result.returncode not in {0, 1}:
        detail = (result.stderr or result.stdout).strip()[-1000:]
        raise DocsError(
            f"quality command failed for {entry['id']} with exit {result.returncode}: {detail}"
        )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise DocsError(f"quality command for {entry['id']} did not emit JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise DocsError(f"quality command for {entry['id']} emitted a non-object JSON value")
    blocking = payload.get("blocking_count")
    if not isinstance(blocking, int):
        blocking_products = payload.get("blocking_products")
        blocking = (
            blocking_products
            if isinstance(blocking_products, int)
            else len(blocking_products)
            if isinstance(blocking_products, list)
            else 0
        )
    return {
        "repository_id": entry["id"],
        "command": command,
        "exit_code": result.returncode,
        "status": str(payload.get("status") or ("blocked" if blocking else "pass")),
        "blocking_count": int(blocking),
        "warning_count": int(payload.get("warning_count") or 0),
        "receipt_path": str(Path(entry["path"]) / entry["quality_receipt"])
        if entry["quality_receipt"]
        else "",
        "data": payload,
    }


def audit_hub(
    hub_path: Path,
    *,
    tool_version: str,
    write_receipt: bool = True,
) -> dict[str, Any]:
    started_at = utc_now()
    started = time.monotonic()
    hub = load_hub(hub_path)
    _require_ignored_output(hub)
    audits: list[dict[str, Any]] = []
    quality: list[dict[str, Any]] = []
    for entry in hub["repositories"]:
        audit = audit_repository(Path(entry["path"]), Path(entry["policy"]), tool_version=tool_version)
        if audit["repository_id"] != entry["id"]:
            raise PolicyError(
                f"hub id {entry['id']!r} does not match policy repository_id "
                f"{audit['repository_id']!r}"
            )
        audits.append(audit)
        quality_result = _quality_result(entry)
        if quality_result is not None:
            quality.append(quality_result)
    blocking_count = sum(int(item["blocking_count"]) for item in audits) + sum(
        int(item["blocking_count"]) for item in quality
    )
    warning_count = sum(int(item["warning_count"]) for item in audits) + sum(
        int(item["warning_count"]) for item in quality
    )
    payload: dict[str, Any] = {
        "schema_version": HUB_AUDIT_SCHEMA,
        "tool_version": tool_version,
        "started_at": started_at,
        "completed_at": utc_now(),
        "duration_ms": round((time.monotonic() - started) * 1000, 3),
        "hub_id": hub["hub_id"],
        "hub_path": str(hub["hub_path"]),
        "repositories_checked": len(audits),
        "repository_ids": [item["repository_id"] for item in audits],
        "files_scanned": sum(int(item["files_scanned"]) for item in audits),
        "blocking_count": blocking_count,
        "warning_count": warning_count,
        "info_count": sum(int(item["info_count"]) for item in audits),
        "status": "blocked" if blocking_count else "pass",
        "repositories": audits,
        "quality": quality,
    }
    payload["deterministic_digest"] = deterministic_audit_digest(payload)
    if write_receipt:
        receipt = Path(hub["output_root"]) / "receipts" / "audit-latest.json"
        _safe_write_json(receipt, payload)
        payload["receipt_path"] = str(receipt)
    return payload


def _expand_build_documents(entry: Mapping[str, Any], tracked: Sequence[str]) -> list[str]:
    selected: set[str] = set()
    for requested in _hub_documents(entry):
        if requested in tracked and requested.lower().endswith(".md"):
            selected.add(requested)
            continue
        prefix = requested.rstrip("/") + "/"
        matches = [
            path for path in tracked if path.startswith(prefix) and path.lower().endswith(".md")
        ]
        if not matches:
            raise PolicyError(
                f"hub document selection is missing or untracked in {entry['id']}: {requested}"
            )
        selected.update(matches)
    if not selected:
        raise PolicyError(f"hub repository {entry['id']} has no selected documents")
    return sorted(selected)


def _expand_build_dependencies(
    entry: Mapping[str, Any],
    selected: Sequence[str],
    tracked: set[str],
) -> list[str]:
    """Include only tracked files linked from configured Markdown.

    Dependencies are link-reachable from the manifest selection and retain the
    owning repository's relative layout. Markdown dependencies are traversed so
    their own local links remain valid without copying an unfiltered directory.
    """

    repo_root = Path(entry["path"])
    staged = set(selected)
    pending = [path for path in selected if path.lower().endswith(".md")]
    cache: dict[str, dict[str, Any]] = {}
    while pending:
        source = pending.pop()
        model = _read_markdown(repo_root, source, cache)
        for link in model["links"]:
            target, _anchor, kind = _normalize_link_target(
                repo_root,
                source,
                str(link["target"]),
            )
            if kind != "local" or target in staged:
                continue
            if target not in tracked or not (repo_root / target).is_file():
                continue
            staged.add(target)
            if target.lower().endswith(".md"):
                pending.append(target)
    return sorted(staged)


def _document_title(path: Path) -> str:
    try:
        model = markdown_model(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return path.stem.replace("-", " ").replace("_", " ").title()
    return str(model.get("title") or path.stem.replace("-", " ").replace("_", " ").title())


def _yaml_scalar(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _nav_yaml(nav: Sequence[tuple[str, str | Sequence[tuple[str, str]]]]) -> list[str]:
    lines = ["nav:"]
    for title, value in nav:
        if isinstance(value, str):
            lines.append(f"  - {_yaml_scalar(title)}: {_yaml_scalar(value)}")
        else:
            lines.append(f"  - {_yaml_scalar(title)}:")
            for child_title, child_path in value:
                lines.append(
                    f"      - {_yaml_scalar(child_title)}: {_yaml_scalar(child_path)}"
                )
    return lines


def _workspace_map(audit: Mapping[str, Any]) -> str:
    lines = [
        "# Workspace Map",
        "",
        "| Repository | Branch | Commit | Dirty | Documentation status |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in audit["repositories"]:
        lines.append(
            f"| `{item['repository_id']}` | `{item['git_branch'] or '(detached)'}` | "
            f"`{item['git_commit'][:12]}` | `{'yes' if item['dirty'] else 'no'}` | "
            f"`{item['status']}` |"
        )
    lines.extend(
        [
            "",
            "This generated view links to tracked authority. It is not a new source of truth.",
            "",
        ]
    )
    return "\n".join(lines)


def _audit_summary_markdown(audit: Mapping[str, Any]) -> str:
    lines = [
        "# Current Audit Summary",
        "",
        f"- Status: `{audit['status']}`",
        f"- Repositories: `{audit['repositories_checked']}`",
        f"- Files scanned: `{audit['files_scanned']}`",
        f"- Blocking findings: `{audit['blocking_count']}`",
        f"- Warnings: `{audit['warning_count']}`",
        f"- Deterministic digest: `{audit['deterministic_digest']}`",
        "",
        "## Repository results",
        "",
        "| Repository | Blocking | Warnings | Digest |",
        "| --- | ---: | ---: | --- |",
    ]
    for item in audit["repositories"]:
        lines.append(
            f"| `{item['repository_id']}` | {item['blocking_count']} | "
            f"{item['warning_count']} | `{item['deterministic_digest']}` |"
        )
    if audit["quality"]:
        lines.extend(
            [
                "",
                "## Deliverable quality",
                "",
                "| Repository | Status | Blocking | Warnings |",
                "| --- | --- | ---: | ---: |",
            ]
        )
        for item in audit["quality"]:
            lines.append(
                f"| `{item['repository_id']}` | `{item['status']}` | "
                f"{item['blocking_count']} | {item['warning_count']} |"
            )
    lines.extend(
        [
            "",
            "Generated summaries are evidence from this run; repository owner documents remain authoritative.",
            "",
        ]
    )
    return "\n".join(lines)


def _landing_page(hub: Mapping[str, Any], audit: Mapping[str, Any]) -> str:
    repository_links = "\n".join(
        f"- [{entry['title']}](workspace-map.md)"
        for entry in hub["repositories"]
    )
    return (
        f"# {hub['site_name']}\n\n"
        "This local site is a generated navigation view over tracked Markdown. "
        "Edit the owning repository documents, never the staging copy.\n\n"
        f"- Current audit status: `{audit['status']}`\n"
        f"- Current audit digest: `{audit['deterministic_digest']}`\n\n"
        "## Workspaces\n\n"
        f"{repository_links}\n"
    )


def _mkdocs_binary() -> str:
    explicit = os.environ.get("QWENDEX_MKDOCS_BIN", "").strip()
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_file() or not os.access(path, os.X_OK):
            raise DocsError(f"QWENDEX_MKDOCS_BIN is not executable: {path}")
        return str(path)
    discovered = shutil.which("mkdocs")
    if not discovered:
        raise DocsError(
            "MkDocs is unavailable; install the documented optional docs environment "
            "before running build or serve"
        )
    return discovered


def build_hub(
    hub_path: Path,
    *,
    tool_version: str,
    strict: bool,
) -> dict[str, Any]:
    started_at = utc_now()
    started = time.monotonic()
    hub = load_hub(hub_path)
    _require_ignored_output(hub)
    audit = audit_hub(hub_path, tool_version=tool_version, write_receipt=True)
    if strict and audit["blocking_count"]:
        return {
            "schema_version": BUILD_SCHEMA,
            "tool_version": tool_version,
            "started_at": started_at,
            "completed_at": utc_now(),
            "duration_ms": round((time.monotonic() - started) * 1000, 3),
            "status": "blocked",
            "strict": True,
            "audit": audit,
            "output_path": "",
            "error": "strict build refused because the aggregate audit has blocking findings",
        }

    output_root = Path(hub["output_root"])
    staging = output_root / "staging"
    docs_dir = staging / "docs"
    site_dir = output_root / "site"
    for generated in (staging, site_dir):
        try:
            generated.relative_to(output_root)
        except ValueError as exc:
            raise PolicyError("generated build path escaped output_root") from exc
        if generated.exists():
            shutil.rmtree(generated)
    docs_dir.mkdir(parents=True, exist_ok=True)

    nav: list[tuple[str, str | Sequence[tuple[str, str]]]] = [
        ("Qdex Workspace Hub", "index.md"),
        ("Workspace Map", "workspace-map.md"),
        ("Current Audit Summary", "audit-summary.md"),
    ]
    grouped_nav: dict[str, list[tuple[str, str]]] = {}
    copied: list[dict[str, Any]] = []
    for entry in hub["repositories"]:
        tracked = git_tracked_files(Path(entry["path"]))
        selected = _expand_build_documents(entry, tracked)
        selected_set = set(selected)
        staged_files = _expand_build_dependencies(entry, selected, set(tracked))
        for relative in staged_files:
            source = Path(entry["path"]) / relative
            destination = docs_dir / entry["id"] / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            copied.append(
                {
                    "repository_id": entry["id"],
                    "source": relative,
                    "staged": destination.relative_to(docs_dir).as_posix(),
                    "kind": "document" if relative in selected_set else "linked_dependency",
                }
            )
        sections = entry["sections"] or [
            {
                "title": entry["nav_group"],
                "documents": entry["documents"] or selected,
            }
        ]
        for section in sections:
            children: list[tuple[str, str]] = []
            section_paths: set[str] = set()
            for requested in section["documents"]:
                if requested in selected_set:
                    section_paths.add(requested)
                else:
                    prefix = requested.rstrip("/") + "/"
                    section_paths.update(path for path in selected if path.startswith(prefix))
            for relative in sorted(section_paths):
                staged_relative = f"{entry['id']}/{relative}"
                children.append((_document_title(Path(entry["path"]) / relative), staged_relative))
            if children:
                grouped_nav.setdefault(section["title"], []).extend(children)

    ordered_titles = list(hub["nav_order"])
    ordered_titles.extend(title for title in grouped_nav if title not in ordered_titles)
    unknown_titles = [title for title in ordered_titles if title not in grouped_nav]
    if unknown_titles:
        raise PolicyError(
            f"nav_order references section titles with no selected documents: {', '.join(unknown_titles)}"
        )
    nav.extend((title, grouped_nav[title]) for title in ordered_titles)

    (docs_dir / "index.md").write_text(_landing_page(hub, audit), encoding="utf-8")
    (docs_dir / "workspace-map.md").write_text(_workspace_map(audit), encoding="utf-8")
    (docs_dir / "audit-summary.md").write_text(
        _audit_summary_markdown(audit), encoding="utf-8"
    )
    config_path = staging / "mkdocs.yml"
    config_lines = [
        f"site_name: {_yaml_scalar(hub['site_name'])}",
        "docs_dir: docs",
        f"site_dir: {_yaml_scalar(str(site_dir))}",
        f"use_directory_urls: {'true' if hub['mkdocs']['use_directory_urls'] else 'false'}",
        "theme:",
        f"  name: {_yaml_scalar(hub['mkdocs']['theme'])}",
        "validation:",
        "  omitted_files: warn",
        "  absolute_links: warn",
        "  unrecognized_links: warn",
        *_nav_yaml(nav),
        "",
    ]
    config_path.write_text("\n".join(config_lines), encoding="utf-8")

    mkdocs = _mkdocs_binary()
    command = [mkdocs, "build", "--config-file", str(config_path)]
    if strict:
        command.append("--strict")
    result = _run(command, cwd=staging, timeout=300)
    status = "pass" if result.returncode == 0 and site_dir.is_dir() else "error"
    receipt: dict[str, Any] = {
        "schema_version": BUILD_SCHEMA,
        "tool_version": tool_version,
        "started_at": started_at,
        "completed_at": utc_now(),
        "duration_ms": round((time.monotonic() - started) * 1000, 3),
        "status": status,
        "strict": strict,
        "hub_id": hub["hub_id"],
        "hub_path": str(hub["hub_path"]),
        "audit_digest": audit["deterministic_digest"],
        "source_commits": {
            item["repository_id"]: {
                "branch": item["git_branch"],
                "commit": item["git_commit"],
                "dirty": item["dirty"],
            }
            for item in audit["repositories"]
        },
        "files_staged": len(copied),
        "staged_files": copied,
        "mkdocs_command": command,
        "mkdocs_exit_code": result.returncode,
        "mkdocs_stdout_tail": result.stdout[-2000:],
        "mkdocs_stderr_tail": result.stderr[-2000:],
        "staging_path": str(staging),
        "output_path": str(site_dir),
        "audit_receipt": audit.get("receipt_path", ""),
        "quality": audit["quality"],
    }
    if status != "pass":
        receipt["error"] = "mkdocs strict build failed or did not create the configured site directory"
    receipt["deterministic_digest"] = deterministic_audit_digest(receipt)
    receipt_path = output_root / "receipts" / "build-latest.json"
    _safe_write_json(receipt_path, receipt)
    receipt["receipt_path"] = str(receipt_path)
    return receipt


def serve_hub(
    hub_path: Path,
    *,
    tool_version: str,
    bind: str,
    port: int,
) -> dict[str, Any]:
    if bind not in {"127.0.0.1", "localhost", "::1"}:
        raise PolicyError("docs serve is local-only; bind must be a loopback address")
    if port < 1 or port > 65535:
        raise PolicyError("docs serve port must be between 1 and 65535")
    build = build_hub(hub_path, tool_version=tool_version, strict=True)
    if build["status"] != "pass":
        return build
    hub = load_hub(hub_path)
    config_path = Path(hub["output_root"]) / "staging" / "mkdocs.yml"
    mkdocs = _mkdocs_binary()
    command = [
        mkdocs,
        "serve",
        "--config-file",
        str(config_path),
        "--dev-addr",
        f"{bind}:{port}",
    ]
    result = _run(command, cwd=config_path.parent, timeout=24 * 60 * 60)
    return {
        "schema_version": BUILD_SCHEMA,
        "tool_version": tool_version,
        "status": "pass" if result.returncode == 0 else "error",
        "command": command,
        "exit_code": result.returncode,
        "output_path": build["output_path"],
        "build_receipt": build.get("receipt_path", ""),
    }


def command(args: Any, *, tool_version: str) -> dict[str, Any]:
    action = str(args.action)
    if action == "audit":
        if bool(args.repo) == bool(args.hub):
            raise PolicyError("docs audit requires exactly one of --repo or --hub")
        if args.repo:
            if not args.policy:
                raise PolicyError("docs audit --repo requires --policy")
            payload = audit_repository(
                Path(args.repo),
                Path(args.policy),
                tool_version=tool_version,
            )
            if args.output:
                output = Path(args.output).expanduser().resolve()
                _safe_write_json(output, payload)
                payload["receipt_path"] = str(output)
            return payload
        if args.policy:
            raise PolicyError("--policy is valid only with docs audit --repo")
        return audit_hub(Path(args.hub), tool_version=tool_version, write_receipt=True)
    if action == "build":
        return build_hub(Path(args.hub), tool_version=tool_version, strict=bool(args.strict))
    if action == "serve":
        return serve_hub(
            Path(args.hub),
            tool_version=tool_version,
            bind=str(args.bind),
            port=int(args.port),
        )
    raise PolicyError(f"unsupported docs action: {action}")


def human_lines(payload: Mapping[str, Any]) -> list[str]:
    lines = [
        f"status: {payload.get('status', 'error')}",
        (
            f"{payload.get('blocking_count', 0)} blocking, "
            f"{payload.get('warning_count', 0)} warning, "
            f"{payload.get('info_count', 0)} info"
        ),
    ]
    findings = payload.get("findings")
    if isinstance(findings, list):
        for finding in findings:
            location = str(finding.get("path") or "(policy)")
            if finding.get("line"):
                location += f":{finding['line']}"
            lines.append(
                f"- {str(finding.get('severity', '')).upper()} "
                f"{finding.get('rule_id')} {location}: {finding.get('message')}"
            )
    if payload.get("receipt_path"):
        lines.append(f"receipt: {payload['receipt_path']}")
    if payload.get("output_path"):
        lines.append(f"output: {payload['output_path']}")
    return lines


def compat_public_docs_audit(
    doc_root: Path,
    required_files: Sequence[str],
    *,
    secret_pattern: Pattern[str],
    naming_patterns: Sequence[tuple[Pattern[str], str]],
) -> dict[str, Any]:
    """Compatibility facade for Qwendex doctor's established payload shape."""

    doc_root = doc_root.resolve()
    missing = [name for name in required_files if not (doc_root / name).exists()]
    files = [name for name in required_files if (doc_root / name).exists()]
    tracked = set(files)
    cache: dict[str, dict[str, Any]] = {}
    dead_links: list[str] = []
    for source in files:
        model = _read_markdown(doc_root, source, cache)
        for link in model["links"]:
            raw_target = str(link["target"])
            target, anchor, kind = _normalize_link_target(doc_root, source, raw_target)
            if kind == "external":
                continue
            if kind == "escape" or target not in tracked or not (doc_root / target).exists():
                dead_links.append(f"{source}: {raw_target}")
                continue
            if anchor and target.lower().endswith(".md"):
                target_model = _read_markdown(doc_root, target, cache)
                if anchor not in target_model["anchors"]:
                    dead_links.append(f"{source}: {raw_target}")

    secret_hits: list[str] = []
    naming_hits: list[str] = []
    for name in files:
        text = (doc_root / name).read_text(encoding="utf-8")
        for line_number, line in enumerate(text.splitlines(), start=1):
            if secret_pattern.search(line):
                secret_hits.append(f"{name}:{line_number}")
            for pattern, message in naming_patterns:
                if pattern.search(line):
                    naming_hits.append(f"{name}:{line_number}: {message}")
    status = "pass" if not (missing or dead_links or secret_hits or naming_hits) else "fail"
    return {
        "status": status,
        "root": str(doc_root),
        "files": files,
        "missing": missing,
        "dead_links": sorted(dead_links),
        "secret_hits": sorted(secret_hits),
        "naming_hits": sorted(naming_hits),
    }
