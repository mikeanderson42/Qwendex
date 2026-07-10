#!/usr/bin/env python3
"""Generic OpenAI Responses-to-chat bridge for the Qwendex local-Qwen runtime."""

from __future__ import annotations

import argparse
import contextlib
import errno
import json
import os
import re
import shlex
import socket
import sys
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

try:
    from local_qwen_bridge_status import (
        build_status_payload as build_bridge_status_payload,
    )
    from local_qwen_bridge_status import (
        runtime_guard_status_payload as bridge_runtime_guard_status_payload,
    )
    from local_qwen_response_shaping import (
        response_payload_with_function_call as shape_response_payload_with_function_call,
    )
    from local_qwen_response_shaping import (
        response_payload_with_message as shape_response_payload_with_message,
    )
    from local_qwen_runtime_guard import GuardAction, GuardConfig, RuntimeGuard
    from local_qwen_tool_envelope import (
        suppress_visible_tool_markup as tool_policy_suppress_visible_tool_markup,
    )
    from local_qwen_tool_envelope import (
        suppressed_exec_marker as tool_policy_suppressed_exec_marker,
    )
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from local_qwen_bridge_status import (
        build_status_payload as build_bridge_status_payload,
    )
    from local_qwen_bridge_status import (
        runtime_guard_status_payload as bridge_runtime_guard_status_payload,
    )
    from local_qwen_response_shaping import (
        response_payload_with_function_call as shape_response_payload_with_function_call,
    )
    from local_qwen_response_shaping import (
        response_payload_with_message as shape_response_payload_with_message,
    )
    from local_qwen_runtime_guard import GuardAction, GuardConfig, RuntimeGuard
    from local_qwen_tool_envelope import (
        suppress_visible_tool_markup as tool_policy_suppress_visible_tool_markup,
    )
    from local_qwen_tool_envelope import (
        suppressed_exec_marker as tool_policy_suppressed_exec_marker,
    )


DEFAULT_LISTEN_HOST = "127.0.0.1"
DEFAULT_LISTEN_PORT = 1234
DEFAULT_TARGET_BASE = "http://127.0.0.1:4000"
DEFAULT_SYSTEM_PROMPT_FILE = ""
BRIDGE_VERSION = "qwendex-local-qwen-responses-v2"


def optional_env_float(name: str) -> float | None:
    raw = os.environ.get(name, "").strip()
    return float(raw) if raw else None


def optional_env_int(name: str) -> int | None:
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw else None


DEFAULT_MAX_OUTPUT_TOKENS = int(
    os.environ.get("CODEX_TEXTGEN_MAX_OUTPUT_TOKENS", "4096")
)
DEFAULT_MAX_FORWARD_BODY_BYTES = int(
    os.environ.get("CODEX_TEXTGEN_MAX_FORWARD_BODY_BYTES", "600000")
)
DEFAULT_UPSTREAM_TIMEOUT_SECONDS = int(
    os.environ.get("CODEX_TEXTGEN_UPSTREAM_TIMEOUT_SECONDS", "600")
)
DEFAULT_TOOL_TEMPERATURE = float(
    os.environ.get("CODEX_TEXTGEN_TOOL_TEMPERATURE", "0.15")
)
DEFAULT_TOOL_TOP_P = optional_env_float("CODEX_TEXTGEN_TOOL_TOP_P")
DEFAULT_TOOL_TOP_K = optional_env_int("CODEX_TEXTGEN_TOOL_TOP_K")
DEFAULT_TOOL_MIN_P = optional_env_float("CODEX_TEXTGEN_TOOL_MIN_P")
DEFAULT_TOOL_REASONING_EFFORT = os.environ.get(
    "CODEX_TEXTGEN_TOOL_REASONING_EFFORT", ""
).strip()
DEFAULT_CONTEXT_LIMIT_TOKENS = int(
    os.environ.get("CODEX_TEXTGEN_CONTEXT_LIMIT_TOKENS", "0")
)
DEFAULT_BRIDGE_LOG_PATH = Path(
    os.environ.get(
        "CODEX_TEXTGEN_LOG_PATH",
        os.environ.get(
            "LOCAL_QWEN_BRIDGE_LOG",
            str(
                Path(
                    os.environ.get(
                        "XDG_STATE_HOME", str(Path.home() / ".local" / "state")
                    )
                )
                / "qwendex"
                / "local_qwen_bridge"
                / "responses_bridge.jsonl"
            ),
        ),
    )
).expanduser()
MAX_HEREDOC_COMMAND_CHARS = int(
    os.environ.get("CODEX_TEXTGEN_MAX_HEREDOC_COMMAND_CHARS", "3500")
)
MAX_EXEC_COMMAND_CHARS = int(
    os.environ.get("CODEX_TEXTGEN_MAX_EXEC_COMMAND_CHARS", "8000")
)
MAX_COMPACT_TOOL_COUNT = max(
    1, int(os.environ.get("CODEX_TEXTGEN_MAX_COMPACT_TOOL_COUNT", "64"))
)
PREFERRED_TOOL_NAMES = ("exec_command", "write_stdin", "update_plan", "view_image")
COMPACT_TOOL_ALLOWLIST = set(PREFERRED_TOOL_NAMES)
LOCAL_MODEL_INTERFACE_MARKERS = (
    "LOCAL_MODEL_TOOL_CALL_TOO_LARGE",
    "LOCAL_MODEL_TOOL_CALL_TRUNCATED",
    "LOCAL_MODEL_TOOL_MARKUP_SUPPRESSED",
    "LOCAL_MODEL_LOOP_DETECTED",
)
LOCAL_QWEN_LOOP_BREAKER = (
    "Local Qwen loop failsafe: if you repeat a command or promise to inspect without acting, stop. "
    "Use the newest user request and latest successful tool output, then call one needed tool or answer."
)
LOCAL_QWEN_END_FRAME_ANCHOR = (
    "[LOCAL HARNESS REMINDER - NOT A USER TASK]\n"
    "The actionable task is the immediately preceding non-reminder user message. "
    "Newest user request and latest tool output take precedence over older plans.\n"
    "- Use bounded reads and short tool arguments.\n"
    "- Do not repeat a successful command.\n"
    "- Avoid large generated shell bodies and malformed tool markup.\n"
    "- If a tool call is rejected, choose one smaller safe action or answer from existing evidence."
)
RUNTIME_GUARD_CONFIG = GuardConfig.from_env()
REPEATED_TOOL_CALL_THRESHOLD = int(
    os.environ.get("CODEX_TEXTGEN_REPEATED_TOOL_CALL_THRESHOLD", "3")
)
CONSECUTIVE_IDENTICAL_TOOL_CALL_THRESHOLD = (
    RUNTIME_GUARD_CONFIG.consecutive_identical_tool_call_threshold
)
TURN_TOOL_CALL_CAP = RUNTIME_GUARD_CONFIG.turn_tool_call_cap
GLOBAL_DUPLICATE_TOOL_CALL_THRESHOLD = (
    RUNTIME_GUARD_CONFIG.global_duplicate_tool_call_threshold
)
ALTERNATING_TOOL_CALL_PATTERN_CYCLES = (
    RUNTIME_GUARD_CONFIG.alternating_tool_call_pattern_cycles
)
READ_LOOP_THRESHOLD = RUNTIME_GUARD_CONFIG.read_loop_threshold
READ_LOOP_WINDOW = RUNTIME_GUARD_CONFIG.read_loop_window
ACTION_STAGNATION_THRESHOLD = RUNTIME_GUARD_CONFIG.action_stagnation_threshold
SHELL_COMMAND_STAGNATION_THRESHOLD = (
    RUNTIME_GUARD_CONFIG.shell_command_stagnation_threshold
)
READ_DUMP_GUARD_ENABLED = os.environ.get(
    "CODEX_TEXTGEN_READ_DUMP_GUARD", "1"
).strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}


class RequestBodyError(ValueError):
    def __init__(self, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def load_json_body(
    handler: BaseHTTPRequestHandler,
    *,
    max_body_bytes: int,
) -> tuple[dict[str, Any] | None, bytes]:
    raw_length = handler.headers.get("Content-Length")
    if raw_length is None:
        raise RequestBodyError("Content-Length is required")
    try:
        length = int(raw_length)
    except (TypeError, ValueError) as exc:
        raise RequestBodyError("invalid Content-Length") from exc
    if length < 0:
        raise RequestBodyError("Content-Length must not be negative")
    if length > max_body_bytes:
        raise RequestBodyError(
            f"request body exceeds {max_body_bytes} bytes",
            status_code=413,
        )
    raw = handler.rfile.read(length) if length else b""
    if len(raw) != length:
        raise RequestBodyError(
            "request body ended before Content-Length bytes were received"
        )
    if not raw:
        return None, raw
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RequestBodyError("request body must be UTF-8 JSON") from exc
    try:
        payload = json.loads(decoded)
    except json.JSONDecodeError as exc:
        raise RequestBodyError(f"invalid JSON body: {exc.msg}") from exc
    return payload if isinstance(payload, dict) else None, raw


def write_json(
    handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]
) -> None:
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def probe_bridge_status(
    base_url: str,
    *,
    expected_target_base: str = "",
    timeout: int = 5,
) -> tuple[bool, str]:
    request = Request(f"{base_url.rstrip('/')}/status", method="GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        return False, str(exc)
    if not isinstance(payload, dict):
        return False, "unexpected bridge status payload"
    version = str(payload.get("version") or "")
    if version != BRIDGE_VERSION:
        return False, f"unexpected bridge version {version or '<missing>'}"
    target_base = str(payload.get("target_base") or "").rstrip("/")
    expected_target = expected_target_base.rstrip("/")
    if expected_target and target_base != expected_target:
        return False, f"bridge target mismatch {target_base or '<missing>'}"
    return True, f"bridge version={version} target={target_base}"


def probe_models(base_url: str, timeout: int = 10) -> tuple[bool, str]:
    request = Request(f"{base_url.rstrip('/')}/v1/models", method="GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
        payload = json.loads(body)
        if isinstance(payload, dict) and isinstance(payload.get("data"), list):
            return True, f"reachable models={len(payload['data'])}"
        return False, "unexpected /v1/models payload"
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        return False, str(exc)


def listener_is_bound(host: str, port: int) -> bool:
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.settimeout(1)
        return sock.connect_ex((host, port)) == 0


def tool_name(tool: dict[str, Any]) -> str:
    function = tool.get("function")
    if isinstance(function, dict):
        return str(function.get("name", ""))
    return str(tool.get("name", ""))


def extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        pieces: list[str] = []
        for item in content:
            if isinstance(item, str):
                pieces.append(item)
                continue
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type", ""))
            if item_type in {"input_text", "output_text", "text"}:
                text = item.get("text")
                if isinstance(text, str):
                    pieces.append(text)
                    continue
            text = item.get("text")
            if isinstance(text, str):
                pieces.append(text)
        return "".join(pieces)
    return ""


def extract_output_text(item: dict[str, Any]) -> str:
    for key in ("output", "content"):
        text = extract_text(item.get(key))
        if text:
            return text
        value = item.get(key)
        if isinstance(value, str):
            return value
    return ""


def normalize_role(role: str) -> str:
    if role == "developer":
        return "system"
    return role


def load_system_prompt_text(path: str | None) -> str:
    if not path:
        return ""
    try:
        text = Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return text


def function_tool_schema(tool: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(tool, dict) or str(tool.get("type", "")) != "function":
        return None
    function = tool.get("function")
    if isinstance(function, dict):
        return function
    function_payload: dict[str, Any] = {}
    for key in ("name", "description", "parameters", "strict"):
        value = tool.get(key)
        if value is not None:
            function_payload[key] = value
    return function_payload if function_payload.get("name") else None


def compact_description(text: Any, limit: int = 180) -> str:
    if not isinstance(text, str):
        return ""
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 3].rstrip() + "..."


def sanitize_tools(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    sanitized = dict(payload)
    removed_tools: list[dict[str, str]] = []
    tools = payload.get("tools")
    kept: list[dict[str, Any]] = []
    if isinstance(tools, list):
        for tool in tools:
            if not isinstance(tool, dict):
                removed_tools.append({"type": type(tool).__name__, "name": ""})
                continue
            if str(tool.get("type", "")) == "function" and function_tool_schema(tool):
                kept.append(tool)
            else:
                removed_tools.append(
                    {"type": str(tool.get("type", "")), "name": tool_name(tool)}
                )
    if kept:
        sanitized["tools"] = kept
    else:
        sanitized.pop("tools", None)
        sanitized.pop("tool_choice", None)
        sanitized.pop("parallel_tool_calls", None)
    return sanitized, {
        "removed_tool_count": len(removed_tools),
        "removed_tools": removed_tools,
        "remaining_tool_count": len(kept),
    }


def build_compact_tool_prompt(tools: Any) -> str:
    if not isinstance(tools, list) or not tools:
        return ""
    functions = [function_tool_schema(tool) for tool in tools if isinstance(tool, dict)]
    functions = [function for function in functions if function]
    preferred = {name: index for index, name in enumerate(PREFERRED_TOOL_NAMES)}
    functions.sort(
        key=lambda function: (
            preferred.get(str(function.get("name") or ""), len(preferred)),
            str(function.get("name") or ""),
        )
    )
    context_limit = getattr(
        ProxyHandler, "context_limit_tokens", DEFAULT_CONTEXT_LIMIT_TOKENS
    )
    lines = [
        "Codex bridge tool protocol:",
        "When a tool is required, output exactly one XML tool call and no surrounding prose:",
        "<tool_call>",
        "<function=tool_name>",
        "<parameter=parameter_name>",
        "parameter value",
        "</parameter>",
        "</function>",
        "</tool_call>",
        "Never invent a tool name. Use only a function listed below.",
        "Do not repeat a successful call. Keep arguments bounded and valid for the declared schema.",
        "Do not use large heredocs or paste long generated files into a shell command.",
    ]
    if context_limit:
        lines.append(
            f"Keep forwarded working context below the {context_limit}-token backend limit."
        )
    lines.append("Available tools:")
    for function in functions[:MAX_COMPACT_TOOL_COUNT]:
        name = str(function.get("name") or "").strip()
        if not name:
            continue
        parameters = function.get("parameters")
        properties = (
            parameters.get("properties", {}) if isinstance(parameters, dict) else {}
        )
        required = (
            set(parameters.get("required", []))
            if isinstance(parameters, dict)
            else set()
        )
        param_names = [
            f"{param}{' required' if param in required else ''}" for param in properties
        ]
        signature = ", ".join(param_names) if param_names else "no parameters"
        description = compact_description(function.get("description"))
        line = f"- {name}({signature})"
        lines.append(f"{line}: {description}" if description else line)
    if len(functions) > MAX_COMPACT_TOOL_COUNT:
        lines.append(
            f"- {len(functions) - MAX_COMPACT_TOOL_COUNT} additional functions omitted from the compact prompt."
        )
    return "\n".join(lines)


def contains_local_interface_marker(text: str) -> str:
    for marker in (
        *LOCAL_MODEL_INTERFACE_MARKERS,
        "FINAL_LOCAL_MODEL_STOP",
        "LOCAL_MODEL_PREMATURE_FINAL_MARKER",
    ):
        if marker in text:
            return marker
    return ""


def compact_interface_marker_context(text: str) -> str:
    marker = contains_local_interface_marker(text)
    if not marker:
        return text
    return (
        f"[previous local interface marker: {marker}; the previous malformed tool attempt failed. "
        "Do not repeat that exact tool envelope. Continue new user requests normally, or retry the "
        "same unfinished read-only task with a smaller command.]"
    )


def runtime_guard_config() -> GuardConfig:
    return GuardConfig(
        profile=RUNTIME_GUARD_CONFIG.profile,
        enabled=RUNTIME_GUARD_CONFIG.enabled,
        consecutive_identical_tool_call_threshold=CONSECUTIVE_IDENTICAL_TOOL_CALL_THRESHOLD,
        turn_tool_call_cap=TURN_TOOL_CALL_CAP,
        global_duplicate_tool_call_threshold=GLOBAL_DUPLICATE_TOOL_CALL_THRESHOLD,
        alternating_tool_call_pattern_cycles=ALTERNATING_TOOL_CALL_PATTERN_CYCLES,
        read_loop_threshold=READ_LOOP_THRESHOLD,
        read_loop_window=READ_LOOP_WINDOW,
        action_stagnation_threshold=ACTION_STAGNATION_THRESHOLD,
        shell_command_stagnation_threshold=SHELL_COMMAND_STAGNATION_THRESHOLD,
        repeated_interface_marker_threshold=RUNTIME_GUARD_CONFIG.repeated_interface_marker_threshold,
        stop_after_duplicate_read_recovery=RUNTIME_GUARD_CONFIG.stop_after_duplicate_read_recovery,
        run_max_wall_time_seconds=RUNTIME_GUARD_CONFIG.run_max_wall_time_seconds,
        run_max_tool_calls=RUNTIME_GUARD_CONFIG.run_max_tool_calls,
    )


def runtime_guard_status_payload() -> dict[str, Any]:
    return bridge_runtime_guard_status_payload(runtime_guard_config())


def responses_raw_items(payload: dict[str, Any]) -> list[Any]:
    raw_input = payload.get("input", [])
    if isinstance(raw_input, str):
        return [{"type": "message", "role": "user", "content": raw_input}]
    if isinstance(raw_input, list):
        return raw_input
    return []


def latest_user_request_from_items(raw_items: list[Any]) -> str:
    latest = ""
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        if str(item.get("type", "")) not in {"", "message"}:
            continue
        role = normalize_role(str(item.get("role", "user")))
        if role != "user":
            continue
        content = strip_prior_marker_preamble_from_user_text(
            extract_text(item.get("content"))
        )
        if content:
            latest = current_user_request_excerpt(content, limit=1400)
    return latest


def latest_user_full_text_from_items(raw_items: list[Any]) -> str:
    latest = ""
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        if str(item.get("type", "")) not in {"", "message"}:
            continue
        role = normalize_role(str(item.get("role", "user")))
        if role != "user":
            continue
        content = strip_prior_marker_preamble_from_user_text(
            extract_text(item.get("content"))
        )
        if content:
            latest = content
    return latest


def tool_outputs_from_items(raw_items: list[Any]) -> list[str]:
    outputs: list[str] = []
    for item in raw_items:
        if (
            not isinstance(item, dict)
            or str(item.get("type", "")) != "function_call_output"
        ):
            continue
        content = extract_output_text(item)
        if content:
            outputs.append(content)
    return outputs


def interface_marker_counts_after_latest_user_request(
    raw_items: list[Any],
) -> dict[str, int]:
    counts = {marker: 0 for marker in LOCAL_MODEL_INTERFACE_MARKERS}
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type", ""))
        if not item_type and ("role" in item or "content" in item):
            item_type = "message"
        content = ""
        if item_type == "message":
            role = normalize_role(str(item.get("role", "user")))
            content = extract_text(item.get("content"))
            if (
                role == "user"
                and strip_prior_marker_preamble_from_user_text(content).strip()
            ):
                counts = {marker: 0 for marker in LOCAL_MODEL_INTERFACE_MARKERS}
                continue
            if role not in {"assistant", "tool"}:
                continue
        elif item_type == "function_call_output":
            content = extract_output_text(item)
        else:
            continue
        for marker in LOCAL_MODEL_INTERFACE_MARKERS:
            if marker in content:
                counts[marker] += content.count(marker)
    return counts


def response_payload_with_message(text: str, model: str = "") -> dict[str, Any]:
    return shape_response_payload_with_message(text, model=model)


def response_payload_with_function_call(
    call: dict[str, Any], model: str = ""
) -> dict[str, Any]:
    return shape_response_payload_with_function_call(call, model=model)


def first_local_interface_marker(text: str) -> str:
    for marker in (
        *LOCAL_MODEL_INTERFACE_MARKERS,
        "FINAL_LOCAL_MODEL_STOP",
        "LOCAL_MODEL_PREMATURE_FINAL_MARKER",
    ):
        if marker in text:
            return marker
    return ""


def local_marker_counts_in_text(text: str) -> dict[str, int]:
    markers = (
        *LOCAL_MODEL_INTERFACE_MARKERS,
        "FINAL_LOCAL_MODEL_STOP",
        "LOCAL_MODEL_PREMATURE_FINAL_MARKER",
    )
    return {marker: text.count(marker) for marker in markers if marker in text}


def response_payload_summary(response_payload: dict[str, Any]) -> dict[str, Any]:
    type_counts: dict[str, int] = {}
    message_texts: list[str] = []
    tool_names: list[str] = []
    for item in response_payload.get("output", []):
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "")
        type_counts[item_type] = type_counts.get(item_type, 0) + 1
        if item_type == "function_call":
            name = str(item.get("name") or "")
            if name:
                tool_names.append(normalize_tool_call_name(name))
        elif item_type == "message":
            for part in item.get("content", []):
                if isinstance(part, dict):
                    text = part.get("text")
                    if isinstance(text, str):
                        message_texts.append(text)
    joined_text = "\n".join(message_texts)
    return {
        "response_output_type_counts": type_counts,
        "response_tool_names": tool_names[:12],
        "response_message_chars": len(joined_text),
        "response_marker": first_local_interface_marker(joined_text),
        "response_marker_counts": local_marker_counts_in_text(joined_text),
    }


def upstream_choice_summary(chat_payload: dict[str, Any]) -> dict[str, Any]:
    choices = chat_payload.get("choices")
    choice = choices[0] if isinstance(choices, list) and choices else {}
    message = choice.get("message") if isinstance(choice, dict) else {}
    raw_text = extract_text(message.get("content")) if isinstance(message, dict) else ""
    tool_calls = message.get("tool_calls") if isinstance(message, dict) else None
    finish_reason = choice.get("finish_reason") if isinstance(choice, dict) else None
    return {
        "upstream_finish_reason": finish_reason,
        "upstream_choice_count": len(choices) if isinstance(choices, list) else 0,
        "upstream_message_chars": len(raw_text),
        "upstream_native_tool_call_count": len(tool_calls)
        if isinstance(tool_calls, list)
        else 0,
        "upstream_tool_markup": "<tool_call" in raw_text or "<|tool_call" in raw_text,
    }


def current_user_request_excerpt(text: str, limit: int = 900) -> str:
    cleaned = text.strip()
    marker = "New user request:"
    marker_index = cleaned.lower().rfind(marker.lower())
    if marker_index >= 0:
        cleaned = cleaned[marker_index + len(marker) :].strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[-limit:]


def strip_prior_marker_preamble_from_user_text(text: str) -> str:
    if not contains_local_interface_marker(text):
        return text
    marker = "New user request:"
    marker_index = text.lower().rfind(marker.lower())
    if marker_index < 0:
        return text
    return text[marker_index + len(marker) :].strip()


def qwen_style_loop_guard_message(raw_items: list[Any]) -> str:
    decision = RuntimeGuard(runtime_guard_config()).evaluate_history(raw_items)
    if decision.action == GuardAction.ALLOW:
        return ""
    loop_type = decision.loop_type.value if decision.loop_type else "runtime_guard"
    observed = f" observed={decision.observed}" if decision.observed is not None else ""
    threshold = (
        f" threshold={decision.threshold}" if decision.threshold is not None else ""
    )
    return f"{loop_type}: {decision.message}.{observed}{threshold}"


def responses_input_to_messages(
    payload: dict[str, Any], tool_prompt: str = ""
) -> list[dict[str, Any]]:
    system_chunks: list[str] = []
    persistent_prompt = load_system_prompt_text(
        getattr(ProxyHandler, "system_prompt_file", None)
    )
    if persistent_prompt:
        system_chunks.append(persistent_prompt)
    if tool_prompt:
        system_chunks.append(tool_prompt)
    instructions = payload.get("instructions")
    if isinstance(instructions, str) and instructions.strip():
        system_chunks.append(instructions.strip())
    system_chunks.append(LOCAL_QWEN_LOOP_BREAKER)

    pending: list[dict[str, Any]] = []
    pending_tool_calls: list[dict[str, Any]] = []
    last_user_request = ""
    seen_non_system = False
    marker = ""

    def flush_tool_calls() -> None:
        nonlocal pending_tool_calls, seen_non_system
        if not pending_tool_calls:
            return
        pending.append(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": pending_tool_calls,
            }
        )
        pending_tool_calls = []
        seen_non_system = True

    raw_items = responses_raw_items(payload)
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type", ""))
        if not item_type and ("role" in item or "content" in item):
            item_type = "message"
        if item_type == "function_call":
            call_id = str(
                item.get("call_id")
                or item.get("id")
                or f"call_{len(pending_tool_calls) + 1}"
            )
            arguments = item.get("arguments")
            if not isinstance(arguments, str):
                arguments = json.dumps(
                    arguments if arguments is not None else {}, ensure_ascii=True
                )
            pending_tool_calls.append(
                {
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": str(item.get("name") or ""),
                        "arguments": arguments,
                    },
                }
            )
            continue

        flush_tool_calls()
        if item_type == "function_call_output":
            call_id = str(item.get("call_id") or "")
            content = extract_output_text(item)
            marker = marker or contains_local_interface_marker(content)
            content = compact_interface_marker_context(content)
            if call_id:
                pending.append(
                    {"role": "tool", "tool_call_id": call_id, "content": content}
                )
            else:
                pending.append({"role": "user", "content": f"[tool output]\n{content}"})
            seen_non_system = True
            continue
        if item_type != "message":
            continue

        role = normalize_role(str(item.get("role", "user")))
        if role not in {"system", "user", "assistant", "tool"}:
            role = "user"
        content = extract_text(item.get("content"))
        marker = marker or contains_local_interface_marker(content)
        if role == "user":
            content = strip_prior_marker_preamble_from_user_text(content)
            if content:
                last_user_request = current_user_request_excerpt(content)
        content = compact_interface_marker_context(content)

        if role == "system" and not seen_non_system:
            if content:
                system_chunks.append(content)
            continue
        if role == "system":
            role = "user"
            content = f"[system context]\n{content}"
        message: dict[str, Any] = {"role": role, "content": content}
        tool_call_id = item.get("tool_call_id")
        if role == "tool" and isinstance(tool_call_id, str) and tool_call_id:
            message["tool_call_id"] = tool_call_id
        pending.append(message)
        seen_non_system = True

    flush_tool_calls()
    messages: list[dict[str, Any]] = []
    merged_system = "\n\n".join(chunk for chunk in system_chunks if chunk.strip())
    if merged_system:
        messages.append({"role": "system", "content": merged_system})
    if not pending:
        pending.append({"role": "user", "content": "Reply with OK."})
    reminder = LOCAL_QWEN_END_FRAME_ANCHOR
    if last_user_request:
        reminder += f"\n\n[CURRENT USER REQUEST]\n{last_user_request}"
    if marker:
        reminder += (
            "\n\nA prior local interface attempt failed. Do not repeat its command shape; "
            "continue from the latest successful evidence."
        )
    loop_hint = qwen_style_loop_guard_message(raw_items)
    if loop_hint:
        reminder += f"\n\n[RUNTIME GUARD]\n{loop_hint}"
    pending.append({"role": "user", "content": reminder})
    messages.extend(pending)
    return messages


def responses_payload_to_tabby_chat(payload: dict[str, Any]) -> dict[str, Any]:
    tools = payload.get("tools")
    chat_payload: dict[str, Any] = {
        "model": payload.get("model"),
        "messages": responses_input_to_messages(
            payload,
            tool_prompt=build_compact_tool_prompt(tools),
        ),
        "stream": False,
    }
    wrapped_tools: list[dict[str, Any]] = []
    if ProxyHandler.native_tools and isinstance(tools, list):
        for tool in tools:
            function = function_tool_schema(tool) if isinstance(tool, dict) else None
            if function:
                wrapped_tools.append({"type": "function", "function": function})
        if wrapped_tools:
            chat_payload["tools"] = wrapped_tools

    tool_choice = payload.get("tool_choice")
    if ProxyHandler.native_tools and wrapped_tools and tool_choice is not None:
        if isinstance(tool_choice, dict) and not isinstance(
            tool_choice.get("function"), dict
        ):
            name = tool_choice.get("name")
            chat_payload["tool_choice"] = (
                {"type": "function", "function": {"name": name}}
                if isinstance(name, str) and name
                else tool_choice
            )
        else:
            chat_payload["tool_choice"] = tool_choice
    if (
        ProxyHandler.native_tools
        and wrapped_tools
        and isinstance(payload.get("parallel_tool_calls"), bool)
    ):
        chat_payload["parallel_tool_calls"] = payload["parallel_tool_calls"]

    requested_max = payload.get("max_output_tokens")
    if isinstance(requested_max, int) and not isinstance(requested_max, bool):
        chat_payload["max_tokens"] = max(
            1, min(requested_max, ProxyHandler.max_output_tokens)
        )
    else:
        chat_payload["max_tokens"] = ProxyHandler.max_output_tokens

    if isinstance(tools, list) and tools:
        chat_payload["temperature"] = ProxyHandler.tool_temperature
    elif isinstance(payload.get("temperature"), (int, float)):
        chat_payload["temperature"] = max(0.0, min(float(payload["temperature"]), 2.0))

    top_p = payload.get("top_p")
    if isinstance(top_p, (int, float)):
        chat_payload["top_p"] = max(0.0, min(float(top_p), 1.0))
    elif isinstance(tools, list) and tools and ProxyHandler.tool_top_p is not None:
        chat_payload["top_p"] = ProxyHandler.tool_top_p
    top_k = payload.get("top_k")
    if isinstance(top_k, int) and not isinstance(top_k, bool):
        chat_payload["top_k"] = max(0, top_k)
    elif isinstance(tools, list) and tools and ProxyHandler.tool_top_k is not None:
        chat_payload["top_k"] = ProxyHandler.tool_top_k
    min_p = payload.get("min_p")
    if isinstance(min_p, (int, float)):
        chat_payload["min_p"] = max(0.0, min(float(min_p), 1.0))
    elif isinstance(tools, list) and tools and ProxyHandler.tool_min_p is not None:
        chat_payload["min_p"] = ProxyHandler.tool_min_p

    reasoning_effort = payload.get("reasoning_effort")
    reasoning = payload.get("reasoning")
    if not reasoning_effort and isinstance(reasoning, dict):
        reasoning_effort = reasoning.get("effort")
    if isinstance(reasoning_effort, str) and reasoning_effort:
        chat_payload["reasoning_effort"] = reasoning_effort
    elif isinstance(tools, list) and tools and ProxyHandler.tool_reasoning_effort:
        chat_payload["reasoning_effort"] = ProxyHandler.tool_reasoning_effort
    if payload.get("stop") is not None:
        chat_payload["stop"] = payload["stop"]

    chat_payload["add_generation_prompt"] = True
    chat_payload["chat_template_kwargs"] = {
        "enable_thinking": ProxyHandler.enable_thinking,
        "preserve_thinking": ProxyHandler.preserve_thinking,
    }
    chat_payload["template_vars"] = {
        "enable_thinking": ProxyHandler.enable_thinking,
        "preserve_thinking": ProxyHandler.preserve_thinking,
        "thinking_budget": -1 if ProxyHandler.enable_thinking else 0,
    }
    return chat_payload


def coerce_nonnegative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def usage_from_chat(chat_payload: dict[str, Any]) -> dict[str, int]:
    usage = chat_payload.get("usage")
    if not isinstance(usage, dict):
        return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    prompt_tokens = coerce_nonnegative_int(usage.get("prompt_tokens"))
    completion_tokens = coerce_nonnegative_int(usage.get("completion_tokens"))
    total_tokens = coerce_nonnegative_int(usage.get("total_tokens"))
    if not total_tokens:
        total_tokens = prompt_tokens + completion_tokens
    return {
        "input_tokens": prompt_tokens,
        "output_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


TOOL_CALL_BLOCK_RE = re.compile(
    r"<tool_call>\s*<function=([^>]+)>\s*(.*?)\s*</function>\s*</tool_call>",
    re.DOTALL,
)
PARTIAL_TOOL_CALL_BLOCK_RE = re.compile(
    r"<tool_call>\s*<function=([^>]+)>\s*(.*?)\s*</function>",
    re.DOTALL,
)
RAW_FUNCTION_BLOCK_RE = re.compile(
    r"<function=([^>]+)>\s*(.*?)\s*</function>",
    re.DOTALL,
)
BARE_TOOL_CALL_BLOCK_RE = re.compile(
    r"<tool_call>\s*([A-Za-z0-9_]+)>\s*(.*?)\s*</function>\s*</tool_call>",
    re.DOTALL,
)
JSON_TOOL_CALL_BLOCK_RE = re.compile(
    r"<tool_call>\s*(\{.*?\})\s*</tool_call>",
    re.DOTALL,
)
HYBRID_JSON_XML_TOOL_CALL_BLOCK_RE = re.compile(
    r"<tool_call>\s*\{[^{}]*?\"name\"\s*:\s*\"([^\"]+)\".*?(<parameter=.*?)</(?:function|tool_call)>\s*</tool_call>",
    re.DOTALL,
)
INCOMPLETE_TOOL_CALL_BLOCK_RE = re.compile(
    r"<tool_call>\s*<function=([^>]+)>\s*(.*)",
    re.DOTALL,
)
PARAMETER_RE = re.compile(
    r"<parameter=([^>]+)>\s*(.*?)\s*</parameter>",
    re.DOTALL,
)
INCOMPLETE_PARAMETER_RE = re.compile(
    r"<parameter=([^\s>]+)[^>]*>\s*(.*)",
    re.DOTALL,
)
GEMMA_TOOL_CALL_RE = re.compile(
    r"<\|tool_call\>\s*(?:call:)?([A-Za-z0-9_.]+)\s*\(",
    re.DOTALL,
)
GEMMA_BRACE_TOOL_CALL_RE = re.compile(
    r"<\|tool_call\>\s*call:([^\s{]+)\s*",
    re.DOTALL,
)
PYTHON_STYLE_TOOL_CALL_RE = re.compile(
    r"(?<![\w.])(exec_command|write_stdin|update_plan|view_image)\s*\(",
    re.DOTALL,
)


def normalize_tool_call_name(name: str) -> str:
    name = name.strip()
    if name.startswith("functions."):
        name = name.split(".", 1)[1]
    compact = re.sub(r"[^A-Za-z0-9_]+", "", name).lower()
    if compact in COMPACT_TOOL_ALLOWLIST:
        return compact
    if compact.startswith("exec") and compact.endswith("command"):
        return "exec_command"
    if compact.endswith("command") and "command" in compact:
        return "exec_command"
    if (
        compact.startswith("writestdin")
        or compact.startswith("write")
        and compact.endswith("stdin")
    ):
        return "write_stdin"
    if compact.startswith("update") and compact.endswith("plan"):
        return "update_plan"
    if compact.startswith("view") and compact.endswith("image"):
        return "view_image"
    return name


def json_loads_lenient(raw_json: str) -> Any:
    try:
        return json.loads(raw_json)
    except json.JSONDecodeError:
        return json.loads(raw_json, strict=False)


def extract_balanced_fragment(
    text: str, start: int, opener: str, closer: str
) -> str | None:
    if start < 0 or start >= len(text) or text[start] != opener:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def extract_balanced_call_arguments(text: str, open_paren_index: int) -> str | None:
    fragment = extract_balanced_fragment(text, open_paren_index, "(", ")")
    if fragment is None:
        return None
    return fragment[1:-1]


def extract_gemma4_brace_fragment(text: str, start: int) -> str | None:
    if start < 0 or start >= len(text) or text[start] != "{":
        return None
    depth = 0
    in_string = False
    quote_token = '<|"|>'
    quote_len = len(quote_token)
    index = start
    while index < len(text):
        if text[index : index + quote_len] == quote_token:
            in_string = not in_string
            index += quote_len
            continue
        if in_string:
            index += 1
            continue
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
        index += 1
    return None


def consume_gemma4_tool_closer(text: str, start: int) -> int:
    index = start
    while index < len(text) and text[index].isspace():
        index += 1
    for closer in ("<tool_call|>", "</tool_call>"):
        if text.startswith(closer, index):
            return index + len(closer)
    return start


def parse_gemma4_brace_arguments(fragment: str) -> dict[str, Any] | None:
    parts = fragment.split('<|"|>')
    for index in range(len(parts)):
        if index % 2 == 0:
            parts[index] = re.sub(r"(^|[{,\[])\s*(\w+)\s*:", r'\1"\2":', parts[index])
        else:
            parts[index] = json.dumps(parts[index])[1:-1]
    try:
        parsed = json.loads('"'.join(parts))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def extract_quoted_assignment(text: str, key: str) -> str | None:
    match = re.search(rf"(?:^|,)\s*{re.escape(key)}\s*=\s*\"", text, re.DOTALL)
    if not match:
        return None
    chars: list[str] = []
    escaped = False
    for index in range(match.end(), len(text)):
        char = text[index]
        if escaped:
            chars.append("\\" + char)
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == '"' and next_nonspace(text, index + 1) in {",", ")", ""}:
            raw = "".join(chars)
            try:
                return json_loads_lenient(f'"{raw}"')
            except json.JSONDecodeError:
                return raw
        chars.append(char)
    return None


def extract_simple_assignment(text: str, key: str) -> Any:
    quoted = extract_quoted_assignment(text, key)
    if quoted is not None:
        return quoted
    match = re.search(rf"(?:^|,)\s*{re.escape(key)}\s*=\s*([^,\s)]+)", text, re.DOTALL)
    if not match:
        return None
    value = match.group(1).strip()
    if value.isdigit():
        return int(value)
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    if value == "None":
        return None
    return value


def next_nonspace(text: str, start: int) -> str:
    for char in text[start:]:
        if not char.isspace():
            return char
    return ""


def extract_jsonish_string_value(text: str, key: str) -> str | None:
    match = re.search(rf'"{re.escape(key)}"\s*:\s*"', text, re.DOTALL)
    if not match:
        return None
    chars: list[str] = []
    escaped = False
    for index in range(match.end(), len(text)):
        char = text[index]
        if escaped:
            chars.append(char)
            escaped = False
            continue
        if char == "\\":
            chars.append(char)
            escaped = True
            continue
        if char == '"' and next_nonspace(text, index + 1) in {",", "}"}:
            value = "".join(chars)
            try:
                return json_loads_lenient(f'"{value}"')
            except json.JSONDecodeError:
                return value
        chars.append(char)
    return None


def extract_jsonish_arguments(raw_json: str, function_name: str) -> dict[str, Any]:
    arguments: dict[str, Any] = {}
    arguments_match = re.search(r'"arguments"\s*:', raw_json, re.DOTALL)
    if arguments_match:
        object_start = raw_json.find("{", arguments_match.end())
        fragment = extract_balanced_fragment(raw_json, object_start, "{", "}")
        if fragment:
            try:
                parsed = json_loads_lenient(fragment)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                arguments.update(parsed)

    if function_name == "update_plan":
        plan_match = re.search(r'"plan"\s*:', raw_json, re.DOTALL)
        if plan_match:
            array_start = raw_json.find("[", plan_match.end())
            fragment = extract_balanced_fragment(raw_json, array_start, "[", "]")
            if fragment:
                try:
                    parsed_plan = json_loads_lenient(fragment)
                except json.JSONDecodeError:
                    parsed_plan = None
                if isinstance(parsed_plan, list):
                    arguments["plan"] = parsed_plan
        explanation = extract_jsonish_string_value(raw_json, "explanation")
        if explanation is not None:
            arguments["explanation"] = explanation
        return arguments

    for key in ("cmd", "chars", "path", "justification"):
        value = extract_jsonish_string_value(raw_json, key)
        if value is not None:
            arguments[key] = value
    return arguments


def build_json_tool_call(raw_json: str, call_index: int) -> dict[str, Any] | None:
    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None

    function_name = str(payload.get("name") or payload.get("function") or "").strip()
    arguments = payload.get("arguments", {})
    if isinstance(arguments, str):
        try:
            parsed_arguments = json.loads(arguments)
        except json.JSONDecodeError:
            parsed_arguments = (
                {"cmd": arguments} if function_name == "exec_command" else {}
            )
        arguments = parsed_arguments
    if not isinstance(arguments, dict):
        arguments = {}
    if not function_name:
        return None

    return {
        "id": f"json_tool_call_{call_index}",
        "type": "function",
        "function": {
            "name": normalize_tool_call_name(function_name),
            "arguments": json.dumps(arguments, ensure_ascii=True),
        },
    }


def build_jsonish_tool_call(raw_json: str, call_index: int) -> dict[str, Any] | None:
    name_match = re.search(r'"name"\s*:\s*"([^"]+)"', raw_json, re.DOTALL)
    if not name_match:
        return None
    function_name = normalize_tool_call_name(name_match.group(1).strip())
    arguments = extract_jsonish_arguments(raw_json, function_name)
    return {
        "id": f"jsonish_tool_call_{call_index}",
        "type": "function",
        "function": {
            "name": function_name,
            "arguments": json.dumps(arguments, ensure_ascii=True),
        },
    }


def incomplete_jsonish_tool_call(
    text: str, call_index: int
) -> tuple[dict[str, Any] | None, str]:
    start = text.find("<tool_call>")
    if start < 0:
        return None, ""
    raw = text[start + len("<tool_call>") :].strip()
    object_start = raw.find("{")
    if object_start < 0 or not re.search(r'"name"\s*:', raw, re.DOTALL):
        return None, ""
    fragment = extract_balanced_fragment(raw, object_start, "{", "}")
    if fragment:
        tool_call = build_json_tool_call(
            fragment, call_index
        ) or build_jsonish_tool_call(fragment, call_index)
        if tool_call:
            return tool_call, ""
    tool_call = build_jsonish_tool_call(raw, call_index)
    if tool_call:
        try:
            recovered_arguments = json.loads(
                tool_call.get("function", {}).get("arguments") or "{}"
            )
        except json.JSONDecodeError:
            recovered_arguments = {}
        if recovered_arguments:
            return tool_call, ""
    name_match = re.search(r'"name"\s*:\s*"([^"]+)"', raw, re.DOTALL)
    function_name = (
        normalize_tool_call_name(name_match.group(1)) if name_match else "unknown"
    )
    diagnostic = (
        f"LOCAL_MODEL_TOOL_CALL_TRUNCATED: incomplete {function_name} tool envelope arrived without a closing "
        "</tool_call>. Restart from a smaller command; avoid large heredoc file bodies in a single tool call."
    )
    return None, diagnostic


def build_tool_call(
    function_name: str, function_body: str, call_index: int
) -> dict[str, Any]:
    arguments: dict[str, str] = {}
    for parameter_match in PARAMETER_RE.finditer(function_body):
        key = parameter_match.group(1).strip().split()[0]
        value = parameter_match.group(2).strip()
        arguments[key] = value
    return {
        "id": f"xml_tool_call_{call_index}",
        "type": "function",
        "function": {
            "name": normalize_tool_call_name(function_name),
            "arguments": json.dumps(arguments, ensure_ascii=True),
        },
    }


def parse_gemma4_tool_calls(
    text: str, call_index: int
) -> tuple[list[dict[str, Any]], str, int]:
    tool_calls: list[dict[str, Any]] = []
    remaining_parts: list[str] = []
    cursor = 0
    open_token = "<|tool_call>"

    while True:
        start = text.find(open_token, cursor)
        if start < 0:
            suffix = text[cursor:]
            if suffix.strip():
                remaining_parts.append(suffix.strip())
            break

        prefix = text[cursor:start]
        if prefix.strip():
            remaining_parts.append(prefix.strip())

        index = start + len(open_token)
        match = re.match(r"\s*(?:call:)?([A-Za-z0-9_.-]+)\s*", text[index:], re.DOTALL)
        if not match:
            remaining_parts.append(
                "LOCAL_MODEL_TOOL_CALL_TRUNCATED: Gemma-style tool call arrived without a function name."
            )
            cursor = len(text)
            break

        function_name = normalize_tool_call_name(match.group(1).strip())
        index += match.end()
        while index < len(text) and text[index].isspace():
            index += 1

        arguments: dict[str, Any] | None = None
        end = index
        if index < len(text) and text[index] == "{":
            fragment = extract_gemma4_brace_fragment(text, index)
            if fragment is None:
                remaining_parts.append(
                    "LOCAL_MODEL_TOOL_CALL_TRUNCATED: incomplete Gemma-style tool call arrived without a balanced closing brace."
                )
                cursor = len(text)
                break
            arguments = parse_gemma4_brace_arguments(fragment)
            end = index + len(fragment)
        elif index < len(text) and text[index] == "(":
            argument_text = extract_balanced_call_arguments(text, index)
            if argument_text is None:
                remaining_parts.append(
                    "LOCAL_MODEL_TOOL_CALL_TRUNCATED: incomplete Gemma-style tool call arrived without a balanced closing parenthesis."
                )
                cursor = len(text)
                break
            arguments = {}
            for key in (
                "cmd",
                "chars",
                "path",
                "justification",
                "max_output_tokens",
                "yield_time_ms",
                "session_id",
                "login",
                "tty",
            ):
                value = extract_simple_assignment(argument_text, key)
                if value is not None:
                    arguments[key] = value
            end = index + len(argument_text) + 2
        else:
            remaining_parts.append(
                "LOCAL_MODEL_TOOL_CALL_TRUNCATED: Gemma-style tool call arrived without argument braces."
            )
            cursor = len(text)
            break

        if arguments is None:
            remaining_parts.append(
                f"LOCAL_MODEL_TOOL_CALL_TRUNCATED: could not parse Gemma-style arguments for {function_name}."
            )
            cursor = len(text)
            break

        call_index += 1
        tool_calls.append(
            {
                "id": f"gemma_tool_call_{call_index}",
                "type": "function",
                "function": {
                    "name": function_name,
                    "arguments": json.dumps(arguments, ensure_ascii=True),
                },
            }
        )
        cursor = consume_gemma4_tool_closer(text, end)

    remaining_text = "\n\n".join(part for part in remaining_parts if part)
    return tool_calls, remaining_text, call_index


def parse_python_style_tool_calls(
    text: str, call_index: int
) -> tuple[list[dict[str, Any]], str, int]:
    tool_calls: list[dict[str, Any]] = []
    remaining_parts: list[str] = []
    cursor = 0
    argument_keys = (
        "cmd",
        "command",
        "chars",
        "path",
        "justification",
        "max_output_tokens",
        "yield_time_ms",
        "session_id",
        "login",
        "tty",
        "workdir",
        "shell",
    )

    for match in PYTHON_STYLE_TOOL_CALL_RE.finditer(text):
        open_paren_index = text.find("(", match.end() - 1)
        argument_text = extract_balanced_call_arguments(text, open_paren_index)
        if argument_text is None:
            continue
        prefix = text[cursor : match.start()]
        if prefix.strip():
            remaining_parts.append(prefix.strip())
        cursor = open_paren_index + len(argument_text) + 2

        arguments: dict[str, Any] = {}
        for key in argument_keys:
            value = extract_simple_assignment(argument_text, key)
            if value is not None:
                arguments[key] = value
        if not arguments:
            continue
        call_index += 1
        tool_calls.append(
            {
                "id": f"python_tool_call_{call_index}",
                "type": "function",
                "function": {
                    "name": normalize_tool_call_name(match.group(1).strip()),
                    "arguments": json.dumps(arguments, ensure_ascii=True),
                },
            }
        )

    suffix = text[cursor:]
    if suffix.strip():
        remaining_parts.append(suffix.strip())
    remaining_text = "\n\n".join(part for part in remaining_parts if part)
    return tool_calls, remaining_text, call_index


def parse_xml_tool_calls(text: str) -> tuple[list[dict[str, Any]], str]:
    tool_calls: list[dict[str, Any]] = []
    stripped = text.strip()
    if (
        "<tool_call>" not in stripped
        and "<|tool_call>" not in stripped
        and "<function=" not in stripped
        and not PYTHON_STYLE_TOOL_CALL_RE.search(stripped)
    ):
        return tool_calls, text

    remaining_parts: list[str] = []
    cursor = 0
    call_index = 0
    if "<|tool_call>" in stripped:
        gemma_tool_calls, gemma_remaining_text, call_index = parse_gemma4_tool_calls(
            text, call_index
        )
        if gemma_tool_calls:
            return gemma_tool_calls, gemma_remaining_text

    for match in TOOL_CALL_BLOCK_RE.finditer(text):
        prefix = text[cursor : match.start()]
        if prefix.strip():
            remaining_parts.append(prefix.strip())
        cursor = match.end()
        call_index += 1
        function_name = match.group(1).strip()
        function_body = match.group(2)
        tool_calls.append(build_tool_call(function_name, function_body, call_index))
    suffix = text[cursor:]
    if not tool_calls:
        cursor = 0
        for match in PARTIAL_TOOL_CALL_BLOCK_RE.finditer(text):
            prefix = text[cursor : match.start()]
            if prefix.strip():
                remaining_parts.append(prefix.strip())
            cursor = match.end()
            call_index += 1
            tool_calls.append(
                build_tool_call(match.group(1).strip(), match.group(2), call_index)
            )
        suffix = ""
    if not tool_calls:
        cursor = 0
        for match in JSON_TOOL_CALL_BLOCK_RE.finditer(text):
            prefix = text[cursor : match.start()]
            if prefix.strip():
                remaining_parts.append(prefix.strip())
            cursor = match.end()
            call_index += 1
            raw_json = match.group(1).strip()
            tool_call = build_json_tool_call(
                raw_json, call_index
            ) or build_jsonish_tool_call(raw_json, call_index)
            if tool_call:
                tool_calls.append(tool_call)
        suffix = text[cursor:]
    if not tool_calls:
        cursor = 0
        for match in HYBRID_JSON_XML_TOOL_CALL_BLOCK_RE.finditer(text):
            prefix = text[cursor : match.start()]
            if prefix.strip():
                remaining_parts.append(prefix.strip())
            cursor = match.end()
            call_index += 1
            function_name = match.group(1).strip()
            function_body = match.group(2)
            tool_calls.append(build_tool_call(function_name, function_body, call_index))
        suffix = text[cursor:]
    if not tool_calls:
        cursor = 0
        for match in BARE_TOOL_CALL_BLOCK_RE.finditer(text):
            prefix = text[cursor : match.start()]
            if prefix.strip():
                remaining_parts.append(prefix.strip())
            cursor = match.end()
            call_index += 1
            tool_calls.append(
                build_tool_call(match.group(1).strip(), match.group(2), call_index)
            )
        suffix = ""
    if not tool_calls:
        cursor = 0
        for match in RAW_FUNCTION_BLOCK_RE.finditer(text):
            prefix = text[cursor : match.start()]
            if prefix.strip():
                remaining_parts.append(prefix.strip())
            cursor = match.end()
            call_index += 1
            tool_calls.append(
                build_tool_call(match.group(1).strip(), match.group(2), call_index)
            )
        suffix = text[cursor:]
    if not tool_calls:
        python_tool_calls, python_remaining_text, call_index = (
            parse_python_style_tool_calls(text, call_index)
        )
        if python_tool_calls:
            return python_tool_calls, python_remaining_text
    if not tool_calls:
        match = INCOMPLETE_TOOL_CALL_BLOCK_RE.search(text)
        if match:
            function_name = normalize_tool_call_name(match.group(1).strip())
            remaining_parts = [
                f"LOCAL_MODEL_TOOL_CALL_TRUNCATED: incomplete {function_name} tool envelope "
                "arrived without closing function and tool-call tags."
            ]
            suffix = ""
    if not tool_calls and "<tool_call>" in text:
        call_index += 1
        tool_call, diagnostic = incomplete_jsonish_tool_call(text, call_index)
        if tool_call:
            tool_calls.append(tool_call)
            suffix = ""
        elif diagnostic:
            remaining_parts = [diagnostic]
            suffix = ""
    if not tool_calls and "<|tool_call>" in text:
        match = GEMMA_BRACE_TOOL_CALL_RE.search(text)
        if match:
            function_name = normalize_tool_call_name(match.group(1).strip())
            brace_start = text.find("{", match.end())
            fragment = extract_gemma4_brace_fragment(text, brace_start)
            arguments = parse_gemma4_brace_arguments(fragment) if fragment else None
            if arguments is not None:
                call_index += 1
                tool_calls.append(
                    {
                        "id": f"gemma_tool_call_{call_index}",
                        "type": "function",
                        "function": {
                            "name": function_name,
                            "arguments": json.dumps(arguments, ensure_ascii=True),
                        },
                    }
                )
                prefix = text[: match.start()]
                remaining_parts = [prefix.strip()] if prefix.strip() else []
                suffix = ""
            elif fragment is None:
                remaining_parts = [
                    "LOCAL_MODEL_TOOL_CALL_TRUNCATED: incomplete Gemma-style tool call arrived without a balanced closing brace."
                ]
                suffix = ""
        match = None if tool_calls else GEMMA_TOOL_CALL_RE.search(text)
        if match:
            function_name = normalize_tool_call_name(match.group(1).strip())
            open_paren_index = text.find("(", match.end() - 1)
            argument_text = extract_balanced_call_arguments(text, open_paren_index)
            if argument_text is not None:
                arguments: dict[str, Any] = {}
                for key in (
                    "cmd",
                    "chars",
                    "path",
                    "justification",
                    "max_output_tokens",
                    "yield_time_ms",
                    "session_id",
                    "login",
                    "tty",
                ):
                    value = extract_simple_assignment(argument_text, key)
                    if value is not None:
                        arguments[key] = value
                call_index += 1
                tool_calls.append(
                    {
                        "id": f"gemma_tool_call_{call_index}",
                        "type": "function",
                        "function": {
                            "name": function_name,
                            "arguments": json.dumps(arguments, ensure_ascii=True),
                        },
                    }
                )
                prefix = text[: match.start()]
                suffix = ""
                remaining_parts = [prefix.strip()] if prefix.strip() else []
            else:
                remaining_parts = [
                    "LOCAL_MODEL_TOOL_CALL_TRUNCATED: incomplete Gemma-style tool call arrived without a balanced closing parenthesis."
                ]
                suffix = ""
    if suffix.strip():
        remaining_parts.append(suffix.strip())
    if not tool_calls and not remaining_parts:
        return tool_calls, text
    remaining_text = "\n\n".join(part for part in remaining_parts if part)
    if remaining_text.strip() in {"...", "• ...", "•", ""}:
        remaining_text = ""
    return tool_calls, remaining_text


def collapse_exact_duplicate_text(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return text
    lines = [line.strip() for line in stripped.splitlines() if line.strip()]
    if len(lines) == 2 and lines[0] == lines[1]:
        return lines[0]
    midpoint = len(stripped) // 2
    if len(stripped) % 2 == 0 and stripped[:midpoint] == stripped[midpoint:]:
        return stripped[:midpoint].strip()
    return text


def suppress_visible_tool_markup(text: str, *, parsed_tool_call: bool = False) -> str:
    return tool_policy_suppress_visible_tool_markup(
        text, parsed_tool_call=parsed_tool_call
    )


def unclosed_shell_quote(cmd: str) -> str:
    quote = ""
    escaped = False
    for ch in cmd:
        if escaped:
            escaped = False
            continue
        if ch == "\\" and quote != "'":
            escaped = True
            continue
        if quote:
            if ch == quote:
                quote = ""
            continue
        if ch in {"'", '"'}:
            quote = ch
    return quote


def suppressed_exec_marker(message: str) -> str:
    return tool_policy_suppressed_exec_marker(message)


def bounded_single_text_cat_command(cmd: str) -> str:
    if re.search(r"[;&|<>]", cmd):
        return ""
    try:
        parts = shlex.split(cmd)
    except ValueError:
        return ""
    if len(parts) != 2 or parts[0] != "cat":
        return ""
    path = parts[1]
    if path.startswith("-"):
        return ""
    if not re.search(
        r"\.(?:md|markdown|txt|rst|json|jsonl|csv)\Z", path, flags=re.IGNORECASE
    ):
        return ""
    return f"sed -n '1,220p' {shlex.quote(path)}"


def command_looks_like_unbounded_read_dump(cmd: str) -> bool:
    if not READ_DUMP_GUARD_ENABLED:
        return False
    compact = " ".join(cmd.split()).lower()
    text_ext = r"(?:md|markdown|txt|rst|json|jsonl|csv)"

    raw_shell_dump_patterns = (
        rf"\bcat\b[^;&|]*\*\.{text_ext}\b",
        r"\bfind\b.+\s-exec\s+cat\b",
        r"\bfind\b.+\|\s*xargs\s+cat\b",
        rf"\bfor\b.+\bin\b.+\*\.{text_ext}\b.+\bcat\b",
    )
    if any(re.search(pattern, compact) for pattern in raw_shell_dump_patterns):
        return True

    if not re.search(r"\bpython3?\s+-c\b", compact):
        return False
    if "print" not in compact:
        return False

    multi_file_read = any(
        marker in compact
        for marker in (
            ".glob(",
            ".rglob(",
            "glob.glob",
            "os.listdir",
            "os.walk",
            "*.md",
            "*.markdown",
            "*.txt",
            "*.rst",
            "*.json",
            "*.jsonl",
            "*.csv",
        )
    )
    raw_read = any(marker in compact for marker in (".read_text(", ".read(", "open("))
    full_content_print = any(
        re.search(pattern, compact)
        for pattern in (
            r"\bprint\s*\(\s*(?:content|contents|data|text|body|bodies|files)\s*\)",
            r"\bprint\s*\(\s*f\.read\s*\(",
            r"\bprint\s*\([^)]*\.read_text\s*\(",
            r"\bjson\.dump[s]?\s*\(\s*(?:content|contents|data|text|body|bodies|files)\b",
            r"\bprint\s*\(\s*['\"][^'\"]*['\"]\s*,\s*(?:content|contents|data|text|body)\b",
        )
    )
    return multi_file_read and raw_read and full_content_print


def python_c_quoted_span(cmd: str) -> tuple[int, int, int, str] | None:
    match = re.search(r"\bpython3?\s+-c\s*(['\"])", cmd)
    if not match:
        return None
    quote = match.group(1)
    quote_start = match.end() - 1
    escaped = False
    for index in range(quote_start + 1, len(cmd)):
        ch = cmd[index]
        if escaped:
            escaped = False
            continue
        if ch == "\\" and quote != "'":
            escaped = True
            continue
        if ch == quote:
            return quote_start, quote_start + 1, index, quote
    return None


def normalize_multiline_python_c(cmd: str) -> str:
    span = python_c_quoted_span(cmd)
    if not span:
        return cmd
    quote_start, code_start, code_end, _quote = span
    code = cmd[code_start:code_end]
    if "\n" not in code:
        return cmd
    prefix = cmd[:quote_start]
    suffix = cmd[code_end + 1 :]
    rewritten = shlex.quote(f"exec({code!r})")
    return f"{prefix}{rewritten}{suffix}"


def sanitize_exec_command(cmd: str) -> str:
    cleaned = cmd.strip()
    if not cleaned:
        return cleaned
    if unclosed_shell_quote(cleaned):
        return suppressed_exec_marker(
            "LOCAL_MODEL_TOOL_CALL_TRUNCATED: exec_command was suppressed because shell quotes were unbalanced."
        )
    if "\n" in cleaned and re.search(r"\bpython3?\s+-c\s*['\"]", cleaned):
        cleaned = normalize_multiline_python_c(cleaned)
    bounded_cat = bounded_single_text_cat_command(cleaned)
    if bounded_cat:
        cleaned = bounded_cat
    elif command_looks_like_unbounded_read_dump(cleaned):
        return suppressed_exec_marker(
            "LOCAL_MODEL_TOOL_CALL_TOO_LARGE: an unbounded multi-file read was suppressed."
        )
    if re.search(r"\bsed\s+-i\b", cleaned) and re.search(r"\.csv(?:\s|$)", cleaned):
        return suppressed_exec_marker(
            "LOCAL_MODEL_TOOL_CALL_TRUNCATED: an in-place sed CSV edit was suppressed; use a CSV-aware writer."
        )
    if (
        re.search(r"\.csv(?:['\"]|\s|$)", cleaned)
        and "DictReader" in cleaned
        and "csv.writer" in cleaned
        and "writerows" in cleaned
    ):
        return suppressed_exec_marker(
            "LOCAL_MODEL_TOOL_CALL_TRUNCATED: writing dictionary rows through csv.writer was suppressed; use DictWriter."
        )

    has_heredoc = bool(re.search(r"<<-?\s*", cleaned) or "cat >" in cleaned)
    risky_text_write = bool(
        has_heredoc
        and re.search(
            r"\.(?:md|markdown|txt|rst|json|jsonl|csv)(?:['\"]|\s|$)",
            cleaned,
            re.IGNORECASE,
        )
    )
    if (
        risky_text_write
        or len(cleaned) > MAX_EXEC_COMMAND_CHARS
        or (has_heredoc and len(cleaned) > MAX_HEREDOC_COMMAND_CHARS)
    ):
        return suppressed_exec_marker(
            "LOCAL_MODEL_TOOL_CALL_TOO_LARGE: an oversized generated command or heredoc was suppressed."
        )
    heredoc_match = re.search(r"<<-?\s*['\"]?([A-Za-z0-9_.-]+)['\"]?", cleaned)
    if heredoc_match and not re.search(
        rf"(?m)^{re.escape(heredoc_match.group(1))}\s*$",
        cleaned,
    ):
        return suppressed_exec_marker(
            "LOCAL_MODEL_TOOL_CALL_TRUNCATED: a heredoc terminator was missing."
        )

    cleaned = re.split(r"\n\s*</", cleaned, maxsplit=1)[0].strip()
    cleaned = re.sub(
        r"</(?:cmd|chars|path|parameter|function|tool_call)>\s*$", "", cleaned
    ).strip()
    if cleaned.endswith("|"):
        cleaned = cleaned.rstrip("|").rstrip() + " | head -500"
    if re.search(r"\bgit\s+diff\b", cleaned):
        safe_pagers = ("| head", "| sed", "| tail", "| rg", "| grep", "| awk")
        if "--stat" in cleaned and not any(pager in cleaned for pager in safe_pagers):
            cleaned += " | head -220"
        elif "--stat" not in cleaned and not any(
            pager in cleaned for pager in safe_pagers
        ):
            cleaned += " | head -500"
    return cleaned


def normalize_exec_workdir(parsed: dict[str, Any]) -> None:
    workdir = parsed.get("workdir")
    cmd = parsed.get("cmd")
    if not isinstance(workdir, str) or not isinstance(cmd, str):
        return
    workdir = workdir.strip()
    if not workdir or Path(workdir).exists():
        return
    parent = Path(workdir).parent
    if not parent.exists():
        return
    if "mkdir -p" not in cmd or workdir not in cmd:
        return
    parsed.pop("workdir", None)


def parse_yamlish_update_plan(plan_text: str) -> list[dict[str, str]] | None:
    items: list[dict[str, str]] = []
    current: dict[str, str] = {}
    lines = plan_text.splitlines()
    numbered_re = re.compile(
        r"^\s*(?:-\s*)?(?:step\s*)?\d+[.):]\s*(.*?)\s*\(\s*status\s*:\s*([^)]+)\)\s*$",
        re.IGNORECASE,
    )
    numbered_paren_status_re = re.compile(
        r"^\s*(?:-\s*)?(?:step\s*)?\d+[.):]\s*(.*?)\s*\(\s*(in_progress|pending|completed)\s*\)\s*$",
        re.IGNORECASE,
    )
    numbered_inline_status_re = re.compile(
        r"^\s*(?:-\s*)?(?:step\s*)?\d+[.):]\s*(.*?)\s+(?:and\s+)?status\s+([A-Za-z_ -]+)\.?\s*$",
        re.IGNORECASE,
    )
    numbered_items: list[dict[str, str]] = []
    for raw_line in lines:
        match = numbered_re.match(raw_line)
        if match:
            numbered_items.append(
                {"step": match.group(1).strip(), "status": match.group(2).strip()}
            )
            continue
        match = numbered_paren_status_re.match(raw_line)
        if match:
            numbered_items.append(
                {"step": match.group(1).strip(), "status": match.group(2).strip()}
            )
            continue
        match = numbered_inline_status_re.match(raw_line)
        if match:
            step = re.sub(
                r"\s+(?:and\s+)?$", "", match.group(1).strip(), flags=re.IGNORECASE
            )
            status = match.group(2).strip().rstrip(".")
            numbered_items.append({"step": step, "status": status})
    if numbered_items:
        return numbered_items
    if "status:" not in plan_text:
        return None
    if "- step:" not in plan_text:
        return None
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("- "):
            if current:
                items.append(current)
            current = {}
            line = line[2:].strip()
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key in {"step", "title", "status"}:
            current[key] = value
    if current:
        items.append(current)
    normalized: list[dict[str, str]] = []
    for item in items:
        status = item.get("status", "").strip()
        step = item.get("title") or item.get("step") or ""
        if item.get("title") and item.get("step", "").isdigit():
            step = item["title"]
        step = step.strip()
        if not step or not status:
            return None
        normalized.append({"step": step, "status": status})
    return normalized or None


def normalize_update_plan_items(plan: Any) -> list[dict[str, str]] | None:
    if isinstance(plan, str):
        return parse_yamlish_update_plan(plan.strip())
    if not isinstance(plan, list):
        return None
    normalized: list[dict[str, str]] = []
    string_item_re = re.compile(
        r"^(.*?)\s+(?:and\s+)?status\s+([A-Za-z_ -]+)\.?\s*$", re.IGNORECASE
    )
    for item in plan:
        if isinstance(item, dict):
            step = str(item.get("step") or item.get("title") or "").strip()
            status = str(item.get("status") or "").strip()
        elif isinstance(item, str):
            match = string_item_re.match(item.strip())
            if not match:
                return None
            step = match.group(1).strip()
            status = match.group(2).strip().rstrip(".")
        else:
            return None
        if not step or not status:
            return None
        normalized.append({"step": step, "status": status})
    return normalized or None


def plan_items_from_explanation(explanation: Any) -> list[dict[str, str]] | None:
    if not isinstance(explanation, str):
        return None
    text = explanation.strip()
    if not text:
        return None
    parsed = parse_yamlish_update_plan(text)
    if parsed:
        return parsed
    numbered = re.findall(
        r"(?:^|[:,;]\s*)\d+[.)]\s*(.*?)(?:\s+and\s+status\s+|\s+status\s+)(in_progress|pending|completed)\b",
        text,
        flags=re.IGNORECASE,
    )
    if numbered:
        return [
            {"step": step.strip(), "status": status.strip()}
            for step, status in numbered
        ]
    clauses = re.split(r"\s*,\s*(?:then\s+)?|\s+then\s+", text, flags=re.IGNORECASE)
    items: list[dict[str, str]] = []
    inline_re = re.compile(
        r"^(?:create\s+\w+\s+steps?:\s*)?(.*?)(?:\s+and\s+status\s+|\s+status\s+)(in_progress|pending|completed)\b",
        re.IGNORECASE,
    )
    for clause in clauses:
        match = inline_re.search(clause.strip())
        if not match:
            continue
        step = re.sub(
            r"^(?:step\s*\d+\s*(?:is|:)?\s*)",
            "",
            match.group(1).strip(),
            flags=re.IGNORECASE,
        )
        if step:
            items.append({"step": step, "status": match.group(2).strip()})
    return items or None


def normalize_function_arguments(
    function_name: str,
    arguments: Any,
    *,
    latest_user_text: str = "",
) -> str:
    del latest_user_text
    normalized_name = normalize_tool_call_name(function_name)
    if isinstance(arguments, str):
        try:
            parsed = json_loads_lenient(arguments)
        except json.JSONDecodeError:
            return "{}"
    elif isinstance(arguments, dict):
        parsed = dict(arguments)
    else:
        return "{}"
    if not isinstance(parsed, dict):
        return "{}"

    if normalized_name == "exec_command":
        if not isinstance(parsed.get("cmd"), str) and isinstance(
            parsed.get("command"), str
        ):
            parsed["cmd"] = parsed.pop("command")
        if isinstance(parsed.get("cmd"), str):
            parsed["cmd"] = sanitize_exec_command(parsed["cmd"])
        normalize_exec_workdir(parsed)
    elif normalized_name == "update_plan":
        plan_value = parsed.get("plan")
        if isinstance(plan_value, str) and plan_value.strip().startswith("["):
            try:
                plan_value = json_loads_lenient(plan_value)
            except json.JSONDecodeError:
                pass
        parsed_plan = normalize_update_plan_items(plan_value)
        if parsed_plan is None:
            parsed_plan = plan_items_from_explanation(parsed.get("explanation"))
        if parsed_plan is not None:
            parsed["plan"] = parsed_plan

    numeric_fields = {
        "exec_command": {"max_output_tokens", "yield_time_ms"},
        "write_stdin": {"session_id", "max_output_tokens", "yield_time_ms"},
    }.get(normalized_name, set())
    for field in numeric_fields:
        value = parsed.get(field)
        if isinstance(value, str) and re.fullmatch(r"\s*\d+\s*", value):
            parsed[field] = int(value)
    for field in {"login", "tty"} if normalized_name == "exec_command" else set():
        value = parsed.get(field)
        if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
            parsed[field] = value.strip().lower() == "true"
        elif isinstance(value, str):
            parsed.pop(field, None)
    return json.dumps(parsed, ensure_ascii=True, sort_keys=True)


def allowed_tool_names(payload: dict[str, Any] | None) -> set[str]:
    if not isinstance(payload, dict):
        return set()
    names: set[str] = set()
    tools = payload.get("tools")
    if not isinstance(tools, list):
        return names
    for tool in tools:
        function = function_tool_schema(tool) if isinstance(tool, dict) else None
        if function:
            name = normalize_tool_call_name(str(function.get("name") or ""))
            if name:
                names.add(name)
    return names


def required_tool_parameters(payload: dict[str, Any] | None, name: str) -> set[str]:
    if not isinstance(payload, dict):
        return set()
    tools = payload.get("tools")
    if not isinstance(tools, list):
        return set()
    for tool in tools:
        function = function_tool_schema(tool) if isinstance(tool, dict) else None
        if not function:
            continue
        tool_name_value = normalize_tool_call_name(str(function.get("name") or ""))
        if tool_name_value != name:
            continue
        parameters = function.get("parameters")
        required = parameters.get("required") if isinstance(parameters, dict) else None
        return (
            {str(parameter) for parameter in required}
            if isinstance(required, list)
            else set()
        )
    return set()


def safe_guard_message(reason: str, safe_next_action: str = "") -> str:
    message = f"The local runtime rejected a proposed tool call: {reason.strip().rstrip('.')}."
    if safe_next_action:
        message += f" {safe_next_action.strip()}"
    return message


def native_or_parsed_tool_calls(
    message: dict[str, Any],
) -> tuple[list[dict[str, Any]], str]:
    message_text = extract_text(message.get("content"))
    tool_calls = message.get("tool_calls")
    if not isinstance(tool_calls, list) or not tool_calls:
        legacy_call = message.get("function_call")
        if isinstance(legacy_call, dict):
            tool_calls = [
                {
                    "id": "legacy_function_call_1",
                    "type": "function",
                    "function": legacy_call,
                }
            ]
    if isinstance(tool_calls, list) and tool_calls:
        return tool_calls, message_text
    parsed, cleaned = parse_xml_tool_calls(message_text)
    return parsed, cleaned


def chat_completion_to_response(
    chat_payload: dict[str, Any],
    request_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    choices = chat_payload.get("choices")
    choice = choices[0] if isinstance(choices, list) and choices else {}
    message = choice.get("message") if isinstance(choice, dict) else {}
    if not isinstance(message, dict):
        message = {}

    raw_items = responses_raw_items(request_payload or {})
    tool_calls, message_text = native_or_parsed_tool_calls(message)
    parsed_tool_call = bool(tool_calls)
    message_text = suppress_visible_tool_markup(
        collapse_exact_duplicate_text(message_text),
        parsed_tool_call=parsed_tool_call,
    )
    if contains_local_interface_marker(message_text):
        message_text = (
            "The local model returned an internal guard diagnostic instead of a usable answer. "
            "Retry with one smaller bounded action or continue from the latest successful output."
        )

    allowed = allowed_tool_names(request_payload)
    output: list[dict[str, Any]] = []
    rejected: list[str] = []
    effective_history = list(raw_items)
    seen_call_ids: set[str] = set()
    runtime_guard = RuntimeGuard(runtime_guard_config())

    for index, tool_call in enumerate(
        tool_calls if isinstance(tool_calls, list) else []
    ):
        if not isinstance(tool_call, dict):
            rejected.append("the upstream tool entry was not an object")
            continue
        function = tool_call.get("function")
        if not isinstance(function, dict):
            rejected.append("the upstream tool entry had no function object")
            continue
        name = normalize_tool_call_name(str(function.get("name") or ""))
        if not name:
            rejected.append("the upstream tool entry had no function name")
            continue
        if name not in allowed:
            rejected.append(
                f"function {name!r} was not present in the request tool list"
            )
            continue

        raw_arguments = function.get("arguments")
        if isinstance(raw_arguments, str):
            try:
                parsed_arguments = json_loads_lenient(raw_arguments)
            except json.JSONDecodeError:
                rejected.append(f"function {name!r} had malformed JSON arguments")
                continue
        elif isinstance(raw_arguments, dict):
            parsed_arguments = raw_arguments
        else:
            rejected.append(f"function {name!r} arguments were not a JSON object")
            continue
        if not isinstance(parsed_arguments, dict):
            rejected.append(f"function {name!r} arguments were not a JSON object")
            continue
        missing_parameters = required_tool_parameters(request_payload, name) - set(
            parsed_arguments
        )
        if missing_parameters:
            missing = ", ".join(sorted(missing_parameters))
            rejected.append(
                f"function {name!r} was missing required parameters: {missing}"
            )
            continue

        normalized_arguments = normalize_function_arguments(name, parsed_arguments)
        if contains_local_interface_marker(normalized_arguments):
            rejected.append(
                f"function {name!r} exceeded the local command-safety envelope"
            )
            continue
        proposed = {
            "type": "function_call",
            "name": name,
            "arguments": normalized_arguments,
        }
        decision = runtime_guard.evaluate_proposed_call(effective_history, proposed)
        if decision.action != GuardAction.ALLOW:
            rejected.append(
                safe_guard_message(decision.message, decision.safe_next_action)
            )
            continue

        call_id = str(
            tool_call.get("id") or tool_call.get("call_id") or f"call_{index + 1}"
        )
        if call_id in seen_call_ids:
            call_id = f"{call_id}_{index + 1}"
        seen_call_ids.add(call_id)
        item = {
            "id": f"fc_{index + 1}",
            "type": "function_call",
            "status": "completed",
            "call_id": call_id,
            "name": name,
            "arguments": normalized_arguments,
        }
        output.append(item)
        effective_history.append(
            {
                "type": "function_call",
                "call_id": call_id,
                "name": name,
                "arguments": normalized_arguments,
            }
        )

    if rejected and not output:
        message_text = "\n".join(dict.fromkeys(rejected))
    elif rejected:
        message_text = "\n".join(
            part
            for part in (
                message_text,
                "Some proposed tool calls were rejected: "
                + "; ".join(dict.fromkeys(rejected)),
            )
            if part
        )
    if message_text:
        output.insert(
            0,
            {
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [
                    {
                        "type": "output_text",
                        "text": message_text,
                        "annotations": [],
                    }
                ],
            },
        )
    if not output:
        output.append(
            {
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": "", "annotations": []}],
            }
        )

    parallel = (
        bool(request_payload.get("parallel_tool_calls", False))
        if isinstance(request_payload, dict)
        else False
    )
    return {
        "id": str(chat_payload.get("id") or "resp_local_qwen"),
        "object": "response",
        "created": coerce_nonnegative_int(chat_payload.get("created")),
        "model": str(chat_payload.get("model") or ""),
        "status": "completed",
        "output": output,
        "parallel_tool_calls": parallel,
        "tools": [],
        "usage": usage_from_chat(chat_payload),
    }


def sse_event(event: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(event, separators=(',', ':'))}\n\n".encode()


def emit_responses_stream(
    handler: BaseHTTPRequestHandler, response_payload: dict[str, Any]
) -> None:
    base_response = dict(response_payload)
    base_response["status"] = "in_progress"
    base_response["output"] = []

    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream")
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("Connection", "close")
    handler.end_headers()

    handler.wfile.write(
        sse_event({"type": "response.created", "response": base_response})
    )
    handler.wfile.write(
        sse_event({"type": "response.in_progress", "response": base_response})
    )

    for output_index, item in enumerate(response_payload.get("output", [])):
        item_type = item.get("type")
        if item_type == "message":
            added_item = {
                "id": item["id"],
                "type": "message",
                "role": "assistant",
                "status": "in_progress",
                "content": [],
            }
            part = item["content"][0]
            handler.wfile.write(
                sse_event(
                    {
                        "type": "response.output_item.added",
                        "output_index": output_index,
                        "item": added_item,
                    }
                )
            )
            handler.wfile.write(
                sse_event(
                    {
                        "type": "response.content_part.added",
                        "output_index": output_index,
                        "item_id": item["id"],
                        "content_index": 0,
                        "part": {"type": "output_text", "text": "", "annotations": []},
                    }
                )
            )
            handler.wfile.write(
                sse_event(
                    {
                        "type": "response.output_text.delta",
                        "output_index": output_index,
                        "item_id": item["id"],
                        "content_index": 0,
                        "delta": part.get("text", ""),
                    }
                )
            )
            handler.wfile.write(
                sse_event(
                    {
                        "type": "response.output_text.done",
                        "output_index": output_index,
                        "item_id": item["id"],
                        "content_index": 0,
                        "text": part.get("text", ""),
                    }
                )
            )
            handler.wfile.write(
                sse_event(
                    {
                        "type": "response.content_part.done",
                        "output_index": output_index,
                        "item_id": item["id"],
                        "content_index": 0,
                        "part": part,
                    }
                )
            )
            handler.wfile.write(
                sse_event(
                    {
                        "type": "response.output_item.done",
                        "output_index": output_index,
                        "item": item,
                    }
                )
            )
            continue

        if item_type == "function_call":
            added_item = {
                "id": item["id"],
                "type": "function_call",
                "call_id": item["call_id"],
                "name": item["name"],
                "arguments": "",
                "status": "in_progress",
            }
            arguments = item.get("arguments", "")
            handler.wfile.write(
                sse_event(
                    {
                        "type": "response.output_item.added",
                        "output_index": output_index,
                        "item": added_item,
                    }
                )
            )
            handler.wfile.write(
                sse_event(
                    {
                        "type": "response.function_call_arguments.delta",
                        "output_index": output_index,
                        "item_id": item["id"],
                        "delta": arguments,
                        "name": item["name"],
                        "call_id": item["call_id"],
                    }
                )
            )
            handler.wfile.write(
                sse_event(
                    {
                        "type": "response.function_call_arguments.done",
                        "output_index": output_index,
                        "item_id": item["id"],
                        "arguments": arguments,
                        "name": item["name"],
                        "call_id": item["call_id"],
                    }
                )
            )
            handler.wfile.write(
                sse_event(
                    {
                        "type": "response.output_item.done",
                        "output_index": output_index,
                        "item": item,
                    }
                )
            )

    handler.wfile.write(
        sse_event({"type": "response.completed", "response": response_payload})
    )
    handler.wfile.write(b"data: [DONE]\n\n")
    handler.wfile.flush()


def send_responses_payload(
    handler: BaseHTTPRequestHandler,
    response_payload: dict[str, Any],
    *,
    stream: bool,
) -> None:
    if stream:
        emit_responses_stream(handler, response_payload)
    else:
        write_json(handler, 200, response_payload)


SyntheticResponseHandler = Callable[[dict[str, Any]], dict[str, Any] | None]


def runtime_guard_response(payload: dict[str, Any]) -> dict[str, Any] | None:
    decision = RuntimeGuard(runtime_guard_config()).evaluate_history(
        responses_raw_items(payload)
    )
    if decision.action == GuardAction.ALLOW:
        return None
    return response_payload_with_message(
        safe_guard_message(decision.message, decision.safe_next_action),
        model=str(payload.get("model") or ""),
    )


SYNTHETIC_RESPONSE_HANDLERS: tuple[tuple[str, SyntheticResponseHandler], ...] = (
    ("runtime_guard", runtime_guard_response),
)


def synthetic_response_handler_names() -> list[str]:
    return [name for name, _handler in SYNTHETIC_RESPONSE_HANDLERS]


def synthetic_response_from_payload(
    payload: dict[str, Any],
) -> tuple[str, dict[str, Any] | None]:
    for name, handler in SYNTHETIC_RESPONSE_HANDLERS:
        response = handler(payload)
        if response is not None:
            return name, response
    return "", None


class ProxyHandler(BaseHTTPRequestHandler):
    target_base = DEFAULT_TARGET_BASE
    log_path: Path | None = None
    system_prompt_file: str | None = DEFAULT_SYSTEM_PROMPT_FILE
    native_tools = False
    max_output_tokens = DEFAULT_MAX_OUTPUT_TOKENS
    max_forward_body_bytes = DEFAULT_MAX_FORWARD_BODY_BYTES
    tool_temperature = DEFAULT_TOOL_TEMPERATURE
    tool_top_p = DEFAULT_TOOL_TOP_P
    tool_top_k = DEFAULT_TOOL_TOP_K
    tool_min_p = DEFAULT_TOOL_MIN_P
    tool_reasoning_effort = DEFAULT_TOOL_REASONING_EFFORT
    enable_thinking = False
    preserve_thinking = False
    context_limit_tokens = DEFAULT_CONTEXT_LIMIT_TOKENS
    upstream_timeout_seconds = DEFAULT_UPSTREAM_TIMEOUT_SECONDS

    def _write_log(self, entry: dict[str, Any]) -> None:
        if self.log_path is None:
            return
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")

    def _proxy_models(self) -> None:
        request = Request(f"{self.target_base}/v1/models", method="GET")
        try:
            with urlopen(request, timeout=60) as response:
                body = response.read()
                try:
                    payload = json.loads(body.decode("utf-8"))
                except json.JSONDecodeError:
                    payload = None
                if (
                    isinstance(payload, dict)
                    and isinstance(payload.get("data"), list)
                    and "models" not in payload
                ):
                    payload = dict(payload)
                    payload["models"] = []
                    body = json.dumps(payload, sort_keys=True).encode("utf-8")
                self.send_response(response.status)
                for key, value in response.getheaders():
                    if key.lower() in {"transfer-encoding", "content-length"}:
                        continue
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        except HTTPError as exc:
            body = exc.read()
            self.send_response(exc.code)
            for key, value in exc.headers.items():
                if key.lower() == "transfer-encoding":
                    continue
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)
        except (URLError, TimeoutError, socket.timeout) as exc:
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    {"error": {"message": str(exc), "type": "proxy_error"}}
                ).encode("utf-8")
            )

    def _proxy_status(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(
            json.dumps(
                build_bridge_status_payload(
                    version=BRIDGE_VERSION,
                    runtime_guard=runtime_guard_config(),
                    target_base=self.target_base,
                    native_tools=self.native_tools,
                    system_prompt_file=self.system_prompt_file,
                    max_output_tokens=self.max_output_tokens,
                    context_limit_tokens=self.context_limit_tokens,
                    max_forward_body_bytes=self.max_forward_body_bytes,
                    tool_temperature=self.tool_temperature,
                    tool_top_p=self.tool_top_p,
                    tool_top_k=self.tool_top_k,
                    tool_min_p=self.tool_min_p,
                    tool_reasoning_effort=self.tool_reasoning_effort,
                    enable_thinking=self.enable_thinking,
                    preserve_thinking=self.preserve_thinking,
                    max_heredoc_command_chars=MAX_HEREDOC_COMMAND_CHARS,
                    max_exec_command_chars=MAX_EXEC_COMMAND_CHARS,
                    repeated_tool_call_threshold=REPEATED_TOOL_CALL_THRESHOLD,
                    turn_tool_call_cap=TURN_TOOL_CALL_CAP,
                    global_duplicate_tool_call_threshold=GLOBAL_DUPLICATE_TOOL_CALL_THRESHOLD,
                    alternating_tool_call_pattern_cycles=ALTERNATING_TOOL_CALL_PATTERN_CYCLES,
                    shell_command_stagnation_threshold=SHELL_COMMAND_STAGNATION_THRESHOLD,
                    upstream_timeout_seconds=self.upstream_timeout_seconds,
                    synthetic_response_handlers=synthetic_response_handler_names(),
                ),
                sort_keys=True,
            ).encode("utf-8")
        )

    def _proxy_responses(self) -> None:
        try:
            body_obj, _raw_body = load_json_body(
                self,
                max_body_bytes=ProxyHandler.max_forward_body_bytes,
            )
        except RequestBodyError as exc:
            write_json(
                self,
                exc.status_code,
                {"error": {"message": str(exc), "type": "invalid_request"}},
            )
            return
        if not isinstance(body_obj, dict):
            write_json(
                self,
                400,
                {
                    "error": {
                        "message": "expected JSON object body",
                        "type": "invalid_request",
                    }
                },
            )
            return

        sanitized, meta = sanitize_tools(body_obj)
        synthetic_handler, synthetic_response = synthetic_response_from_payload(
            sanitized
        )
        if synthetic_response is not None:
            self._write_log(
                {
                    "method": self.command,
                    "path": self.path,
                    "event": "synthetic_response",
                    "synthetic_handler": synthetic_handler,
                    "removed_tool_count": meta["removed_tool_count"],
                    "remaining_tool_count": meta["remaining_tool_count"],
                    **response_payload_summary(synthetic_response),
                }
            )
            send_responses_payload(
                self,
                synthetic_response,
                stream=body_obj.get("stream") is True,
            )
            return

        chat_request = responses_payload_to_tabby_chat(sanitized)
        upstream_body = json.dumps(chat_request).encode("utf-8")
        self._write_log(
            {
                "method": self.command,
                "path": self.path,
                "removed_tool_count": meta["removed_tool_count"],
                "removed_tools": meta["removed_tools"],
                "remaining_tool_count": meta["remaining_tool_count"],
                "native_tools": ProxyHandler.native_tools,
                "compact_tool_prompt": bool(
                    chat_request.get("messages") and meta["remaining_tool_count"]
                ),
                "forward_body_bytes": len(upstream_body),
                "message_count": len(chat_request.get("messages", [])),
                "stream_passthrough": False,
                "max_tokens": chat_request.get("max_tokens"),
            }
        )

        if len(upstream_body) > ProxyHandler.max_forward_body_bytes:
            self._write_log(
                {
                    "method": self.command,
                    "path": self.path,
                    "event": "forward_body_too_large",
                    "forward_body_bytes": len(upstream_body),
                    "max_forward_body_bytes": ProxyHandler.max_forward_body_bytes,
                    "removed_tool_count": meta["removed_tool_count"],
                    "remaining_tool_count": meta["remaining_tool_count"],
                }
            )
            self.send_response(413)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    {
                        "error": {
                            "message": (
                                f"forwarded prompt body is {len(upstream_body)} bytes, "
                                f"above limit {ProxyHandler.max_forward_body_bytes}; compact or resume with less context"
                            ),
                            "type": "context_limit",
                        }
                    }
                ).encode("utf-8")
            )
            return

        request = Request(
            f"{self.target_base}/v1/chat/completions",
            data=upstream_body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(
                request, timeout=ProxyHandler.upstream_timeout_seconds
            ) as response:
                chat_payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            body = exc.read()
            self._write_log(
                {
                    "method": self.command,
                    "path": self.path,
                    "event": "upstream_http_error",
                    "status": exc.code,
                    "body_preview": body[:1000].decode("utf-8", errors="replace"),
                }
            )
            self.send_response(exc.code)
            content_type = exc.headers.get("Content-Type", "application/json")
            self.send_header("Content-Type", content_type)
            self.end_headers()
            self.wfile.write(body)
            return
        except (URLError, TimeoutError, socket.timeout) as exc:
            self._write_log(
                {
                    "method": self.command,
                    "path": self.path,
                    "event": "upstream_url_error",
                    "error": str(exc),
                }
            )
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    {"error": {"message": str(exc), "type": "proxy_error"}}
                ).encode("utf-8")
            )
            return
        except json.JSONDecodeError as exc:
            self._write_log(
                {
                    "method": self.command,
                    "path": self.path,
                    "event": "upstream_json_error",
                    "error": str(exc),
                }
            )
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    {
                        "error": {
                            "message": f"invalid upstream JSON: {exc}",
                            "type": "proxy_error",
                        }
                    }
                ).encode("utf-8")
            )
            return

        if not isinstance(chat_payload, dict):
            payload_type = type(chat_payload).__name__
            self._write_log(
                {
                    "method": self.command,
                    "path": self.path,
                    "event": "upstream_json_shape_error",
                    "payload_type": payload_type,
                }
            )
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    {
                        "error": {
                            "message": (
                                "invalid upstream JSON: expected an object, "
                                f"received {payload_type}"
                            ),
                            "type": "proxy_error",
                        }
                    }
                ).encode("utf-8")
            )
            return

        choices = chat_payload.get("choices")
        choice = choices[0] if isinstance(choices, list) and choices else {}
        message = choice.get("message") if isinstance(choice, dict) else {}
        raw_message_text = (
            extract_text(message.get("content")) if isinstance(message, dict) else ""
        )
        if "<tool_call" in raw_message_text or "<|tool_call" in raw_message_text:
            self._write_log(
                {
                    "event": "upstream_tool_markup",
                    "raw_message_len": len(raw_message_text),
                    "markup_family": (
                        "gemma" if "<|tool_call" in raw_message_text else "xml"
                    ),
                }
            )
        response_payload = chat_completion_to_response(chat_payload, sanitized)
        self._write_log(
            {
                "method": self.command,
                "path": self.path,
                "event": "upstream_response",
                **upstream_choice_summary(chat_payload),
                **response_payload_summary(response_payload),
            }
        )
        send_responses_payload(
            self,
            response_payload,
            stream=body_obj.get("stream") is True,
        )

    def do_GET(self) -> None:  # noqa: N802
        request_path = self.path.split("?", 1)[0]
        if request_path in {"/status", "/__tabby_proxy_status"}:
            self._proxy_status()
            return
        if request_path == "/v1/models":
            self._proxy_models()
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] == "/v1/responses":
            self._proxy_responses()
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        return


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Bridge Codex /v1/responses calls to an OpenAI-compatible "
            "/v1/chat/completions endpoint."
        )
    )
    parser.add_argument("--listen-host", default=DEFAULT_LISTEN_HOST)
    parser.add_argument("--listen-port", type=int, default=DEFAULT_LISTEN_PORT)
    parser.add_argument("--target-base", default=DEFAULT_TARGET_BASE)
    parser.add_argument("--system-prompt-file", default=DEFAULT_SYSTEM_PROMPT_FILE)
    parser.add_argument(
        "--native-tools",
        action="store_true",
        help=(
            "Forward OpenAI-style tools to the target endpoint instead of using "
            "the compact bridge XML prompt."
        ),
    )
    parser.add_argument(
        "--log-path",
        default=str(DEFAULT_BRIDGE_LOG_PATH),
    )
    parser.add_argument(
        "--max-output-tokens", type=int, default=DEFAULT_MAX_OUTPUT_TOKENS
    )
    parser.add_argument(
        "--context-limit-tokens", type=int, default=DEFAULT_CONTEXT_LIMIT_TOKENS
    )
    parser.add_argument(
        "--max-forward-body-bytes", type=int, default=DEFAULT_MAX_FORWARD_BODY_BYTES
    )
    parser.add_argument(
        "--tool-temperature", type=float, default=DEFAULT_TOOL_TEMPERATURE
    )
    parser.add_argument("--tool-top-p", type=float, default=DEFAULT_TOOL_TOP_P)
    parser.add_argument("--tool-top-k", type=int, default=DEFAULT_TOOL_TOP_K)
    parser.add_argument("--tool-min-p", type=float, default=DEFAULT_TOOL_MIN_P)
    parser.add_argument(
        "--tool-reasoning-effort", default=DEFAULT_TOOL_REASONING_EFFORT
    )
    parser.add_argument("--enable-thinking", choices=["true", "false"], default="false")
    parser.add_argument(
        "--preserve-thinking", choices=["true", "false"], default="false"
    )
    parser.add_argument(
        "--upstream-timeout-seconds", type=int, default=DEFAULT_UPSTREAM_TIMEOUT_SECONDS
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    ProxyHandler.target_base = args.target_base.rstrip("/")
    ProxyHandler.log_path = Path(args.log_path)
    ProxyHandler.system_prompt_file = args.system_prompt_file
    ProxyHandler.native_tools = bool(args.native_tools)
    ProxyHandler.max_output_tokens = max(1, int(args.max_output_tokens))
    ProxyHandler.context_limit_tokens = max(0, int(args.context_limit_tokens))
    ProxyHandler.max_forward_body_bytes = max(4096, int(args.max_forward_body_bytes))
    ProxyHandler.tool_temperature = max(0.0, float(args.tool_temperature))
    ProxyHandler.tool_top_p = (
        None if args.tool_top_p is None else max(0.0, float(args.tool_top_p))
    )
    ProxyHandler.tool_top_k = (
        None if args.tool_top_k is None else max(0, int(args.tool_top_k))
    )
    ProxyHandler.tool_min_p = (
        None if args.tool_min_p is None else max(0.0, float(args.tool_min_p))
    )
    ProxyHandler.tool_reasoning_effort = str(args.tool_reasoning_effort or "").strip()
    ProxyHandler.enable_thinking = args.enable_thinking == "true"
    ProxyHandler.preserve_thinking = args.preserve_thinking == "true"
    ProxyHandler.upstream_timeout_seconds = max(10, int(args.upstream_timeout_seconds))
    local_base = f"http://{args.listen_host}:{args.listen_port}"
    remote_ok, remote_note = probe_models(ProxyHandler.target_base)
    if not remote_ok:
        raise SystemExit(
            f"target chat-completions endpoint is not healthy at {ProxyHandler.target_base}: {remote_note}\n"
            f"Check the remote server first, for example: curl -sS {ProxyHandler.target_base}/v1/models"
        )
    try:
        server = ThreadingHTTPServer((args.listen_host, args.listen_port), ProxyHandler)
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE and listener_is_bound(args.listen_host, args.listen_port):
            local_ok, local_note = probe_bridge_status(
                local_base,
                expected_target_base=ProxyHandler.target_base,
            )
            if local_ok:
                print(
                    f"Qwendex Responses bridge reusing existing healthy listener at {local_base} -> {ProxyHandler.target_base} ({local_note})",
                    flush=True,
                )
                return
            raise SystemExit(
                f"listen address {local_base} is already in use, but the existing listener is not this Qwendex Responses bridge: {local_note}\n"
                "Stop the conflicting listener or choose another port, then retry."
            ) from exc
        raise
    print(
        f"Qwendex Responses bridge listening on {local_base} -> {ProxyHandler.target_base} ({remote_note})",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
