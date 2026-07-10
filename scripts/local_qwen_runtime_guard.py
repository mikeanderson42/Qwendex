#!/usr/bin/env python3
"""Pure runtime guard logic for the local-Qwen Codex bridge.

The bridge is the interception adapter; this module owns deterministic loop and
duplicate-call policy. Keep it side-effect free so tests can exercise the guard
without starting the bridge, Codex, or any MCP server.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

RUNTIME_GUARD_VERSION = "local-qwen-runtime-guard-v1"


class GuardAction(str, Enum):
    ALLOW = "allow"
    RECOVER = "recover"
    STOP = "stop"
    FINALIZE = "finalize"


class LoopType(str, Enum):
    CONSECUTIVE_IDENTICAL_TOOL_CALLS = "consecutive_identical_tool_calls"
    TURN_TOOL_CALL_CAP = "turn_tool_call_cap"
    SHELL_COMMAND_STAGNATION = "shell_command_stagnation"
    GLOBAL_TOOL_CALL_DUPLICATE = "global_tool_call_duplicate"
    ALTERNATING_TOOL_CALL_PATTERN = "alternating_tool_call_pattern"
    READ_FILE_LOOP = "read_file_loop"
    ACTION_STAGNATION = "action_stagnation"
    DUPLICATE_MUTATING_COMMAND = "duplicate_mutating_command"
    DUPLICATE_COMPLETED_COMMAND = "duplicate_completed_command"
    DUPLICATE_READ_COMMAND = "duplicate_read_command"
    REPEATED_INTERFACE_MARKER = "repeated_interface_marker"
    STALE_RECOVERY_LOOP = "stale_recovery_loop"


LOOP_MESSAGES = {
    LoopType.CONSECUTIVE_IDENTICAL_TOOL_CALLS: "the model repeated the same tool call back-to-back",
    LoopType.TURN_TOOL_CALL_CAP: "tool-call cap exceeded",
    LoopType.SHELL_COMMAND_STAGNATION: "git overview inspection repeated without progress",
    LoopType.GLOBAL_TOOL_CALL_DUPLICATE: "duplicate tool call repeated across the turn",
    LoopType.ALTERNATING_TOOL_CALL_PATTERN: "alternating tool pattern detected",
    LoopType.READ_FILE_LOOP: "too many read-like calls after progress",
    LoopType.ACTION_STAGNATION: "same tool/action repeated without progress",
    LoopType.DUPLICATE_MUTATING_COMMAND: "duplicate mutating command suppressed before execution",
    LoopType.DUPLICATE_COMPLETED_COMMAND: "duplicate completed command suppressed after terminal receipt",
    LoopType.DUPLICATE_READ_COMMAND: "duplicate read command was already done",
    LoopType.REPEATED_INTERFACE_MARKER: "repeated local tool-interface failure marker",
    LoopType.STALE_RECOVERY_LOOP: "duplicate recovery already happened for this request",
}


LOCAL_MODEL_INTERFACE_MARKERS = (
    "LOCAL_MODEL_TOOL_CALL_TOO_LARGE",
    "LOCAL_MODEL_TOOL_CALL_TRUNCATED",
    "LOCAL_MODEL_TOOL_MARKUP_SUPPRESSED",
    "LOCAL_MODEL_LOOP_DETECTED",
)


@dataclass(frozen=True)
class GuardConfig:
    profile: str = "balanced"
    enabled: bool = True
    consecutive_identical_tool_call_threshold: int = 5
    turn_tool_call_cap: int = 100
    global_duplicate_tool_call_threshold: int = 6
    alternating_tool_call_pattern_cycles: int = 3
    read_loop_threshold: int = 8
    read_loop_window: int = 15
    action_stagnation_threshold: int = 8
    shell_command_stagnation_threshold: int = 8
    repeated_interface_marker_threshold: int = 2
    stop_after_duplicate_read_recovery: bool = False
    run_max_wall_time_seconds: int = -1
    run_max_tool_calls: int = -1

    def __post_init__(self) -> None:
        profile = self.profile.strip().lower() or "balanced"
        if profile not in {"balanced", "max_safety"}:
            profile = "balanced"
        object.__setattr__(self, "profile", profile)
        if profile == "max_safety":
            object.__setattr__(self, "stop_after_duplicate_read_recovery", True)
            object.__setattr__(self, "repeated_interface_marker_threshold", 1)

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> GuardConfig:
        source = env if env is not None else os.environ
        profile = (
            source.get("LOCAL_QWEN_GUARD_PROFILE")
            or source.get("CODEX_TEXTGEN_GUARD_PROFILE")
            or source.get("CODEX_TEXTGEN_RUNTIME_GUARD_PROFILE")
            or "balanced"
        )
        enabled = parse_bool(source.get("CODEX_TEXTGEN_RUNTIME_GUARD", "1"), default=True)
        return cls(
            profile=profile,
            enabled=enabled,
            consecutive_identical_tool_call_threshold=parse_int(
                source.get("CODEX_TEXTGEN_CONSECUTIVE_TOOL_CALL_THRESHOLD"), 5
            ),
            turn_tool_call_cap=parse_int(source.get("CODEX_TEXTGEN_TURN_TOOL_CALL_CAP"), 100),
            global_duplicate_tool_call_threshold=parse_int(
                source.get("CODEX_TEXTGEN_GLOBAL_DUPLICATE_TOOL_CALL_THRESHOLD"), 6
            ),
            alternating_tool_call_pattern_cycles=parse_int(
                source.get("CODEX_TEXTGEN_ALTERNATING_TOOL_CALL_PATTERN_CYCLES"), 3
            ),
            read_loop_threshold=parse_int(source.get("CODEX_TEXTGEN_READ_LOOP_THRESHOLD"), 8),
            read_loop_window=parse_int(source.get("CODEX_TEXTGEN_READ_LOOP_WINDOW"), 15),
            action_stagnation_threshold=parse_int(
                source.get("CODEX_TEXTGEN_ACTION_STAGNATION_THRESHOLD"), 8
            ),
            shell_command_stagnation_threshold=parse_int(
                source.get("CODEX_TEXTGEN_SHELL_COMMAND_STAGNATION_THRESHOLD"), 8
            ),
            run_max_wall_time_seconds=parse_budget_int(
                source.get("LOCAL_QWEN_CODEX_MAX_WALL_TIME_SECONDS")
                or source.get("CODEX_TEXTGEN_RUN_MAX_WALL_TIME_SECONDS"),
                default=-1,
                allow_zero=False,
            ),
            run_max_tool_calls=parse_budget_int(
                source.get("LOCAL_QWEN_CODEX_MAX_TOOL_CALLS")
                or source.get("CODEX_TEXTGEN_RUN_MAX_TOOL_CALLS"),
                default=-1,
                allow_zero=True,
            ),
        )

    def thresholds(self) -> dict[str, int]:
        return {
            "consecutive_identical_tool_call_threshold": self.consecutive_identical_tool_call_threshold,
            "turn_tool_call_cap": self.turn_tool_call_cap,
            "global_duplicate_tool_call_threshold": self.global_duplicate_tool_call_threshold,
            "alternating_tool_call_pattern_cycles": self.alternating_tool_call_pattern_cycles,
            "read_loop_threshold": self.read_loop_threshold,
            "read_loop_window": self.read_loop_window,
            "action_stagnation_threshold": self.action_stagnation_threshold,
            "shell_command_stagnation_threshold": self.shell_command_stagnation_threshold,
        }

    def status_payload(self) -> dict[str, Any]:
        return {
            "runtime_guard_version": RUNTIME_GUARD_VERSION,
            "runtime_guard_profile": self.profile,
            "runtime_guard_enabled": self.enabled,
            "guard_thresholds": self.thresholds(),
            "run_budget_defaults": {
                "max_wall_time_seconds": self.run_max_wall_time_seconds,
                "max_tool_calls": self.run_max_tool_calls,
            },
        }


@dataclass(frozen=True)
class ToolRecord:
    name: str
    arguments: Any
    normalized_arguments: str
    key: str


@dataclass(frozen=True)
class GuardDecision:
    action: GuardAction = GuardAction.ALLOW
    loop_type: LoopType | None = None
    message: str = ""
    marker: str = ""
    threshold: int | None = None
    observed: int | None = None
    tool_name: str = ""
    args_hash: str = ""
    safe_next_action: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def allow(cls) -> GuardDecision:
        return cls()

    @classmethod
    def stop(
        cls,
        loop_type: LoopType,
        *,
        threshold: int | None = None,
        observed: int | None = None,
        tool_name: str = "",
        key: str = "",
        detail: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> GuardDecision:
        return cls(
            action=GuardAction.STOP,
            loop_type=loop_type,
            message=detail or LOOP_MESSAGES[loop_type],
            marker=marker_for_loop(loop_type, metadata),
            threshold=threshold,
            observed=observed,
            tool_name=tool_name,
            args_hash=hash_key(key) if key else "",
            safe_next_action=safe_next_action_for_loop(loop_type),
            metadata=metadata or {},
        )

    @classmethod
    def recover(
        cls,
        loop_type: LoopType,
        *,
        threshold: int | None = None,
        observed: int | None = None,
        tool_name: str = "",
        key: str = "",
        detail: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> GuardDecision:
        return cls(
            action=GuardAction.RECOVER,
            loop_type=loop_type,
            message=detail or LOOP_MESSAGES[loop_type],
            marker=marker_for_loop(loop_type, metadata),
            threshold=threshold,
            observed=observed,
            tool_name=tool_name,
            args_hash=hash_key(key) if key else "",
            safe_next_action=safe_next_action_for_loop(loop_type),
            metadata=metadata or {},
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "action": self.action.value,
            "loop_type": self.loop_type.value if self.loop_type else "",
            "marker": self.marker,
            "reason": self.message,
            "threshold": self.threshold,
            "observed": self.observed,
            "tool_name": self.tool_name,
            "args_hash": self.args_hash,
            "safe_next_action": self.safe_next_action,
            "metadata": self.metadata,
        }


def marker_for_loop(loop_type: LoopType, metadata: dict[str, Any] | None = None) -> str:
    if loop_type == LoopType.DUPLICATE_READ_COMMAND:
        return "DUPLICATE_READ_ALREADY_DONE"
    if loop_type == LoopType.DUPLICATE_COMPLETED_COMMAND:
        return "DUPLICATE_COMPLETED_COMMAND"
    if loop_type == LoopType.REPEATED_INTERFACE_MARKER and metadata:
        marker = metadata.get("marker")
        return str(marker) if marker else "LOCAL_MODEL_LOOP_DETECTED"
    return "LOCAL_MODEL_LOOP_DETECTED"


def safe_next_action_for_loop(loop_type: LoopType) -> str:
    if loop_type == LoopType.DUPLICATE_READ_COMMAND:
        return "Use the prior successful read output; answer or run a different bounded command."
    if loop_type == LoopType.DUPLICATE_COMPLETED_COMMAND:
        return "Finalize from the terminal receipt; do not rerun the completed command."
    if loop_type == LoopType.REPEATED_INTERFACE_MARKER:
        return "Stop this command shape and retry only once with a smaller bounded command."
    if loop_type == LoopType.DUPLICATE_MUTATING_COMMAND:
        return "Do not rerun the mutating command; inspect the artifact or receipt instead."
    return "Stop the loop and summarize current state before any further tool call."


class RuntimeGuard:
    def __init__(self, config: GuardConfig | None = None):
        self.config = config or GuardConfig.from_env()

    def evaluate_history(self, raw_items: list[Any]) -> GuardDecision:
        if not self.config.enabled:
            return GuardDecision.allow()
        records = tool_call_records_after_latest_user(raw_items)
        marker_decision = self._check_repeated_interface_markers(raw_items)
        if marker_decision.action != GuardAction.ALLOW:
            return marker_decision
        if not records:
            return GuardDecision.allow()

        decision = self._check_run_tool_budget(records)
        if decision.action != GuardAction.ALLOW:
            return decision
        decision = self._check_turn_cap(records)
        if decision.action != GuardAction.ALLOW:
            return decision
        decision = self._check_consecutive_identical(records)
        if decision.action != GuardAction.ALLOW:
            return decision
        decision = self._check_shell_stagnation(records)
        if decision.action != GuardAction.ALLOW:
            return decision
        decision = self._check_global_duplicate(records)
        if decision.action != GuardAction.ALLOW:
            return decision
        decision = self._check_alternating_pattern(records)
        if decision.action != GuardAction.ALLOW:
            return decision
        decision = self._check_read_loop(records)
        if decision.action != GuardAction.ALLOW:
            return decision
        return self._check_action_stagnation(records)

    def evaluate_proposed_call(self, raw_items: list[Any], proposed_call: dict[str, Any]) -> GuardDecision:
        if not self.config.enabled:
            return GuardDecision.allow()
        proposed = tool_record_from_item(proposed_call)
        if proposed is None:
            return GuardDecision.allow()
        budget_decision = self.evaluate_proposed_budget(raw_items, proposed_call)
        if budget_decision.action != GuardAction.ALLOW:
            return budget_decision
        if (
            self.config.stop_after_duplicate_read_recovery
            and duplicate_read_recovery_seen_after_latest_user(raw_items)
        ):
            return GuardDecision.stop(
                LoopType.STALE_RECOVERY_LOOP,
                observed=1,
                tool_name=proposed.name,
                key=proposed.key,
            )
        prior_keys = {record.key for record in tool_call_records_after_latest_user(raw_items)}
        if proposed.key in prior_keys and proposed.name == "exec_command":
            if exec_command_looks_read_only(proposed.normalized_arguments):
                return GuardDecision.recover(
                    LoopType.DUPLICATE_READ_COMMAND,
                    observed=2,
                    tool_name=proposed.name,
                    key=proposed.key,
                )
            if exec_command_looks_mutating(proposed.normalized_arguments):
                if completed_no_next_output_seen_after_latest_user(raw_items, proposed.key):
                    return GuardDecision.recover(
                        LoopType.DUPLICATE_COMPLETED_COMMAND,
                        observed=2,
                        tool_name=proposed.name,
                        key=proposed.key,
                        metadata={"completion": "terminal_no_next_item"},
                    )
                return GuardDecision.stop(
                    LoopType.DUPLICATE_MUTATING_COMMAND,
                    observed=2,
                    tool_name=proposed.name,
                    key=proposed.key,
            )
        return self.evaluate_history([*raw_items, proposed_call])

    def evaluate_proposed_budget(
        self, raw_items: list[Any], proposed_call: dict[str, Any]
    ) -> GuardDecision:
        if not self.config.enabled:
            return GuardDecision.allow()
        proposed = tool_record_from_item(proposed_call)
        if proposed is None:
            return GuardDecision.allow()
        threshold = self.config.run_max_tool_calls
        if threshold >= 0:
            prior_count = len(tool_call_records_after_latest_user(raw_items))
            if prior_count >= threshold:
                return GuardDecision.stop(
                    LoopType.TURN_TOOL_CALL_CAP,
                    threshold=threshold,
                    observed=prior_count + 1,
                    tool_name=proposed.name,
                    key=proposed.key,
                    detail="run tool-call budget exceeded",
                    metadata={"budget": "run_max_tool_calls"},
                )
        return GuardDecision.allow()

    def _check_run_tool_budget(self, records: list[ToolRecord]) -> GuardDecision:
        threshold = self.config.run_max_tool_calls
        if threshold >= 0 and len(records) > threshold:
            return GuardDecision.stop(
                LoopType.TURN_TOOL_CALL_CAP,
                threshold=threshold,
                observed=len(records),
                tool_name=records[-1].name,
                key=records[-1].key,
                detail="run tool-call budget exceeded",
                metadata={"budget": "run_max_tool_calls"},
            )
        return GuardDecision.allow()

    def _check_turn_cap(self, records: list[ToolRecord]) -> GuardDecision:
        threshold = self.config.turn_tool_call_cap
        if threshold > 0 and len(records) > threshold:
            return GuardDecision.stop(
                LoopType.TURN_TOOL_CALL_CAP,
                threshold=threshold,
                observed=len(records),
                tool_name=records[-1].name,
                key=records[-1].key,
            )
        return GuardDecision.allow()

    def _check_consecutive_identical(self, records: list[ToolRecord]) -> GuardDecision:
        threshold = self.config.consecutive_identical_tool_call_threshold
        if threshold <= 0:
            return GuardDecision.allow()
        streak = 0
        last_key = ""
        for record in records:
            if record.key == last_key:
                streak += 1
            else:
                last_key = record.key
                streak = 1
            if streak >= threshold:
                return GuardDecision.stop(
                    LoopType.CONSECUTIVE_IDENTICAL_TOOL_CALLS,
                    threshold=threshold,
                    observed=streak,
                    tool_name=record.name,
                    key=record.key,
                )
        return GuardDecision.allow()

    def _check_shell_stagnation(self, records: list[ToolRecord]) -> GuardDecision:
        threshold = self.config.shell_command_stagnation_threshold
        if threshold <= 0:
            return GuardDecision.allow()
        streak = 0
        last_shell_key = ""
        for record in records:
            key = exec_command_git_overview_inspection_key(record.normalized_arguments)
            if not key:
                streak = 0
                last_shell_key = ""
                continue
            if key == last_shell_key:
                streak += 1
            else:
                last_shell_key = key
                streak = 1
            if streak >= threshold:
                return GuardDecision.stop(
                    LoopType.SHELL_COMMAND_STAGNATION,
                    threshold=threshold,
                    observed=streak,
                    tool_name=record.name,
                    key=record.key,
                )
        return GuardDecision.allow()

    def _check_global_duplicate(self, records: list[ToolRecord]) -> GuardDecision:
        threshold = self.config.global_duplicate_tool_call_threshold
        if threshold <= 0:
            return GuardDecision.allow()
        counts: dict[str, int] = {}
        by_key: dict[str, ToolRecord] = {}
        for record in records:
            counts[record.key] = counts.get(record.key, 0) + 1
            by_key[record.key] = record
            if counts[record.key] >= threshold:
                return GuardDecision.stop(
                    LoopType.GLOBAL_TOOL_CALL_DUPLICATE,
                    threshold=threshold,
                    observed=counts[record.key],
                    tool_name=record.name,
                    key=record.key,
                )
        return GuardDecision.allow()

    def _check_alternating_pattern(self, records: list[ToolRecord]) -> GuardDecision:
        cycles = self.config.alternating_tool_call_pattern_cycles
        window_size = 2 * cycles
        if cycles <= 0 or len(records) < window_size:
            return GuardDecision.allow()
        keys = [record.key for record in records]
        for start in range(0, len(keys) - window_size + 1):
            window = keys[start : start + window_size]
            first, second = window[0], window[1]
            if first == second:
                continue
            if all(key == (first if index % 2 == 0 else second) for index, key in enumerate(window)):
                record = records[start + window_size - 1]
                return GuardDecision.stop(
                    LoopType.ALTERNATING_TOOL_CALL_PATTERN,
                    threshold=cycles,
                    observed=cycles,
                    tool_name=record.name,
                    key=record.key,
                )
        return GuardDecision.allow()

    def _check_read_loop(self, records: list[ToolRecord]) -> GuardDecision:
        threshold = self.config.read_loop_threshold
        window_size = self.config.read_loop_window
        if threshold <= 0 or window_size <= 0:
            return GuardDecision.allow()
        recent: list[ToolRecord] = []
        has_seen_non_read = False
        for record in records:
            is_read = record_is_read_like(record)
            if not is_read:
                has_seen_non_read = True
            recent.append(record)
            if len(recent) > window_size:
                recent.pop(0)
            if not has_seen_non_read or len(recent) < threshold:
                continue
            read_count = sum(1 for item in recent if record_is_read_like(item))
            if read_count >= threshold:
                return GuardDecision.stop(
                    LoopType.READ_FILE_LOOP,
                    threshold=threshold,
                    observed=read_count,
                    tool_name=record.name,
                    key=record.key,
                )
        return GuardDecision.allow()

    def _check_action_stagnation(self, records: list[ToolRecord]) -> GuardDecision:
        threshold = self.config.action_stagnation_threshold
        if threshold <= 0:
            return GuardDecision.allow()
        streak = 0
        last_action = ""
        for record in records:
            action = action_stagnation_key(record)
            if not action:
                streak = 0
                last_action = ""
                continue
            if action == last_action:
                streak += 1
            else:
                last_action = action
                streak = 1
            if streak >= threshold:
                return GuardDecision.stop(
                    LoopType.ACTION_STAGNATION,
                    threshold=threshold,
                    observed=streak,
                    tool_name=record.name,
                    key=record.key,
                )
        return GuardDecision.allow()

    def _check_repeated_interface_markers(self, raw_items: list[Any]) -> GuardDecision:
        threshold = self.config.repeated_interface_marker_threshold
        if threshold <= 0:
            return GuardDecision.allow()
        counts = interface_marker_counts_after_latest_user(raw_items)
        for marker, count in counts.items():
            if count >= threshold:
                return GuardDecision.stop(
                    LoopType.REPEATED_INTERFACE_MARKER,
                    threshold=threshold,
                    observed=count,
                    metadata={"marker": marker},
                )
        return GuardDecision.allow()


def parse_int(raw: str | None, default: int) -> int:
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return int(str(raw).strip())
    except ValueError:
        return default


def parse_bool(raw: str | None, *, default: bool) -> bool:
    if raw is None:
        return default
    normalized = str(raw).strip().lower()
    if not normalized:
        return default
    if normalized in {"0", "false", "no", "off"}:
        return False
    if normalized in {"1", "true", "yes", "on"}:
        return True
    return default


def parse_budget_int(raw: str | None, *, default: int, allow_zero: bool) -> int:
    if raw is None or str(raw).strip() == "":
        return default
    value = int(str(raw).strip())
    if value == -1:
        return value
    if value == 0 and allow_zero:
        return value
    if value <= 0:
        raise ValueError(f"invalid budget value {raw!r}")
    return value


def hash_key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def normalize_role(role: str) -> str:
    lowered = role.strip().lower()
    if lowered in {"assistant", "model"}:
        return "assistant"
    if lowered in {"tool", "function"}:
        return "tool"
    return "user" if lowered == "user" else lowered


def extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts)
    if isinstance(content, dict):
        text = content.get("text")
        return text if isinstance(text, str) else ""
    return ""


def extract_output_text(item: dict[str, Any]) -> str:
    output = item.get("output")
    if isinstance(output, str):
        return output
    return extract_text(item.get("content"))


def item_type(item: dict[str, Any]) -> str:
    raw = str(item.get("type", ""))
    if not raw and ("role" in item or "content" in item):
        return "message"
    return raw


def reset_on_user_message(item: dict[str, Any]) -> bool:
    if item_type(item) != "message":
        return False
    return normalize_role(str(item.get("role", "user"))) == "user" and bool(extract_text(item.get("content")).strip())


def normalize_tool_call_name(name: str) -> str:
    name = name.strip()
    if name.startswith("functions."):
        return name.split(".", 1)[1]
    return name


def normalize_tool_arguments_for_loop_key(arguments: Any, *, name: str = "") -> str:
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            return " ".join(arguments.split())
        arguments = parsed
    normalized_name = normalize_tool_call_name(name)
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


def tool_call_key(name: str, arguments: Any) -> str:
    normalized_name = normalize_tool_call_name(name)
    normalized_args = normalize_tool_arguments_for_loop_key(arguments, name=normalized_name)
    return f"{normalized_name}:{normalized_args}"


def tool_record_from_item(item: dict[str, Any]) -> ToolRecord | None:
    if item_type(item) != "function_call":
        return None
    name = normalize_tool_call_name(str(item.get("name") or ""))
    function = item.get("function")
    if not name and isinstance(function, dict):
        name = normalize_tool_call_name(str(function.get("name") or ""))
        arguments = function.get("arguments")
    else:
        arguments = item.get("arguments")
    normalized = normalize_tool_arguments_for_loop_key(arguments, name=name)
    return ToolRecord(name=name, arguments=arguments, normalized_arguments=normalized, key=f"{name}:{normalized}")


def tool_call_records_after_latest_user(raw_items: list[Any]) -> list[ToolRecord]:
    records: list[ToolRecord] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        if reset_on_user_message(item):
            records = []
            continue
        record = tool_record_from_item(item)
        if record is not None:
            records.append(record)
    return records


def parsed_exec_args(normalized_arguments: str) -> dict[str, Any]:
    try:
        parsed = json.loads(normalized_arguments)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def exec_command_from_normalized(normalized_arguments: str) -> str:
    parsed = parsed_exec_args(normalized_arguments)
    return str(parsed.get("cmd") or parsed.get("command") or "").strip()


def exec_command_looks_mutating(normalized_arguments: str) -> bool:
    cmd = exec_command_from_normalized(normalized_arguments)
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


def exec_command_looks_read_only(normalized_arguments: str) -> bool:
    if exec_command_looks_mutating(normalized_arguments):
        return False
    cmd = exec_command_from_normalized(normalized_arguments)
    if not cmd:
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
        unsafe_markers = (
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
        file_read_markers = (".read(", ".read_text(", "open(", "os.path.getsize", ".stat(")
        if any(marker in compact for marker in unsafe_markers):
            return False
        return "print(" in compact and any(marker in compact for marker in file_read_markers)
    return False


def record_is_read_like(record: ToolRecord) -> bool:
    if record.name in {"read_file", "read_many_files", "list_directory"}:
        return True
    if record.name.startswith(("read_", "list_")):
        return True
    if record.name == "exec_command":
        return exec_command_looks_read_only(record.normalized_arguments)
    return False


def git_revision_token(token: str) -> bool:
    return (
        token in {"HEAD", "@"}
        or re.fullmatch(r"(?:HEAD|@)(?:[~^]\d*)+", token) is not None
        or re.fullmatch(r"[0-9a-f]{7,40}", token, flags=re.IGNORECASE) is not None
        or re.fullmatch(r"[^\s]+\.{2,3}[^\s]+", token) is not None
    )


def git_diff_args_are_overview(args: str) -> bool:
    tokens = args.strip().split()
    if not tokens:
        return True
    if "--" in tokens and tokens.index("--") < len(tokens) - 1:
        return False
    return all(token.startswith("-") or git_revision_token(token) for token in tokens)


def shell_segment_is_git_overview(segment: str) -> bool:
    match = re.match(r"^git(?:\s+(?:-C\s+\S+|--no-pager))*\s+(status|diff|ls-files)\b", segment, flags=re.I)
    if not match:
        return False
    command = match.group(1).lower()
    if command != "diff":
        return True
    return git_diff_args_are_overview(segment[match.end() :])


def exec_command_git_overview_inspection_key(normalized_arguments: str) -> str:
    cmd = exec_command_from_normalized(normalized_arguments)
    if not cmd:
        return ""
    segments = [segment.strip() for segment in re.split(r"&&|\|\||[;&|\n]", cmd) if segment.strip()]
    if not segments or not all(shell_segment_is_git_overview(segment) for segment in segments):
        return ""
    return "exec_command:git-overview-inspection"


def action_stagnation_key(record: ToolRecord) -> str:
    if record.name != "exec_command":
        return record.name
    overview = exec_command_git_overview_inspection_key(record.normalized_arguments)
    if overview:
        return overview
    if exec_command_looks_read_only(record.normalized_arguments):
        return ""
    return ""


def interface_marker_counts_after_latest_user(raw_items: list[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        if reset_on_user_message(item):
            counts = {}
            continue
        if item_type(item) != "function_call_output":
            continue
        text = extract_output_text(item)
        for marker in LOCAL_MODEL_INTERFACE_MARKERS:
            if marker in text:
                counts[marker] = counts.get(marker, 0) + 1
    return counts


def duplicate_read_recovery_seen_after_latest_user(raw_items: list[Any]) -> bool:
    seen = False
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        if reset_on_user_message(item):
            seen = False
            continue
        if item_type(item) == "function_call_output" and "DUPLICATE_READ_ALREADY_DONE" in extract_output_text(item):
            seen = True
    return seen


TERMINAL_NO_NEXT_OUTPUT_RE = re.compile(
    r"\b(?:[A-Z0-9_]+_(?:DONE|ALREADY_PRESENT)|DOCUMENT_SECTION_(?:DONE|ALREADY_PRESENT))\b"
    r".*?\baction=[A-Za-z0-9_-]+"
    r".*?\bnext_item=(?:None|null|none)\b",
    flags=re.IGNORECASE | re.DOTALL,
)


def output_has_terminal_no_next_receipt(text: str) -> bool:
    if not text:
        return False
    if TERMINAL_NO_NEXT_OUTPUT_RE.search(text):
        return True
    has_completion_marker = bool(
        re.search(
            r"\b(?:[A-Z0-9_]+_(?:DONE|ALREADY_PRESENT)|DOCUMENT_SECTION_(?:DONE|ALREADY_PRESENT))\b",
            text,
        )
    )
    return has_completion_marker and bool(re.search(r'"next_item"\s*:\s*null\b', text, flags=re.IGNORECASE))


def completed_no_next_output_seen_after_latest_user(raw_items: list[Any], duplicate_key: str) -> bool:
    matching_call_ids: set[str] = set()
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        if reset_on_user_message(item):
            matching_call_ids.clear()
            continue
        current_type = item_type(item)
        if current_type == "function_call":
            record = tool_record_from_item(item)
            if record is not None and record.key == duplicate_key:
                call_id = str(item.get("call_id") or item.get("id") or "")
                if call_id:
                    matching_call_ids.add(call_id)
            continue
        if current_type != "function_call_output":
            continue
        call_id = str(item.get("call_id") or "")
        if call_id in matching_call_ids and output_has_terminal_no_next_receipt(extract_output_text(item)):
            return True
    return False
