#!/usr/bin/env python3
"""Small stdio MCP server for local-model artifact queues.

This server intentionally implements only the MCP methods Codex needs for
tool discovery and tool calls. It is dependency-free so the local harness does
not depend on a Python MCP package or an extra Docker container.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.parse
import urllib.request
from collections import Counter, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

QUEUE_FILE = "TASK_QUEUE.md"
SERVER_VERSION = "2026-06-28-document-upsert"
DEFAULT_BRIDGE_LOG = Path(
    os.environ.get(
        "CODEX_TEXTGEN_LOG_PATH",
        os.environ.get(
            "LOCAL_QWEN_BRIDGE_LOG",
            str(
                Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state")))
                / "qwendex"
                / "local_qwen_bridge"
                / "responses_bridge.jsonl"
            ),
        ),
    )
).expanduser()
REPORT_SKIP_DIRS = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
}
STATUS_MARKERS = {
    "pending": " ",
    "in_progress": "-",
    "completed": "x",
    "blocked": "!",
}
MARKER_STATUS = {value: key for key, value in STATUS_MARKERS.items()}
OPEN_STATUSES = {"pending", "in_progress", "blocked"}


class ToolError(Exception):
    pass


@dataclass
class QueueItem:
    file: str
    description: str
    status: str = "pending"


def json_line(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"


def respond(request_id: Any, result: dict[str, Any]) -> None:
    sys.stdout.write(json_line({"jsonrpc": "2.0", "id": request_id, "result": result}))
    sys.stdout.flush()


def respond_error(request_id: Any, code: int, message: str) -> None:
    sys.stdout.write(
        json_line({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}})
    )
    sys.stdout.flush()


def tool_text(payload: dict[str, Any], *, is_error: bool = False) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, indent=2, sort_keys=True)}],
        "isError": is_error,
    }


def trusted_roots() -> list[Path]:
    raw = os.environ.get("ARTIFACT_QUEUE_MCP_TRUSTED_ROOTS") or os.environ.get("QWENDEX_TRUSTED_ROOTS", "")
    roots = (
        [Path(item).expanduser() for item in raw.split(":") if item.strip()]
        if raw
        else [Path.cwd()]
    )
    resolved: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        try:
            candidate = root.resolve()
        except OSError:
            continue
        key = str(candidate)
        if candidate.exists() and key not in seen:
            seen.add(key)
            resolved.append(candidate)
    return resolved


def is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def resolve_target_dir(raw: str) -> Path:
    if not raw or not isinstance(raw, str):
        raise ToolError("dir must be a non-empty string")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    try:
        resolved = path.resolve()
    except OSError as exc:
        raise ToolError(f"could not resolve dir: {exc}") from exc
    roots = trusted_roots()
    if not any(is_relative_to(resolved, root) for root in roots):
        allowed = ", ".join(str(root) for root in roots)
        raise ToolError(f"dir is outside trusted roots: {resolved}; allowed roots: {allowed}")
    return resolved


def queue_path(target_dir: Path) -> Path:
    return target_dir / QUEUE_FILE


def parse_item_arg(raw: str) -> QueueItem:
    if not isinstance(raw, str):
        raise ToolError("queue items must be strings in file.md::description format")
    if "::" in raw:
        file_name, description = raw.split("::", 1)
    else:
        file_name, description = raw, raw
    file_name = file_name.strip()
    description = description.strip() or file_name
    if not file_name or "/" in file_name or file_name in {".", ".."}:
        raise ToolError(f"invalid queue file name: {raw!r}")
    return QueueItem(file=file_name, description=description)


def read_queue(path: Path) -> list[QueueItem]:
    if not path.exists():
        return []
    items: list[QueueItem] = []
    pattern = re.compile(r"^- \[([ xX!\-])\] `([^`]+)`(?: \| (.*))?$")
    simple_pattern = re.compile(r"^- `?([^`|\s]+)`?(?: \| (.*))?$")
    numbered_pattern = re.compile(r"^\d+[.)]\s+`?([^`|\s]+)`?(?: \| (.*))?$")
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        match = pattern.match(stripped)
        if not match:
            simple_match = simple_pattern.match(stripped) or numbered_pattern.match(stripped)
            if simple_match:
                file_name, description = simple_match.groups()
                if file_name.endswith(".md"):
                    items.append(QueueItem(file=file_name, description=description or file_name))
            continue
        marker, file_name, description = match.groups()
        status = MARKER_STATUS.get(marker.lower(), "pending")
        items.append(QueueItem(file=file_name, description=description or file_name, status=status))
    validate_items(items)
    return items


def write_queue(path: Path, items: list[QueueItem]) -> None:
    validate_items(items)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Task Queue",
        "",
        "Use this queue for local-Qwen multi-artifact work. Keep exactly one item in_progress.",
        "",
    ]
    for item in items:
        marker = STATUS_MARKERS[item.status]
        lines.append(f"- [{marker}] `{item.file}` | {item.description}")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def validate_items(items: list[QueueItem]) -> None:
    seen: set[str] = set()
    in_progress = 0
    for item in items:
        if item.status not in STATUS_MARKERS:
            raise ToolError(f"invalid queue status for {item.file}: {item.status}")
        if item.file in seen:
            raise ToolError(f"duplicate queue file: {item.file}")
        seen.add(item.file)
        if item.status == "in_progress":
            in_progress += 1
    if in_progress > 1:
        raise ToolError("queue has more than one in_progress item")


def merge_items(existing: list[QueueItem], incoming: list[QueueItem]) -> list[QueueItem]:
    by_file = {item.file: item for item in existing}
    merged = list(existing)
    for item in incoming:
        current = by_file.get(item.file)
        if current:
            current.description = item.description
            continue
        by_file[item.file] = item
        merged.append(item)
    return merged


def maybe_complete_existing(target_dir: Path, items: list[QueueItem], min_bytes: int) -> None:
    for item in items:
        artifact = target_dir / item.file
        if artifact.is_file() and artifact.stat().st_size >= min_bytes:
            item.status = "completed"


def counts(items: list[QueueItem]) -> dict[str, int]:
    result = {status: 0 for status in STATUS_MARKERS}
    for item in items:
        result[item.status] = result.get(item.status, 0) + 1
    return result


def queue_summary(target_dir: Path, items: list[QueueItem]) -> dict[str, Any]:
    tally = counts(items)
    open_count = sum(tally[status] for status in OPEN_STATUSES)
    next_item = find_next(items)
    return {
        "schema": "artifact_queue_mcp.status.v1",
        "status": "pass",
        "dir": str(target_dir),
        "queue": str(queue_path(target_dir)),
        "counts": {**tally, "open": open_count},
        "next": item_payload(next_item) if next_item else None,
        "items": [item_payload(item) for item in items],
    }


def item_payload(item: QueueItem) -> dict[str, str]:
    return {"file": item.file, "description": item.description, "status": item.status}


def find_item(items: list[QueueItem], file_name: str) -> QueueItem:
    for item in items:
        if item.file == file_name:
            return item
    raise ToolError(f"queue item not found: {file_name}")


def find_next(items: list[QueueItem]) -> QueueItem | None:
    for status in ("in_progress", "pending"):
        for item in items:
            if item.status == status:
                return item
    return None


def require_file_name(raw: Any) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise ToolError("file must be a non-empty string")
    file_name = raw.strip()
    if "/" in file_name or file_name in {".", ".."}:
        raise ToolError(f"invalid queue file name: {file_name!r}")
    return file_name


def resolve_markdown_artifact(target_dir: Path, raw: Any) -> tuple[str, Path]:
    if not isinstance(raw, str) or not raw.strip():
        raise ToolError("file must be a non-empty string")
    file_name = raw.strip()
    path = Path(file_name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ToolError(f"invalid markdown file path: {file_name!r}")
    if path.suffix.lower() != ".md":
        raise ToolError(f"document_section_upsert only supports Markdown files: {file_name!r}")
    resolved = (target_dir / path).resolve()
    if not is_relative_to(resolved, target_dir):
        raise ToolError(f"markdown file is outside target dir: {file_name!r}")
    return file_name, resolved


def normalize_section_body(raw: Any) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise ToolError("body must be a non-empty string")
    return raw.strip() + "\n"


def title_from_stem(path: Path) -> str:
    words = path.stem.replace("_", " ").replace("-", " ").split()
    return " ".join(word.capitalize() for word in words) or "Document"


def upsert_markdown_section(text: str, heading: str, body: str, level: int) -> tuple[str, str]:
    rendered = f"{heading}\n\n{body.rstrip()}\n"
    heading_pattern = re.compile(rf"^{re.escape(heading)}\s*$", re.MULTILINE)
    match = heading_pattern.search(text)
    if not match:
        base = text.rstrip()
        prefix = f"{base}\n\n" if base else ""
        return "inserted", prefix + rendered

    line_start = match.start()
    content_start = text.find("\n", match.end())
    if content_start == -1:
        content_start = match.end()
    else:
        content_start += 1

    next_heading = re.search(rf"^#{{1,{level}}}\s+", text[content_start:], flags=re.MULTILINE)
    section_end = content_start + next_heading.start() if next_heading else len(text)
    replacement = rendered.rstrip() + "\n\n"
    candidate = text[:line_start] + replacement + text[section_end:].lstrip("\n")
    current_section = text[line_start:section_end].strip()
    if current_section == rendered.strip():
        return "already_present", text
    return "updated", candidate.rstrip() + "\n"


def tool_queue_status(args: dict[str, Any]) -> dict[str, Any]:
    target_dir = resolve_target_dir(str(args.get("dir", "")))
    return queue_summary(target_dir, read_queue(queue_path(target_dir)))


def tool_queue_next(args: dict[str, Any]) -> dict[str, Any]:
    target_dir = resolve_target_dir(str(args.get("dir", "")))
    items = read_queue(queue_path(target_dir))
    blocked = [item for item in items if item.status == "blocked"]
    next_item = find_next(items)
    status = "next" if next_item else ("blocked" if blocked else "done")
    payload = queue_summary(target_dir, items)
    payload.update(
        {
            "schema": "artifact_queue_mcp.next.v1",
            "status": status,
            "blocked": [item_payload(item) for item in blocked],
        }
    )
    return payload


def tool_queue_init(args: dict[str, Any]) -> dict[str, Any]:
    target_dir = resolve_target_dir(str(args.get("dir", "")))
    raw_items = args.get("items", [])
    if not isinstance(raw_items, list):
        raise ToolError("items must be an array")
    incoming = [parse_item_arg(value) for value in raw_items]
    path = queue_path(target_dir)
    items = merge_items(read_queue(path), incoming)
    min_bytes = int(args.get("min_bytes", 1))
    if bool(args.get("complete_existing", False)):
        maybe_complete_existing(target_dir, items, min_bytes)
    write_queue(path, items)
    payload = queue_summary(target_dir, items)
    payload["schema"] = "artifact_queue_mcp.init.v1"
    return payload


def tool_queue_start(args: dict[str, Any]) -> dict[str, Any]:
    target_dir = resolve_target_dir(str(args.get("dir", "")))
    file_name = require_file_name(args.get("file"))
    path = queue_path(target_dir)
    items = read_queue(path)
    item = find_item(items, file_name)
    for other in items:
        if other.status == "in_progress" and other.file != item.file:
            other.status = "pending"
    item.status = "in_progress"
    write_queue(path, items)
    payload = queue_summary(target_dir, items)
    payload["schema"] = "artifact_queue_mcp.start.v1"
    payload["started"] = item_payload(item)
    return payload


def tool_queue_done(args: dict[str, Any]) -> dict[str, Any]:
    target_dir = resolve_target_dir(str(args.get("dir", "")))
    file_name = require_file_name(args.get("file"))
    min_bytes = int(args.get("min_bytes", 1))
    path = queue_path(target_dir)
    items = read_queue(path)
    item = find_item(items, file_name)
    artifact = target_dir / item.file
    if not artifact.is_file():
        raise ToolError(f"artifact missing: {artifact}")
    size = artifact.stat().st_size
    if size < min_bytes:
        raise ToolError(f"artifact too small: {artifact} bytes={size} min={min_bytes}")
    item.status = "completed"
    write_queue(path, items)
    payload = queue_summary(target_dir, items)
    payload["schema"] = "artifact_queue_mcp.done.v1"
    payload["completed"] = {**item_payload(item), "bytes": str(size)}
    return payload


def tool_queue_blocked(args: dict[str, Any]) -> dict[str, Any]:
    target_dir = resolve_target_dir(str(args.get("dir", "")))
    file_name = require_file_name(args.get("file"))
    path = queue_path(target_dir)
    items = read_queue(path)
    item = find_item(items, file_name)
    item.status = "blocked"
    write_queue(path, items)
    payload = queue_summary(target_dir, items)
    payload["schema"] = "artifact_queue_mcp.blocked.v1"
    payload["blocked_item"] = {**item_payload(item), "reason": str(args.get("reason", ""))}
    return payload


def tool_document_section_upsert(args: dict[str, Any]) -> dict[str, Any]:
    target_dir = resolve_target_dir(str(args.get("dir", "")))
    file_name, artifact = resolve_markdown_artifact(target_dir, args.get("file"))
    section_title = str(args.get("section_title", "")).strip()
    if not section_title:
        raise ToolError("section_title must be a non-empty string")
    level = clamp_int(args.get("level"), 2, 1, 6)
    body = normalize_section_body(args.get("body"))
    min_bytes = clamp_int(args.get("min_bytes"), 1, 0, 10_000_000)
    item_number = args.get("item_number")
    total_items = args.get("total_items")
    parsed_item_number = int(item_number) if item_number is not None else None
    parsed_total_items = int(total_items) if total_items is not None else None

    heading = f"{'#' * level} {section_title}"
    if artifact.exists():
        text = artifact.read_text(encoding="utf-8")
    else:
        text = f"# {title_from_stem(artifact)}\n"
    action, updated = upsert_markdown_section(text, heading, body, level)

    artifact.parent.mkdir(parents=True, exist_ok=True)
    if action != "already_present":
        artifact.write_text(updated, encoding="utf-8")
    size = artifact.stat().st_size
    if size < min_bytes:
        raise ToolError(f"artifact too small after upsert: {artifact} bytes={size} min={min_bytes}")

    next_item = None
    if parsed_item_number is not None and parsed_total_items is not None and parsed_item_number < parsed_total_items:
        next_item = parsed_item_number + 1
    return {
        "schema": "local_harness_mcp.document_section_upsert.v1",
        "status": "pass",
        "summary": f"{action} section {section_title!r} in {file_name}",
        "next_actions": [
            "continue_item_update" if next_item is not None else "verify_or_finish",
        ],
        "artifacts": [str(artifact)],
        "dir": str(target_dir),
        "file": file_name,
        "artifact": str(artifact),
        "section": section_title,
        "heading": heading,
        "action": action,
        "bytes": size,
        "item_number": parsed_item_number,
        "total_items": parsed_total_items,
        "next_item": next_item,
        "next_action": "continue_item_update" if next_item is not None else "verify_or_finish",
    }


TOOL_HANDLERS = {
    "queue_status": tool_queue_status,
    "queue_next": tool_queue_next,
    "queue_init": tool_queue_init,
    "queue_start": tool_queue_start,
    "queue_done": tool_queue_done,
    "queue_blocked": tool_queue_blocked,
    "document_section_upsert": tool_document_section_upsert,
}


def clamp_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def clip_text(value: Any, max_chars: int) -> str:
    text = "" if value is None else str(value)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 15].rstrip() + "...[truncated]"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def rel_path(root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(root))
    except ValueError:
        return str(path)


def read_recent_jsonl(path: Path, limit: int) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    lines: deque[str] = deque(maxlen=max(limit * 4, 40))
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            lines.append(line)
    items: list[dict[str, Any]] = []
    for line in lines:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            items.append(payload)
    return items[-limit:]


def bridge_log_summary(path: Path, limit: int = 40) -> dict[str, Any]:
    items = read_recent_jsonl(path, limit)
    event_counts = Counter(str(item.get("event") or "request_forwarded") for item in items)
    marker_counts: Counter[str] = Counter()
    latest_marker = ""
    for item in items:
        counts_payload = item.get("response_marker_counts")
        if isinstance(counts_payload, dict):
            for marker, count in counts_payload.items():
                try:
                    marker_counts[str(marker)] += int(count)
                except (TypeError, ValueError):
                    continue
        marker = str(item.get("response_marker") or "")
        if marker:
            latest_marker = marker
    return {
        "available": path.is_file(),
        "path": str(path),
        "sampled_entries": len(items),
        "event_counts": dict(event_counts),
        "marker_counts": dict(marker_counts),
        "latest_marker": latest_marker,
    }


def queue_files_under(repo: Path, limit: int) -> list[Path]:
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(repo):
        dirnames[:] = sorted(name for name in dirnames if name not in REPORT_SKIP_DIRS)
        if QUEUE_FILE in filenames:
            found.append(Path(dirpath) / QUEUE_FILE)
        if len(found) >= max(limit * 3, limit):
            break
    return sorted(found, key=lambda path: path.stat().st_mtime, reverse=True)[:limit]


def builtin_run_report(repo: Path, max_chars: int) -> str:
    bridge = bridge_log_summary(DEFAULT_BRIDGE_LOG)
    lines = [
        "# Local Qwen Run Report",
        "",
        f"- Generated: `{utc_now()}`",
        f"- Repo: `{repo}`",
        "- Source: `mcp_builtin`",
        f"- Bridge log: `{bridge['path']}` available={bridge['available']}",
        f"- Bridge events: `{bridge['event_counts']}`",
        f"- Bridge markers: `{bridge['marker_counts']}` latest=`{bridge['latest_marker']}`",
        "",
        "## Queues",
        "",
    ]
    queues = queue_files_under(repo, 8)
    if not queues:
        lines.append("- none")
    for queue in queues:
        try:
            items = read_queue(queue)
            tally = counts(items)
            open_count = sum(tally[status] for status in OPEN_STATUSES)
            next_item = find_next(items)
            next_label = next_item.file if next_item else ""
            lines.append(
                f"- `{rel_path(repo, queue.parent)}` "
                f"pending={tally['pending']} "
                f"in_progress={tally['in_progress']} "
                f"completed={tally['completed']} "
                f"blocked={tally['blocked']} "
                f"open={open_count} "
                f"next=`{next_label}`"
            )
        except Exception as exc:
            lines.append(f"- `{rel_path(repo, queue)}` error={clip_text(exc, 240)}")
    lines.extend(
        [
            "",
            "## Recovery Guidance",
            "",
            "- Resume only the listed next queue item when a queue has open items.",
            "- Treat bridge markers as stop signs for that command shape; retry with a smaller command.",
            "- Prefer queue tools or repo-local helpers for multi-artifact work.",
        ]
    )
    return clip_text("\n".join(lines).rstrip() + "\n", max_chars)


def tool_search_web(args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query", "")).strip()
    if not query:
        raise ToolError("query must be a non-empty string")
    limit = clamp_int(args.get("limit"), 5, 1, 10)
    snippet_chars = clamp_int(args.get("snippet_chars"), 360, 80, 800)
    timeout = clamp_int(args.get("timeout_seconds"), 10, 2, 30)
    base_url = str(os.environ.get("SEARXNG_URL") or "http://127.0.0.1:6060").rstrip("/")
    parsed_base = urllib.parse.urlsplit(base_url)
    if (
        parsed_base.scheme not in {"http", "https"}
        or parsed_base.hostname not in {"127.0.0.1", "::1", "localhost"}
        or parsed_base.username is not None
        or parsed_base.password is not None
        or parsed_base.query
        or parsed_base.fragment
        or parsed_base.path not in {"", "/"}
    ):
        raise ToolError("SEARXNG_URL must be an exact loopback HTTP(S) origin")
    params_dict: dict[str, Any] = {"q": query, "format": "json"}
    for key in ("language", "categories", "engines", "time_range"):
        value = str(args.get(key, "")).strip()
        if value:
            params_dict[key] = value
    if "safesearch" in args:
        params_dict["safesearch"] = clamp_int(args.get("safesearch"), 0, 0, 2)
    params = urllib.parse.urlencode(params_dict)
    url = f"{base_url}/search?{params}"
    with urllib.request.urlopen(url, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8", errors="replace"))
    results: list[dict[str, Any]] = []
    for item in payload.get("results", [])[:limit]:
        if not isinstance(item, dict):
            continue
        results.append(
            {
                "title": clip_text(item.get("title", ""), 180),
                "url": clip_text(item.get("url", ""), 300),
                "content": clip_text(item.get("content", ""), snippet_chars),
                "engine": clip_text(item.get("engine", ""), 80),
                "score": item.get("score", ""),
                "publishedDate": item.get("publishedDate"),
            }
        )
    return {
        "schema": "local_harness_mcp.search_web.v1",
        "status": "pass",
        "query": payload.get("query", query),
        "base_url": base_url,
        "count": len(results),
        "results": results,
    }


def tool_run_report(args: dict[str, Any]) -> dict[str, Any]:
    repo = resolve_target_dir(str(args.get("repo", "")))
    max_chars = clamp_int(args.get("max_chars"), 8000, 1000, 20000)
    report = builtin_run_report(repo, max_chars)
    return {
        "schema": "local_harness_mcp.run_report.v1",
        "status": "pass",
        "repo": str(repo),
        "source": "mcp_builtin",
        "report": report,
    }


TOOL_HANDLERS.update(
    {
        "search_web": tool_search_web,
        "local_qwen_run_report": tool_run_report,
    }
)


def tool_schema(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": properties,
            "required": required,
        },
    }


TOOLS = [
    tool_schema(
        "queue_status",
        "Read a TASK_QUEUE.md artifact queue and return counts, items, and the next item.",
        {"dir": {"type": "string", "description": "Artifact directory, relative to repo root or absolute under a trusted root."}},
        ["dir"],
    ),
    tool_schema(
        "queue_next",
        "Return the next in-progress or pending queue item, or blocked/done state.",
        {"dir": {"type": "string", "description": "Artifact directory."}},
        ["dir"],
    ),
    tool_schema(
        "queue_init",
        "Create or merge TASK_QUEUE.md items. Items use file.md::description strings.",
        {
            "dir": {"type": "string", "description": "Artifact directory."},
            "items": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Queue items as file.md::description.",
            },
            "complete_existing": {"type": "boolean", "description": "Mark existing artifact files complete when large enough."},
            "min_bytes": {"type": "integer", "minimum": 0, "description": "Minimum bytes for complete_existing."},
        },
        ["dir", "items"],
    ),
    tool_schema(
        "queue_start",
        "Mark exactly one queue item in progress and reset other in-progress items to pending.",
        {"dir": {"type": "string"}, "file": {"type": "string", "description": "Queue file name, no path separators."}},
        ["dir", "file"],
    ),
    tool_schema(
        "queue_done",
        "Verify a named artifact exists and has enough bytes, then mark it completed.",
        {
            "dir": {"type": "string"},
            "file": {"type": "string", "description": "Queue file name, no path separators."},
            "min_bytes": {"type": "integer", "minimum": 0, "description": "Minimum artifact size."},
        },
        ["dir", "file"],
    ),
    tool_schema(
        "queue_blocked",
        "Mark a named queue item blocked with a short reason.",
        {
            "dir": {"type": "string"},
            "file": {"type": "string", "description": "Queue file name, no path separators."},
            "reason": {"type": "string", "description": "Short blocker reason."},
        },
        ["dir", "file"],
    ),
    tool_schema(
        "document_section_upsert",
        "Insert or replace one Markdown section under a trusted root and return continuation metadata for itemized updates.",
        {
            "dir": {"type": "string", "description": "Trusted repository or artifact directory."},
            "file": {
                "type": "string",
                "description": "Relative Markdown path under dir, for example docs/example.md.",
            },
            "section_title": {"type": "string", "description": "Heading text without leading # markers."},
            "body": {"type": "string", "description": "Markdown body for this section."},
            "level": {
                "type": "integer",
                "minimum": 1,
                "maximum": 6,
                "description": "Markdown heading level. Defaults to 2.",
            },
            "item_number": {"type": "integer", "minimum": 1, "description": "Optional current item number."},
            "total_items": {"type": "integer", "minimum": 1, "description": "Optional total number of items."},
            "min_bytes": {
                "type": "integer",
                "minimum": 0,
                "description": "Minimum artifact size after the upsert.",
            },
        },
        ["dir", "file", "section_title", "body"],
    ),
    tool_schema(
        "search_web",
        "Run a capped local SearXNG JSON search and return compact results without thumbnails or raw HTML.",
        {
            "query": {"type": "string", "description": "Search query."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 10, "description": "Maximum result count."},
            "snippet_chars": {
                "type": "integer",
                "minimum": 80,
                "maximum": 800,
                "description": "Maximum characters per result snippet.",
            },
            "timeout_seconds": {"type": "integer", "minimum": 2, "maximum": 30},
            "language": {"type": "string", "description": "Optional SearXNG language code, such as en or all."},
            "safesearch": {
                "type": "integer",
                "minimum": 0,
                "maximum": 2,
                "description": "Optional SearXNG safe-search level: 0 off, 1 moderate, 2 strict.",
            },
            "categories": {"type": "string", "description": "Optional SearXNG categories, such as general."},
            "engines": {"type": "string", "description": "Optional comma-separated SearXNG engine list."},
            "time_range": {
                "type": "string",
                "description": "Optional SearXNG time range, for example day, week, month, or year.",
            },
        },
        ["query"],
    ),
    tool_schema(
        "local_qwen_run_report",
        "Read a trusted repo's compact local-Qwen run report for bridge markers, queues, and verification packets.",
        {
            "repo": {"type": "string", "description": "Trusted repo root, such as the current Qwendex workspace."},
            "max_chars": {"type": "integer", "minimum": 1000, "maximum": 20000},
        },
        ["repo"],
    ),
]


def handle_request(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    if method == "initialize":
        return {
            "protocolVersion": "2025-11-25",
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {
                "name": "local-harness",
                "title": "Local Harness",
                "version": SERVER_VERSION,
                "description": "Bounded local tools for artifact queues, Markdown document upserts, SearXNG search, and local-Qwen run reports.",
            },
            "instructions": (
                "Use queue tools only for TASK_QUEUE.md artifact queues. Keep one item in progress, "
                "verify artifacts with queue_done, stop if queue_next reports blocked items, "
                "use document_section_upsert for one-section Markdown edits, "
                "use bounded outputs, and use search_web only for capped local SearXNG lookups."
            ),
        }
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        params = message.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        if name not in TOOL_HANDLERS:
            return tool_text({"status": "error", "error": f"unknown tool: {name}"}, is_error=True)
        if not isinstance(args, dict):
            return tool_text({"status": "error", "error": "arguments must be an object"}, is_error=True)
        try:
            return tool_text(TOOL_HANDLERS[name](args))
        except Exception as exc:
            return tool_text({"status": "error", "error": str(exc), "tool": name}, is_error=True)
    if method in {"notifications/initialized", "ping"}:
        return {} if method == "ping" else None
    raise ToolError(f"unsupported method: {method}")


def main() -> int:
    print(f"local-harness MCP ready; cwd={Path.cwd()}", file=sys.stderr)
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            print(f"invalid json: {exc}", file=sys.stderr)
            continue
        request_id = message.get("id")
        try:
            result = handle_request(message)
            if request_id is not None and result is not None:
                respond(request_id, result)
        except Exception as exc:
            if request_id is not None:
                respond_error(request_id, -32603, str(exc))
            else:
                print(f"notification error: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
