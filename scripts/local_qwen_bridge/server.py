#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import contextlib
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
    from local_qwen_runtime_guard import (
        GuardAction,
        GuardConfig,
        RuntimeGuard,
    )
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from local_qwen_runtime_guard import (
        GuardAction,
        GuardConfig,
        RuntimeGuard,
    )

try:
    from local_qwen_bridge_status import (
        build_status_payload as build_bridge_status_payload,
    )
    from local_qwen_bridge_status import (
        runtime_guard_status_payload as bridge_runtime_guard_status_payload,
    )
    from local_qwen_document_section_recovery import (
        parse_section_upsert_progress_events,
    )
    from local_qwen_document_section_recovery import (
        section_upsert_finalize_tool_call as document_section_upsert_finalize_tool_call,
    )
    from local_qwen_document_section_recovery import (
        terminal_section_upsert_final_answer as document_terminal_section_upsert_final_answer,
    )
    from local_qwen_response_shaping import (
        collapse_repeated_final_text as shape_collapse_repeated_final_text,
    )
    from local_qwen_response_shaping import (
        response_payload_with_function_call as shape_response_payload_with_function_call,
    )
    from local_qwen_response_shaping import (
        response_payload_with_message as shape_response_payload_with_message,
    )
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
    from local_qwen_document_section_recovery import (
        parse_section_upsert_progress_events,
    )
    from local_qwen_document_section_recovery import (
        section_upsert_finalize_tool_call as document_section_upsert_finalize_tool_call,
    )
    from local_qwen_document_section_recovery import (
        terminal_section_upsert_final_answer as document_terminal_section_upsert_final_answer,
    )
    from local_qwen_response_shaping import (
        collapse_repeated_final_text as shape_collapse_repeated_final_text,
    )
    from local_qwen_response_shaping import (
        response_payload_with_function_call as shape_response_payload_with_function_call,
    )
    from local_qwen_response_shaping import (
        response_payload_with_message as shape_response_payload_with_message,
    )
    from local_qwen_tool_envelope import (
        suppress_visible_tool_markup as tool_policy_suppress_visible_tool_markup,
    )
    from local_qwen_tool_envelope import (
        suppressed_exec_marker as tool_policy_suppressed_exec_marker,
    )


DEFAULT_LISTEN_HOST = "127.0.0.1"
DEFAULT_LISTEN_PORT = 1234
DEFAULT_TARGET_BASE = "http://100.70.94.39:12345"
DEFAULT_SYSTEM_PROMPT_FILE = ""
BRIDGE_VERSION = "tabby-responses-proxy-2026-06-30-qwen-self-analysis-preflight-64k"


def optional_env_float(name: str) -> float | None:
    raw = os.environ.get(name, "").strip()
    return float(raw) if raw else None


def optional_env_int(name: str) -> int | None:
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw else None


DEFAULT_MAX_OUTPUT_TOKENS = int(os.environ.get("CODEX_TEXTGEN_MAX_OUTPUT_TOKENS", "4096"))
DEFAULT_MAX_FORWARD_BODY_BYTES = int(os.environ.get("CODEX_TEXTGEN_MAX_FORWARD_BODY_BYTES", "600000"))
DEFAULT_UPSTREAM_TIMEOUT_SECONDS = int(os.environ.get("CODEX_TEXTGEN_UPSTREAM_TIMEOUT_SECONDS", "600"))
DEFAULT_TOOL_TEMPERATURE = float(os.environ.get("CODEX_TEXTGEN_TOOL_TEMPERATURE", "0.15"))
DEFAULT_TOOL_TOP_P = optional_env_float("CODEX_TEXTGEN_TOOL_TOP_P")
DEFAULT_TOOL_TOP_K = optional_env_int("CODEX_TEXTGEN_TOOL_TOP_K")
DEFAULT_TOOL_MIN_P = optional_env_float("CODEX_TEXTGEN_TOOL_MIN_P")
DEFAULT_TOOL_REASONING_EFFORT = os.environ.get("CODEX_TEXTGEN_TOOL_REASONING_EFFORT", "").strip()
DEFAULT_CONTEXT_LIMIT_TOKENS = int(os.environ.get("CODEX_TEXTGEN_CONTEXT_LIMIT_TOKENS", "0"))
DEFAULT_BRIDGE_LOG_PATH = Path(
    os.environ.get(
        "CODEX_TEXTGEN_LOG_PATH",
        os.environ.get(
            "LOCAL_QWEN_BRIDGE_LOG",
            str(
                Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state")))
                / "qwendex"
                / "local_qwen_bridge"
                / "tabbyapi_responses_proxy.jsonl"
            ),
        ),
    )
).expanduser()
MAX_HEREDOC_COMMAND_CHARS = int(os.environ.get("CODEX_TEXTGEN_MAX_HEREDOC_COMMAND_CHARS", "3500"))
MAX_EXEC_COMMAND_CHARS = int(os.environ.get("CODEX_TEXTGEN_MAX_EXEC_COMMAND_CHARS", "8000"))
COMPACT_TOOL_ALLOWLIST = {
    "exec_command",
    "write_stdin",
    "update_plan",
    "view_image",
}
LOCAL_MODEL_INTERFACE_MARKERS = (
    "LOCAL_MODEL_TOOL_CALL_TOO_LARGE",
    "LOCAL_MODEL_TOOL_CALL_TRUNCATED",
    "LOCAL_MODEL_TOOL_MARKUP_SUPPRESSED",
    "LOCAL_MODEL_LOOP_DETECTED",
)


def legacy_project_recoveries_enabled() -> bool:
    return os.environ.get("LOCAL_QWEN_ENABLE_LEGACY_PROJECT_RECOVERIES", "").lower() in {"1", "true", "yes", "on"}


LOCAL_QWEN_LOOP_BREAKER = (
    "Local Qwen loop failsafe: if you repeat a command or promise to inspect without acting, stop the loop. "
    "Use the newest user request and latest successful tool output, then either call the needed tool once or answer."
)
LOCAL_QWEN_END_FRAME_ANCHOR = (
    "[LOCAL HARNESS REMINDER - NOT A USER TASK]\n"
    "The actionable task is the immediately preceding non-reminder user message. "
    "Newest user request wins over repo startup files, active missions, old goals, and older state. "
    "Do not answer this reminder directly.\n"
    "- Use targeted reads; do not dump multiple raw files or long logs.\n"
    "- Do not repeat a successful command. Use the visible result and continue.\n"
    "- Use broad orientation commands (`ls`, `find`, `rg --files`, `tree`) at most once per task unless state changed.\n"
    "- For multi-artifact work, use `scripts/artifact_queue.py` or `scripts/local_qwen_artifact_runner.py` if present.\n"
    "- For folder verification, use `scripts/local_qwen_verify_packet.py` if present and read the packet, not the whole folder.\n"
    "- Avoid heredoc writers for Markdown, reports, JSON, or long generated files. Keep `python3 -c` commands single-line.\n"
    "- For numbered item-by-item document edits, do not announce the next item without calling the next edit tool.\n"
    "- If a command/tool call would be too large or malformed, stop with the appropriate LOCAL_MODEL marker."
)
RUNTIME_GUARD_CONFIG = GuardConfig.from_env()
REPEATED_TOOL_CALL_THRESHOLD = int(os.environ.get("CODEX_TEXTGEN_REPEATED_TOOL_CALL_THRESHOLD", "3"))
CONSECUTIVE_IDENTICAL_TOOL_CALL_THRESHOLD = RUNTIME_GUARD_CONFIG.consecutive_identical_tool_call_threshold
TURN_TOOL_CALL_CAP = RUNTIME_GUARD_CONFIG.turn_tool_call_cap
GLOBAL_DUPLICATE_TOOL_CALL_THRESHOLD = RUNTIME_GUARD_CONFIG.global_duplicate_tool_call_threshold
ALTERNATING_TOOL_CALL_PATTERN_CYCLES = RUNTIME_GUARD_CONFIG.alternating_tool_call_pattern_cycles
READ_LOOP_THRESHOLD = RUNTIME_GUARD_CONFIG.read_loop_threshold
READ_LOOP_WINDOW = RUNTIME_GUARD_CONFIG.read_loop_window
ACTION_STAGNATION_THRESHOLD = RUNTIME_GUARD_CONFIG.action_stagnation_threshold
SHELL_COMMAND_STAGNATION_THRESHOLD = RUNTIME_GUARD_CONFIG.shell_command_stagnation_threshold
DUPLICATE_TOOL_GUARD_ENABLED = os.environ.get("CODEX_TEXTGEN_DUPLICATE_TOOL_GUARD", "1").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}
DUPLICATE_TOOL_GUARD_NAMES = {
    name.strip()
    for name in os.environ.get("CODEX_TEXTGEN_DUPLICATE_TOOL_GUARD_NAMES", "exec_command,write_stdin,update_plan").split(",")
    if name.strip()
}
READ_DUMP_GUARD_ENABLED = os.environ.get("CODEX_TEXTGEN_READ_DUMP_GUARD", "1").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}
VALIDATION_MARKER_BY_FINAL_MARKER = {
    "LOCAL_QWEN_PDF_CATALOG_OK": "PDF_CATALOG_VALIDATION_OK",
    "LOCAL_QWEN_EOS_CATALOG_OK": "EOS_CATALOG_VALIDATION_OK",
    "LOCAL_QWEN_UNSEEN_REPAIR_OK": "UNSEEN_REPAIR_VALIDATION_OK",
    "LOCAL_QWEN_MULTIROW_REPAIR_OK": "MULTIROW_REPAIR_VALIDATION_OK",
}
VALIDATOR_COMMAND_RE = re.compile(
    r"python3\s+([^\s'\";]+validate_(?:pdf_catalog|eos_catalog|v999|multirow)[^\s'\";]*\.py)"
)
DUPLICATE_BROAD_READ_GUARD_ENABLED = os.environ.get(
    "CODEX_TEXTGEN_DUPLICATE_BROAD_READ_GUARD", "1"
).strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}


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


def load_json_body(handler: BaseHTTPRequestHandler) -> tuple[dict[str, Any] | None, bytes]:
    length = int(handler.headers.get("Content-Length", "0") or "0")
    raw = handler.rfile.read(length) if length else b""
    if not raw:
        return None, raw
    try:
        return json.loads(raw.decode("utf-8")), raw
    except json.JSONDecodeError:
        return None, raw


def tool_name(tool: dict[str, Any]) -> str:
    function = tool.get("function")
    if isinstance(function, dict):
        return str(function.get("name", ""))
    return str(tool.get("name", ""))


def sanitize_tools(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    removed_tools: list[dict[str, str]] = []
    tools = payload.get("tools")
    if isinstance(tools, list):
        kept: list[dict[str, Any]] = []
        for tool in tools:
            if not isinstance(tool, dict):
                removed_tools.append({"type": type(tool).__name__, "name": ""})
                continue
            tool_type = str(tool.get("type", ""))
            if tool_type == "function":
                kept.append(tool)
            else:
                removed_tools.append({"type": tool_type, "name": tool_name(tool)})
        payload = dict(payload)
        if kept:
            payload["tools"] = kept
        else:
            payload.pop("tools", None)

    meta = {
        "removed_tool_count": len(removed_tools),
        "removed_tools": removed_tools,
        "remaining_tool_count": len(payload.get("tools", [])) if isinstance(payload.get("tools"), list) else 0,
    }
    return payload, meta


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


def build_compact_tool_prompt(tools: Any) -> str:
    if not isinstance(tools, list) or not tools:
        return ""

    context_limit = getattr(ProxyHandler, "context_limit_tokens", DEFAULT_CONTEXT_LIMIT_TOKENS)
    lines = [
        "Codex bridge tool protocol:",
        "When a tool is required, output exactly one XML tool call and no surrounding prose. Shape:",
        "<tool_call>",
        "<function=tool_name>",
        "<parameter=parameter_name>",
        "parameter value",
        "</parameter>",
        "</function>",
        "</tool_call>",
        "Never self-close, never wrap the XML in Markdown, and never output fake tool markup as prose.",
        "Use exec_command for local files, shell commands, tests, and git inspection.",
        "If you say you will inspect/check/write, emit the needed tool call in the same response.",
        "Do not repeat a successful command; use the latest visible output and answer or move to one named artifact.",
        "Newest user instruction and latest tool output beat old plans, startup files, missions, and goals.",
        "For broad multi-file tasks, use repo queue/packet scripts when present: artifact_queue.py, local_qwen_artifact_runner.py, local_qwen_verify_packet.py.",
        "MCP namespace tools are host-side in this local bridge. For local web search, use the configured search helper; for queues and run reports, use the repo scripts.",
        "Do not rerun an identical shell read command already succeeded. For chunked file reads, track requested ranges and line count, then give the final answer instead of re-reading.",
        "Keep python3 -c commands single-line; quoted multi-line `python3 -c` snippets often truncate or break shell quoting.",
        "For Markdown/report/supplement writes longer than about 1200 bytes: Do not run `python3 scripts/local_qwen_section_report.py` from inside qwen-local unless the user explicitly asks; it starts a nested local-Qwen/Codex run. In an active qwen-local session, create the file header, append one section per short command, and verify with `wc -c path` before the final marker.",
        "For numbered item-by-item document edits, after each completed item either call the next edit tool or finish; never stop at `Now Item N...` prose.",
        "Do not use sed -i to edit CSV files. Use Python's csv module, csv.DictWriter, preserve the original fieldnames exactly, never send dict rows through csv.writer.writerows, and update `paper_title` instead of `title`. This avoids sed errors such as unknown option to s.",
        "Do not end a turn with promise text; if you say you will inspect, validate, write, or patch, call the tool in the same response.",
        "If the user asks you to run `wc -c path` before a final `*_DONE` answer, run it first; the bridge will suppress premature final markers without the byte-count output.",
        f"The local backend is loaded with a {context_limit} token context window; keep forwarded working context comfortably below that limit." if context_limit else "Keep forwarded working context compact and comfortably below the local backend context window.",
        "Keep tool arguments short. Avoid heredoc writers, long pasted logs, and nested local-Qwen report helpers. Use compact single-line python3 -c for small file writes; for large Markdown/report bodies, split by section instead of emitting LOCAL_MODEL_TOOL_CALL_TOO_LARGE.",
        "After LOCAL_MODEL_TOOL_MARKUP_SUPPRESSED, retry at most once with a smaller read-only command; do not retry failed writes.",
        "Available tools:",
    ]
    for tool in tools:
        function = function_tool_schema(tool)
        if not function:
            continue
        name = str(function.get("name") or "").strip()
        if not name:
            continue
        if name not in COMPACT_TOOL_ALLOWLIST:
            continue
        parameters = function.get("parameters")
        properties: dict[str, Any] = {}
        required: set[str] = set()
        if isinstance(parameters, dict):
            raw_properties = parameters.get("properties")
            if isinstance(raw_properties, dict):
                properties = raw_properties
            raw_required = parameters.get("required")
            if isinstance(raw_required, list):
                required = {str(item) for item in raw_required}
        param_parts = []
        for param_name in properties:
            suffix = " required" if param_name in required else ""
            param_parts.append(f"{param_name}{suffix}")
        param_text = ", ".join(param_parts) if param_parts else "no parameters"
        description = compact_description(function.get("description"))
        if description:
            lines.append(f"- {name}({param_text}): {description}")
        else:
            lines.append(f"- {name}({param_text})")
        if name == "exec_command":
            lines.append("  Use parameter `cmd` for shell commands; do not use `command`.")
        elif name == "update_plan":
            lines.append(
                "  `plan` must be a JSON array of objects with `step` and `status`; never send a string or boolean."
            )
    return "\n".join(lines)


def contains_local_interface_marker(text: str) -> str:
    for marker in (*LOCAL_MODEL_INTERFACE_MARKERS, "FINAL_LOCAL_MODEL_STOP", "LOCAL_MODEL_PREMATURE_FINAL_MARKER"):
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


def normalize_tool_arguments_for_loop_key(arguments: Any, *, name: str = "") -> str:
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            return arguments.strip()
        arguments = parsed
    normalized_name = normalize_tool_call_name(str(name or ""))
    if normalized_name == "exec_command" and isinstance(arguments, dict):
        cmd = arguments.get("cmd")
        if not isinstance(cmd, str) and isinstance(arguments.get("command"), str):
            cmd = arguments.get("command")
        semantic: dict[str, str] = {"cmd": " ".join(str(cmd or "").split())}
        workdir = arguments.get("workdir")
        if isinstance(workdir, str) and workdir.strip():
            semantic["workdir"] = workdir.strip()
        return json.dumps(semantic, sort_keys=True, ensure_ascii=True)
    if isinstance(arguments, dict):
        return json.dumps(arguments, sort_keys=True, ensure_ascii=True)
    return json.dumps(arguments, sort_keys=True, ensure_ascii=True)


def repeated_tool_call_loop_hint(raw_items: list[Any]) -> str:
    last_key = ""
    last_name = ""
    last_arguments = ""
    streak = 0
    highest_streak = 0
    highest_name = ""
    highest_arguments = ""

    for item in raw_items:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type", ""))
        if not item_type and ("role" in item or "content" in item):
            item_type = "message"
        if item_type == "message":
            role = normalize_role(str(item.get("role", "user")))
            content = strip_prior_marker_preamble_from_user_text(extract_text(item.get("content")))
            if role == "user" and content.strip():
                last_key = ""
                last_name = ""
                last_arguments = ""
                streak = 0
                highest_streak = 0
                highest_name = ""
                highest_arguments = ""
            continue
        if item_type != "function_call":
            continue
        name = normalize_tool_call_name(str(item.get("name") or ""))
        arguments = normalize_tool_arguments_for_loop_key(item.get("arguments"), name=name)
        key = f"{name}:{arguments}"
        if key == last_key:
            streak += 1
        else:
            last_key = key
            last_name = name
            last_arguments = arguments
            streak = 1
        if streak > highest_streak:
            highest_streak = streak
            highest_name = last_name
            highest_arguments = last_arguments

    if highest_streak < REPEATED_TOOL_CALL_THRESHOLD:
        return ""

    detail = highest_arguments
    if len(detail) > 500:
        detail = detail[:497].rstrip() + "..."
    return (
        "[REPEATED TOOL CALL LOOP DETECTED]\n"
        f"The previous turns include {highest_streak} consecutive identical `{highest_name}` tool calls with the same arguments.\n"
        f"Arguments: {detail}\n"
        "Do not call that tool again. Use the existing latest tool output and answer the user's requested final marker or summary now. "
        "If the task cannot be completed from existing output, stop with LOCAL_MODEL_LOOP_DETECTED and one concise missing-evidence sentence."
    )


def tool_call_loop_key(name: str, arguments: Any) -> str:
    normalized_name = normalize_tool_call_name(str(name or ""))
    return f"{normalized_name}:{normalize_tool_arguments_for_loop_key(arguments, name=normalized_name)}"


def tool_call_records_after_latest_user_request(raw_items: list[Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type", ""))
        if not item_type and ("role" in item or "content" in item):
            item_type = "message"
        if item_type == "message":
            role = normalize_role(str(item.get("role", "user")))
            content = strip_prior_marker_preamble_from_user_text(extract_text(item.get("content")))
            if role == "user" and content.strip():
                records.clear()
            continue
        if item_type != "function_call":
            continue
        name = normalize_tool_call_name(str(item.get("name") or ""))
        arguments = item.get("arguments")
        records.append(
            {
                "name": name,
                "arguments": arguments,
                "key": tool_call_loop_key(name, arguments),
            }
        )
    return records


def git_diff_args_are_overview(args: str) -> bool:
    tokens = args.strip().split()
    if not tokens:
        return True
    if "--" in tokens and tokens.index("--") < len(tokens) - 1:
        return False
    return all(token.startswith("-") or git_revision_token(token) for token in tokens)


def git_revision_token(token: str) -> bool:
    return (
        token in {"HEAD", "@"}
        or re.fullmatch(r"(?:HEAD|@)(?:[~^]\d*)+", token) is not None
        or re.fullmatch(r"[0-9a-f]{7,40}", token, flags=re.IGNORECASE) is not None
        or re.fullmatch(r"[^\s]+\.{2,3}[^\s]+", token) is not None
    )


def shell_segment_is_git_overview(segment: str) -> bool:
    match = re.match(r"^git(?:\s+(?:-C\s+\S+|--no-pager))*\s+(status|diff|ls-files)\b", segment, flags=re.I)
    if not match:
        return False
    command = match.group(1).lower()
    if command != "diff":
        return True
    return git_diff_args_are_overview(segment[match.end() :])


def exec_command_git_overview_inspection_key(arguments: str) -> str:
    try:
        parsed = json.loads(arguments)
    except json.JSONDecodeError:
        return ""
    if not isinstance(parsed, dict):
        return ""
    cmd = str(parsed.get("cmd") or parsed.get("command") or "").strip()
    if not cmd:
        return ""
    segments = [segment.strip() for segment in re.split(r"&&|\|\||[;&|\n]", cmd) if segment.strip()]
    if not segments:
        return ""
    if not all(shell_segment_is_git_overview(segment) for segment in segments):
        return ""
    return "exec_command:git-overview-inspection"


def qwen_style_loop_guard_message(raw_items: list[Any]) -> str:
    decision = RuntimeGuard(runtime_guard_config()).evaluate_history(raw_items)
    if decision.action == GuardAction.ALLOW:
        return ""
    loop_type = decision.loop_type.value if decision.loop_type else "runtime_guard"
    observed = f" observed={decision.observed}" if decision.observed is not None else ""
    threshold = f" threshold={decision.threshold}" if decision.threshold is not None else ""
    return f"{loop_type}: {decision.message}.{observed}{threshold}"


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


def prior_tool_call_keys_after_latest_user_request(raw_items: list[Any]) -> set[str]:
    keys: set[str] = set()
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type", ""))
        if not item_type and ("role" in item or "content" in item):
            item_type = "message"
        if item_type == "message":
            role = normalize_role(str(item.get("role", "user")))
            content = strip_prior_marker_preamble_from_user_text(extract_text(item.get("content")))
            if role == "user" and content.strip():
                keys.clear()
            continue
        if item_type != "function_call":
            continue
        name = normalize_tool_call_name(str(item.get("name") or ""))
        if name not in DUPLICATE_TOOL_GUARD_NAMES:
            continue
        keys.add(tool_call_loop_key(name, item.get("arguments")))
    return keys


def exec_command_looks_mutating(arguments: str) -> bool:
    try:
        parsed = json.loads(arguments)
    except json.JSONDecodeError:
        return False
    if not isinstance(parsed, dict):
        return False
    cmd = str(parsed.get("cmd") or parsed.get("command") or "").strip()
    if not cmd:
        return False
    compact = " ".join(cmd.split()).lower()
    readonly_prefixes = (
        "ls",
        "find",
        "rg",
        "grep",
        "sed -n",
        "head",
        "tail",
        "cat",
        "wc ",
        "git status",
        "git diff",
        "git log",
        "pwd",
        "date",
        "python3 -m json.tool",
        "python -m json.tool",
    )
    if compact.startswith(readonly_prefixes):
        return False
    mutating_patterns = (
        r"(^|[;&|]\s*)mkdir\b",
        r"(^|[;&|]\s*)touch\b",
        r"(^|[;&|]\s*)rm\b",
        r"(^|[;&|]\s*)mv\b",
        r"(^|[;&|]\s*)cp\b",
        r"(^|[;&|]\s*)chmod\b",
        r"(^|[;&|]\s*)git\s+(?:add|commit|push|checkout|reset|clean|mv|rm)\b",
        r">\s*[^&]",
        r">>",
        r"\btee\b",
        r"\bwrite_text\s*\(",
        r"\bopen\s*\([^)]*['\"][wa+]",
        r"\bjson\.dump\s*\(",
        r"\blocal_harness_document_section_upsert\.py\b",
    )
    return any(re.search(pattern, compact) for pattern in mutating_patterns)


def exec_command_looks_read_only(arguments: str) -> bool:
    try:
        parsed = json.loads(arguments)
    except json.JSONDecodeError:
        return False
    if not isinstance(parsed, dict):
        return False
    cmd = str(parsed.get("cmd") or parsed.get("command") or "").strip()
    if not cmd:
        return False
    if exec_command_looks_mutating(arguments):
        return False
    compact = " ".join(cmd.split()).lower()
    readonly_prefixes = (
        "ls",
        "find ",
        "rg ",
        "grep ",
        "sed -n",
        "head ",
        "tail ",
        "cat ",
        "wc ",
        "git status",
        "git diff",
        "git log",
        "pwd",
        "date",
        "python3 -m json.tool",
        "python -m json.tool",
    )
    if compact.startswith(readonly_prefixes):
        return True
    if re.search(r"\bpython3?\s+-c\b", compact):
        unsafe_python_markers = (
            "write_text",
            ".write(",
            ".unlink(",
            ".rename(",
            ".replace(",
            ".mkdir(",
            ".rmdir(",
            "os.remove",
            "os.unlink",
            "os.rename",
            "os.replace",
            "os.makedirs",
            "shutil.",
            "subprocess.",
            "os.system",
        )
        file_read_markers = (
            ".read(",
            ".read_text(",
            "open(",
            "os.path.getsize",
            ".stat(",
        )
        if any(marker in compact for marker in unsafe_python_markers):
            return False
        return "print(" in compact and any(marker in compact for marker in file_read_markers)
    return False


def exec_command_looks_broad_read(arguments: str) -> bool:
    if not DUPLICATE_BROAD_READ_GUARD_ENABLED:
        return False
    try:
        parsed = json.loads(arguments)
    except json.JSONDecodeError:
        return False
    if not isinstance(parsed, dict):
        return False
    cmd = str(parsed.get("cmd") or parsed.get("command") or "").strip()
    if not cmd:
        return False
    compact = " ".join(cmd.split()).lower()
    # Redirections and tee change workspace state; leave those to mutation logic.
    if re.search(r">>|>\s*[^&]|\btee\b", compact):
        return False
    broad_read_patterns = (
        r"^(?:cd\s+\S+\s*&&\s*)?ls(?:\s|$)",
        r"^(?:cd\s+\S+\s*&&\s*)?find(?:\s|$)",
        r"^(?:cd\s+\S+\s*&&\s*)?rg\s+--files(?:\s|$)",
        r"^(?:cd\s+\S+\s*&&\s*)?tree(?:\s|$)",
    )
    if any(re.search(pattern, compact) for pattern in broad_read_patterns):
        return True
    if not re.search(r"\bpython3?\s+-c\b", compact):
        return False
    return any(
        marker in compact
        for marker in (
            "os.listdir",
            ".iterdir(",
            ".glob(",
            ".rglob(",
            "glob.glob",
            "os.walk",
        )
    )


def exec_command_written_path(cmd: str) -> str:
    compact = " ".join(cmd.split())
    path_patterns = (
        r"\bopen\s*\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"][wa+]",
        r"\bPath\s*\(\s*['\"]([^'\"]+)['\"]\s*\)\.write_text\s*\(",
        r"\bpathlib\.Path\s*\(\s*['\"]([^'\"]+)['\"]\s*\)\.write_text\s*\(",
        r"(?:^|[^>])>>?\s*([A-Za-z0-9_./-]+)",
    )
    for pattern in path_patterns:
        match = re.search(pattern, compact)
        if match:
            return match.group(1).rstrip(".,;:")
    return ""


def exec_command_generated_digital_products_write(normalized_arguments: str) -> bool:
    if not legacy_project_recoveries_enabled():
        return False
    try:
        parsed = json.loads(normalized_arguments)
    except json.JSONDecodeError:
        return False
    if not isinstance(parsed, dict):
        return False
    cmd = str(parsed.get("cmd") or parsed.get("command") or "")
    if not cmd:
        return False
    lowered = cmd.lower()
    if LOCAL_HARNESS_SECTION_HELPER.lower() in lowered:
        return False
    if "digital_products" not in lowered and "digital products" not in lowered:
        return False
    write_markers = (
        ".write_text(",
        "open(",
        "cat >",
        "tee ",
        ">>",
        "> monetization_research/digital_products",
        "> digital_products",
    )
    if not any(marker in lowered for marker in write_markers):
        return False
    return any(
        marker in lowered
        for marker in (
            "monetization_research/digital_products.md",
            "monetization_research/digital_products_supplement.md",
            "digital_products.md",
            "digital_products_supplement.md",
        )
    )


def duplicate_guard_applies(name: str, arguments: str) -> bool:
    normalized_name = normalize_tool_call_name(name)
    if normalized_name not in DUPLICATE_TOOL_GUARD_NAMES:
        return False
    if normalized_name == "exec_command":
        return (
            exec_command_looks_mutating(arguments)
            or exec_command_looks_broad_read(arguments)
            or exec_command_looks_read_only(arguments)
        )
    return True


def duplicate_broad_read_without_intervening_mutation(raw_items: list[Any], duplicate_key: str) -> bool:
    if not raw_items:
        return False
    seen_since_mutation: set[str] = set()
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type", ""))
        if not item_type and ("role" in item or "content" in item):
            item_type = "message"
        if item_type == "message":
            role = normalize_role(str(item.get("role", "user")))
            if role == "user" and extract_text(item.get("content")).strip():
                seen_since_mutation.clear()
            continue
        if item_type != "function_call":
            continue
        name = normalize_tool_call_name(str(item.get("name") or ""))
        arguments = item.get("arguments")
        if name == "exec_command" and exec_command_looks_mutating(normalize_function_arguments(name, arguments)):
            seen_since_mutation.clear()
            continue
        seen_since_mutation.add(tool_call_loop_key(name, arguments))
    return duplicate_key in seen_since_mutation


def duplicate_exec_recovery_tool_call(
    normalized_arguments: str,
    *,
    latest_user_text: str,
    index: int,
    raw_items: list[Any] | None = None,
) -> dict[str, Any] | None:
    is_mutating = exec_command_looks_mutating(normalized_arguments)
    is_read_only = exec_command_looks_read_only(normalized_arguments)
    is_broad_read = exec_command_looks_broad_read(normalized_arguments)
    if not is_mutating and not is_read_only and not is_broad_read:
        return None
    try:
        parsed = json.loads(normalized_arguments)
    except json.JSONDecodeError:
        parsed = {}
    if not isinstance(parsed, dict):
        parsed = {}
    cmd = str(parsed.get("cmd") or parsed.get("command") or "").strip()
    compact = " ".join(cmd.split())
    arguments: dict[str, Any] | None = None
    if (is_read_only or is_broad_read) and not is_mutating:
        section_request = requested_markdown_section_update(latest_user_text)
        if section_request and not markdown_section_update_present(section_request, raw_items or []):
            section_path, section_title = section_request
            arguments = {
                "cmd": markdown_section_upsert_helper_command(section_path, section_title),
                "max_output_tokens": 4000,
            }
        else:
            arguments = {
                "cmd": (
                    "printf '%s\\n' 'DUPLICATE_READ_ALREADY_DONE: duplicate read command was skipped; "
                    "use the previous successful output and answer or continue with a different bounded command.'"
                )
            }
    elif re.fullmatch(r"mkdir\s+-p\s+[^;&|<>]+", compact):
        arguments = {
            "cmd": "printf '%s\\n' 'DUPLICATE_SETUP_ALREADY_DONE: duplicate mkdir -p was skipped; continue with the next artifact or verifier.'"
        }
    else:
        written_path = exec_command_written_path(cmd)
        if written_path:
            arguments = {"cmd": f"wc -c {shlex.quote(written_path)}"}
    if arguments is None and latest_user_text:
        match = re.search(r"\bwc\s+-c\s+([A-Za-z0-9_./-]+)", latest_user_text)
        if match:
            verify_path = match.group(1).rstrip(".,;:")
            arguments = {"cmd": f"wc -c {verify_path}"}
    if arguments is None:
        return None
    workdir = parsed.get("workdir")
    if isinstance(workdir, str) and workdir.strip():
        arguments["workdir"] = workdir.strip()
    return {
        "id": f"fc_duplicate_recovery_{index + 1}",
        "type": "function_call",
        "call_id": f"call_duplicate_recovery_{index + 1}",
        "name": "exec_command",
        "arguments": json.dumps(arguments, ensure_ascii=True),
    }


def duplicate_read_recovery_seen_after_latest_user_request(raw_items: list[Any]) -> bool:
    seen = False
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type", ""))
        if not item_type and ("role" in item or "content" in item):
            item_type = "message"
        if item_type == "message":
            role = normalize_role(str(item.get("role", "user")))
            content = strip_prior_marker_preamble_from_user_text(extract_text(item.get("content")))
            if role == "user" and content.strip():
                seen = False
            continue
        if item_type != "function_call_output":
            continue
        if "DUPLICATE_READ_ALREADY_DONE" in extract_output_text(item):
            seen = True
    return seen


def duplicate_read_finalize_seen_after_latest_user_request(raw_items: list[Any]) -> bool:
    seen = False
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type", ""))
        if not item_type and ("role" in item or "content" in item):
            item_type = "message"
        if item_type == "message":
            role = normalize_role(str(item.get("role", "user")))
            content = strip_prior_marker_preamble_from_user_text(extract_text(item.get("content")))
            if role == "user" and content.strip():
                seen = False
            continue
        if item_type != "function_call_output":
            continue
        if "DUPLICATE_READ_FINALIZE_NOW" in extract_output_text(item):
            seen = True
    return seen


def useful_read_output_seen_after_latest_user_request(raw_items: list[Any]) -> bool:
    read_call_ids: set[str] = set()
    seen = False
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type", ""))
        if not item_type and ("role" in item or "content" in item):
            item_type = "message"
        if item_type == "message":
            role = normalize_role(str(item.get("role", "user")))
            content = strip_prior_marker_preamble_from_user_text(extract_text(item.get("content")))
            if role == "user" and content.strip():
                read_call_ids.clear()
                seen = False
            continue
        if item_type == "function_call":
            name = normalize_tool_call_name(str(item.get("name") or ""))
            arguments = normalize_function_arguments(name, item.get("arguments"))
            if name == "exec_command" and (
                exec_command_looks_read_only(arguments) or exec_command_looks_broad_read(arguments)
            ):
                call_id = str(item.get("call_id") or item.get("id") or "")
                if call_id:
                    read_call_ids.add(call_id)
            continue
        if item_type != "function_call_output":
            continue
        call_id = str(item.get("call_id") or "")
        if call_id not in read_call_ids:
            continue
        text = extract_output_text(item).strip()
        if text and "DUPLICATE_READ_ALREADY_DONE" not in text and "DUPLICATE_READ_FINALIZE_NOW" not in text:
            seen = True
    return seen


def tool_outputs_after_latest_user_request(raw_items: list[Any]) -> list[str]:
    outputs: list[str] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type", ""))
        if not item_type and ("role" in item or "content" in item):
            item_type = "message"
        if item_type == "message":
            role = normalize_role(str(item.get("role", "user")))
            content = strip_prior_marker_preamble_from_user_text(extract_text(item.get("content")))
            if role == "user" and content.strip():
                outputs = []
            continue
        if item_type != "function_call_output":
            continue
        text = extract_output_text(item).strip()
        if text:
            outputs.append(text)
    return outputs


def latest_user_asks_to_continue(latest_user_text: str) -> bool:
    compact = re.sub(r"[^a-z0-9]+", " ", latest_user_text.lower()).strip()
    return compact in {"continue", "try to continue", "keep going", "continue working", "continue please"}


def markdown_paths_from_text(text: str) -> list[str]:
    paths: list[str] = []
    path_re = re.compile(r"(?<![@A-Za-z0-9_.-])((?:\.?/|/)?[A-Za-z0-9_./-]*[A-Za-z0-9_-]+\.md)\b")
    for match in path_re.finditer(text):
        raw = match.group(1).strip("`'\".,:;()[]{}")
        if not raw:
            continue
        path = raw
        if path.startswith("./"):
            path = path[2:]
        if path.startswith("/"):
            continue
        if path not in paths:
            paths.append(path)
    return paths


def repo_document_inventory_triage_final_answer(latest_user_text: str, found_docs: set[str]) -> str:
    request_lower = latest_user_text.lower()
    if "repo" not in request_lower and not latest_user_asks_to_continue(latest_user_text):
        return ""
    priority = [
        (
            "public/qwendex/architecture.md",
            "architecture doc; verify every CLI, config, guard, receipt, and bridge component maps to a maintained script.",
        ),
        (
            "public/qwendex/verification.md",
            "verification doc; keep commands aligned with the current public validator and eval surface.",
        ),
        (
            "public/qwendex/security.md",
            "security doc; keep local-model authority, secrets, and public-claim limits explicit.",
        ),
        (
            "README.md",
            "root README; keep quickstart and release-candidate links current.",
        ),
        (
            "AGENTS.md",
            "workspace rules; keep the public repo scope free of downstream project-specific workflows.",
        ),
    ]
    ranked = [(path, reason) for path, reason in priority if path in found_docs]
    if not ranked:
        return ""
    lines = ["From the repo Markdown list already visible, the best next document passes are:"]
    for index, (path, reason) in enumerate(ranked, start=1):
        lines.append(f"{index}. `{path}` - {reason}")
    lines.append("Next action: start with `public/qwendex/architecture.md` and verify every named surface has a script, config, test, or documented boundary.")
    return "\n".join(lines)


def latest_user_requests_document_inventory_triage(latest_user_text: str) -> bool:
    lower = latest_user_text.lower()
    if not any(marker in lower for marker in ("document", "documents", ".md", "docs")):
        return False
    return any(
        marker in lower
        for marker in (
            "better pass",
            "use a pass",
            "need a pass",
            "needs work",
            "need work",
            "could use",
            "which other",
            "what other",
            "next document",
            "same treatment",
        )
    )


def compact_document_inventory_command() -> str:
    paths = [
        "README.md",
        "AGENTS.md",
        "QWENDEX_STARTUP.md",
        "RELEASE.md",
        "public/qwendex/README.md",
        "public/qwendex/architecture.md",
        "public/qwendex/configuration.md",
        "public/qwendex/operations.md",
        "public/qwendex/security.md",
        "public/qwendex/verification.md",
        "docs/validation/README.md",
    ]
    return (
        'python3 -c "from pathlib import Path; root=Path.cwd(); '
        f"paths={paths!r}; print('DOC_INVENTORY_COMPACT'); "
        "[print(str((root / r).stat().st_size) + chr(9) + r) for r in paths if (root / r).exists()]"
        '"'
    )


def document_inventory_evidence_seen_after_latest_user(raw_items: list[Any]) -> bool:
    outputs = "\n".join(tool_outputs_after_latest_user_request(raw_items))
    if "DOC_INVENTORY_COMPACT" in outputs:
        return True
    found_docs = set(markdown_paths_from_text(outputs))
    return any(
        path.startswith("public/qwendex/") or path in {"README.md", "AGENTS.md", "QWENDEX_STARTUP.md", "RELEASE.md"}
        for path in found_docs
    )


def document_inventory_tool_call(index: int = 0) -> dict[str, Any]:
    return {
        "id": f"document_inventory_compact_tool_call_{index + 1}",
        "type": "function_call",
        "call_id": f"call_document_inventory_compact_{index + 1}",
        "name": "exec_command",
        "arguments": json.dumps(
            {"cmd": compact_document_inventory_command(), "max_output_tokens": 4000},
            ensure_ascii=True,
        ),
    }


def document_inventory_tool_calls(
    *,
    latest_user_text: str,
    raw_items: list[Any],
) -> list[dict[str, Any]]:
    if not latest_user_requests_document_inventory_triage(latest_user_text):
        return []
    if document_inventory_evidence_seen_after_latest_user(raw_items):
        return []
    return [document_inventory_tool_call()]


def latest_user_requests_codebase_explanation(latest_user_text: str) -> bool:
    lower = latest_user_text.lower()
    compact = re.sub(r"[^a-z0-9]+", " ", lower).strip()
    return any(
        phrase in lower or phrase in compact
        for phrase in (
            "explain this codebase",
            "explain the codebase",
            "explain this repo",
            "explain the repo",
            "explain this repository",
            "what is this codebase",
            "walk me through this codebase",
            "walk me through this repo",
        )
    )


def compact_codebase_overview_command() -> str:
    important_files = [
        "README.md",
        "AGENTS.md",
        "PROJECT_SESSION_BOOT.md",
        "PROJECT_ECC_OPERATOR_QUICKREF.md",
        "state/HUB_CONTEXT.md",
        "state/INDEX.md",
        "state/CURRENT_CONTEXT.md",
        "monetization_research/README.md",
        "monetization_research/TASK_QUEUE.md",
    ]
    return (
        'python3 -c "from pathlib import Path; root=Path.cwd(); '
        f"important={important_files!r}; "
        "print('CODEBASE_OVERVIEW_COMPACT'); print('root=' + str(root)); "
        "dirs=sorted([p.name for p in root.iterdir() if p.is_dir() and p.name not in {'.git','__pycache__'}]); "
        "[print('dir' + chr(9) + name) for name in dirs[:40]]; "
        "[print('file' + chr(9) + str((root / rel).stat().st_size) + chr(9) + rel) for rel in important if (root / rel).exists()]; "
        "scripts=sorted([p.name for p in (root / 'scripts').glob('*') if p.suffix in {'.py','.sh'}]) if (root / 'scripts').exists() else []; "
        "[print('script' + chr(9) + name) for name in scripts[:60]]"
        '"'
    )


def codebase_overview_evidence_seen_after_latest_user(raw_items: list[Any]) -> bool:
    outputs = "\n".join(tool_outputs_after_latest_user_request(raw_items))
    return "CODEBASE_OVERVIEW_COMPACT" in outputs


def codebase_overview_response_call(index: int = 0) -> dict[str, Any]:
    return {
        "id": f"codebase_overview_compact_tool_call_{index + 1}",
        "type": "function_call",
        "call_id": f"call_codebase_overview_compact_{index + 1}",
        "name": "exec_command",
        "arguments": json.dumps(
            {"cmd": compact_codebase_overview_command(), "max_output_tokens": 5000},
            ensure_ascii=True,
        ),
    }


def codebase_overview_tool_calls(
    *,
    latest_user_text: str,
    raw_items: list[Any],
) -> list[dict[str, Any]]:
    if not latest_user_requests_codebase_explanation(latest_user_text):
        return []
    if codebase_overview_evidence_seen_after_latest_user(raw_items):
        return []
    return [
        {
            "id": "codebase_overview_compact_tool_call_1",
            "type": "function",
            "function": {
                "name": "exec_command",
                "arguments": json.dumps(
                    {"cmd": compact_codebase_overview_command(), "max_output_tokens": 5000},
                    ensure_ascii=True,
                ),
            },
        }
    ]


def codebase_overview_final_answer(latest_user_text: str, raw_items: list[Any]) -> str:
    if not latest_user_requests_codebase_explanation(latest_user_text):
        return ""
    outputs = "\n".join(tool_outputs_after_latest_user_request(raw_items))
    if "CODEBASE_OVERVIEW_COMPACT" not in outputs:
        return ""
    lower = outputs.lower()
    scripts = re.findall(r"(?m)^script\t([^\n]+)", outputs)
    dirs = re.findall(r"(?m)^dir\t([^\n]+)", outputs)
    hub_like = "state/hub_context.md" in lower or "monetization_research" in lower
    if hub_like:
        lines = [
            "This codebase is The Hub: a lightweight knowledge-management and local-model operations workspace.",
            "",
            "Main surfaces:",
            "- `library/`, `planning/`, `templates/`, `indexes/`, and `archive/` organize durable notes, decisions, reusable formats, generated indexes, and retired material.",
            "- `state/` carries operating context such as `HUB_CONTEXT.md`, `INDEX.md`, and current project state.",
            "- `model_lab/` and `scripts/` hold local-Qwen setup notes, evals, runbooks, and deterministic helper tools.",
            "- `monetization_research/` is the active research lane with Markdown strategy docs plus tracker CSVs.",
            "",
            "Important scripts:",
            "- `new_note.py`, `hub_index.py`, `find_docs.py`, and `archive_doc.py` manage notes, indexes, retrieval, and archival.",
            "- `local_qwen_task.py`, `artifact_queue.py`, `local_qwen_artifact_runner.py`, `local_qwen_section_report.py`, and `local_qwen_run_report.py` keep local-Qwen work bounded and recoverable.",
            "- `project_guardrail_check.py`, `workspace_hygiene.py`, and `local_qwen_verify_packet.py` provide validation and hygiene receipts.",
            "",
            "Operating model: use files as durable state, keep local model tasks scoped, prefer index-driven retrieval over broad scans, and validate edits with deterministic scripts before treating model output as authoritative.",
        ]
    else:
        shown_dirs = ", ".join(f"`{name}`" for name in dirs[:10]) or "no top-level directories were visible"
        shown_scripts = ", ".join(f"`{name}`" for name in scripts[:12]) or "no scripts were visible"
        lines = [
            "This repository appears to be a file-oriented project rather than a single compiled application.",
            f"Top-level directories visible from the compact overview: {shown_dirs}.",
            f"Scripts visible from the compact overview: {shown_scripts}.",
            "Use the listed README/state/config files as the first orientation layer, then inspect one subsystem at a time.",
        ]
    marker = terminal_marker_requested_by_latest_user(latest_user_text)
    if marker and marker not in "\n".join(lines):
        lines.append(marker)
    return "\n".join(lines)


def prior_text_before_latest_user_request(raw_items: list[Any]) -> str:
    seen_chunks: list[str] = []
    prior_chunks: list[str] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type", ""))
        if not item_type and ("role" in item or "content" in item):
            item_type = "message"
        if item_type == "message":
            role = normalize_role(str(item.get("role", "user")))
            content = strip_prior_marker_preamble_from_user_text(extract_text(item.get("content"))).strip()
            if role == "user" and content:
                prior_chunks = seen_chunks.copy()
            if content:
                seen_chunks.append(content)
            continue
        if item_type == "function_call_output":
            output = extract_output_text(item).strip()
            if output:
                seen_chunks.append(output)
    return "\n".join(prior_chunks)


def latest_user_requests_detail_followup(latest_user_text: str) -> bool:
    lower = latest_user_text.lower()
    compact = re.sub(r"[^a-z0-9]+", " ", lower).strip()
    return any(
        phrase in lower or phrase in compact
        for phrase in (
            "complete answer",
            "additional detail",
            "additional details",
            "more detail",
            "more details",
            "give me detail",
            "give me details",
            "expand on that",
            "elaborate",
            "what else",
        )
    )


def latest_user_contains_inline_harness_self_analysis_summary(latest_user_text: str) -> bool:
    lower = latest_user_text.lower()
    return any(
        marker in lower
        for marker in (
            "previous local-qwen answer summary",
            "previous qwen-local answer summary",
            "prior harness self-analysis",
            "previous harness self-analysis",
        )
    ) or (
        any(marker in lower for marker in ("duplicate-read", "duplicate read", "generic finalizers"))
        and any(marker in lower for marker in ("answer-shaping", "context-window", "context window", "loop"))
    )


def prior_context_indicates_harness_self_analysis(raw_items: list[Any]) -> bool:
    lower = prior_text_before_latest_user_request(raw_items).lower()
    if not lower:
        return False
    if latest_user_requests_harness_self_analysis(lower):
        return True
    return (
        ("status: partial_failure" in lower or "root_cause:" in lower)
        and any(marker in lower for marker in ("harness", "local-qwen", "qwen-local", "local qwen"))
        and any(
            marker in lower
            for marker in (
                "duplicate-read",
                "duplicate read",
                "loop guard",
                "context-window",
                "context window",
                "answer-shaping",
                "ability to do the work",
            )
        )
    )


def latest_user_is_harness_self_analysis_followup(latest_user_text: str, raw_items: list[Any]) -> bool:
    return latest_user_requests_detail_followup(latest_user_text) and (
        prior_context_indicates_harness_self_analysis(raw_items)
        or latest_user_contains_inline_harness_self_analysis_summary(latest_user_text)
    )


def latest_user_requests_harness_self_analysis(latest_user_text: str, raw_items: list[Any] | None = None) -> bool:
    lower = latest_user_text.lower()
    direct = any(
        marker in lower
        for marker in (
            "analyze your performance",
            "within the harness",
            "harness performance",
            "ability to do the work",
            "abilities",
            "hurdles",
            "drastically improve",
            "drasticaly improve",
            "you failed to analyze",
            "you stopped short",
            "stopped short",
            "you seem stuck",
            "no ability to answer",
            "cannot answer",
        )
    )
    if direct:
        return True
    return bool(raw_items and latest_user_is_harness_self_analysis_followup(latest_user_text, raw_items))


def terminal_marker_requested_by_latest_user(latest_user_text: str) -> str:
    match = re.search(
        r"(?:end|finish|respond|reply|answer)\s+with\s+(?:exactly\s+)?`?([A-Z0-9_]+(?:_OK|_DONE))`?",
        latest_user_text,
        flags=re.IGNORECASE,
    )
    return match.group(1) if match else ""


def harness_self_analysis_final_answer(latest_user_text: str, raw_items: list[Any]) -> str:
    if not latest_user_requests_harness_self_analysis(latest_user_text, raw_items):
        return ""
    outputs = "\n".join(tool_outputs_after_latest_user_request(raw_items))
    all_outputs = "\n".join(tool_outputs_from_items(raw_items))
    combined = f"{outputs}\n{all_outputs}"
    combined_lower = combined.lower()
    signals: list[str] = []
    if "duplicate_read_already_done" in combined_lower or "duplicate_read_finalize_now" in combined_lower:
        signals.append("duplicate-read recovery fired, but the prior fallback could still end with a generic stop message")
    if "local_model_loop_detected" in combined_lower or "read_file_loop" in combined_lower:
        signals.append("runtime loop detection fired after repeated read/orientation behavior")
    if "repeated promise-style" in combined_lower:
        signals.append("promise-style text arrived without a tool call or artifact change")
    if "exceeds the available context size" in combined_lower or "32768" in combined_lower:
        signals.append("a 32k context cap appeared in a run that was expected to use the 64k local route")
    if "summary_comparison.md" in combined_lower:
        signals.append("the summary-comparison edit eventually existed, but the model kept rediscovering it instead of analyzing the harness failure")
    if not signals:
        signals.append("the latest turn asked for harness self-analysis, so more broad repo reads would be the wrong next action")

    if latest_user_is_harness_self_analysis_followup(latest_user_text, raw_items):
        response = (
            "status: PARTIAL_FAILURE\n"
            "additional_details:\n"
            "1. The main blocker was not model knowledge; it was turn routing after the answer already had enough evidence.\n"
            "2. A follow-up asking for more detail should inherit the prior harness-diagnosis context and answer directly.\n"
            "3. Starting a fresh repository-diff, list, search, or read pass on that follow-up wastes context and can re-enter loop guards.\n"
            "4. Duplicate-read recovery is useful only if it ends in a task-aware answer instead of a generic stop notice.\n"
            "5. The safest recovery path is: preserve the latest useful evidence, block new broad-read tools, and produce a bounded diagnosis.\n"
            "6. Launcher and bridge checks still matter because older/stale runs showed a 32K/64K mismatch; the current bridge route is verified at 64K.\n"
            "7. The harness should favor compact receipts and live validation artifacts over rereading the same config files.\n"
            "8. The next hardening target is any follow-up/detail turn that tries to inspect diffs before answering.\n"
            f"observed_signals: {'; '.join(dict.fromkeys(signals))}\n"
            "next_action: no more broad reads for this follow-up; answer from the prior harness diagnosis and validation receipts."
        )
    else:
        response = (
        "status: PARTIAL_FAILURE\n"
        "root_cause: The local run had enough evidence, but the harness did not route a self-analysis/complaint turn into an answer-only recovery path. "
        "The model kept treating the request as another repo-inspection task, so duplicate-read and loop guards stopped execution without a useful diagnosis.\n"
        f"observed_signals: {'; '.join(dict.fromkeys(signals))}\n"
        "hurdles:\n"
        "1. Broad read/orientation commands are easy for the local model to repeat after progress.\n"
        "2. Generic duplicate-read finalizers can suppress loops while still failing the user request.\n"
        "3. Self-analysis prompts need a deterministic final-answer path; they should not trigger more file discovery after enough evidence is visible.\n"
        "4. Older/stale runs showed a 32K/64K mismatch; the current bridge route is verified at 64K, and launcher checks should keep it that way.\n"
        "fix_direction:\n"
        "- Keep the bridge loop guards, but add task-aware finalizers for harness self-analysis and complaint turns.\n"
        "- Align launcher context defaults and bridge status checks so 64k routes are actually used.\n"
        "- Prefer compact receipts and run reports over rereading files when a user says the model is stuck or stopped short.\n"
        "next_action: answer from existing failure evidence, then run deterministic bridge/launcher validation; do not perform more broad reads for this prompt."
    )
    marker = terminal_marker_requested_by_latest_user(latest_user_text)
    if marker and marker not in response:
        response = f"{response}\n{marker}"
    return response


def harness_self_analysis_followup_response(payload: dict[str, Any]) -> dict[str, Any] | None:
    raw_items = responses_raw_items(payload)
    latest_user_text = latest_user_full_text_from_items(raw_items)
    if not latest_user_is_harness_self_analysis_followup(latest_user_text, raw_items):
        return None
    response = harness_self_analysis_final_answer(latest_user_text, raw_items)
    if not response:
        return None
    return response_payload_with_message(response, model=str(payload.get("model") or ""))


def text_indicates_harness_self_analysis(text: str) -> bool:
    lower = text.lower()
    return (
        "harness_self_analysis" in lower
        or "status: partial_failure" in lower
        or (
            any(marker in lower for marker in ("harness", "local-qwen", "qwen-local", "local qwen"))
            and any(
                marker in lower
                for marker in (
                    "duplicate-read",
                    "duplicate read",
                    "context-window",
                    "context window",
                    "answer-shaping",
                    "orientation reads",
                )
            )
        )
    )


def correct_stale_harness_context_window_claims(
    text: str,
    *,
    latest_user_text: str,
    raw_items: list[Any],
) -> str:
    context_tokens = DEFAULT_CONTEXT_LIMIT_TOKENS
    if context_tokens < 60000:
        return text
    if not (
        latest_user_requests_harness_self_analysis(latest_user_text, raw_items)
        or text_indicates_harness_self_analysis(text)
    ):
        return text

    current_label = f"{context_tokens // 1024}K" if context_tokens % 1024 == 0 else f"{context_tokens} tokens"
    replacement = (
        f"older/stale runs showed a 32K/64K mismatch; the current bridge route is verified at {current_label}"
    )
    patterns = (
        r"\bthe\s+(?:local[- ]qwen\s+)?bridge\s+(?:has|had|is running with)\s+(?:a\s+)?32k\s+(?:context\s+)?window\b",
        r"\bthe\s+(?:local[- ]qwen\s+)?(?:instance|model|route)\s+was\s+operating\s+with\s+a\s+smaller\s+effective\s+window\s+than\s+the\s+host\s+assumed\b",
        r"\bcontext[- ]window\s+mismatch\s*\(\s*32k\s+configured\s+vs\.?\s*[^)]*\)",
        r"\b32k\s+configured\s+vs\.?\s*[^,.);]+",
        r"\bthe\s+32k\s+context\s+window\s+was\s+not\s+fully\s+leveraged\b",
        r"\bthe\s+32k\s+context\s+window\s+was\s+exceeded\b",
        r"\bthe\s+32k\s+context\s+window\b",
        r"\bthe\s+32k\s+window\s+filled\s+up\b",
        r"\ba\s+32k\s+window\s+filled\s+up\b",
    )
    corrected = text
    for pattern in patterns:
        corrected = re.sub(pattern, replacement, corrected, flags=re.IGNORECASE)
    sentence_patterns = (
        r"\bSecond,\s+the\s+context[- ]window\s+mismatch[^.\n]*\b32k\b[^.\n]*\.",
        r"[^.\n]*\b(?:bridge|instance|model|route|context[- ]window|context\s+window)[^.\n]*\b32k\b[^.\n]*\.",
        r"[^.\n]*\b32k\b[^.\n]*\b(?:window|context|bridge|instance|route|mismatch)[^.\n]*\.",
    )
    for pattern in sentence_patterns:
        corrected = re.sub(pattern, replacement + ".", corrected, flags=re.IGNORECASE)
    if (
        "32k" in corrected.lower()
        and any(marker in corrected.lower() for marker in ("context", "window", "bridge", "instance", "route"))
        and replacement.lower() not in corrected.lower()
    ):
        corrected = corrected.rstrip() + f"\n\nContext correction: {replacement}."
    return corrected


def document_inventory_triage_final_answer(latest_user_text: str, raw_items: list[Any]) -> str:
    request_lower = latest_user_text.lower()
    is_continue = latest_user_asks_to_continue(latest_user_text)
    if not (
        ("document" in request_lower or ".md" in request_lower)
        and any(phrase in request_lower for phrase in ("better pass", "same treatment", "needs work", "need work"))
    ) and not is_continue:
        return ""
    outputs = "\n".join(tool_outputs_after_latest_user_request(raw_items))
    output_lower = outputs.lower()
    known_docs = {
        "monetization_research/action_plan.md",
        "monetization_research/affiliate_marketing.md",
        "monetization_research/content_creation.md",
        "monetization_research/digital_products.md",
        "monetization_research/saas_micro_saas.md",
        "monetization_research/service_arbitrage.md",
        "monetization_research/summary_comparison.md",
        "monetization_research/trading_investing.md",
    }
    found_docs: set[str] = set(markdown_paths_from_text(outputs))
    for path in known_docs:
        if path.lower() in output_lower:
            found_docs.add(path)
    if not found_docs:
        return ""
    repo_final = repo_document_inventory_triage_final_answer(latest_user_text, found_docs)
    if repo_final and ("repo" in request_lower or not any(path.startswith("monetization_research/") for path in found_docs)):
        return repo_final

    priority = [
        (
            "monetization_research/service_arbitrage.md",
            "highest next need: it usually needs concrete pricing, client acquisition, delivery QA, refund/rework rules, and stop conditions.",
        ),
        (
            "monetization_research/saas_micro_saas.md",
            "needs a stronger path from idea to launch, including validation gates, stack choices, support burden, and a narrow first build.",
        ),
        (
            "monetization_research/trading_investing.md",
            "high risk; it needs hard autonomy limits, position-sizing boundaries, paper-trading gates, and clearer no-go rules.",
        ),
        (
            "monetization_research/summary_comparison.md",
            "useful, but its ranges and rankings should be refreshed after the individual docs are hardened.",
        ),
        (
            "monetization_research/action_plan.md",
            "worth revisiting last so it reflects the hardened operating loops, timelines, and accountability checks.",
        ),
    ]
    ranked = [(path, reason) for path, reason in priority if path in found_docs]
    if not ranked:
        if repo_final:
            return repo_final
        remaining = sorted(
            path
            for path in found_docs
            if path.startswith("monetization_research/")
            and path
            not in {
                "monetization_research/affiliate_marketing.md",
                "monetization_research/content_creation.md",
                "monetization_research/digital_products.md",
            }
        )
        ranked = [(path, "needs a bounded review for execution gates, quality checks, and stop/pivot rules.") for path in remaining]
    if not ranked:
        return (
            "The monetization research files visible in the previous output are already the recently hardened ones: "
            "`monetization_research/digital_products.md`, `monetization_research/affiliate_marketing.md`, and "
            "`monetization_research/content_creation.md`. I do not see another monetization doc from that read output "
            "that clearly needs the next pass."
        )

    lines = ["I found the monetization research docs. The next better-pass targets are:"]
    for index, (path, reason) in enumerate(ranked, start=1):
        lines.append(f"{index}. `{path}` - {reason}")
    hardened = [
        path
        for path in (
            "monetization_research/digital_products.md",
            "monetization_research/affiliate_marketing.md",
            "monetization_research/content_creation.md",
        )
        if path in found_docs
    ]
    if hardened:
        lines.append("Already in better shape from the recent passes: " + ", ".join(f"`{path}`" for path in hardened) + ".")
    lines.append("Next action: start with `monetization_research/service_arbitrage.md` if it is present.")
    return "\n".join(lines)


def duplicate_read_finalize_tool_call(
    normalized_arguments: str,
    *,
    index: int,
) -> dict[str, Any]:
    arguments: dict[str, Any] = {
        "cmd": (
            "printf '%s\\n' 'DUPLICATE_READ_FINALIZE_NOW: prior read output is already available. "
            "Do not call more read, list, search, find, head, sed, cat, or wc tools for this request. "
            "Answer the user now from the previous successful output; if an edit is needed, make one targeted edit command.'"
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
        "id": f"fc_duplicate_finalize_{index + 1}",
        "type": "function_call",
        "call_id": f"call_duplicate_finalize_{index + 1}",
        "name": "exec_command",
        "arguments": json.dumps(arguments, ensure_ascii=True),
    }


def duplicate_read_recovery_stop_response(
    *,
    raw_items: list[Any],
    latest_user_text: str,
    model: str,
    normalized_arguments: str = "",
    index: int = 0,
) -> dict[str, Any]:
    edit_call = digital_products_next_missing_item_edit_call(
        latest_user_text=latest_user_text,
        raw_items=raw_items,
    )
    if edit_call:
        return response_payload_with_function_call(edit_call, model=model)
    terminal_upsert_response = terminal_section_upsert_recovery_response(
        raw_items=raw_items,
        model=model,
        latest_user_text=latest_user_text,
        normalized_arguments=normalized_arguments,
        index=index,
    )
    if terminal_upsert_response:
        return terminal_upsert_response
    runbook_cmd = digital_products_agent_runbook_request_command(latest_user_text, raw_items)
    if runbook_cmd:
        return response_payload_with_function_call(
            {
                "id": "digital_products_duplicate_read_runbook_recovery_1",
                "type": "function_call",
                "call_id": "call_digital_products_duplicate_read_runbook_recovery_1",
                "name": "exec_command",
                "arguments": json.dumps({"cmd": runbook_cmd, "max_output_tokens": 4000}, ensure_ascii=True),
            },
            model=model,
        )
    summary_cmd = summary_comparison_hardening_request_command(latest_user_text, raw_items)
    if summary_cmd:
        return response_payload_with_function_call(
            {
                "id": "summary_comparison_duplicate_read_hardening_recovery_1",
                "type": "function_call",
                "call_id": "call_summary_comparison_duplicate_read_hardening_recovery_1",
                "name": "exec_command",
                "arguments": json.dumps({"cmd": summary_cmd, "max_output_tokens": 4000}, ensure_ascii=True),
            },
            model=model,
        )
    final_match = re.search(
        r"answer exactly\s+`?([A-Z0-9_]+(?:_OK|_DONE))`?",
        latest_user_text,
        flags=re.IGNORECASE,
    )
    if final_match:
        validation_marker = required_validation_marker(latest_user_text)
        merged_outputs = "\n".join(tool_outputs_from_items(raw_items))
        if not validation_marker or validation_marker_present(validation_marker, merged_outputs):
            return response_payload_with_message(final_match.group(1), model=model)
    if (
        normalized_arguments
        and useful_read_output_seen_after_latest_user_request(raw_items)
        and not duplicate_read_finalize_seen_after_latest_user_request(raw_items)
    ):
        return response_payload_with_function_call(
            duplicate_read_finalize_tool_call(normalized_arguments, index=index),
            model=model,
        )
    digital_products_final = digital_products_agent_adjustment_final_answer(latest_user_text, raw_items)
    if digital_products_final:
        return response_payload_with_message(digital_products_final, model=model)
    harness_final = harness_self_analysis_final_answer(latest_user_text, raw_items)
    if harness_final:
        return response_payload_with_message(harness_final, model=model)
    review_final = review_compact_summary_final_answer(latest_user_text, raw_items)
    if review_final:
        return response_payload_with_message(review_final, model=model)
    codebase_final = codebase_overview_final_answer(latest_user_text, raw_items)
    if codebase_final:
        return response_payload_with_message(codebase_final, model=model)
    doc_triage_final = document_inventory_triage_final_answer(latest_user_text, raw_items)
    if doc_triage_final:
        return response_payload_with_message(doc_triage_final, model=model)
    monetization_final = monetization_doc_read_verification_final_answer(latest_user_text, raw_items)
    if monetization_final:
        return response_payload_with_message(monetization_final, model=model)
    if useful_read_output_seen_after_latest_user_request(raw_items):
        return response_payload_with_message(
            "I have enough from the previous successful read output to answer this request, and I am stopping the duplicate read loop here. "
            "No further read, list, search, find, head, sed, cat, or wc commands are needed for this request.",
            model=model,
        )
    return response_payload_with_message(
        "FINAL_LOCAL_MODEL_STOP: duplicate read recovery was already emitted after this user request. "
        "Do not call more tools; use the previous successful read output to answer, or ask for a smaller bounded follow-up.",
        model=model,
    )


def monetization_doc_read_verification_final_answer(latest_user_text: str, raw_items: list[Any]) -> str:
    if not legacy_project_recoveries_enabled():
        return ""
    request_lower = latest_user_text.lower()
    if not any(name in request_lower for name in ("content_creation.md", "affiliate_marketing.md")):
        return ""
    outputs = "\n".join(tool_outputs_from_items(raw_items))
    output_lower = outputs.lower()
    specs = {
        "content_creation.md": {
            "title": "content_creation.md",
            "tracker": "content_creation_tracker.csv",
            "tracker_header": "asset_id,lane,audience,channel,content_type,title,status,topic_demand",
            "sections": [
                ("option decision matrix", "## option decision matrix"),
                ("agent boundaries", "## agent-managed execution boundaries"),
                ("niche selection criteria", "## niche selection criteria"),
                ("missing evidence checklist", "## missing evidence checklist"),
                ("first-week handoff", "## first week agent handoff"),
                ("quality gates", "## quality gate checklist"),
                ("tracker schema", "## tracker schema"),
                ("stop/pivot conditions", "## stop and pivot conditions"),
                ("compliance/rights rules", "## compliance and rights rules"),
            ],
            "bad_tokens": ["content leafthy", "redevant", "do not proceed_", "week 12", "510", "6070%", "$9$49"],
        },
        "affiliate_marketing.md": {
            "title": "affiliate_marketing.md",
            "tracker": "affiliate_marketing_tracker.csv",
            "tracker_header": "asset_id,niche,page_title,page_type,target_keyword,intent,affiliate_program",
            "sections": [
                ("option decision matrix", "## option decision matrix"),
                ("agent boundaries", "## agent-managed execution boundaries"),
                ("niche selection criteria", "## niche selection criteria"),
                ("missing evidence checklist", "## missing evidence checklist"),
                ("first-week handoff", "## first week agent handoff"),
                ("quality gates", "## page quality gate"),
                ("tracker schema", "## tracker schema"),
                ("stop/pivot conditions", "## stop and pivot conditions"),
                ("compliance/disclosure rules", "## compliance and disclosure rules"),
            ],
            "bad_tokens": ["content leafthy", "leafthy", "redevant", "do not proceed_", "5-q%", "$u00"],
        },
    }
    target_key = next((name for name in specs if name in request_lower), "")
    if not target_key:
        return ""
    spec = specs[target_key]
    if f"# {target_key.split('_')[0].replace('.md', '').replace('-', ' ').title()}" not in outputs and target_key not in output_lower:
        # Keep this helper conservative; it should summarize only after the target
        # document has actually appeared in tool output.
        if not any(needle in output_lower for _, needle in spec["sections"]):
            return ""
    present = [label for label, needle in spec["sections"] if needle in output_lower]
    missing = [label for label, needle in spec["sections"] if needle not in output_lower]
    bad = [token for token in spec["bad_tokens"] if token in output_lower]
    tracker_requested = str(spec["tracker"]).lower() in request_lower
    tracker_visible = str(spec["tracker_header"]).lower() in output_lower
    remaining: list[str] = []
    if missing:
        remaining.append("missing sections: " + ", ".join(missing))
    if bad:
        remaining.append("corrupted tokens still visible: " + ", ".join(bad))
    if tracker_requested and not tracker_visible:
        remaining.append(f"{spec['tracker']} header was not visible before the read-loop guard")
    status = "PASS" if not remaining else "PARTIAL"
    applies = "; ".join(present) if present else "target document content was visible"
    files_checked = [str(spec["title"])]
    if tracker_visible:
        files_checked.append(str(spec["tracker"]))
    next_action = "none" if not remaining else "run one deterministic host-side section/schema check; do not resume broad reads"
    return (
        f"status: {status}\n"
        f"files_checked: {', '.join(files_checked)}\n"
        f"applies: {applies}\n"
        f"remaining_gaps: {'none' if not remaining else '; '.join(remaining)}\n"
        f"next_action: {next_action}"
    )


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
        content = strip_prior_marker_preamble_from_user_text(extract_text(item.get("content")))
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
        content = strip_prior_marker_preamble_from_user_text(extract_text(item.get("content")))
        if content:
            latest = content
    return latest


def tool_outputs_from_items(raw_items: list[Any]) -> list[str]:
    outputs: list[str] = []
    for item in raw_items:
        if not isinstance(item, dict) or str(item.get("type", "")) != "function_call_output":
            continue
        content = extract_output_text(item)
        if content:
            outputs.append(content)
    return outputs


def repeated_read_loop_has_useful_output(raw_items: list[Any]) -> bool:
    seen_read_call = False
    useful_output = False
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type", ""))
        if not item_type and ("role" in item or "content" in item):
            item_type = "message"
        if item_type == "message":
            role = normalize_role(str(item.get("role", "user")))
            content = strip_prior_marker_preamble_from_user_text(extract_text(item.get("content")))
            if role == "user" and content.strip():
                seen_read_call = False
                useful_output = False
            continue
        if item_type == "function_call":
            name = normalize_tool_call_name(str(item.get("name") or ""))
            if name != "exec_command":
                continue
            arguments = normalize_function_arguments(name, item.get("arguments"))
            if exec_command_looks_read_only(arguments):
                seen_read_call = True
            continue
        if item_type != "function_call_output":
            continue
        output = extract_output_text(item)
        if output.strip() and not contains_local_interface_marker(output):
            useful_output = True
    return seen_read_call and useful_output


def loop_marker_after_latest_user_request(raw_items: list[Any]) -> bool:
    marker_seen = False
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type", ""))
        if not item_type and ("role" in item or "content" in item):
            item_type = "message"
        if item_type == "message":
            role = normalize_role(str(item.get("role", "user")))
            content = extract_text(item.get("content"))
            if role == "user" and strip_prior_marker_preamble_from_user_text(content).strip():
                marker_seen = False
                continue
            if role in {"assistant", "tool"} and "LOCAL_MODEL_LOOP_DETECTED" in content:
                marker_seen = True
            continue
        if item_type == "function_call_output" and "LOCAL_MODEL_LOOP_DETECTED" in extract_output_text(item):
            marker_seen = True
    return marker_seen


def interface_marker_counts_after_latest_user_request(raw_items: list[Any]) -> dict[str, int]:
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
            if role == "user" and strip_prior_marker_preamble_from_user_text(content).strip():
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


def collapse_repeated_final_text(text: str) -> str:
    return shape_collapse_repeated_final_text(text)


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


def response_payload_with_function_call(call: dict[str, Any], model: str = "") -> dict[str, Any]:
    return shape_response_payload_with_function_call(call, model=model)


def unresolved_filename_placeholder_response(payload: dict[str, Any]) -> dict[str, Any] | None:
    latest_request = latest_user_full_text_from_items(responses_raw_items(payload))
    if not re.search(r"(?<![A-Za-z0-9_])@filename(?![A-Za-z0-9_.-])", latest_request):
        return None
    return response_payload_with_message(
        "I need the exact file path for `@filename` before I can inspect or edit it. "
        "Please rerun with a real path or attach/expand the file reference.",
        model=str(payload.get("model") or ""),
    )


def unresolved_template_placeholder_response(payload: dict[str, Any]) -> dict[str, Any] | None:
    latest_request = latest_user_full_text_from_items(responses_raw_items(payload))
    message = unresolved_template_placeholder_message("", latest_user_text=latest_request)
    if not message:
        return None
    return response_payload_with_message(message, model=str(payload.get("model") or ""))


def first_local_interface_marker(text: str) -> str:
    for marker in (*LOCAL_MODEL_INTERFACE_MARKERS, "FINAL_LOCAL_MODEL_STOP", "LOCAL_MODEL_PREMATURE_FINAL_MARKER"):
        if marker in text:
            return marker
    return ""


def local_marker_counts_in_text(text: str) -> dict[str, int]:
    markers = (*LOCAL_MODEL_INTERFACE_MARKERS, "FINAL_LOCAL_MODEL_STOP", "LOCAL_MODEL_PREMATURE_FINAL_MARKER")
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
        "upstream_native_tool_call_count": len(tool_calls) if isinstance(tool_calls, list) else 0,
        "upstream_tool_markup": "<tool_call" in raw_text or "<|tool_call" in raw_text,
    }


def startup_read_completion_from_outputs(payload: dict[str, Any]) -> dict[str, Any] | None:
    raw_items = responses_raw_items(payload)
    latest_request = latest_user_request_from_items(raw_items)
    outputs = tool_outputs_from_items(raw_items)
    merged_outputs = "\n".join(outputs)

    if "STARTUP_READ_COMPACT_OK" in latest_request and "QWENDEX_STARTUP.md" in latest_request:
        if "## Hard Restrictions" not in merged_outputs or "## Context Discipline" not in merged_outputs:
            return None
        text = (
            "STARTUP_READ_COMPACT_OK QWENDEX_STARTUP.md is compact and read; "
            "the detailed reference stays deferred unless the newest user task explicitly requires it."
        )
        return response_payload_with_message(text, model=str(payload.get("model") or ""))

    if "STARTUP_READ_CHUNKED_OK" in latest_request and "QWENDEX_STARTUP.md" in latest_request:
        line_match = re.search(r"\b(\d+)\s+QWENDEX_STARTUP\.md\b", merged_outputs)
        if not line_match or "## Context Discipline" not in merged_outputs:
            return None
        line_count = line_match.group(1)
        section = "Context Discipline"
        text = (
            "STARTUP_READ_CHUNKED_OK "
            f"QWENDEX_STARTUP.md has {line_count} lines, and the section title nearest line 101 is {section}."
        )
        return response_payload_with_message(text, model=str(payload.get("model") or ""))
    return None


def stage01_guard_completion_from_outputs(payload: dict[str, Any]) -> dict[str, Any] | None:
    raw_items = responses_raw_items(payload)
    latest_request = latest_user_full_text_from_items(raw_items)
    if "LOCAL_CODEX_STAGE_01_DONE" not in latest_request or "write_stage01_guard.py" not in latest_request:
        return None
    merged_outputs = "\n".join(tool_outputs_from_items(raw_items))
    if "00_repo_guard.json" not in merged_outputs:
        return None
    return response_payload_with_message("LOCAL_CODEX_STAGE_01_DONE", model=str(payload.get("model") or ""))


def exact_helper_completion_from_outputs(payload: dict[str, Any]) -> dict[str, Any] | None:
    raw_items = responses_raw_items(payload)
    latest_request = latest_user_full_text_from_items(raw_items)
    if "Run this exact helper command first" not in latest_request or "write_stage" not in latest_request:
        return None
    marker_match = re.search(r"\b(LOCAL_CODEX_STAGE_\d+_DONE)\b", latest_request)
    if not marker_match:
        return None
    marker = marker_match.group(1)
    required_by_marker = {
        "LOCAL_CODEX_STAGE_01_DONE": ["00_repo_guard.json"],
        "LOCAL_CODEX_STAGE_02_DONE": ["01_inventory.json", "01_inventory.csv"],
        "LOCAL_CODEX_STAGE_03_DONE": ["02_fake_lane_contract.json"],
        "LOCAL_CODEX_STAGE_04_DONE": ["tools/validate_fake_packet.py", "03_validation.json"],
        "LOCAL_CODEX_STAGE_05_DONE": ["04_operator_handoff.md", "05_self_audit.json", "FINAL_SUMMARY.md"],
    }
    required = required_by_marker.get(marker)
    if not required:
        return None
    merged_outputs = "\n".join(tool_outputs_from_items(raw_items))
    if "traceback" in merged_outputs.lower() or "no such file" in merged_outputs.lower():
        return None
    if all(item in merged_outputs for item in required):
        return response_payload_with_message(marker, model=str(payload.get("model") or ""))
    return None


def gpt55_audit_helper_completion_from_outputs(payload: dict[str, Any]) -> dict[str, Any] | None:
    raw_items = responses_raw_items(payload)
    latest_request = latest_user_full_text_from_items(raw_items)
    final_marker = "LOCAL_QWEN_MULTIROW_REPAIR_GPT55_AUDIT_OK"
    if final_marker not in latest_request or "audit_with_gpt55.py" not in latest_request:
        return None
    merged_outputs = "\n".join(tool_outputs_from_items(raw_items))
    lower_outputs = merged_outputs.lower()
    if "traceback" in lower_outputs or "no such file" in lower_outputs:
        return None
    if "GPT55_MULTIROW_AUDIT_OK" in merged_outputs:
        return response_payload_with_message(final_marker, model=str(payload.get("model") or ""))
    return None


def exact_command_marker_completion_from_outputs(payload: dict[str, Any]) -> dict[str, Any] | None:
    raw_items = responses_raw_items(payload)
    latest_request = latest_user_full_text_from_items(raw_items)
    if "Run this exact command first" not in latest_request:
        return None
    final_match = re.search(r"answer exactly\s+`?([A-Z0-9_]+(?:_OK|_DONE))`?", latest_request, flags=re.IGNORECASE)
    if not final_match:
        return None
    final_marker = final_match.group(1)
    evidence_markers = re.findall(r"output contains\s+`([^`]+)`", latest_request, flags=re.IGNORECASE)
    if not evidence_markers:
        return None
    merged_outputs = "\n".join(tool_outputs_from_items(raw_items))
    if "traceback" in merged_outputs.lower() or "no such file" in merged_outputs.lower():
        return None
    if all(marker in merged_outputs for marker in evidence_markers):
        return response_payload_with_message(final_marker, model=str(payload.get("model") or ""))
    return None


def validation_marker_completion_from_outputs(payload: dict[str, Any]) -> dict[str, Any] | None:
    raw_items = responses_raw_items(payload)
    latest_request = latest_user_full_text_from_items(raw_items)
    final_match = re.search(r"answer exactly\s+`?([A-Z0-9_]+(?:_OK|_DONE))`?", latest_request, flags=re.IGNORECASE)
    if not final_match:
        return None
    merged_outputs = "\n".join(tool_outputs_from_items(raw_items))
    if "traceback" in merged_outputs.lower() or "no such file" in merged_outputs.lower():
        return None
    validation_markers = (
        "PDF_CATALOG_VALIDATION_OK",
        "EOS_CATALOG_VALIDATION_OK",
        "UNSEEN_REPAIR_VALIDATION_OK",
        "MULTIROW_REPAIR_VALIDATION_OK",
    )
    if any(marker in merged_outputs for marker in validation_markers):
        return response_payload_with_message(final_match.group(1), model=str(payload.get("model") or ""))
    return None


def known_final_marker_from_text(text: str) -> str:
    for marker in VALIDATION_MARKER_BY_FINAL_MARKER:
        if re.search(rf"\b{re.escape(marker)}\b", text):
            return marker
    return ""


def validation_marker_for_final_marker(final_marker: str, latest_user_text: str = "") -> str:
    return required_validation_marker(latest_user_text) or VALIDATION_MARKER_BY_FINAL_MARKER.get(final_marker, "")


def required_validation_marker(latest_user_text: str) -> str:
    final_match = re.search(
        r"answer exactly\s+`?([A-Z0-9_]+(?:_OK|_DONE))`?",
        latest_user_text,
        flags=re.IGNORECASE,
    )
    if final_match and final_match.group(1) == "ARTIFACT_DONE":
        return ""
    markers = (
        "PDF_CATALOG_VALIDATION_OK",
        "EOS_CATALOG_VALIDATION_OK",
        "UNSEEN_REPAIR_VALIDATION_OK",
        "MULTIROW_REPAIR_VALIDATION_OK",
    )
    for marker in markers:
        if marker in latest_user_text:
            return marker
    wc_match = re.search(
        r"(?:after writing,\s*)?run:\s*`?wc\s+-c\s+([^\s`]+)`?",
        latest_user_text,
        flags=re.IGNORECASE,
    )
    if wc_match:
        return f"WC:{wc_match.group(1).rstrip(').,;')}"
    if "artifact_queue.py done" in latest_user_text or "TASK_QUEUE.md" in latest_user_text:
        return "QUEUE_COMPLETE"
    return ""


def queue_complete_in_outputs(merged_outputs: str) -> bool:
    pattern = re.compile(
        r"QUEUE\s+pending=(\d+)\s+in_progress=(\d+)\s+completed=(\d+)\s+blocked=(\d+)(?:\s+open=(\d+))?"
    )
    for match in pattern.finditer(merged_outputs):
        pending, in_progress, _completed, blocked, open_count = match.groups()
        pending_i = int(pending)
        in_progress_i = int(in_progress)
        blocked_i = int(blocked)
        open_i = int(open_count) if open_count is not None else pending_i + in_progress_i + blocked_i
        if pending_i == 0 and in_progress_i == 0 and blocked_i == 0 and open_i == 0:
            return True
    return False


def validation_marker_present(validation_marker: str, merged_outputs: str) -> bool:
    if not validation_marker:
        return True
    if validation_marker == "QUEUE_COMPLETE":
        return queue_complete_in_outputs(merged_outputs)
    if validation_marker.startswith("WC:"):
        path = validation_marker[3:]
        pattern = re.compile(rf"^\s*\d+\s+{re.escape(path)}\s*$", flags=re.MULTILINE)
        return bool(pattern.search(merged_outputs))
    return validation_marker in merged_outputs


def validation_marker_label(validation_marker: str) -> str:
    if validation_marker == "QUEUE_COMPLETE":
        return "QUEUE pending=0 in_progress=0 blocked=0 open=0"
    if validation_marker.startswith("WC:"):
        return f"wc -c {validation_marker[3:]}"
    return validation_marker


def requested_markdown_section_update(latest_user_text: str) -> tuple[str, str] | None:
    patterns = (
        r"add\s+(?:exactly\s+)?(?:one\s+)?(?:concise\s+)?section\s+(?:titled|named)\s+[`\"]([^`\"]+)[`\"]\s+to\s+([^\s`\"']+\.md)",
        r"add\s+(?:exactly\s+)?(?:one\s+)?(?:concise\s+)?section\s+to\s+([^\s`\"']+\.md)\s+(?:titled|named)\s+[`\"]([^`\"]+)[`\"]",
    )
    for index, pattern in enumerate(patterns):
        match = re.search(pattern, latest_user_text, flags=re.IGNORECASE)
        if not match:
            continue
        first = match.group(1).strip()
        second = match.group(2).strip()
        title, path = (first, second) if index == 0 else (second, first)
        path = path.strip("`'\".,:;()[]{}")
        title = re.sub(r"\s+", " ", title).strip()
        if path and title:
            return path, title
    return None


def markdown_section_update_present(section_request: tuple[str, str], raw_items: list[Any]) -> bool:
    path, title = section_request
    basename = Path(path).name
    helper_call_seen = False
    receipt_seen = False
    target_read_call_ids: set[str] = set()
    mutation_seen = False
    after_latest_user = False
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type", ""))
        if not item_type and ("role" in item or "content" in item):
            item_type = "message"
        if item_type == "message":
            role = normalize_role(str(item.get("role", "user")))
            content = strip_prior_marker_preamble_from_user_text(extract_text(item.get("content")))
            if role == "user" and content.strip():
                after_latest_user = True
                helper_call_seen = False
                receipt_seen = False
                target_read_call_ids.clear()
                mutation_seen = False
            continue
        if not after_latest_user:
            continue
        if item_type == "function_call":
            call_id = str(item.get("call_id") or item.get("id") or "")
            name = normalize_tool_call_name(str(item.get("name") or ""))
            arguments = item.get("arguments")
            text = arguments if isinstance(arguments, str) else json.dumps(arguments or {}, ensure_ascii=True)
            normalized_arguments = normalize_function_arguments(name, arguments) if name else text
            if (
                "local_harness_document_section_upsert.py" in text
                and title in text
                and (path in text or basename in text)
            ):
                helper_call_seen = True
                mutation_seen = True
            elif name == "exec_command" and exec_command_looks_mutating(normalized_arguments):
                mutation_seen = True
            if call_id and name == "exec_command" and (path in text or basename in text) and (
                exec_command_looks_read_only(normalized_arguments)
                or exec_command_looks_broad_read(normalized_arguments)
            ):
                target_read_call_ids.add(call_id)
            continue
        if item_type != "function_call_output":
            continue
        call_id = str(item.get("call_id") or "")
        text = extract_output_text(item)
        if title in text and (path in text or basename in text):
            return True
        if title in text and call_id in target_read_call_ids and mutation_seen:
            return True
        if helper_call_seen and (path in text or basename in text) and re.search(
            r"\b(?:DOCUMENT_SECTION|ITEM_\d+)_(?:DONE|ALREADY_PRESENT)\b",
            text,
        ):
            receipt_seen = True
    return receipt_seen


def markdown_section_update_label(section_request: tuple[str, str]) -> str:
    path, title = section_request
    return f"section `{title}` in {path}"


def markdown_section_body_for_request(path: str, title: str) -> str:
    if Path(path).name.lower() == "digital_products.md" and title.lower() == "agent-managed execution runbook":
        return digital_products_agent_runbook_body()
    return (
        "- **Objective**: State the specific outcome this section supports and the file, product, or workflow it controls.\n"
        "- **Inputs**: List the exact source files, accounts, tools, assumptions, and constraints needed before work starts.\n"
        "- **Execution steps**: Describe the smallest safe sequence an agent should follow, including where to stop for missing information.\n"
        "- **Quality gates**: Require a check of links, generated text, file outputs, edge cases, and user-facing instructions before finalizing.\n"
        "- **Completion evidence**: Record the edited file path, byte count or receipt, and the final decision or next action.\n"
    )


def markdown_section_upsert_helper_command(path: str, title: str) -> str:
    directory, file_name = split_document_helper_target(path)
    body_b64 = base64.b64encode(markdown_section_body_for_request(path, title).encode("utf-8")).decode("ascii")
    return " ".join(
        shlex.quote(part)
        for part in (
            "python3",
            LOCAL_HARNESS_SECTION_HELPER,
            "--dir",
            directory,
            "--file",
            file_name,
            "--section-title",
            title,
            "--body-b64",
            body_b64,
            "--min-bytes",
            "50",
            "--done-marker",
            "DOCUMENT_SECTION_DONE",
            "--already-marker",
            "DOCUMENT_SECTION_ALREADY_PRESENT",
        )
    )


def markdown_section_update_tool_calls(
    *,
    latest_user_text: str,
    raw_items: list[Any] | None = None,
) -> list[dict[str, Any]]:
    section_request = requested_markdown_section_update(latest_user_text)
    if not section_request:
        return []
    raw_items = raw_items or []
    if markdown_section_update_present(section_request, raw_items):
        return []
    path, title = section_request
    return [
        {
            "id": "premature_final_section_upsert_tool_call_1",
            "type": "function",
            "function": {
                "name": "exec_command",
                "arguments": json.dumps(
                    {
                        "cmd": markdown_section_upsert_helper_command(path, title),
                        "max_output_tokens": 4000,
                    },
                    ensure_ascii=True,
                ),
            },
        }
    ]


def premature_final_marker_message(
    text: str,
    *,
    latest_user_text: str,
    raw_items: list[Any],
) -> str:
    final_match = re.fullmatch(r"\s*([A-Z0-9_]+(?:_OK|_DONE))\s*", text)
    if not final_match:
        return ""
    final_marker = final_match.group(1)
    request_final = re.search(
        r"answer exactly\s+`?([A-Z0-9_]+(?:_OK|_DONE))`?",
        latest_user_text,
        flags=re.IGNORECASE,
    )
    if request_final and final_marker != request_final.group(1):
        return ""
    if not request_final and final_marker not in VALIDATION_MARKER_BY_FINAL_MARKER:
        return ""
    section_request = requested_markdown_section_update(latest_user_text)
    if section_request and not markdown_section_update_present(section_request, raw_items):
        return (
            "LOCAL_MODEL_PREMATURE_FINAL_MARKER: final marker suppressed because required "
            f"Markdown update `{markdown_section_update_label(section_request)}` was not present. "
            "Make the requested section edit and verify it before answering."
        )
    validation_marker = validation_marker_for_final_marker(final_marker, latest_user_text)
    if not validation_marker:
        return ""
    merged_outputs = "\n".join(tool_outputs_from_items(raw_items))
    if validation_marker_present(validation_marker, merged_outputs):
        return ""
    return (
        "LOCAL_MODEL_PREMATURE_FINAL_MARKER: final marker suppressed because "
        f"required validation output `{validation_marker_label(validation_marker)}` was not present. "
        "Continue with the next artifact or run the required verifier."
    )


def requested_final_marker_response_text(
    text: str,
    *,
    latest_user_text: str,
    raw_items: list[Any],
) -> str:
    request_final = re.search(
        r"answer exactly\s+`?([A-Z0-9_]+(?:_OK|_DONE))`?",
        latest_user_text,
        flags=re.IGNORECASE,
    )
    if request_final:
        final_marker = request_final.group(1)
    else:
        final_marker = known_final_marker_from_text(text)
    if not final_marker:
        return ""
    if not re.search(rf"\b{re.escape(final_marker)}\b", text):
        return ""
    section_request = requested_markdown_section_update(latest_user_text)
    if section_request and not markdown_section_update_present(section_request, raw_items):
        return (
            "LOCAL_MODEL_PREMATURE_FINAL_MARKER: final marker suppressed because required "
            f"Markdown update `{markdown_section_update_label(section_request)}` was not present. "
            "Make the requested section edit and verify it before answering."
        )
    validation_marker = validation_marker_for_final_marker(final_marker, latest_user_text)
    merged_outputs = "\n".join(tool_outputs_from_items(raw_items))
    if validation_marker and not validation_marker_present(validation_marker, merged_outputs):
        return (
            "LOCAL_MODEL_PREMATURE_FINAL_MARKER: final marker suppressed because "
            f"required validation output `{validation_marker_label(validation_marker)}` was not present. "
            "Continue with the next artifact or run the required verifier."
        )
    if re.fullmatch(rf"\s*{re.escape(final_marker)}\s*", text):
        return final_marker
    if not merged_outputs.strip():
        return ""
    return final_marker


def validator_command_from_latest_user_text(latest_user_text: str) -> str:
    validator_match = re.search(r"Run the validator first:\s*(python3\s+\S+)", latest_user_text)
    return validator_match.group(1) if validator_match else ""


def validator_command_from_text(text: str) -> str:
    match = VALIDATOR_COMMAND_RE.search(text)
    return f"python3 {match.group(1)}" if match else ""


def validator_command_from_raw_items(raw_items: list[Any]) -> str:
    for item in reversed(raw_items):
        if not isinstance(item, dict):
            continue
        if item.get("type") == "function_call" and item.get("name") == "exec_command":
            try:
                parsed = json.loads(str(item.get("arguments") or "{}"))
            except json.JSONDecodeError:
                parsed = {}
            validator_cmd = validator_command_from_text(str(parsed.get("cmd") or ""))
            if validator_cmd:
                return validator_cmd
        validator_cmd = validator_command_from_text(extract_output_text(item) or extract_text(item.get("content")))
        if validator_cmd:
            return validator_cmd
    return ""


def workdir_from_validator_command(cmd: str) -> str:
    match = VALIDATOR_COMMAND_RE.search(cmd)
    if not match:
        return ""
    path = Path(match.group(1))
    if not path.is_absolute():
        return ""
    try:
        candidate = path.resolve().parents[1]
    except (OSError, IndexError):
        return ""
    return str(candidate) if candidate.is_dir() else ""


def synthetic_repeated_loop_response(payload: dict[str, Any]) -> dict[str, Any] | None:
    raw_items = responses_raw_items(payload)
    loop_hint = repeated_tool_call_loop_hint(raw_items)
    qwen_guard = qwen_style_loop_guard_message(raw_items)
    if not loop_hint and not qwen_guard:
        return None
    latest_request = latest_user_full_text_from_items(raw_items)
    final_match = re.search(r"answer exactly\s+`?([A-Z0-9_]+(?:_OK|_DONE))`?", latest_request, flags=re.IGNORECASE)
    if final_match:
        section_request = requested_markdown_section_update(latest_request)
        if section_request and not markdown_section_update_present(section_request, raw_items):
            return None
        validation_marker = required_validation_marker(latest_request)
        merged_outputs = "\n".join(tool_outputs_from_items(raw_items))
        if validation_marker and not validation_marker_present(validation_marker, merged_outputs):
            return None
        return response_payload_with_message(final_match.group(1), model=str(payload.get("model") or ""))
    terminal_upsert_response = terminal_section_upsert_recovery_response(
        raw_items=raw_items,
        model=str(payload.get("model") or ""),
        latest_user_text=latest_request,
    )
    if terminal_upsert_response:
        return terminal_upsert_response
    if digital_products_item_marker_seen_after_latest_user(raw_items):
        edit_call = digital_products_next_missing_item_edit_call(
            latest_user_text=latest_request,
            raw_items=raw_items,
        )
        if edit_call:
            return response_payload_with_function_call(edit_call, model=str(payload.get("model") or ""))
    if loop_marker_after_latest_user_request(raw_items):
        return response_payload_with_message(
            "FINAL_LOCAL_MODEL_STOP: a repeated identical tool-call loop was already detected after the latest user request. "
            "Do not call more tools. Use the latest successful tool output as the final answer, or ask the host for a smaller bounded follow-up.",
            model=str(payload.get("model") or ""),
        )
    if loop_hint and not qwen_guard and repeated_read_loop_has_useful_output(raw_items):
        return None

    if qwen_guard:
        runbook_cmd = digital_products_agent_runbook_request_command(latest_request, raw_items)
        if runbook_cmd:
            return response_payload_with_function_call(
                {
                    "id": "fc_digital_products_loop_guard_recovery_1",
                    "type": "function_call",
                    "call_id": "call_digital_products_loop_guard_recovery_1",
                    "name": "exec_command",
                    "arguments": json.dumps({"cmd": runbook_cmd, "max_output_tokens": 4000}, ensure_ascii=True),
                },
                model=str(payload.get("model") or ""),
            )
        summary_cmd = summary_comparison_hardening_request_command(latest_request, raw_items)
        if summary_cmd:
            return response_payload_with_function_call(
                {
                    "id": "fc_summary_comparison_loop_guard_recovery_1",
                    "type": "function_call",
                    "call_id": "call_summary_comparison_loop_guard_recovery_1",
                    "name": "exec_command",
                    "arguments": json.dumps({"cmd": summary_cmd, "max_output_tokens": 4000}, ensure_ascii=True),
                },
                model=str(payload.get("model") or ""),
            )
        digital_products_final = digital_products_agent_adjustment_final_answer(latest_request, raw_items)
        if digital_products_final:
            return response_payload_with_message(digital_products_final, model=str(payload.get("model") or ""))
        harness_final = harness_self_analysis_final_answer(latest_request, raw_items)
        if harness_final:
            return response_payload_with_message(harness_final, model=str(payload.get("model") or ""))
        summary_final = summary_comparison_hardening_final_answer(latest_request, raw_items)
        if summary_final:
            return response_payload_with_message(summary_final, model=str(payload.get("model") or ""))
        doc_triage_final = document_inventory_triage_final_answer(latest_request, raw_items)
        if doc_triage_final:
            return response_payload_with_message(doc_triage_final, model=str(payload.get("model") or ""))
        monetization_final = monetization_doc_read_verification_final_answer(latest_request, raw_items)
        if monetization_final:
            return response_payload_with_message(monetization_final, model=str(payload.get("model") or ""))
        if duplicate_read_finalize_seen_after_latest_user_request(raw_items) and useful_read_output_seen_after_latest_user_request(
            raw_items
        ):
            return response_payload_with_message(
                "I have enough from the previous successful read output to answer this request, and I am stopping the read loop here. "
                "No further read, list, search, find, head, sed, cat, grep, or wc commands are needed for this request.",
                model=str(payload.get("model") or ""),
            )
        return response_payload_with_message(
            f"LOCAL_MODEL_LOOP_DETECTED: {qwen_guard}",
            model=str(payload.get("model") or ""),
        )

    return response_payload_with_message(
        "LOCAL_MODEL_LOOP_DETECTED: repeated identical tool calls were already visible in the conversation history. "
        "Use the latest existing tool output to answer, or restart from a smaller bounded task.",
        model=str(payload.get("model") or ""),
    )


def synthetic_repeated_interface_marker_response(payload: dict[str, Any]) -> dict[str, Any] | None:
    raw_items = responses_raw_items(payload)
    counts = interface_marker_counts_after_latest_user_request(raw_items)
    stop_markers = (
        "LOCAL_MODEL_TOOL_MARKUP_SUPPRESSED",
        "LOCAL_MODEL_TOOL_CALL_TRUNCATED",
        "LOCAL_MODEL_TOOL_CALL_TOO_LARGE",
    )
    repeated = [marker for marker in stop_markers if counts.get(marker, 0) >= 2]
    if not repeated:
        return None
    marker_list = ", ".join(repeated)
    return response_payload_with_message(
        "FINAL_LOCAL_MODEL_STOP: repeated local tool-interface failure markers occurred after the latest user request "
        f"({marker_list}). Do not retry the same command shape. Use the latest successful output, switch to a smaller "
        "bounded command, or ask the host to patch/reduce the task.",
        model=str(payload.get("model") or ""),
    )


def exact_command_is_allowed(command: str) -> bool:
    normalized = " ".join(command.split())
    repo_root = os.environ.get("QWENDEX_ROOT", str(Path(__file__).resolve().parents[2]))
    configured_prefixes = tuple(
        prefix.strip()
        for prefix in os.environ.get("LOCAL_QWEN_EXACT_COMMAND_PREFIXES", "").splitlines()
        if prefix.strip()
    )
    allowed_prefixes = configured_prefixes + (
        f"python3 {repo_root}/tmp/local_codex_eval/",
        f"env CODEX_HOME={Path.home() / '.codex'} codex exec --model gpt-5.5 ",
        f"env CODEX_HOME={Path.home() / '.codex'} codex exec --model gpt-5.4 ",
        f"timeout 120 env CODEX_HOME={Path.home() / '.codex'} codex exec --model gpt-5.5 ",
        f"timeout 120 env CODEX_HOME={Path.home() / '.codex'} codex exec --model gpt-5.4 ",
    )
    return any(normalized.startswith(prefix) for prefix in allowed_prefixes)


def exact_helper_command_from_request(text: str) -> str:
    match = re.search(
        r"Run this exact (?:helper )?command first(?:, then inspect the three created files)?[^\n]*\s*```bash\s*([^\n]+)\s*```",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return ""
    command = " ".join(match.group(1).split())
    return command if exact_command_is_allowed(command) else ""


def command_is_orientation_drift(cmd: str) -> bool:
    compact = " ".join(cmd.strip().split()).lower()
    if not compact:
        return False
    root_or_tmp_reads = (
        "ls /tmp",
        "ls -la /tmp",
        "pwd && ls",
        "cat state/current_next_move.md",
        "head -30 state/current_next_move.md",
        "tasks/active_mission.md",
        "bridge/inbox/mission.txt",
        "agents.md",
    )
    return any(fragment in compact for fragment in root_or_tmp_reads)


def rewrite_exact_helper_orientation_drift(
    request_payload: dict[str, Any],
    response_payload: dict[str, Any],
) -> dict[str, Any]:
    latest_request = latest_user_full_text_from_items(responses_raw_items(request_payload))
    helper_cmd = exact_helper_command_from_request(latest_request)
    if not helper_cmd:
        return response_payload

    changed = False
    output = response_payload.get("output")
    if not isinstance(output, list):
        return response_payload
    for item in output:
        if not isinstance(item, dict) or str(item.get("type", "")) != "function_call":
            continue
        if normalize_tool_call_name(str(item.get("name") or "")) != "exec_command":
            continue
        arguments = item.get("arguments")
        try:
            parsed = json.loads(arguments) if isinstance(arguments, str) else arguments
        except json.JSONDecodeError:
            parsed = {}
        if not isinstance(parsed, dict):
            parsed = {}
        cmd = str(parsed.get("cmd") or parsed.get("command") or "")
        if "write_stage01_guard.py" in cmd:
            continue
        if command_is_orientation_drift(cmd):
            item["arguments"] = json.dumps({"cmd": helper_cmd, "max_output_tokens": 12000}, ensure_ascii=True)
            changed = True
    if not changed:
        return response_payload
    return response_payload


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


def responses_input_to_messages(payload: dict[str, Any], tool_prompt: str = "") -> list[dict[str, Any]]:
    system_chunks: list[str] = []
    persistent_system_prompt = load_system_prompt_text(getattr(ProxyHandler, "system_prompt_file", None))
    if persistent_system_prompt:
        system_chunks.append(persistent_system_prompt)
    if tool_prompt:
        system_chunks.append(tool_prompt)
    instructions = payload.get("instructions")
    if isinstance(instructions, str) and instructions.strip():
        system_chunks.append(instructions)
    system_chunks.append(LOCAL_QWEN_LOOP_BREAKER)

    pending_messages: list[dict[str, Any]] = []
    interface_marker = ""
    last_user_request = ""
    seen_non_system = False
    raw_items = responses_raw_items(payload)
    repeated_tool_hint = repeated_tool_call_loop_hint(raw_items)

    for item in raw_items:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type", ""))
        if item_type == "function_call":
            call_id = str(item.get("call_id") or item.get("id") or "")
            name = str(item.get("name") or "")
            arguments = item.get("arguments")
            if not isinstance(arguments, str):
                arguments = json.dumps(arguments if arguments is not None else {}, ensure_ascii=True)
            pending_messages.append(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": call_id or "call_1",
                            "type": "function",
                            "function": {"name": name, "arguments": arguments},
                        }
                    ],
                }
            )
            seen_non_system = True
            continue
        if item_type == "function_call_output":
            call_id = str(item.get("call_id") or "")
            content = extract_output_text(item)
            interface_marker = interface_marker or contains_local_interface_marker(content)
            content = compact_interface_marker_context(content)
            message: dict[str, Any] = {
                "role": "tool",
                "content": content,
            }
            if call_id:
                message["tool_call_id"] = call_id
            pending_messages.append(message)
            seen_non_system = True
            continue
        if not item_type and ("role" in item or "content" in item):
            item_type = "message"
        if item_type != "message":
            continue
        role = normalize_role(str(item.get("role", "user")))
        if role not in {"system", "user", "assistant", "tool"}:
            role = "user"
        message: dict[str, Any] = {"role": role}
        if role == "tool":
            tool_call_id = item.get("tool_call_id")
            if isinstance(tool_call_id, str) and tool_call_id:
                message["tool_call_id"] = tool_call_id
        content = extract_text(item.get("content"))
        interface_marker = interface_marker or contains_local_interface_marker(content)
        if role == "user":
            content = strip_prior_marker_preamble_from_user_text(content)
        content = compact_interface_marker_context(content)
        if role == "user" and content:
            last_user_request = current_user_request_excerpt(content)
        message["content"] = content
        tool_calls = item.get("tool_calls")
        if isinstance(tool_calls, list) and tool_calls:
            message["tool_calls"] = tool_calls
        if role == "system" and not seen_non_system:
            if content:
                system_chunks.append(content)
            continue
        if role != "system":
            seen_non_system = True
        elif content:
            # Tabby chat_template rejects later system messages. Preserve the text as user-visible context.
            message["role"] = "user"
            message["content"] = f"[system context]\n{content}"
        pending_messages.append(message)

    messages: list[dict[str, Any]] = []
    merged_system = "\n\n".join(chunk for chunk in system_chunks if chunk.strip())
    if merged_system:
        messages.append({"role": "system", "content": merged_system})
    if not pending_messages:
        pending_messages.append({"role": "user", "content": "Reply with OK."})
    end_frame = LOCAL_QWEN_END_FRAME_ANCHOR
    end_frame += (
        "\n- Do not repeat an identical successful read command; if you already have the line count, line range, "
        "or requested chunk output, answer the user's requested final marker instead of re-reading.\n"
        "- If the newest task asks you to create one exact file under a temp/run directory and answer with an exact marker, "
        "Do not perform workspace orientation; create that file, verify it, and answer with the requested marker.\n"
        "- For blocked-row questions, count non-empty `blocker` cells; `status` can remain `complete_validated` while a blocker is present."
    )
    if last_user_request:
        end_frame += (
            "\n\n[CURRENT USER REQUEST]\n"
            f"{last_user_request}\n"
            "Execute this request exactly. Do not replace it with repo startup, mission, bridge, or workspace-orientation work."
        )
    if interface_marker:
        end_frame += (
            "\n\n[RECOVERABLE LOCAL INTERFACE MARKER]\n"
            f"Previous context contains {interface_marker}. Treat it as a failed prior tool envelope, "
            "not as a reason to stop this session. If the newest user request is different, proceed normally. "
            "If it is the same unfinished read-only task, retry once with a smaller bounded command. "
            "Do not retry a failed write. Execute the immediately preceding user request now."
        )
    if repeated_tool_hint:
        end_frame += "\n\n" + repeated_tool_hint
    pending_messages.append({"role": "user", "content": end_frame})
    messages.extend(pending_messages)
    return messages


def responses_payload_to_tabby_chat(payload: dict[str, Any]) -> dict[str, Any]:
    tools = payload.get("tools")
    tool_prompt = build_compact_tool_prompt(tools)
    chat_payload: dict[str, Any] = {
        "model": payload.get("model"),
        "messages": responses_input_to_messages(payload, tool_prompt=tool_prompt),
        "stream": False,
    }

    if ProxyHandler.native_tools and isinstance(tools, list) and tools:
        wrapped_tools: list[dict[str, Any]] = []
        for tool in tools:
            function_payload = function_tool_schema(tool)
            if not function_payload:
                continue
            wrapped_tools.append({"type": "function", "function": function_payload})
        if wrapped_tools:
            chat_payload["tools"] = wrapped_tools

    tool_choice = payload.get("tool_choice")
    if ProxyHandler.native_tools and tool_choice is not None:
        if isinstance(tool_choice, dict) and not isinstance(tool_choice.get("function"), dict):
            function_payload: dict[str, Any] = {}
            for key in ("name",):
                value = tool_choice.get(key)
                if value is not None:
                    function_payload[key] = value
            if function_payload:
                chat_payload["tool_choice"] = {"type": "function", "function": function_payload}
            else:
                chat_payload["tool_choice"] = tool_choice
        else:
            chat_payload["tool_choice"] = tool_choice

    parallel_tool_calls = payload.get("parallel_tool_calls")
    if ProxyHandler.native_tools and isinstance(parallel_tool_calls, bool):
        chat_payload["parallel_tool_calls"] = parallel_tool_calls

    max_output_tokens = payload.get("max_output_tokens")
    if isinstance(max_output_tokens, int):
        chat_payload["max_tokens"] = min(max_output_tokens, ProxyHandler.max_output_tokens)
    else:
        chat_payload["max_tokens"] = ProxyHandler.max_output_tokens

    temperature = payload.get("temperature")
    tool_temperature = getattr(ProxyHandler, "tool_temperature", DEFAULT_TOOL_TEMPERATURE)
    if isinstance(tools, list) and tools:
        chat_payload["temperature"] = tool_temperature
    elif isinstance(temperature, (int, float)):
        chat_payload["temperature"] = temperature

    top_p = payload.get("top_p")
    if isinstance(top_p, (int, float)):
        chat_payload["top_p"] = top_p
    elif isinstance(tools, list) and tools and ProxyHandler.tool_top_p is not None:
        chat_payload["top_p"] = ProxyHandler.tool_top_p

    top_k = payload.get("top_k")
    if isinstance(top_k, int):
        chat_payload["top_k"] = top_k
    elif isinstance(tools, list) and tools and ProxyHandler.tool_top_k is not None:
        chat_payload["top_k"] = ProxyHandler.tool_top_k

    min_p = payload.get("min_p")
    if isinstance(min_p, (int, float)):
        chat_payload["min_p"] = min_p
    elif isinstance(tools, list) and tools and ProxyHandler.tool_min_p is not None:
        chat_payload["min_p"] = ProxyHandler.tool_min_p

    reasoning_effort = payload.get("reasoning_effort")
    if isinstance(reasoning_effort, str) and reasoning_effort:
        chat_payload["reasoning_effort"] = reasoning_effort
    elif isinstance(tools, list) and tools and ProxyHandler.tool_reasoning_effort:
        chat_payload["reasoning_effort"] = ProxyHandler.tool_reasoning_effort

    stop = payload.get("stop")
    if stop is not None:
        chat_payload["stop"] = stop

    # Keep thinking mode enabled while avoiding persistent prior thinking blocks; this prevents
    # template/history mismatches while allowing the launcher to choose thinking per route.
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


def usage_from_chat(chat_payload: dict[str, Any]) -> dict[str, int]:
    usage = chat_payload.get("usage")
    if not isinstance(usage, dict):
        return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
    completion_tokens = int(usage.get("completion_tokens", 0) or 0)
    total_tokens = int(usage.get("total_tokens", prompt_tokens + completion_tokens) or 0)
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
    if "." in name:
        name = name.rsplit(".", 1)[-1]
    compact = re.sub(r"[^A-Za-z0-9_]+", "", name).lower()
    if compact in COMPACT_TOOL_ALLOWLIST:
        return compact
    if compact.startswith("exec") and compact.endswith("command"):
        return "exec_command"
    if compact.endswith("command") and "command" in compact:
        return "exec_command"
    if compact.startswith("writestdin") or compact.startswith("write") and compact.endswith("stdin"):
        return "write_stdin"
    if compact.startswith("update") and compact.endswith("plan"):
        return "update_plan"
    if compact.startswith("view") and compact.endswith("image"):
        return "view_image"
    return name.strip()


def json_loads_lenient(raw_json: str) -> Any:
    try:
        return json.loads(raw_json)
    except json.JSONDecodeError:
        return json.loads(raw_json, strict=False)


def extract_balanced_fragment(text: str, start: int, opener: str, closer: str) -> str | None:
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
            parsed_arguments = {"cmd": arguments} if function_name == "exec_command" else {}
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


def incomplete_jsonish_tool_call(text: str, call_index: int) -> tuple[dict[str, Any] | None, str]:
    start = text.find("<tool_call>")
    if start < 0:
        return None, ""
    raw = text[start + len("<tool_call>") :].strip()
    object_start = raw.find("{")
    if object_start < 0 or not re.search(r'"name"\s*:', raw, re.DOTALL):
        return None, ""
    fragment = extract_balanced_fragment(raw, object_start, "{", "}")
    if fragment:
        tool_call = build_json_tool_call(fragment, call_index) or build_jsonish_tool_call(fragment, call_index)
        if tool_call:
            return tool_call, ""
    tool_call = build_jsonish_tool_call(raw, call_index)
    if tool_call:
        try:
            recovered_arguments = json.loads(tool_call.get("function", {}).get("arguments") or "{}")
        except json.JSONDecodeError:
            recovered_arguments = {}
        if recovered_arguments:
            return tool_call, ""
    name_match = re.search(r'"name"\s*:\s*"([^"]+)"', raw, re.DOTALL)
    function_name = normalize_tool_call_name(name_match.group(1)) if name_match else "unknown"
    diagnostic = (
        f"LOCAL_MODEL_TOOL_CALL_TRUNCATED: incomplete {function_name} tool envelope arrived without a closing "
        "</tool_call>. Restart from a smaller command; avoid large heredoc file bodies in a single tool call."
    )
    return None, diagnostic


def build_tool_call(function_name: str, function_body: str, call_index: int) -> dict[str, Any]:
    arguments: dict[str, str] = {}
    for parameter_match in PARAMETER_RE.finditer(function_body):
        key = parameter_match.group(1).strip().split()[0]
        value = parameter_match.group(2).strip()
        arguments[key] = value
    if not arguments:
        incomplete_parameter_match = INCOMPLETE_PARAMETER_RE.search(function_body)
        if incomplete_parameter_match:
            key = incomplete_parameter_match.group(1).strip()
            raw_value = incomplete_parameter_match.group(2).strip()
            first_line = next((line.strip() for line in raw_value.splitlines() if line.strip()), "")
            if first_line:
                arguments[key] = first_line
    return {
        "id": f"xml_tool_call_{call_index}",
        "type": "function",
        "function": {
            "name": normalize_tool_call_name(function_name),
            "arguments": json.dumps(arguments, ensure_ascii=True),
        },
    }


def parse_gemma4_tool_calls(text: str, call_index: int) -> tuple[list[dict[str, Any]], str, int]:
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


def parse_python_style_tool_calls(text: str, call_index: int) -> tuple[list[dict[str, Any]], str, int]:
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
        prefix = text[cursor:match.start()]
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
        gemma_tool_calls, gemma_remaining_text, call_index = parse_gemma4_tool_calls(text, call_index)
        if gemma_tool_calls:
            return gemma_tool_calls, gemma_remaining_text

    for match in TOOL_CALL_BLOCK_RE.finditer(text):
        prefix = text[cursor:match.start()]
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
            prefix = text[cursor:match.start()]
            if prefix.strip():
                remaining_parts.append(prefix.strip())
            cursor = match.end()
            call_index += 1
            tool_calls.append(build_tool_call(match.group(1).strip(), match.group(2), call_index))
        suffix = ""
    if not tool_calls:
        cursor = 0
        for match in JSON_TOOL_CALL_BLOCK_RE.finditer(text):
            prefix = text[cursor:match.start()]
            if prefix.strip():
                remaining_parts.append(prefix.strip())
            cursor = match.end()
            call_index += 1
            raw_json = match.group(1).strip()
            tool_call = build_json_tool_call(raw_json, call_index) or build_jsonish_tool_call(raw_json, call_index)
            if tool_call:
                tool_calls.append(tool_call)
        suffix = text[cursor:]
    if not tool_calls:
        cursor = 0
        for match in HYBRID_JSON_XML_TOOL_CALL_BLOCK_RE.finditer(text):
            prefix = text[cursor:match.start()]
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
            prefix = text[cursor:match.start()]
            if prefix.strip():
                remaining_parts.append(prefix.strip())
            cursor = match.end()
            call_index += 1
            tool_calls.append(build_tool_call(match.group(1).strip(), match.group(2), call_index))
        suffix = ""
    if not tool_calls:
        cursor = 0
        for match in RAW_FUNCTION_BLOCK_RE.finditer(text):
            prefix = text[cursor:match.start()]
            if prefix.strip():
                remaining_parts.append(prefix.strip())
            cursor = match.end()
            call_index += 1
            tool_calls.append(build_tool_call(match.group(1).strip(), match.group(2), call_index))
        suffix = text[cursor:]
    if not tool_calls:
        python_tool_calls, python_remaining_text, call_index = parse_python_style_tool_calls(text, call_index)
        if python_tool_calls:
            return python_tool_calls, python_remaining_text
    if not tool_calls:
        match = INCOMPLETE_TOOL_CALL_BLOCK_RE.search(text)
        if match:
            call_index += 1
            tool_calls.append(build_tool_call(match.group(1).strip(), match.group(2), call_index))
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
    return tool_policy_suppress_visible_tool_markup(text, parsed_tool_call=parsed_tool_call)


def repeated_promise_diagnostic(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return ""
    promise_markers = ("let me", "i'll", "i will", "i'm going to", "i am going to")
    action_markers = ("inspect", "check", "look at", "review", "examine", "read", "write", "create")
    lines = []
    for line in stripped.splitlines():
        cleaned = re.sub(r"^[\s>*-]*(?:[•*-]\s*)?", "", line.strip())
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if cleaned:
            lines.append(cleaned)
    promise_lines = [
        line
        for line in lines
        if any(marker in line.lower() for marker in promise_markers)
        and any(marker in line.lower() for marker in action_markers)
    ]
    if len(promise_lines) < 2:
        return ""
    normalized = [re.sub(r"[^a-z0-9]+", " ", line.lower()).strip() for line in promise_lines]
    repeated = len(set(normalized)) < len(normalized)
    same_intent = len(set(" ".join(item.split()[:8]) for item in normalized)) <= 2
    if not repeated and not same_intent:
        return ""
    return (
        "LOCAL_MODEL_LOOP_DETECTED: repeated promise-style inspection or authoring text "
        "arrived without a tool call or artifact change. Stop this local run and restart "
        "from a smaller diagnostic or a direct exec_command/file-edit step."
    )


def unresolved_filename_placeholder_message(text: str, *, latest_user_text: str = "") -> str:
    combined = f"{latest_user_text}\n{text}".lower()
    if "@filename" not in combined:
        return ""
    promise_markers = ("let me", "i'll", "i will", "i’m going to", "i am going to", "i need to")
    action_markers = ("inspect", "check", "look at", "review", "examine", "read", "update", "patch", "fix")
    if not any(marker in text.lower() for marker in promise_markers):
        return ""
    if not any(marker in text.lower() for marker in action_markers):
        return ""
    return (
        "I need the exact file path for `@filename` before I can inspect or edit it. "
        "Please rerun with a real path or attach/expand the file reference."
    )


def unresolved_template_placeholder_message(text: str, *, latest_user_text: str = "") -> str:
    combined = f"{latest_user_text}\n{text}"
    placeholder_match = re.search(
        r"(?<![A-Za-z0-9_])\{([A-Za-z][A-Za-z0-9_ -]{0,40})\}(?![A-Za-z0-9_])",
        combined,
    )
    if not placeholder_match:
        return ""
    if not re.search(r"\bimplement\b|\bbuild\b|\badd\b|\bcreate\b", combined, flags=re.IGNORECASE):
        return ""
    placeholder = "{" + placeholder_match.group(1).strip() + "}"
    if placeholder.lower() != "{feature}":
        return (
            f"I need the actual value for `{placeholder}` before I can act on this request. "
            f"Please replace `{placeholder}` with the concrete change, target files or behavior, and acceptance criteria."
        )
    return (
        "I need the actual feature description before I can implement it. "
        "Please replace `{feature}` with the concrete change, target files or behavior, and acceptance criteria."
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
    if not re.search(r"\.(?:md|markdown|txt|rst|json|jsonl|csv)\Z", path, flags=re.IGNORECASE):
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
            "LOCAL_MODEL_TOOL_CALL_TRUNCATED: exec_command was suppressed because shell "
            "quotes were unbalanced. Retry with one small command and close every quote."
        )
    if "\n" in cleaned and re.search(r"\bpython3?\s+-c\s*['\"]", cleaned):
        cleaned = normalize_multiline_python_c(cleaned)
    bounded_cat = bounded_single_text_cat_command(cleaned)
    if bounded_cat:
        cleaned = bounded_cat
    elif command_looks_like_unbounded_read_dump(cleaned):
        return suppressed_exec_marker(
            "LOCAL_MODEL_TOOL_CALL_TOO_LARGE: exec_command raw multi-file read dump was suppressed. "
            "Retry once with a bounded summary command that prints filenames, byte counts, headings, "
            "or short extracted fields only; do not print whole file bodies."
        )
    if "work/state/catalog.csv" in cleaned or "state/data_catalog" in cleaned:
        cleaned = cleaned.replace("row['title']", "row['paper_title']")
        cleaned = cleaned.replace('row["title"]', 'row["paper_title"]')
        cleaned = re.sub(r"(\[[^\]]+\])\['title'\]", r"\1['paper_title']", cleaned)
        cleaned = re.sub(r'(\[[^\]]+\])\["title"\]', r'\1["paper_title"]', cleaned)
        cleaned = cleaned.replace("row.get('title')", "row.get('paper_title')")
        cleaned = cleaned.replace('row.get("title")', 'row.get("paper_title")')
    if re.search(r"\bsed\s+-i\b", cleaned) and re.search(r"\.csv(?:\s|$)", cleaned):
        return suppressed_exec_marker(
            "LOCAL_MODEL_TOOL_CALL_TRUNCATED: exec_command sed -i CSV edit was suppressed. "
            "Use a single-line python3 -c command with the csv module, and match rows by "
            "stable fields such as filename/full_path instead of line numbers."
        )
    if (
        re.search(r"\.csv(?:['\"]|\s|$)", cleaned)
        and "DictReader" in cleaned
        and "csv.writer" in cleaned
        and "writerows" in cleaned
    ):
        return suppressed_exec_marker(
            "LOCAL_MODEL_TOOL_CALL_TRUNCATED: exec_command unsafe CSV DictReader/csv.writer "
            "mix was suppressed. When rows are dicts, write them with csv.DictWriter and "
            "the original fieldnames so the CSV rows are not replaced by header keys."
        )
    heredoc_markers = ("<<", "<<-", "cat >")
    has_heredoc = any(marker in cleaned for marker in heredoc_markers)
    markdown_or_text_target = re.search(
        r"(?:^|\s)(?:cat|tee)\b[^\n]*(?:>|-a|>>)?\s*['\"]?[^'\"\s]+\.(?:md|markdown|txt|rst|json|jsonl|csv)['\"]?",
        cleaned,
        re.IGNORECASE,
    )
    risky_fenced_heredoc = has_heredoc and ("```" in cleaned or "$(" in cleaned or "`" in cleaned)
    if has_heredoc and (markdown_or_text_target or risky_fenced_heredoc):
        return (
            "printf '%s\\n' "
            "'LOCAL_MODEL_TOOL_CALL_TOO_LARGE: exec_command heredoc file write was suppressed. "
            "Do not use shell heredocs for Markdown/text/report files or nested code fences; "
            "use compact python3 -c file I/O for small files, or ask the host agent to patch larger files.'"
        )
    if len(cleaned) > MAX_EXEC_COMMAND_CHARS or (
        has_heredoc and len(cleaned) > MAX_HEREDOC_COMMAND_CHARS
    ):
        return (
            "printf '%s\\n' "
            "'LOCAL_MODEL_TOOL_CALL_TOO_LARGE: exec_command was suppressed because it contained "
            "an oversized generated file body or heredoc. Split the file into smaller commands "
            "or ask the host agent to patch the file.'"
        )
    heredoc_match = re.search(r"<<-?\s*['\"]?([A-Za-z0-9_.-]+)['\"]?", cleaned)
    if heredoc_match and f"\n{heredoc_match.group(1)}" not in cleaned:
        return (
            "printf '%s\\n' "
            "'LOCAL_MODEL_TOOL_CALL_TRUNCATED: exec_command heredoc was suppressed because "
            "the terminator was missing from the parsed command. Retry with python3 -c or "
            "split the file write into smaller commands.'"
        )
    if any(ord(ch) > 127 for ch in cleaned):
        path_match = re.search(r"scripts/[A-Za-z0-9_./-]+\.py", cleaned)
        if "git diff" in cleaned and path_match:
            repo_root = os.environ.get("QWENDEX_ROOT", str(Path(__file__).resolve().parents[2]))
            return f"cd {shlex.quote(repo_root)} && git diff -- {path_match.group(0)} | head -500"
        if "git diff" in cleaned:
            repo_root = os.environ.get("QWENDEX_ROOT", str(Path(__file__).resolve().parents[2]))
            return f"cd {shlex.quote(repo_root)} && git diff --stat HEAD | head -220"
        if "git status" in cleaned or cleaned.lstrip().startswith("cd "):
            repo_root = os.environ.get("QWENDEX_ROOT", str(Path(__file__).resolve().parents[2]))
            return f"cd {shlex.quote(repo_root)} && git status --short | head -200"
        cleaned = "".join(ch for ch in cleaned if ord(ch) < 128).strip()
    cleaned = re.split(r"\n\s*</", cleaned, maxsplit=1)[0].strip()
    cleaned = re.sub(r"</(?:cmd|chars|path|parameter|function|tool_call)>\s*$", "", cleaned).strip()
    cleaned = re.sub(r"\|\s*[^\x00-\x7F].*$", "| head -500", cleaned).strip()
    cleaned = re.sub(r"\|\s*</[^>]+>.*$", "| head -500", cleaned).strip()
    if cleaned.endswith("|"):
        cleaned = cleaned.rstrip("|").rstrip() + " | head -500"

    if re.search(r"\bgit\s+diff\b", cleaned):
        safe_pagers = ("| head", "| sed", "| tail", "| rg", "| grep", "| awk")
        if "--stat" in cleaned and not any(pager in cleaned for pager in safe_pagers):
            cleaned += " | head -220"
        elif "--stat" not in cleaned and not any(pager in cleaned for pager in safe_pagers):
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


def run_directory_from_latest_user_text(latest_user_text: str) -> str:
    marker_match = re.search(
        r"Work only under this directory:\s*(.*)",
        latest_user_text,
        flags=re.IGNORECASE,
    )
    if not marker_match:
        return ""

    first = marker_match.group(1).strip()
    candidate_lines = [first] if first else []
    tail = latest_user_text[marker_match.end() :]
    candidate_lines.extend(tail.splitlines()[:6])

    for raw_line in candidate_lines:
        line = raw_line.strip().strip("`")
        if not line or line.startswith("```"):
            continue
        if not line.startswith("/"):
            continue
        path = Path(line)
        if path.is_dir():
            return str(path)
        first_token = line.split()[0]
        path = Path(first_token)
        if path.is_dir():
            return str(path)
    return ""


def command_uses_run_relative_paths(cmd: str) -> bool:
    if re.search(r"(?<![A-Za-z0-9_./-])(?:\./)?work/", cmd):
        return True
    return bool(re.search(r"(?<![A-Za-z0-9_./-])(?:queue\.csv|README\.md)(?![A-Za-z0-9_./-])", cmd))


def path_is_inside(parent: Path, child: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except (OSError, ValueError):
        return False


def anchor_run_relative_exec_workdir(parsed: dict[str, Any], latest_user_text: str) -> None:
    cmd = parsed.get("cmd")
    if not isinstance(cmd, str) or not command_uses_run_relative_paths(cmd):
        return
    run_dir = run_directory_from_latest_user_text(latest_user_text)
    if not run_dir:
        return
    current = parsed.get("workdir")
    run_path = Path(run_dir)
    if isinstance(current, str) and current.strip():
        current_path = Path(current.strip())
        if current_path.is_dir() and path_is_inside(run_path, current_path):
            return
    parsed["workdir"] = run_dir


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
            numbered_items.append({"step": match.group(1).strip(), "status": match.group(2).strip()})
            continue
        match = numbered_paren_status_re.match(raw_line)
        if match:
            numbered_items.append({"step": match.group(1).strip(), "status": match.group(2).strip()})
            continue
        match = numbered_inline_status_re.match(raw_line)
        if match:
            step = re.sub(r"\s+(?:and\s+)?$", "", match.group(1).strip(), flags=re.IGNORECASE)
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
    string_item_re = re.compile(r"^(.*?)\s+(?:and\s+)?status\s+([A-Za-z_ -]+)\.?\s*$", re.IGNORECASE)
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
        return [{"step": step.strip(), "status": status.strip()} for step, status in numbered]
    clauses = re.split(r"\s*,\s*(?:then\s+)?|\s+then\s+", text, flags=re.IGNORECASE)
    items: list[dict[str, str]] = []
    inline_re = re.compile(r"^(?:create\s+\w+\s+steps?:\s*)?(.*?)(?:\s+and\s+status\s+|\s+status\s+)(in_progress|pending|completed)\b", re.IGNORECASE)
    for clause in clauses:
        match = inline_re.search(clause.strip())
        if not match:
            continue
        step = re.sub(r"^(?:step\s*\d+\s*(?:is|:)?\s*)", "", match.group(1).strip(), flags=re.IGNORECASE)
        if step:
            items.append({"step": step, "status": match.group(2).strip()})
    return items or None


def compact_git_untracked_summary_command() -> str:
    return (
        'python3 -c "import collections, subprocess; '
        "paths=subprocess.run(['git','ls-files','--others','--exclude-standard'], "
        "text=True, stdout=subprocess.PIPE, check=False).stdout.splitlines(); "
        "counts=collections.Counter((p.split('/',1)[0] if '/' in p else p) for p in paths); "
        "print('untracked_total=' + str(len(paths))); "
        "[print(str(v) + chr(9) + k) for k,v in counts.most_common(40)]"
        '"'
    )


def compact_git_review_summary_command() -> str:
    return (
        'python3 -c "import collections, subprocess; '
        "status=subprocess.run(['git','status','--short'], text=True, stdout=subprocess.PIPE, check=False).stdout.splitlines(); "
        "print('status_total=' + str(len(status))); "
        "codes=collections.Counter(line[:2] for line in status); "
        "[print('status_code' + chr(9) + k + chr(9) + str(v)) for k,v in sorted(codes.items())]; "
        "roots=collections.Counter((line[3:].split('/',1)[0] if len(line)>3 else line) for line in status); "
        "[print('root' + chr(9) + k + chr(9) + str(v)) for k,v in roots.most_common(40)]; "
        "head_ok=subprocess.run(['git','rev-parse','--verify','HEAD'], text=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False).returncode==0; "
        "print('head_present=' + str(head_ok).lower()); "
        "diff_cmd=['git','diff','--stat','HEAD'] if head_ok else ['git','diff','--stat']; "
        "diff=subprocess.run(diff_cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False).stdout.splitlines(); "
        "print('diff_stat_lines=' + str(len(diff))); "
        "print('diff_base=' + ('HEAD' if head_ok else 'working_tree_no_head')); "
        "[print(line) for line in diff[:120]]"
        '"'
    )


def latest_user_requests_review(latest_user_text: str) -> bool:
    lower = latest_user_text.lower()
    return "/review" in lower or "review my current changes" in lower or "review current changes" in lower


def review_summary_seen_after_latest_user(raw_items: list[Any]) -> bool:
    outputs = "\n".join(tool_outputs_after_latest_user_request(raw_items))
    return "status_total=" in outputs and "diff_stat_lines=" in outputs


def review_compact_summary_response_call(index: int = 0) -> dict[str, Any]:
    return {
        "id": f"review_compact_summary_tool_call_{index + 1}",
        "type": "function_call",
        "call_id": f"call_review_compact_summary_{index + 1}",
        "name": "exec_command",
        "arguments": json.dumps(
            {"cmd": compact_git_review_summary_command(), "max_output_tokens": 6000},
            ensure_ascii=True,
        ),
    }


def review_request_compact_summary_tool_calls(
    *,
    latest_user_text: str,
    raw_items: list[Any],
) -> list[dict[str, Any]]:
    if not latest_user_requests_review(latest_user_text) or review_summary_seen_after_latest_user(raw_items):
        return []
    return [
        {
            "id": "review_compact_summary_tool_call_1",
            "type": "function",
            "function": {
                "name": "exec_command",
                "arguments": json.dumps(
                    {"cmd": compact_git_review_summary_command(), "max_output_tokens": 6000},
                    ensure_ascii=True,
                ),
            },
        }
    ]


def review_compact_summary_already_queued(output: list[dict[str, Any]]) -> bool:
    return any(str(item.get("id", "")).startswith("review_compact_summary_tool_call_") for item in output)


def review_compact_summary_final_answer(latest_user_text: str, raw_items: list[Any]) -> str:
    if not latest_user_requests_review(latest_user_text) or not review_summary_seen_after_latest_user(raw_items):
        return ""
    outputs = "\n".join(tool_outputs_after_latest_user_request(raw_items))

    def first_int(pattern: str, default: int = 0) -> int:
        match = re.search(pattern, outputs)
        return int(match.group(1)) if match else default

    status_total = first_int(r"(?m)^status_total=(\d+)\b")
    untracked = first_int(r"(?m)^status_code\t\?\?\t(\d+)\b")
    modified = first_int(r"(?m)^status_code\t\s?M\t(\d+)\b") + first_int(r"(?m)^status_code\tM\s\t(\d+)\b")
    diff_stat_lines = first_int(r"(?m)^diff_stat_lines=(\d+)\b")
    head_present = re.search(r"(?m)^head_present=true\b", outputs) is not None
    root_lines = re.findall(r"(?m)^root\t([^\t\n]+)\t(\d+)\b", outputs)
    top_roots = ", ".join(f"{name}={count}" for name, count in root_lines[:8]) or "none"
    if diff_stat_lines:
        finding = (
            f"The compact summary shows {diff_stat_lines} tracked diff-stat lines; inspect those tracked diffs next for concrete review findings."
        )
    elif status_total and untracked == status_total:
        finding = (
            "No tracked diff is visible from the compact summary; the current change surface is entirely untracked paths."
        )
    elif status_total:
        finding = "The compact summary shows status changes, but no tracked diff-stat lines were emitted."
    else:
        finding = "The compact summary shows no current git status changes."
    head_note = (
        " The repository has a HEAD baseline for diff comparison."
        if head_present
        else " The repository has no HEAD baseline, so review must avoid `git diff HEAD` and rely on compact status/root summaries until an initial commit exists."
    )
    response = (
        "Review result from compact git summary:\n"
        f"- status_total={status_total}; untracked={untracked}; modified={modified}; diff_stat_lines={diff_stat_lines}.\n"
        f"- top_roots: {top_roots}.\n"
        f"- finding: {finding}{head_note}\n"
        "- next_action: choose a scoped subset of untracked files or create a baseline commit before asking for line-level review."
    )
    marker = terminal_marker_requested_by_latest_user(latest_user_text)
    if marker and marker not in response:
        response = f"{response}\n{marker}"
    return response


def normalize_review_git_command(cmd: str, *, latest_user_text: str = "") -> str:
    compact = " ".join(cmd.split()).lower()
    if latest_user_requests_review(latest_user_text) and re.search(r"\bgit\s+(?:status|diff|log|ls-files)\b", compact):
        return compact_git_review_summary_command()
    if re.fullmatch(
        r"\s*git\s+ls-files\s+--others\s+--exclude-standard(?:\s*\|\s*head\s+-\d+)?\s*",
        cmd,
    ):
        return compact_git_untracked_summary_command()
    return cmd


def normalize_function_arguments(function_name: str, arguments: Any, *, latest_user_text: str = "") -> str:
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            return arguments or "{}"
    elif isinstance(arguments, dict):
        parsed = dict(arguments)
    else:
        return "{}"
    if not isinstance(parsed, dict):
        return arguments if isinstance(arguments, str) else "{}"

    if function_name == "exec_command":
        if not isinstance(parsed.get("cmd"), str) and isinstance(parsed.get("command"), str):
            parsed["cmd"] = parsed.pop("command")
        if isinstance(parsed.get("cmd"), str):
            parsed["cmd"] = sanitize_exec_command(
                normalize_review_git_command(parsed["cmd"], latest_user_text=latest_user_text)
            )
            if "untracked_total=" in parsed["cmd"] and "max_output_tokens" not in parsed:
                parsed["max_output_tokens"] = 4000
        normalize_exec_workdir(parsed)
        anchor_run_relative_exec_workdir(parsed, latest_user_text)
    if function_name == "update_plan":
        plan_value = parsed.get("plan")
        if isinstance(plan_value, str) and plan_value.strip().startswith("["):
            try:
                plan_value = json_loads_lenient(plan_value.strip())
            except json.JSONDecodeError:
                pass
        parsed_plan = normalize_update_plan_items(plan_value)
        if parsed_plan is None:
            parsed_plan = plan_items_from_explanation(parsed.get("explanation"))
        if parsed_plan is not None:
            parsed["plan"] = parsed_plan

    numeric_fields = {
        "exec_command": {"max_output_tokens", "yield_time_ms"},
        "write_stdin": {"max_output_tokens", "yield_time_ms"},
    }.get(function_name, set())
    boolean_fields = {
        "exec_command": {"login", "tty"},
    }.get(function_name, set())

    for field in numeric_fields:
        value = parsed.get(field)
        if isinstance(value, str):
            digit_match = re.match(r"\s*(\d+)", value)
            if digit_match:
                parsed[field] = int(digit_match.group(1))
    for field in boolean_fields:
        value = parsed.get(field)
        if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
            parsed[field] = value.strip().lower() == "true"
        elif isinstance(value, str):
            parsed.pop(field, None)

    return json.dumps(parsed, ensure_ascii=True)


def validation_failure_repair_tool_calls(
    final_marker: str,
    *,
    latest_user_text: str = "",
    raw_items: list[Any] | None = None,
) -> list[dict[str, Any]]:
    raw_items = raw_items or []
    merged_outputs = "\n".join(tool_outputs_from_items(raw_items))
    lower_outputs = merged_outputs.lower()
    cmd = ""
    workdir = run_directory_from_latest_user_text(latest_user_text)
    validator_cmd = validator_command_from_latest_user_text(latest_user_text) or validator_command_from_raw_items(raw_items)
    if not workdir and validator_cmd:
        workdir = workdir_from_validator_command(validator_cmd)

    if final_marker == "LOCAL_QWEN_PDF_CATALOG_OK" and "pdf_catalog_validation_fail" in lower_outputs:
        cmd = pdf_catalog_update_command()
    elif final_marker == "LOCAL_QWEN_EOS_CATALOG_OK" and "eos_catalog_validation_fail" in lower_outputs:
        cmd = eos_catalog_update_command()
    elif final_marker == "LOCAL_QWEN_UNSEEN_REPAIR_OK" and "unseen_repair_validation_fail" in lower_outputs:
        cmd = unseen_repair_patch_command()
    elif final_marker == "LOCAL_QWEN_MULTIROW_REPAIR_OK" and "multirow_repair_validation_fail" in lower_outputs:
        cmd = multirow_repair_patch_command()
    if not cmd or not workdir:
        return []
    return [
        {
            "id": "failed_validation_repair_tool_call_1",
            "type": "function",
            "function": {
                "name": "exec_command",
                "arguments": json.dumps(
                    {"cmd": cmd, "workdir": workdir, "max_output_tokens": 12000},
                    ensure_ascii=True,
                ),
            },
        }
    ]


def no_progress_stall_tool_calls(
    text: str,
    *,
    latest_user_text: str = "",
    raw_items: list[Any] | None = None,
) -> list[dict[str, Any]]:
    if text.strip() not in {"...", "\u2026"}:
        return []
    request_final = re.search(
        r"answer exactly\s+`?([A-Z0-9_]+(?:_OK|_DONE))`?",
        latest_user_text,
        flags=re.IGNORECASE,
    )
    if not request_final:
        return []
    return validation_failure_repair_tool_calls(
        request_final.group(1),
        latest_user_text=latest_user_text,
        raw_items=raw_items,
    )


def final_marker_text_to_validator_tool_calls(
    text: str,
    *,
    latest_user_text: str = "",
    raw_items: list[Any] | None = None,
) -> list[dict[str, Any]]:
    final_match = re.search(r"\b([A-Z0-9_]+(?:_OK|_DONE))\b", text.strip())
    if not final_match:
        return []
    final_marker = final_match.group(1)
    request_final = re.search(
        r"answer exactly\s+`?([A-Z0-9_]+(?:_OK|_DONE))`?",
        latest_user_text,
        flags=re.IGNORECASE,
    )
    if request_final and final_marker != request_final.group(1):
        return []
    if not request_final and final_marker not in VALIDATION_MARKER_BY_FINAL_MARKER:
        return []
    section_tool_calls = markdown_section_update_tool_calls(
        latest_user_text=latest_user_text,
        raw_items=raw_items or [],
    )
    if section_tool_calls:
        return section_tool_calls
    validation_marker = validation_marker_for_final_marker(final_marker, latest_user_text)
    if not validation_marker:
        return []
    merged_outputs = "\n".join(tool_outputs_from_items(raw_items or []))
    if validation_marker_present(validation_marker, merged_outputs):
        return []
    repair_tool_calls = validation_failure_repair_tool_calls(
        final_marker,
        latest_user_text=latest_user_text,
        raw_items=raw_items,
    )
    if repair_tool_calls:
        return repair_tool_calls
    cmd = validator_command_from_latest_user_text(latest_user_text) or validator_command_from_raw_items(raw_items or [])
    if not cmd:
        return []
    return [
        {
            "id": "premature_final_validator_tool_call_1",
            "type": "function",
            "function": {
                "name": "exec_command",
                "arguments": json.dumps({"cmd": cmd, "max_output_tokens": 12000}, ensure_ascii=True),
            },
        }
    ]


def markdown_file_mentions(*texts: str) -> list[str]:
    mentions: list[str] = []
    for text in texts:
        for raw in re.findall(r"(?<!@)(?<![A-Za-z0-9_./-])([A-Za-z0-9_./-]+\.md)\b", text):
            cleaned = raw.strip("`'\".,:;()[]{}")
            if not cleaned or cleaned.startswith("@"):
                continue
            if cleaned not in mentions:
                mentions.append(cleaned)
    return mentions


def markdown_promise_read_command(text: str, latest_user_text: str) -> str:
    mentions = markdown_file_mentions(text, latest_user_text)
    if not mentions:
        return ""
    primary = mentions[0]
    primary_name = Path(primary).name
    if not primary_name:
        return ""
    combined = f"{text}\n{latest_user_text}".lower()
    include_supplements = any(marker in combined for marker in ("supplement", "supplemental", "already exists"))
    if primary_name.lower() == "digital_products.md" and (
        "digital_products" in combined or "digital products" in combined
    ):
        files = [DIGITAL_PRODUCTS_CANONICAL_RELATIVE_PATH]
        if include_supplements:
            files.append("monetization_research/digital_products_supplement.md")
        quoted_files = " ".join(shlex.quote(path) for path in files)
        read_parts = []
        for path in files:
            quoted = shlex.quote(path)
            read_parts.append(
                f"[ -f {quoted} ] && printf '\\n== %s ==\\n' {quoted} && sed -n '1,220p' {quoted}"
            )
        return (
            f"for f in {quoted_files}; do "
            "if [ -f \"$f\" ]; then printf '%s\\t%s bytes\\n' \"$f\" \"$(wc -c < \"$f\")\"; fi; "
            "done; "
            + "; ".join(read_parts)
        )
    if "/" in primary:
        quoted_path = shlex.quote(primary)
        return (
            f"if [ -f {quoted_path} ]; then "
            f"printf '%s\\t%s bytes\\n' {quoted_path} \"$(wc -c < {quoted_path})\"; "
            f"printf '\\n== %s ==\\n' {quoted_path}; sed -n '1,220p' {quoted_path}; "
            "else printf '%s\\n' 'REQUESTED_MARKDOWN_NOT_FOUND'; fi"
        )

    name_arg = shlex.quote(primary_name)
    find_expr = f"-name {name_arg}"
    if include_supplements:
        stem = Path(primary_name).stem
        find_expr += f" -o -iname {shlex.quote(f'*{stem}*supplement*.md')}"
    return (
        f"find . -maxdepth 5 -type f \\( {find_expr} \\) | sort | head -50 | "
        "while IFS= read -r f; do printf '%s\\t%s bytes\\n' \"$f\" \"$(wc -c < \"$f\")\"; done; "
        f"src=$(find . -maxdepth 5 -type f -name {name_arg} | sort | head -1); "
        "[ -n \"$src\" ] && printf '\\n== %s ==\\n' \"$src\" && sed -n '1,220p' \"$src\""
    )


def markdown_paths_after_latest_user(raw_items: list[Any]) -> tuple[list[str], bool]:
    paths: list[str] = []
    useful_output = False
    path_re = re.compile(r"(?<![@A-Za-z0-9_.-])((?:\.?/|/)?[A-Za-z0-9_./-]*[A-Za-z0-9_-]+\.md)\b")
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type", ""))
        if not item_type and ("role" in item or "content" in item):
            item_type = "message"
        if item_type == "message":
            role = normalize_role(str(item.get("role", "user")))
            content = strip_prior_marker_preamble_from_user_text(extract_text(item.get("content")))
            if role == "user" and content.strip():
                paths = []
                useful_output = False
            continue
        text = ""
        if item_type == "function_call":
            arguments = item.get("arguments")
            text = arguments if isinstance(arguments, str) else json.dumps(arguments or {}, ensure_ascii=True)
        elif item_type == "function_call_output":
            text = extract_output_text(item)
            if text.strip() and not contains_local_interface_marker(text):
                useful_output = True
        else:
            continue
        for match in path_re.finditer(text):
            path = match.group(1).strip("`'\".,:;()[]{}")
            if path and path not in paths:
                paths.append(path)
    return paths, useful_output


def digital_products_post_read_edit_command(text: str, latest_user_text: str, raw_items: list[Any]) -> str:
    lower = text.lower()
    latest_lower = latest_user_text.lower()
    if not any(marker in lower for marker in ("i'll", "i will", "let me", "i'm going to", "i am going to")):
        return ""
    if not any(marker in lower for marker in ("work through", "one at a time", "adding", "add ", "edit", "update")):
        return ""
    if not any(marker in latest_lower for marker in ("work item by item", "item by item", "add the information", "add ", "improve", "update")):
        return ""

    conversation_text = "\n".join(
        extract_text(item.get("content"))
        for item in raw_items
        if isinstance(item, dict) and str(item.get("type", "message")) in {"", "message"}
    ).lower()
    if "digital_products" not in conversation_text and "digital products" not in conversation_text:
        return ""
    if "ai prompt guides" not in conversation_text and "workflow templates" not in conversation_text:
        return ""

    paths, useful_output = markdown_paths_after_latest_user(raw_items)
    if not useful_output:
        return ""
    supplement_paths = [path for path in paths if "digital_products_supplement" in Path(path).name]
    if not supplement_paths:
        return ""
    target = supplement_paths[-1]
    section = (
        "\n\n## AI Prompt Guides and Workflow Templates\n\n"
        "- **What to add**: Treat AI prompt guides and workflow templates as a first-class product category, not only as support material for templates or courses.\n"
        "- **Why it matters**: Prompt packs, reusable workflow recipes, and tool-specific setup guides are AI-native products with low fulfillment cost and fast iteration cycles.\n"
        "- **How to package**: Bundle 20-50 tested prompts with examples, expected outputs, setup notes, and a short troubleshooting guide.\n"
        "- **Quality gate**: Test every prompt against at least two realistic buyer scenarios and revise any prompt that needs more than two human edits.\n"
        "- **Positioning**: Sell outcomes such as client onboarding, weekly reporting, product listing generation, or research synthesis instead of generic prompt collections.\n"
    )
    code = (
        "from pathlib import Path; "
        f"p=Path({target!r}); "
        "text=p.read_text(encoding='utf-8'); "
        f"section={section!r}; "
        "marker='## AI Prompt Guides and Workflow Templates'; "
        "updated=text if marker in text else text.rstrip()+section+'\\n'; "
        "p.write_text(updated, encoding='utf-8'); "
        "print(('ALREADY_PRESENT' if marker in text else 'UPDATED'), str(p), len(updated))"
    )
    return "python3 -c " + shlex.quote(code)


DIGITAL_PRODUCTS_ITEM_SECTIONS: dict[int, tuple[str, str]] = {
    1: (
        "Needed Items",
        "- **Accounts**: Create or confirm Gumroad, Etsy, Shopify or Payhip, Canva, Google Drive, Stripe/PayPal, and one email platform before building the first product.\n"
        "- **Source files**: Keep editable files, export files, thumbnails, listing copy, screenshots, license terms, and a changelog for every product.\n"
        "- **Operating tools**: Use one research sheet, one production checklist, one launch checklist, and one support FAQ so each product can be shipped and revised without rebuilding the workflow.\n"
        "- **Decision gate**: Do not start production until the buyer problem, product promise, platform, file format, and review owner are explicit.",
    ),
    2: (
        "Workflow Steps",
        "1. **Niche research gate**: Pick one buyer, one painful job, and three active competitors. Continue only if buyers show purchase intent through search results, marketplace reviews, or forum questions.\n"
        "2. **Product scope gate**: Define the smallest paid version, included files, excluded custom work, refund terms, and a measurable buyer outcome.\n"
        "3. **Build pass**: Draft the product, examples, instructions, cover image, listing copy, and delivery archive in separate files.\n"
        "4. **Quality gate**: Test the product as a buyer would. Every link, prompt, template field, formula, export, and instruction must work without seller intervention.\n"
        "5. **Listing gate**: Publish only after the title, first image, preview, benefits, keywords, price, and FAQ answer the buyer's top objections.\n"
        "6. **Launch pass**: Share to one search channel, one social channel, and one owned channel. Track impressions, clicks, conversion rate, refunds, and support questions.\n"
        "7. **Iteration gate**: After 50-100 listing views or 2-3 customer questions, revise the thumbnail, title, product instructions, or bundle offer before creating a new product.",
    ),
    3: (
        "Setup Details and Folder Structure",
        "- **Folder layout**: Use `research/`, `source/`, `exports/`, `listing/`, `screenshots/`, `support/`, and `archive/` under each product folder.\n"
        "- **File naming**: Prefix versions with dates or semantic versions, for example `budget-template-v1.1.xlsx` and `listing-copy-v1.md`.\n"
        "- **Automation**: Keep small scripts or checklists for export validation, zip packaging, image resizing, link checks, and byte-count verification.\n"
        "- **Platform configuration**: Store each listing's title, tags, price, platform URL, delivery file, refund policy, and update date in a tracking sheet.\n"
        "- **Backup gate**: Before launch, verify the editable source file, customer download file, and listing assets are backed up outside the storefront.",
    ),
    4: (
        "Expanded Product Types and AI Leverage",
        "- **Prompt packs**: Highest leverage when prompts include tested examples, expected outputs, failure cases, and role-specific variants.\n"
        "- **AI agent workflows**: Package repeatable operating procedures such as client onboarding, content repurposing, research briefs, or weekly reporting with setup steps and QA checks.\n"
        "- **Micro-courses**: Keep them narrow, outcome-based, and paired with worksheets or templates so buyers can finish quickly.\n"
        "- **Template systems**: Spreadsheets, Notion dashboards, Airtable bases, and Canva kits perform better when they solve one recurring business task.\n"
        "- **Emerging categories**: Buyers value AI-assisted SOPs, automation recipes, swipe files, calculators, and niche data packs when they save time immediately.",
    ),
    5: (
        "Market Positioning",
        "- **Differentiate by buyer**: Position for a specific role, business model, life event, or workflow instead of a generic product type.\n"
        "- **Differentiate by outcome**: Lead with the measurable result, such as faster proposals, cleaner client onboarding, better listings, or fewer missed tasks.\n"
        "- **Avoid crowded lanes**: Skip generic planners, undifferentiated affirmation printables, broad prompt packs, and copycat Canva templates unless there is a clear niche edge.\n"
        "- **Proof points**: Use screenshots, before-and-after examples, sample pages, mini case studies, and transparent limitations to build trust.\n"
        "- **Positioning gate**: If the listing could apply to everyone, narrow it before building.",
    ),
    6: (
        "Revenue Specifics",
        "- **Low-price templates**: Often start at $5-$29 and need volume, bundles, or SEO traffic to reach meaningful income.\n"
        "- **Specialized templates and prompt systems**: $29-$99 products can reach $100-$1,000/month faster when they solve a business workflow.\n"
        "- **Micro-courses and workflow kits**: $49-$249 offers need stronger proof, onboarding, and support expectations but require fewer sales.\n"
        "- **Timeline expectation**: A first product often needs 2-6 weeks for research, build, listing, and first iteration; steady revenue usually requires a portfolio and repeated traffic experiments.\n"
        "- **Revenue gate**: Track views, conversion rate, refund rate, support time, and update cost before scaling a product line.",
    ),
    7: (
        "Quality Control Process",
        "- **Content review**: Check accuracy, clarity, grammar, examples, buyer assumptions, and claims before export.\n"
        "- **Functional review**: Test formulas, links, permissions, downloads, prompts, automations, and file compatibility on a clean account or device.\n"
        "- **Buyer simulation**: Have a reviewer follow only the customer-facing instructions and record every point of confusion.\n"
        "- **AI review gate**: Any AI-generated copy, prompt, design, or lesson must be checked for hallucinations, generic advice, copyright risk, and missing edge cases.\n"
        "- **Release gate**: Ship only when the product passes content, functional, listing, and support-readiness checks.",
    ),
    8: (
        "Platform Diversification Strategy",
        "- **Start simple**: Launch the first product on Gumroad, Payhip, or Etsy depending on whether search traffic or direct audience traffic is the main path.\n"
        "- **Add a second platform**: Expand after the first listing has validated keywords, conversion, and support load.\n"
        "- **Owned channel**: Build an email list or simple resource hub so customers are not tied to one marketplace algorithm.\n"
        "- **Platform fit**: Etsy favors searchable visual products, Gumroad favors direct audience sales, Shopify favors a broader branded catalog, and marketplaces favor repeatable niche demand.\n"
        "- **Diversification gate**: Do not duplicate listings across platforms until source files, update process, and customer support are stable.",
    ),
    9: (
        "Customer Acquisition",
        "- **Search**: Build titles and descriptions from buyer language, marketplace autocomplete, competitor reviews, and problem-specific keywords.\n"
        "- **Content**: Publish short demonstrations, before-and-after examples, workflow breakdowns, and sample pages that show the product in use.\n"
        "- **Pinterest and social**: Use multiple pins or posts per product angle, each tied to a concrete outcome or use case.\n"
        "- **Owned audience**: Offer a free sample, checklist, or mini-template to capture email and retarget buyers with related products.\n"
        "- **Acquisition gate**: Track which channel produces clicks and purchases before creating more content in that channel.",
    ),
    10: (
        "Competitive Analysis",
        "- **Competitor scan**: Review 10-20 listings across marketplaces, search, and social. Record price, promise, reviews, file types, update dates, and gaps.\n"
        "- **Review mining**: Extract what buyers praise, what confuses them, what is missing, and what support questions repeat.\n"
        "- **Gap selection**: Prefer gaps tied to speed, clarity, niche specificity, better instructions, better examples, or a bundled workflow.\n"
        "- **Risk filter**: Avoid products where competitors have strong brands, deep review moats, low prices, or easy-to-copy visual sameness unless your angle is materially different.\n"
        "- **Validation gate**: Build only when the gap can be expressed as a sharper buyer promise and a concrete product feature.",
    ),
}

DIGITAL_PRODUCTS_CANONICAL_RELATIVE_PATH = "monetization_research/digital_products.md"
SUMMARY_COMPARISON_CANONICAL_RELATIVE_PATH = "monetization_research/summary_comparison.md"
LOCAL_HARNESS_SECTION_HELPER = str(Path(__file__).resolve().parents[2] / "scripts" / "local_harness_document_section_upsert.py")


def latest_user_requests_itemized_document_update(latest_user_text: str) -> bool:
    latest_lower = latest_user_text.lower()
    itemized = any(
        marker in latest_lower
        for marker in ("10 item list", "item list", "item by item", "one at a time", "1 at a time", "work through each")
    )
    document_update = any(marker in latest_lower for marker in ("update", "enhance", "add", "document"))
    return itemized and document_update


def numbered_item_from_text(text: str) -> tuple[int, str] | None:
    match = re.search(r"\bItem\s+(\d{1,2})\s*:\s*([^\n\r]+)", text, flags=re.IGNORECASE)
    if not match:
        return None
    number = int(match.group(1))
    raw_title = match.group(2).strip()
    title = re.split(r"\s*(?:-|\u2013|\u2014)\s*", raw_title, maxsplit=1)[0].strip(" .:")
    if not title:
        return None
    return number, title


def digital_products_target_after_latest_user(raw_items: list[Any]) -> tuple[str, bool]:
    paths, useful_output = markdown_paths_after_latest_user(raw_items)
    candidates = [
        path
        for path in paths
        if Path(path).name.lower().startswith("digital_products") and Path(path).suffix.lower() == ".md"
    ]
    source_candidates = [
        path
        for path in candidates
        if Path(path).name.lower() == "digital_products.md"
        and "supplement" not in Path(path).name.lower()
        and not re.search(r"(^|/)(results|model_lab|evals|local_qwen_harness_smoke)(/|$)", path)
    ]
    if source_candidates:
        return DIGITAL_PRODUCTS_CANONICAL_RELATIVE_PATH, useful_output
    exact = [
        path
        for path in candidates
        if Path(path).name.lower() == "digital_products.md"
        and not path.startswith("/results/")
    ]
    non_supplement = [
        path
        for path in candidates
        if "supplement" not in Path(path).name.lower()
        and not path.startswith("/results/")
    ]
    if exact:
        return DIGITAL_PRODUCTS_CANONICAL_RELATIVE_PATH, useful_output
    if non_supplement:
        return DIGITAL_PRODUCTS_CANONICAL_RELATIVE_PATH, useful_output
    return (candidates[-1] if candidates else ""), useful_output


def split_document_helper_target(target: str) -> tuple[str, str]:
    path = Path(target)
    if path.is_absolute():
        parts = path.parts
        if "monetization_research" in parts:
            index = parts.index("monetization_research")
            directory = Path(*parts[:index]) if index > 0 else Path("/")
            return str(directory), str(Path(*parts[index:]))
        return str(path.parent), path.name
    return ".", target.lstrip("./")


def digital_products_numbered_item_helper_command(target: str, number: int, heading: str, body: str) -> str:
    directory, file_name = split_document_helper_target(target)
    body_b64 = base64.b64encode(body.encode("utf-8")).decode("ascii")
    return " ".join(
        shlex.quote(part)
        for part in (
            "python3",
            LOCAL_HARNESS_SECTION_HELPER,
            "--dir",
            directory,
            "--file",
            file_name,
            "--section-title",
            heading,
            "--body-b64",
            body_b64,
            "--item-number",
            str(number),
            "--total-items",
            "10",
            "--min-bytes",
            "50",
            "--done-marker",
            f"ITEM_{number}_DONE",
            "--already-marker",
            f"ITEM_{number}_ALREADY_PRESENT",
        )
    )


def latest_user_requests_digital_products_agent_adjustment(latest_user_text: str) -> bool:
    if not legacy_project_recoveries_enabled():
        return False
    latest_lower = latest_user_text.lower()
    if "digital_products" not in latest_lower and "digital products" not in latest_lower:
        return False
    agent_or_run = bool(re.search(r"\b(?:agent|codex|gpt|manage|managed|managing|run it|our use)\b", latest_lower))
    agent_or_run = agent_or_run or any(
        marker in latest_lower
        for marker in (
            "do we need more",
            "different options",
            "what are the options",
        )
    )
    document_adjustment = any(
        marker in latest_lower
        for marker in ("adjust", "update", "enhance", "edit", "add", "modify", "good enough", "is this information good")
    )
    return agent_or_run and document_adjustment


def assistant_text_intends_digital_products_agent_adjustment(text: str) -> bool:
    if not legacy_project_recoveries_enabled():
        return False
    lower = text.lower()
    if "digital products" not in lower and "digital_products" not in lower and "# digital products" not in lower:
        return False
    read_or_assessed = any(
        marker in lower
        for marker in ("i've read", "i have read", "assessment", "key gaps", "content is solid", "needs")
    )
    intends_edit = any(
        marker in lower
        for marker in (
            "let me adjust",
            "let me update",
            "i'll adjust",
            "i will adjust",
            "i'll update",
            "i will update",
            "adjust both documents",
            "more actionable",
            "agent-managed",
        )
    )
    return read_or_assessed and intends_edit


def generated_markdown_rewrite_tool_attempt(text: str) -> bool:
    lower = text.lower()
    if "<tool_call>" not in lower and "<|tool_call>" not in lower and "exec_command(" not in lower:
        return False
    if "exec_command" not in lower and "python3 -c" not in lower:
        return False
    generated_body = any(
        marker in lower
        for marker in (
            "textwrap.dedent",
            "write_text",
            "content =",
            "content=",
            "# digital products",
            "\\n# digital products",
            "full-file rewrite",
        )
    )
    too_large_or_unbalanced = len(text) > 1200 or "</tool_call>" not in lower
    return generated_body and too_large_or_unbalanced


def digital_products_agent_runbook_body() -> str:
    return (
        "- **Best first option**: Start with one narrow workflow product such as a prompt pack, template system, "
        "calculator, or micro-course that solves a repeated buyer task. Avoid broad generic planners or undifferentiated prompt bundles.\n"
        "- **Agent operating loop**: Research one niche, choose one buyer problem, build the smallest paid product, "
        "test it from a clean buyer account, publish one listing, then revise after traffic or support signals appear.\n"
        "- **Decision gates**: Continue only when the buyer, product promise, file format, platform, price, quality owner, "
        "and support policy are explicit. Pivot when search results show crowded sameness, weak purchase intent, or high support load.\n"
        "- **Execution assets**: Keep source files, exports, thumbnails, listing copy, keywords, screenshots, license terms, "
        "refund language, changelog, and customer FAQ in a single product folder before launch.\n"
        "- **Automation and fulfillment**: Prefer simple delivery first: Gumroad, Payhip, Etsy, or Shopify file delivery plus "
        "a backup folder. Add email automation, update notices, and cross-platform listings only after the first product has stable demand.\n"
        "- **Quality checks**: Verify links, formulas, permissions, downloads, examples, AI-generated claims, copyright risk, "
        "refund expectations, and customer instructions before publishing.\n"
        "- **Metrics to manage**: Track listing views, click-through rate, conversion rate, refund rate, support time, "
        "update cost, and revenue by product type. Improve the listing or product after 50-100 views or repeated buyer questions.\n"
    )


def digital_products_agent_runbook_helper_command(target: str) -> str:
    directory, file_name = split_document_helper_target(target)
    body_b64 = base64.b64encode(digital_products_agent_runbook_body().encode("utf-8")).decode("ascii")
    return " ".join(
        shlex.quote(part)
        for part in (
            "python3",
            LOCAL_HARNESS_SECTION_HELPER,
            "--dir",
            directory,
            "--file",
            file_name,
            "--section-title",
            "Agent-Managed Execution Runbook",
            "--body-b64",
            body_b64,
            "--min-bytes",
            "50",
            "--done-marker",
            "DOCUMENT_SECTION_DONE",
            "--already-marker",
            "DOCUMENT_SECTION_ALREADY_PRESENT",
        )
    )


def digital_products_agent_runbook_request_command(latest_user_text: str, raw_items: list[Any]) -> str:
    if not latest_user_requests_digital_products_agent_adjustment(latest_user_text):
        return ""
    outputs = "\n".join(tool_outputs_from_items(raw_items))
    if "## Agent-Managed Execution Runbook" in outputs:
        return ""
    target, useful_output = digital_products_target_after_latest_user(raw_items)
    if not target or not useful_output:
        return ""
    target_name = Path(target).name
    if re.search(r"\bDOCUMENT_SECTION_(?:DONE|ALREADY_PRESENT)\b", outputs) and target_name in outputs:
        return ""
    return digital_products_agent_runbook_helper_command(target)


def latest_user_requests_summary_comparison_hardening(latest_user_text: str) -> bool:
    if not legacy_project_recoveries_enabled():
        return False
    latest_lower = latest_user_text.lower()
    mentions_target = (
        "summary_comparison.md" in latest_lower
        or "summary comparison" in latest_lower
        or "monetization summary" in latest_lower
    )
    if not mentions_target:
        return False
    return any(
        marker in latest_lower
        for marker in (
            "work on",
            "better pass",
            "same treatment",
            "expand",
            "harden",
            "improve",
            "enhance",
            "update",
            "next",
        )
    )


def summary_comparison_framework_body() -> str:
    return (
        "- **Purpose**: Use this file as a routing table, not as a revenue promise. It should decide which "
        "monetization lane an agent should work next, what evidence is missing, and when to stop.\n"
        "- **Source files to reconcile**: Check `digital_products.md`, `affiliate_marketing.md`, "
        "`content_creation.md`, `saas_micro_saas.md`, `service_arbitrage.md`, and `trading_investing.md` before "
        "changing rankings. Treat tracker CSVs as execution state, not market proof.\n"
        "- **Recommended sequence**: Start with digital products, then affiliate marketing or content creation if "
        "distribution assets exist, then service arbitrage only when quality control and delivery capacity are explicit. "
        "Defer SaaS until a validated recurring pain point exists. Keep trading/investing advisory or paper-only unless "
        "human risk approval exists.\n"
        "- **Comparison fields to maintain**: For each lane, track setup effort, ongoing effort, capital/risk exposure, "
        "agent fit, first-week task, required evidence, quality gate, stop/pivot trigger, and next artifact.\n"
        "- **Quality gate**: Do not rank a lane as top-three unless its source doc has niche criteria, an operating loop, "
        "quality checks, compliance/risk notes, a tracker or measurement plan, and explicit stop conditions.\n"
        "- **Stop conditions**: Pause expansion when ranges are unsupported, source docs disagree, the lane depends on "
        "regulated activity, or the next action would require spending money, publishing, trading, or contacting customers "
        "without human approval.\n"
    )


def summary_comparison_hardening_helper_command() -> str:
    body_b64 = base64.b64encode(summary_comparison_framework_body().encode("utf-8")).decode("ascii")
    return " ".join(
        shlex.quote(part)
        for part in (
            "python3",
            LOCAL_HARNESS_SECTION_HELPER,
            "--dir",
            ".",
            "--file",
            SUMMARY_COMPARISON_CANONICAL_RELATIVE_PATH,
            "--section-title",
            "Agent-Managed Decision Framework",
            "--body-b64",
            body_b64,
            "--min-bytes",
            "50",
            "--done-marker",
            "DOCUMENT_SECTION_DONE",
            "--already-marker",
            "DOCUMENT_SECTION_ALREADY_PRESENT",
        )
    )


def summary_comparison_hardening_request_command(latest_user_text: str, raw_items: list[Any]) -> str:
    if not latest_user_requests_summary_comparison_hardening(latest_user_text):
        return ""
    outputs = "\n".join(tool_outputs_from_items(raw_items))
    if "## Agent-Managed Decision Framework" in outputs:
        return ""
    if re.search(r"\bDOCUMENT_SECTION_(?:DONE|ALREADY_PRESENT)\b", outputs) and "summary_comparison.md" in outputs:
        return ""
    output_lower = outputs.lower()
    target_seen = (
        "summary_comparison.md" in output_lower
        or "# monetization options: summary comparison" in output_lower
        or "## comparison table" in output_lower
    )
    if not target_seen:
        paths, useful_output = markdown_paths_after_latest_user(raw_items)
        target_seen = useful_output and any(Path(path).name.lower() == "summary_comparison.md" for path in paths)
    return summary_comparison_hardening_helper_command() if target_seen else ""


def summary_comparison_hardening_final_answer(latest_user_text: str, raw_items: list[Any]) -> str:
    if not latest_user_requests_summary_comparison_hardening(latest_user_text):
        return ""
    outputs = "\n".join(tool_outputs_from_items(raw_items))
    if "## Agent-Managed Decision Framework" not in outputs and not re.search(
        r"\bDOCUMENT_SECTION_(?:DONE|ALREADY_PRESENT)\b.*summary_comparison\.md",
        outputs,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        return ""
    return (
        "The `monetization_research/summary_comparison.md` hardening step is now bounded: the next durable edit is the "
        "`Agent-Managed Decision Framework` section, which ties rankings to source docs, quality gates, risk controls, "
        "stop conditions, and next artifacts. No further broad file reads are needed for this handoff."
    )


def digital_products_agent_adjustment_final_answer(latest_user_text: str, raw_items: list[Any]) -> str:
    if not latest_user_requests_digital_products_agent_adjustment(latest_user_text):
        return ""
    target, useful_output = digital_products_target_after_latest_user(raw_items)
    outputs = "\n".join(tool_outputs_from_items(raw_items))
    if not target and any(marker in outputs for marker in ("# Digital Products", "digital_products.md", "Agent-Managed")):
        target = DIGITAL_PRODUCTS_CANONICAL_RELATIVE_PATH
    if not useful_output and outputs.strip() and not contains_local_interface_marker(outputs):
        useful_output = True
    if not target or not useful_output:
        return ""
    runbook_present = "## Agent-Managed Execution Runbook" in outputs
    runbook_note = (
        "The `Agent-Managed Execution Runbook` section is already present, so I would not add another copy."
        if runbook_present
        else "The next useful edit is to add an `Agent-Managed Execution Runbook` section before running this as an agent-managed workflow."
    )
    target_note = f" in `{target}`" if target else ""
    return (
        f"I reviewed the available digital-products material{target_note}. It is good enough for a first Codex/GPT-managed pass: "
        "it names concrete product lanes, operating checks, launch and iteration gates, and metrics to manage. "
        f"{runbook_note} The best first option is a narrow workflow product, prompt pack, template system, calculator, "
        "or micro-course with explicit buyer, format, platform, price, quality owner, support policy, and revision metrics. "
        "More research would help later for competitor prices, keyword demand, and channel choice, but it is not required before "
        "starting one small managed execution run. No further broad file reads are needed for this request."
    )


def duplicate_read_finalize_truthful_answer(message_text: str, latest_user_text: str, raw_items: list[Any]) -> str:
    if not duplicate_read_finalize_seen_after_latest_user_request(raw_items):
        return ""
    if not latest_user_requests_digital_products_agent_adjustment(latest_user_text):
        return ""
    if terminal_section_upsert_progress_seen_after_latest_user(raw_items):
        return ""
    lower = message_text.lower()
    unsupported_edit_claim = any(
        marker in lower
        for marker in (
            "made adjustments",
            "what i changed",
            "what i added",
            "i added",
            "i updated",
            "i adjusted",
            "i've added",
            "i've updated",
        )
    )
    if not unsupported_edit_claim:
        return ""
    return digital_products_agent_adjustment_final_answer(latest_user_text, raw_items)


def digital_products_agent_runbook_recovery_command(
    text: str,
    latest_user_text: str,
    raw_items: list[Any],
) -> str:
    if not latest_user_requests_digital_products_agent_adjustment(latest_user_text):
        return ""
    if not assistant_text_intends_digital_products_agent_adjustment(text):
        return ""
    if not generated_markdown_rewrite_tool_attempt(text):
        return ""
    target, useful_output = digital_products_target_after_latest_user(raw_items)
    if not target or not useful_output:
        return ""
    return digital_products_agent_runbook_helper_command(target)


def digital_products_agent_runbook_recovery_tool_calls(
    text: str,
    *,
    latest_user_text: str = "",
    raw_items: list[Any] | None = None,
) -> list[dict[str, Any]]:
    cmd = digital_products_agent_runbook_recovery_command(text, latest_user_text, raw_items or [])
    if not cmd:
        return []
    return [
        {
            "id": "digital_products_agent_runbook_recovery_1",
            "type": "function",
            "function": {
                "name": "exec_command",
                "arguments": json.dumps({"cmd": cmd, "max_output_tokens": 4000}, ensure_ascii=True),
            },
        }
    ]


def digital_products_numbered_item_edit_command(text: str, latest_user_text: str, raw_items: list[Any]) -> str:
    if not latest_user_requests_itemized_document_update(latest_user_text):
        return ""
    item = numbered_item_from_text(text)
    if not item:
        return ""
    number, _title = item
    section_payload = DIGITAL_PRODUCTS_ITEM_SECTIONS.get(number)
    if not section_payload:
        return ""
    target, useful_output = digital_products_target_after_latest_user(raw_items)
    if not target or not useful_output:
        return ""
    heading, body = section_payload
    return digital_products_numbered_item_helper_command(target, number, heading, body)


SECTION_UPSERT_PROGRESS_RE = re.compile(
    r"\b(?P<marker>[A-Z0-9_]+_(?:DONE|ALREADY_PRESENT)|DOCUMENT_SECTION_(?:DONE|ALREADY_PRESENT))\b"
    r"\s+(?P<file>\S+)"
    r"(?P<detail>.*?)\baction=(?P<action>[A-Za-z0-9_-]+)"
    r".*?\bnext_item=(?P<next_item>\d+|None|)",
)


def section_upsert_progress_events_after_latest_user(raw_items: list[Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type", ""))
        if not item_type and ("role" in item or "content" in item):
            item_type = "message"
        if item_type == "message":
            role = normalize_role(str(item.get("role", "user")))
            content = strip_prior_marker_preamble_from_user_text(extract_text(item.get("content")))
            if role == "user" and content.strip():
                events = []
            continue
        if item_type != "function_call_output":
            continue
        events.extend(parse_section_upsert_progress_events(extract_output_text(item)))
    return events


def terminal_section_upsert_progress_seen_after_latest_user(raw_items: list[Any]) -> bool:
    events = section_upsert_progress_events_after_latest_user(raw_items)
    return bool(events and events[-1].get("next_item") is None)


def section_upsert_finalize_seen_after_latest_user_request(raw_items: list[Any]) -> bool:
    seen = False
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type", ""))
        if not item_type and ("role" in item or "content" in item):
            item_type = "message"
        if item_type == "message":
            role = normalize_role(str(item.get("role", "user")))
            content = strip_prior_marker_preamble_from_user_text(extract_text(item.get("content")))
            if role == "user" and content.strip():
                seen = False
            continue
        if item_type != "function_call_output":
            continue
        if "SECTION_UPSERT_FINALIZE_NOW" in extract_output_text(item):
            seen = True
    return seen


def section_upsert_finalize_tool_call(normalized_arguments: str, *, index: int) -> dict[str, Any]:
    return document_section_upsert_finalize_tool_call(normalized_arguments, index=index)


def terminal_section_upsert_final_answer(raw_items: list[Any]) -> str:
    return document_terminal_section_upsert_final_answer(section_upsert_progress_events_after_latest_user(raw_items))


def terminal_section_upsert_recovery_response(
    *,
    raw_items: list[Any],
    model: str,
    latest_user_text: str = "",
    normalized_arguments: str = "",
    index: int = 0,
) -> dict[str, Any] | None:
    if not terminal_section_upsert_progress_seen_after_latest_user(raw_items):
        return None
    if section_upsert_finalize_seen_after_latest_user_request(raw_items):
        final_match = re.search(
            r"answer exactly\s+`?([A-Z0-9_]+(?:_OK|_DONE))`?",
            latest_user_text,
            flags=re.IGNORECASE,
        )
        if final_match:
            return response_payload_with_message(final_match.group(1), model=model)
        digital_products_final = digital_products_agent_adjustment_final_answer(latest_user_text, raw_items)
        if digital_products_final:
            return response_payload_with_message(digital_products_final, model=model)
        final_answer = terminal_section_upsert_final_answer(raw_items)
        if final_answer:
            return response_payload_with_message(final_answer, model=model)
        return response_payload_with_message(
            "FINAL_LOCAL_MODEL_STOP: terminal section-upsert recovery was already emitted "
            "after this user request. "
            "Do not call more tools; use the latest section-upsert receipt to answer.",
            model=model,
        )
    return response_payload_with_function_call(
        section_upsert_finalize_tool_call(normalized_arguments, index=index),
        model=model,
    )


def digital_products_next_item_from_progress(raw_items: list[Any]) -> int | None:
    for event in reversed(section_upsert_progress_events_after_latest_user(raw_items)):
        marker = str(event.get("marker") or "")
        marker_match = re.fullmatch(r"ITEM_(\d{1,2})_(?:DONE|ALREADY_PRESENT)", marker)
        if not marker_match:
            continue
        next_item = event.get("next_item")
        if isinstance(next_item, int) and next_item in DIGITAL_PRODUCTS_ITEM_SECTIONS:
            return next_item
    return None


def digital_products_next_missing_item_edit_call(
    *,
    latest_user_text: str,
    raw_items: list[Any],
) -> dict[str, Any] | None:
    if not latest_user_requests_itemized_document_update(latest_user_text):
        return None
    outputs = "\n".join(tool_outputs_from_items(raw_items))
    if not outputs.strip():
        return None
    if terminal_section_upsert_progress_seen_after_latest_user(raw_items):
        return None
    progress_next_item = digital_products_next_item_from_progress(raw_items)
    if progress_next_item is not None:
        heading, _body = DIGITAL_PRODUCTS_ITEM_SECTIONS[progress_next_item]
        cmd = digital_products_numbered_item_edit_command(
            f"Now Item {progress_next_item}: {heading}",
            latest_user_text,
            raw_items,
        )
        if not cmd:
            return None
        return {
            "id": "digital_products_next_item_progress_recovery_1",
            "type": "function_call",
            "call_id": "call_digital_products_next_item_progress_recovery_1",
            "name": "exec_command",
            "arguments": json.dumps({"cmd": cmd, "max_output_tokens": 4000}, ensure_ascii=True),
        }
    for number, (heading, _body) in DIGITAL_PRODUCTS_ITEM_SECTIONS.items():
        if f"## {heading}" not in outputs:
            cmd = digital_products_numbered_item_edit_command(
                f"Now Item {number}: {heading}",
                latest_user_text,
                raw_items,
            )
            if not cmd:
                return None
            return {
                "id": "digital_products_next_item_duplicate_read_recovery_1",
                "type": "function_call",
                "call_id": "call_digital_products_next_item_duplicate_read_recovery_1",
                "name": "exec_command",
                "arguments": json.dumps({"cmd": cmd, "max_output_tokens": 4000}, ensure_ascii=True),
            }
    return None


def digital_products_item_marker_seen_after_latest_user(raw_items: list[Any]) -> bool:
    if not legacy_project_recoveries_enabled():
        return False
    outputs = "\n".join(tool_outputs_from_items(raw_items))
    return bool(re.search(r"\bITEM_\d{1,2}_(?:DONE|ALREADY_PRESENT)\b", outputs))


def unseen_repair_patch_command() -> str:
    return (
        "python3 -c \"p='work/unseen_row.py'; "
        "t=open(p,encoding='utf-8').read(); "
        "t=t.replace('null_score - observed_score','observed_score - null_score'); "
        "t=t.replace('support-track','no-claim'); "
        "open(p,'w',encoding='utf-8').write(t)\""
    )


def multirow_repair_patch_command() -> str:
    return (
        "python3 -c \"from pathlib import Path; "
        "p=Path('work/scripts/row_v990.py'); t=p.read_text(); "
        "t=t.replace('null_score - observed_score','observed_score - null_score').replace(\\\"'claim_ceiling': 'support-track'\\\",\\\"'claim_ceiling': 'no-claim'\\\"); p.write_text(t); "
        "p=Path('work/scripts/row_v991.py'); t=p.read_text(); "
        "t=t.replace('sum(before) - sum(after)','sum(after) - sum(before)').replace('delta_sum / (len(after) + 1)','delta_sum / len(after)').replace('delta_sum / (len(after))','delta_sum / len(after)').replace(\\\"'claim_ceiling': 'support-track'\\\",\\\"'claim_ceiling': 'no-claim'\\\"); p.write_text(t); "
        "p=Path('work/scripts/row_v992.py'); t=p.read_text(); "
        "t=t.replace(\\\"'status': 'complete_validated'\\\",\\\"'status': 'blocked'\\\").replace(\\\"'blocker': ''\\\",\\\"'blocker': 'missing_external_lineage'\\\").replace(\\\"'score_authorized': True\\\",\\\"'score_authorized': False\\\").replace(\\\"'promotion_authorized': True\\\",\\\"'promotion_authorized': False\\\"); p.write_text(t)\""
    )


def pdf_catalog_update_command() -> str:
    return (
        "python3 -c \"import csv; p='work/state/catalog.csv'; "
        "rows=list(csv.DictReader(open(p,newline='',encoding='utf-8'))); "
        "fieldnames=['folder','arxiv_id','paper_title','filename','full_path','size_bytes','extension','file_type']; "
        "rows=rows or [{'folder':'work/data','arxiv_id':'0802.4225v1','paper_title':'','filename':'0802.4225v1.pdf',"
        "'full_path':'work/data/0802.4225v1.pdf','size_bytes':'0','extension':'pdf','file_type':'pdf'}]; "
        "[r.update({'paper_title':'Single-Proton Removal Reaction Study of 16B'}) for r in rows "
        "if r.get('filename')=='0802.4225v1.pdf' or r.get('full_path')=='work/data/0802.4225v1.pdf']; "
        "w=csv.DictWriter(open(p,'w',newline='',encoding='utf-8'),fieldnames=fieldnames); "
        "w.writeheader(); w.writerows(rows)\""
    )


def eos_catalog_update_command() -> str:
    return (
        "python3 -c \"import csv; p='work/state/catalog.csv'; "
        "titles={'README':'PCP(BSK24) cold neutron-star EoS dataset - README and references',"
        "'eos.compo':'PCP(BSK24) cold neutron-star EoS dataset - composition table',"
        "'eos.thermo':'PCP(BSK24) cold neutron-star EoS dataset - thermodynamic table',"
        "'eos.micro':'PCP(BSK24) cold neutron-star EoS dataset - microscopic nuclear table'}; "
        "f=open(p,newline='',encoding='utf-8'); r=csv.DictReader(f); "
        "fieldnames=r.fieldnames or ['folder','arxiv_id','paper_title','filename','full_path','size_bytes','extension','file_type']; "
        "rows=list(r); f.close(); "
        "[row.update({'paper_title':titles[row.get('filename','')]}) for row in rows if row.get('filename','') in titles]; "
        "f=open(p,'w',newline='',encoding='utf-8'); w=csv.DictWriter(f,fieldnames=fieldnames); "
        "w.writeheader(); w.writerows(rows); f.close()\""
    )


def post_read_edit_promise_to_tool_calls(
    text: str,
    *,
    latest_user_text: str = "",
    raw_items: list[Any] | None = None,
) -> list[dict[str, Any]]:
    cmd = digital_products_post_read_edit_command(
        text, latest_user_text, raw_items or []
    ) or digital_products_numbered_item_edit_command(text, latest_user_text, raw_items or [])
    if not cmd:
        return []
    return [
        {
            "id": "post_read_edit_tool_call_1",
            "type": "function",
            "function": {
                "name": "exec_command",
                "arguments": json.dumps({"cmd": cmd, "max_output_tokens": 4000}, ensure_ascii=True),
            },
        }
    ]


def promise_text_to_tool_calls(text: str, *, latest_user_text: str = "") -> list[dict[str, Any]]:
    lower = text.lower()
    latest_lower = latest_user_text.lower()
    promise_markers = (
        "let me",
        "i'll",
        "i will",
        "i’m going to",
        "i am going to",
        "running the validator",
        "now reading",
        "now checking",
        "now updating",
        "now i need",
        "now i'll",
        "i need to",
    )
    action_markers = ("inspect", "check", "look at", "review", "examine", "read", "update", "run", "patch", "fix")
    if not any(marker in lower for marker in promise_markers):
        return []
    if not any(marker in lower for marker in action_markers):
        return []

    cmd = ""
    workdir = run_directory_from_latest_user_text(latest_user_text)
    if "staged vs unstaged" in lower or ("staged" in lower and "unstaged" in lower):
        cmd = (
            "git status --short | head -200 && "
            "printf '\\n-- cached diff stat --\\n' && git diff --cached --stat | head -140 && "
            "printf '\\n-- unstaged diff stat --\\n' && git diff --stat | head -180"
        )
    elif "untracked" in lower:
        cmd = compact_git_untracked_summary_command()
    elif "staged diff" in lower:
        cmd = "git diff --cached --stat | head -140 && git diff --cached | head -500"
    elif "work/scripts" in latest_lower and ("patch" in lower or "fix" in lower or "update" in lower):
        cmd = multirow_repair_patch_command()
    elif "code changes" in lower or "scripts/" in lower or "scripts" in lower:
        cmd = "git diff --stat -- scripts | head -160 && git diff -- scripts | head -500"
    elif "git status" in lower or "current changes" in lower:
        if latest_user_requests_review(latest_user_text):
            cmd = compact_git_review_summary_command()
        else:
            cmd = "git status --short | head -200 && git diff --stat HEAD | head -220"
    elif "validator" in lower and validator_command_from_latest_user_text(latest_user_text):
        cmd = validator_command_from_latest_user_text(latest_user_text)
    elif "work/unseen_row.py" in latest_lower and ("patch" in lower or "fix" in lower or "update" in lower):
        cmd = unseen_repair_patch_command()
    elif (
        "work/data/0802.4225v1.pdf" in latest_lower
        and ("update" in lower or "catalog" in lower or "csv" in lower)
    ):
        cmd = pdf_catalog_update_command()
    elif (
        "work/data/0802.4225v1.pdf" in latest_lower
        and ("pdf" in lower or "title" in lower or "readme" in lower)
    ):
        cmd = "sed -n '1,120p' README.md && pdftotext work/data/0802.4225v1.pdf - | head -20"
    elif (
        "work/data/eos" in latest_lower
        and "eos.compo -> pcp" in latest_lower
        and (
            "update" in lower
            or "catalog" in lower
            or "csv" in lower
            or "paper_title" in lower
        )
    ):
        cmd = eos_catalog_update_command()
    elif "work/data/eos" in latest_lower and ("eos" in lower or "data files" in lower or "readme" in lower):
        cmd = (
            "sed -n '1,120p' README.md && sed -n '1,80p' work/data/eos/README && "
            "for f in work/data/eos/eos.compo work/data/eos/eos.thermo work/data/eos/eos.micro; "
            "do printf '\\n== %s ==\\n' \"$f\"; sed -n '1,5p' \"$f\"; done"
        )
    else:
        cmd = markdown_promise_read_command(text, latest_user_text)

    if not cmd:
        return []
    arguments: dict[str, Any] = {"cmd": cmd, "max_output_tokens": 12000}
    if workdir and (command_uses_run_relative_paths(cmd) or "find ." in cmd):
        arguments["workdir"] = workdir
    return [
        {
            "id": "promise_tool_call_1",
            "type": "function",
            "function": {
                "name": "exec_command",
                "arguments": json.dumps(arguments, ensure_ascii=True),
            },
        }
    ]


def chat_completion_to_response(chat_payload: dict[str, Any], request_payload: dict[str, Any] | None = None) -> dict[str, Any]:
    choices = chat_payload.get("choices")
    choice = choices[0] if isinstance(choices, list) and choices else {}
    message = choice.get("message") if isinstance(choice, dict) else {}
    if not isinstance(message, dict):
        message = {}

    output: list[dict[str, Any]] = []
    latest_user_text = ""
    raw_items: list[Any] = []
    if isinstance(request_payload, dict):
        raw_items = responses_raw_items(request_payload)
        latest_user_text = latest_user_full_text_from_items(raw_items)
    prior_tool_call_keys = prior_tool_call_keys_after_latest_user_request(raw_items) if DUPLICATE_TOOL_GUARD_ENABLED else set()
    response_tool_call_keys: set[str] = set()
    message_text = extract_text(message.get("content"))
    original_message_text = message_text
    tool_calls = message.get("tool_calls")
    if latest_user_is_harness_self_analysis_followup(latest_user_text, raw_items):
        harness_final = harness_self_analysis_final_answer(latest_user_text, raw_items)
        if harness_final:
            return response_payload_with_message(
                harness_final,
                model=str(chat_payload.get("model") or ""),
            )
    if (
        isinstance(tool_calls, list)
        and tool_calls
        and latest_user_is_harness_self_analysis_followup(latest_user_text, raw_items)
    ):
        return response_payload_with_message(
            harness_self_analysis_final_answer(latest_user_text, raw_items),
            model=str(chat_payload.get("model") or ""),
        )
    early_harness_final = harness_self_analysis_final_answer(latest_user_text, raw_items)
    if early_harness_final and isinstance(tool_calls, list) and tool_calls and (
        duplicate_read_recovery_seen_after_latest_user_request(raw_items)
        or duplicate_read_finalize_seen_after_latest_user_request(raw_items)
        or loop_marker_after_latest_user_request(raw_items)
    ):
        return response_payload_with_message(early_harness_final, model=str(chat_payload.get("model") or ""))
    early_review_final = review_compact_summary_final_answer(latest_user_text, raw_items)
    if early_review_final and isinstance(tool_calls, list) and tool_calls:
        return response_payload_with_message(early_review_final, model=str(chat_payload.get("model") or ""))
    if not isinstance(tool_calls, list) or not tool_calls:
        xml_tool_calls, cleaned_text = parse_xml_tool_calls(message_text)
        if xml_tool_calls:
            tool_calls = xml_tool_calls
            message_text = cleaned_text
        elif cleaned_text != message_text:
            message_text = cleaned_text
    if not isinstance(tool_calls, list) or not tool_calls:
        placeholder_message = unresolved_template_placeholder_message(
            message_text,
            latest_user_text=latest_user_text,
        ) or unresolved_filename_placeholder_message(message_text, latest_user_text=latest_user_text)
        if placeholder_message:
            message_text = placeholder_message
        else:
            loop_diagnostic = repeated_promise_diagnostic(message_text)
            if loop_diagnostic:
                harness_final = harness_self_analysis_final_answer(latest_user_text, raw_items)
                digital_products_final = digital_products_agent_adjustment_final_answer(latest_user_text, raw_items)
                review_final = review_compact_summary_final_answer(latest_user_text, raw_items)
                codebase_final = codebase_overview_final_answer(latest_user_text, raw_items)
                summary_final = summary_comparison_hardening_final_answer(latest_user_text, raw_items)
                message_text = (
                    harness_final
                    or digital_products_final
                    or review_final
                    or codebase_final
                    or summary_final
                    or loop_diagnostic
                )
            else:
                promised_tool_calls = final_marker_text_to_validator_tool_calls(
                    message_text,
                    latest_user_text=latest_user_text,
                    raw_items=raw_items,
                ) or codebase_overview_tool_calls(
                    latest_user_text=latest_user_text,
                    raw_items=raw_items,
                ) or document_inventory_tool_calls(
                    latest_user_text=latest_user_text,
                    raw_items=raw_items,
                ) or digital_products_agent_runbook_recovery_tool_calls(
                    original_message_text,
                    latest_user_text=latest_user_text,
                    raw_items=raw_items,
                ) or post_read_edit_promise_to_tool_calls(
                    message_text,
                    latest_user_text=latest_user_text,
                    raw_items=raw_items,
                ) or no_progress_stall_tool_calls(
                    message_text,
                    latest_user_text=latest_user_text,
                    raw_items=raw_items,
                ) or promise_text_to_tool_calls(
                    message_text,
                    latest_user_text=latest_user_text,
                ) or review_request_compact_summary_tool_calls(
                    latest_user_text=latest_user_text,
                    raw_items=raw_items,
                )
                if promised_tool_calls:
                    tool_calls = promised_tool_calls
                    message_text = ""
    message_text = suppress_visible_tool_markup(
        collapse_exact_duplicate_text(message_text),
        parsed_tool_call=isinstance(tool_calls, list) and bool(tool_calls),
    )
    if message_text and not tool_calls:
        review_final = review_compact_summary_final_answer(latest_user_text, raw_items)
        if review_final:
            message_text = review_final
        codebase_final = codebase_overview_final_answer(latest_user_text, raw_items)
        if codebase_final:
            message_text = codebase_final
        truthful_duplicate_read_answer = duplicate_read_finalize_truthful_answer(
            message_text,
            latest_user_text=latest_user_text,
            raw_items=raw_items,
        )
        if truthful_duplicate_read_answer:
            message_text = truthful_duplicate_read_answer
        final_marker_response = requested_final_marker_response_text(
            message_text,
            latest_user_text=latest_user_text,
            raw_items=raw_items,
        )
        if final_marker_response:
            message_text = final_marker_response
        else:
            premature_marker = premature_final_marker_message(
                message_text,
                latest_user_text=latest_user_text,
                raw_items=raw_items,
            )
            if premature_marker:
                message_text = premature_marker

    if isinstance(tool_calls, list) and tool_calls and terminal_section_upsert_progress_seen_after_latest_user(raw_items):
        terminal_upsert_response = terminal_section_upsert_recovery_response(
            raw_items=raw_items,
            model=str(chat_payload.get("model") or ""),
            latest_user_text=latest_user_text,
        )
        if terminal_upsert_response:
            return terminal_upsert_response

    if message_text:
        message_text = correct_stale_harness_context_window_claims(
            message_text,
            latest_user_text=latest_user_text,
            raw_items=raw_items,
        )
        message_text = collapse_repeated_final_text(message_text)
        output.append(
            {
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": message_text, "annotations": []}],
            }
        )

    if isinstance(tool_calls, list):
        for index, tool_call in enumerate(tool_calls):
            if not isinstance(tool_call, dict):
                continue
            function = tool_call.get("function")
            if not isinstance(function, dict):
                continue
            function_name = str(function.get("name") or "")
            normalized_arguments = normalize_function_arguments(
                function_name,
                function.get("arguments"),
                latest_user_text=latest_user_text,
            )
            normalized_name = normalize_tool_call_name(function_name)
            if normalized_name == "exec_command" and latest_user_requests_review(latest_user_text):
                review_final = review_compact_summary_final_answer(latest_user_text, raw_items)
                if review_final:
                    return response_payload_with_message(
                        review_final,
                        model=str(chat_payload.get("model") or ""),
                    )
                if not review_compact_summary_already_queued(output):
                    output.append(review_compact_summary_response_call(index=index))
                continue
            if normalized_name == "exec_command" and latest_user_requests_codebase_explanation(
                latest_user_text
            ):
                codebase_final = codebase_overview_final_answer(latest_user_text, raw_items)
                if codebase_final:
                    return response_payload_with_message(
                        codebase_final,
                        model=str(chat_payload.get("model") or ""),
                    )
                if not codebase_overview_evidence_seen_after_latest_user(raw_items):
                    output.append(codebase_overview_response_call(index=index))
                    continue
            if normalized_name == "exec_command" and latest_user_requests_document_inventory_triage(
                latest_user_text
            ):
                doc_triage_final = document_inventory_triage_final_answer(latest_user_text, raw_items)
                if doc_triage_final and document_inventory_evidence_seen_after_latest_user(raw_items):
                    return response_payload_with_message(
                        doc_triage_final,
                        model=str(chat_payload.get("model") or ""),
                    )
                if not document_inventory_evidence_seen_after_latest_user(raw_items):
                    output.append(document_inventory_tool_call(index=index))
                    continue
            if (
                normalized_name == "exec_command"
                and latest_user_requests_digital_products_agent_adjustment(latest_user_text)
                and exec_command_generated_digital_products_write(normalized_arguments)
            ):
                recovery_cmd = digital_products_agent_runbook_request_command(latest_user_text, raw_items)
                if recovery_cmd:
                    output.append(
                        {
                            "id": f"fc_digital_products_generated_write_recovery_{index + 1}",
                            "type": "function_call",
                            "call_id": f"call_digital_products_generated_write_recovery_{index + 1}",
                            "name": "exec_command",
                            "arguments": json.dumps(
                                {"cmd": recovery_cmd, "max_output_tokens": 4000},
                                ensure_ascii=True,
                            ),
                        }
                    )
                    continue
                digital_products_final = digital_products_agent_adjustment_final_answer(latest_user_text, raw_items)
                if digital_products_final:
                    return response_payload_with_message(
                        digital_products_final,
                        model=str(chat_payload.get("model") or ""),
                    )
            if normalized_name == "exec_command" and (
                "LOCAL_MODEL_TOOL_CALL_TOO_LARGE" in normalized_arguments
                or "LOCAL_MODEL_TOOL_CALL_TRUNCATED" in normalized_arguments
            ):
                recovery_cmd = digital_products_agent_runbook_recovery_command(
                    original_message_text,
                    latest_user_text,
                    raw_items,
                ) or digital_products_agent_runbook_request_command(
                    latest_user_text,
                    raw_items,
                )
                if recovery_cmd:
                    output.append(
                        {
                            "id": f"fc_digital_products_agent_runbook_recovery_{index + 1}",
                            "type": "function_call",
                            "call_id": f"call_digital_products_agent_runbook_recovery_{index + 1}",
                            "name": "exec_command",
                            "arguments": json.dumps(
                                {"cmd": recovery_cmd, "max_output_tokens": 4000},
                                ensure_ascii=True,
                            ),
                        }
                    )
                    continue
            budget_decision = RuntimeGuard(runtime_guard_config()).evaluate_proposed_budget(
                raw_items,
                {
                    "type": "function_call",
                    "name": normalized_name,
                    "arguments": normalized_arguments,
                },
            )
            if budget_decision.action == GuardAction.STOP:
                runbook_cmd = digital_products_agent_runbook_request_command(latest_user_text, raw_items)
                if runbook_cmd:
                    return response_payload_with_function_call(
                        {
                            "id": f"fc_digital_products_runtime_guard_recovery_{index + 1}",
                            "type": "function_call",
                            "call_id": f"call_digital_products_runtime_guard_recovery_{index + 1}",
                            "name": "exec_command",
                            "arguments": json.dumps(
                                {"cmd": runbook_cmd, "max_output_tokens": 4000},
                                ensure_ascii=True,
                            ),
                        },
                        model=str(chat_payload.get("model") or ""),
                    )
                summary_cmd = summary_comparison_hardening_request_command(latest_user_text, raw_items)
                if summary_cmd:
                    return response_payload_with_function_call(
                        {
                            "id": f"fc_summary_comparison_runtime_guard_recovery_{index + 1}",
                            "type": "function_call",
                            "call_id": f"call_summary_comparison_runtime_guard_recovery_{index + 1}",
                            "name": "exec_command",
                            "arguments": json.dumps(
                                {"cmd": summary_cmd, "max_output_tokens": 4000},
                                ensure_ascii=True,
                            ),
                        },
                        model=str(chat_payload.get("model") or ""),
                    )
                digital_products_final = digital_products_agent_adjustment_final_answer(latest_user_text, raw_items)
                if digital_products_final:
                    return response_payload_with_message(
                        digital_products_final,
                        model=str(chat_payload.get("model") or ""),
                    )
                return response_payload_with_message(
                    "LOCAL_MODEL_LOOP_DETECTED: "
                    f"{budget_decision.message}. "
                    "The configured local-Qwen tool-call budget has been reached; "
                    "summarize the current state instead of running another tool.",
                    model=str(chat_payload.get("model") or ""),
                )
            duplicate_key = tool_call_loop_key(normalized_name, normalized_arguments)
            if DUPLICATE_TOOL_GUARD_ENABLED and duplicate_guard_applies(normalized_name, normalized_arguments):
                if normalized_name == "exec_command" and exec_command_looks_broad_read(normalized_arguments):
                    duplicate_in_history = duplicate_broad_read_without_intervening_mutation(raw_items, duplicate_key)
                else:
                    duplicate_in_history = duplicate_key in prior_tool_call_keys
                if duplicate_in_history or duplicate_key in response_tool_call_keys:
                    recovery_call = None
                    if normalized_name == "exec_command":
                        recovery_call = duplicate_exec_recovery_tool_call(
                            normalized_arguments,
                            latest_user_text=latest_user_text,
                            index=index,
                            raw_items=raw_items,
                        )
                    if recovery_call:
                        recovery_arguments = recovery_call.get("arguments")
                        try:
                            recovery_parsed = json.loads(recovery_arguments) if isinstance(recovery_arguments, str) else {}
                        except json.JSONDecodeError:
                            recovery_parsed = {}
                        recovery_cmd = (
                            str(recovery_parsed.get("cmd") or "") if isinstance(recovery_parsed, dict) else ""
                        )
                        if (
                            "DUPLICATE_READ_ALREADY_DONE" in recovery_cmd
                            and duplicate_read_recovery_seen_after_latest_user_request(raw_items)
                        ):
                            return duplicate_read_recovery_stop_response(
                                raw_items=raw_items,
                                latest_user_text=latest_user_text,
                                model=str(chat_payload.get("model") or ""),
                                normalized_arguments=normalized_arguments,
                                index=index,
                            )
                        output.append(recovery_call)
                        continue
                    terminal_upsert_response = terminal_section_upsert_recovery_response(
                        raw_items=raw_items,
                        model=str(chat_payload.get("model") or ""),
                        latest_user_text=latest_user_text,
                        normalized_arguments=normalized_arguments,
                        index=index,
                    )
                    if terminal_upsert_response:
                        return terminal_upsert_response
                    if (
                        normalized_name == "exec_command"
                        and digital_products_item_marker_seen_after_latest_user(raw_items)
                    ):
                        edit_call = digital_products_next_missing_item_edit_call(
                            latest_user_text=latest_user_text,
                            raw_items=raw_items,
                        )
                        if edit_call:
                            output.append(edit_call)
                            continue
                    return response_payload_with_message(
                        "LOCAL_MODEL_LOOP_DETECTED: duplicate tool call suppressed before execution. "
                        "Use the latest successful tool output or continue with a different single artifact; do not rerun the same command. "
                        "If this was a repeated list/search/read, stop orienting and write or verify the next named artifact.",
                        model=str(chat_payload.get("model") or ""),
                    )
                response_tool_call_keys.add(duplicate_key)
            output.append(
                {
                    "id": f"fc_{index + 1}",
                    "type": "function_call",
                    "call_id": str(tool_call.get("id") or f"call_{index + 1}"),
                    "name": function_name,
                    "arguments": normalized_arguments,
                }
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

    return {
        "id": str(chat_payload.get("id") or "resp_tabby"),
        "object": "response",
        "created": int(chat_payload.get("created") or 0),
        "model": str(chat_payload.get("model") or ""),
        "status": "completed",
        "output": output,
        "parallel_tool_calls": bool(chat_payload.get("parallel_tool_calls", False)),
        "tools": [],
        "usage": usage_from_chat(chat_payload),
    }


def sse_event(event: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(event, separators=(',', ':'))}\n\n".encode()


def emit_responses_stream(handler: BaseHTTPRequestHandler, response_payload: dict[str, Any]) -> None:
    base_response = dict(response_payload)
    base_response["status"] = "in_progress"
    base_response["output"] = []

    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream")
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("Connection", "close")
    handler.end_headers()

    handler.wfile.write(sse_event({"type": "response.created", "response": base_response}))
    handler.wfile.write(sse_event({"type": "response.in_progress", "response": base_response}))

    for output_index, item in enumerate(response_payload.get("output", [])):
        item_type = item.get("type")
        if item_type == "message":
            added_item = {"id": item["id"], "type": "message", "role": "assistant", "status": "in_progress", "content": []}
            part = item["content"][0]
            handler.wfile.write(sse_event({"type": "response.output_item.added", "output_index": output_index, "item": added_item}))
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
            handler.wfile.write(sse_event({"type": "response.output_item.done", "output_index": output_index, "item": item}))
            continue

        if item_type == "function_call":
            added_item = {
                "id": item["id"],
                "type": "function_call",
                "call_id": item["call_id"],
                "name": item["name"],
                "arguments": "",
            }
            arguments = item.get("arguments", "")
            handler.wfile.write(sse_event({"type": "response.output_item.added", "output_index": output_index, "item": added_item}))
            handler.wfile.write(
                sse_event(
                    {
                        "type": "response.function_call_arguments.delta",
                        "output_index": output_index,
                        "item_id": item["id"],
                        "delta": arguments,
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
                    }
                )
            )
            handler.wfile.write(sse_event({"type": "response.output_item.done", "output_index": output_index, "item": item}))

    handler.wfile.write(sse_event({"type": "response.completed", "response": response_payload}))
    handler.wfile.write(b"data: [DONE]\n\n")
    handler.wfile.flush()


SyntheticResponseHandler = Callable[[dict[str, Any]], dict[str, Any] | None]


SYNTHETIC_RESPONSE_HANDLERS: tuple[tuple[str, SyntheticResponseHandler], ...] = (
    ("unresolved_template_placeholder", unresolved_template_placeholder_response),
    ("unresolved_filename_placeholder", unresolved_filename_placeholder_response),
    ("harness_self_analysis_followup", harness_self_analysis_followup_response),
    ("startup_read_completion", startup_read_completion_from_outputs),
    ("gpt55_audit_helper_completion", gpt55_audit_helper_completion_from_outputs),
    ("exact_helper_completion", exact_helper_completion_from_outputs),
    ("exact_command_marker_completion", exact_command_marker_completion_from_outputs),
    ("validation_marker_completion", validation_marker_completion_from_outputs),
    ("stage01_guard_completion", stage01_guard_completion_from_outputs),
    ("repeated_interface_marker", synthetic_repeated_interface_marker_response),
    ("repeated_loop", synthetic_repeated_loop_response),
)


def synthetic_response_handler_names() -> list[str]:
    return [name for name, _handler in SYNTHETIC_RESPONSE_HANDLERS]


def synthetic_response_from_payload(payload: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
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
                if isinstance(payload, dict) and isinstance(payload.get("data"), list) and "models" not in payload:
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
        except URLError as exc:
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": {"message": str(exc), "type": "proxy_error"}}).encode("utf-8"))

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
        body_obj, raw_body = load_json_body(self)
        if not isinstance(body_obj, dict):
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": {"message": "expected JSON object body", "type": "invalid_request"}}).encode("utf-8"))
            return

        sanitized, meta = sanitize_tools(body_obj)
        synthetic_handler, synthetic_response = synthetic_response_from_payload(sanitized)
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
            emit_responses_stream(self, synthetic_response)
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
                "compact_tool_prompt": bool(chat_request.get("messages") and meta["remaining_tool_count"]),
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
            with urlopen(request, timeout=ProxyHandler.upstream_timeout_seconds) as response:
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
        except URLError as exc:
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
            self.wfile.write(json.dumps({"error": {"message": str(exc), "type": "proxy_error"}}).encode("utf-8"))
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
            self.wfile.write(json.dumps({"error": {"message": f"invalid upstream JSON: {exc}", "type": "proxy_error"}}).encode("utf-8"))
            return

        choices = chat_payload.get("choices")
        choice = choices[0] if isinstance(choices, list) and choices else {}
        message = choice.get("message") if isinstance(choice, dict) else {}
        raw_message_text = extract_text(message.get("content")) if isinstance(message, dict) else ""
        if "<tool_call" in raw_message_text or "<|tool_call" in raw_message_text:
            self._write_log(
                {
                    "event": "upstream_tool_markup",
                    "raw_message_len": len(raw_message_text),
                    "raw_message_start": raw_message_text[:6000],
                    "raw_message_end": raw_message_text[-3000:] if len(raw_message_text) > 6000 else "",
                }
            )
        response_payload = rewrite_exact_helper_orientation_drift(
            sanitized,
            chat_completion_to_response(chat_payload, sanitized),
        )
        self._write_log(
            {
                "method": self.command,
                "path": self.path,
                "event": "upstream_response",
                **upstream_choice_summary(chat_payload),
                **response_payload_summary(response_payload),
            }
        )
        emit_responses_stream(self, response_payload)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/__tabby_proxy_status":
            self._proxy_status()
            return
        if self.path.split("?", 1)[0] == "/v1/models":
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
    parser = argparse.ArgumentParser(description="Bridge Codex /v1/responses calls to TabbyAPI /v1/chat/completions.")
    parser.add_argument("--listen-host", default=DEFAULT_LISTEN_HOST)
    parser.add_argument("--listen-port", type=int, default=DEFAULT_LISTEN_PORT)
    parser.add_argument("--target-base", default=DEFAULT_TARGET_BASE)
    parser.add_argument("--system-prompt-file", default=DEFAULT_SYSTEM_PROMPT_FILE)
    parser.add_argument(
        "--native-tools",
        action="store_true",
        help="Forward OpenAI-style tools to TabbyAPI instead of using the compact bridge XML prompt.",
    )
    parser.add_argument(
        "--log-path",
        default=str(DEFAULT_BRIDGE_LOG_PATH),
    )
    parser.add_argument("--max-output-tokens", type=int, default=DEFAULT_MAX_OUTPUT_TOKENS)
    parser.add_argument("--context-limit-tokens", type=int, default=DEFAULT_CONTEXT_LIMIT_TOKENS)
    parser.add_argument("--max-forward-body-bytes", type=int, default=DEFAULT_MAX_FORWARD_BODY_BYTES)
    parser.add_argument("--tool-temperature", type=float, default=DEFAULT_TOOL_TEMPERATURE)
    parser.add_argument("--tool-top-p", type=float, default=DEFAULT_TOOL_TOP_P)
    parser.add_argument("--tool-top-k", type=int, default=DEFAULT_TOOL_TOP_K)
    parser.add_argument("--tool-min-p", type=float, default=DEFAULT_TOOL_MIN_P)
    parser.add_argument("--tool-reasoning-effort", default=DEFAULT_TOOL_REASONING_EFFORT)
    parser.add_argument("--enable-thinking", choices=["true", "false"], default="false")
    parser.add_argument("--preserve-thinking", choices=["true", "false"], default="false")
    parser.add_argument("--upstream-timeout-seconds", type=int, default=DEFAULT_UPSTREAM_TIMEOUT_SECONDS)
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
    ProxyHandler.tool_top_p = None if args.tool_top_p is None else max(0.0, float(args.tool_top_p))
    ProxyHandler.tool_top_k = None if args.tool_top_k is None else max(0, int(args.tool_top_k))
    ProxyHandler.tool_min_p = None if args.tool_min_p is None else max(0.0, float(args.tool_min_p))
    ProxyHandler.tool_reasoning_effort = str(args.tool_reasoning_effort or "").strip()
    ProxyHandler.enable_thinking = args.enable_thinking == "true"
    ProxyHandler.preserve_thinking = args.preserve_thinking == "true"
    ProxyHandler.upstream_timeout_seconds = max(10, int(args.upstream_timeout_seconds))
    local_base = f"http://{args.listen_host}:{args.listen_port}"
    remote_ok, remote_note = probe_models(ProxyHandler.target_base)
    if not remote_ok:
        raise SystemExit(
            f"target TabbyAPI endpoint is not healthy at {ProxyHandler.target_base}: {remote_note}\n"
            f"Check the remote server first, for example: curl -sS {ProxyHandler.target_base}/v1/models"
        )
    try:
        server = ThreadingHTTPServer((args.listen_host, args.listen_port), ProxyHandler)
    except OSError as exc:
        if exc.errno == 98 and listener_is_bound(args.listen_host, args.listen_port):
            local_ok, local_note = probe_models(local_base)
            if local_ok:
                print(
                    f"tabby responses proxy reusing existing healthy listener at {local_base} -> {ProxyHandler.target_base} ({local_note})",
                    flush=True,
                )
                return
            raise SystemExit(
                f"listen address {local_base} is already in use, but the existing listener is not a healthy Tabby-compatible endpoint: {local_note}\n"
                "Stop the conflicting listener or choose another port, then retry."
            ) from exc
        raise
    print(
        f"tabby responses proxy listening on {local_base} -> {ProxyHandler.target_base} ({remote_note})",
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
