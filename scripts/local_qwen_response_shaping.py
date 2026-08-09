#!/usr/bin/env python3
"""Responses payload shaping for deterministic local-Qwen bridge recoveries."""

from __future__ import annotations

import json
import re
from typing import Any


def schema_required_keys(payload: dict[str, Any] | None) -> set[str]:
    if not isinstance(payload, dict):
        return set()
    candidates: list[Any] = []
    text = payload.get("text")
    if isinstance(text, dict):
        text_format = text.get("format")
        if isinstance(text_format, dict):
            candidates.append(text_format.get("schema"))
    response_format = payload.get("response_format")
    if isinstance(response_format, dict):
        candidates.append(response_format.get("schema"))
        json_schema = response_format.get("json_schema")
        if isinstance(json_schema, dict):
            candidates.append(json_schema.get("schema"))
    for schema in candidates:
        if not isinstance(schema, dict):
            continue
        required = schema.get("required")
        if isinstance(required, list):
            keys = {str(key) for key in required if str(key).strip()}
            if keys:
                return keys
    return set()


def canonical_json_object(output_text: str, required: set[str]) -> str:
    if not required or not output_text or len(output_text) > 50000:
        return ""
    process_exit = re.search(r"Process exited with code\s+(-?\d+)", output_text)
    if process_exit and int(process_exit.group(1)) != 0:
        return ""
    candidates: list[Any] = []
    try:
        parsed = json.loads(output_text)
    except json.JSONDecodeError:
        parsed = None
    if parsed is not None:
        if (
            isinstance(parsed, dict)
            and parsed.get("exit_code") is not None
            and parsed.get("exit_code") != 0
        ):
            return ""
        candidates.append(parsed)
        if isinstance(parsed, dict) and parsed.get("exit_code") in {None, 0}:
            for field in ("output", "aggregated_output", "stdout"):
                nested = parsed.get(field)
                if not isinstance(nested, str) or not nested.strip():
                    continue
                try:
                    candidates.append(json.loads(nested))
                except json.JSONDecodeError:
                    pass

    decoder = json.JSONDecoder()
    for match in re.finditer(r"(?m)^\s*\{", output_text):
        candidate_text = output_text[match.start() :].lstrip()
        try:
            candidate, end = decoder.raw_decode(candidate_text)
        except json.JSONDecodeError:
            continue
        if candidate_text[end:].strip() not in {"", "```"}:
            continue
        candidates.append(candidate)

    for candidate in reversed(candidates):
        if isinstance(candidate, dict) and required.issubset(candidate):
            return json.dumps(candidate, ensure_ascii=False, indent=2)
    return ""


def collapse_repeated_final_text(text: str) -> str:
    if not text or len(text) < 80:
        return text
    stripped = text.strip()
    if not stripped:
        return text
    lines = stripped.splitlines()
    if len(lines) >= 4 and len(lines) % 2 == 0:
        midpoint = len(lines) // 2
        if lines[:midpoint] == lines[midpoint:]:
            return "\n".join(lines[:midpoint])
    midpoint = len(stripped) // 2
    if len(stripped) % 2 == 0 and stripped[:midpoint] == stripped[midpoint:]:
        return stripped[:midpoint]
    collapsed_detail_loop = collapse_repeated_detail_loop(stripped)
    if collapsed_detail_loop:
        return collapsed_detail_loop
    return text


def collapse_repeated_detail_loop(stripped: str) -> str:
    lines = stripped.splitlines()
    if len(lines) < 12:
        return ""
    normalized_to_indices: dict[str, list[int]] = {}
    for index, line in enumerate(lines):
        cleaned = re.sub(r"^\s*\d+[.)]\s*", "", line).strip()
        cleaned = re.sub(r"\*\*", "", cleaned)
        if len(cleaned) < 60:
            continue
        normalized = re.sub(r"\s+", " ", cleaned.lower()).strip()
        if normalized:
            normalized_to_indices.setdefault(normalized, []).append(index)
    repeated_indices = [indices for indices in normalized_to_indices.values() if len(indices) >= 3]
    if not repeated_indices:
        return collapse_repeated_numbered_heading_loop(lines)
    second_repetition_index = min(indices[1] for indices in repeated_indices)
    cut_index = second_repetition_index
    for index in range(second_repetition_index, -1, -1):
        if re.match(r"^\s*\d+[.)]\s+", lines[index]):
            cut_index = index
            break
    kept = "\n".join(lines[:cut_index]).rstrip()
    if len(kept) < 80:
        return ""
    return kept + "\n\nFurther repeated detail was suppressed by the local harness."


def collapse_repeated_numbered_heading_loop(lines: list[str]) -> str:
    heading_to_indices: dict[str, list[int]] = {}
    for index, line in enumerate(lines):
        match = re.match(r"^\s*\d+[.)]\s+(.+?)\s*$", line)
        if not match:
            continue
        heading = re.sub(r"\*\*", "", match.group(1)).strip(" :.-")
        normalized = re.sub(r"\s+", " ", heading.lower()).strip()
        if len(normalized) < 20 or len(normalized) > 140:
            continue
        heading_to_indices.setdefault(normalized, []).append(index)
    repeated_indices = [indices for indices in heading_to_indices.values() if len(indices) >= 3]
    if not repeated_indices:
        return ""
    second_repetition_index = min(indices[1] for indices in repeated_indices)
    kept = "\n".join(lines[:second_repetition_index]).rstrip()
    if len(kept) < 80:
        return ""
    return kept + "\n\nFurther repeated numbered detail was suppressed by the local harness."


def response_payload_with_message(text: str, model: str = "") -> dict[str, Any]:
    text = collapse_repeated_final_text(text)
    return {
        "id": "resp_qwendex_local_recovery",
        "object": "response",
        "created": 0,
        "model": model,
        "status": "completed",
        "output": [
            {
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": text, "annotations": []}],
            }
        ],
        "parallel_tool_calls": False,
        "tools": [],
        "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
    }


def response_payload_with_function_call(call: dict[str, Any], model: str = "") -> dict[str, Any]:
    return {
        "id": "resp_qwendex_local_recovery",
        "object": "response",
        "created": 0,
        "model": model,
        "status": "completed",
        "output": [call],
        "parallel_tool_calls": False,
        "tools": [],
        "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
    }
