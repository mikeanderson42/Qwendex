#!/usr/bin/env python3
"""Status payload construction for the local-Qwen Responses bridge."""

from __future__ import annotations

from typing import Any

BRIDGE_PACKAGE_VERSION = "local-qwen-bridge-v2"


def runtime_guard_status_payload(runtime_guard: Any) -> dict[str, Any]:
    if hasattr(runtime_guard, "status_payload"):
        payload = runtime_guard.status_payload()
        return dict(payload) if isinstance(payload, dict) else {}
    return {}


def build_status_payload(
    *,
    version: str,
    runtime_guard: Any,
    target_base: str,
    native_tools: bool,
    system_prompt_file: str,
    max_output_tokens: int,
    context_limit_tokens: int,
    max_forward_body_bytes: int,
    tool_temperature: float,
    tool_top_p: float | None,
    tool_top_k: int | None,
    tool_min_p: float | None,
    tool_reasoning_effort: str,
    enable_thinking: bool,
    preserve_thinking: bool,
    max_heredoc_command_chars: int,
    max_exec_command_chars: int,
    repeated_tool_call_threshold: int,
    turn_tool_call_cap: int,
    global_duplicate_tool_call_threshold: int,
    alternating_tool_call_pattern_cycles: int,
    shell_command_stagnation_threshold: int,
    upstream_timeout_seconds: int,
    synthetic_response_handlers: list[str],
) -> dict[str, Any]:
    return {
        "version": version,
        "bridge_package_version": BRIDGE_PACKAGE_VERSION,
        **runtime_guard_status_payload(runtime_guard),
        "target_base": target_base,
        "native_tools": native_tools,
        "system_prompt_file": system_prompt_file,
        "max_output_tokens": max_output_tokens,
        "context_limit_tokens": context_limit_tokens,
        "max_forward_body_bytes": max_forward_body_bytes,
        "tool_temperature": tool_temperature,
        "tool_top_p": tool_top_p,
        "tool_top_k": tool_top_k,
        "tool_min_p": tool_min_p,
        "tool_reasoning_effort": tool_reasoning_effort,
        "enable_thinking": enable_thinking,
        "preserve_thinking": preserve_thinking,
        "effective_thinking_budget": -1 if enable_thinking else 0,
        "max_heredoc_command_chars": max_heredoc_command_chars,
        "max_exec_command_chars": max_exec_command_chars,
        "repeated_tool_call_threshold": repeated_tool_call_threshold,
        "turn_tool_call_cap": turn_tool_call_cap,
        "global_duplicate_tool_call_threshold": global_duplicate_tool_call_threshold,
        "alternating_tool_call_pattern_cycles": alternating_tool_call_pattern_cycles,
        "shell_command_stagnation_threshold": shell_command_stagnation_threshold,
        "upstream_timeout_seconds": upstream_timeout_seconds,
        "synthetic_response_handlers": synthetic_response_handlers,
    }
