#!/usr/bin/env python3
"""Pure tool-envelope policy for the local-Qwen Responses bridge."""

from __future__ import annotations

from dataclasses import dataclass

TOOL_MARKUP_MARKER = "LOCAL_MODEL_TOOL_MARKUP_SUPPRESSED"
TOOL_CALL_TOO_LARGE_MARKER = "LOCAL_MODEL_TOOL_CALL_TOO_LARGE"
TOOL_CALL_TRUNCATED_MARKER = "LOCAL_MODEL_TOOL_CALL_TRUNCATED"


@dataclass(frozen=True)
class VisibleMarkupClassification:
    present: bool
    marker: str = ""
    family: str = ""
    reason: str = ""


def classify_visible_tool_markup(text: str) -> VisibleMarkupClassification:
    if not text:
        return VisibleMarkupClassification(False)
    lowered = text.lower()
    checks = (
        ("gemma_tool_call", ("<|tool_call>", "<tool_call|>")),
        ("xml_tool_call", ("<tool_call>", "</tool_call>")),
        ("xml_function", ("<function=", "</function>", "<parameter=", "</parameter>")),
        ("xml_parameter", ("</cmd>", "</chars>", "</path>", "</parameter>")),
    )
    for family, needles in checks:
        if any(needle in lowered for needle in needles):
            return VisibleMarkupClassification(
                True,
                marker=TOOL_MARKUP_MARKER,
                family=family,
                reason="raw tool-call markup remained in assistant text after parsing",
            )
    return VisibleMarkupClassification(False)


def suppress_visible_tool_markup(text: str, *, parsed_tool_call: bool = False) -> str:
    classification = classify_visible_tool_markup(text)
    if not classification.present:
        return text
    if parsed_tool_call:
        return ""
    return (
        f"{classification.marker}: raw tool-call markup remained in assistant text "
        "after parsing. Restart from a smaller command; do not emit large heredoc file bodies."
    )


def suppressed_exec_marker(message: str) -> str:
    safe_message = message.replace("'", "'\"'\"'")
    return f"printf '%s\\n' '{safe_message}'"
