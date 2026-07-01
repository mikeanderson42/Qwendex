#!/usr/bin/env python3
"""Pure helpers for document-section upsert recovery receipts."""

from __future__ import annotations

import json
import re
from typing import Any

SECTION_UPSERT_PROGRESS_RE = re.compile(
    r"\b(?P<marker>[A-Z0-9_]+_(?:DONE|ALREADY_PRESENT)|DOCUMENT_SECTION_(?:DONE|ALREADY_PRESENT))\b"
    r"\s+(?P<file>\S+)"
    r"(?P<detail>.*?)\baction=(?P<action>[A-Za-z0-9_-]+)"
    r".*?\bnext_item=(?P<next_item>\d+|None|)",
)


def parse_section_upsert_progress_events(text: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for match in SECTION_UPSERT_PROGRESS_RE.finditer(text):
        next_text = match.group("next_item")
        bytes_match = re.search(r"\bbytes=(\d+)\b", match.group(0))
        events.append(
            {
                "marker": match.group("marker"),
                "file": match.group("file").strip("`'\".,:;()[]{}"),
                "bytes": int(bytes_match.group(1)) if bytes_match else None,
                "action": match.group("action"),
                "next_item": int(next_text) if next_text.isdigit() else None,
            }
        )
    return events


def section_upsert_finalize_tool_call(normalized_arguments: str, *, index: int) -> dict[str, Any]:
    arguments: dict[str, Any] = {
        "cmd": (
            "printf '%s\\n' 'SECTION_UPSERT_FINALIZE_NOW: the repeated section upsert was skipped "
            "because the latest receipt already reported next_item=None. "
            "Do not rerun the same upsert. Answer the user now from the latest receipt, "
            "including the file, action, byte count, and completion status. "
            "Label this as mechanical section-upsert completion only; do not imply content "
            "quality was verified. Include one bounded post-edit validation next action.'"
        )
    }
    try:
        parsed = json.loads(normalized_arguments)
    except json.JSONDecodeError:
        parsed = {}
    if isinstance(parsed, dict):
        workdir = parsed.get("workdir")
        if isinstance(workdir, str) and workdir.strip():
            arguments["workdir"] = workdir.strip()
    return {
        "id": f"fc_section_upsert_finalize_{index + 1}",
        "type": "function_call",
        "call_id": f"call_section_upsert_finalize_{index + 1}",
        "name": "exec_command",
        "arguments": json.dumps(arguments, ensure_ascii=True),
    }


def terminal_section_upsert_final_answer(events: list[dict[str, Any]]) -> str:
    if not events or events[-1].get("next_item") is not None:
        return ""
    event = events[-1]
    file_path = str(event.get("file") or "").strip()
    marker = str(event.get("marker") or "").strip()
    action = str(event.get("action") or "").strip()
    byte_count = event.get("bytes")
    details = []
    if marker:
        details.append(f"marker={marker}")
    if action:
        details.append(f"action={action}")
    if isinstance(byte_count, int):
        details.append(f"bytes={byte_count}")
    details.append("next_item=None")
    target = f" for `{file_path}`" if file_path else ""
    return (
        f"The local harness section upsert is mechanically complete{target}. "
        f"Latest receipt: {', '.join(details)}. This confirms the helper wrote or found the "
        "section; it does not validate content quality. No further section-upsert retry is "
        "needed, but a bounded post-edit review or deterministic doc sanity check should run "
        "before treating the document as substantively complete."
    )
