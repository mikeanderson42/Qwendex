"""Tool-call parsing facade for JSON, Python-style, XML, Gemma, and Qwen fragments."""

from __future__ import annotations

from typing import Any

from . import server


def parse_tool_calls(text: str) -> tuple[list[dict[str, Any]], str]:
    return server.parse_xml_tool_calls(text)


def parse_xml_tool_calls(text: str) -> tuple[list[dict[str, Any]], str]:
    return server.parse_xml_tool_calls(text)


def normalize_tool_call_name(name: str) -> str:
    return server.normalize_tool_call_name(name)


def build_tool_call(function_name: str, function_body: str, call_index: int) -> dict[str, Any]:
    return server.build_tool_call(function_name, function_body, call_index)
