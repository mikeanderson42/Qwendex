#!/usr/bin/env python3
"""Public Qwendex CLI facade for Codex plus bounded local Qwen support."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.1.0-rc.1"
CONFIG_DIR = ROOT / "config" / "qwendex"
DEFAULT_PROJECT_CONFIG = CONFIG_DIR / "qwendex.json"
DEFAULT_USER_CONFIG = Path.home() / ".config" / "qwendex" / "config.json"
PUBLIC_DOC_DIR = ROOT / "public" / "qwendex"
DEFAULT_RESULTS_ROOT = ROOT / "results" / "qwendex"
LLMSTACK_CONFIG_DIR = ROOT / "config" / "local_llm_stack"
LLMSTACK_PUBLIC_CONFIG = LLMSTACK_CONFIG_DIR / "stack_manager.json"
LLMSTACK_SAMPLE_CONFIG = LLMSTACK_CONFIG_DIR / "stack_manager.sample.json"
LLMSTACK_LOCAL_CONFIG = LLMSTACK_CONFIG_DIR / "stack_manager.local.json"

PUBLIC_DOC_FILES = (
    "README.md",
    "quickstart.md",
    "architecture.md",
    "llmstack.md",
    "configuration.md",
    "operations.md",
    "seat-handoff.md",
    "learning-loop.md",
    "manager-mode.md",
    "codex-patching.md",
    "dev-environment.md",
    "testbench.md",
    "tool-server.md",
    "security.md",
    "verification.md",
    "troubleshooting.md",
    "release-notes.md",
    "staging-receipt.md",
)

REQUIRED_SURFACE_FILES = (
    "llmstack",
    "scripts/qwendex",
    "scripts/qwendex_cli.py",
    "scripts/qwendex_dev_env",
    "scripts/qwendex_testbench",
    "scripts/llm",
    "scripts/windows/open.ps1",
    "scripts/run_local_qwen_codex.sh",
    "scripts/local_qwen_harness_eval.py",
    "scripts/local_qwen_harness_gate.py",
    "scripts/local_qwen_harness_ledger.py",
    "scripts/local_qwen_skillopt_wrapper.py",
    "config/qwendex/qwendex.schema.json",
    "config/qwendex/qwendex.json",
    "config/qwendex/profiles.json",
    "config/qwendex/model-catalog.json",
    "config/local_llm_stack/stack_manager.json",
    "config/local_llm_stack/stack_manager.sample.json",
    "config/local_llm_stack/profiles.example.json",
)

LLMSTACK_PUBLIC_FILES = (
    "llmstack",
    "config/local_llm_stack/stack_manager.json",
    "config/local_llm_stack/stack_manager.sample.json",
    "config/local_llm_stack/profiles.example.json",
    "config/local_llm_stack/litellm.local.yaml",
    "config/local_llm_stack/litellm.textgen.local.yaml",
    "config/local_llm_stack/textgen_cmd_flags.txt",
    "scripts/local_llm_stack.py",
    "scripts/llm",
    "scripts/windows/open.ps1",
    "scripts/run_textgen_safe_no_model.sh",
    "scripts/run_llamacpp_qwopucode_gguf.sh",
    "scripts/run_vllm_qwopucode_gguf.sh",
    "scripts/run_koboldcpp_gguf.sh",
)

LLMSTACK_PRIVATE_PATTERNS = (
    re.compile(r"/home/tweak"),
    re.compile(r"/mnt/c/Users/Tweak", re.IGNORECASE),
    re.compile(r"\bAnderson\b"),
    re.compile(r"\bSTAR\b"),
    re.compile(r"\bGTM\b"),
)

SECRET_RE = re.compile(
    r"(sk-[A-Za-z0-9_-]{16,}|github_pat_[A-Za-z0-9_]+|ghp_[A-Za-z0-9_]+|"
    r"(?i:password|secret|api[_-]?key)\s*[:=]\s*['\"]?[A-Za-z0-9_./+=-]{12,})"
)
PUBLIC_NAMING_PATTERNS = (
    (re.compile(r"\bQwenDex\b"), "Use Qwendex, not QwenDex."),
    (re.compile(r"\bQwen\s+Code\b", re.IGNORECASE), "Do not frame Qwendex as Qwen Code."),
    (re.compile(r"standalone\s+bridge", re.IGNORECASE), "Do not frame Qwendex as a standalone bridge."),
    (re.compile(r"\bbridge-first\b", re.IGNORECASE), "Do not frame Qwendex as bridge-first."),
    (re.compile(r"\bCodex/Qwen bridge\b", re.IGNORECASE), "Use Qwendex product language."),
)

MANAGER_MODE_ORDER = ("auto", "lite", "medium", "heavy", "manager")
MANAGER_MODE_ALIASES = {
    "": "",
    "auto": "auto",
    "lite": "lite",
    "medium": "medium",
    "heavy": "heavy",
    "manager": "manager",
    "manager_mode": "manager",
    "manager-only": "manager",
    "manager_only": "manager",
    "manual": "lite",
}
MANAGER_MODE_LABELS = {
    "auto": "Auto",
    "lite": "Lite",
    "medium": "Medium",
    "heavy": "Heavy",
    "manager": "Manager Mode",
}
MANAGER_REASONING_LEVELS = {"low", "medium", "high", "xhigh"}
MANAGER_DEPLOY_POLICIES = {"auto", "disabled"}
MANAGER_MAX_SUBAGENTS_LIMIT = 10
QWENDEX_CODEX_PATCH_MARKER = "QWENDEX_CODEX_TUI_PATCH_V1"
QWENDEX_CODEX_STATUS_ITEM_ID = "qwendex-manager"
QWENDEX_CODEX_STATUS_FILE_ENV = "QWENDEX_CODEX_STATUS_FILE"
CODEX_PATCH_MANIFESTS: dict[str, dict[str, Any]] = {
    "0.142.4": {
        "codex_tag": "rust-v0.142.4",
        "patch_marker": QWENDEX_CODEX_PATCH_MARKER,
        "status_line_item": QWENDEX_CODEX_STATUS_ITEM_ID,
        "status_file_env": QWENDEX_CODEX_STATUS_FILE_ENV,
        "keymap_actions": ["qwendex_toggle_manager", "qwendex_toggle_kaveman", "qwendex_toggle_local"],
        "toggle_commands": {
            "manager": "qwendex manager mode --toggle --json",
            "kaveman": "qwendex manager kaveman --toggle --json",
            "local": "qwendex manager local --toggle --json",
        },
        "source_anchors": [
            {
                "path": "codex-rs/tui/src/bottom_pane/status_line_setup.rs",
                "anchors": ["pub(crate) enum StatusLineItem", "TaskProgress"],
            },
            {
                "path": "codex-rs/tui/src/chatwidget/status_surfaces.rs",
                "anchors": ["status_line_value_for_item", "StatusLineItem::TaskProgress"],
            },
            {
                "path": "codex-rs/tui/src/bottom_pane/status_line_style.rs",
                "anchors": ["impl StatusLineAccent", "StatusLineItem::TaskProgress"],
            },
            {
                "path": "codex-rs/tui/src/bottom_pane/status_surface_preview.rs",
                "anchors": ["pub(crate) enum StatusSurfacePreviewItem", "StatusSurfacePreviewItem::TaskProgress"],
            },
            {
                "path": "codex-rs/config/src/tui_keymap.rs",
                "anchors": ["pub struct TuiGlobalKeymap", "toggle_raw_output"],
            },
            {
                "path": "codex-rs/tui/src/keymap.rs",
                "anchors": ["pub(crate) struct AppKeymap", "toggle_raw_output"],
            },
            {
                "path": "codex-rs/tui/src/app/input.rs",
                "anchors": ["app_keymap_shortcuts_available", "toggle_raw_output"],
            },
        ],
        "required_source_edits": [
            "Add StatusLineItem::QwendexManager serialized as qwendex-manager.",
            "Render qwendex-manager from QWENDEX_CODEX_STATUS_FILE JSON text.",
            "Add qwendex-manager to status preview and styling surfaces.",
            "Add global keymap actions qwendex_toggle_manager, qwendex_toggle_kaveman, and qwendex_toggle_local.",
            "Dispatch those actions before generic composer input handling.",
            "After each action, call the configured Qwendex toggle command and refresh status surfaces.",
        ],
    },
}

DEFAULT_CONFIG: dict[str, Any] = {
    "schema_version": "qwendex.config.v1",
    "version": VERSION,
    "default_seat": "primary",
    "routing": {
        "mode": "token_saver",
        "prefer_local_qwen_when_available": True,
        "local_probe_url": "http://127.0.0.1:1234/v1/models",
        "local_model": "qwen-local",
        "fallback_seat": "primary",
        "probe_timeout_seconds": 2,
        "prefer_for_task_classes": [
            "exec",
            "read-heavy audit",
            "docs draft",
            "queue workflow",
            "bounded patch",
            "smoke probe",
            "artifact summary",
        ],
        "primary_required_for_task_classes": [
            "release acceptance",
            "architecture",
            "security",
            "public docs claims",
            "protocol changes",
        ],
    },
    "context": {
        "compact_limit": 56000,
        "max_output_tokens": 2048,
        "tool_output_token_limit": 1200,
        "reminder_tool_call_threshold": 50,
        "reminder_repeat_interval": 25,
        "phase_boundary_labels": [
            "after-exploration",
            "before-implementation",
            "after-milestone",
            "before-verification",
            "handoff",
            "phase-boundary",
        ],
    },
    "guard": {
        "profile": "balanced",
        "max_wall_time_seconds": -1,
        "max_tool_calls": -1,
        "markers": [
            "LOCAL_MODEL_TOOL_CALL_TOO_LARGE",
            "LOCAL_MODEL_TOOL_CALL_TRUNCATED",
            "LOCAL_MODEL_TOOL_MARKUP_SUPPRESSED",
            "LOCAL_MODEL_LOOP_DETECTED",
            "LOCAL_QWEN_VALIDATOR_FAILED",
        ],
    },
    "sandbox": {
        "mode": "workspace-write",
        "trusted_roots": ["."],
    },
    "receipts": {
        "dir": "results/qwendex",
        "ledger": "~/.local/state/qwendex/qwendex.sqlite",
    },
    "state": {
        "db": "~/.local/state/qwendex/qwendex.sqlite",
    },
    "eval": {
        "mode": "offline-first",
        "default_case": "all",
        "live_requires_running_stack": True,
    },
    "learning": {
        "mode": "stage_only",
        "default_backend": "mock",
        "auto_harvest": True,
        "auto_stage_safe_proposals": True,
        "codex_budget_requires_approval": True,
    },
    "orchestration": {
        "mode": "auto",
        "manager_only_available": True,
        "shortcut": "Alt+M",
        "shortcut_command": "scripts/qwendex manager mode --toggle --json",
        "manager_deploy_policy": "auto",
        "max_subagents": 4,
        "stale_after_minutes": 30,
        "local_subagents": {
            "enabled": True,
            "shortcut": "Alt+L",
            "shortcut_command": "scripts/qwendex manager local --toggle --json",
        },
        "kaveman": {
            "enabled": False,
            "shortcut": "Alt+K",
            "shortcut_command": "scripts/qwendex manager kaveman --toggle --json",
            "directive": "Use terse output: short, direct, minimal prose, no optional explanation unless asked.",
        },
        "mode_order": list(MANAGER_MODE_ORDER),
        "mode_profiles": {
            "auto": {"label": "Auto", "offload_target": "auto", "max_subagents": 4},
            "lite": {"label": "Lite", "offload_target": "10-20%", "max_subagents": 2},
            "medium": {"label": "Medium", "offload_target": "25-45%", "max_subagents": 4},
            "heavy": {"label": "Heavy", "offload_target": "50-75%", "max_subagents": 6},
            "manager": {"label": "Manager Mode", "offload_target": "85-95%", "max_subagents": 10},
        },
        "estimator": {
            "enabled": True,
            "skill": "qwendex-auto-manager-estimator",
            "model": "gpt-5.5",
            "reasoning": "medium",
            "max_input_tokens": 1200,
            "max_output_tokens": 512,
        },
        "local_qwen_eligibility": {
            "allowed_task_classes": [
                "read-heavy audit",
                "docs draft",
                "queue workflow",
                "bounded patch",
                "smoke probe",
                "artifact summary",
                "review",
            ],
            "denied_task_classes": [
                "architecture",
                "security",
                "release acceptance",
                "public docs claims",
                "protocol changes",
            ],
            "max_risk": "medium",
        },
        "escalation_thresholds": {
            "high": ["architecture", "security", "release", "protocol", "migration", "schema"],
            "xhigh": ["security release", "credential", "public release acceptance", "protocol migration"],
        },
        "stale_session_thresholds_minutes": {
            "auto": 30,
            "lite": 45,
            "medium": 30,
            "heavy": 20,
            "manager": 15,
        },
        "close_stale_policy": "close completed agents immediately; close idle read-only agents after stale_after_minutes; never close an active writer without integrating or stopping it",
        "auto_deploy_when": [
            "multiple independent review lanes exist",
            "task changes both code and public docs",
            "security or release claims are in scope",
            "long-running verification can run beside integration work",
            "dirty worktree integration needs independent audit",
        ],
        "manager_responsibilities": [
            "split lanes with disjoint write surfaces",
            "keep critical-path fixes local",
            "integrate subagent findings",
            "close agents after review",
        ],
        "borrowed_patterns": [
            "LangGraph persistence/memory patterns inform durable state without adding a runtime dependency",
            "AutoGen teams/termination patterns inform lane stop conditions",
            "Anthropic effective agents/contextual retrieval patterns inform context packets and review gates",
            "SWE-agent trajectories and OpenHands-style eval harnesses inform receipts and replayable validation",
            "SWE-bench Verified, tau-bench, MCP security guidance, and Berkeley function-calling eval ideas inform release checks",
        ],
    },
    "mcp_tools": [
        "queue workflow",
        "document section upsert",
        "bounded report runner",
        "capped local search",
        "qwendex status",
        "receipt lookup",
        "eval summary",
        "learning proposal summary",
    ],
    "seats": {
        "primary": {
            "model": "gpt-5.5",
            "authority": "release_review",
            "backend": "codex",
            "context_window": 200000,
            "guard_profile": "balanced",
        },
        "qwen": {
            "model": "qwen-local",
            "authority": "bounded_operator",
            "backend": "local-responses-adapter",
            "context_window": 65536,
            "compact_limit": 56000,
            "guard_profile": "balanced",
            "prompt_template": "config/local_llm_stack/qwen3_codex_tool_plain.jinja",
        },
        "audit": {
            "model": "gpt-5.5",
            "authority": "read_only_review",
            "backend": "codex",
            "context_window": 200000,
            "guard_profile": "max_safety",
        },
        "release": {
            "model": "gpt-5.5",
            "authority": "public_release_acceptance",
            "backend": "codex",
            "context_window": 200000,
            "guard_profile": "max_safety",
        },
        "sandbox": {
            "model": "qwen-local",
            "authority": "isolated_probe",
            "backend": "local-responses-adapter",
            "context_window": 32768,
            "guard_profile": "max_safety",
        },
    },
}


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def deep_merge(base: dict[str, Any], update: Mapping[str, Any]) -> dict[str, Any]:
    merged = json.loads(json.dumps(base))
    for key, value in update.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def secret_like_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    if normalized.endswith("_token_limit") or normalized.endswith("_tokens"):
        return False
    exact = {
        "secret",
        "password",
        "credential",
        "credentials",
        "api_key",
        "apikey",
        "access_token",
        "refresh_token",
        "auth_token",
        "bearer_token",
        "private_key",
    }
    return normalized in exact or normalized.endswith("_secret") or normalized.endswith("_password")


def contains_secret_material(data: Any) -> bool:
    if isinstance(data, Mapping):
        for key, value in data.items():
            if secret_like_key(str(key)):
                return True
            if contains_secret_material(value):
                return True
    if isinstance(data, list):
        return any(contains_secret_material(item) for item in data)
    if isinstance(data, str):
        return SECRET_RE.search(data) is not None
    return False


def safe_load_json_file(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Qwendex config must be an object: {path}")
    if contains_secret_material(data):
        raise ValueError(f"Qwendex config must not contain secret-like keys or values: {path}")
    return data


def env_flag(value: str | None) -> bool | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return None


def normalize_manager_mode(value: Any) -> str:
    text = str(value or "").strip().lower().replace(" ", "_")
    return MANAGER_MODE_ALIASES.get(text, text)


def normalize_local_toggle(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "y", "on", "enable", "enabled"}:
        return True
    if text in {"0", "false", "no", "n", "off", "disable", "disabled"}:
        return False
    return None


def manager_mode_profile(config: Mapping[str, Any], mode: str) -> dict[str, Any]:
    normalized = normalize_manager_mode(mode) or "auto"
    profiles = config.get("orchestration", {}).get("mode_profiles", {})
    profile = profiles.get(normalized, {}) if isinstance(profiles, Mapping) else {}
    return {
        "mode": normalized,
        "label": str(profile.get("label") or MANAGER_MODE_LABELS.get(normalized, normalized.title())),
        "offload_target": str(profile.get("offload_target") or ("auto" if normalized == "auto" else "")),
        "max_subagents": profile.get("max_subagents", config.get("orchestration", {}).get("max_subagents", 4)),
    }


def manager_ui_indicator(config: Mapping[str, Any], mode: str) -> str:
    profile = manager_mode_profile(config, mode)
    shortcut = config.get("orchestration", {}).get("shortcut", "Alt+M")
    return f"({shortcut}) Agent Manager: [ {profile['label']} ]"


def local_indicator(config: Mapping[str, Any], enabled: bool) -> str:
    local_cfg = config.get("orchestration", {}).get("local_subagents", {})
    shortcut = local_cfg.get("shortcut", "Alt+L") if isinstance(local_cfg, Mapping) else "Alt+L"
    return f"({shortcut}) Local: [{'Y' if enabled else 'N'}]"


def kaveman_default_enabled(config: Mapping[str, Any]) -> bool:
    kaveman = config.get("orchestration", {}).get("kaveman", {})
    if isinstance(kaveman, Mapping) and isinstance(kaveman.get("enabled"), bool):
        return bool(kaveman["enabled"])
    return False


def kaveman_indicator(config: Mapping[str, Any], enabled: bool) -> str:
    kaveman = config.get("orchestration", {}).get("kaveman", {})
    shortcut = kaveman.get("shortcut", "Alt+K") if isinstance(kaveman, Mapping) else "Alt+K"
    return f"({shortcut}) Kaveman: [{'Y' if enabled else 'N'}]"


def kaveman_directive(config: Mapping[str, Any]) -> str:
    kaveman = config.get("orchestration", {}).get("kaveman", {})
    if isinstance(kaveman, Mapping) and isinstance(kaveman.get("directive"), str):
        return kaveman["directive"]
    return "Use terse output: short, direct, minimal prose, no optional explanation unless asked."


def estimator_config(config: Mapping[str, Any]) -> dict[str, Any]:
    estimator = config.get("orchestration", {}).get("estimator", {})
    return {
        "enabled": bool(estimator.get("enabled", True)) if isinstance(estimator, Mapping) else True,
        "skill": str(estimator.get("skill", "qwendex-auto-manager-estimator")) if isinstance(estimator, Mapping) else "qwendex-auto-manager-estimator",
        "model": str(estimator.get("model", "gpt-5.5")) if isinstance(estimator, Mapping) else "gpt-5.5",
        "reasoning": str(estimator.get("reasoning", "medium")) if isinstance(estimator, Mapping) else "medium",
        "max_input_tokens": int(estimator.get("max_input_tokens", 1200)) if isinstance(estimator, Mapping) else 1200,
        "max_output_tokens": int(estimator.get("max_output_tokens", 512)) if isinstance(estimator, Mapping) else 512,
    }


def env_config(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    source = env or os.environ
    data: dict[str, Any] = {}
    if source.get("QWENDEX_DEFAULT_SEAT"):
        data["default_seat"] = source["QWENDEX_DEFAULT_SEAT"]
    if source.get("QWENDEX_RESULTS_ROOT"):
        data["receipts"] = {"dir": source["QWENDEX_RESULTS_ROOT"]}
    if source.get("QWENDEX_LEDGER_DB"):
        data.setdefault("receipts", {})["ledger"] = source["QWENDEX_LEDGER_DB"]
    if source.get("QWENDEX_STATE_DB"):
        data["state"] = {"db": source["QWENDEX_STATE_DB"]}
    if source.get("QWENDEX_GUARD_PROFILE"):
        data["guard"] = {"profile": source["QWENDEX_GUARD_PROFILE"]}
    if source.get("QWENDEX_LEARNING_MODE"):
        data["learning"] = {"mode": source["QWENDEX_LEARNING_MODE"]}
    orchestration: dict[str, Any] = {}
    if source.get("QWENDEX_ORCHESTRATION_MODE"):
        orchestration["mode"] = source["QWENDEX_ORCHESTRATION_MODE"]
    if source.get("QWENDEX_MANAGER_MODE"):
        orchestration["mode"] = source["QWENDEX_MANAGER_MODE"]
    if source.get("QWENDEX_MANAGER_DEPLOY_POLICY"):
        orchestration["manager_deploy_policy"] = source["QWENDEX_MANAGER_DEPLOY_POLICY"]
    estimator: dict[str, Any] = {}
    if source.get("QWENDEX_ESTIMATOR_MODEL"):
        estimator["model"] = source["QWENDEX_ESTIMATOR_MODEL"]
    if source.get("QWENDEX_ESTIMATOR_REASONING"):
        estimator["reasoning"] = source["QWENDEX_ESTIMATOR_REASONING"]
    if estimator:
        orchestration["estimator"] = estimator
    local_enabled = normalize_local_toggle(source.get("QWENDEX_LOCAL_SUBAGENTS"))
    if local_enabled is not None:
        orchestration["local_subagents"] = {"enabled": local_enabled}
    kaveman_enabled = normalize_local_toggle(source.get("QWENDEX_KAVEMAN"))
    if kaveman_enabled is not None:
        orchestration["kaveman"] = {"enabled": kaveman_enabled}
    if orchestration:
        data["orchestration"] = orchestration
    routing: dict[str, Any] = {}
    if source.get("QWENDEX_ROUTING_MODE"):
        routing["mode"] = source["QWENDEX_ROUTING_MODE"]
    prefer_local = env_flag(source.get("QWENDEX_PREFER_LOCAL_QWEN"))
    if prefer_local is not None:
        routing["prefer_local_qwen_when_available"] = prefer_local
    if source.get("QWENDEX_LOCAL_QWEN_PROBE_URL"):
        routing["local_probe_url"] = source["QWENDEX_LOCAL_QWEN_PROBE_URL"]
    if source.get("QWENDEX_LOCAL_QWEN_MODEL"):
        routing["local_model"] = source["QWENDEX_LOCAL_QWEN_MODEL"]
    if source.get("QWENDEX_FALLBACK_SEAT"):
        routing["fallback_seat"] = source["QWENDEX_FALLBACK_SEAT"]
    if routing:
        data["routing"] = routing
    return data


def validate_qwendex_config(config: Mapping[str, Any]) -> list[str]:
    failures: list[str] = []
    allowed_top = set(DEFAULT_CONFIG)
    unknown = sorted(set(config) - allowed_top)
    failures.extend(f"unknown top-level key: {key}" for key in unknown)
    required = {"schema_version", "version", "default_seat", "routing", "guard", "receipts", "state", "eval", "learning", "seats"}
    missing = sorted(required - set(config))
    failures.extend(f"missing required key: {key}" for key in missing)
    if config.get("schema_version") != "qwendex.config.v1":
        failures.append(f"invalid schema_version: {config.get('schema_version')}")
    if config.get("default_seat") not in config.get("seats", {}):
        failures.append(f"unknown default_seat: {config.get('default_seat')}")
    routing = config.get("routing", {})
    if routing.get("mode") not in {"manual", "token_saver", "primary_only"}:
        failures.append(f"invalid routing.mode: {routing.get('mode')}")
    if not isinstance(routing.get("prefer_local_qwen_when_available"), bool):
        failures.append(f"invalid routing.prefer_local_qwen_when_available: {routing.get('prefer_local_qwen_when_available')}")
    if not isinstance(routing.get("local_model"), str) or not routing.get("local_model"):
        failures.append(f"invalid routing.local_model: {routing.get('local_model')}")
    probe_url = routing.get("local_probe_url")
    if not isinstance(probe_url, str) or not probe_url.startswith(("http://", "https://")):
        failures.append(f"invalid routing.local_probe_url: {probe_url}")
    timeout = routing.get("probe_timeout_seconds")
    if not isinstance(timeout, int | float) or timeout <= 0 or timeout > 30:
        failures.append(f"invalid routing.probe_timeout_seconds: {timeout}")
    if routing.get("fallback_seat") not in config.get("seats", {}):
        failures.append(f"unknown routing.fallback_seat: {routing.get('fallback_seat')}")
    for list_key in ("prefer_for_task_classes", "primary_required_for_task_classes"):
        values = routing.get(list_key)
        if not isinstance(values, list) or not all(isinstance(item, str) and item.strip() for item in values):
            failures.append(f"invalid routing.{list_key}: {values}")
    if config.get("guard", {}).get("profile") not in {"balanced", "max_safety"}:
        failures.append(f"invalid guard.profile: {config.get('guard', {}).get('profile')}")
    state_db = config.get("state", {}).get("db")
    if not isinstance(state_db, str) or not state_db:
        failures.append(f"invalid state.db: {state_db}")
    if config.get("learning", {}).get("mode") not in {"stage_only", "manual", "disabled"}:
        failures.append(f"invalid learning.mode: {config.get('learning', {}).get('mode')}")
    if config.get("learning", {}).get("default_backend") not in {"mock", "codex"}:
        failures.append(f"invalid learning.default_backend: {config.get('learning', {}).get('default_backend')}")
    orchestration = config.get("orchestration", {})
    if normalize_manager_mode(orchestration.get("mode")) not in set(MANAGER_MODE_ORDER):
        failures.append(f"invalid orchestration.mode: {config.get('orchestration', {}).get('mode')}")
    max_subagents = orchestration.get("max_subagents", 0)
    if not isinstance(max_subagents, int) or max_subagents < 1 or max_subagents > MANAGER_MAX_SUBAGENTS_LIMIT:
        failures.append(f"invalid orchestration.max_subagents: {max_subagents}")
    if orchestration.get("manager_deploy_policy", "auto") not in MANAGER_DEPLOY_POLICIES:
        failures.append(f"invalid orchestration.manager_deploy_policy: {orchestration.get('manager_deploy_policy')}")
    stale_after = orchestration.get("stale_after_minutes", 0)
    if not isinstance(stale_after, int) or stale_after < 5 or stale_after > 240:
        failures.append(f"invalid orchestration.stale_after_minutes: {stale_after}")
    local_subagents = orchestration.get("local_subagents", {})
    if not isinstance(local_subagents, Mapping):
        failures.append("invalid orchestration.local_subagents")
    else:
        if not isinstance(local_subagents.get("enabled"), bool):
            failures.append(f"invalid orchestration.local_subagents.enabled: {local_subagents.get('enabled')}")
        if not isinstance(local_subagents.get("shortcut"), str) or not local_subagents.get("shortcut"):
            failures.append(f"invalid orchestration.local_subagents.shortcut: {local_subagents.get('shortcut')}")
        if not isinstance(local_subagents.get("shortcut_command"), str) or not local_subagents.get("shortcut_command"):
            failures.append(f"invalid orchestration.local_subagents.shortcut_command: {local_subagents.get('shortcut_command')}")
    kaveman = orchestration.get("kaveman", {})
    if not isinstance(kaveman, Mapping):
        failures.append("invalid orchestration.kaveman")
    else:
        if not isinstance(kaveman.get("enabled"), bool):
            failures.append(f"invalid orchestration.kaveman.enabled: {kaveman.get('enabled')}")
        if not isinstance(kaveman.get("shortcut"), str) or not kaveman.get("shortcut"):
            failures.append(f"invalid orchestration.kaveman.shortcut: {kaveman.get('shortcut')}")
        if not isinstance(kaveman.get("shortcut_command"), str) or not kaveman.get("shortcut_command"):
            failures.append(f"invalid orchestration.kaveman.shortcut_command: {kaveman.get('shortcut_command')}")
        if not isinstance(kaveman.get("directive"), str) or not kaveman.get("directive"):
            failures.append(f"invalid orchestration.kaveman.directive: {kaveman.get('directive')}")
    mode_order = orchestration.get("mode_order", list(MANAGER_MODE_ORDER))
    if [normalize_manager_mode(item) for item in mode_order] != list(MANAGER_MODE_ORDER):
        failures.append(f"invalid orchestration.mode_order: {mode_order}")
    profiles = orchestration.get("mode_profiles", {})
    if not isinstance(profiles, Mapping):
        failures.append("invalid orchestration.mode_profiles")
    else:
        for mode in MANAGER_MODE_ORDER:
            profile = profiles.get(mode, {})
            if not isinstance(profile, Mapping):
                failures.append(f"invalid orchestration.mode_profiles.{mode}")
                continue
            if not isinstance(profile.get("label"), str) or not profile.get("label"):
                failures.append(f"invalid orchestration.mode_profiles.{mode}.label")
            if not isinstance(profile.get("offload_target"), str) or not profile.get("offload_target"):
                failures.append(f"invalid orchestration.mode_profiles.{mode}.offload_target")
            profile_max = profile.get("max_subagents", max_subagents)
            if not isinstance(profile_max, int) or profile_max < 1 or profile_max > MANAGER_MAX_SUBAGENTS_LIMIT:
                failures.append(f"invalid orchestration.mode_profiles.{mode}.max_subagents: {profile_max}")
    estimator = orchestration.get("estimator", {})
    if not isinstance(estimator, Mapping):
        failures.append("invalid orchestration.estimator")
    else:
        if not isinstance(estimator.get("model"), str) or not estimator.get("model"):
            failures.append(f"invalid orchestration.estimator.model: {estimator.get('model')}")
        if estimator.get("reasoning") not in MANAGER_REASONING_LEVELS:
            failures.append(f"invalid orchestration.estimator.reasoning: {estimator.get('reasoning')}")
        for token_key in ("max_input_tokens", "max_output_tokens"):
            value = estimator.get(token_key)
            if not isinstance(value, int) or value < 64 or value > 20000:
                failures.append(f"invalid orchestration.estimator.{token_key}: {value}")
    local_eligibility = orchestration.get("local_qwen_eligibility", {})
    if not isinstance(local_eligibility, Mapping):
        failures.append("invalid orchestration.local_qwen_eligibility")
    else:
        for list_key in ("allowed_task_classes", "denied_task_classes"):
            values = local_eligibility.get(list_key)
            if not isinstance(values, list) or not all(isinstance(item, str) and item.strip() for item in values):
                failures.append(f"invalid orchestration.local_qwen_eligibility.{list_key}: {values}")
        if local_eligibility.get("max_risk") not in {"low", "medium", "high"}:
            failures.append(f"invalid orchestration.local_qwen_eligibility.max_risk: {local_eligibility.get('max_risk')}")
    stale_thresholds = orchestration.get("stale_session_thresholds_minutes", {})
    if not isinstance(stale_thresholds, Mapping):
        failures.append("invalid orchestration.stale_session_thresholds_minutes")
    else:
        for mode in MANAGER_MODE_ORDER:
            threshold = stale_thresholds.get(mode, stale_after)
            if not isinstance(threshold, int) or threshold < 5 or threshold > 240:
                failures.append(f"invalid orchestration.stale_session_thresholds_minutes.{mode}: {threshold}")
    for seat in ("primary", "qwen", "audit", "release", "sandbox"):
        if seat not in config.get("seats", {}):
            failures.append(f"missing seat: {seat}")
    return failures


def load_qwendex_config(
    *,
    cli_overrides: Mapping[str, Any] | None = None,
    env: Mapping[str, str] | None = None,
    project_config: Path | None = None,
    user_config: Path | None = None,
) -> dict[str, Any]:
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    config = deep_merge(config, safe_load_json_file(user_config or DEFAULT_USER_CONFIG))
    config = deep_merge(config, safe_load_json_file(project_config or DEFAULT_PROJECT_CONFIG))
    config = deep_merge(config, env_config(env))
    if cli_overrides:
        config = deep_merge(config, {key: value for key, value in cli_overrides.items() if value not in (None, "")})
    failures = validate_qwendex_config(config)
    if failures:
        raise ValueError("; ".join(failures))
    return config


def redact_text(text: str) -> str:
    return SECRET_RE.sub("[redacted]", text)


def redact_obj(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): redact_obj(item) for key, item in value.items() if not secret_like_key(str(key))}
    if isinstance(value, list):
        return [redact_obj(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def stable_envelope(
    *,
    command: str,
    status: str,
    summary: str,
    artifacts: list[str] | None = None,
    next_actions: list[str] | None = None,
    errors: list[str] | None = None,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "qwendex.cli.v1",
        "command": command,
        "status": status,
        "summary": redact_text(summary),
        "version": VERSION,
        "artifacts": artifacts or [],
        "next_actions": next_actions or [],
        "errors": redact_obj(errors or []),
        "data": redact_obj(data or {}),
    }


def print_json(data: dict[str, Any]) -> None:
    print(json.dumps(data, indent=2, sort_keys=True))


def script_module(module_name: str) -> Any:
    module_path = ROOT / "scripts" / f"{module_name}.py"
    spec = importlib.util.spec_from_file_location(f"{module_name}_qwendex", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module: {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def results_root(config: Mapping[str, Any]) -> Path:
    raw = str(config.get("receipts", {}).get("dir") or DEFAULT_RESULTS_ROOT)
    path = Path(raw).expanduser()
    return path if path.is_absolute() else ROOT / path


def configured_ledger_path(config: Mapping[str, Any]) -> Path:
    raw = str(config.get("receipts", {}).get("ledger") or DEFAULT_CONFIG["receipts"]["ledger"])
    path = Path(raw).expanduser()
    return path if path.is_absolute() else ROOT / path


def state_db_path(config: Mapping[str, Any]) -> Path:
    raw = str(config.get("state", {}).get("db") or DEFAULT_CONFIG["state"]["db"])
    path = Path(raw).expanduser()
    return path if path.is_absolute() else ROOT / path


def routing_policy(config: Mapping[str, Any]) -> dict[str, Any]:
    routing = config.get("routing", {})
    return {
        "mode": routing.get("mode", "token_saver"),
        "prefer_local_qwen_when_available": bool(routing.get("prefer_local_qwen_when_available", True)),
        "local_probe_url": str(routing.get("local_probe_url", DEFAULT_CONFIG["routing"]["local_probe_url"])),
        "local_model": str(routing.get("local_model", DEFAULT_CONFIG["routing"]["local_model"])),
        "fallback_seat": str(routing.get("fallback_seat", "primary")),
        "probe_timeout_seconds": routing.get("probe_timeout_seconds", 2),
        "prefer_for_task_classes": list(routing.get("prefer_for_task_classes", [])),
        "primary_required_for_task_classes": list(routing.get("primary_required_for_task_classes", [])),
    }


def task_class_matches(task_class: str, configured: list[str]) -> bool:
    normalized = task_class.strip().lower()
    return normalized in {item.strip().lower() for item in configured}


def text_contains_any(text: str, needles: list[str]) -> bool:
    normalized = text.strip().lower()
    return any(item.strip().lower() in normalized for item in needles if item.strip())


def forced_local_qwen_probe(env: Mapping[str, str]) -> bool | None:
    available = env_flag(env.get("QWENDEX_FORCE_LOCAL_QWEN_AVAILABLE"))
    unavailable = env_flag(env.get("QWENDEX_FORCE_LOCAL_QWEN_UNAVAILABLE"))
    if unavailable is True:
        return False
    if available is not None:
        return available
    return None


def probe_local_qwen(config: Mapping[str, Any], *, env: Mapping[str, str] | None = None) -> dict[str, Any]:
    source = env or os.environ
    policy = routing_policy(config)
    model = policy["local_model"]
    url = policy["local_probe_url"]
    forced = forced_local_qwen_probe(source)
    if forced is not None:
        return {
            "available": forced,
            "source": "env",
            "url": url,
            "model": model,
            "visible_models": [model] if forced else [],
            "reason": "forced_available" if forced else "forced_unavailable",
        }
    try:
        timeout = float(policy["probe_timeout_seconds"])
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(512_000).decode("utf-8", errors="replace")
        payload = json.loads(raw)
        visible_models = [
            str(item.get("id"))
            for item in payload.get("data", [])
            if isinstance(item, Mapping) and item.get("id")
        ]
        available = model in visible_models
        return {
            "available": available,
            "source": "probe",
            "url": url,
            "model": model,
            "visible_models": visible_models[:50],
            "reason": "model_visible" if available else "model_not_visible",
        }
    except (OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError) as exc:
        return {
            "available": False,
            "source": "probe",
            "url": url,
            "model": model,
            "visible_models": [],
            "reason": redact_text(str(exc) or exc.__class__.__name__),
        }


def local_subagents_default_enabled(config: Mapping[str, Any]) -> bool:
    local_cfg = config.get("orchestration", {}).get("local_subagents", {})
    if isinstance(local_cfg, Mapping) and isinstance(local_cfg.get("enabled"), bool):
        return bool(local_cfg["enabled"])
    return True


def local_subagent_status(
    config: Mapping[str, Any],
    *,
    enabled: bool | None = None,
    env: Mapping[str, str] | None = None,
    probe: bool = False,
) -> dict[str, Any]:
    is_enabled = local_subagents_default_enabled(config) if enabled is None else bool(enabled)
    policy = routing_policy(config)
    if not is_enabled:
        return {
            "enabled": False,
            "available": False,
            "usable": False,
            "indicator": local_indicator(config, False),
            "source": "state",
            "reason": "toggle_off",
            "model": policy["local_model"],
        }
    forced = forced_local_qwen_probe(env or os.environ)
    should_probe = probe or forced is not None
    probe_result = probe_local_qwen(config, env=env) if should_probe else {
        "available": None,
        "source": "not_probed",
        "model": policy["local_model"],
        "reason": "enabled_not_probed",
    }
    available = probe_result.get("available")
    return {
        "enabled": True,
        "available": available,
        "usable": bool(available),
        "indicator": local_indicator(config, True),
        "source": probe_result.get("source", "not_probed"),
        "reason": probe_result.get("reason", ""),
        "model": probe_result.get("model", policy["local_model"]),
        "probe": probe_result,
    }


def risk_rank(risk: str) -> int:
    return {"low": 1, "medium": 2, "high": 3}.get(risk.strip().lower(), 2)


def infer_task_class(prompt: str) -> str:
    text = prompt.lower()
    if any(word in text for word in ("security", "credential", "auth", "threat")):
        return "security"
    if any(word in text for word in ("release", "ship", "acceptance")):
        return "release acceptance"
    if any(word in text for word in ("architecture", "protocol", "migration", "schema")):
        return "architecture"
    if any(word in text for word in ("receipt", "artifact", "summarize", "summary")):
        return "artifact summary"
    if any(word in text for word in ("doc", "readme", "typo", "copy")):
        return "docs draft"
    return "bounded patch"


def is_local_qwen_lane_eligible(
    config: Mapping[str, Any],
    *,
    task_class: str,
    risk: str,
    local_enabled: bool,
) -> bool:
    if not local_enabled:
        return False
    eligibility = config.get("orchestration", {}).get("local_qwen_eligibility", {})
    if not isinstance(eligibility, Mapping):
        return False
    denied = list(eligibility.get("denied_task_classes", []))
    allowed = list(eligibility.get("allowed_task_classes", []))
    if task_class_matches(task_class, denied) or text_contains_any(task_class, denied):
        return False
    max_risk = str(eligibility.get("max_risk", "medium"))
    if risk_rank(risk) > risk_rank(max_risk):
        return False
    return task_class_matches(task_class, allowed) or text_contains_any(task_class, allowed)


def lane_model_reasoning(
    config: Mapping[str, Any],
    *,
    task_class: str,
    lane: str = "",
    risk: str = "medium",
    local_status: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    estimator = estimator_config(config)
    local = local_status or local_subagent_status(config, probe=False)
    combined = " ".join([task_class, lane]).strip().lower()
    thresholds = config.get("orchestration", {}).get("escalation_thresholds", {})
    xhigh_terms = list(thresholds.get("xhigh", [])) if isinstance(thresholds, Mapping) else []
    high_terms = list(thresholds.get("high", [])) if isinstance(thresholds, Mapping) else []
    local_eligible = is_local_qwen_lane_eligible(
        config,
        task_class=task_class,
        risk=risk,
        local_enabled=bool(local.get("enabled")),
    )
    local_usable = local_eligible and bool(local.get("usable"))
    if text_contains_any(combined, xhigh_terms):
        selected_reasoning = "xhigh"
        source = "lane_escalation"
        escalation = "xhigh threshold matched for high-risk lane"
    elif risk_rank(risk) >= 3 or text_contains_any(combined, high_terms):
        selected_reasoning = "high"
        source = "lane_escalation"
        escalation = "high threshold matched for architecture/security/release/protocol lane"
    elif local_usable:
        selected_reasoning = "low"
        source = "local_qwen_token_saver"
        escalation = ""
    else:
        selected_reasoning = estimator["reasoning"]
        source = "default_policy"
        escalation = ""
    selected_model = str(config.get("routing", {}).get("local_model", "qwen-local")) if local_usable else estimator["model"]
    return {
        "selected_model": selected_model,
        "selected_reasoning": selected_reasoning,
        "reasoning_source": source,
        "escalation_reason": escalation,
        "token_saver_used": local_usable,
        "local_qwen_eligible": local_eligible,
        "local_qwen_available": local.get("available"),
    }


def reasoning_policy(config: Mapping[str, Any], local_status: Mapping[str, Any] | None = None) -> dict[str, Any]:
    local = local_status or local_subagent_status(config, probe=False)
    return {
        "main_session": {
            "selected_model": "user-selected",
            "selected_reasoning": "user-selected",
            "reasoning_source": "user_selected",
            "escalation_reason": "",
            "token_saver_used": False,
            "local_qwen_eligible": False,
        },
        "default_lane": lane_model_reasoning(config, task_class="artifact summary", lane="default", risk="low", local_status=local),
        "high_risk_lane": lane_model_reasoning(config, task_class="security", lane="review", risk="high", local_status=local),
    }


def resolve_route(
    config: Mapping[str, Any],
    *,
    requested_seat: str = "auto",
    task_class: str = "exec",
    env: Mapping[str, str] | None = None,
    prefer_local: bool = False,
    local_enabled: bool | None = None,
) -> dict[str, Any]:
    policy = routing_policy(config)
    requested = requested_seat or "auto"
    seats = config.get("seats", {})
    local_status = local_subagent_status(config, enabled=local_enabled, env=env, probe=False)
    if requested != "auto":
        seat = requested if requested in seats else str(config.get("default_seat", "primary"))
        explicit_local_qwen = seat == "qwen"
        return {
            "requested_seat": requested,
            "seat": seat,
            "model": seats.get(seat, {}).get("model", ""),
            "selected_model": seats.get(seat, {}).get("model", ""),
            "selected_reasoning": "user-selected",
            "reasoning_source": "explicit_seat",
            "escalation_reason": "",
            "token_saver_used": False,
            "local_qwen_eligible": explicit_local_qwen and bool(local_status.get("enabled")),
            "task_class": task_class,
            "reason": "explicit_seat",
            "local_qwen": {"available": None, "source": "not_probed", "model": policy["local_model"]},
            "local_subagents": local_status,
            "routing": policy,
        }
    if policy["mode"] == "primary_only":
        seat = policy["fallback_seat"]
        return {
            "requested_seat": requested,
            "seat": seat,
            "model": seats.get(seat, {}).get("model", ""),
            "selected_model": seats.get(seat, {}).get("model", ""),
            "selected_reasoning": "medium",
            "reasoning_source": "routing_primary_only",
            "escalation_reason": "",
            "token_saver_used": False,
            "local_qwen_eligible": False,
            "task_class": task_class,
            "reason": "routing_primary_only",
            "local_qwen": {"available": None, "source": "not_probed", "model": policy["local_model"]},
            "local_subagents": local_status,
            "routing": policy,
        }
    if policy["mode"] == "manual":
        seat = str(config.get("default_seat", policy["fallback_seat"]))
        return {
            "requested_seat": requested,
            "seat": seat,
            "model": seats.get(seat, {}).get("model", ""),
            "selected_model": seats.get(seat, {}).get("model", ""),
            "selected_reasoning": "medium",
            "reasoning_source": "routing_manual_default",
            "escalation_reason": "",
            "token_saver_used": False,
            "local_qwen_eligible": False,
            "task_class": task_class,
            "reason": "routing_manual_default",
            "local_qwen": {"available": None, "source": "not_probed", "model": policy["local_model"]},
            "local_subagents": local_status,
            "routing": policy,
        }
    local_intent = prefer_local or (
        policy["prefer_local_qwen_when_available"]
        and task_class_matches(task_class, policy["prefer_for_task_classes"])
        and not task_class_matches(task_class, policy["primary_required_for_task_classes"])
    )
    should_prefer_local = bool(local_status["enabled"]) and local_intent
    local_qwen = probe_local_qwen(config, env=env) if should_prefer_local else {
        "available": None,
        "source": "not_probed",
        "model": policy["local_model"],
        "reason": "local_subagents_disabled" if local_intent else "task_class_not_preferred",
    }
    seat = "qwen" if should_prefer_local and local_qwen.get("available") else policy["fallback_seat"]
    disabled_by_toggle = local_intent and not local_status["enabled"]
    return {
        "requested_seat": requested,
        "seat": seat,
        "model": seats.get(seat, {}).get("model", ""),
        "selected_model": seats.get(seat, {}).get("model", ""),
        "selected_reasoning": "low" if seat == "qwen" else "medium",
        "reasoning_source": "local_qwen_token_saver" if seat == "qwen" else "fallback_policy",
        "escalation_reason": "",
        "token_saver_used": seat == "qwen",
        "local_qwen_eligible": should_prefer_local,
        "task_class": task_class,
        "reason": "local_qwen_available" if seat == "qwen" else ("local_subagents_disabled" if disabled_by_toggle else "fallback_seat"),
        "local_qwen": local_qwen,
        "local_subagents": {**local_status, "available": local_qwen.get("available") if should_prefer_local else local_status.get("available"), "usable": bool(local_status.get("enabled")) and seat == "qwen"},
        "routing": policy,
    }


def json_dumps(value: Any) -> str:
    return json.dumps(redact_obj(value), sort_keys=True)


def json_loads_list(value: str | None) -> list[Any]:
    if not value:
        return []
    data = json.loads(value)
    return data if isinstance(data, list) else []


def make_id(prefix: str) -> str:
    return f"{prefix}_{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')}"


def connect_state(config: Mapping[str, Any]) -> sqlite3.Connection:
    path = state_db_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    ensure_state_schema(conn)
    return conn


def ensure_table_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def ensure_state_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS qwendex_manager_settings (
          key TEXT PRIMARY KEY,
          value_json TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS qwendex_tasks (
          task_id TEXT PRIMARY KEY,
          title TEXT NOT NULL,
          priority TEXT NOT NULL,
          owner TEXT NOT NULL,
          phase TEXT NOT NULL,
          status TEXT NOT NULL,
          summary TEXT NOT NULL,
          blocked_reason TEXT NOT NULL,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          started_at TEXT NOT NULL,
          finished_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS qwendex_agent_sessions (
          agent_id TEXT PRIMARY KEY,
          lane TEXT NOT NULL,
          task_id TEXT NOT NULL,
          owner TEXT NOT NULL,
          write_surface TEXT NOT NULL,
          stop_condition TEXT NOT NULL,
          artifacts_json TEXT NOT NULL,
          status TEXT NOT NULL,
          heartbeat_at TEXT NOT NULL,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          stop_reason TEXT NOT NULL,
          close_receipt TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS qwendex_context_snapshots (
          snapshot_id TEXT PRIMARY KEY,
          task_id TEXT NOT NULL,
          objective TEXT NOT NULL,
          decisions_json TEXT NOT NULL,
          open_files_json TEXT NOT NULL,
          evidence_refs_json TEXT NOT NULL,
          blocked_items_json TEXT NOT NULL,
          next_actions_json TEXT NOT NULL,
          budget INTEGER NOT NULL,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS qwendex_handoffs (
          handoff_id TEXT PRIMARY KEY,
          task_id TEXT NOT NULL,
          status TEXT NOT NULL,
          summary TEXT NOT NULL,
          evidence_refs_json TEXT NOT NULL,
          next_actions_json TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS qwendex_evidence (
          evidence_id TEXT PRIMARY KEY,
          task_id TEXT NOT NULL,
          claim TEXT NOT NULL,
          path TEXT NOT NULL,
          sha256 TEXT NOT NULL,
          kind TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS qwendex_receipt_links (
          run_id TEXT PRIMARY KEY,
          task_id TEXT NOT NULL,
          phase_id TEXT NOT NULL,
          receipt_path TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        """
    )
    ensure_table_column(conn, "qwendex_agent_sessions", "context_packet_json", "TEXT NOT NULL DEFAULT '{}'")
    ensure_table_column(conn, "qwendex_agent_sessions", "routing_json", "TEXT NOT NULL DEFAULT '{}'")
    ensure_table_column(conn, "qwendex_agent_sessions", "validation_status", "TEXT NOT NULL DEFAULT 'pending'")
    conn.commit()


def row_to_task(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def row_to_agent_session(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row)
    data["artifacts"] = json_loads_list(data.pop("artifacts_json", "[]"))
    context_packet = data.pop("context_packet_json", "{}")
    routing = data.pop("routing_json", "{}")
    try:
        parsed_context = json.loads(context_packet) if context_packet else {}
    except json.JSONDecodeError:
        parsed_context = {}
    try:
        parsed_routing = json.loads(routing) if routing else {}
    except json.JSONDecodeError:
        parsed_routing = {}
    data["context_packet"] = parsed_context if isinstance(parsed_context, dict) else {}
    data["routing"] = parsed_routing if isinstance(parsed_routing, dict) else {}
    return data


def row_to_context_snapshot(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row)
    for key in ("decisions", "open_files", "evidence_refs", "blocked_items", "next_actions"):
        data[key] = json_loads_list(data.pop(f"{key}_json", "[]"))
    return data


def row_to_handoff(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row)
    data["evidence_refs"] = json_loads_list(data.pop("evidence_refs_json", "[]"))
    data["next_actions"] = json_loads_list(data.pop("next_actions_json", "[]"))
    return data


def row_to_evidence(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def get_manager_setting(conn: sqlite3.Connection, key: str, default: Any) -> Any:
    row = conn.execute("SELECT value_json FROM qwendex_manager_settings WHERE key = ?", (key,)).fetchone()
    if row is None:
        return default
    try:
        return json.loads(row["value_json"])
    except json.JSONDecodeError:
        return default


def set_manager_setting(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute(
        """
        INSERT INTO qwendex_manager_settings (key, value_json, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at
        """,
        (key, json_dumps(value), utc_now()),
    )


def current_manager_mode(config: Mapping[str, Any], conn: sqlite3.Connection, explicit: str = "") -> str:
    if explicit:
        return normalize_manager_mode(explicit)
    stored = get_manager_setting(conn, "selected_mode", "")
    mode = normalize_manager_mode(stored)
    if mode:
        return mode
    return normalize_manager_mode(config.get("orchestration", {}).get("mode")) or "auto"


def current_local_enabled(config: Mapping[str, Any], conn: sqlite3.Connection) -> bool:
    stored = get_manager_setting(conn, "local_subagents_enabled", None)
    parsed = normalize_local_toggle(stored)
    return local_subagents_default_enabled(config) if parsed is None else parsed


def current_kaveman_enabled(config: Mapping[str, Any], conn: sqlite3.Connection) -> bool:
    stored = get_manager_setting(conn, "kaveman_enabled", None)
    parsed = normalize_local_toggle(stored)
    return kaveman_default_enabled(config) if parsed is None else parsed


def stale_age_seconds(row: Mapping[str, Any]) -> float:
    try:
        return (datetime.now(UTC) - parse_utc(str(row["heartbeat_at"]))).total_seconds()
    except (KeyError, ValueError):
        return 0.0


def summarize_agent_sessions(
    sessions: list[dict[str, Any]],
    *,
    stale_after_minutes: int,
) -> dict[str, Any]:
    active = [session for session in sessions if session.get("status") == "active"]
    stale = [
        session
        for session in active
        if stale_age_seconds(session) >= stale_after_minutes * 60
    ]
    receipts = [
        session.get("context_packet", {}).get("receipt_path", "")
        for session in sessions
        if session.get("context_packet", {}).get("receipt_path")
    ]
    files_touched = sorted({
        path
        for session in sessions
        for path in session.get("context_packet", {}).get("exact_files", [])
    })
    blockers = [
        session.get("stop_reason", "")
        for session in sessions
        if session.get("stop_reason") and session.get("status") != "closed"
    ]
    validation_counts = {"pending": 0, "pass": 0, "fail": 0}
    for session in sessions:
        status = str(session.get("validation_status") or "pending")
        validation_counts[status] = validation_counts.get(status, 0) + 1
    return {
        "active_subagents": {"count": len(active), "agents": active},
        "stale_sessions": {"count": len(stale), "agents": stale},
        "subagent_state": {
            "context_used": sum(int(session.get("context_packet", {}).get("context_budget") or 0) for session in sessions),
            "files_touched": files_touched,
            "blockers": blockers,
            "receipts": receipts,
            "validation_status": validation_counts,
            "unresolved_risks": [
                session.get("context_packet", {}).get("risk", "")
                for session in active
                if session.get("context_packet", {}).get("risk") in {"medium", "high"}
            ],
        },
    }


def manager_deploy_policy(config: Mapping[str, Any]) -> str:
    raw = str(config.get("orchestration", {}).get("manager_deploy_policy", "auto")).strip().lower()
    return raw if raw in MANAGER_DEPLOY_POLICIES else "auto"


def manager_deployment_contract(mode: str, policy: str, active_count: int) -> dict[str, Any]:
    required = normalize_manager_mode(mode) == "manager" and policy == "auto"
    healthy = not required or active_count > 0
    return {
        "policy": policy,
        "required": required,
        "active_count": active_count,
        "healthy": healthy,
        "status": "pass" if healthy else "blocked",
        "summary": (
            "Manager Mode has active agent lanes."
            if required and healthy
            else "Manager deployment is disabled by policy."
            if policy == "disabled"
            else "Manager deployment is not required for this mode."
            if not required
            else "Manager Mode requires at least one active agent lane."
        ),
    }


def high_value_add_lines(local_status: Mapping[str, Any], *, release_risk: str = "medium") -> list[str]:
    lines: list[str] = []
    if local_status.get("enabled"):
        suffix = "local Qwen is available." if local_status.get("usable") else "local Qwen is enabled but not confirmed live."
        lines.append(f"High-value add: run qwendex eval --live --json before release; {suffix}")
    else:
        lines.append("High-value add: local subagents are toggled off; use Alt+L before delegating bounded receipt work.")
    if release_risk in {"medium", "high"}:
        lines.append("High-value add: escalate only the security-review lane to high; main session can stay user-selected.")
    return lines[:2]


def manager_self_estimate(
    config: Mapping[str, Any],
    *,
    mode: str,
    local_status: Mapping[str, Any],
    stale_pressure: str = "none",
    validation_confidence: str = "medium",
    release_risk: str = "medium",
) -> dict[str, Any]:
    profile = manager_mode_profile(config, mode)
    return {
        "mode": profile["mode"],
        "label": profile["label"],
        "offload_target": profile["offload_target"],
        "harness_completeness": "surface_ready",
        "validation_confidence": validation_confidence,
        "release_risk": release_risk,
        "local_qwen_availability": local_status,
        "stale_subagent_pressure": stale_pressure,
        "context_pressure": "normal",
        "next_best_validation": "scripts/qwendex eval --json",
        "reasoning_policy": reasoning_policy(config, local_status),
    }


def estimate_task(
    config: Mapping[str, Any],
    *,
    prompt: str,
    local_status: Mapping[str, Any],
) -> dict[str, Any]:
    text = prompt.strip()
    lower = text.lower()
    task_class = infer_task_class(text)
    high_risk = any(word in lower for word in ("security", "credential", "release", "protocol", "architecture", "migration"))
    many_files = any(word in lower for word in ("several", "multiple", "across", "many"))
    validation_heavy = any(word in lower for word in ("test", "eval", "release", "security", "protocol"))
    if high_risk and many_files:
        recommended = "manager"
        complexity = "heavy"
        usefulness = "high"
        risk = "high"
        scope = "many_files"
    elif high_risk or many_files:
        recommended = "heavy"
        complexity = "medium"
        usefulness = "medium"
        risk = "high" if high_risk else "medium"
        scope = "several_files" if many_files else "few_files"
    elif any(word in lower for word in ("typo", "small", "one file", "single")):
        recommended = "lite"
        complexity = "simple"
        usefulness = "low"
        risk = "low"
        scope = "single_file"
    else:
        recommended = "medium"
        complexity = "medium"
        usefulness = "medium"
        risk = "medium"
        scope = "few_files"
    default_lane = lane_model_reasoning(config, task_class=task_class, lane="default", risk=risk, local_status=local_status)
    higher_lanes: list[dict[str, Any]] = []
    if risk == "high":
        lane = lane_model_reasoning(config, task_class=task_class, lane="review", risk="high", local_status=local_status)
        higher_lanes.append({"lane": f"{task_class}-review", **lane})
    return {
        "task_complexity": complexity,
        "risk": risk,
        "likely_file_scope": scope,
        "validation_depth": "full" if validation_heavy or risk == "high" else "focused",
        "subagent_usefulness": usefulness,
        "recommended_mode": recommended,
        "confidence": "medium" if text else "low",
        "higher_reasoning_lanes": higher_lanes,
        "task_class": task_class,
        "default_lane": default_lane,
    }


def manager_mode_payload(
    config: Mapping[str, Any],
    *,
    mode: str,
    local_status: Mapping[str, Any],
    max_subagents: int,
    stale_after_minutes: int,
    kaveman_enabled: bool = False,
    legacy_mode: str = "",
    sessions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    profile = manager_mode_profile(config, mode)
    summary = summarize_agent_sessions(sessions or [], stale_after_minutes=stale_after_minutes)
    data = {
        "mode": profile["mode"],
        "label": profile["label"],
        "legacy_mode": legacy_mode,
        "ui_indicator": manager_ui_indicator(config, profile["mode"]),
        "kaveman_indicator": kaveman_indicator(config, kaveman_enabled),
        "kaveman_enabled": kaveman_enabled,
        "kaveman_directive": kaveman_directive(config) if kaveman_enabled else "",
        "local_indicator": local_status["indicator"],
        "local_subagents": local_status,
        "offload_target": profile["offload_target"],
        "shortcut": config["orchestration"]["shortcut"],
        "shortcut_command": config["orchestration"]["shortcut_command"],
        "local_shortcut": config["orchestration"].get("local_subagents", {}).get("shortcut", "Alt+L"),
        "local_shortcut_command": config["orchestration"].get("local_subagents", {}).get("shortcut_command", "scripts/qwendex manager local --toggle --json"),
        "kaveman_shortcut": config["orchestration"].get("kaveman", {}).get("shortcut", "Alt+K"),
        "kaveman_shortcut_command": config["orchestration"].get("kaveman", {}).get("shortcut_command", "scripts/qwendex manager kaveman --toggle --json"),
        "shortcut_note": "Bind shortcuts in the terminal or host UI; a non-interactive CLI cannot globally capture keyboard chords.",
        "manager_only_available": config["orchestration"]["manager_only_available"],
        "manager_deploy_policy": manager_deploy_policy(config),
        "max_subagents": max_subagents,
        "stale_after_minutes": stale_after_minutes,
        "close_stale_policy": config["orchestration"]["close_stale_policy"],
        "auto_deploy_when": config["orchestration"]["auto_deploy_when"],
        "manager_responsibilities": config["orchestration"]["manager_responsibilities"],
        "borrowed_patterns": config["orchestration"]["borrowed_patterns"],
        "reasoning_policy": reasoning_policy(config, local_status),
        "lane_template": [],
        "next_actions": ["Run scripts/qwendex manager estimate --prompt '...' --json"],
        "high_value_add": high_value_add_lines(local_status),
    }
    lane_specs = [
        ("implementation", "bounded patch", "medium", "owned by main or one worker"),
        ("review", "artifact summary", "low", "read-only"),
        ("docs/security", "security", "high", "public docs only"),
        ("verification", "smoke probe", "low", "receipts only"),
    ]
    for lane, task_class, risk, write_surface in lane_specs:
        data["lane_template"].append({
            "lane": lane,
            "owner": "main-or-worker",
            "write_surface": write_surface,
            "risk": risk,
            "stop_condition": "tests or blocker receipt",
            "artifacts": [],
            "integration_status": "pending",
            **lane_model_reasoning(config, task_class=task_class, lane=lane, risk=risk, local_status=local_status),
        })
    data.update(summary)
    data["deployment_contract"] = manager_deployment_contract(
        profile["mode"],
        data["manager_deploy_policy"],
        int(data["active_subagents"]["count"]),
    )
    data["manager_estimate"] = manager_self_estimate(
        config,
        mode=profile["mode"],
        local_status=local_status,
        stale_pressure="high" if data["stale_sessions"]["count"] else "none",
    )
    return data


def manager_status_surface_text(label: str, local_enabled: bool, kaveman_enabled: bool) -> str:
    return (
        f"{{Qwendex}} Agent Manager: [{label}] | Kaveman: [{'Y' if kaveman_enabled else 'N'}] "
        f"| Local: [{'Y' if local_enabled else 'N'}] (Alt+M/K/L)"
    )


def codex_status_payload(config: Mapping[str, Any], *, write_path: Path | None = None) -> dict[str, Any]:
    with connect_state(config) as conn:
        mode = current_manager_mode(config, conn)
        local_enabled = current_local_enabled(config, conn)
        kaveman_enabled = current_kaveman_enabled(config, conn)
        local_status = local_subagent_status(config, enabled=local_enabled, env=os.environ, probe=True)
        if local_enabled and not local_status.get("usable"):
            set_manager_setting(conn, "local_subagents_enabled", False)
            conn.commit()
            local_status = local_subagent_status(config, enabled=False, env=os.environ, probe=False)
    profile = manager_mode_profile(config, mode)
    text = manager_status_surface_text(
        profile["label"],
        bool(local_status.get("enabled")) and bool(local_status.get("usable")),
        kaveman_enabled,
    )
    data = {
        "text": text,
        "mode": profile["mode"],
        "label": profile["label"],
        "kaveman": "Y" if kaveman_enabled else "N",
        "kaveman_enabled": kaveman_enabled,
        "kaveman_directive": kaveman_directive(config) if kaveman_enabled else "",
        "local": "Y" if local_status.get("enabled") else "N",
        "local_enabled": bool(local_status.get("enabled")),
        "local_available": local_status.get("available"),
        "local_usable": bool(local_status.get("usable")),
        "state_db": str(state_db_path(config)),
        "status_file_env": QWENDEX_CODEX_STATUS_FILE_ENV,
    }
    if write_path is not None:
        target = write_path.expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        data["status_file"] = str(target)
    return data


def sync_codex_status_file_from_env(config: Mapping[str, Any]) -> str:
    raw = os.environ.get(QWENDEX_CODEX_STATUS_FILE_ENV, "").strip()
    if not raw:
        return ""
    return str(codex_status_payload(config, write_path=Path(raw)).get("status_file") or "")


def parse_codex_version_output(output: str) -> str:
    match = re.search(r"(\d+\.\d+\.\d+)", output)
    return match.group(1) if match else ""


def detect_codex_version(codex_bin: str) -> dict[str, Any]:
    path = shutil.which(codex_bin) or (codex_bin if Path(codex_bin).exists() else "")
    try:
        result = subprocess.run(
            [codex_bin, "--version"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "codex_bin": codex_bin,
            "path": path,
            "raw": "",
            "version": "",
            "returncode": 127,
            "error": redact_text(str(exc)),
        }
    raw = (result.stdout or result.stderr).strip()
    return {
        "codex_bin": codex_bin,
        "path": path,
        "raw": raw,
        "version": parse_codex_version_output(raw),
        "returncode": result.returncode,
        "error": redact_text(result.stderr.strip()) if result.returncode else "",
    }


def codex_source_patch_state(source: Path, manifest: Mapping[str, Any]) -> dict[str, Any]:
    root = source.expanduser().resolve()
    files: list[dict[str, Any]] = []
    missing_files: list[str] = []
    missing_anchors: list[str] = []
    marker_hits: list[str] = []
    for spec in manifest.get("source_anchors", []):
        rel = str(spec.get("path") or "")
        path = root / rel
        if not path.is_file():
            missing_files.append(rel)
            files.append({"path": rel, "exists": False, "anchors_ok": False, "patched": False})
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        anchors = [str(anchor) for anchor in spec.get("anchors", [])]
        absent = [anchor for anchor in anchors if anchor not in text]
        missing_anchors.extend(f"{rel}: {anchor}" for anchor in absent)
        patched = QWENDEX_CODEX_PATCH_MARKER in text or QWENDEX_CODEX_STATUS_ITEM_ID in text
        if patched:
            marker_hits.append(rel)
        files.append({
            "path": rel,
            "exists": True,
            "anchors_ok": not absent,
            "patched": patched,
            "missing_anchors": absent,
        })
    return {
        "root": str(root),
        "files": files,
        "missing_files": missing_files,
        "missing_anchors": missing_anchors,
        "patch_marker_hits": marker_hits,
        "anchors_ok": not missing_files and not missing_anchors,
        "applied": bool(marker_hits),
    }


def codex_source_patch_specs(version: str) -> list[dict[str, Any]]:
    marker = f"// {QWENDEX_CODEX_PATCH_MARKER}"
    if version != "0.142.4":
        return []
    return [
        {
            "path": "codex-rs/tui/src/bottom_pane/status_line_setup.rs",
            "replacements": [
                (
                    """    /// Latest checklist task progress from `update_plan` (if available).
    TaskProgress,
""",
                    f"""    /// Latest checklist task progress from `update_plan` (if available).
    TaskProgress,

    {marker}
    /// Qwendex manager, Kaveman, and local routing state.
    #[strum(to_string = "qwendex-manager")]
    QwendexManager,
""",
                ),
                (
                    """            StatusLineItem::TaskProgress => {
                "Latest task progress from update_plan (omitted until available)"
            }
""",
                    """            StatusLineItem::TaskProgress => {
                "Latest task progress from update_plan (omitted until available)"
            },
            StatusLineItem::QwendexManager => "Qwendex manager mode, Kaveman, and local routing state",
""",
                ),
                (
                    """            StatusLineItem::TaskProgress => StatusSurfacePreviewItem::TaskProgress,
""",
                    """            StatusLineItem::TaskProgress => StatusSurfacePreviewItem::TaskProgress,
            StatusLineItem::QwendexManager => StatusSurfacePreviewItem::QwendexManager,
""",
                ),
            ],
        },
        {
            "path": "codex-rs/tui/src/chatwidget/status_surfaces.rs",
            "replacements": [
                (
                    """impl ChatWidget {
""",
                    f"""{marker}
fn qwendex_status_line_text() -> Option<String> {{
    let status_file = std::env::var("QWENDEX_CODEX_STATUS_FILE").ok()?;
    let raw = std::fs::read_to_string(status_file).ok()?;
    let value = serde_json::from_str::<serde_json::Value>(&raw).ok()?;
    value
        .get("text")
        .and_then(|text| text.as_str())
        .map(str::trim)
        .filter(|text| !text.is_empty())
        .map(ToOwned::to_owned)
}}

impl ChatWidget {{
""",
                ),
                (
                    """            StatusLineItem::RawOutput => self.raw_output_mode().then(|| "raw output".to_string()),
""",
                    """            StatusLineItem::RawOutput => self.raw_output_mode().then(|| "raw output".to_string()),
            StatusLineItem::QwendexManager => qwendex_status_line_text(),
""",
                ),
                (
                    """            StatusSurfacePreviewItem::RawOutput => StatusLineItem::RawOutput,
""",
                    """            StatusSurfacePreviewItem::RawOutput => StatusLineItem::RawOutput,
            StatusSurfacePreviewItem::QwendexManager => StatusLineItem::QwendexManager,
""",
                ),
            ],
        },
        {
            "path": "codex-rs/tui/src/bottom_pane/status_line_style.rs",
            "replacements": [
                (
                    """            StatusLineItem::FastMode | StatusLineItem::RawOutput => Self::Mode,
""",
                    f"""            {marker}
            StatusLineItem::FastMode | StatusLineItem::RawOutput | StatusLineItem::QwendexManager => Self::Mode,
""",
                ),
            ],
        },
        {
            "path": "codex-rs/tui/src/bottom_pane/status_surface_preview.rs",
            "replacements": [
                (
                    """    TaskProgress,
}
""",
                    f"""    TaskProgress,
    {marker}
    QwendexManager,
}}
""",
                ),
                (
                    """            StatusSurfacePreviewItem::TaskProgress => "Tasks 0/0",
""",
                    """            StatusSurfacePreviewItem::TaskProgress => "Tasks 0/0",
            StatusSurfacePreviewItem::QwendexManager => "{Qwendex} Agent Manager: [Manager Mode] | Kaveman: [N] | Local: [Y] (Alt+M/K/L)",
""",
                ),
                (
                    """            Self::TaskProgress,
        ]
""",
                    """            Self::TaskProgress,
            Self::QwendexManager,
        ]
""",
                ),
            ],
        },
        {
            "path": "codex-rs/config/src/tui_keymap.rs",
            "replacements": [
                (
                    """    /// Toggle raw scrollback mode for copy-friendly transcript selection.
    pub toggle_raw_output: Option<KeybindingsSpec>,
}
""",
                    f"""    /// Toggle raw scrollback mode for copy-friendly transcript selection.
    pub toggle_raw_output: Option<KeybindingsSpec>,
    {marker}
    /// Toggle Qwendex Manager Mode.
    pub qwendex_toggle_manager: Option<KeybindingsSpec>,
    /// Toggle Qwendex Kaveman output mode.
    pub qwendex_toggle_kaveman: Option<KeybindingsSpec>,
    /// Toggle Qwendex local routing.
    pub qwendex_toggle_local: Option<KeybindingsSpec>,
}}
""",
                ),
            ],
        },
        {
            "path": "codex-rs/tui/src/keymap.rs",
            "replacements": [
                (
                    """    /// Toggle raw scrollback mode for copy-friendly transcript selection.
    pub(crate) toggle_raw_output: Vec<KeyBinding>,
}
""",
                    f"""    /// Toggle raw scrollback mode for copy-friendly transcript selection.
    pub(crate) toggle_raw_output: Vec<KeyBinding>,
    {marker}
    /// Toggle Qwendex Manager Mode.
    pub(crate) qwendex_toggle_manager: Vec<KeyBinding>,
    /// Toggle Qwendex Kaveman output mode.
    pub(crate) qwendex_toggle_kaveman: Vec<KeyBinding>,
    /// Toggle Qwendex local routing.
    pub(crate) qwendex_toggle_local: Vec<KeyBinding>,
}}
""",
                ),
                (
                    """            toggle_raw_output: resolve_bindings(
                keymap.global.toggle_raw_output.as_ref(),
                &defaults.app.toggle_raw_output,
                "tui.keymap.global.toggle_raw_output",
            )?,
""",
                    """            toggle_raw_output: resolve_bindings(
                keymap.global.toggle_raw_output.as_ref(),
                &defaults.app.toggle_raw_output,
                "tui.keymap.global.toggle_raw_output",
            )?,
            qwendex_toggle_manager: resolve_bindings(
                keymap.global.qwendex_toggle_manager.as_ref(),
                &defaults.app.qwendex_toggle_manager,
                "tui.keymap.global.qwendex_toggle_manager",
            )?,
            qwendex_toggle_kaveman: resolve_bindings(
                keymap.global.qwendex_toggle_kaveman.as_ref(),
                &defaults.app.qwendex_toggle_kaveman,
                "tui.keymap.global.qwendex_toggle_kaveman",
            )?,
            qwendex_toggle_local: resolve_bindings(
                keymap.global.qwendex_toggle_local.as_ref(),
                &defaults.app.qwendex_toggle_local,
                "tui.keymap.global.qwendex_toggle_local",
            )?,
""",
                ),
                (
                    """            (
                keymap.global.toggle_raw_output.as_ref(),
                app.toggle_raw_output.as_slice(),
            ),
""",
                    """            (
                keymap.global.toggle_raw_output.as_ref(),
                app.toggle_raw_output.as_slice(),
            ),
            (
                keymap.global.qwendex_toggle_manager.as_ref(),
                app.qwendex_toggle_manager.as_slice(),
            ),
            (
                keymap.global.qwendex_toggle_kaveman.as_ref(),
                app.qwendex_toggle_kaveman.as_slice(),
            ),
            (
                keymap.global.qwendex_toggle_local.as_ref(),
                app.qwendex_toggle_local.as_slice(),
            ),
""",
                ),
                (
                    """                toggle_raw_output: default_bindings![alt(KeyCode::Char('r'))],
            },
""",
                    """                toggle_raw_output: default_bindings![alt(KeyCode::Char('r'))],
                qwendex_toggle_manager: default_bindings![alt(KeyCode::Char('m'))],
                qwendex_toggle_kaveman: default_bindings![alt(KeyCode::Char('k'))],
                qwendex_toggle_local: default_bindings![alt(KeyCode::Char('l'))],
            },
""",
                ),
                (
                    """                ("toggle_fast_mode", self.app.toggle_fast_mode.as_slice()),
                ("toggle_raw_output", self.app.toggle_raw_output.as_slice()),
                ("chat.interrupt_turn", self.chat.interrupt_turn.as_slice()),
""",
                    """                ("toggle_fast_mode", self.app.toggle_fast_mode.as_slice()),
                ("toggle_raw_output", self.app.toggle_raw_output.as_slice()),
                ("qwendex_toggle_manager", self.app.qwendex_toggle_manager.as_slice()),
                ("qwendex_toggle_kaveman", self.app.qwendex_toggle_kaveman.as_slice()),
                ("qwendex_toggle_local", self.app.qwendex_toggle_local.as_slice()),
                ("chat.interrupt_turn", self.chat.interrupt_turn.as_slice()),
""",
                ),
                (
                    """                ("toggle_fast_mode", self.app.toggle_fast_mode.as_slice()),
                ("toggle_raw_output", self.app.toggle_raw_output.as_slice()),
            ],
            [
""",
                    """                ("toggle_fast_mode", self.app.toggle_fast_mode.as_slice()),
                ("toggle_raw_output", self.app.toggle_raw_output.as_slice()),
                ("qwendex_toggle_manager", self.app.qwendex_toggle_manager.as_slice()),
                ("qwendex_toggle_kaveman", self.app.qwendex_toggle_kaveman.as_slice()),
                ("qwendex_toggle_local", self.app.qwendex_toggle_local.as_slice()),
            ],
            [
""",
                ),
                (
                    """                ("toggle_vim_mode", self.app.toggle_vim_mode.as_slice()),
                ("toggle_fast_mode", self.app.toggle_fast_mode.as_slice()),
                ("toggle_raw_output", self.app.toggle_raw_output.as_slice()),
                (
                    "composer.history_search_previous",
""",
                    """                ("toggle_vim_mode", self.app.toggle_vim_mode.as_slice()),
                ("toggle_fast_mode", self.app.toggle_fast_mode.as_slice()),
                ("toggle_raw_output", self.app.toggle_raw_output.as_slice()),
                ("qwendex_toggle_manager", self.app.qwendex_toggle_manager.as_slice()),
                ("qwendex_toggle_kaveman", self.app.qwendex_toggle_kaveman.as_slice()),
                ("qwendex_toggle_local", self.app.qwendex_toggle_local.as_slice()),
                (
                    "composer.history_search_previous",
""",
                ),
            ],
        },
        {
            "path": "codex-rs/tui/src/app/input.rs",
            "replacements": [
                (
                    """    pub(super) async fn handle_key_event(
""",
                    f"""    {marker}
    fn run_qwendex_toggle_command(&mut self, tui: &mut tui::Tui, label: &str, args: &[&str]) {{
        let output = std::process::Command::new("qwendex").args(args).output();
        match output {{
            Ok(output) if output.status.success() => {{
                self.chat_widget.add_to_history(history_cell::new_info_event(
                    format!("Qwendex {{label}} toggled."),
                    None,
                ));
            }}
            Ok(output) => {{
                let stderr = String::from_utf8_lossy(&output.stderr);
                self.chat_widget.add_to_history(history_cell::new_error_event(format!(
                    "Qwendex {{label}} toggle failed: {{stderr}}"
                )));
            }}
            Err(err) => {{
                self.chat_widget.add_to_history(history_cell::new_error_event(format!(
                    "Qwendex {{label}} toggle failed: {{err}}"
                )));
            }}
        }}
        self.refresh_status_line();
        tui.frame_requester().schedule_frame();
    }}

    pub(super) async fn handle_key_event(
""",
                ),
                (
                    """        if app_keymap_shortcuts_available && self.keymap.app.toggle_raw_output.is_pressed(key_event)
        {
            let enabled = !self.chat_widget.raw_output_mode();
            self.apply_raw_output_mode(tui, enabled, /*notify*/ false);
            return;
        }
""",
                    """        if app_keymap_shortcuts_available
            && self.keymap.app.qwendex_toggle_manager.is_pressed(key_event)
        {
            self.run_qwendex_toggle_command(
                tui,
                "manager mode",
                &["manager", "mode", "--toggle", "--json"],
            );
            return;
        }

        if app_keymap_shortcuts_available
            && self.keymap.app.qwendex_toggle_kaveman.is_pressed(key_event)
        {
            self.run_qwendex_toggle_command(
                tui,
                "Kaveman mode",
                &["manager", "kaveman", "--toggle", "--json"],
            );
            return;
        }

        if app_keymap_shortcuts_available
            && self.keymap.app.qwendex_toggle_local.is_pressed(key_event)
        {
            self.run_qwendex_toggle_command(
                tui,
                "local routing",
                &["manager", "local", "--toggle", "--json"],
            );
            return;
        }

        if app_keymap_shortcuts_available && self.keymap.app.toggle_raw_output.is_pressed(key_event)
        {
            let enabled = !self.chat_widget.raw_output_mode();
            self.apply_raw_output_mode(tui, enabled, /*notify*/ false);
            return;
        }
""",
                ),
            ],
        },
    ]


def apply_codex_source_patch(source: Path, version: str, *, dry_run: bool = False) -> dict[str, Any]:
    root = source.expanduser().resolve()
    changes: list[dict[str, Any]] = []
    errors: list[str] = []
    specs = codex_source_patch_specs(version)
    if not specs:
        return {"changed": False, "changes": changes, "errors": [f"no source patch is available for Codex {version}"]}
    for spec in specs:
        rel = str(spec["path"])
        path = root / rel
        if not path.is_file():
            errors.append(f"missing file: {rel}")
            changes.append({"path": rel, "changed": False, "replacements": 0, "error": "missing file"})
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        updated = text
        replacements = 0
        missing: list[str] = []
        for old, new in spec["replacements"]:
            if new in updated:
                continue
            if old not in updated:
                missing.append(old.splitlines()[0] if old.splitlines() else old[:80])
                continue
            updated = updated.replace(old, new)
            replacements += 1
        if missing:
            errors.extend(f"{rel}: missing replacement anchor: {item}" for item in missing)
        changed = updated != text
        if changed and not dry_run:
            path.write_text(updated, encoding="utf-8")
        changes.append({
            "path": rel,
            "changed": changed,
            "replacements": replacements,
            "dry_run": dry_run,
            "missing": missing,
        })
    return {"changed": any(change["changed"] for change in changes), "changes": changes, "errors": errors}


def codex_patch_payload(args: argparse.Namespace) -> dict[str, Any]:
    version_info = detect_codex_version(args.codex_bin)
    version = version_info.get("version") or ""
    manifest = CODEX_PATCH_MANIFESTS.get(version)
    source_state = codex_source_patch_state(Path(args.source), manifest) if args.source and manifest else None
    supported = manifest is not None
    applied = bool(source_state and source_state.get("applied"))
    anchors_ok = bool(source_state.get("anchors_ok")) if source_state else None
    data = {
        "version": version_info,
        "supported": supported,
        "applied": applied,
        "source": source_state,
        "manifest": manifest or {},
        "known_versions": sorted(CODEX_PATCH_MANIFESTS),
        "runtime_contract": {
            "status_file_env": QWENDEX_CODEX_STATUS_FILE_ENV,
            "status_line_item": QWENDEX_CODEX_STATUS_ITEM_ID,
            "status_command": "qwendex codex-status --write \"$QWENDEX_CODEX_STATUS_FILE\" --json",
            "manager_toggle": "qwendex manager mode --toggle --json",
            "kaveman_toggle": "qwendex manager kaveman --toggle --json",
            "local_toggle": "qwendex manager local --toggle --json",
        },
    }
    if version_info.get("returncode") != 0 or not version:
        return stable_envelope(
            command="codex-patch",
            status="blocked",
            summary="Could not determine the installed Codex CLI version.",
            errors=[version_info.get("error") or version_info.get("raw") or "codex --version failed"],
            data=data,
        )
    if not supported:
        return stable_envelope(
            command="codex-patch",
            status="blocked",
            summary=f"Codex {version} is not in the Qwendex patch manifest.",
            errors=[f"unknown Codex version: {version}"],
            next_actions=["Run qwendex codex-patch locations --json and refresh the source anchors for this Codex version."],
            data=data,
        )
    if source_state and not anchors_ok:
        return stable_envelope(
            command="codex-patch",
            status="blocked",
            summary=f"Codex {version} is supported, but the supplied source checkout no longer matches the patch anchors.",
            errors=list(source_state.get("missing_files", [])) + list(source_state.get("missing_anchors", [])),
            next_actions=["Refresh the version manifest before applying the Qwendex TUI patch."],
            data=data,
        )
    if args.require_applied and not applied:
        return stable_envelope(
            command="codex-patch",
            status="blocked",
            summary=f"Codex {version} is supported, but the Qwendex TUI patch is not applied to the checked source.",
            errors=["patch marker not found"],
            next_actions=["Apply the Qwendex Codex TUI source patch, rebuild Codex, then rerun preflight with --source."],
            data=data,
        )
    summary = f"Codex {version} is supported by the Qwendex patch manifest."
    if source_state:
        summary += " Source patch marker is present." if applied else " Source anchors are ready; patch marker is not present yet."
    else:
        summary += " No source checkout was supplied, so installed-binary patch state was not asserted."
    return stable_envelope(
        command="codex-patch",
        status="pass",
        summary=summary,
        next_actions=[] if applied else ["Use a source checkout when you want preflight to assert the Qwendex TUI patch is applied."],
        data=data,
    )


def codex_patch_apply_payload(args: argparse.Namespace) -> dict[str, Any]:
    version_info = detect_codex_version(args.codex_bin)
    version = version_info.get("version") or ""
    manifest = CODEX_PATCH_MANIFESTS.get(version)
    source = Path(args.source).expanduser() if args.source else None
    source_state = codex_source_patch_state(source, manifest) if source and manifest else None
    data: dict[str, Any] = {
        "action": "apply",
        "dry_run": bool(args.dry_run),
        "version": version_info,
        "supported": manifest is not None,
        "source": source_state,
        "manifest": manifest or {},
        "known_versions": sorted(CODEX_PATCH_MANIFESTS),
        "runtime_contract": {
            "status_file_env": QWENDEX_CODEX_STATUS_FILE_ENV,
            "status_line_item": QWENDEX_CODEX_STATUS_ITEM_ID,
            "status_command": "qwendex codex-status --write \"$QWENDEX_CODEX_STATUS_FILE\" --json",
            "manager_toggle": "qwendex manager mode --toggle --json",
            "kaveman_toggle": "qwendex manager kaveman --toggle --json",
            "local_toggle": "qwendex manager local --toggle --json",
        },
    }
    if version_info.get("returncode") != 0 or not version:
        return stable_envelope(
            command="codex-patch",
            status="blocked",
            summary="Could not determine the installed Codex CLI version.",
            errors=[version_info.get("error") or version_info.get("raw") or "codex --version failed"],
            data=data,
        )
    if not manifest:
        return stable_envelope(
            command="codex-patch",
            status="blocked",
            summary=f"Codex {version} is not in the Qwendex patch manifest.",
            errors=[f"unknown Codex version: {version}"],
            next_actions=["Run qwendex codex-patch locations --json and refresh the source anchors for this Codex version."],
            data=data,
        )
    if not source:
        return stable_envelope(
            command="codex-patch",
            status="blocked",
            summary="A Codex source checkout is required before Qwendex can apply the TUI patch.",
            errors=["missing --source"],
            next_actions=[f"Check out Codex {manifest.get('codex_tag')} source, then rerun with --source /path/to/codex."],
            data=data,
        )
    if source_state and not source_state.get("anchors_ok"):
        return stable_envelope(
            command="codex-patch",
            status="blocked",
            summary=f"Codex {version} is supported, but the supplied source checkout no longer matches the patch anchors.",
            errors=list(source_state.get("missing_files", [])) + list(source_state.get("missing_anchors", [])),
            next_actions=["Refresh the version manifest before applying the Qwendex TUI patch."],
            data=data,
        )
    apply_result = apply_codex_source_patch(source, version, dry_run=bool(args.dry_run))
    after_state = codex_source_patch_state(source, manifest)
    data["apply"] = apply_result
    data["source_after"] = after_state
    if apply_result.get("errors"):
        return stable_envelope(
            command="codex-patch",
            status="blocked",
            summary="Qwendex could not apply the Codex TUI source patch cleanly.",
            errors=list(apply_result.get("errors", [])),
            next_actions=["Review the missing anchors, refresh the manifest if Codex changed, then rerun preflight."],
            data=data,
        )
    if args.dry_run:
        summary = f"Codex {version} source patch dry-run completed."
        summary += " Patch is already present." if source_state and source_state.get("applied") else " Patch can be applied."
        return stable_envelope(
            command="codex-patch",
            status="pass",
            summary=summary,
            next_actions=[] if source_state and source_state.get("applied") else ["Rerun without --dry-run to apply the source patch."],
            data=data,
        )
    if not after_state.get("applied"):
        return stable_envelope(
            command="codex-patch",
            status="blocked",
            summary="Qwendex applied edits, but post-apply preflight did not find the patch marker.",
            errors=["patch marker not found after apply"],
            next_actions=["Inspect the source checkout before rebuilding Codex."],
            data=data,
        )
    changed = bool(apply_result.get("changed"))
    return stable_envelope(
        command="codex-patch",
        status="pass",
        summary=f"Codex {version} source patch {'applied' if changed else 'is already applied'} and preflight passed.",
        next_actions=["Rebuild/install Codex from this source checkout, then run qwendex codex-patch preflight --source <checkout> --require-applied --json."],
        data=data,
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_utc(value: str) -> datetime:
    text = value.replace("Z", "+00:00")
    return datetime.fromisoformat(text)


def digest_json(data: dict[str, Any]) -> str:
    clean = {**data, "sha256": ""}
    return hashlib.sha256(json.dumps(clean, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def effective_policy(config: Mapping[str, Any], *, seat: str = "") -> dict[str, Any]:
    seat_config = config.get("seats", {}).get(seat, {}) if seat else {}
    return {
        "seat": seat,
        "guard": {
            "profile": config.get("guard", {}).get("profile"),
            "max_wall_time_seconds": config.get("guard", {}).get("max_wall_time_seconds"),
            "max_tool_calls": config.get("guard", {}).get("max_tool_calls"),
            "markers": list(config.get("guard", {}).get("markers", [])),
        },
        "sandbox": {
            "mode": config.get("sandbox", {}).get("mode"),
            "trusted_roots": list(config.get("sandbox", {}).get("trusted_roots", [])),
        },
        "context": {
            "compact_limit": seat_config.get("compact_limit", config.get("context", {}).get("compact_limit")),
            "max_output_tokens": config.get("context", {}).get("max_output_tokens"),
            "tool_output_token_limit": config.get("context", {}).get("tool_output_token_limit"),
            "context_window": seat_config.get("context_window"),
        },
        "routing": routing_policy(config),
    }


def write_receipt(config: Mapping[str, Any], prefix: str, payload: dict[str, Any]) -> Path:
    out_root = results_root(config)
    out_root.mkdir(parents=True, exist_ok=True)
    started_at = utc_now()
    run_id = f"{prefix}_{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')}"
    clean_payload = redact_obj(payload)
    receipt = {
        "schema_version": "qwendex.receipt.v1",
        "version": VERSION,
        "run_id": run_id,
        "started_at": started_at,
        "repo_root": str(ROOT),
        "task_id": "",
        "phase_id": "",
        "parent_run_id": "",
        "stop_reason": "",
        "evidence_refs": [],
        "limitations": [],
        "verification_refs": [],
        **clean_payload,
        "sha256": "",
    }
    if "effective_policy" not in receipt:
        receipt["effective_policy"] = effective_policy(config, seat=str(receipt.get("seat") or ""))
    receipt["sha256"] = digest_json(receipt)
    path = out_root / f"{run_id}.json"
    path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def verify_receipt_data(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {"verified": False, "errors": ["receipt must be a JSON object"]}
    errors: list[str] = []
    schema = data.get("schema_version")
    if schema not in {"qwendex.receipt.v1", "local_qwen_harness_eval.v1"}:
        errors.append(f"unsupported receipt schema_version: {schema}")
    if not isinstance(data.get("sha256"), str) or not data.get("sha256"):
        errors.append("receipt missing sha256")
    elif digest_json(data) != data.get("sha256"):
        errors.append("sha256 mismatch")
    if schema == "qwendex.receipt.v1":
        for field in ("run_id", "started_at", "repo_root"):
            if not data.get(field):
                errors.append(f"missing {field}")
    if schema == "local_qwen_harness_eval.v1":
        for field in ("case_id", "run_id", "success", "functional_status", "drift_status"):
            if field not in data:
                errors.append(f"missing {field}")
    return {
        "verified": not errors,
        "errors": errors,
        "schema_version": schema,
        "sha256": data.get("sha256", ""),
    }


def subprocess_failure_tail(result: subprocess.CompletedProcess[str] | subprocess.TimeoutExpired[str]) -> str:
    stdout = getattr(result, "stdout", "") or ""
    stderr = getattr(result, "stderr", "") or ""
    return redact_text((stderr or stdout)[-1000:])


def required_surface_check() -> dict[str, Any]:
    missing = [path for path in REQUIRED_SURFACE_FILES if not (ROOT / path).exists()]
    executable = os.access(ROOT / "scripts" / "qwendex", os.X_OK) if (ROOT / "scripts" / "qwendex").exists() else False
    return {
        "status": "pass" if not missing and executable else "fail",
        "missing": missing,
        "executable": executable,
        "required": list(REQUIRED_SURFACE_FILES),
    }


def public_docs_audit(doc_root: Path = PUBLIC_DOC_DIR) -> dict[str, Any]:
    missing = [name for name in PUBLIC_DOC_FILES if not (doc_root / name).exists()]
    files = [name for name in PUBLIC_DOC_FILES if (doc_root / name).exists()]
    dead_links: list[str] = []
    secret_hits: list[str] = []
    naming_hits: list[str] = []
    for name in files:
        path = doc_root / name
        text = path.read_text(encoding="utf-8")
        for line_no, line in enumerate(text.splitlines(), start=1):
            if SECRET_RE.search(line):
                secret_hits.append(f"{name}:{line_no}")
            for pattern, message in PUBLIC_NAMING_PATTERNS:
                if pattern.search(line):
                    naming_hits.append(f"{name}:{line_no}: {message}")
        for match in re.finditer(r"\[[^\]]+\]\(([^)]+)\)", text):
            target = match.group(1).strip()
            if not target or target.startswith(("#", "http://", "https://", "mailto:")):
                continue
            target_path = target.split("#", 1)[0]
            resolved = (path.parent / target_path).resolve()
            if not resolved.exists():
                dead_links.append(f"{name}: {target}")
    status = "pass" if not (missing or dead_links or secret_hits or naming_hits) else "fail"
    return {
        "status": status,
        "root": str(doc_root),
        "files": files,
        "missing": missing,
        "dead_links": dead_links,
        "secret_hits": secret_hits,
        "naming_hits": naming_hits,
    }


def json_file_status(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"path": rel(path), "exists": False, "valid": False, "error": "missing"}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"path": rel(path), "exists": True, "valid": False, "error": str(exc)}
    return {"path": rel(path), "exists": True, "valid": True, "data": data}


def gitignore_mentions(path: str) -> bool:
    ignore_file = ROOT / ".gitignore"
    if not ignore_file.exists():
        return False
    lines = [
        line.strip().lstrip("/")
        for line in ignore_file.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    return path in lines


def command_available(name: str) -> bool:
    result = subprocess.run(
        ["bash", "-lc", f"command -v {shlex_quote(name)} >/dev/null 2>&1"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=5,
    )
    return result.returncode == 0


def shlex_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def llmstack_private_hits() -> list[str]:
    hits: list[str] = []
    for item in LLMSTACK_PUBLIC_FILES:
        path = ROOT / item
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for line_no, line in enumerate(text.splitlines(), start=1):
            for pattern in LLMSTACK_PRIVATE_PATTERNS:
                if pattern.search(line):
                    hits.append(f"{item}:{line_no}: {pattern.pattern}")
    return hits


def llmstack_public_contract() -> dict[str, Any]:
    sample_status = json_file_status(LLMSTACK_SAMPLE_CONFIG)
    public_status = json_file_status(LLMSTACK_PUBLIC_CONFIG)
    profiles_status = json_file_status(LLMSTACK_CONFIG_DIR / "profiles.example.json")
    sample_data = sample_status.get("data") if sample_status.get("valid") else {}
    public_data = public_status.get("data") if public_status.get("valid") else {}
    services = sample_data.get("services", []) if isinstance(sample_data, Mapping) else []
    service_names = [
        str(item.get("name"))
        for item in services
        if isinstance(item, Mapping) and item.get("name")
    ]
    backend_profiles = sample_data.get("backend_profiles", []) if isinstance(sample_data, Mapping) else []
    aliases = [
        str(item.get("model_alias"))
        for item in backend_profiles
        if isinstance(item, Mapping) and item.get("model_alias")
    ]
    private_hits = llmstack_private_hits()
    missing_files = [item for item in LLMSTACK_PUBLIC_FILES if not (ROOT / item).exists()]
    config_errors: list[str] = []
    for status in (sample_status, public_status, profiles_status):
        if not status["valid"]:
            config_errors.append(f"{status['path']}: {status['error']}")
    if sample_status.get("valid") and public_status.get("valid") and public_data != sample_data:
        config_errors.append("config/local_llm_stack/stack_manager.json must match stack_manager.sample.json")
    required_services = {"textgen", "litellm", "bridge"}
    missing_services = sorted(required_services - set(service_names))
    if missing_services:
        config_errors.append("missing sample services: " + ", ".join(missing_services))
    status = "pass" if not (private_hits or missing_files or config_errors) else "fail"
    optional_programs = {
        "tmux": command_available("tmux"),
        "powershell.exe": command_available("powershell.exe"),
        "litellm": command_available("litellm"),
        "vllm": command_available("vllm"),
    }
    return {
        "status": status,
        "config": {
            "public_config": rel(LLMSTACK_PUBLIC_CONFIG),
            "sample_config": rel(LLMSTACK_SAMPLE_CONFIG),
            "local_config": rel(LLMSTACK_LOCAL_CONFIG),
            "local_config_present": LLMSTACK_LOCAL_CONFIG.exists(),
            "local_config_ignored": gitignore_mentions("config/local_llm_stack/stack_manager.local.json"),
            "override_env": ["QWENDEX_LLMSTACK_CONFIG", "LOCAL_LLM_STACK_CONFIG"],
        },
        "public_boundary": {
            "module": "qwendex.llmstack",
            "bundles_host_programs": False,
            "bundles_model_weights": False,
            "managed_scope": "configuration, launch wrappers, bridge/proxy, guard markers, receipts, and validation",
            "private_scope": "model weights, backend installs, credentials, logs, transcripts, and machine-local profiles",
        },
        "services_configured": service_names,
        "backend_endpoint": "http://127.0.0.1:5000/v1",
        "model_alias": aliases[0] if aliases else "qwen-local",
        "codex_bridge": "http://127.0.0.1:1234/v1",
        "guard_config": {
            "env_file": "config/local_llm_stack/local_harness.env",
            "sample_env": "config/local_llm_stack/local_harness.env.sample",
            "markers": list(DEFAULT_CONFIG["guard"]["markers"]),
        },
        "receipts_results": {
            "default_results_root": str(DEFAULT_RESULTS_ROOT.relative_to(ROOT)),
            "ledger": DEFAULT_CONFIG["receipts"]["ledger"],
        },
        "optional_host_programs": optional_programs,
        "missing_optional_host_programs": [name for name, available in optional_programs.items() if not available],
        "private_hits": private_hits,
        "missing_files": missing_files,
        "config_errors": config_errors,
    }


def command_check(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    surface = required_surface_check()
    artifacts = [path for path in REQUIRED_SURFACE_FILES if (ROOT / path).exists()]
    status = "pass" if surface["status"] == "pass" else "fail"
    with connect_state(config) as conn:
        mode = current_manager_mode(config, conn)
        local_status = local_subagent_status(config, enabled=current_local_enabled(config, conn), env=os.environ, probe=False)
    manager_estimate = manager_self_estimate(
        config,
        mode=mode,
        local_status=local_status,
        validation_confidence="medium" if status == "pass" else "low",
        release_risk="medium",
    )
    return stable_envelope(
        command="check",
        status=status,
        summary="Qwendex surface is ready." if status == "pass" else "Qwendex surface is incomplete.",
        artifacts=artifacts,
        next_actions=[] if status == "pass" else ["Run scripts/qwendex doctor --json"],
        errors=surface["missing"],
        data={
            "surface": surface,
            "default_seat": config["default_seat"],
            "routing": routing_policy(config),
            "manager_estimate": manager_estimate,
            "high_value_add": high_value_add_lines(local_status, release_risk="medium"),
        },
    )


def command_doctor(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    surface = required_surface_check()
    docs = public_docs_audit(PUBLIC_DOC_DIR)
    critical = list(surface["missing"])
    if not surface["executable"]:
        critical.append("scripts/qwendex is not executable")
    critical.extend(f"public/qwendex/{name} missing" for name in docs["missing"])
    critical.extend(docs["dead_links"])
    critical.extend(docs["secret_hits"])
    critical.extend(docs["naming_hits"])
    artifacts = [f"public/qwendex/{name}" for name in docs["files"]]
    artifacts.extend(path for path in REQUIRED_SURFACE_FILES if (ROOT / path).exists())
    status = "pass" if not critical else "fail"
    with connect_state(config) as conn:
        mode = current_manager_mode(config, conn)
        local_status = local_subagent_status(config, enabled=current_local_enabled(config, conn), env=os.environ, probe=False)
    manager_estimate = manager_self_estimate(
        config,
        mode=mode,
        local_status=local_status,
        validation_confidence="medium" if status == "pass" else "low",
        release_risk="medium" if status == "pass" else "high",
    )
    return stable_envelope(
        command="doctor",
        status=status,
        summary="Qwendex doctor found no critical issues." if status == "pass" else "Qwendex doctor found critical issues.",
        artifacts=artifacts,
        next_actions=["Run scripts/qwendex eval --json"] if status == "pass" else ["Repair listed critical issues."],
        errors=critical,
        data={
            "critical_issues": critical,
            "surface": surface,
            "public_docs": docs,
            "config": {
                "default_seat": config["default_seat"],
                "learning_mode": config["learning"]["mode"],
                "guard_profile": config["guard"]["profile"],
                "routing": routing_policy(config),
            },
            "manager_estimate": manager_estimate,
            "high_value_add": high_value_add_lines(local_status, release_risk=manager_estimate["release_risk"]),
        },
    )


def stack_command(command: str, service: str, dry_run: bool) -> dict[str, Any]:
    cmd = [str(ROOT / "scripts" / "llm"), command]
    if service:
        cmd.append(service)
    cmd.append("--json")
    if dry_run:
        return {"status": "ready", "command": cmd}
    try:
        result = subprocess.run(
            cmd,
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
            timeout=300,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "status": "fail",
            "command": cmd,
            "returncode": "timeout",
            "stdout": redact_text((exc.stdout or "")[-4000:]),
            "stderr": redact_text((exc.stderr or "")[-4000:]),
        }
    return {
        "status": "pass" if result.returncode == 0 else "fail",
        "command": cmd,
        "returncode": result.returncode,
        "stdout": redact_text(result.stdout[-4000:]),
        "stderr": redact_text(result.stderr[-4000:]),
    }


def command_stack(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    action_map = {"up": "start", "down": "stop", "restart": "restart"}
    data = stack_command(action_map[args.command], args.service, args.dry_run)
    status = "pass" if data["status"] in {"pass", "ready"} else "fail"
    return stable_envelope(
        command=args.command,
        status=status,
        summary=f"Qwendex {args.command} {'dry run is ready' if args.dry_run else data['status']}.",
        artifacts=[],
        next_actions=["Run scripts/qwendex check --json"],
        errors=[] if status == "pass" else [data.get("stderr") or data.get("stdout") or "stack command failed"],
        data=data,
    )


def command_llmstack(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    contract = llmstack_public_contract()
    if args.action in {"check", "doctor"}:
        status = contract["status"]
        summary = "Qwendex LLMStack public contract is ready." if status == "pass" else "Qwendex LLMStack public contract has issues."
        return stable_envelope(
            command="llmstack",
            status=status,
            summary=summary,
            artifacts=[item for item in LLMSTACK_PUBLIC_FILES if (ROOT / item).exists()],
            next_actions=["Run scripts/qwendex llmstack restart bridge --dry-run --json"] if status == "pass" else ["Repair listed LLMStack contract issues."],
            errors=contract["private_hits"] + contract["missing_files"] + contract["config_errors"],
            data={"action": args.action, "contract": contract},
        )
    action_map = {"up": "start", "down": "stop", "restart": "restart"}
    delegate = stack_command(action_map[args.action], args.service, args.dry_run)
    status = "pass" if delegate["status"] in {"pass", "ready"} and contract["status"] == "pass" else "fail"
    errors = []
    if contract["status"] != "pass":
        errors.extend(contract["private_hits"] + contract["missing_files"] + contract["config_errors"])
    if delegate["status"] not in {"pass", "ready"}:
        errors.append(delegate.get("stderr") or delegate.get("stdout") or "stack delegate failed")
    return stable_envelope(
        command="llmstack",
        status=status,
        summary=f"Qwendex LLMStack {args.action} {'dry run is ready' if args.dry_run else delegate['status']}.",
        artifacts=[],
        next_actions=["Run scripts/qwendex llmstack check --json"],
        errors=errors,
        data={"action": args.action, "service": args.service, "contract": contract, "delegate": delegate},
    )


def is_exact_qwendex_ok(prompt: str) -> bool:
    normalized = " ".join(prompt.strip().split()).lower()
    return normalized in {
        "reply exactly qwendex_ok",
        "reply exactly: qwendex_ok",
        "reply with exactly qwendex_ok",
        "reply with exactly: qwendex_ok",
    }


def qwendex_exec_cwd(raw: str = "") -> Path:
    value = (raw or os.environ.get("QWENDEX_EXEC_CWD", "")).strip()
    if not value:
        return ROOT
    return Path(value).expanduser().resolve()


def codex_mcp_override_args(exec_cwd: Path) -> list[str]:
    trusted_roots = os.environ.get("QWENDEX_MCP_TRUSTED_ROOTS", "").strip()
    if not trusted_roots:
        trusted_roots = f"{ROOT}:{exec_cwd}"
    local_harness = ROOT / "scripts" / "artifact_queue_mcp.py"
    return [
        "-c",
        'mcp_servers.local-harness.command="python3"',
        "-c",
        f"mcp_servers.local-harness.args=[{json.dumps(str(local_harness))}]",
        "-c",
        f"mcp_servers.local-harness.cwd={json.dumps(str(ROOT))}",
        "-c",
        "mcp_servers.local-harness.env.ARTIFACT_QUEUE_MCP_TRUSTED_ROOTS="
        + json.dumps(trusted_roots),
        "-c",
        'mcp_servers.local-harness.env.SEARXNG_URL="http://127.0.0.1:6060"',
    ]


def exec_command_for_seat(seat: str, seat_config: Mapping[str, Any], prompt: str, *, cwd: Path | None = None) -> list[str]:
    exec_cwd = cwd or qwendex_exec_cwd()
    if seat in {"qwen", "sandbox"}:
        return [
            str(ROOT / "scripts" / "run_local_qwen_codex.sh"),
            "--cwd",
            str(exec_cwd),
            "--minimal",
            "--ephemeral",
            "--exec",
            prompt,
        ]
    return [
        "codex",
        "exec",
        "--sandbox",
        "workspace-write",
        *codex_mcp_override_args(exec_cwd),
        "-m",
        str(seat_config.get("model", "gpt-5.5")),
        "-C",
        str(exec_cwd),
        prompt,
    ]


def command_exec(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    prompt = " ".join(args.prompt).strip()
    exec_cwd = qwendex_exec_cwd(args.cwd)
    with connect_state(config) as conn:
        local_enabled = current_local_enabled(config, conn)
    route = resolve_route(
        config,
        requested_seat=args.seat or "auto",
        task_class="exec",
        env=os.environ,
        prefer_local=args.prefer_local,
        local_enabled=local_enabled,
    )
    seat = route["seat"]
    seat_config = config["seats"].get(seat, config["seats"]["qwen"])
    if is_exact_qwendex_ok(prompt):
        review_status = "synthetic_exact_marker" if seat == "qwen" else "seat_exact_marker"
        path = write_receipt(
            config,
            "exec",
            {
                "seat": seat,
                "model": seat_config.get("model", ""),
                "profile": seat,
                "task_class": "exec",
                "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                "tool_calls": [],
                "files_touched": [],
                "markers": [],
                "eval_result": "pass",
                "review_status": review_status,
                "routing": route,
                "output": "QWENDEX_OK",
            },
        )
        return stable_envelope(
            command="exec",
            status="pass",
            summary="QWENDEX_OK",
            artifacts=[str(path)],
            next_actions=["Run scripts/qwendex receipt latest --json"],
            data={"seat": seat, "model": seat_config.get("model", ""), "output": "QWENDEX_OK", "routing": route},
        )
    cmd = exec_command_for_seat(seat, seat_config, prompt, cwd=exec_cwd)
    if args.dry_run:
        data = {"status": "ready", "command": cmd, "seat": seat, "model": seat_config.get("model"), "routing": route}
        return stable_envelope(
            command="exec",
            status="pass",
            summary="Qwendex exec dry run is ready.",
            data=data,
            next_actions=["Start the stack with scripts/qwendex up before live exec."],
        )
    try:
        result = subprocess.run(
            cmd,
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
            timeout=args.timeout,
        )
    except subprocess.TimeoutExpired as exc:
        path = write_receipt(
            config,
            "exec",
            {
                "seat": seat,
                "model": seat_config.get("model", ""),
                "profile": seat,
                "task_class": "exec",
                "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                "tool_calls": [],
                "files_touched": [],
                "markers": ["QWENDEX_TIMEOUT"],
                "eval_result": "fail",
                "review_status": "timeout",
                "routing": route,
                "returncode": "timeout",
                "stdout_tail": (exc.stdout or "")[-2000:],
                "stderr_tail": (exc.stderr or "")[-2000:],
            },
        )
        return stable_envelope(
            command="exec",
            status="fail",
            summary="Qwendex exec timed out.",
            artifacts=[str(path)],
            next_actions=["Retry with a smaller prompt or a larger --timeout."],
            errors=[subprocess_failure_tail(exc) or "timeout"],
            data={"seat": seat, "model": seat_config.get("model"), "markers": ["QWENDEX_TIMEOUT"], "routing": route},
        )
    status = "pass" if result.returncode == 0 else "fail"
    markers = [marker for marker in config["guard"]["markers"] if marker in (result.stdout + result.stderr)]
    path = write_receipt(
        config,
        "exec",
        {
            "seat": seat,
            "model": seat_config.get("model", ""),
            "profile": seat,
            "task_class": "exec",
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "tool_calls": [],
            "files_touched": [],
            "markers": markers,
            "eval_result": status,
            "review_status": "requires_gpt_review" if seat == "qwen" else "primary_review",
            "routing": route,
            "returncode": result.returncode,
            "stdout_tail": result.stdout[-2000:],
            "stderr_tail": result.stderr[-2000:],
        },
    )
    return stable_envelope(
        command="exec",
        status=status,
        summary="Qwendex exec completed." if status == "pass" else "Qwendex exec failed.",
        artifacts=[str(path)],
        next_actions=["Review the receipt before accepting Qwen output."],
        errors=[] if status == "pass" else [subprocess_failure_tail(result)],
        data={"seat": seat, "model": seat_config.get("model"), "markers": markers, "routing": route},
    )


def command_route(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    with connect_state(config) as conn:
        local_enabled = current_local_enabled(config, conn)
    route = resolve_route(
        config,
        requested_seat=args.seat,
        task_class=args.task_class,
        env=os.environ,
        prefer_local=args.prefer_local,
        local_enabled=local_enabled,
    )
    next_actions = ["Review Qwen receipts with a GPT/Codex authority seat before release acceptance."] if route["seat"] == "qwen" else [
        "Start the local stack with scripts/qwendex up if you want auto routing to prefer Qwen."
    ]
    return stable_envelope(
        command="route",
        status="pass",
        summary=f"Qwendex routes {route['task_class']} to {route['seat']}.",
        next_actions=next_actions,
        data=route,
    )


def live_preflight() -> dict[str, Any]:
    checks = [
        ["scripts/run_local_qwen_codex.sh", "--check"],
        ["python3", "scripts/validate_local_qwen_reliability.py", "--require-live-bridge"],
    ]
    results: list[dict[str, Any]] = []
    failures: list[str] = []
    for cmd in checks:
        try:
            result = subprocess.run(
                cmd,
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
                timeout=300,
            )
            item = {
                "command": cmd,
                "returncode": result.returncode,
                "stdout_tail": redact_text(result.stdout[-2000:]),
                "stderr_tail": redact_text(result.stderr[-2000:]),
            }
            if result.returncode != 0:
                failures.append(subprocess_failure_tail(result) or "live preflight failed")
        except subprocess.TimeoutExpired as exc:
            item = {
                "command": cmd,
                "returncode": "timeout",
                "stdout_tail": redact_text((exc.stdout or "")[-2000:]),
                "stderr_tail": redact_text((exc.stderr or "")[-2000:]),
            }
            failures.append(subprocess_failure_tail(exc) or "live preflight timed out")
        results.append(item)
    return {"status": "pass" if not failures else "fail", "checks": results, "failures": failures}


def command_eval(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    if args.live:
        preflight = live_preflight()
        if preflight["status"] != "pass":
            return stable_envelope(
                command="eval",
                status="fail",
                summary="Qwendex live eval preflight failed.",
                errors=preflight["failures"],
                data={"live_preflight": preflight},
            )
    module = script_module("local_qwen_harness_eval")
    configured_case = args.case or config["eval"]["default_case"]
    run_all = args.all or configured_case in {"", "all"}
    case_id = "" if run_all else configured_case
    result = module.run_harness_eval(
        repo_root=ROOT,
        results_root=args.results_root or module.DEFAULT_RESULTS_ROOT,
        ledger_db_path=configured_ledger_path(config),
        case_id=case_id,
        run_all=run_all,
        live=args.live,
    )
    case_ids = list(result.get("case_ids", []))
    failures = list(result.get("failures", []))
    result["metrics"] = {
        "total_cases": len(case_ids),
        "failed_cases": len(failures),
        "passed_cases": max(0, len(case_ids) - len(failures)),
        "receipt_count": len(result.get("receipts", [])),
        "default_mode": "all" if run_all else "single_case",
    }
    status = "pass" if result.get("success") else "fail"
    with connect_state(config) as conn:
        mode = current_manager_mode(config, conn)
        local_status = local_subagent_status(config, enabled=current_local_enabled(config, conn), env=os.environ, probe=False)
    result["manager_estimate"] = manager_self_estimate(
        config,
        mode=mode,
        local_status=local_status,
        validation_confidence="high" if status == "pass" else "low",
        release_risk="low" if status == "pass" else "high",
    )
    result["high_value_add"] = high_value_add_lines(local_status, release_risk=result["manager_estimate"]["release_risk"])
    return stable_envelope(
        command="eval",
        status=status,
        summary="Qwendex eval passed." if status == "pass" else "Qwendex eval failed.",
        artifacts=list(result.get("receipts", [])),
        next_actions=["Run scripts/qwendex receipt latest --json"],
        errors=list(result.get("failures", [])),
        data=result,
    )


def receipt_candidates(config: Mapping[str, Any]) -> list[Path]:
    candidates: list[Path] = []
    for root in (results_root(config), ROOT / "results" / "local_qwen_harness_hardening"):
        if root.exists():
            candidates.extend(path for path in root.rglob("*.json") if path.is_file())
    return sorted(candidates, key=lambda path: path.stat().st_mtime, reverse=True)


def trusted_receipt_roots(config: Mapping[str, Any]) -> list[Path]:
    return [
        results_root(config).resolve(strict=False),
        (ROOT / "results" / "local_qwen_harness_hardening").resolve(strict=False),
    ]


def is_under_root(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve(strict=False))
        return True
    except (OSError, ValueError):
        return False


def is_trusted_receipt_path(path: Path, config: Mapping[str, Any]) -> bool:
    return any(is_under_root(path, root) for root in trusted_receipt_roots(config))


def command_receipt(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    target = args.target
    if target == "latest":
        candidates = receipt_candidates(config)
        if not candidates:
            return stable_envelope(
                command="receipt",
                status="blocked",
                summary="No Qwendex receipts are available yet.",
                next_actions=["Run scripts/qwendex exec 'Reply exactly QWENDEX_OK' --json"],
            )
        path = candidates[0]
    else:
        path = Path(target).expanduser()
        if not path.is_absolute():
            path = ROOT / path
    if not is_trusted_receipt_path(path, config):
        return stable_envelope(
            command="receipt",
            status="blocked",
            summary="Receipt lookup is limited to trusted receipt roots.",
            errors=[str(path)],
            data={"trusted_roots": [str(root) for root in trusted_receipt_roots(config)]},
        )
    if not path.exists():
        return stable_envelope(
            command="receipt",
            status="blocked",
            summary=f"Receipt not found: {target}",
            errors=[target],
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return stable_envelope(
            command="receipt",
            status="blocked",
            summary=f"Receipt verification failed for {rel(path)}.",
            artifacts=[str(path)],
            errors=[f"invalid JSON: {exc}"],
        )
    verification = verify_receipt_data(data)
    if not verification["verified"]:
        return stable_envelope(
            command="receipt",
            status="blocked",
            summary=f"Receipt verification failed for {rel(path)}.",
            artifacts=[str(path)],
            errors=list(verification["errors"]),
            data={"verification": verification},
        )
    return stable_envelope(
        command="receipt",
        status="pass",
        summary=f"Loaded verified receipt {rel(path)}.",
        artifacts=[str(path)],
        data={"receipt": data, "verification": verification},
    )


def command_seat(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    if not args.seat:
        return stable_envelope(
            command="seat",
            status="pass",
            summary=f"Current default seat is {config['default_seat']}.",
            data={"default_seat": config["default_seat"], "seats": config["seats"]},
        )
    if args.seat not in config["seats"]:
        return stable_envelope(
            command="seat",
            status="blocked",
            summary=f"Unknown Qwendex seat: {args.seat}",
            errors=[args.seat],
            data={"available": sorted(config["seats"])},
        )
    seat_config = config["seats"][args.seat]
    review_status = "requires_gpt_review" if args.seat == "qwen" else "seat_selected"
    path = write_receipt(
        config,
        "seat",
        {
            "seat": args.seat,
            "model": seat_config.get("model", ""),
            "profile": args.seat,
            "task_class": "seat_probe",
            "tool_calls": [],
            "files_touched": [],
            "markers": [],
            "eval_result": "pass",
            "review_status": review_status,
            "authority": seat_config.get("authority", ""),
        },
    )
    return stable_envelope(
        command="seat",
        status="pass",
        summary=f"Qwendex seat {args.seat} is available.",
        artifacts=[str(path)],
        next_actions=["Run scripts/qwendex eval --json"],
        data={"seat": args.seat, "profile": seat_config, "review_status": review_status},
    )


def repo_relative_candidate(path: Path) -> str | None:
    candidate = path.expanduser()
    if not candidate.is_absolute():
        candidate = ROOT / candidate
    try:
        resolved_root = ROOT.resolve()
        resolved = candidate.resolve(strict=False)
        return resolved.relative_to(resolved_root).as_posix()
    except (OSError, ValueError):
        return None


def is_auto_adopt_allowed(path: Path) -> bool:
    text = repo_relative_candidate(path)
    if text is None:
        return False
    denied_exact = {
        ".codex/config.toml",
        ".codex/hooks.json",
        "hooks/hooks.json",
        "config/local_llm_stack/local_harness.env",
    }
    denied_prefixes = (
        "state/",
        "hooks/",
        "scripts/local_qwen_bridge/",
        "public/qwendex/",
    )
    denied_suffixes = (".env", ".key", ".pem", ".p12", ".pfx")
    if text in denied_exact:
        return False
    if any(text.startswith(prefix) for prefix in denied_prefixes):
        return False
    if any(text.endswith(suffix) for suffix in denied_suffixes):
        return False
    allowed_prefixes = (
        ".codex/skills/",
        "tests/smoke/",
        "docs/generated/local_llm_stack/",
    )
    return any(text.startswith(prefix) for prefix in allowed_prefixes)


def proposal_report(path: Path) -> dict[str, Any]:
    if not path:
        return {"paths": [], "errors": ["proposal path is required"]}
    if not path.exists():
        return {"paths": [], "errors": [f"proposal not found: {path}"]}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        return {"paths": [], "errors": [f"proposal unreadable: {exc}"]}
    except json.JSONDecodeError as exc:
        return {"paths": [], "errors": [f"proposal invalid JSON: {exc}"]}
    if not isinstance(data, dict):
        return {"paths": [], "errors": ["proposal must be a JSON object"]}
    raw_paths = data.get("changed_files") or data.get("paths") or data.get("files")
    if raw_paths is None:
        return {"paths": [], "errors": ["proposal is missing changed_files, paths, or files"]}
    if isinstance(raw_paths, str):
        raw_paths = [raw_paths]
    if not isinstance(raw_paths, list):
        return {"paths": [], "errors": ["proposal path metadata must be a string or list"]}
    errors = [
        f"proposal path metadata item {index} must be a non-empty string"
        for index, item in enumerate(raw_paths)
        if not isinstance(item, str) or not item.strip()
    ]
    if errors:
        return {"paths": [], "errors": errors}
    paths = [Path(item) for item in raw_paths]
    if not paths:
        return {"paths": [], "errors": ["proposal path metadata is empty"]}
    return {"paths": paths, "errors": []}


def proposal_paths(path: Path) -> list[Path]:
    return list(proposal_report(path)["paths"])


def command_learn(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    if args.action in {"stage", "audit", "proposal-summary"}:
        module = script_module("local_qwen_skillopt_wrapper")
        data = module.proposal_summary(ROOT)
        if args.action == "audit":
            data["auto_adopt_denied_prefixes"] = [
                "hooks/",
                ".codex/config.toml",
                "config/local_llm_stack/local_harness.env",
                "scripts/local_qwen_bridge/",
                "public/qwendex/",
                "state/",
            ]
        return stable_envelope(
            command="learn",
            status="pass",
            summary="Qwendex learning proposals are staged for review.",
            artifacts=[item["path"] for item in data.get("proposals", [])],
            next_actions=["Review staged proposals before adoption."],
            data=data,
        )
    if args.action == "adopt":
        proposal = Path(args.proposal).expanduser() if args.proposal else Path()
        report = proposal_report(proposal)
        paths = list(report["paths"])
        unsafe = [
            repo_relative_candidate(path) or path.as_posix()
            for path in paths
            if not is_auto_adopt_allowed(path)
        ]
        if not args.approve:
            return stable_envelope(
                command="learn",
                status="blocked",
                summary="Learning adoption requires explicit approval and cannot auto-adopt by default.",
                errors=["explicit approval required"],
                data={"proposal": str(proposal), "unsafe_paths": unsafe, "proposal_errors": report["errors"]},
            )
        if report["errors"]:
            return stable_envelope(
                command="learn",
                status="blocked",
                summary="Learning adoption requires a valid proposal with path metadata.",
                errors=list(report["errors"]),
                data={"proposal": str(proposal)},
            )
        if unsafe:
            return stable_envelope(
                command="learn",
                status="blocked",
                summary="Learning adoption is blocked by denied paths.",
                errors=unsafe,
                data={"proposal": str(proposal), "unsafe_paths": unsafe},
            )
        return stable_envelope(
            command="learn",
            status="pass",
            summary="Learning proposal is allowlisted for manual adoption.",
            artifacts=[str(proposal)] if proposal else [],
            data={"proposal": str(proposal), "paths": [repo_relative_candidate(path) or path.as_posix() for path in paths]},
        )
    if args.action == "rollback":
        return stable_envelope(
            command="learn",
            status="blocked",
            summary="Rollback is intentionally manual until a reviewed adoption receipt is supplied.",
            errors=["reviewed adoption receipt required"],
        )
    module = script_module("local_qwen_skillopt_wrapper")
    data = module.run_skillopt_action(
        args.action,
        project=ROOT,
        backend=args.backend,
        source=args.source,
        json_output=args.json,
        allow_codex_budget=args.allow_codex_budget,
        execute=not args.no_execute,
    )
    status = "pass" if data.get("status") in {"pass", "ready"} else data.get("status", "fail")
    artifacts = [item["path"] for item in data.get("proposal_summary", {}).get("proposals", [])]
    return stable_envelope(
        command="learn",
        status=status,
        summary=f"Qwendex learn {args.action} returned {data.get('status')}.",
        artifacts=artifacts,
        next_actions=["Review staged proposals before adoption."],
        errors=[] if status in {"pass", "ready"} else [data.get("message", "SkillOpt action failed")],
        data=data,
    )


def fetch_task(conn: sqlite3.Connection, task_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM qwendex_tasks WHERE task_id = ?", (task_id,)).fetchone()
    return row_to_task(row)


def command_task(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    now = utc_now()
    with connect_state(config) as conn:
        if args.action == "create":
            task_id = args.task_id or make_id("task")
            conn.execute(
                """
                INSERT INTO qwendex_tasks
                (task_id, title, priority, owner, phase, status, summary, blocked_reason, created_at, updated_at, started_at, finished_at)
                VALUES (?, ?, ?, ?, ?, 'open', ?, '', ?, ?, '', '')
                """,
                (task_id, args.title, args.priority, args.owner, args.phase, args.summary, now, now),
            )
            conn.commit()
            task = fetch_task(conn, task_id)
            return stable_envelope(
                command="task",
                status="pass",
                summary=f"Created Qwendex task {task_id}.",
                data={"task": task, "state_db": str(state_db_path(config))},
            )
        if args.action == "status":
            if args.task_id:
                task = fetch_task(conn, args.task_id)
                if task is None:
                    return stable_envelope(command="task", status="blocked", summary=f"Task not found: {args.task_id}", errors=[args.task_id])
                return stable_envelope(command="task", status="pass", summary=f"Loaded task {args.task_id}.", data={"task": task})
            rows = conn.execute("SELECT * FROM qwendex_tasks ORDER BY updated_at DESC LIMIT ?", (args.limit,)).fetchall()
            return stable_envelope(
                command="task",
                status="pass",
                summary=f"Loaded {len(rows)} Qwendex tasks.",
                data={"tasks": [row_to_task(row) for row in rows]},
            )
        task = fetch_task(conn, args.task_id)
        if task is None:
            return stable_envelope(command="task", status="blocked", summary=f"Task not found: {args.task_id}", errors=[args.task_id])
        updates: dict[str, Any] = {"updated_at": now}
        if args.action == "start":
            updates.update({"status": "in_progress", "started_at": task.get("started_at") or now})
        elif args.action == "finish":
            updates.update({"status": "done", "finished_at": now})
        elif args.action == "block":
            updates.update({"status": "blocked", "blocked_reason": args.reason})
        elif args.action == "update":
            if args.title:
                updates["title"] = args.title
            if args.priority:
                updates["priority"] = args.priority
            if args.owner:
                updates["owner"] = args.owner
            if args.phase:
                updates["phase"] = args.phase
            if args.status:
                updates["status"] = args.status
            if args.summary:
                updates["summary"] = args.summary
        assignments = ", ".join(f"{key} = ?" for key in updates)
        conn.execute(f"UPDATE qwendex_tasks SET {assignments} WHERE task_id = ?", (*updates.values(), args.task_id))
        conn.commit()
        task = fetch_task(conn, args.task_id)
        return stable_envelope(command="task", status="pass", summary=f"Updated Qwendex task {args.task_id}.", data={"task": task})


def mode_stale_after_minutes(config: Mapping[str, Any], mode: str, override: int = 0) -> int:
    if override:
        return override
    thresholds = config.get("orchestration", {}).get("stale_session_thresholds_minutes", {})
    if isinstance(thresholds, Mapping):
        value = thresholds.get(normalize_manager_mode(mode))
        if isinstance(value, int):
            return value
    return int(config.get("orchestration", {}).get("stale_after_minutes", 30))


def manager_estimate_envelope(
    config: Mapping[str, Any],
    *,
    command_name: str,
    prompt: str,
    mode: str,
    local_status: Mapping[str, Any],
) -> dict[str, Any]:
    estimate = estimate_task(config, prompt=prompt.strip(), local_status=local_status)
    profile = manager_mode_profile(config, mode)
    data = {
        "mode": profile["mode"],
        "label": profile["label"],
        "ui_indicator": manager_ui_indicator(config, profile["mode"]),
        "local_indicator": local_status["indicator"],
        "local_subagents": local_status,
        "estimator": estimator_config(config),
        "estimate": estimate,
        "reasoning_policy": reasoning_policy(config, local_status),
        "high_value_add": high_value_add_lines(local_status, release_risk=estimate["risk"]),
    }
    return stable_envelope(
        command=command_name,
        status="pass",
        summary=f"Qwendex Auto estimates {estimate['recommended_mode']} for this task.",
        next_actions=["Use the recommended mode only after reviewing the task scope."],
        data=data,
    )


def command_estimate(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    with connect_state(config) as conn:
        mode = current_manager_mode(config, conn)
        local_status = local_subagent_status(config, enabled=current_local_enabled(config, conn), env=os.environ, probe=True)
    return manager_estimate_envelope(
        config,
        command_name="estimate",
        prompt=args.prompt,
        mode=mode,
        local_status=local_status,
    )


def command_manager_state(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any] | None:
    if not args.action:
        return None
    now = utc_now()
    with connect_state(config) as conn:
        mode = current_manager_mode(config, conn)
        stale_after = mode_stale_after_minutes(config, mode, args.stale_after_minutes)
        max_subagents = args.max_subagents or manager_mode_profile(config, mode)["max_subagents"]
        local_status = local_subagent_status(config, enabled=current_local_enabled(config, conn), env=os.environ, probe=True)
        kaveman_enabled = current_kaveman_enabled(config, conn)
        if args.action == "mode":
            if args.toggle:
                mode = "auto" if mode == "manager" else "manager"
                set_manager_setting(conn, "selected_mode", mode)
                conn.commit()
            elif args.cycle:
                index = MANAGER_MODE_ORDER.index(mode) if mode in MANAGER_MODE_ORDER else 0
                mode = MANAGER_MODE_ORDER[(index + 1) % len(MANAGER_MODE_ORDER)]
                set_manager_setting(conn, "selected_mode", mode)
                conn.commit()
            elif args.set:
                requested = normalize_manager_mode(args.set)
                if requested not in MANAGER_MODE_ORDER:
                    return stable_envelope(command="manager", status="blocked", summary=f"Unknown manager mode: {args.set}", errors=[args.set])
                mode = requested
                set_manager_setting(conn, "selected_mode", mode)
                conn.commit()
            rows = conn.execute("SELECT * FROM qwendex_agent_sessions ORDER BY updated_at DESC LIMIT ?", (args.limit,)).fetchall()
            sessions = [row_to_agent_session(row) for row in rows]
            data = manager_mode_payload(
                config,
                mode=mode,
                local_status=local_status,
                max_subagents=args.max_subagents or manager_mode_profile(config, mode)["max_subagents"],
                stale_after_minutes=mode_stale_after_minutes(config, mode, args.stale_after_minutes),
                kaveman_enabled=kaveman_enabled,
                sessions=[session for session in sessions if session],
            )
            data["state_db"] = str(state_db_path(config))
            data["codex_status_file"] = sync_codex_status_file_from_env(config)
            status = data["deployment_contract"]["status"]
            return stable_envelope(
                command="manager",
                status=status,
                summary=f"Qwendex manager mode is {data['label']}.",
                next_actions=(
                    ["Spawn/register at least one manager lane or set orchestration.manager_deploy_policy to disabled."]
                    if status == "blocked"
                    else data["next_actions"]
                ),
                data=data,
            )
        if args.action == "kaveman":
            enabled = kaveman_enabled
            if args.toggle:
                enabled = not enabled
            elif args.set:
                parsed = normalize_local_toggle(args.set)
                if parsed is None:
                    return stable_envelope(command="manager", status="blocked", summary=f"Unknown Kaveman toggle: {args.set}", errors=[args.set])
                enabled = parsed
            set_manager_setting(conn, "kaveman_enabled", enabled)
            conn.commit()
            kaveman_enabled = enabled
            rows = conn.execute("SELECT * FROM qwendex_agent_sessions ORDER BY updated_at DESC LIMIT ?", (args.limit,)).fetchall()
            sessions = [row_to_agent_session(row) for row in rows]
            data = manager_mode_payload(
                config,
                mode=mode,
                local_status=local_status,
                max_subagents=max_subagents,
                stale_after_minutes=stale_after,
                kaveman_enabled=kaveman_enabled,
                sessions=[session for session in sessions if session],
            )
            data["state_db"] = str(state_db_path(config))
            data["codex_status_file"] = sync_codex_status_file_from_env(config)
            return stable_envelope(
                command="manager",
                status="pass",
                summary=f"Qwendex Kaveman mode is {'enabled' if enabled else 'disabled'}.",
                next_actions=data["next_actions"],
                data=data,
            )
        if args.action == "local":
            enabled = current_local_enabled(config, conn)
            if args.toggle:
                enabled = not enabled
            elif args.set:
                parsed = normalize_local_toggle(args.set)
                if parsed is None:
                    return stable_envelope(command="manager", status="blocked", summary=f"Unknown local toggle: {args.set}", errors=[args.set])
                enabled = parsed
            set_manager_setting(conn, "local_subagents_enabled", enabled)
            conn.commit()
            local_status = local_subagent_status(config, enabled=enabled, env=os.environ, probe=True)
            if enabled and not local_status.get("usable"):
                enabled = False
                set_manager_setting(conn, "local_subagents_enabled", False)
                conn.commit()
                local_status = local_subagent_status(config, enabled=False, env=os.environ, probe=False)
            rows = conn.execute("SELECT * FROM qwendex_agent_sessions ORDER BY updated_at DESC LIMIT ?", (args.limit,)).fetchall()
            sessions = [row_to_agent_session(row) for row in rows]
            data = manager_mode_payload(
                config,
                mode=mode,
                local_status=local_status,
                max_subagents=max_subagents,
                stale_after_minutes=stale_after,
                kaveman_enabled=kaveman_enabled,
                sessions=[session for session in sessions if session],
            )
            data["state_db"] = str(state_db_path(config))
            data["codex_status_file"] = sync_codex_status_file_from_env(config)
            return stable_envelope(
                command="manager",
                status="pass",
                summary=f"Qwendex local subagents are {'enabled' if enabled else 'disabled'}.",
                next_actions=data["next_actions"],
                data=data,
            )
        if args.action == "estimate":
            return manager_estimate_envelope(
                config,
                command_name="manager",
                prompt=args.prompt,
                mode=mode,
                local_status=local_status,
            )
        if args.action == "status":
            rows = conn.execute(
                "SELECT * FROM qwendex_agent_sessions ORDER BY updated_at DESC LIMIT ?",
                (args.limit,),
            ).fetchall()
            sessions = [row_to_agent_session(row) for row in rows]
            data = manager_mode_payload(
                config,
                mode=mode,
                local_status=local_status,
                max_subagents=max_subagents,
                stale_after_minutes=stale_after,
                kaveman_enabled=kaveman_enabled,
                sessions=[session for session in sessions if session],
            )
            data["agent_sessions"] = [session for session in sessions if session]
            data["state_db"] = str(state_db_path(config))
            status = data["deployment_contract"]["status"]
            return stable_envelope(
                command="manager",
                status=status,
                summary=f"Loaded {len(data['agent_sessions'])} Qwendex manager sessions.",
                next_actions=(
                    ["Spawn/register at least one manager lane or set orchestration.manager_deploy_policy to disabled."]
                    if status == "blocked"
                    else data["next_actions"]
                ),
                data=data,
            )
        if args.action == "assign":
            if not args.agent_id or not args.lane:
                return stable_envelope(command="manager", status="blocked", summary="Manager assign requires --agent-id and --lane.", errors=["missing agent_id or lane"])
            artifacts = args.artifact or []
            task_class = args.task_class or infer_task_class(args.lane)
            risk = args.risk or ("high" if task_class in {"security", "architecture", "release acceptance"} else "medium")
            routing = lane_model_reasoning(config, task_class=task_class, lane=args.lane, risk=risk, local_status=local_status)
            context_packet = {
                "objective": args.objective or args.stop_condition,
                "task_class": task_class,
                "allowed_scope": args.write_surface,
                "exact_files": args.file or [],
                "needed_docs": args.needed_doc or [],
                "stop_condition": args.stop_condition,
                "expected_artifact": args.expected_artifact,
                "receipt_path": args.receipt_path,
                "context_budget": args.context_budget or config["context"]["compact_limit"],
                "model_reasoning_assignment": routing,
                "review_requirement": args.review_requirement,
                "risk": risk,
            }
            conn.execute(
                """
                INSERT INTO qwendex_agent_sessions
                (agent_id, lane, task_id, owner, write_surface, stop_condition, artifacts_json, status, heartbeat_at, created_at, updated_at, stop_reason, close_receipt, context_packet_json, routing_json, validation_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, '', '', ?, ?, 'pending')
                ON CONFLICT(agent_id) DO UPDATE SET
                  lane=excluded.lane,
                  task_id=excluded.task_id,
                  owner=excluded.owner,
                  write_surface=excluded.write_surface,
                  stop_condition=excluded.stop_condition,
                  artifacts_json=excluded.artifacts_json,
                  status='active',
                  heartbeat_at=excluded.heartbeat_at,
                  updated_at=excluded.updated_at,
                  stop_reason='',
                  close_receipt='',
                  context_packet_json=excluded.context_packet_json,
                  routing_json=excluded.routing_json,
                  validation_status='pending'
                """,
                (
                    args.agent_id,
                    args.lane,
                    args.task_id or "",
                    args.owner,
                    args.write_surface,
                    args.stop_condition,
                    json_dumps(artifacts),
                    now,
                    now,
                    now,
                    json_dumps(context_packet),
                    json_dumps(routing),
                ),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM qwendex_agent_sessions WHERE agent_id = ?", (args.agent_id,)).fetchone()
            return stable_envelope(
                command="manager",
                status="pass",
                summary=f"Assigned agent session {args.agent_id} to lane {args.lane}.",
                next_actions=["Review subagent output before treating it as authoritative."],
                data={"agent_session": row_to_agent_session(row)},
            )
        if args.action == "heartbeat":
            row = conn.execute("SELECT * FROM qwendex_agent_sessions WHERE agent_id = ?", (args.agent_id,)).fetchone()
            if row is None:
                return stable_envelope(command="manager", status="blocked", summary=f"Agent session not found: {args.agent_id}", errors=[args.agent_id])
            conn.execute(
                "UPDATE qwendex_agent_sessions SET heartbeat_at = ?, updated_at = ? WHERE agent_id = ?",
                (now, now, args.agent_id),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM qwendex_agent_sessions WHERE agent_id = ?", (args.agent_id,)).fetchone()
            return stable_envelope(command="manager", status="pass", summary=f"Heartbeat recorded for {args.agent_id}.", data={"agent_session": row_to_agent_session(row)})
        if args.action == "close-stale":
            cutoff_seconds = max(stale_after, 5) * 60
            rows = conn.execute("SELECT * FROM qwendex_agent_sessions WHERE status = 'active'").fetchall()
            closed: list[dict[str, Any]] = []
            for row in rows:
                if (datetime.now(UTC) - parse_utc(row["heartbeat_at"])).total_seconds() >= cutoff_seconds:
                    close_receipt = make_id("close")
                    conn.execute(
                        "UPDATE qwendex_agent_sessions SET status = 'closed', updated_at = ?, stop_reason = 'stale', close_receipt = ? WHERE agent_id = ?",
                        (now, close_receipt, row["agent_id"]),
                    )
                    updated = conn.execute("SELECT * FROM qwendex_agent_sessions WHERE agent_id = ?", (row["agent_id"],)).fetchone()
                    closed.append(row_to_agent_session(updated) or {})
            conn.commit()
            return stable_envelope(
                command="manager",
                status="pass",
                summary=f"Closed {len(closed)} stale Qwendex manager sessions.",
                data={"closed_count": len(closed), "closed": closed, "stale_after_minutes": max(stale_after, 5)},
            )
    return stable_envelope(command="manager", status="blocked", summary=f"Unknown manager action: {args.action}", errors=[args.action])


def latest_snapshot(conn: sqlite3.Connection, task_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM qwendex_context_snapshots WHERE task_id = ? ORDER BY created_at DESC LIMIT 1",
        (task_id,),
    ).fetchone()
    return row_to_context_snapshot(row)


def command_context(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    now = utc_now()
    with connect_state(config) as conn:
        if args.action == "snapshot":
            snapshot_id = make_id("ctx")
            conn.execute(
                """
                INSERT INTO qwendex_context_snapshots
                (snapshot_id, task_id, objective, decisions_json, open_files_json, evidence_refs_json, blocked_items_json, next_actions_json, budget, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    args.task_id,
                    args.objective,
                    json_dumps(args.decision or []),
                    json_dumps(args.open_file or []),
                    json_dumps(args.evidence or []),
                    json_dumps(args.blocked_item or []),
                    json_dumps(args.next_action or []),
                    args.budget,
                    now,
                ),
            )
            conn.commit()
            snapshot = latest_snapshot(conn, args.task_id)
            return stable_envelope(
                command="context",
                status="pass",
                summary=f"Created context snapshot {snapshot_id}.",
                data={"snapshot": snapshot},
            )
        snapshot = latest_snapshot(conn, args.task_id)
        if args.action == "reminder":
            threshold = int(config["context"].get("reminder_tool_call_threshold", 50))
            interval = int(config["context"].get("reminder_repeat_interval", 25))
            boundary_labels = {str(item).lower() for item in config["context"].get("phase_boundary_labels", [])}
            phase = (args.phase or "").strip().lower()
            at_boundary = bool(phase and phase in boundary_labels)
            over_threshold = args.tool_calls >= threshold
            repeated = over_threshold and interval > 0 and (args.tool_calls - threshold) >= interval
            if not over_threshold and not at_boundary:
                recommendation = "continue"
            elif snapshot is None:
                recommendation = "snapshot_first"
            elif at_boundary or repeated:
                recommendation = "compact_now"
            else:
                recommendation = "compact_plan"
            next_command = (
                f"scripts/qwendex context snapshot --task-id {args.task_id} --objective '...' --next-action '...' --json"
                if recommendation == "snapshot_first"
                else f"scripts/qwendex context compact-plan --task-id {args.task_id} --budget {args.budget or config['context']['compact_limit']} --json"
            )
            reminder = {
                "task_id": args.task_id,
                "tool_calls": args.tool_calls,
                "threshold": threshold,
                "repeat_interval": interval,
                "phase": args.phase,
                "at_phase_boundary": at_boundary,
                "has_snapshot": snapshot is not None,
                "recommendation": recommendation,
                "rationale": {
                    "over_threshold": over_threshold,
                    "repeated_reminder": repeated,
                    "phase_boundary_labels": sorted(boundary_labels),
                },
                "next_command": next_command,
                "snapshot": snapshot,
            }
            return stable_envelope(
                command="context",
                status="pass",
                summary=f"Context reminder recommends {recommendation}.",
                next_actions=[next_command],
                data={"reminder": reminder},
            )
        if snapshot is None:
            return stable_envelope(command="context", status="blocked", summary=f"No context snapshot found for {args.task_id}.", errors=[args.task_id])
        if args.action == "compact-plan":
            budget = args.budget or snapshot.get("budget") or config["context"]["compact_limit"]
            plan = {
                "task_id": args.task_id,
                "budget": budget,
                "summary": snapshot["objective"],
                "keep": ["objective", "decisions", "open_files", "evidence_refs", "blocked_items", "next_actions"],
                "decisions": snapshot["decisions"][:10],
                "open_files": snapshot["open_files"][:20],
                "evidence_refs": snapshot["evidence_refs"][:20],
                "blocked_items": snapshot["blocked_items"],
                "next_actions": snapshot["next_actions"][:10],
            }
            return stable_envelope(command="context", status="pass", summary=f"Built compact plan for {args.task_id}.", data={"compact_plan": plan})
        if args.action == "pack":
            evidence_rows = conn.execute(
                "SELECT * FROM qwendex_evidence WHERE task_id = ? ORDER BY created_at DESC LIMIT ?",
                (args.task_id, args.limit),
            ).fetchall()
            handoff_rows = conn.execute(
                "SELECT * FROM qwendex_handoffs WHERE task_id = ? ORDER BY created_at DESC LIMIT ?",
                (args.task_id, args.limit),
            ).fetchall()
            return stable_envelope(
                command="context",
                status="pass",
                summary=f"Built context pack for {args.task_id}.",
                data={
                    "snapshot": snapshot,
                    "evidence": [row_to_evidence(row) for row in evidence_rows],
                    "handoffs": [row_to_handoff(row) for row in handoff_rows],
                },
            )
    return stable_envelope(command="context", status="blocked", summary=f"Unknown context action: {args.action}", errors=[args.action])


def command_handoff(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    now = utc_now()
    with connect_state(config) as conn:
        if args.action == "create":
            handoff_id = args.handoff_id or make_id("handoff")
            conn.execute(
                """
                INSERT INTO qwendex_handoffs
                (handoff_id, task_id, status, summary, evidence_refs_json, next_actions_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    handoff_id,
                    args.task_id,
                    args.status,
                    args.summary,
                    json_dumps(args.evidence or []),
                    json_dumps(args.next_action or []),
                    now,
                ),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM qwendex_handoffs WHERE handoff_id = ?", (handoff_id,)).fetchone()
            return stable_envelope(command="handoff", status="pass", summary=f"Created handoff {handoff_id}.", data={"handoff": row_to_handoff(row)})
        if args.handoff_id:
            row = conn.execute("SELECT * FROM qwendex_handoffs WHERE handoff_id = ?", (args.handoff_id,)).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM qwendex_handoffs WHERE task_id = ? ORDER BY created_at DESC LIMIT 1",
                (args.task_id,),
            ).fetchone()
        if row is None:
            target = args.handoff_id or args.task_id
            return stable_envelope(command="handoff", status="blocked", summary=f"Handoff not found: {target}", errors=[target])
        return stable_envelope(command="handoff", status="pass", summary="Loaded Qwendex handoff.", data={"handoff": row_to_handoff(row)})


def command_evidence(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    now = utc_now()
    with connect_state(config) as conn:
        if args.action == "add":
            evidence_id = args.evidence_id or make_id("ev")
            path = Path(args.path).expanduser()
            digest = args.sha256 or (sha256_file(path) if path.exists() and path.is_file() else hashlib.sha256(str(path).encode("utf-8")).hexdigest())
            conn.execute(
                """
                INSERT INTO qwendex_evidence
                (evidence_id, task_id, claim, path, sha256, kind, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (evidence_id, args.task_id, args.claim, str(path), digest, args.kind, now),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM qwendex_evidence WHERE evidence_id = ?", (evidence_id,)).fetchone()
            return stable_envelope(command="evidence", status="pass", summary=f"Added evidence {evidence_id}.", data={"evidence": row_to_evidence(row)})
        params: list[Any] = []
        where: list[str] = []
        if args.task_id:
            where.append("task_id = ?")
            params.append(args.task_id)
        if args.claim:
            where.append("claim LIKE ?")
            params.append(f"%{args.claim}%")
        query = "SELECT * FROM qwendex_evidence"
        if where:
            query += " WHERE " + " AND ".join(where)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(args.limit)
        rows = conn.execute(query, tuple(params)).fetchall()
        return stable_envelope(
            command="evidence",
            status="pass",
            summary=f"Loaded {len(rows)} evidence records.",
            data={"evidence": [row_to_evidence(row) for row in rows]},
        )


def command_queue(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    module = script_module("artifact_queue_mcp")
    payload = {
        "dir": args.dir,
        "file": args.file,
        "items": args.item or [],
        "min_bytes": args.min_bytes,
        "complete_existing": args.complete_existing,
        "reason": args.reason,
    }
    action_map = {
        "status": module.tool_queue_status,
        "next": module.tool_queue_next,
        "init": module.tool_queue_init,
        "start": module.tool_queue_start,
        "done": module.tool_queue_done,
        "blocked": module.tool_queue_blocked,
    }
    try:
        data = action_map[args.action](payload)
    except Exception as exc:
        return stable_envelope(
            command="queue",
            status="blocked",
            summary=f"Qwendex queue {args.action} blocked.",
            errors=[str(exc)],
            data={"dir": args.dir, "action": args.action},
        )
    status = "blocked" if args.action == "next" and data.get("status") == "blocked" else "pass"
    return stable_envelope(
        command="queue",
        status=status,
        summary=f"Qwendex queue {args.action} returned {data.get('status', 'pass')}.",
        artifacts=[data.get("queue", "")] if data.get("queue") else [],
        next_actions=["Resolve blocked queue items before continuing."] if status == "blocked" else [],
        data={"queue": data},
    )


def command_manager(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    state_response = command_manager_state(args, config)
    if state_response is not None:
        return state_response
    with connect_state(config) as conn:
        mode = current_manager_mode(config, conn, explicit=args.mode)
        local_status = local_subagent_status(config, enabled=current_local_enabled(config, conn), env=os.environ, probe=True)
        kaveman_enabled = current_kaveman_enabled(config, conn)
        rows = conn.execute("SELECT * FROM qwendex_agent_sessions ORDER BY updated_at DESC LIMIT ?", (args.limit,)).fetchall()
        sessions = [row_to_agent_session(row) for row in rows]
    profile = manager_mode_profile(config, mode)
    max_subagents = args.max_subagents or profile["max_subagents"]
    stale_after = mode_stale_after_minutes(config, mode, args.stale_after_minutes)
    errors: list[str] = []
    if not isinstance(max_subagents, int) or max_subagents < 1 or max_subagents > MANAGER_MAX_SUBAGENTS_LIMIT:
        errors.append(f"max_subagents must be between 1 and {MANAGER_MAX_SUBAGENTS_LIMIT}: {max_subagents}")
    if not isinstance(stale_after, int) or stale_after < 5 or stale_after > 240:
        errors.append(f"stale_after_minutes must be between 5 and 240: {stale_after}")
    if errors:
        return stable_envelope(
            command="manager",
            status="blocked",
            summary="Qwendex manager override values are outside configured bounds.",
            errors=errors,
            data={
                "max_subagents": max_subagents,
                "stale_after_minutes": stale_after,
                "allowed": {"max_subagents": [1, MANAGER_MAX_SUBAGENTS_LIMIT], "stale_after_minutes": [5, 240]},
            },
        )
    legacy_mode = args.mode if args.mode and normalize_manager_mode(args.mode) != args.mode else ""
    data = manager_mode_payload(
        config,
        mode=mode,
        local_status=local_status,
        max_subagents=max_subagents,
        stale_after_minutes=stale_after,
        kaveman_enabled=kaveman_enabled,
        legacy_mode=legacy_mode,
        sessions=[session for session in sessions if session],
    )
    data["close_stale"] = args.close_stale
    status = data["deployment_contract"]["status"]
    return stable_envelope(
        command="manager",
        status=status,
        summary=f"Qwendex manager mode is {data['label']}.",
        next_actions=(
            ["Spawn/register at least one manager lane or set orchestration.manager_deploy_policy to disabled."]
            if status == "blocked"
            else data["next_actions"]
        ),
        data=data,
    )


def command_codex_status(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    write_path = Path(args.write).expanduser() if args.write else None
    data = codex_status_payload(config, write_path=write_path)
    artifacts = [data["status_file"]] if data.get("status_file") else []
    return stable_envelope(
        command="codex-status",
        status="pass",
        summary=data["text"],
        artifacts=artifacts,
        data=data,
    )


def command_codex_patch(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    del config
    if args.action == "locations":
        data = {
            "known_versions": sorted(CODEX_PATCH_MANIFESTS),
            "manifests": CODEX_PATCH_MANIFESTS,
            "runtime_contract": {
                "status_file_env": QWENDEX_CODEX_STATUS_FILE_ENV,
                "status_line_item": QWENDEX_CODEX_STATUS_ITEM_ID,
                "patch_marker": QWENDEX_CODEX_PATCH_MARKER,
                "manager_toggle": "qwendex manager mode --toggle --json",
                "kaveman_toggle": "qwendex manager kaveman --toggle --json",
                "local_toggle": "qwendex manager local --toggle --json",
            },
        }
        return stable_envelope(
            command="codex-patch",
            status="pass",
            summary="Qwendex Codex TUI patch locations are available.",
            data=data,
        )
    if args.action == "apply":
        return codex_patch_apply_payload(args)
    return codex_patch_payload(args)


def command_version(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    return stable_envelope(
        command="version",
        status="pass",
        summary=f"Qwendex {VERSION}",
        data={"version": VERSION, "config_schema": DEFAULT_CONFIG["schema_version"]},
    )


def command_line() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Qwendex public CLI")
    parser.add_argument("--config", type=Path, help="project Qwendex config")
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check")
    check.add_argument("--json", action="store_true")

    doctor = sub.add_parser("doctor")
    doctor.add_argument("--json", action="store_true")

    for name in ("up", "down", "restart"):
        command = sub.add_parser(name)
        command.add_argument("service", nargs="?", default="all")
        command.add_argument("--dry-run", action="store_true")
        command.add_argument("--json", action="store_true")

    llmstack = sub.add_parser("llmstack")
    llmstack_sub = llmstack.add_subparsers(dest="action", required=True)
    for name in ("check", "doctor"):
        command = llmstack_sub.add_parser(name)
        command.add_argument("--json", action="store_true")
    for name in ("up", "down", "restart"):
        command = llmstack_sub.add_parser(name)
        command.add_argument("service", nargs="?", default="all")
        command.add_argument("--dry-run", action="store_true")
        command.add_argument("--json", action="store_true")

    exec_parser = sub.add_parser("exec")
    exec_parser.add_argument("prompt", nargs="+")
    exec_parser.add_argument("--seat", choices=["auto", *sorted(DEFAULT_CONFIG["seats"])], default="auto")
    exec_parser.add_argument("--prefer-local", action="store_true")
    exec_parser.add_argument("--timeout", type=int, default=600)
    exec_parser.add_argument("--cwd", default="")
    exec_parser.add_argument("--dry-run", action="store_true")
    exec_parser.add_argument("--json", action="store_true")

    route = sub.add_parser("route")
    route.add_argument("--seat", choices=["auto", *sorted(DEFAULT_CONFIG["seats"])], default="auto")
    route.add_argument("--task-class", default="exec")
    route.add_argument("--prefer-local", action="store_true")
    route.add_argument("--json", action="store_true")

    estimate = sub.add_parser("estimate")
    estimate.add_argument("--prompt", default="")
    estimate.add_argument("--json", action="store_true")

    eval_parser = sub.add_parser("eval")
    eval_parser.add_argument("--case", default="")
    eval_parser.add_argument("--all", action="store_true")
    eval_parser.add_argument("--live", action="store_true")
    eval_parser.add_argument("--results-root", type=Path)
    eval_parser.add_argument("--json", action="store_true")

    receipt = sub.add_parser("receipt")
    receipt.add_argument("target", nargs="?", default="latest")
    receipt.add_argument("--json", action="store_true")

    seat = sub.add_parser("seat")
    seat.add_argument("seat", nargs="?", choices=sorted(DEFAULT_CONFIG["seats"]))
    seat.add_argument("--json", action="store_true")

    task = sub.add_parser("task")
    task.add_argument("action", choices=["create", "start", "update", "finish", "block", "status"])
    task.add_argument("--task-id", default="")
    task.add_argument("--title", default="")
    task.add_argument("--priority", default="P2")
    task.add_argument("--owner", default="main")
    task.add_argument("--phase", default="default")
    task.add_argument("--status", choices=["open", "in_progress", "done", "blocked"], default="")
    task.add_argument("--summary", default="")
    task.add_argument("--reason", default="")
    task.add_argument("--limit", type=int, default=20)
    task.add_argument("--json", action="store_true")

    context = sub.add_parser("context")
    context.add_argument("action", choices=["snapshot", "compact-plan", "pack", "reminder"])
    context.add_argument("--task-id", required=True)
    context.add_argument("--objective", default="")
    context.add_argument("--phase", default="")
    context.add_argument("--decision", action="append")
    context.add_argument("--open-file", action="append")
    context.add_argument("--evidence", action="append")
    context.add_argument("--blocked-item", action="append")
    context.add_argument("--next-action", action="append")
    context.add_argument("--tool-calls", type=int, default=0)
    context.add_argument("--budget", type=int, default=0)
    context.add_argument("--limit", type=int, default=10)
    context.add_argument("--json", action="store_true")

    handoff = sub.add_parser("handoff")
    handoff.add_argument("action", choices=["create", "show"])
    handoff.add_argument("--handoff-id", default="")
    handoff.add_argument("--task-id", default="")
    handoff.add_argument("--status", default="ready")
    handoff.add_argument("--summary", default="")
    handoff.add_argument("--evidence", action="append")
    handoff.add_argument("--next-action", action="append")
    handoff.add_argument("--json", action="store_true")

    evidence = sub.add_parser("evidence")
    evidence.add_argument("action", choices=["add", "query"])
    evidence.add_argument("--evidence-id", default="")
    evidence.add_argument("--task-id", default="")
    evidence.add_argument("--claim", default="")
    evidence.add_argument("--path", default="")
    evidence.add_argument("--sha256", default="")
    evidence.add_argument("--kind", default="artifact")
    evidence.add_argument("--limit", type=int, default=20)
    evidence.add_argument("--json", action="store_true")

    queue = sub.add_parser("queue")
    queue.add_argument("action", choices=["status", "next", "init", "start", "done", "blocked"])
    queue.add_argument("--dir", default=".")
    queue.add_argument("--item", action="append")
    queue.add_argument("--file", default="")
    queue.add_argument("--min-bytes", type=int, default=1)
    queue.add_argument("--complete-existing", action="store_true")
    queue.add_argument("--reason", default="")
    queue.add_argument("--json", action="store_true")

    learn = sub.add_parser("learn")
    learn.add_argument(
        "action",
        choices=["status", "harvest", "dry-run", "run", "stage", "adopt", "audit", "rollback", "proposal-summary"],
    )
    learn.add_argument("--backend", default="")
    learn.add_argument("--source", default="")
    learn.add_argument("--proposal", default="")
    learn.add_argument("--approve", action="store_true")
    learn.add_argument("--allow-codex-budget", action="store_true")
    learn.add_argument("--no-execute", action="store_true")
    learn.add_argument("--json", action="store_true")

    manager = sub.add_parser("manager")
    manager.add_argument("action", nargs="?", choices=["status", "assign", "heartbeat", "close-stale", "mode", "estimate", "kaveman", "local"])
    manager.add_argument("--mode", choices=["manual", "auto", "lite", "medium", "heavy", "manager", "manager_only"], default="")
    manager.add_argument("--set", default="")
    manager.add_argument("--cycle", action="store_true")
    manager.add_argument("--toggle", action="store_true")
    manager.add_argument("--prompt", default="")
    manager.add_argument("--max-subagents", type=int, default=0)
    manager.add_argument("--stale-after-minutes", type=int, default=0)
    manager.add_argument("--close-stale", action="store_true")
    manager.add_argument("--agent-id", default="")
    manager.add_argument("--lane", default="")
    manager.add_argument("--task-id", default="")
    manager.add_argument("--objective", default="")
    manager.add_argument("--task-class", default="")
    manager.add_argument("--file", action="append")
    manager.add_argument("--needed-doc", action="append")
    manager.add_argument("--owner", default="manager")
    manager.add_argument("--write-surface", default="read-only")
    manager.add_argument("--stop-condition", default="return compact findings")
    manager.add_argument("--expected-artifact", default="")
    manager.add_argument("--receipt-path", default="")
    manager.add_argument("--context-budget", type=int, default=0)
    manager.add_argument("--risk", choices=["low", "medium", "high"], default="")
    manager.add_argument("--review-requirement", default="manager review required")
    manager.add_argument("--artifact", action="append")
    manager.add_argument("--limit", type=int, default=20)
    manager.add_argument("--shortcut", action="store_true")
    manager.add_argument("--json", action="store_true")

    codex_status = sub.add_parser("codex-status")
    codex_status.add_argument("--write", default="")
    codex_status.add_argument("--plain", action="store_true")
    codex_status.add_argument("--json", action="store_true")

    codex_patch = sub.add_parser("codex-patch")
    codex_patch.add_argument("action", choices=["status", "preflight", "locations", "apply"], nargs="?", default="status")
    codex_patch.add_argument("--codex-bin", default="codex")
    codex_patch.add_argument("--source", default="")
    codex_patch.add_argument("--require-applied", action="store_true")
    codex_patch.add_argument("--dry-run", action="store_true")
    codex_patch.add_argument("--json", action="store_true")

    version = sub.add_parser("version")
    version.add_argument("--json", action="store_true")
    return parser


def human_print(data: dict[str, Any]) -> None:
    print(f"status: {data['status']}")
    print(data["summary"])
    for error in data.get("errors", []):
        print(f"- {error}")
    for artifact in data.get("artifacts", [])[:10]:
        print(artifact)


def run(args: argparse.Namespace) -> dict[str, Any]:
    config = load_qwendex_config(project_config=args.config)
    if args.command == "check":
        return command_check(args, config)
    if args.command == "doctor":
        return command_doctor(args, config)
    if args.command in {"up", "down", "restart"}:
        return command_stack(args, config)
    if args.command == "llmstack":
        return command_llmstack(args, config)
    if args.command == "exec":
        return command_exec(args, config)
    if args.command == "route":
        return command_route(args, config)
    if args.command == "estimate":
        return command_estimate(args, config)
    if args.command == "eval":
        return command_eval(args, config)
    if args.command == "receipt":
        return command_receipt(args, config)
    if args.command == "seat":
        return command_seat(args, config)
    if args.command == "task":
        return command_task(args, config)
    if args.command == "context":
        return command_context(args, config)
    if args.command == "handoff":
        return command_handoff(args, config)
    if args.command == "evidence":
        return command_evidence(args, config)
    if args.command == "queue":
        return command_queue(args, config)
    if args.command == "learn":
        return command_learn(args, config)
    if args.command == "manager":
        return command_manager(args, config)
    if args.command == "codex-status":
        return command_codex_status(args, config)
    if args.command == "codex-patch":
        return command_codex_patch(args, config)
    if args.command == "version":
        return command_version(args, config)
    raise RuntimeError(f"unknown command: {args.command}")


def exit_code(data: Mapping[str, Any]) -> int:
    return 0 if data.get("status") in {"pass", "ready"} else 1


def main(argv: list[str] | None = None) -> int:
    parser = command_line()
    args = parser.parse_args(argv)
    try:
        data = run(args)
    except Exception as exc:  # pragma: no cover - defensive CLI boundary
        data = stable_envelope(command=getattr(args, "command", "unknown"), status="fail", summary=str(exc), errors=[str(exc)])
    if args.command == "codex-status" and not getattr(args, "json", False):
        print(data.get("data", {}).get("text", data["summary"]))
    elif getattr(args, "json", False):
        print_json(data)
    else:
        human_print(data)
    return exit_code(data)


if __name__ == "__main__":
    raise SystemExit(main())
