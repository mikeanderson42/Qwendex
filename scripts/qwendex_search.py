#!/usr/bin/env python3
"""Live, repository-bounded ripgrep helpers for experimental Qwendex search.

This module deliberately keeps raw search material in memory.  Callers that
need to retain it must write it below an ignored evaluation artifact root; the
metadata-only performance database must never receive this data.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping


RAW_SEARCH_SCHEMA_VERSION = "qwendex.search_raw_result.v1"
RAW_ARTIFACT_SCHEMA_VERSION = "qwendex.search_raw_artifact.v1"
COMPACT_SEARCH_SCHEMA_VERSION = "qwendex.search_compact_result.v1"
PATH_SEARCH_SCHEMA_VERSION = "qwendex.search_path_result.v1"
FRESHNESS_SCHEMA_VERSION = "qwendex.search_freshness_matrix.v1"
SEARCH_CANDIDATE_ID = "search_evidence_compaction_v1"
SEARCH_CANDIDATE_VERSION = "1"

_MAX_SAFE_FILES = 100_000
_MAX_TIMEOUT_SECONDS = 300
_MAX_PER_FILE_RANGES = 200
_MAX_TOTAL_RANGES = 2_000
_MAX_PAGE_SIZE = 500
_DEFINITION_PREFIX = re.compile(
    r"^\s*(?:def|class|function|fn|interface|struct|enum|type|const|let|var)\b",
    re.IGNORECASE,
)


class SearchError(ValueError):
    """Raised when a requested search cannot stay inside its repository scope."""


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8", "surrogateescape"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(131_072), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _run_checked(args: list[str], *, cwd: Path, timeout_seconds: int) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            args,
            cwd=cwd,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
        )
    except FileNotFoundError as exc:
        raise SearchError(f"required executable unavailable: {args[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise SearchError(f"search timed out after {timeout_seconds}s") from exc


def canonical_repository_root(root: Path | str) -> Path:
    requested = Path(root).expanduser().resolve(strict=False)
    if not requested.exists() or not requested.is_dir():
        raise SearchError("search root must be an existing directory")
    home = Path.home().resolve(strict=False)
    if requested == home:
        raise SearchError("refusing a broad home-directory search root")
    result = _run_checked(
        ["git", "-C", str(requested), "rev-parse", "--show-toplevel"],
        cwd=requested,
        timeout_seconds=10,
    )
    if result.returncode != 0:
        raise SearchError("search root is not inside a Git worktree")
    repository = Path(result.stdout.decode("utf-8", "replace").strip()).resolve(strict=False)
    if repository == home:
        raise SearchError("refusing a Git worktree rooted at the home directory")
    if not _within(requested, repository):
        raise SearchError("search root escapes the repository worktree")
    return repository


def repository_scope_digest(root: Path | str) -> str:
    return "sha256:" + sha256_text(str(canonical_repository_root(root)))


def _git_files(repository: Path, *, include_ignored: bool) -> list[str]:
    base = _run_checked(
        ["git", "-C", str(repository), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=repository,
        timeout_seconds=30,
    )
    if base.returncode != 0:
        raise SearchError("could not enumerate current worktree files")
    values = [item for item in base.stdout.decode("utf-8", "surrogateescape").split("\0") if item]
    if include_ignored:
        ignored = _run_checked(
            ["git", "-C", str(repository), "ls-files", "-z", "--others", "--ignored", "--exclude-standard"],
            cwd=repository,
            timeout_seconds=30,
        )
        if ignored.returncode != 0:
            raise SearchError("could not enumerate explicitly included ignored files")
        values.extend(item for item in ignored.stdout.decode("utf-8", "surrogateescape").split("\0") if item)
    return sorted(set(values))


def safe_worktree_files(
    root: Path | str,
    *,
    include_ignored: bool = False,
    max_files: int = _MAX_SAFE_FILES,
) -> tuple[Path, Path, list[str], dict[str, int]]:
    """Return Git-enumerated current files while excluding escaping symlinks.

    Passing an explicit bounded file list to ripgrep lets normal Git ignore
    behavior coexist with tracked hidden files and safe in-repository symlinks.
    """

    requested = Path(root).expanduser().resolve(strict=False)
    repository = canonical_repository_root(requested)
    bounded_max = max(1, min(_MAX_SAFE_FILES, int(max_files)))
    counters = {
        "enumerated": 0,
        "included": 0,
        "missing": 0,
        "outside_requested_root": 0,
        "external_symlink_denied": 0,
        "file_limit_omitted": 0,
    }
    selected: list[str] = []
    for relative in _git_files(repository, include_ignored=include_ignored):
        counters["enumerated"] += 1
        relative_path = Path(relative)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            counters["external_symlink_denied"] += 1
            continue
        candidate = repository / relative_path
        if not _within(candidate, requested):
            counters["outside_requested_root"] += 1
            continue
        resolved = candidate.resolve(strict=False)
        if not _within(resolved, repository):
            counters["external_symlink_denied"] += 1
            continue
        if not candidate.exists() or not candidate.is_file():
            counters["missing"] += 1
            continue
        if len(selected) >= bounded_max:
            counters["file_limit_omitted"] += 1
            continue
        selected.append(relative_path.as_posix())
    counters["included"] = len(selected)
    return repository, requested, selected, counters


def _chunks(values: Iterable[str], *, max_items: int = 500, max_bytes: int = 192_000) -> Iterable[list[str]]:
    current: list[str] = []
    size = 0
    for value in values:
        value_size = len(value.encode("utf-8", "surrogateescape")) + 1
        if current and (len(current) >= max_items or size + value_size > max_bytes):
            yield current
            current = []
            size = 0
        current.append(value)
        size += value_size
    if current:
        yield current


def _decode_rg_value(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    text = value.get("text")
    if isinstance(text, str):
        return text
    encoded = value.get("bytes")
    if isinstance(encoded, str):
        try:
            return base64.b64decode(encoded).decode("utf-8", "replace")
        except (ValueError, UnicodeDecodeError):
            return ""
    return ""


def _parse_rg_json(raw: bytes) -> tuple[list[dict[str, Any]], int]:
    matches: list[dict[str, Any]] = []
    parse_errors = 0
    for line in raw.splitlines():
        if not line:
            continue
        try:
            event = json.loads(line.decode("utf-8", "replace"))
        except json.JSONDecodeError:
            parse_errors += 1
            continue
        data = event.get("data", {}) if isinstance(event, dict) else {}
        event_type = event.get("type") if isinstance(event, dict) else ""
        path = _decode_rg_value(data.get("path"))
        if event_type == "match" and path:
            matches.append(
                {
                    "kind": "match",
                    "path": path,
                    "line_number": int(data.get("line_number") or 0),
                    "line_text": _decode_rg_value(data.get("lines")),
                    "submatches": [
                        {
                            "start": int(item.get("start") or 0),
                            "end": int(item.get("end") or 0),
                            "text": _decode_rg_value(item.get("match")),
                        }
                        for item in data.get("submatches", [])
                        if isinstance(item, dict)
                    ],
                }
            )
        elif event_type == "binary" and path:
            matches.append(
                {
                    "kind": "binary",
                    "path": path,
                    "line_number": None,
                    "binary_offset": int(data.get("binary_offset") or 0),
                }
            )
    return matches, parse_errors


def raw_content_search(
    pattern: str,
    *,
    root: Path | str,
    mode: str,
    include_ignored: bool = False,
    max_files: int = _MAX_SAFE_FILES,
    timeout_seconds: int = 30,
) -> dict[str, Any]:
    """Execute live ripgrep against current, safely scoped worktree files."""

    expression = str(pattern)
    if not expression:
        raise SearchError("content pattern must not be empty")
    if mode not in {"literal", "regex"}:
        raise SearchError("content search mode must be literal or regex")
    timeout = max(1, min(_MAX_TIMEOUT_SECONDS, int(timeout_seconds)))
    repository, requested, files, safety = safe_worktree_files(
        root,
        include_ignored=include_ignored,
        max_files=max_files,
    )
    started = time.monotonic()
    raw_parts: list[bytes] = []
    stderr_parts: list[bytes] = []
    process_count = 0
    for chunk in _chunks(files):
        args = ["rg", "--json", "--no-messages", "--hidden"]
        if mode == "literal":
            args.append("-F")
        args.extend([expression, "--", *chunk])
        completed = _run_checked(args, cwd=repository, timeout_seconds=timeout)
        process_count += 1
        raw_parts.append(completed.stdout)
        stderr_parts.append(completed.stderr)
        if completed.returncode not in {0, 1}:
            message = completed.stderr.decode("utf-8", "replace").strip()
            raise SearchError(message or "ripgrep failed")
    raw_output = b"".join(raw_parts)
    matches, parse_errors = _parse_rg_json(raw_output)
    file_paths = sorted({str(item.get("path") or "") for item in matches if item.get("path")})
    return {
        "schema_version": RAW_SEARCH_SCHEMA_VERSION,
        "created_at": utc_now(),
        "repository_scope_digest": "sha256:" + sha256_text(str(repository)),
        "root_relative": "." if requested == repository else requested.relative_to(repository).as_posix(),
        "mode": mode,
        "query_fingerprint": "sha256:" + sha256_text(expression),
        "raw_output_bytes": len(raw_output),
        "raw_output_sha256": "sha256:" + sha256_bytes(raw_output),
        "match_count": sum(1 for item in matches if item.get("kind") == "match"),
        "file_count": len(file_paths),
        "binary_file_count": sum(1 for item in matches if item.get("kind") == "binary"),
        "matches": matches,
        "raw_rg_jsonl": raw_output.decode("utf-8", "replace"),
        "stderr_bytes": sum(len(item) for item in stderr_parts),
        "process_count": process_count,
        "duration_ms": round((time.monotonic() - started) * 1000, 3),
        "safety": safety,
    }


def candidate_registry() -> dict[str, Any]:
    """Return the single narrow, default-off candidate declaration."""

    return {
        "schema_version": "qwendex.optimization_lab.candidate_registry.v1",
        "candidates": [
            {
                "candidate_id": SEARCH_CANDIDATE_ID,
                "candidate_version": SEARCH_CANDIDATE_VERSION,
                "activation_mechanism": "explicit `performance lab run --candidate search_evidence_compaction_v1` or direct experimental `search` command",
                "default_state": "off",
                "expected_affected_tool_families": ["search", "read", "context"],
                "required_metrics": [
                    "raw_output_bytes",
                    "compact_output_bytes",
                    "compression_ratio",
                    "raw_match_count",
                    "retained_range_count",
                    "omitted_range_count",
                    "continuation_requests",
                    "candidate_duration_ms",
                    "candidate_adoption",
                ],
                "hard_quality_gates": [
                    "relevant_file_recall_non_inferior",
                    "relevant_region_recall_non_inferior",
                    "modified_and_untracked_visibility",
                    "repository_and_symlink_boundary",
                    "privacy_boundary",
                ],
                "performance_gates": [
                    "search_evidence_reduction",
                    "tool_call_non_regression",
                    "wall_time_non_regression",
                ],
                "known_limitations": [
                    "No content-result cache or persistent index is used.",
                    "A direct command does not prove live model adoption.",
                    "Raw evidence is retained only in ignored local evaluation artifacts.",
                ],
            }
        ],
    }


def _bounded(value: Any, *, default: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(maximum, parsed))


def _range_class(line_text: str, *, mode: str, pattern: str) -> tuple[int, str]:
    if _DEFINITION_PREFIX.search(line_text):
        return 0, "likely_definition"
    if mode == "literal":
        identifier = pattern.replace("_", "").isalnum()
        if identifier and re.search(rf"\b{re.escape(pattern)}\b", line_text):
            return 1, "exact_identifier"
        return 2, "exact_literal"
    return 3, "regex_match"


def _excerpt(value: str, *, limit: int = 360) -> tuple[str, bool]:
    normalized = value.rstrip("\r\n")
    if len(normalized) <= limit:
        return normalized, False
    return normalized[:limit], True


def _pagination_signature(raw: Mapping[str, Any], *, mode: str, budgets: Mapping[str, int]) -> str:
    material = {
        "repository_scope_digest": raw.get("repository_scope_digest"),
        "raw_output_sha256": raw.get("raw_output_sha256"),
        "query_fingerprint": raw.get("query_fingerprint"),
        "mode": mode,
        "budgets": dict(sorted(budgets.items())),
    }
    return sha256_text(json.dumps(material, sort_keys=True, separators=(",", ":")))[:32]


def _page_offset(page_token: str, signature: str) -> int:
    if not page_token:
        return 0
    parts = page_token.split(":")
    if len(parts) != 3 or parts[0] != "v1" or parts[2] != signature:
        raise SearchError("invalid or stale search continuation token")
    try:
        offset = int(parts[1])
    except ValueError as exc:
        raise SearchError("invalid search continuation offset") from exc
    if offset < 0:
        raise SearchError("invalid search continuation offset")
    return offset


def _compact_ranges(
    raw: Mapping[str, Any],
    *,
    pattern: str,
    mode: str,
    context_lines: int,
    merge_gap: int,
    max_range_lines: int,
) -> tuple[list[dict[str, Any]], int]:
    by_path: dict[str, dict[int, dict[str, Any]]] = {}
    binary_file_count = 0
    for item in raw.get("matches", []):
        if not isinstance(item, Mapping):
            continue
        if item.get("kind") == "binary":
            binary_file_count += 1
            continue
        if item.get("kind") != "match":
            continue
        path = str(item.get("path") or "")
        line_number = int(item.get("line_number") or 0)
        if not path or line_number < 1:
            continue
        line_text = str(item.get("line_text") or "")
        score, match_class = _range_class(line_text, mode=mode, pattern=pattern)
        current = by_path.setdefault(path, {}).get(line_number)
        candidate = {
            "line_number": line_number,
            "line_text": line_text,
            "score": score,
            "match_class": match_class,
        }
        if current is None or (score, match_class, line_text) < (current["score"], current["match_class"], current["line_text"]):
            by_path[path][line_number] = candidate
    ranges: list[dict[str, Any]] = []
    for path, lines_by_number in by_path.items():
        lines = [lines_by_number[number] for number in sorted(lines_by_number)]
        merged: list[dict[str, Any]] = []
        for line in lines:
            start = max(1, int(line["line_number"]) - context_lines)
            end = int(line["line_number"]) + context_lines
            excerpt, excerpt_truncated = _excerpt(str(line["line_text"]))
            item = {
                "path": path,
                "start_line": start,
                "end_line": end,
                "score": int(line["score"]),
                "reason": str(line["match_class"]),
                "match_lines": [int(line["line_number"])],
                "line_evidence": [
                    {
                        "line_number": int(line["line_number"]),
                        "excerpt": excerpt,
                        "excerpt_truncated": excerpt_truncated,
                        "match_class": str(line["match_class"]),
                    }
                ],
            }
            if (
                merged
                and start <= int(merged[-1]["end_line"]) + merge_gap + 1
                and end - int(merged[-1]["start_line"]) + 1 <= max_range_lines
            ):
                prior = merged[-1]
                prior["end_line"] = max(int(prior["end_line"]), end)
                prior["score"] = min(int(prior["score"]), int(item["score"]))
                if (int(item["score"]), str(item["reason"])) < (int(prior["score"]), str(prior["reason"])):
                    prior["reason"] = item["reason"]
                prior["match_lines"] = sorted(set([*prior["match_lines"], *item["match_lines"]]))
                evidence = {int(value["line_number"]): value for value in [*prior["line_evidence"], *item["line_evidence"]]}
                prior["line_evidence"] = [evidence[number] for number in sorted(evidence)]
            else:
                merged.append(item)
        ranges.extend(merged)
    return sorted(ranges, key=lambda item: (int(item["score"]), str(item["path"]), int(item["start_line"]), int(item["end_line"]))), binary_file_count


def _select_file_ranges(ranges: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    """Preserve best classes, then spread equally ranked broad matches by line."""

    if len(ranges) <= limit:
        return list(ranges)
    best_score = int(ranges[0]["score"])
    best = [item for item in ranges if int(item["score"]) == best_score]
    remainder = [item for item in ranges if int(item["score"]) != best_score]

    def spread(values: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
        if count >= len(values):
            return list(values)
        if count == 1:
            return [values[0]]
        indices = {round(slot * (len(values) - 1) / (count - 1)) for slot in range(count)}
        return [item for index, item in enumerate(values) if index in indices]

    if len(best) >= limit:
        return spread(best, limit)
    return [*best, *spread(remainder, limit - len(best))]


def compact_content_search(
    raw: Mapping[str, Any],
    *,
    pattern: str,
    mode: str,
    per_file_ranges: int = 12,
    total_ranges: int = 96,
    max_files: int = 64,
    page_size: int = 96,
    page_token: str = "",
    context_lines: int = 2,
    merge_gap: int = 2,
    max_range_lines: int = 24,
) -> dict[str, Any]:
    """Deterministically compact one live raw ripgrep result for model evidence."""

    started = time.monotonic()
    if raw.get("schema_version") != RAW_SEARCH_SCHEMA_VERSION:
        raise SearchError("raw search result schema is unsupported")
    if mode not in {"literal", "regex"}:
        raise SearchError("content search mode must be literal or regex")
    per_file = _bounded(per_file_ranges, default=12, maximum=_MAX_PER_FILE_RANGES)
    total = _bounded(total_ranges, default=96, maximum=_MAX_TOTAL_RANGES)
    files_limit = _bounded(max_files, default=64, maximum=_MAX_SAFE_FILES)
    page = _bounded(page_size, default=96, maximum=_MAX_PAGE_SIZE)
    ranges, binary_file_count = _compact_ranges(
        raw,
        pattern=pattern,
        mode=mode,
        context_lines=max(0, min(20, int(context_lines))),
        merge_gap=max(0, min(20, int(merge_gap))),
        max_range_lines=max(3, min(500, int(max_range_lines))),
    )
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in ranges:
        grouped.setdefault(str(item["path"]), []).append(item)
    ranked_paths = sorted(
        grouped,
        key=lambda path: (
            min(int(item["score"]) for item in grouped[path]),
            min(int(item["start_line"]) for item in grouped[path]),
            path,
        ),
    )
    selected_paths = ranked_paths[:files_limit]
    by_selected_path: dict[str, list[dict[str, Any]]] = {}
    per_file_omitted = 0
    for path in selected_paths:
        file_ranges = sorted(grouped[path], key=lambda item: (int(item["score"]), int(item["start_line"]), int(item["end_line"])))
        by_selected_path[path] = _select_file_ranges(file_ranges, limit=per_file)
        per_file_omitted += max(0, len(file_ranges) - per_file)
    # Take the top range from each ranked file before taking second ranges.
    # That deterministic breadth-first pass prevents one definition-dense file
    # from consuming the complete evidence budget during broad discovery.
    budgeted: list[dict[str, Any]] = []
    for rank in range(per_file):
        for path in selected_paths:
            ranges_for_path = by_selected_path[path]
            if rank < len(ranges_for_path):
                budgeted.append(ranges_for_path[rank])
    total_omitted = max(0, len(budgeted) - total) + per_file_omitted + sum(len(grouped[path]) for path in ranked_paths[files_limit:])
    budgeted = budgeted[:total]
    budgets = {
        "per_file_ranges": per_file,
        "total_ranges": total,
        "max_files": files_limit,
        "page_size": page,
    }
    signature = _pagination_signature(raw, mode=mode, budgets=budgets)
    offset = _page_offset(page_token, signature)
    if offset > len(budgeted):
        raise SearchError("search continuation offset exceeds retained evidence")
    retained = budgeted[offset : offset + page]
    next_offset = offset + len(retained)
    continuation = f"v1:{next_offset}:{signature}" if next_offset < len(budgeted) else ""
    model_evidence = [f"{item['path']}:{item['start_line']}-{item['end_line']} — {item['reason']}" for item in retained]
    compact_bytes = len(json.dumps(model_evidence, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    retained_files = sorted({str(item["path"]) for item in retained})
    return {
        "schema_version": COMPACT_SEARCH_SCHEMA_VERSION,
        "candidate_id": SEARCH_CANDIDATE_ID,
        "candidate_version": SEARCH_CANDIDATE_VERSION,
        "repository_scope_digest": raw.get("repository_scope_digest"),
        "query_fingerprint": raw.get("query_fingerprint"),
        "mode": mode,
        "deterministic_rule": {
            "context_lines": max(0, min(20, int(context_lines))),
            "merge_gap_lines": max(0, min(20, int(merge_gap))),
            "max_range_lines": max(3, min(500, int(max_range_lines))),
            "ranking": "likely definitions, exact identifiers/literals, then regex matches; ties sort by path and line",
            "truncation": "ranked evidence is retained from the beginning only; omitted counts and continuation are explicit",
        },
        "raw_match_count": int(raw.get("match_count") or 0),
        "raw_file_count": int(raw.get("file_count") or 0),
        "raw_output_bytes": int(raw.get("raw_output_bytes") or 0),
        "compact_output_bytes": compact_bytes,
        "compression_ratio": round(compact_bytes / int(raw.get("raw_output_bytes") or 1), 6),
        "retained_range_count": len(retained),
        "retained_file_count": len(retained_files),
        "retained_files": retained_files,
        "ranges": retained,
        "model_evidence": model_evidence,
        "omitted_range_count": max(0, len(ranges) - len(retained)),
        "budget_omitted_range_count": total_omitted,
        "pagination_omitted_range_count": max(0, len(budgeted) - next_offset),
        "truncated": len(ranges) > len(retained),
        "continuation_token": continuation or None,
        "continuation_requests": 1 if page_token else 0,
        "binary_file_count": binary_file_count,
        "candidate_duration_ms": round((time.monotonic() - started) * 1000, 3),
    }


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write(encoded)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def write_raw_evidence_artifact(
    path: Path | str,
    *,
    raw: Mapping[str, Any],
    pair_id: str,
    run_id: str,
    variant: str,
) -> dict[str, Any]:
    """Persist raw evidence only at an ignored artifact path selected by a lab."""

    target = Path(path)
    payload = {
        "schema_version": RAW_ARTIFACT_SCHEMA_VERSION,
        "candidate_id": SEARCH_CANDIDATE_ID if variant == "candidate" else "baseline_raw_ripgrep",
        "candidate_version": SEARCH_CANDIDATE_VERSION if variant == "candidate" else "not_applicable",
        "repository_scope_digest": raw.get("repository_scope_digest"),
        "pair_association": {"run_id": run_id, "pair_id": pair_id, "variant": variant},
        "query_fingerprint": raw.get("query_fingerprint"),
        "created_at": utc_now(),
        "retention_boundary": "ignored_local_evaluation_artifact",
        "raw_result": dict(raw),
    }
    _write_json(target, payload)
    return {
        "path": target.as_posix(),
        "sha256": "sha256:" + sha256_file(target),
        "bytes": target.stat().st_size,
    }


def content_search_payload(
    pattern: str,
    *,
    root: Path | str,
    mode: str,
    include_ignored: bool = False,
    max_files: int = _MAX_SAFE_FILES,
    per_file_ranges: int = 12,
    total_ranges: int = 96,
    max_files_evidence: int = 64,
    page_size: int = 96,
    page_token: str = "",
) -> dict[str, Any]:
    raw = raw_content_search(
        pattern,
        root=root,
        mode=mode,
        include_ignored=include_ignored,
        max_files=max_files,
    )
    compact = compact_content_search(
        raw,
        pattern=pattern,
        mode=mode,
        per_file_ranges=per_file_ranges,
        total_ranges=total_ranges,
        max_files=max_files_evidence,
        page_size=page_size,
        page_token=page_token,
    )
    return {
        "schema_version": COMPACT_SEARCH_SCHEMA_VERSION,
        "candidate": candidate_registry()["candidates"][0],
        "activation": {"default_state": "off", "active": True, "source": "explicit_direct_command"},
        "result": compact,
        "raw_statistics": {
            "raw_match_count": raw["match_count"],
            "raw_file_count": raw["file_count"],
            "raw_output_bytes": raw["raw_output_bytes"],
            "raw_output_sha256": raw["raw_output_sha256"],
            "safety": raw["safety"],
        },
    }


def path_search_payload(
    pattern: str,
    *,
    root: Path | str,
    mode: str = "regex",
    include_ignored: bool = False,
    max_files: int = _MAX_SAFE_FILES,
    page_size: int = 100,
    page_token: str = "",
) -> dict[str, Any]:
    if not pattern:
        raise SearchError("path pattern must not be empty")
    if mode not in {"literal", "regex"}:
        raise SearchError("path search mode must be literal or regex")
    repository, requested, files, safety = safe_worktree_files(root, include_ignored=include_ignored, max_files=max_files)
    root_relative = "." if requested == repository else requested.relative_to(repository).as_posix()
    if mode == "literal":
        matches = [path for path in files if pattern in path]
    else:
        try:
            expression = re.compile(pattern)
        except re.error as exc:
            raise SearchError("invalid path regular expression") from exc
        matches = [path for path in files if expression.search(path)]
    size = _bounded(page_size, default=100, maximum=_MAX_PAGE_SIZE)
    signature = sha256_text(json.dumps({"scope": "sha256:" + sha256_text(str(repository)), "root": root_relative, "mode": mode, "pattern": sha256_text(pattern)}, sort_keys=True))[:32]
    offset = _page_offset(page_token, signature)
    if offset > len(matches):
        raise SearchError("path continuation offset exceeds results")
    selected = matches[offset : offset + size]
    next_offset = offset + len(selected)
    continuation = f"v1:{next_offset}:{signature}" if next_offset < len(matches) else None
    return {
        "schema_version": PATH_SEARCH_SCHEMA_VERSION,
        "repository_scope_digest": "sha256:" + sha256_text(str(repository)),
        "root_relative": root_relative,
        "mode": mode,
        "query_fingerprint": "sha256:" + sha256_text(pattern),
        "match_count": len(matches),
        "paths": selected,
        "omitted_count": max(0, len(matches) - len(selected)),
        "truncated": len(matches) > len(selected),
        "continuation_token": continuation,
        "safety": safety,
    }


def _fixture_git(repository: Path, *args: str) -> None:
    completed = subprocess.run(["git", "-C", str(repository), *args], check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if completed.returncode != 0:
        raise SearchError("could not prepare search freshness fixture")


def freshness_matrix() -> dict[str, Any]:
    """Exercise current-worktree, ignore, binary, and symlink behavior safely."""

    rows: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="qwendex-search-freshness-") as temporary:
        parent = Path(temporary)
        repository = parent / "repo with $meta [space]"
        repository.mkdir()
        _fixture_git(repository, "init")
        _fixture_git(repository, "config", "user.email", "freshness@example.test")
        _fixture_git(repository, "config", "user.name", "Qwendex Freshness")
        (repository / "tracked.txt").write_text("tracked-token-before\n", encoding="utf-8")
        (repository / "deleted.txt").write_text("deleted-token\n", encoding="utf-8")
        (repository / "old-name.txt").write_text("renamed-token\n", encoding="utf-8")
        (repository / ".hidden-tracked.txt").write_text("hidden-token\n", encoding="utf-8")
        (repository / "target.txt").write_text("inside-symlink-token\n", encoding="utf-8")
        (repository / "inside-link.txt").symlink_to("target.txt")
        (repository / "binary.bin").write_bytes(b"binary-token\x00payload")
        (repository / "invalid.txt").write_bytes(b"invalid-token \xff\n")
        (repository / "long.txt").write_text("long-token " + "x" * 20_000 + "\n", encoding="utf-8")
        (repository / "many.txt").write_text("".join(f"many-token {index}\n" for index in range(80)), encoding="utf-8")
        (repository / "overlap.txt").write_text("overlap-token\n", encoding="utf-8")
        (repository / ".gitignore").write_text("generated.txt\n", encoding="utf-8")
        _fixture_git(repository, "add", ".")
        _fixture_git(repository, "commit", "-m", "freshness fixture")

        (repository / "tracked.txt").write_text("modified-token\n", encoding="utf-8")
        (repository / "untracked.txt").write_text("untracked-token\n", encoding="utf-8")
        (repository / "deleted.txt").unlink()
        (repository / "old-name.txt").rename(repository / "renamed-name.txt")
        (repository / "generated.txt").write_text("ignored-token\n", encoding="utf-8")
        outside = parent / "outside.txt"
        outside.write_text("external-symlink-token\n", encoding="utf-8")
        (repository / "external-link.txt").symlink_to(outside)

        def observed(token: str, *, include_ignored: bool = False) -> set[str]:
            result = raw_content_search(token, root=repository, mode="literal", include_ignored=include_ignored)
            return {str(item.get("path") or "") for item in result["matches"] if item.get("kind") in {"match", "binary"}}

        checks = [
            ("modified_tracked", "modified-token", {"tracked.txt"}, False),
            ("untracked", "untracked-token", {"untracked.txt"}, False),
            ("deleted_tracked", "deleted-token", set(), False),
            ("renamed", "renamed-token", {"renamed-name.txt"}, False),
            ("hidden_tracked", "hidden-token", {".hidden-tracked.txt"}, False),
            ("ignored_default", "ignored-token", set(), False),
            ("ignored_explicit", "ignored-token", {"generated.txt"}, True),
            ("symlink_inside", "inside-symlink-token", {"inside-link.txt", "target.txt"}, False),
            ("binary", "binary-token", {"binary.bin"}, False),
            ("invalid_utf8", "invalid-token", {"invalid.txt"}, False),
            ("very_long_line", "long-token", {"long.txt"}, False),
            ("many_matches", "many-token", {"many.txt"}, False),
            ("path_with_spaces_or_metacharacters", "modified-token", {"tracked.txt"}, False),
        ]
        for name, token, expected, include_ignored in checks:
            values = observed(token, include_ignored=include_ignored)
            if name == "symlink_inside":
                passed = "inside-link.txt" in values or "target.txt" in values
            else:
                passed = values == expected
            rows.append({"case": name, "status": "pass" if passed else "fail", "observed_count": len(values)})

        external = raw_content_search("external-symlink-token", root=repository, mode="literal")
        external_paths = {str(item.get("path") or "") for item in external["matches"]}
        rows.append(
            {
                "case": "symlink_escape_denied",
                "status": "pass" if not external_paths and int(external["safety"].get("external_symlink_denied") or 0) >= 1 else "fail",
                "observed_count": len(external_paths),
            }
        )
        many = raw_content_search("many-token", root=repository, mode="literal")
        compact_many = compact_content_search(many, pattern="many-token", mode="literal", per_file_ranges=3, total_ranges=3, page_size=3)
        rows.append(
            {
                "case": "compaction_many_matches_explicitly_omitted",
                "status": "pass" if compact_many["truncated"] and compact_many["omitted_range_count"] > 0 else "fail",
                "observed_count": compact_many["retained_range_count"],
            }
        )
        first = raw_content_search("overlap-token", root=repository, mode="literal")
        second = raw_content_search("overlap", root=repository, mode="literal")
        combined = dict(first)
        combined["matches"] = [*first["matches"], *second["matches"]]
        compact_overlap = compact_content_search(combined, pattern="overlap", mode="literal", per_file_ranges=8, total_ranges=8, page_size=8)
        rows.append(
            {
                "case": "overlapping_matches_deduplicated",
                "status": "pass" if compact_overlap["retained_range_count"] == 1 and compact_overlap["ranges"][0]["match_lines"] == [1] else "fail",
                "observed_count": compact_overlap["retained_range_count"],
            }
        )
    return {
        "schema_version": FRESHNESS_SCHEMA_VERSION,
        "status": "pass" if all(item["status"] == "pass" for item in rows) else "fail",
        "rows": rows,
        "zero_modified_or_untracked_misses": all(
            item["status"] == "pass" for item in rows if item["case"] in {"modified_tracked", "untracked"}
        ),
    }
