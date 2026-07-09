#!/usr/bin/env python3
"""Public Qwendex CLI facade for Codex plus bounded local Qwen support."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shlex
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
VERSION = "0.0.2-rc4"
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
    "agent-management.md",
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
    "scripts/qdex",
    "scripts/qwendex",
    "scripts/qwendex_cli.py",
    "scripts/qwendex_install_deps",
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
    "config/qwendex/dependencies.json",
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

MANAGER_MODE_ORDER = ("off", "auto", "lite", "medium", "heavy", "manager")
MANAGER_MODE_ALIASES = {
    "": "",
    "off": "off",
    "disabled": "off",
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
    "off": "Off",
    "auto": "Auto",
    "lite": "Lite",
    "medium": "Medium",
    "heavy": "Heavy",
    "manager": "Manager Mode",
}
AGENT_USE_ORDER = ("off", "auto", "lite", "medium", "heavy", "manager")
AGENT_USE_LABELS = {
    "off": "Off",
    "auto": "Auto",
    "lite": "Lite",
    "medium": "Medium",
    "heavy": "Heavy",
    "manager": "Manager",
}
AGENT_USE_ALIASES = {
    "off": "off",
    "disabled": "off",
    "auto": "auto",
    "lite": "lite",
    "light": "lite",
    "medium": "medium",
    "default": "medium",
    "heavy": "heavy",
    "manager": "manager",
    "manager_mode": "manager",
    "manager_only": "manager",
}
AGENT_TERMINAL_STATUSES = {"completed", "blocked", "failed", "closed", "tombstoned"}
AGENT_HOOK_EVENTS = {
    "SessionStart",
    "UserPromptSubmit",
    "SubagentStart",
    "SubagentStop",
    "Stop",
    "PreToolUse",
    "PostToolUse",
    "PreCompact",
    "PostCompact",
}
MANAGED_AGENT_HOOKS = {
    "UserPromptSubmit": {"matcher": "", "timeout": 5},
    "SubagentStart": {"matcher": ".*", "timeout": 5},
    "SubagentStop": {"matcher": ".*", "timeout": 5},
    "Stop": {"matcher": "", "timeout": 5},
    "PreToolUse": {"matcher": ".*", "timeout": 5},
    "PreCompact": {"matcher": "", "timeout": 5},
    "PostCompact": {"matcher": "", "timeout": 5},
}
READ_ONLY_AGENT_PROFILES = {"explorer", "verifier", "docs_researcher", "audit", "review"}
ROOT_ONLY_AGENT_TOOLS = {"spawn_agent", "close_agent", "wait_agent", "resume_agent", "agent_ledger_update_status"}
WRITE_TOOL_NAMES = {"write", "edit", "apply_patch", "create_file", "delete_file", "move_file"}
MANAGER_DECISION_ATTACH_WINDOW_MINUTES = 24 * 60
MANAGED_HOOK_RUNTIME_ENV_KEYS = (
    "CODEX_HOME",
    "QWENDEX_STATE_DB",
    "QWENDEX_RESULTS_ROOT",
    "QWENDEX_LEDGER_DB",
    "QWENDEX_CODEX_STATUS_FILE",
    "QWENDEX_DEV_ROOT",
    "QWENDEX_ROOT",
)
RELEASE_COMMAND_RE = re.compile(
    r"(^|\s)(npm|pnpm|yarn)\s+.*\bpublish\b|"
    r"(^|\s)cargo\s+publish\b|"
    r"(^|\s)gh\s+release\s+create\b|"
    r"(^|\s)git\s+push\s+(--tags|origin\s+(main|master))\b"
)
DEFAULT_AGENT_PROFILES: dict[str, dict[str, Any]] = {
    "explorer": {
        "name": "explorer",
        "description": "Read-only repo explorer that maps affected files, symbols, tests, risks, and implementation paths.",
        "role": "exploration",
        "model_reasoning_effort": "medium",
        "sandbox_mode": "read-only",
        "tools_allow": ["read", "search", "status"],
        "tools_deny": ["write", "spawn_agent", "close_agent"],
        "can_spawn": False,
        "final_report_required": True,
        "default_required": True,
        "nickname_candidates": ["Atlas", "Scout", "Mapper"],
    },
    "implementer": {
        "name": "implementer",
        "description": "Scoped implementation worker for narrow code changes.",
        "role": "implementation",
        "model_reasoning_effort": "high",
        "sandbox_mode": "workspace-write",
        "tools_allow": ["read", "search", "write", "test", "status"],
        "tools_deny": ["spawn_agent", "close_agent"],
        "can_spawn": False,
        "final_report_required": True,
        "default_required": True,
        "nickname_candidates": ["Builder", "Patch", "Forge"],
    },
    "verifier": {
        "name": "verifier",
        "description": "Read-only verifier for tests, regressions, dirty state, and unsupported claims.",
        "role": "verification",
        "model_reasoning_effort": "high",
        "sandbox_mode": "read-only",
        "tools_allow": ["read", "search", "test", "status"],
        "tools_deny": ["write", "spawn_agent", "close_agent"],
        "can_spawn": False,
        "final_report_required": True,
        "default_required": True,
        "nickname_candidates": ["Audit", "FIDO", "Check"],
    },
    "docs_researcher": {
        "name": "docs_researcher",
        "description": "Documentation/API specialist for version-specific external or local docs verification.",
        "role": "docs",
        "model_reasoning_effort": "medium",
        "sandbox_mode": "read-only",
        "tools_allow": ["read", "search", "web"],
        "tools_deny": ["write", "spawn_agent", "close_agent"],
        "can_spawn": False,
        "final_report_required": True,
        "default_required": False,
        "nickname_candidates": ["Docs", "Reference", "PAO"],
    },
    "release_manager": {
        "name": "release_manager",
        "description": "Release/versioning specialist for changelog, semver, tags, and publish checklist.",
        "role": "release",
        "model_reasoning_effort": "medium",
        "sandbox_mode": "workspace-write",
        "tools_allow": ["read", "search", "write", "test", "status"],
        "tools_deny": ["publish", "push_tags", "spawn_agent", "close_agent"],
        "can_spawn": False,
        "final_report_required": True,
        "default_required": False,
        "nickname_candidates": ["Release", "Surgeon", "Ship"],
    },
    "scribe": {
        "name": "scribe",
        "description": "Background run logger that preserves decisions, raw outputs, and final artifacts.",
        "role": "logging",
        "model_reasoning_effort": "low",
        "sandbox_mode": "workspace-write",
        "tools_allow": ["read", "write-run-artifacts"],
        "tools_deny": ["write-source", "spawn_agent", "close_agent"],
        "can_spawn": False,
        "final_report_required": False,
        "default_required": False,
        "nickname_candidates": ["Scribe", "Log", "Archive"],
    },
}
DEFAULT_MANAGER_TEAM = {
    "name": "manager",
    "description": "Default Qwendex Manager Mode team.",
    "default_mode": "Manager",
    "members": ["explorer", "implementer", "verifier", "docs_researcher", "release_manager", "scribe"],
    "required_lanes_by_task": {
        "repo_exploration": ["explorer"],
        "code_edit_small": ["implementer", "verifier"],
        "code_edit_complex": ["explorer", "implementer", "verifier"],
        "docs_api_uncertainty": ["docs_researcher"],
        "release_publish": ["release_manager", "verifier"],
    },
    "routing_rules": [
        "quick questions go direct when no repo, docs, or edit work is needed",
        "read-heavy repo mapping uses explorer in Heavy/Manager",
        "non-trivial edits use implementer plus verifier in Heavy/Manager",
        "release tasks use release_manager plus verifier and require explicit publish approval",
    ],
}
MANAGER_REASONING_LEVELS = {"low", "medium", "high", "xhigh"}
MANAGER_DEPLOY_POLICIES = {"auto", "disabled"}
MANAGER_MAX_SUBAGENTS_LIMIT = 10
MANAGER_DECISION_ROUTES = {"direct_single_writer", "manager_subagents", "blocked"}
MANAGER_STOP_STATUSES = {
    "STOP_MANAGER_PREFLIGHT_READY",
    "STOP_MANAGER_DIRECT_READY",
    "STOP_MANAGER_SUBAGENTS_READY",
    "STOP_MANAGER_BLOCKED_UNHOOKED",
    "STOP_MANAGER_UNATTACHED",
    "STOP_MANAGER_VALIDATION_PENDING",
    "STOP_MANAGER_CLOSED",
}
MANAGER_PROMPT_UNKNOWN_SUMMARY = "interactive_prompt_unknown_prelaunch"
MANAGER_UNHOOKED_OVERRIDE_ENV = "QWENDEX_MANAGER_ALLOW_UNHOOKED"
MANAGER_UNHOOKED_REASON_ENV = "QWENDEX_MANAGER_UNHOOKED_REASON"
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
            {
                "path": "codex-rs/tui/src/terminal_visualization_instructions.rs",
                "anchors": ["with_terminal_visualization_instructions", "TERMINAL_VISUALIZATION_INSTRUCTIONS"],
            },
        ],
        "required_source_edits": [
            "Add StatusLineItem::QwendexManager serialized as qwendex-manager.",
            "Render qwendex-manager from QWENDEX_CODEX_STATUS_FILE JSON text.",
            "Add qwendex-manager to status preview and styling surfaces.",
            "Add global keymap actions qwendex_toggle_manager, qwendex_toggle_kaveman, and qwendex_toggle_local.",
            "Dispatch those actions before generic composer input handling.",
            "After each action, call the configured Qwendex toggle command and refresh status surfaces.",
            "Append the active Kaveman directive from QWENDEX_CODEX_STATUS_FILE to TUI developer instructions.",
        ],
    },
}
CODEX_PATCH_MANIFESTS["0.142.5"] = {
    **CODEX_PATCH_MANIFESTS["0.142.4"],
    "codex_tag": "rust-v0.142.5",
}
CODEX_PATCH_MANIFESTS["0.143.0"] = {
    **CODEX_PATCH_MANIFESTS["0.142.5"],
    "codex_tag": "rust-v0.143.0",
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
            "off": {"label": "Off", "offload_target": "0%", "max_subagents": 1},
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
            "off": 30,
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


def normalize_agent_use_mode(value: Any) -> str:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return AGENT_USE_ALIASES.get(text, text)


def agent_policy_defaults(mode: str) -> dict[str, Any]:
    common = {
        "mode": mode,
        "agent_use": AGENT_USE_LABELS[mode],
        "child_can_spawn": False,
        "release_slot_on_final_status": True,
        "tombstone_uncloseable_agents": True,
        "emit_agent_status_events": True,
        "mirror_ledger_to_files": mode in {"heavy", "manager"},
        "policy_variant": "qwendex-cli-v1",
    }
    table: dict[str, dict[str, Any]] = {
        "off": {
            "min_threads": 0,
            "max_threads": 0,
            "max_depth": 0,
            "root_can_spawn": False,
            "require_agent_ledger": False,
            "require_verifier_for_edits": False,
            "require_final_report_contract": False,
            "require_routing_reason": False,
            "forbid_fork_context": True,
            "default_fork_context": False,
            "max_inherited_context_bytes": 0,
            "agent_idle_timeout_ms": 0,
            "wait_timeout_ms": 0,
            "close_timeout_ms": 5000,
            "max_resteer_attempts": 0,
        },
        "auto": {
            "min_threads": 2,
            "max_threads": 4,
            "max_depth": 1,
            "root_can_spawn": True,
            "require_agent_ledger": False,
            "require_verifier_for_edits": False,
            "require_final_report_contract": True,
            "require_routing_reason": False,
            "forbid_fork_context": False,
            "default_fork_context": False,
            "max_inherited_context_bytes": 16000,
            "agent_idle_timeout_ms": 300000,
            "wait_timeout_ms": 120000,
            "close_timeout_ms": 7500,
            "max_resteer_attempts": 1,
        },
        "lite": {
            "min_threads": 0,
            "max_threads": 1,
            "max_depth": 0,
            "root_can_spawn": False,
            "require_agent_ledger": False,
            "require_verifier_for_edits": False,
            "require_final_report_contract": False,
            "require_routing_reason": False,
            "forbid_fork_context": True,
            "default_fork_context": False,
            "max_inherited_context_bytes": 0,
            "agent_idle_timeout_ms": 0,
            "wait_timeout_ms": 0,
            "close_timeout_ms": 5000,
            "max_resteer_attempts": 0,
        },
        "medium": {
            "min_threads": 2,
            "max_threads": 4,
            "max_depth": 1,
            "root_can_spawn": True,
            "require_agent_ledger": False,
            "require_verifier_for_edits": False,
            "require_final_report_contract": True,
            "require_routing_reason": False,
            "forbid_fork_context": False,
            "default_fork_context": False,
            "max_inherited_context_bytes": 16000,
            "agent_idle_timeout_ms": 300000,
            "wait_timeout_ms": 120000,
            "close_timeout_ms": 7500,
            "max_resteer_attempts": 1,
        },
        "heavy": {
            "min_threads": 4,
            "max_threads": 8,
            "max_depth": 1,
            "root_can_spawn": True,
            "require_agent_ledger": True,
            "require_verifier_for_edits": True,
            "require_final_report_contract": True,
            "require_routing_reason": True,
            "forbid_fork_context": True,
            "default_fork_context": False,
            "max_inherited_context_bytes": 8192,
            "agent_idle_timeout_ms": 240000,
            "wait_timeout_ms": 90000,
            "close_timeout_ms": 10000,
            "max_resteer_attempts": 1,
        },
        "manager": {
            "min_threads": 8,
            "max_threads": MANAGER_MAX_SUBAGENTS_LIMIT,
            "max_depth": 1,
            "root_can_spawn": True,
            "require_agent_ledger": True,
            "require_verifier_for_edits": True,
            "require_final_report_contract": True,
            "require_routing_reason": True,
            "forbid_fork_context": True,
            "default_fork_context": False,
            "max_inherited_context_bytes": 4096,
            "agent_idle_timeout_ms": 180000,
            "wait_timeout_ms": 60000,
            "close_timeout_ms": 10000,
            "max_resteer_attempts": 2,
        },
    }
    return {**common, **table[mode]}


def agent_policy_hash(policy: Mapping[str, Any]) -> str:
    hashed = {
        key: value
        for key, value in policy.items()
        if key not in {"policy_hash", "source", "selector", "warnings", "errors", "env"}
    }
    return hashlib.sha256(json.dumps(hashed, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def resolve_agent_policy(
    config: Mapping[str, Any],
    *,
    cli_agent_use: str = "",
    env: Mapping[str, str] | None = None,
    selected_manager_mode: str = "",
) -> dict[str, Any]:
    del config
    source_env = env or os.environ
    selector_source = "default"
    selector = "Medium"
    if cli_agent_use:
        selector_source = "cli"
        selector = cli_agent_use
    elif source_env.get("QWENDEX_AGENT_USE"):
        selector_source = "qwendex-env"
        selector = str(source_env["QWENDEX_AGENT_USE"])
    elif source_env.get("CODEX_AGENT_USE"):
        selector_source = "codex-env"
        selector = str(source_env["CODEX_AGENT_USE"])
    elif selected_manager_mode:
        selector_source = "manager-mode"
        selector = selected_manager_mode
    mode = normalize_agent_use_mode(selector)
    warnings: list[str] = []
    errors: list[str] = []
    strict = env_flag(source_env.get("QWENDEX_AGENT_USE_STRICT")) is True
    if mode not in AGENT_USE_ORDER:
        message = f"invalid agent use selector {selector!r}; expected Off, Auto, Lite, Medium, Heavy, or Manager"
        if strict:
            errors.append(message)
            mode = "medium"
        else:
            warnings.append(f"{message}; falling back to Medium")
            mode = "medium"
            selector_source = f"{selector_source}-fallback"
    policy = agent_policy_defaults(mode)
    policy.update({
        "source": selector_source,
        "selector": selector,
        "warnings": warnings,
        "errors": errors,
    })
    policy["policy_hash"] = agent_policy_hash(policy)
    policy["env"] = {
        "QWENDEX_EFFECTIVE_AGENT_USE": policy["agent_use"],
        "QWENDEX_AGENT_POLICY_HASH": policy["policy_hash"],
        "QWENDEX_AGENT_POLICY_SOURCE": selector_source,
    }
    policy["tool_surface"] = {
        "root_management_tools": [
            "spawn_agent",
            "send_input",
            "resume_agent",
            "wait_agent",
            "close_agent",
            "list_agents",
            "agent_ledger_list",
            "agent_ledger_get",
            "agent_ledger_update_status",
            "agent_ledger_mark_required",
            "agent_ledger_resteer",
            "agent_ledger_tombstone",
        ] if policy["root_can_spawn"] else [],
        "child_management_tools": ["report_agent_result"] if mode != "lite" else [],
        "denied_child_tools": ["spawn_agent", "close_agent", "wait_agent", "resume_agent", "agent_ledger_update_status"],
    }
    return policy


def apply_agent_policy_env(policy: Mapping[str, Any]) -> None:
    for key, value in policy.get("env", {}).items():
        os.environ[str(key)] = str(value)


def policy_mode_for_manager(args: argparse.Namespace, config: Mapping[str, Any], fallback_mode: str) -> str:
    policy = resolve_agent_policy(
        config,
        cli_agent_use=getattr(args, "agent_use", ""),
        selected_manager_mode=fallback_mode,
    )
    if policy["errors"]:
        raise ValueError("; ".join(policy["errors"]))
    if policy["source"] != "default" and not getattr(args, "mode", ""):
        return str(policy["mode"])
    return fallback_mode


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


def local_state_label(local_state: str) -> str:
    return {
        "ready": "Ready",
        "off": "Off",
        "unavailable": "Unavailable",
        "unknown": "Unavailable",
    }.get(local_state, "Unavailable")


def local_indicator(config: Mapping[str, Any], enabled: bool, local_state: str | None = None) -> str:
    local_cfg = config.get("orchestration", {}).get("local_subagents", {})
    shortcut = local_cfg.get("shortcut", "Alt+L") if isinstance(local_cfg, Mapping) else "Alt+L"
    state = local_state or ("ready" if enabled else "off")
    return f"({shortcut}) Local: [{local_state_label(state)}]"


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


def qwendex_dev_paths_from_codex_home(env: Mapping[str, str] | None = None) -> dict[str, str]:
    source = env or os.environ
    raw_codex_home = str(source.get("CODEX_HOME") or "").strip()
    if not raw_codex_home:
        return {}
    codex_home = Path(raw_codex_home).expanduser()
    work_root = codex_home.parent
    if codex_home.name != "codex_home" or work_root.name != ".qwendex-dev":
        return {}
    state_root = work_root / "state"
    return {
        "results_root": str(work_root / "results" / "qwendex"),
        "ledger_db": str(state_root / "qwendex_ledger.sqlite"),
        "state_db": str(state_root / "qwendex.sqlite"),
    }


def env_config(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    source = env or os.environ
    dev_paths = qwendex_dev_paths_from_codex_home(source)
    data: dict[str, Any] = {}
    if source.get("QWENDEX_DEFAULT_SEAT"):
        data["default_seat"] = source["QWENDEX_DEFAULT_SEAT"]
    results_root = source.get("QWENDEX_RESULTS_ROOT") or dev_paths.get("results_root")
    if results_root:
        data["receipts"] = {"dir": results_root}
    ledger_db = source.get("QWENDEX_LEDGER_DB") or dev_paths.get("ledger_db")
    if ledger_db:
        data.setdefault("receipts", {})["ledger"] = ledger_db
    state_db = source.get("QWENDEX_STATE_DB") or dev_paths.get("state_db")
    if state_db:
        data["state"] = {"db": state_db}
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
            "local_enabled": False,
            "local_available": False,
            "local_usable": False,
            "local_state": "off",
            "indicator": local_indicator(config, False, "off"),
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
    if available is True:
        local_state = "ready"
    elif available is False:
        local_state = "unavailable"
    else:
        local_state = "unknown"
    return {
        "enabled": True,
        "available": available,
        "usable": bool(available),
        "local_enabled": True,
        "local_available": available,
        "local_usable": bool(available),
        "local_state": local_state,
        "indicator": local_indicator(config, True, local_state),
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
    route_local_status = dict(local_status)
    if should_prefer_local:
        route_local_state = "ready" if local_qwen.get("available") else "unavailable"
        route_local_usable = route_local_state == "ready" and seat == "qwen"
        route_local_status.update({
            "available": bool(local_qwen.get("available")),
            "usable": route_local_usable,
            "local_available": bool(local_qwen.get("available")),
            "local_usable": route_local_usable,
            "local_state": route_local_state,
            "indicator": local_indicator(config, True, route_local_state),
            "probe": local_qwen,
            "source": local_qwen.get("source"),
            "reason": local_qwen.get("reason"),
        })
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
        "local_subagents": route_local_status,
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


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def codex_home_from_env(env: Mapping[str, str] | None = None) -> Path:
    source = env or os.environ
    raw = str(source.get("CODEX_HOME") or Path.home() / ".codex")
    return Path(raw).expanduser()


def path_digest_policy(path: Path) -> str:
    return "sha256:" + sha256_text(str(path.expanduser().resolve(strict=False)))


def prompt_digest_and_summary(prompt: str, *, known: bool) -> tuple[str, str]:
    if not known:
        return "", MANAGER_PROMPT_UNKNOWN_SUMMARY
    clean = redact_text(" ".join(prompt.strip().split()))
    if len(clean) > 180:
        clean = clean[:177].rstrip() + "..."
    return sha256_text(prompt), clean or "empty_prompt"


def git_branch_and_status_digest(repo: Path | None = None) -> tuple[str, str]:
    root = repo or Path(os.environ.get("QWENDEX_MANAGER_TARGET_REPO") or os.environ.get("QWENDEX_EXEC_CWD") or os.getcwd())
    try:
        branch_result = subprocess.run(
            ["git", "-C", str(root), "branch", "--show-current"],
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
        status_result = subprocess.run(
            ["git", "-C", str(root), "status", "--short"],
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
    except Exception:
        return "", sha256_text("git_unavailable")
    branch = branch_result.stdout.strip() if branch_result.returncode == 0 else ""
    status_text = status_result.stdout if status_result.returncode == 0 else status_result.stderr
    return branch, sha256_text(status_text or "")


def manager_receipt_path(config: Mapping[str, Any], ledger_id: str) -> Path:
    return results_root(config) / "manager" / f"{safe_artifact_component(ledger_id, 'manager_decision')}.json"


def write_manager_decision_receipt(config: Mapping[str, Any], payload: Mapping[str, Any]) -> str:
    ledger_id = str(payload.get("ledger_id") or "manager_decision")
    path = manager_receipt_path(config, ledger_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(redact_obj(payload), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


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
        CREATE TABLE IF NOT EXISTS qwendex_agent_file_locks (
          lock_id TEXT PRIMARY KEY,
          agent_id TEXT NOT NULL,
          path TEXT NOT NULL,
          lock_type TEXT NOT NULL,
          acquired_at TEXT NOT NULL,
          released_at TEXT NOT NULL,
          reason TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS qwendex_manager_decisions (
          ledger_id TEXT PRIMARY KEY,
          session_id TEXT NOT NULL,
          record_type TEXT NOT NULL,
          schema_version INTEGER NOT NULL,
          timestamp_created TEXT NOT NULL,
          timestamp_updated TEXT NOT NULL,
          mode TEXT NOT NULL,
          agent_use TEXT NOT NULL,
          policy_source TEXT NOT NULL,
          policy_hash TEXT NOT NULL,
          codex_home_digest_or_path_policy TEXT NOT NULL,
          codex_home TEXT NOT NULL,
          hook_source_count INTEGER NOT NULL,
          hook_configured INTEGER NOT NULL,
          hook_verified INTEGER NOT NULL,
          hook_override INTEGER NOT NULL,
          hook_override_reason TEXT NOT NULL,
          local_enabled INTEGER NOT NULL,
          local_usable INTEGER NOT NULL,
          cloud_usable INTEGER NOT NULL,
          prompt_known INTEGER NOT NULL,
          prompt_digest TEXT NOT NULL,
          prompt_summary TEXT NOT NULL,
          estimate_id TEXT NOT NULL,
          selected_route TEXT NOT NULL,
          routing_reason TEXT NOT NULL,
          subagents_allowed INTEGER NOT NULL,
          subagents_used INTEGER NOT NULL,
          direct_work_exception INTEGER NOT NULL,
          verifier_required INTEGER NOT NULL,
          validation_plan TEXT NOT NULL,
          branch TEXT NOT NULL,
          git_status_digest TEXT NOT NULL,
          final_status TEXT NOT NULL,
          validation_result TEXT NOT NULL,
          stop_status TEXT NOT NULL,
          receipt_paths_json TEXT NOT NULL,
          unresolved_risks_json TEXT NOT NULL
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


def row_bool(row: Mapping[str, Any], key: str) -> bool:
    return bool(int(row.get(key) or 0))


def row_to_manager_decision(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row)
    for key in (
        "hook_configured",
        "hook_verified",
        "hook_override",
        "local_enabled",
        "local_usable",
        "cloud_usable",
        "prompt_known",
        "subagents_allowed",
        "subagents_used",
        "direct_work_exception",
        "verifier_required",
    ):
        data[key] = row_bool(data, key)
    for key in ("receipt_paths", "unresolved_risks"):
        raw = data.pop(f"{key}_json", "[]")
        try:
            parsed = json.loads(raw) if raw else []
        except json.JSONDecodeError:
            parsed = []
        data[key] = parsed if isinstance(parsed, list) else []
    return data


def latest_manager_decision(conn: sqlite3.Connection, ledger_id: str = "", session_id: str = "") -> dict[str, Any] | None:
    if ledger_id:
        row = conn.execute("SELECT * FROM qwendex_manager_decisions WHERE ledger_id = ?", (ledger_id,)).fetchone()
        return row_to_manager_decision(row)
    if session_id:
        row = conn.execute(
            "SELECT * FROM qwendex_manager_decisions WHERE session_id = ? ORDER BY timestamp_updated DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        return row_to_manager_decision(row)
    row = conn.execute("SELECT * FROM qwendex_manager_decisions ORDER BY timestamp_updated DESC LIMIT 1").fetchone()
    return row_to_manager_decision(row)


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


def row_to_file_lock(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def artifact_for_kind(artifacts: list[Any], suffix: str) -> str:
    for item in artifacts:
        text = str(item or "")
        if text.endswith(suffix):
            return text
    return ""


def agent_outcomes_for_sessions(sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    outcomes: list[dict[str, Any]] = []
    for session in sessions:
        artifacts = list(session.get("artifacts", []))
        outcomes.append({
            "agent_id": session.get("agent_id", ""),
            "lane": session.get("lane", ""),
            "task_id": session.get("task_id", ""),
            "status": session.get("status", ""),
            "validation_status": session.get("validation_status", ""),
            "required": session_is_required(session),
            "raw_output_artifact": artifact_for_kind(artifacts, "/raw-output.md"),
            "compact_report_artifact": artifact_for_kind(artifacts, "/compact-report.json"),
            "aggregate_raw_output_artifact": artifact_for_kind(artifacts, "/raw-agent-output.md"),
            "artifacts": artifacts,
        })
    return outcomes


def normalize_lock_path(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    path = Path(raw).expanduser()
    if path.is_absolute():
        try:
            return rel(path.resolve())
        except ValueError:
            return str(path.resolve())
    return str(Path(raw).as_posix()).lstrip("./")


def event_file_paths(event: Mapping[str, Any]) -> list[str]:
    values: list[Any] = []
    for key in ("path", "file", "file_path", "target_path"):
        if event.get(key):
            values.append(event[key])
    for key in ("paths", "files", "files_changed"):
        item = event.get(key)
        if isinstance(item, list):
            values.extend(item)
        elif item:
            values.append(item)
    tool_input = event.get("tool_input")
    if isinstance(tool_input, Mapping):
        for key in ("path", "file", "file_path", "target_path"):
            if tool_input.get(key):
                values.append(tool_input[key])
        for key in ("paths", "files", "files_changed"):
            item = tool_input.get(key)
            if isinstance(item, list):
                values.extend(item)
            elif item:
                values.append(item)
    normalized: list[str] = []
    for item in values:
        if isinstance(item, Mapping):
            item = item.get("path") or item.get("file") or item.get("file_path") or ""
        path = normalize_lock_path(str(item))
        if path and path not in normalized:
            normalized.append(path)
    return normalized


def active_file_locks(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM qwendex_agent_file_locks WHERE released_at = '' ORDER BY acquired_at, path"
    ).fetchall()
    return [lock for row in rows if (lock := row_to_file_lock(row))]


def release_agent_locks(conn: sqlite3.Connection, agent_id: str, *, now: str) -> list[dict[str, Any]]:
    if not agent_id:
        return []
    rows = conn.execute(
        "SELECT * FROM qwendex_agent_file_locks WHERE agent_id = ? AND released_at = ''",
        (agent_id,),
    ).fetchall()
    conn.execute(
        "UPDATE qwendex_agent_file_locks SET released_at = ? WHERE agent_id = ? AND released_at = ''",
        (now, agent_id),
    )
    return [lock for row in rows if (lock := row_to_file_lock(row))]


def scribe_path_allowed(path: str) -> bool:
    normalized = normalize_lock_path(path)
    return normalized.startswith(".qwendex/runs/")


def safe_artifact_component(value: str, fallback: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip()).strip(".-")
    return (text[:96] or fallback).strip(".-") or fallback


def final_report_sections(message: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current = ""
    for line in (message or "").splitlines():
        match = re.match(r"^\s*([A-Za-z][A-Za-z0-9_ -]{0,48})\s*:\s*(.*)$", line)
        if match:
            current = match.group(1).strip().lower().replace("-", "_").replace(" ", "_")
            sections.setdefault(current, [])
            if match.group(2).strip():
                sections[current].append(match.group(2).strip())
            continue
        if current:
            sections.setdefault(current, []).append(line.rstrip())
    return {key: "\n".join(value).strip() for key, value in sections.items()}


def compact_agent_report(
    *,
    agent_id: str,
    session: Mapping[str, Any],
    message: str,
    final_status: Mapping[str, Any],
    now: str,
) -> dict[str, Any]:
    sections = final_report_sections(message)
    task_name = sections.get("task_name") or str(session.get("stop_condition") or session.get("lane") or "")
    summary = sections.get("summary")
    if not summary:
        lines = [line.strip() for line in message.splitlines() if line.strip() and line.strip() != "FINAL_REPORT"]
        summary = "\n".join(lines[:8])
    return {
        "schema_version": "qwendex.agent_report.v1",
        "agent_id": agent_id,
        "lane": session.get("lane", ""),
        "task_id": session.get("task_id", ""),
        "task_name": task_name,
        "status": final_status.get("status", ""),
        "validation_status": final_status.get("validation_status", ""),
        "summary": summary[:2000],
        "files_inspected": sections.get("files_inspected", ""),
        "files_changed": sections.get("files_changed", ""),
        "commands_run": sections.get("commands_run", ""),
        "evidence": sections.get("evidence", ""),
        "blockers": sections.get("blockers", ""),
        "remaining_risk": sections.get("remaining_risk", ""),
        "next_recommended_action": sections.get("next_recommended_action", ""),
        "created_at": now,
    }


def write_agent_output_artifacts(
    *,
    event: Mapping[str, Any],
    session: Mapping[str, Any],
    agent_id: str,
    message: str,
    report_message: str,
    final_status: Mapping[str, Any],
    now: str,
) -> dict[str, Any]:
    run_id = safe_artifact_component(
        str(event.get("run_id") or event.get("session_id") or session.get("task_id") or "session"),
        "session",
    )
    safe_agent_id = safe_artifact_component(agent_id, "agent")
    run_dir = ROOT / ".qwendex" / "runs" / run_id
    agent_dir = run_dir / safe_agent_id
    agent_dir.mkdir(parents=True, exist_ok=True)
    raw_path = agent_dir / "raw-output.md"
    compact_path = agent_dir / "compact-report.json"
    aggregate_path = run_dir / "raw-agent-output.md"
    raw_path.write_text(message, encoding="utf-8")
    compact = compact_agent_report(
        agent_id=agent_id,
        session=session,
        message=report_message,
        final_status=final_status,
        now=now,
    )
    compact["raw_output_artifact"] = rel(raw_path)
    compact["compact_report_artifact"] = rel(compact_path)
    compact["aggregate_raw_output_artifact"] = rel(aggregate_path)
    compact_path.write_text(json_dumps(compact) + "\n", encoding="utf-8")
    existing = aggregate_path.read_text(encoding="utf-8") if aggregate_path.exists() else "# Raw Agent Outputs\n"
    entry = (
        f"\n## {agent_id} - {session.get('lane', '')} - {now}\n\n"
        f"Raw output: {rel(raw_path)}\n\n"
        f"Compact report: {rel(compact_path)}\n"
    )
    aggregate_path.write_text(existing.rstrip() + entry + "\n", encoding="utf-8")
    artifacts = [rel(raw_path), rel(compact_path), rel(aggregate_path)]
    return {"artifacts": artifacts, "compact_report": compact}


def acquire_file_locks(
    conn: sqlite3.Connection,
    *,
    agent_id: str,
    paths: list[str],
    lock_type: str,
    now: str,
    reason: str,
) -> dict[str, Any]:
    normalized_paths = [path for path in (normalize_lock_path(item) for item in paths) if path]
    active = active_file_locks(conn)
    conflicts: list[dict[str, Any]] = []
    if lock_type == "write":
        for lock in active:
            if lock.get("agent_id") == agent_id:
                continue
            same_path = lock.get("path") in normalized_paths
            other_writer = lock.get("lock_type") == "write"
            if same_path or other_writer:
                conflicts.append(lock)
    if conflicts:
        return {"acquired": [], "conflicts": conflicts, "active_locks": active}
    acquired: list[dict[str, Any]] = []
    for path in normalized_paths:
        existing = conn.execute(
            """
            SELECT * FROM qwendex_agent_file_locks
            WHERE agent_id = ? AND path = ? AND lock_type = ? AND released_at = ''
            """,
            (agent_id, path, lock_type),
        ).fetchone()
        if existing is not None:
            lock = row_to_file_lock(existing)
            if lock:
                acquired.append(lock)
            continue
        lock_id = make_id("lock")
        conn.execute(
            """
            INSERT INTO qwendex_agent_file_locks
            (lock_id, agent_id, path, lock_type, acquired_at, released_at, reason)
            VALUES (?, ?, ?, ?, ?, '', ?)
            """,
            (lock_id, agent_id, path, lock_type, now, reason),
        )
        row = conn.execute("SELECT * FROM qwendex_agent_file_locks WHERE lock_id = ?", (lock_id,)).fetchone()
        lock = row_to_file_lock(row)
        if lock:
            acquired.append(lock)
    return {"acquired": acquired, "conflicts": [], "active_locks": active_file_locks(conn)}


def file_lock_summary(config: Mapping[str, Any]) -> dict[str, Any]:
    try:
        with connect_state(config) as conn:
            active = active_file_locks(conn)
    except sqlite3.Error as exc:
        return {
            "strategy": "single_writer",
            "status": "error",
            "active_count": 0,
            "active": [],
            "error": str(exc),
        }
    active_writers = [lock for lock in active if lock.get("lock_type") == "write"]
    return {
        "strategy": "single_writer",
        "status": "locked" if active_writers else "ready",
        "active_count": len(active),
        "active_writer_count": len(active_writers),
        "active": active,
        "active_writers": active_writers,
    }


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


def selected_manager_mode_for_policy(config: Mapping[str, Any], explicit: str = "") -> str:
    try:
        with connect_state(config) as conn:
            return current_manager_mode(config, conn, explicit=explicit)
    except (OSError, sqlite3.Error, ValueError):
        return normalize_manager_mode(explicit) or normalize_manager_mode(config.get("orchestration", {}).get("mode")) or "auto"


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


def manager_session_is_stale(session: Mapping[str, Any], *, stale_after_minutes: int) -> bool:
    return stale_age_seconds(session) >= stale_after_minutes * 60


def manager_session_is_read_only(session: Mapping[str, Any]) -> bool:
    return str(session.get("write_surface") or "").strip().lower() in {"read-only", "readonly"}


def reconcile_stale_manager_sessions(
    conn: sqlite3.Connection,
    *,
    stale_after_minutes: int,
    now: str,
) -> dict[str, Any]:
    rows = conn.execute("SELECT * FROM qwendex_agent_sessions WHERE status = 'active'").fetchall()
    closed: list[dict[str, Any]] = []
    skipped_writers: list[dict[str, Any]] = []
    for row in rows:
        session = row_to_agent_session(row)
        if not session or not manager_session_is_stale(session, stale_after_minutes=stale_after_minutes):
            continue
        if not manager_session_is_read_only(session):
            skipped_writers.append(session)
            continue
        close_receipt = make_id("close")
        conn.execute(
            "UPDATE qwendex_agent_sessions SET status = 'closed', updated_at = ?, stop_reason = 'stale', close_receipt = ? WHERE agent_id = ?",
            (now, close_receipt, session["agent_id"]),
        )
        release_agent_locks(conn, str(session["agent_id"]), now=now)
        updated = conn.execute("SELECT * FROM qwendex_agent_sessions WHERE agent_id = ?", (session["agent_id"],)).fetchone()
        closed.append(row_to_agent_session(updated) or {})
    conn.commit()
    return {
        "closed_count": len(closed),
        "closed": closed,
        "skipped_writer_count": len(skipped_writers),
        "skipped_writers": skipped_writers,
        "stale_after_minutes": max(stale_after_minutes, 5),
    }


def summarize_agent_sessions(
    sessions: list[dict[str, Any]],
    *,
    stale_after_minutes: int,
) -> dict[str, Any]:
    active_all = [session for session in sessions if session.get("status") == "active"]
    stale = [
        session
        for session in active_all
        if manager_session_is_stale(session, stale_after_minutes=stale_after_minutes)
    ]
    stale_ids = {session.get("agent_id") for session in stale}
    active = [session for session in active_all if session.get("agent_id") not in stale_ids]
    stale_writers = [session for session in stale if not manager_session_is_read_only(session)]
    open_sessions = [session for session in sessions if str(session.get("status") or "") not in AGENT_TERMINAL_STATUSES]
    receipts = [
        session.get("context_packet", {}).get("receipt_path", "")
        for session in open_sessions
        if session.get("context_packet", {}).get("receipt_path")
    ]
    files_touched = sorted({
        path
        for session in open_sessions
        for path in session.get("context_packet", {}).get("exact_files", [])
    })
    blockers = [
        session.get("stop_reason", "")
        for session in open_sessions
        if session.get("stop_reason") and session.get("status") != "closed"
    ]
    blockers.extend(f"stale writer lane: {session.get('lane') or session.get('agent_id')}" for session in stale_writers)
    validation_counts = {"pending": 0, "pass": 0, "fail": 0}
    for session in open_sessions:
        status = str(session.get("validation_status") or "pending")
        validation_counts[status] = validation_counts.get(status, 0) + 1
    return {
        "active_subagents": {"count": len(active), "agents": active},
        "stale_sessions": {"count": len(stale), "agents": stale},
        "stale_writer_sessions": {"count": len(stale_writers), "agents": stale_writers},
        "agent_outcomes": agent_outcomes_for_sessions(sessions),
        "subagent_state": {
            "context_used": sum(int(session.get("context_packet", {}).get("context_budget") or 0) for session in open_sessions),
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


def classify_manager_validation_sessions(
    sessions: list[dict[str, Any]],
    *,
    stale_after_minutes: int,
) -> dict[str, Any]:
    buckets: dict[str, list[dict[str, Any]]] = {
        "validated": [],
        "closed_without_validation_evidence": [],
        "stale_pending_validation": [],
        "orphaned_session": [],
        "needs_manual_review": [],
    }
    for session in sessions:
        item = {
            "agent_id": session.get("agent_id"),
            "lane": session.get("lane"),
            "task_id": session.get("task_id"),
            "status": session.get("status"),
            "validation_status": session.get("validation_status") or "pending",
            "updated_at": session.get("updated_at"),
        }
        validation = str(session.get("validation_status") or "pending")
        status = str(session.get("status") or "")
        has_evidence = bool(session.get("artifacts") or session.get("context_packet", {}).get("receipt_path"))
        if validation == "pass":
            buckets["validated"].append(item)
        elif not session.get("task_id"):
            buckets["orphaned_session"].append(item)
        elif status in AGENT_TERMINAL_STATUSES and validation == "pending":
            if has_evidence:
                buckets["needs_manual_review"].append(item)
            else:
                buckets["closed_without_validation_evidence"].append(item)
        elif manager_session_is_stale(session, stale_after_minutes=stale_after_minutes) and validation == "pending":
            buckets["stale_pending_validation"].append(item)
        elif validation == "pending":
            buckets["needs_manual_review"].append(item)
    counts = {key: len(value) for key, value in buckets.items()}
    return {
        "classifications": buckets,
        "counts": counts,
        "pending_validation_count": (
            counts["closed_without_validation_evidence"]
            + counts["stale_pending_validation"]
            + counts["orphaned_session"]
            + counts["needs_manual_review"]
        ),
        "repair_policy": "classification only; Qwendex does not mark stale sessions validated without evidence",
    }


def manager_deploy_policy(config: Mapping[str, Any]) -> str:
    raw = str(config.get("orchestration", {}).get("manager_deploy_policy", "auto")).strip().lower()
    return raw if raw in MANAGER_DEPLOY_POLICIES else "auto"


def manager_deployment_contract(mode: str, policy: str, active_count: int) -> dict[str, Any]:
    required = normalize_manager_mode(mode) == "manager" and policy == "auto"
    healthy = not required or active_count > 0
    status = "ready" if required and healthy else "standby" if required else "ready"
    return {
        "policy": policy,
        "required": required,
        "active_count": active_count,
        "healthy": healthy,
        "status": status,
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


def normalize_health_mode(value: str | None) -> str:
    return "strict" if str(value or "").strip().lower() == "strict" else "advisory"


def manager_health_summary(
    config: Mapping[str, Any],
    sessions: list[dict[str, Any]],
    *,
    mode: str,
    stale_after_minutes: int,
    health_mode: str = "advisory",
) -> dict[str, Any]:
    summary = summarize_agent_sessions(sessions, stale_after_minutes=stale_after_minutes)
    contract = manager_deployment_contract(
        normalize_manager_mode(mode),
        manager_deploy_policy(config),
        int(summary["active_subagents"]["count"]),
    )
    issues: list[str] = []
    warnings: list[str] = []
    validation_debt = classify_manager_validation_sessions(sessions, stale_after_minutes=stale_after_minutes)
    if summary["stale_writer_sessions"]["count"]:
        ids = ", ".join(str(session.get("agent_id")) for session in summary["stale_writer_sessions"]["agents"])
        message = f"stale manager writer sessions require integration or explicit stop: {ids}"
        if normalize_health_mode(health_mode) == "strict":
            issues.append(message)
        else:
            warnings.append(message)
    if validation_debt["pending_validation_count"]:
        warnings.append(
            f"{validation_debt['pending_validation_count']} manager sessions have pending or missing validation evidence; run scripts/qwendex manager reconcile --pending-validation --json."
        )
    if contract["status"] == "standby":
        message = contract["summary"]
        if normalize_health_mode(health_mode) == "strict":
            issues.append(message)
        else:
            warnings.append(message)
    if contract["status"] == "blocked":
        issues.append(contract["summary"])
    if issues:
        status = "blocked"
    elif warnings:
        status = "warning" if summary["stale_writer_sessions"]["count"] else "standby"
    else:
        status = contract["status"]
    return {
        "status": status,
        "health_mode": normalize_health_mode(health_mode),
        "issues": issues,
        "warnings": warnings,
        "validation_debt": validation_debt,
        "deployment_contract": contract,
        "repair_command": "scripts/qwendex manager repair --safe --json",
    }


def manager_health_issues(config: Mapping[str, Any], sessions: list[dict[str, Any]], *, mode: str, stale_after_minutes: int) -> list[str]:
    health = manager_health_summary(config, sessions, mode=mode, stale_after_minutes=stale_after_minutes, health_mode="strict")
    issues = list(health["issues"])
    contract = health["deployment_contract"]
    if contract["status"] not in {"ready"} and contract["summary"] not in issues:
        issues.append(contract["summary"])
    return issues


def manager_session_safe_repairable(session: Mapping[str, Any]) -> bool:
    context_packet = session.get("context_packet", {})
    return (
        not manager_session_is_read_only(session)
        and not session.get("artifacts")
        and not session.get("close_receipt")
        and not context_packet.get("receipt_path")
        and not context_packet.get("exact_files")
        and str(session.get("validation_status") or "pending") == "pending"
    )


def repair_manager_sessions(
    conn: sqlite3.Connection,
    *,
    stale_after_minutes: int,
    now: str,
    safe: bool,
) -> dict[str, Any]:
    rows = conn.execute("SELECT * FROM qwendex_agent_sessions WHERE status = 'active'").fetchall()
    closed_read_only: list[dict[str, Any]] = []
    closed_writers: list[dict[str, Any]] = []
    manual_close: list[dict[str, Any]] = []
    for row in rows:
        session = row_to_agent_session(row)
        if not session or not manager_session_is_stale(session, stale_after_minutes=stale_after_minutes):
            continue
        if manager_session_is_read_only(session):
            reason = "stale"
        elif safe and manager_session_safe_repairable(session):
            reason = "safe_stale_repair"
        else:
            manual_close.append({
                "agent_id": session.get("agent_id"),
                "lane": session.get("lane"),
                "write_surface": session.get("write_surface"),
                "command": f"scripts/qwendex manager close --agent-id {session.get('agent_id')} --reason operator_reviewed_stale --json",
            })
            continue
        close_receipt = make_id("close")
        conn.execute(
            "UPDATE qwendex_agent_sessions SET status = 'closed', updated_at = ?, stop_reason = ?, close_receipt = ? WHERE agent_id = ?",
            (now, reason, close_receipt, session["agent_id"]),
        )
        release_agent_locks(conn, str(session["agent_id"]), now=now)
        updated = conn.execute("SELECT * FROM qwendex_agent_sessions WHERE agent_id = ?", (session["agent_id"],)).fetchone()
        closed = row_to_agent_session(updated) or {}
        if reason == "safe_stale_repair":
            closed_writers.append(closed)
        else:
            closed_read_only.append(closed)
    conn.commit()
    return {
        "safe": safe,
        "closed_count": len(closed_read_only) + len(closed_writers),
        "closed_read_only_count": len(closed_read_only),
        "closed_writer_count": len(closed_writers),
        "manual_close_count": len(manual_close),
        "closed_read_only": closed_read_only,
        "closed_writers": closed_writers,
        "manual_close": manual_close,
        "stale_after_minutes": max(stale_after_minutes, 5),
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
    health_mode: str = "advisory",
    agent_policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    profile = manager_mode_profile(config, mode)
    resolved_agent_policy = dict(agent_policy or resolve_agent_policy(config, selected_manager_mode=profile["mode"]))
    summary = summarize_agent_sessions(sessions or [], stale_after_minutes=stale_after_minutes)
    data = {
        "mode": profile["mode"],
        "label": profile["label"],
        "agent_use": resolved_agent_policy["agent_use"],
        "agent_policy": resolved_agent_policy,
        "agent_policy_hash": resolved_agent_policy["policy_hash"],
        "agent_policy_source": resolved_agent_policy["source"],
        "agent_policy_warnings": list(resolved_agent_policy.get("warnings", [])),
        "write_safety": file_lock_summary(config),
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
    health = manager_health_summary(
        config,
        sessions or [],
        mode=profile["mode"],
        stale_after_minutes=stale_after_minutes,
        health_mode=health_mode,
    )
    data["manager_health"] = health
    if data["stale_writer_sessions"]["count"] and normalize_health_mode(health_mode) == "strict":
        data["deployment_contract"] = {
            **data["deployment_contract"],
            "healthy": False,
            "status": "blocked",
            "summary": "Stale manager writer sessions require integration or explicit stop.",
        }
    data["manager_estimate"] = manager_self_estimate(
        config,
        mode=profile["mode"],
        local_status=local_status,
        stale_pressure="high" if data["stale_sessions"]["count"] else "none",
    )
    return data


def manager_status_surface_text(label: str, local_state: str, kaveman_enabled: bool) -> str:
    return (
        f"{{Qwendex}} Agent Manager: [{label}] | Kaveman: [{'Y' if kaveman_enabled else 'N'}] "
        f"| Local: [{local_state_label(local_state)}] (Alt+M/K/L)"
    )


def status_file_state_db(payload: Any) -> str:
    if not isinstance(payload, Mapping):
        return ""
    direct = payload.get("state_db")
    if isinstance(direct, str):
        return direct
    nested = payload.get("data")
    if isinstance(nested, Mapping) and isinstance(nested.get("state_db"), str):
        return nested["state_db"]
    return ""


def codex_status_file_diagnostics(config: Mapping[str, Any], path: Path | None = None) -> dict[str, Any]:
    raw = str(path or os.environ.get(QWENDEX_CODEX_STATUS_FILE_ENV, "")).strip()
    active_state_db = str(state_db_path(config))
    data: dict[str, Any] = {
        "state_db": active_state_db,
        "status_file": raw,
        "status_file_exists": False,
        "status_file_state_db": "",
        "status_file_state_mismatch": False,
        "warnings": [],
        "next_actions": [],
    }
    if not raw:
        return data
    status_path = Path(raw).expanduser()
    data["status_file"] = str(status_path)
    if not status_path.exists():
        return data
    data["status_file_exists"] = True
    try:
        payload = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data["warnings"].append(f"Codex status file is unreadable: {status_path}")
        data["next_actions"].append(f"scripts/qwendex codex-status --write {shlex.quote(str(status_path))} --json")
        return data
    status_state_db = status_file_state_db(payload)
    data["status_file_state_db"] = status_state_db
    if status_state_db and status_state_db != active_state_db:
        data["status_file_state_mismatch"] = True
        data["warnings"].append(
            f"Codex status file was written from a different Qwendex state DB: {status_state_db}"
        )
        data["next_actions"].append(f"scripts/qwendex codex-status --write {shlex.quote(str(status_path))} --json")
    return data


def codex_status_payload(config: Mapping[str, Any], *, write_path: Path | None = None) -> dict[str, Any]:
    status_file_diagnostics = codex_status_file_diagnostics(config, write_path)
    with connect_state(config) as conn:
        selected_mode = current_manager_mode(config, conn)
        mode = selected_mode
        agent_policy = resolve_agent_policy(config, selected_manager_mode=selected_mode)
        if agent_policy["source"] not in {"default", "manager-mode"}:
            mode = str(agent_policy["mode"])
        stale_after = mode_stale_after_minutes(config, mode)
        reconcile_stale_manager_sessions(conn, stale_after_minutes=stale_after, now=utc_now())
        local_enabled = current_local_enabled(config, conn)
        kaveman_enabled = current_kaveman_enabled(config, conn)
        local_status = local_subagent_status(config, enabled=local_enabled, env=os.environ, probe=True)
    requested_override, requested_override_reason = manager_hook_override(os.environ)
    base_hook_status = hook_status_for_codex_home(
        codex_home_from_env(os.environ),
        required_for_write=True,
    )
    hook_override = requested_override and not bool(base_hook_status.get("verified"))
    hook_status = dict(base_hook_status)
    hook_status["override"] = hook_override
    hook_status["override_reason"] = requested_override_reason if hook_override else None
    manager_preflight_required = selected_mode == "manager" or str(agent_policy.get("mode") or "") == "manager"
    profile = manager_mode_profile(config, mode)
    text = manager_status_surface_text(
        profile["label"],
        str(local_status.get("local_state") or "unknown"),
        kaveman_enabled,
    )
    data = {
        "text": text,
        "mode": profile["mode"],
        "label": profile["label"],
        "selected_manager_mode": selected_mode,
        "manager_preflight_required": manager_preflight_required,
        "agent_use": agent_policy["agent_use"],
        "agent_policy_hash": agent_policy["policy_hash"],
        "agent_policy_source": agent_policy["source"],
        "kaveman": "Y" if kaveman_enabled else "N",
        "kaveman_enabled": kaveman_enabled,
        "kaveman_directive": kaveman_directive(config) if kaveman_enabled else "",
        "local": "Y" if local_status.get("enabled") else "N",
        "local_enabled": bool(local_status.get("enabled")),
        "local_available": local_status.get("available"),
        "local_usable": bool(local_status.get("usable")),
        "local_state": local_status.get("local_state"),
        "hook_status": hook_status,
        "hook_source_count": hook_status["hook_source_count"],
        "state_db": str(state_db_path(config)),
        "status_file_env": QWENDEX_CODEX_STATUS_FILE_ENV,
        "status_file_diagnostics": status_file_diagnostics,
        "warnings": list(status_file_diagnostics["warnings"]),
    }
    if manager_preflight_required and not hook_status["verified"] and not hook_status["override"]:
        data["warnings"].append("Manager Mode CODEX_HOME has no verified Qwendex Codex hooks installed.")
        data.setdefault("next_actions", []).extend([hook_status["install_command"], hook_status["verify_command"]])
    if status_file_diagnostics["next_actions"]:
        data.setdefault("next_actions", []).extend(list(status_file_diagnostics["next_actions"]))
    if write_path is not None:
        target = write_path.expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        file_data = dict(data)
        file_data["status_file"] = str(target)
        file_data["status_file_diagnostics"] = {
            "state_db": str(state_db_path(config)),
            "status_file": str(target),
            "status_file_exists": True,
            "status_file_state_db": str(state_db_path(config)),
            "status_file_state_mismatch": False,
            "warnings": [],
            "next_actions": [],
        }
        file_data["warnings"] = [
            warning
            for warning in data.get("warnings", [])
            if warning not in set(status_file_diagnostics["warnings"])
        ]
        diagnostic_actions = set(status_file_diagnostics["next_actions"])
        file_next_actions = [
            action
            for action in data.get("next_actions", [])
            if action not in diagnostic_actions
        ]
        if file_next_actions:
            file_data["next_actions"] = file_next_actions
        else:
            file_data.pop("next_actions", None)
        target.write_text(json.dumps(file_data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
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
    if version not in CODEX_PATCH_MANIFESTS:
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
            StatusSurfacePreviewItem::QwendexManager => "{Qwendex} Agent Manager: [Manager Mode] | Kaveman: [N] | Local: [Ready] (Alt+M/K/L)",
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
        {
            "path": "codex-rs/tui/src/terminal_visualization_instructions.rs",
            "replacements": [
                (
                    """pub(crate) fn with_terminal_visualization_instructions(
    config: &Config,
    control_instructions: Option<String>,
) -> Option<String> {
    if !config
        .features
        .enabled(Feature::TerminalVisualizationInstructions)
    {
        return control_instructions;
    }

    let existing_instructions =
        control_instructions.or_else(|| config.developer_instructions.clone());
    Some(match existing_instructions.as_deref() {
        Some(existing) if !existing.trim().is_empty() => {
            format!("{existing}\\n\\n{TERMINAL_VISUALIZATION_INSTRUCTIONS}")
        }
        _ => TERMINAL_VISUALIZATION_INSTRUCTIONS.to_string(),
    })
}
""",
                    f"""{marker}
fn qwendex_kaveman_directive() -> Option<String> {{
    let status_file = std::env::var("QWENDEX_CODEX_STATUS_FILE").ok()?;
    let raw = std::fs::read_to_string(status_file).ok()?;
    let value = serde_json::from_str::<serde_json::Value>(&raw).ok()?;
    if !value
        .get("kaveman_enabled")
        .and_then(|enabled| enabled.as_bool())
        .unwrap_or(false)
    {{
        return None;
    }}
    value
        .get("kaveman_directive")
        .and_then(|directive| directive.as_str())
        .map(str::trim)
        .filter(|directive| !directive.is_empty())
        .map(|directive| format!("Qwendex Kaveman directive: {{directive}}"))
}}

pub(crate) fn with_terminal_visualization_instructions(
    config: &Config,
    control_instructions: Option<String>,
) -> Option<String> {{
    let mut blocks = Vec::new();
    if let Some(existing) = control_instructions.or_else(|| config.developer_instructions.clone()) {{
        if !existing.trim().is_empty() {{
            blocks.push(existing);
        }}
    }}
    if config
        .features
        .enabled(Feature::TerminalVisualizationInstructions)
    {{
        blocks.push(TERMINAL_VISUALIZATION_INSTRUCTIONS.to_string());
    }}
    if let Some(directive) = qwendex_kaveman_directive() {{
        blocks.push(directive);
    }}
    (!blocks.is_empty()).then(|| blocks.join("\\n\\n"))
}}
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
    health_mode = normalize_health_mode(getattr(args, "health_mode", "advisory"))
    surface = required_surface_check()
    artifacts = [path for path in REQUIRED_SURFACE_FILES if (ROOT / path).exists()]
    status = "pass" if surface["status"] == "pass" else "fail"
    manager_issues: list[str] = []
    manager_warnings: list[str] = []
    manager_health: dict[str, Any] = {}
    with connect_state(config) as conn:
        mode = current_manager_mode(config, conn)
        agent_policy = resolve_agent_policy(config, cli_agent_use=getattr(args, "agent_use", ""), selected_manager_mode=mode)
        local_status = local_subagent_status(config, enabled=current_local_enabled(config, conn), env=os.environ, probe=False)
        stale_after = mode_stale_after_minutes(config, mode)
        reconcile_stale_manager_sessions(conn, stale_after_minutes=stale_after, now=utc_now())
        rows = conn.execute("SELECT * FROM qwendex_agent_sessions ORDER BY updated_at DESC").fetchall()
        sessions = [session for row in rows if (session := row_to_agent_session(row))]
        manager_health = manager_health_summary(
            config,
            sessions,
            mode=mode,
            stale_after_minutes=stale_after,
            health_mode=health_mode,
        )
        manager_issues = list(manager_health["issues"])
        manager_warnings = list(manager_health["warnings"])
    if manager_issues:
        status = "fail"
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
        summary=(
            "Qwendex surface is ready."
            if status == "pass" and not manager_warnings
            else "Qwendex surface is ready with advisory warnings."
            if status == "pass"
            else "Qwendex surface is incomplete."
        ),
        artifacts=artifacts,
        next_actions=(
            [manager_health["repair_command"]]
            if manager_warnings
            else []
            if status == "pass"
            else ["Run scripts/qwendex doctor --health-mode strict --json"]
        ),
        errors=[*surface["missing"], *manager_issues],
        data={
            "manager_health_mode": health_mode,
            "health_mode": health_mode,
            "warnings": manager_warnings,
            "agent_policy": agent_policy,
            "agent_policy_hash": agent_policy["policy_hash"],
            "surface": surface,
            "manager_health_issues": [*manager_issues, *manager_warnings],
            "manager_health": manager_health,
            "default_seat": config["default_seat"],
            "routing": routing_policy(config),
            "manager_estimate": manager_estimate,
            "high_value_add": high_value_add_lines(local_status, release_risk="medium"),
        },
    )


def command_doctor(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    health_mode = normalize_health_mode(getattr(args, "health_mode", "advisory"))
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
    manager_issues: list[str] = []
    manager_warnings: list[str] = []
    manager_health: dict[str, Any] = {}
    with connect_state(config) as conn:
        mode = current_manager_mode(config, conn)
        agent_policy = resolve_agent_policy(config, cli_agent_use=getattr(args, "agent_use", ""), selected_manager_mode=mode)
        local_status = local_subagent_status(config, enabled=current_local_enabled(config, conn), env=os.environ, probe=False)
        stale_after = mode_stale_after_minutes(config, mode)
        reconcile_stale_manager_sessions(conn, stale_after_minutes=stale_after, now=utc_now())
        rows = conn.execute("SELECT * FROM qwendex_agent_sessions ORDER BY updated_at DESC").fetchall()
        sessions = [session for row in rows if (session := row_to_agent_session(row))]
        manager_health = manager_health_summary(
            config,
            sessions,
            mode=mode,
            stale_after_minutes=stale_after,
            health_mode=health_mode,
        )
        manager_issues = list(manager_health["issues"])
        manager_warnings = list(manager_health["warnings"])
    critical.extend(manager_issues)
    status = "pass" if not critical else "fail"
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
        summary=(
            "Qwendex doctor found no critical issues."
            if status == "pass" and not manager_warnings
            else "Qwendex doctor found advisory warnings."
            if status == "pass"
            else "Qwendex doctor found critical issues."
        ),
        artifacts=artifacts,
        next_actions=(
            [manager_health["repair_command"]]
            if manager_warnings
            else ["Run scripts/qwendex eval --json"]
            if status == "pass"
            else ["Repair listed critical issues."]
        ),
        errors=critical,
        data={
            "manager_health_mode": health_mode,
            "health_mode": health_mode,
            "warnings": manager_warnings,
            "agent_policy": agent_policy,
            "agent_policy_hash": agent_policy["policy_hash"],
            "critical_issues": critical,
            "manager_health_issues": [*manager_issues, *manager_warnings],
            "manager_health": manager_health,
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
    agent_policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    estimate = estimate_task(config, prompt=prompt.strip(), local_status=local_status)
    profile = manager_mode_profile(config, mode)
    resolved_agent_policy = dict(agent_policy or resolve_agent_policy(config, selected_manager_mode=profile["mode"]))
    data = {
        "mode": profile["mode"],
        "label": profile["label"],
        "agent_use": resolved_agent_policy["agent_use"],
        "agent_policy": resolved_agent_policy,
        "agent_policy_hash": resolved_agent_policy["policy_hash"],
        "agent_policy_source": resolved_agent_policy["source"],
        "agent_policy_warnings": list(resolved_agent_policy.get("warnings", [])),
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
        agent_policy = resolve_agent_policy(config, cli_agent_use=getattr(args, "agent_use", ""), selected_manager_mode=mode)
        if agent_policy["errors"]:
            return stable_envelope(command="estimate", status="blocked", summary="Invalid Qwendex agent policy.", errors=list(agent_policy["errors"]), data={"agent_policy": agent_policy})
        mode = policy_mode_for_manager(args, config, mode)
        local_status = local_subagent_status(config, enabled=current_local_enabled(config, conn), env=os.environ, probe=True)
    return manager_estimate_envelope(
        config,
        command_name="estimate",
        prompt=args.prompt,
        mode=mode,
        local_status=local_status,
        agent_policy=agent_policy,
    )


def parse_timeout_ms(value: str, default_ms: int) -> int:
    text = str(value or "").strip().lower()
    if not text:
        return default_ms
    try:
        if text.endswith("ms"):
            return max(0, int(float(text[:-2])))
        if text.endswith("s"):
            return max(0, int(float(text[:-1]) * 1000))
        return max(0, int(float(text) * 1000))
    except ValueError:
        return default_ms


def read_hook_event(args: argparse.Namespace) -> dict[str, Any]:
    raw = str(getattr(args, "event_json", "") or "").strip()
    if raw:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("hook event JSON must be an object")
        return data
    if not sys.stdin.isatty():
        try:
            data = json.load(sys.stdin)
        except json.JSONDecodeError as exc:
            raise ValueError(f"hook event JSON is invalid: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("hook event JSON must be an object")
        return data
    return {}


def agent_mode_context(agent_policy: Mapping[str, Any]) -> str:
    mode = str(agent_policy.get("mode") or "medium")
    contracts = {
        "off": "Off mode: do not spawn subagents unless explicitly requested; keep work in the main session.",
        "auto": "Auto mode: use the task estimate to decide whether bounded specialist lanes are useful.",
        "lite": "Lite mode: prefer direct work. Do not spawn subagents unless explicitly requested or required by policy.",
        "medium": "Medium mode: use a small number of specialists when exploration or verification materially improves quality.",
        "heavy": "Heavy mode: use scoped specialist lanes for non-trivial repo work. Verifier evidence is required for meaningful edits.",
        "manager": (
            "Manager Mode: you are the root orchestrator and context curator. "
            "For non-trivial repo work, maintain an agent ledger and use scoped specialists. "
            "Do not finalize until required agents have FINAL_REPORT, BLOCKED, FAILED, or TOMBSTONED status with evidence. "
            "Small trivial tasks may be handled directly with a recorded direct-work exception."
        ),
    }
    return f"Active Qwendex agent mode: {agent_policy.get('agent_use')}. {contracts.get(mode, contracts['medium'])}"


def subagent_start_context(event: Mapping[str, Any], agent_policy: Mapping[str, Any]) -> str:
    agent_id = str(event.get("agent_id") or "unknown")
    agent_type = str(event.get("agent_type") or event.get("profile") or "unknown")
    task_name = str(event.get("task_name") or event.get("task") or "assigned task")
    return (
        f"You are Qwendex subagent {agent_id} of type {agent_type}. "
        f"Parent mode is {agent_policy.get('agent_use')}. Execute {task_name} now. "
        "Do not merely acknowledge or stand by. Do not spawn subagents. "
        "End with FINAL_REPORT, BLOCKED, or FAILED. Required FINAL_REPORT fields: "
        "status, agent_id, task_name, summary, files_inspected, files_changed, "
        "commands_run, evidence, artifacts, blockers, remaining_risk, next_recommended_action."
    )


def parse_worker_final_status(message: str) -> dict[str, Any]:
    text = message or ""
    if re.search(r"\bBLOCKED\b", text):
        return {"has_contract": True, "status": "blocked", "validation_status": "fail", "reason": "blocked_contract"}
    if re.search(r"\bFAILED\b", text):
        return {"has_contract": True, "status": "failed", "validation_status": "fail", "reason": "failed_contract"}
    if not re.search(r"\bFINAL_REPORT\b", text):
        return {"has_contract": False, "status": "", "validation_status": "pending", "reason": "missing_final_contract"}
    status_match = re.search(r"(?im)^\s*status\s*:\s*([a-z_-]+)", text)
    reported = status_match.group(1).strip().lower() if status_match else "completed"
    if reported in {"blocked", "block"}:
        status = "blocked"
        validation = "fail"
    elif reported in {"failed", "fail", "failure"}:
        status = "failed"
        validation = "fail"
    else:
        status = "completed"
        validation = "pass"
    return {"has_contract": True, "status": status, "validation_status": validation, "reason": "final_report"}


def update_agent_from_final_contract(
    conn: sqlite3.Connection,
    *,
    agent_id: str,
    final_status: Mapping[str, Any],
    now: str,
    artifacts: list[str] | None = None,
) -> dict[str, Any] | None:
    if not agent_id or not final_status.get("has_contract"):
        return None
    row = conn.execute("SELECT * FROM qwendex_agent_sessions WHERE agent_id = ?", (agent_id,)).fetchone()
    if row is None:
        return None
    stored_artifacts = json_loads_list(row["artifacts_json"])
    for artifact in artifacts or []:
        if artifact and artifact not in stored_artifacts:
            stored_artifacts.append(artifact)
    conn.execute(
        """
        UPDATE qwendex_agent_sessions
        SET status = ?, validation_status = ?, heartbeat_at = ?, updated_at = ?, stop_reason = ?, artifacts_json = ?
        WHERE agent_id = ?
        """,
        (
            str(final_status.get("status") or "completed"),
            str(final_status.get("validation_status") or "pending"),
            now,
            now,
            str(final_status.get("reason") or "final_report"),
            json_dumps(stored_artifacts),
            agent_id,
        ),
    )
    release_agent_locks(conn, agent_id, now=now)
    conn.commit()
    updated = conn.execute("SELECT * FROM qwendex_agent_sessions WHERE agent_id = ?", (agent_id,)).fetchone()
    return row_to_agent_session(updated)


def session_is_required(session: Mapping[str, Any]) -> bool:
    packet = session.get("context_packet", {})
    if isinstance(packet, Mapping) and "required" in packet:
        return bool(packet.get("required"))
    return True


def verifier_passed(sessions: list[dict[str, Any]]) -> bool:
    for session in sessions:
        lane_text = " ".join([
            str(session.get("lane") or ""),
            str(session.get("context_packet", {}).get("task_class") or ""),
            str(session.get("owner") or ""),
        ]).lower()
        if "verif" in lane_text or "review" in lane_text:
            if str(session.get("status") or "") in AGENT_TERMINAL_STATUSES and str(session.get("validation_status") or "") != "fail":
                return True
    return False


def final_mentions_agent_outcomes(message: str) -> bool:
    return bool(re.search(r"(?i)\b(agent outcomes|agent ledger|agents?|validation|risks?)\b", message or ""))


def event_command_text(event: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for key in ("command", "cmd", "shell_command"):
        if isinstance(event.get(key), str):
            parts.append(str(event[key]))
    tool_input = event.get("tool_input")
    if isinstance(tool_input, Mapping):
        for key in ("command", "cmd"):
            if isinstance(tool_input.get(key), str):
                parts.append(str(tool_input[key]))
    return "\n".join(parts)


def event_tool_name(event: Mapping[str, Any]) -> str:
    for key in ("tool_name", "tool", "name"):
        if isinstance(event.get(key), str) and event.get(key):
            return str(event[key]).strip()
    return ""


def event_profile(event: Mapping[str, Any]) -> str:
    for key in ("profile", "agent_type", "role", "lane"):
        if isinstance(event.get(key), str) and event.get(key):
            return str(event[key]).strip().lower()
    return ""


def event_agent_id(event: Mapping[str, Any]) -> str:
    for key in ("agent_id", "owner_agent_id", "session_agent_id"):
        if isinstance(event.get(key), str) and event.get(key):
            return str(event[key]).strip()
    return ""


def command_has_shell_redirection(command: str) -> bool:
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return bool(re.search(r"(^|[\s;&|])(?:\d?>{1,2}|&>)\s*\S", command))
    for token in tokens:
        if token in {">", ">>", "&>", "&>>"}:
            return True
        if re.match(r"^(?:\d?>{1,2}|&>{1,2})(?:$|\S)", token):
            return True
    return False


def event_is_write_attempt(tool_lower: str, command: str) -> bool:
    if tool_lower in WRITE_TOOL_NAMES:
        return True
    return bool(
        command_has_shell_redirection(command)
        or re.search(r"\btee\s+", command)
        or re.search(r"\bapply_patch\b", command)
        or re.search(r"\bsed\s+-i\b", command)
        or re.search(r"\bpython3?\s+.*\bwrite_text\b", command)
    )


def pre_tool_gate(config: Mapping[str, Any], event: Mapping[str, Any], agent_policy: Mapping[str, Any]) -> dict[str, Any]:
    tool = event_tool_name(event)
    tool_lower = tool.lower()
    depth = int(event.get("depth") or event.get("spawn_depth") or 0)
    profile = event_profile(event)
    agent_id = event_agent_id(event)
    command = event_command_text(event)
    write_attempt = event_is_write_attempt(tool_lower, command)
    if depth > 0 and tool_lower in ROOT_ONLY_AGENT_TOOLS:
        return {
            "decision": "block",
            "event": "agent.spawn_rejected",
            "reason": f"Child agents cannot use root-only management tool {tool}.",
        }
    if profile in READ_ONLY_AGENT_PROFILES and write_attempt:
        return {
            "decision": "block",
            "event": "agent.write_rejected",
            "reason": f"Read-only profile {profile} cannot write files.",
        }
    if RELEASE_COMMAND_RE.search(command):
        approved = bool(event.get("release_approved")) or env_flag(os.environ.get("QWENDEX_RELEASE_APPROVED")) is True
        if not approved:
            return {
                "decision": "block",
                "event": "agent.release_command_rejected",
                "reason": "Release/publish commands require an explicit release gate approval.",
            }
    if tool_lower == "spawn_agent" and agent_policy.get("mode") in {"off", "lite"} and not event.get("explicit_user_request"):
        return {
            "decision": "block",
            "event": "agent.spawn_rejected",
            "reason": f"{agent_policy.get('agent_use')} mode disables subagents unless the user explicitly requested one.",
        }
    if write_attempt:
        paths = event_file_paths(event)
        if not agent_id:
            return {
                "decision": "block",
                "event": "agent.write_lock_rejected",
                "reason": "Write attempts must include agent_id so Qwendex can record file ownership.",
            }
        if not paths:
            return {
                "decision": "block",
                "event": "agent.write_lock_rejected",
                "reason": "Write attempts must include at least one target file path for Qwendex file-lock tracking.",
            }
        if profile == "scribe":
            denied = [path for path in paths if not scribe_path_allowed(path)]
            if denied:
                return {
                    "decision": "block",
                    "event": "agent.write_rejected",
                    "reason": "Scribe can write only under .qwendex/runs.",
                    "denied_paths": denied,
                }
        with connect_state(config) as conn:
            lock_result = acquire_file_locks(
                conn,
                agent_id=agent_id,
                paths=paths,
                lock_type="write",
                now=utc_now(),
                reason=f"{tool or 'tool'} PreToolUse",
            )
            if lock_result["conflicts"]:
                return {
                    "decision": "block",
                    "event": "agent.file_lock_conflict",
                    "reason": "First-release Manager Mode uses a single writer in the base worktree.",
                    **lock_result,
                }
            conn.commit()
        return {
            "event": "agent.file_locks_acquired",
            "agent_id": agent_id,
            **lock_result,
        }
    return {}


def evaluate_agent_hook(
    config: Mapping[str, Any],
    *,
    event_name: str,
    event: Mapping[str, Any],
    agent_policy: Mapping[str, Any],
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    canonical = event_name or str(event.get("hookEventName") or event.get("event") or "")
    if canonical not in AGENT_HOOK_EVENTS:
        return "blocked", {"decision": "block", "reason": f"Unsupported Qwendex agent hook event: {canonical}"}, {}
    if event.get("stop_hook_active"):
        return "pass", {"continue": True}, {}
    if canonical in {"SessionStart", "UserPromptSubmit"}:
        return "pass", {
            "hookSpecificOutput": {
                "hookEventName": canonical,
                "additionalContext": agent_mode_context(agent_policy),
            }
        }, {}
    if canonical == "SubagentStart":
        return "pass", {
            "hookSpecificOutput": {
                "hookEventName": "SubagentStart",
                "additionalContext": subagent_start_context(event, agent_policy),
            }
        }, {}
    if canonical == "SubagentStop":
        final_message = str(event.get("last_assistant_message") or event.get("message") or event.get("raw_output") or "")
        raw_message = str(event.get("raw_output") or event.get("transcript") or final_message)
        final_status = parse_worker_final_status(final_message)
        if not final_status["has_contract"]:
            return "blocked", {
                "decision": "block",
                "event": "agent.final_contract_missing",
                "reason": "Subagent response must end with FINAL_REPORT, BLOCKED, or FAILED with evidence.",
            }, {"final_status": final_status}
        updated: dict[str, Any] | None = None
        capture: dict[str, Any] = {}
        agent_id = str(event.get("agent_id") or "")
        if agent_id:
            with connect_state(config) as conn:
                row = conn.execute("SELECT * FROM qwendex_agent_sessions WHERE agent_id = ?", (agent_id,)).fetchone()
                session = row_to_agent_session(row) or {}
                now = utc_now()
                try:
                    capture = write_agent_output_artifacts(
                        event=event,
                        session=session,
                        agent_id=agent_id,
                        message=raw_message,
                        report_message=final_message,
                        final_status=final_status,
                        now=now,
                    )
                except OSError as exc:
                    return "blocked", {
                        "decision": "block",
                        "event": "agent.output_capture_failed",
                        "reason": f"Failed to preserve raw agent output: {exc}",
                    }, {"final_status": final_status}
                updated = update_agent_from_final_contract(
                    conn,
                    agent_id=agent_id,
                    final_status=final_status,
                    now=now,
                    artifacts=list(capture.get("artifacts", [])),
                )
        return "pass", {
            "event": f"agent.{final_status['status']}",
            "status": final_status["status"],
            "agent_id": agent_id,
            "artifacts": list(capture.get("artifacts", [])),
        }, {"final_status": final_status, "agent_session": updated, **capture}
    if canonical == "Stop":
        ledger_id, session_id = manager_decision_identity(event)
        selected_mode = selected_manager_mode_for_policy(config)
        manager_enforced = (
            str(agent_policy.get("mode")) == "manager"
            or selected_mode == "manager"
            or bool(ledger_id or session_id)
        )
        if not manager_enforced:
            return "pass", {}, {}
        if str(agent_policy.get("mode")) != "manager":
            agent_policy = resolve_agent_policy(config, env={}, selected_manager_mode="manager")
        with connect_state(config) as conn:
            decision = latest_manager_decision(conn, ledger_id=ledger_id, session_id=session_id) if (ledger_id or session_id) else latest_manager_decision(conn)
            if not (ledger_id or session_id) and not manager_decision_attachable(decision, agent_policy, env=os.environ):
                decision = None
            rows = conn.execute("SELECT * FROM qwendex_agent_sessions ORDER BY updated_at DESC").fetchall()
        sessions = [session for row in rows if (session := row_to_agent_session(row))]
        if decision is None:
            return "blocked", {
                "decision": "block",
                "event": "manager.unattached",
                "reason": "Manager Mode stop requires a preflight manager_decision ledger record.",
                "stop_status": "STOP_MANAGER_UNATTACHED",
            }, {"agent_sessions": sessions}
        selected_route = str(decision.get("selected_route") or "")
        if selected_route == "blocked" or decision.get("stop_status") == "STOP_MANAGER_BLOCKED_UNHOOKED":
            return "blocked", {
                "decision": "block",
                "event": "manager.blocked_unhooked",
                "reason": "Manager Mode launch was blocked before Codex attachment.",
                "stop_status": "STOP_MANAGER_BLOCKED_UNHOOKED",
            }, {"manager_decision": decision, "agent_sessions": sessions}
        last_message = str(event.get("last_assistant_message") or "")
        edit_happened = bool(event.get("edit_happened") or event.get("files_changed"))
        if selected_route == "direct_single_writer":
            hook_verified = bool(decision.get("hook_verified"))
            hook_override = bool(decision.get("hook_override"))
            if not hook_verified and not hook_override:
                return "blocked", {
                    "decision": "block",
                    "event": "manager.blocked_unhooked",
                    "reason": "Direct Manager Mode work requires verified hooks or an explicit hook override.",
                    "stop_status": "STOP_MANAGER_BLOCKED_UNHOOKED",
                }, {"manager_decision": decision}
            if edit_happened and bool(decision.get("verifier_required")) and not stop_event_has_validation_evidence(event, last_message):
                with connect_state(config) as conn:
                    updated_decision = update_manager_decision_terminal(
                        conn,
                        decision,
                        config=config,
                        final_status="validation_pending",
                        validation_result="missing_validation_evidence",
                        stop_status="STOP_MANAGER_VALIDATION_PENDING",
                    )
                return "blocked", {
                    "decision": "block",
                    "event": "manager.validation_pending",
                    "reason": "Direct Manager Mode edits require validation evidence before finalization.",
                    "stop_status": "STOP_MANAGER_VALIDATION_PENDING",
                }, {"manager_decision": updated_decision or decision}
            if edit_happened and not stop_event_has_dirty_classification(event, last_message):
                with connect_state(config) as conn:
                    updated_decision = update_manager_decision_terminal(
                        conn,
                        decision,
                        config=config,
                        final_status="validation_pending",
                        validation_result="missing_dirty_worktree_classification",
                        stop_status="STOP_MANAGER_VALIDATION_PENDING",
                    )
                return "blocked", {
                    "decision": "block",
                    "event": "manager.validation_pending",
                    "reason": "Direct Manager Mode closeout must include dirty worktree classification.",
                    "stop_status": "STOP_MANAGER_VALIDATION_PENDING",
                }, {"manager_decision": updated_decision or decision}
            if edit_happened and not final_mentions_agent_outcomes(last_message):
                return "blocked", {
                    "decision": "block",
                    "event": "manager.final_contract_missing",
                    "reason": "Final Manager Mode response must include validation and unresolved risks.",
                    "stop_status": "STOP_MANAGER_VALIDATION_PENDING",
                }, {"manager_decision": decision}
            with connect_state(config) as conn:
                updated_decision = update_manager_decision_terminal(
                    conn,
                    decision,
                    config=config,
                    final_status="closed",
                    validation_result="pass" if stop_event_has_validation_evidence(event, last_message) or not edit_happened else "not_required",
                    stop_status="STOP_MANAGER_CLOSED",
                    unresolved_risks=[],
                )
            return "pass", {
                "event": "manager.finalized",
                "stop_status": "STOP_MANAGER_CLOSED",
                "ledger_id": decision.get("ledger_id"),
            }, {"manager_decision": updated_decision or decision, "agent_sessions": sessions}
        incomplete = [
            session
            for session in sessions
            if session_is_required(session) and str(session.get("status") or "") not in AGENT_TERMINAL_STATUSES
        ]
        if incomplete:
            names = ", ".join(f"{item.get('agent_id')}:{item.get('status')}" for item in incomplete[:5])
            return "blocked", {
                "decision": "block",
                "event": "manager.stop_gate_continued",
                "reason": f"Manager Mode ledger has incomplete required agents: {names}.",
            }, {"incomplete_required_agents": incomplete, "manager_decision": decision}
        if edit_happened and agent_policy.get("require_verifier_for_edits") and not verifier_passed(sessions):
            with connect_state(config) as conn:
                updated_decision = update_manager_decision_terminal(
                    conn,
                    decision,
                    config=config,
                    final_status="validation_pending",
                    validation_result="missing_verifier_evidence",
                    stop_status="STOP_MANAGER_VALIDATION_PENDING",
                )
            return "blocked", {
                "decision": "block",
                "event": "manager.verifier_required",
                "reason": "Verifier evidence is required for this Manager Mode edit.",
                "stop_status": "STOP_MANAGER_VALIDATION_PENDING",
            }, {"agent_sessions": sessions, "manager_decision": updated_decision or decision}
        if sessions and not final_mentions_agent_outcomes(last_message):
            return "blocked", {
                "decision": "block",
                "event": "manager.final_contract_missing",
                "reason": "Final Manager Mode response must include agent outcomes, validation, and unresolved risks.",
                "stop_status": "STOP_MANAGER_VALIDATION_PENDING",
            }, {"agent_sessions": sessions, "manager_decision": decision}
        if selected_route == "manager_subagents" and not sessions:
            with connect_state(config) as conn:
                updated_decision = update_manager_decision_terminal(
                    conn,
                    decision,
                    config=config,
                    final_status="validation_pending",
                    validation_result="missing_subagent_evidence",
                    stop_status="STOP_MANAGER_VALIDATION_PENDING",
                )
            return "blocked", {
                "decision": "block",
                "event": "manager.validation_pending",
                "reason": "Manager subagent route requires bounded agent evidence and parent review.",
                "stop_status": "STOP_MANAGER_VALIDATION_PENDING",
            }, {"manager_decision": updated_decision or decision}
        with connect_state(config) as conn:
            updated_decision = update_manager_decision_terminal(
                conn,
                decision,
                config=config,
                final_status="closed",
                validation_result="pass",
                stop_status="STOP_MANAGER_CLOSED",
                unresolved_risks=[],
            )
        return "pass", {"event": "manager.finalized", "stop_status": "STOP_MANAGER_CLOSED", "ledger_id": decision.get("ledger_id")}, {"agent_sessions": sessions, "manager_decision": updated_decision or decision}
    if canonical == "PreToolUse":
        result = pre_tool_gate(config, event, agent_policy)
        return ("blocked" if result.get("decision") == "block" else "pass"), result, {}
    if canonical in {"PostToolUse", "PreCompact", "PostCompact"}:
        return "pass", {"event": f"agent.{canonical}", "status": "recorded"}, {}
    return "pass", {}, {}


def codex_hook_output(event_name: str, hook_result: Mapping[str, Any]) -> dict[str, Any]:
    """Return only fields accepted by Codex's hook stdout schema."""
    event = event_name or str(hook_result.get("hookEventName") or "")
    output: dict[str, Any] = {}
    for key in ("continue", "stopReason", "suppressOutput", "systemMessage"):
        if key in hook_result:
            output[key] = hook_result[key]

    hook_specific = hook_result.get("hookSpecificOutput")
    hook_specific_allowed = {
        "SessionStart": {"hookEventName", "additionalContext"},
        "SubagentStart": {"hookEventName", "additionalContext"},
        "UserPromptSubmit": {"hookEventName", "additionalContext"},
        "PreToolUse": {
            "hookEventName",
            "permissionDecision",
            "permissionDecisionReason",
            "updatedInput",
            "additionalContext",
        },
        "PostToolUse": {"hookEventName", "additionalContext", "updatedMCPToolOutput"},
    }
    if isinstance(hook_specific, Mapping) and event in hook_specific_allowed:
        filtered = {
            key: hook_specific[key]
            for key in hook_specific_allowed[event]
            if key in hook_specific
        }
        if filtered:
            output["hookSpecificOutput"] = filtered

    if hook_result.get("decision") == "block":
        reason = str(hook_result.get("reason") or "Qwendex hook blocked this action.").strip()
        if event in {"PreToolUse", "PostToolUse", "UserPromptSubmit", "Stop", "SubagentStop"}:
            output["decision"] = "block"
            output["reason"] = reason
        else:
            output["continue"] = False
            output["stopReason"] = reason
    return output


def managed_hook_runtime_env(
    env: Mapping[str, str] | None = None,
    *,
    codex_home: Path | None = None,
) -> dict[str, str]:
    source = env or os.environ
    codex_home_text = str(codex_home.expanduser()) if codex_home is not None else str(source.get("CODEX_HOME") or "")
    dev_paths = qwendex_dev_paths_from_codex_home({**dict(source), "CODEX_HOME": codex_home_text})
    work_root = Path(codex_home_text).expanduser().parent if codex_home_text else None
    values: dict[str, str] = {}
    if codex_home_text:
        values["CODEX_HOME"] = codex_home_text
    state_db = str(source.get("QWENDEX_STATE_DB") or dev_paths.get("state_db") or "").strip()
    if state_db:
        values["QWENDEX_STATE_DB"] = state_db
    results_root = str(source.get("QWENDEX_RESULTS_ROOT") or dev_paths.get("results_root") or "").strip()
    if results_root:
        values["QWENDEX_RESULTS_ROOT"] = results_root
    ledger_db = str(source.get("QWENDEX_LEDGER_DB") or dev_paths.get("ledger_db") or "").strip()
    if ledger_db:
        values["QWENDEX_LEDGER_DB"] = ledger_db
    status_file = str(source.get("QWENDEX_CODEX_STATUS_FILE") or "").strip()
    if not status_file and work_root is not None and work_root.name == ".qwendex-dev":
        status_file = str(work_root / "codex_status.json")
    if status_file:
        values["QWENDEX_CODEX_STATUS_FILE"] = status_file
    dev_root = str(source.get("QWENDEX_DEV_ROOT") or "").strip()
    if not dev_root and work_root is not None and work_root.name == ".qwendex-dev":
        dev_root = str(work_root.parent)
    if dev_root:
        values["QWENDEX_DEV_ROOT"] = dev_root
    values["QWENDEX_ROOT"] = str(source.get("QWENDEX_ROOT") or ROOT)
    return {key: values[key] for key in MANAGED_HOOK_RUNTIME_ENV_KEYS if values.get(key)}


def shell_env_prefix(runtime_env: Mapping[str, str]) -> str:
    if not runtime_env:
        return ""
    return "env " + " ".join(f"{key}={shlex.quote(str(value))}" for key, value in runtime_env.items())


def managed_agent_hook_config(command_base: str = "", runtime_env: Mapping[str, str] | None = None) -> dict[str, Any]:
    base = str(command_base or ROOT / "scripts" / "qwendex").strip()
    prefix = shell_env_prefix(runtime_env or {})
    command_base_text = f"{prefix} {shlex.quote(base)}" if prefix else shlex.quote(base)
    hooks: dict[str, list[dict[str, Any]]] = {}
    for event_name, spec in MANAGED_AGENT_HOOKS.items():
        hooks[event_name] = [{
            "matcher": spec["matcher"],
            "hooks": [{
                "type": "command",
                "command": f"{command_base_text} agent hook {event_name} --codex-hook-output",
                "timeout": spec["timeout"],
            }],
        }]
    return {"hooks": hooks}


def write_managed_hook_config(path: Path, payload: Mapping[str, Any], *, force: bool) -> Path:
    target = path.expanduser()
    if target.is_dir():
        target = target / "hooks.json"
    if not target.is_absolute():
        target = ROOT / target
    if target.exists() and not force:
        raise FileExistsError(f"hook config already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json_dumps(payload) + "\n", encoding="utf-8")
    return target


def hook_config_path_for_codex_home(codex_home: Path) -> Path:
    return codex_home.expanduser() / "hooks.json"


def managed_hook_commands(payload: Mapping[str, Any]) -> dict[str, list[str]]:
    hooks = payload.get("hooks")
    commands: dict[str, list[str]] = {}
    if not isinstance(hooks, Mapping):
        return commands
    for event_name, entries in hooks.items():
        event_commands: list[str] = []
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            for hook in entry.get("hooks") or []:
                if isinstance(hook, Mapping) and isinstance(hook.get("command"), str):
                    event_commands.append(str(hook["command"]))
        commands[str(event_name)] = event_commands
    return commands


def is_qwendex_agent_hook_command(command: str) -> bool:
    return "qwendex" in command and "agent hook" in command


def is_codex_compatible_agent_hook_command(command: str) -> bool:
    return is_qwendex_agent_hook_command(command) and "--codex-hook-output" in command


def managed_hook_command_env(command: str) -> dict[str, str]:
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return {}
    if not tokens or tokens[0] != "env":
        return {}
    runtime_env: dict[str, str] = {}
    for token in tokens[1:]:
        if "=" not in token or token.startswith("-"):
            break
        key, value = token.split("=", 1)
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
            break
        runtime_env[key] = value
    return runtime_env


def hook_status_for_codex_home(
    codex_home: Path,
    *,
    required_for_write: bool = True,
    override: bool = False,
    override_reason: str = "",
) -> dict[str, Any]:
    target = hook_config_path_for_codex_home(codex_home)
    payload: dict[str, Any] = {}
    parse_error = ""
    if target.is_file():
        try:
            loaded = json.loads(target.read_text(encoding="utf-8"))
            payload = loaded if isinstance(loaded, dict) else {}
        except (OSError, json.JSONDecodeError) as exc:
            parse_error = str(exc)
    commands = managed_hook_commands(payload)
    managed_events = {
        event
        for event, event_commands in commands.items()
        if any(is_codex_compatible_agent_hook_command(command) for command in event_commands)
    }
    required_events = set(MANAGED_AGENT_HOOKS)
    missing_events = sorted(required_events - managed_events)
    incompatible_events = sorted({
        event
        for event, event_commands in commands.items()
        if any(
            is_qwendex_agent_hook_command(command)
            and not is_codex_compatible_agent_hook_command(command)
            for command in event_commands
        )
    })
    hook_source_count = sum(
        1
        for event_commands in commands.values()
        for command in event_commands
        if is_qwendex_agent_hook_command(command)
    )
    compatible_hook_source_count = sum(
        1
        for event_commands in commands.values()
        for command in event_commands
        if is_codex_compatible_agent_hook_command(command)
    )
    runtime_env_by_event = {
        event: [managed_hook_command_env(command) for command in event_commands if is_codex_compatible_agent_hook_command(command)]
        for event, event_commands in commands.items()
    }
    runtime_env_keys_by_event = {
        event: sorted({key for runtime_env in envs for key in runtime_env})
        for event, envs in runtime_env_by_event.items()
        if envs
    }
    runtime_env_state_db_by_event = {
        event: sorted({runtime_env["QWENDEX_STATE_DB"] for runtime_env in envs if runtime_env.get("QWENDEX_STATE_DB")})
        for event, envs in runtime_env_by_event.items()
        if envs
    }
    missing_runtime_env_events = sorted(
        event
        for event in managed_events
        if not any(runtime_env.get("QWENDEX_STATE_DB") for runtime_env in runtime_env_by_event.get(event, []))
    )
    configured = target.is_file() and hook_source_count > 0
    verified = (
        configured
        and compatible_hook_source_count > 0
        and not missing_events
        and not incompatible_events
        and not missing_runtime_env_events
        and not parse_error
    )
    return {
        "codex_home": str(codex_home.expanduser()),
        "hooks_path": str(target),
        "hooks_json_exists": target.is_file(),
        "hook_source_count": hook_source_count,
        "compatible_hook_source_count": compatible_hook_source_count,
        "configured": configured,
        "verified": verified,
        "source_paths": [str(target)] if target.is_file() else [],
        "managed_events": sorted(managed_events),
        "missing_events": missing_events,
        "incompatible_events": incompatible_events,
        "missing_runtime_env_events": missing_runtime_env_events,
        "runtime_env_keys_by_event": runtime_env_keys_by_event,
        "runtime_env_state_db_by_event": runtime_env_state_db_by_event,
        "required_for_write": required_for_write,
        "override": override,
        "override_reason": override_reason or None,
        "parse_error": parse_error,
        "install_command": f'scripts/qwendex agent hook-config --install --codex-home "{codex_home.expanduser()}" --json',
        "verify_command": f'scripts/qwendex agent hook-config --verify --codex-home "{codex_home.expanduser()}" --json',
    }


def manager_hook_override(env: Mapping[str, str] | None = None) -> tuple[bool, str]:
    source = env or os.environ
    override = env_flag(source.get(MANAGER_UNHOOKED_OVERRIDE_ENV)) is True
    if not override:
        return False, ""
    reason = str(source.get(MANAGER_UNHOOKED_REASON_ENV) or "explicit_operator_unhooked_override").strip()
    return True, reason or "explicit_operator_unhooked_override"


def prompt_requests_team(prompt: str) -> bool:
    return bool(re.search(r"(?i)\b(team|squad|manager mode|subagents?|use agents?|spawn agents?|fan out)\b", prompt or ""))


def prompt_is_trivial(prompt: str, task_class: str) -> bool:
    words = re.findall(r"\w+", prompt or "")
    risky = task_class in {"security", "release acceptance", "architecture"}
    work_verbs = re.search(r"(?i)\b(add|change|edit|implement|refactor|test|verify|release|publish|ship|write)\b", prompt or "")
    return len(words) <= 12 and not risky and not work_verbs


def agent_plan_profiles(prompt: str, mode: str, estimate: Mapping[str, Any]) -> tuple[list[str], str]:
    text = prompt.lower()
    task_class = str(estimate.get("task_class") or "bounded patch")
    recommended = str(estimate.get("recommended_mode") or "medium")
    explicit_team = prompt_requests_team(prompt)
    if prompt_is_trivial(prompt, task_class) and not explicit_team:
        return [], "direct_trivial"
    if mode in {"off", "lite"} and not explicit_team:
        return [], f"direct_{mode}_policy"
    profiles: list[str]
    if task_class == "release acceptance" or "publish" in text or "release" in text:
        profiles = ["release_manager", "verifier"]
    elif task_class in {"security", "architecture"}:
        profiles = ["explorer", "verifier"]
        if re.search(r"(?i)\b(add|change|edit|implement|patch|fix)\b", prompt):
            profiles.insert(1, "implementer")
    elif task_class == "docs draft":
        profiles = ["docs_researcher"]
        if re.search(r"(?i)\b(edit|update|write|add)\b", prompt):
            profiles.extend(["implementer", "verifier"])
    elif task_class == "artifact summary":
        profiles = ["explorer"]
    elif recommended in {"heavy", "manager"} or explicit_team:
        profiles = ["explorer", "implementer", "verifier"]
    else:
        profiles = ["implementer", "verifier"]
    if mode == "medium" and not explicit_team:
        profiles = [profile for profile in profiles if profile in {"implementer", "verifier", "docs_researcher", "release_manager"}][:2]
    if mode == "manager" and profiles and "scribe" not in profiles:
        profiles.append("scribe")
    deduped: list[str] = []
    for profile in profiles:
        if profile in DEFAULT_AGENT_PROFILES and profile not in deduped:
            deduped.append(profile)
    return deduped, "team_routing"


def agent_profile_lane(profile: str) -> str:
    return {
        "explorer": "exploration",
        "implementer": "implementation",
        "verifier": "verification",
        "docs_researcher": "docs-research",
        "release_manager": "release-management",
        "scribe": "scribe",
    }.get(profile, profile)


def agent_profile_write_surface(profile: str) -> str:
    profile_data = DEFAULT_AGENT_PROFILES.get(profile, {})
    sandbox = str(profile_data.get("sandbox_mode") or "")
    if sandbox == "read-only":
        return "read-only"
    if profile == "scribe":
        return ".qwendex/runs"
    return "declared-scope"


def build_agent_team_plan(
    config: Mapping[str, Any],
    *,
    prompt: str,
    task_id: str,
    agent_policy: Mapping[str, Any],
    local_status: Mapping[str, Any],
) -> dict[str, Any]:
    estimate = estimate_task(config, prompt=prompt, local_status=local_status)
    mode = str(agent_policy.get("mode") or "medium")
    profiles, reason = agent_plan_profiles(prompt, mode, estimate)
    effective_task_id = task_id or make_id("task")
    assignments: list[dict[str, Any]] = []
    task_slug = safe_artifact_component(effective_task_id, "task")
    for index, profile in enumerate(profiles, start=1):
        lane = agent_profile_lane(profile)
        required = bool(DEFAULT_AGENT_PROFILES.get(profile, {}).get("default_required", True))
        agent_id = safe_artifact_component(f"{task_slug}-{profile}-{index}", f"{profile}-{index}")
        stop_condition = (
            "return FINAL_REPORT with summary, files, commands, evidence, artifacts, blockers, and remaining risk"
            if profile != "scribe"
            else "record run decisions and artifact paths under .qwendex/runs"
        )
        command = [
            "scripts/qwendex",
            "manager",
            "assign",
            "--agent-id",
            agent_id,
            "--lane",
            lane,
            "--task-id",
            effective_task_id,
            "--owner",
            profile,
            "--write-surface",
            agent_profile_write_surface(profile),
            "--stop-condition",
            stop_condition,
        ]
        command.append("--required" if required else "--optional")
        command.append("--json")
        assignments.append({
            "agent_id": agent_id,
            "profile": profile,
            "lane": lane,
            "required": required,
            "write_surface": agent_profile_write_surface(profile),
            "stop_condition": stop_condition,
            "assign_command": " ".join(shlex.quote(part) for part in command),
            "routing": lane_model_reasoning(
                config,
                task_class=str(estimate.get("task_class") or "bounded patch"),
                lane=lane,
                risk=str(estimate.get("risk") or "medium"),
                local_status=local_status,
            ),
        })
    direct_work = not assignments
    return {
        "schema_version": "qwendex.agent_plan.v1",
        "mode": mode,
        "agent_use": agent_policy.get("agent_use"),
        "task_id": effective_task_id,
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "task_class": estimate.get("task_class"),
        "estimate": estimate,
        "routing_reason": reason,
        "direct_work": direct_work,
        "direct_work_exception": (
            f"No agents used because the task is trivial or policy is {agent_policy.get('agent_use')}."
            if direct_work
            else ""
        ),
        "profiles": profiles,
        "assignments": assignments,
        "team": DEFAULT_MANAGER_TEAM,
    }


def persist_manager_decision(conn: sqlite3.Connection, decision: Mapping[str, Any]) -> dict[str, Any] | None:
    routing = decision.get("routing_decision", {})
    hook_status = decision.get("hook_status", {})
    availability = decision.get("agent_availability", {})
    prompt = decision.get("prompt", {})
    branch = str(decision.get("branch") or "")
    git_status_digest = str(decision.get("git_status_digest") or "")
    receipt_paths = list(decision.get("receipt_paths") or [])
    unresolved_risks = list(decision.get("unresolved_risks") or [])
    now = str(decision.get("timestamp") or utc_now())
    created = str(decision.get("timestamp_created") or now)
    ledger_id = str(decision.get("ledger_id") or make_id("mgrldg"))
    conn.execute(
        """
        INSERT INTO qwendex_manager_decisions
        (ledger_id, session_id, record_type, schema_version, timestamp_created, timestamp_updated,
         mode, agent_use, policy_source, policy_hash, codex_home_digest_or_path_policy, codex_home,
         hook_source_count, hook_configured, hook_verified, hook_override, hook_override_reason,
         local_enabled, local_usable, cloud_usable, prompt_known, prompt_digest, prompt_summary,
         estimate_id, selected_route, routing_reason, subagents_allowed, subagents_used,
         direct_work_exception, verifier_required, validation_plan, branch, git_status_digest,
         final_status, validation_result, stop_status, receipt_paths_json, unresolved_risks_json)
        VALUES (?, ?, 'manager_decision', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(ledger_id) DO UPDATE SET
          session_id=excluded.session_id,
          schema_version=excluded.schema_version,
          timestamp_updated=excluded.timestamp_updated,
          mode=excluded.mode,
          agent_use=excluded.agent_use,
          policy_source=excluded.policy_source,
          policy_hash=excluded.policy_hash,
          codex_home_digest_or_path_policy=excluded.codex_home_digest_or_path_policy,
          codex_home=excluded.codex_home,
          hook_source_count=excluded.hook_source_count,
          hook_configured=excluded.hook_configured,
          hook_verified=excluded.hook_verified,
          hook_override=excluded.hook_override,
          hook_override_reason=excluded.hook_override_reason,
          local_enabled=excluded.local_enabled,
          local_usable=excluded.local_usable,
          cloud_usable=excluded.cloud_usable,
          prompt_known=excluded.prompt_known,
          prompt_digest=excluded.prompt_digest,
          prompt_summary=excluded.prompt_summary,
          estimate_id=excluded.estimate_id,
          selected_route=excluded.selected_route,
          routing_reason=excluded.routing_reason,
          subagents_allowed=excluded.subagents_allowed,
          subagents_used=excluded.subagents_used,
          direct_work_exception=excluded.direct_work_exception,
          verifier_required=excluded.verifier_required,
          validation_plan=excluded.validation_plan,
          branch=excluded.branch,
          git_status_digest=excluded.git_status_digest,
          final_status=excluded.final_status,
          validation_result=excluded.validation_result,
          stop_status=excluded.stop_status,
          receipt_paths_json=excluded.receipt_paths_json,
          unresolved_risks_json=excluded.unresolved_risks_json
        """,
        (
            ledger_id,
            str(decision.get("session_id") or ""),
            int(decision.get("schema_version") or 1),
            created,
            now,
            str(decision.get("mode") or ""),
            str(decision.get("agent_use") or ""),
            str(decision.get("policy_source") or ""),
            str(decision.get("policy_hash") or ""),
            str(decision.get("codex_home_digest_or_path_policy") or ""),
            str(decision.get("codex_home") or ""),
            int(hook_status.get("hook_source_count") or 0),
            1 if hook_status.get("configured") else 0,
            1 if hook_status.get("verified") else 0,
            1 if hook_status.get("override") else 0,
            str(hook_status.get("override_reason") or ""),
            1 if availability.get("local_enabled") else 0,
            1 if availability.get("local_usable") else 0,
            1 if availability.get("cloud_usable") else 0,
            1 if prompt.get("known") else 0,
            str(prompt.get("prompt_digest") or ""),
            str(prompt.get("prompt_summary") or ""),
            str(decision.get("manager_estimate", {}).get("estimate_id") or ""),
            str(routing.get("selected_route") or ""),
            str(routing.get("routing_reason") or ""),
            1 if routing.get("subagents_allowed") else 0,
            1 if routing.get("subagents_used") else 0,
            1 if routing.get("direct_work_exception") else 0,
            1 if routing.get("verifier_required") else 0,
            str(routing.get("validation_plan") or ""),
            branch,
            git_status_digest,
            str(decision.get("final_status") or ""),
            str(decision.get("validation_result") or ""),
            str(decision.get("stop_status") or ""),
            json_dumps(receipt_paths),
            json_dumps(unresolved_risks),
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM qwendex_manager_decisions WHERE ledger_id = ?", (ledger_id,)).fetchone()
    return row_to_manager_decision(row)


def manager_preflight_payload(
    config: Mapping[str, Any],
    *,
    prompt: str = "",
    prompt_known: bool = False,
    dry_run: bool = False,
    repo: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    source_env = env or os.environ
    with connect_state(config) as conn:
        mode = current_manager_mode(config, conn)
        agent_policy = resolve_agent_policy(config, selected_manager_mode=mode, env=source_env)
        local_status = local_subagent_status(config, enabled=current_local_enabled(config, conn), env=source_env, probe=True)
    codex_home = codex_home_from_env(source_env)
    requested_override, requested_override_reason = manager_hook_override(source_env)
    base_hook_status = hook_status_for_codex_home(
        codex_home,
        required_for_write=True,
    )
    override = requested_override and not bool(base_hook_status.get("verified"))
    hook_status = dict(base_hook_status)
    hook_status["override"] = override
    hook_status["override_reason"] = requested_override_reason if override else None
    timestamp = utc_now()
    session_id = str(source_env.get("QWENDEX_MANAGER_SESSION_ID") or make_id("mgrsess"))
    ledger_id = str(source_env.get("QWENDEX_MANAGER_LEDGER_ID") or make_id("mgrldg"))
    prompt_digest, prompt_summary = prompt_digest_and_summary(prompt, known=prompt_known)
    estimate_id = ""
    estimate: dict[str, Any] | None = None
    validation_plan = "focused"
    if prompt_known:
        estimate_id = make_id("estimate")
        estimate = estimate_task(config, prompt=prompt, local_status=local_status)
        validation_plan = str(estimate.get("validation_depth") or validation_plan)
    manager_required = mode == "manager" or str(agent_policy.get("mode") or "") == "manager"
    hook_blocked = manager_required and not bool(hook_status["verified"]) and not override
    if hook_blocked:
        selected_route = "blocked"
        routing_reason = "Manager Mode requires Qwendex Codex hooks or explicit unhooked override."
        stop_status = "STOP_MANAGER_BLOCKED_UNHOOKED"
        direct_work_exception = False
        subagents_allowed = False
        final_status = "blocked"
        ok = False
    elif prompt_known:
        plan = build_agent_team_plan(
            config,
            prompt=prompt,
            task_id=session_id,
            agent_policy=agent_policy,
            local_status=local_status,
        )
        if plan["assignments"]:
            selected_route = "manager_subagents"
            routing_reason = "manager plan selected bounded subagent lanes; root must review and close lanes before finalization"
            stop_status = "STOP_MANAGER_SUBAGENTS_READY"
            direct_work_exception = False
            subagents_allowed = True
        else:
            selected_route = "direct_single_writer"
            routing_reason = str(plan.get("direct_work_exception") or plan.get("routing_reason") or "direct work selected by manager plan")
            stop_status = "STOP_MANAGER_DIRECT_READY"
            direct_work_exception = True
            subagents_allowed = False
        final_status = "preflight_ready"
        ok = True
    else:
        selected_route = "direct_single_writer"
        routing_reason = "interactive prompt unknown before Codex launch; hooks/finalization must update record when prompt is available"
        stop_status = "STOP_MANAGER_PREFLIGHT_READY"
        direct_work_exception = True
        subagents_allowed = False
        final_status = "preflight_ready"
        ok = True
    branch, git_digest = git_branch_and_status_digest(repo)
    receipt_path = str(manager_receipt_path(config, ledger_id))
    try:
        receipt_ref = str(Path(receipt_path).relative_to(ROOT))
    except ValueError:
        receipt_ref = receipt_path
    payload: dict[str, Any] = {
        "ok": ok,
        "schema_version": 1,
        "record_type": "manager_decision",
        "session_id": session_id,
        "ledger_id": ledger_id,
        "timestamp": timestamp,
        "timestamp_created": timestamp,
        "timestamp_updated": timestamp,
        "mode": "manager" if manager_required else str(agent_policy.get("mode") or mode),
        "selected_manager_mode": mode,
        "effective_agent_mode": str(agent_policy.get("mode") or ""),
        "agent_use": str(agent_policy.get("agent_use") or ""),
        "policy_source": str(agent_policy.get("source") or ""),
        "policy_hash": str(agent_policy.get("policy_hash") or ""),
        "codex_home": str(codex_home),
        "codex_home_digest_or_path_policy": path_digest_policy(codex_home),
        "hook_status": hook_status,
        "agent_availability": {
            "local_enabled": bool(local_status.get("enabled")),
            "local_usable": bool(local_status.get("usable")),
            "local_endpoint": str(local_status.get("probe", {}).get("url") or routing_policy(config)["local_probe_url"]),
            "cloud_usable": True,
        },
        "prompt": {
            "known": prompt_known,
            "prompt_digest": prompt_digest or None,
            "prompt_summary": prompt_summary,
        },
        "manager_estimate": {
            "created": prompt_known,
            "estimate_id": estimate_id or None,
            "reason": "" if prompt_known else MANAGER_PROMPT_UNKNOWN_SUMMARY,
            "estimate": estimate,
        },
        "routing_decision": {
            "selected_route": selected_route,
            "routing_reason": routing_reason,
            "subagents_allowed": subagents_allowed,
            "subagents_used": False,
            "direct_work_exception": direct_work_exception,
            "verifier_required": bool(agent_policy.get("require_verifier_for_edits")),
            "validation_plan": validation_plan,
        },
        "branch": branch,
        "git_status_digest": git_digest,
        "final_status": final_status,
        "validation_result": "",
        "stop_status": stop_status,
        "receipt_paths": [receipt_ref],
        "unresolved_risks": (
            ["qwendex hooks missing; launch blocked until hooks are installed or override is set"]
            if hook_blocked
            else ["unhooked override used; operator must name this in final report"]
            if override
            else []
        ),
        "dry_run": dry_run,
        "manager_required": manager_required,
        "exports": {
            "QWENDEX_MANAGER_SESSION_ID": session_id,
            "QWENDEX_MANAGER_LEDGER_ID": ledger_id,
            "QWENDEX_MANAGER_POLICY_HASH": str(agent_policy.get("policy_hash") or ""),
            "QWENDEX_MANAGER_STOP_STATUS": stop_status,
        },
    }
    if not dry_run:
        with connect_state(config) as conn:
            persisted = persist_manager_decision(conn, payload)
        payload["decision_ledger"] = persisted
        write_manager_decision_receipt(config, payload)
    return payload


def manager_decision_attachable(
    decision: Mapping[str, Any] | None,
    agent_policy: Mapping[str, Any],
    *,
    env: Mapping[str, str] | None = None,
) -> bool:
    if not decision or str(decision.get("final_status") or "") not in {"preflight_ready", "closed"}:
        return False
    expected_home = path_digest_policy(codex_home_from_env(env))
    actual_home = str(decision.get("codex_home_digest_or_path_policy") or "")
    if actual_home and actual_home != expected_home:
        return False
    expected_policy = str(agent_policy.get("policy_hash") or "")
    actual_policy = str(decision.get("policy_hash") or "")
    if expected_policy and actual_policy and expected_policy != actual_policy:
        return False
    timestamp = str(decision.get("timestamp_updated") or decision.get("timestamp_created") or "")
    try:
        age_seconds = (datetime.now(UTC) - parse_utc(timestamp)).total_seconds()
    except (TypeError, ValueError):
        return False
    return age_seconds <= MANAGER_DECISION_ATTACH_WINDOW_MINUTES * 60


def manager_decision_identity(event: Mapping[str, Any]) -> tuple[str, str]:
    ledger_id = str(
        event.get("manager_ledger_id")
        or event.get("ledger_id")
        or os.environ.get("QWENDEX_MANAGER_LEDGER_ID")
        or ""
    ).strip()
    session_id = str(
        event.get("manager_session_id")
        or event.get("session_id")
        or os.environ.get("QWENDEX_MANAGER_SESSION_ID")
        or ""
    ).strip()
    return ledger_id, session_id


def update_manager_decision_terminal(
    conn: sqlite3.Connection,
    decision: Mapping[str, Any],
    *,
    config: Mapping[str, Any] | None = None,
    final_status: str,
    validation_result: str,
    stop_status: str,
    receipt_paths: list[str] | None = None,
    unresolved_risks: list[str] | None = None,
) -> dict[str, Any] | None:
    paths = list(receipt_paths if receipt_paths is not None else decision.get("receipt_paths") or [])
    risks = list(unresolved_risks if unresolved_risks is not None else decision.get("unresolved_risks") or [])
    conn.execute(
        """
        UPDATE qwendex_manager_decisions
        SET timestamp_updated = ?, final_status = ?, validation_result = ?, stop_status = ?,
            receipt_paths_json = ?, unresolved_risks_json = ?
        WHERE ledger_id = ?
        """,
        (
            utc_now(),
            final_status,
            validation_result,
            stop_status,
            json_dumps(paths),
            json_dumps(risks),
            str(decision.get("ledger_id") or ""),
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM qwendex_manager_decisions WHERE ledger_id = ?", (str(decision.get("ledger_id") or ""),)).fetchone()
    updated = row_to_manager_decision(row)
    if config is not None and updated is not None:
        write_manager_decision_receipt(config, manager_decision_receipt_payload(updated))
    return updated


def manager_decision_receipt_payload(decision: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "ok": str(decision.get("selected_route") or "") != "blocked",
        "schema_version": int(decision.get("schema_version") or 1),
        "record_type": "manager_decision",
        "session_id": decision.get("session_id"),
        "ledger_id": decision.get("ledger_id"),
        "timestamp": decision.get("timestamp_updated"),
        "timestamp_created": decision.get("timestamp_created"),
        "timestamp_updated": decision.get("timestamp_updated"),
        "mode": decision.get("mode"),
        "agent_use": decision.get("agent_use"),
        "policy_source": decision.get("policy_source"),
        "policy_hash": decision.get("policy_hash"),
        "codex_home": decision.get("codex_home"),
        "codex_home_digest_or_path_policy": decision.get("codex_home_digest_or_path_policy"),
        "hook_status": {
            "hook_source_count": decision.get("hook_source_count"),
            "configured": decision.get("hook_configured"),
            "verified": decision.get("hook_verified"),
            "override": decision.get("hook_override"),
            "override_reason": decision.get("hook_override_reason") or None,
            "required_for_write": True,
        },
        "agent_availability": {
            "local_enabled": decision.get("local_enabled"),
            "local_usable": decision.get("local_usable"),
            "cloud_usable": decision.get("cloud_usable"),
        },
        "prompt": {
            "known": decision.get("prompt_known"),
            "prompt_digest": decision.get("prompt_digest") or None,
            "prompt_summary": decision.get("prompt_summary"),
        },
        "manager_estimate": {
            "created": bool(decision.get("estimate_id")),
            "estimate_id": decision.get("estimate_id") or None,
        },
        "routing_decision": {
            "selected_route": decision.get("selected_route"),
            "routing_reason": decision.get("routing_reason"),
            "subagents_allowed": decision.get("subagents_allowed"),
            "subagents_used": decision.get("subagents_used"),
            "direct_work_exception": decision.get("direct_work_exception"),
            "verifier_required": decision.get("verifier_required"),
            "validation_plan": decision.get("validation_plan"),
        },
        "branch": decision.get("branch"),
        "git_status_digest": decision.get("git_status_digest"),
        "final_status": decision.get("final_status"),
        "validation_result": decision.get("validation_result"),
        "stop_status": decision.get("stop_status"),
        "receipt_paths": list(decision.get("receipt_paths") or []),
        "unresolved_risks": list(decision.get("unresolved_risks") or []),
    }


def stop_event_has_validation_evidence(event: Mapping[str, Any], message: str) -> bool:
    if event.get("validation_evidence") or event.get("receipt_paths") or event.get("commands_run"):
        return True
    text = message or ""
    if re.search(r"(?i)\b(pytest|unittest|ruff|py_compile|qwendex-dev verify|scripts/qwendex|receipt)\b", text):
        return True
    negative = re.compile(
        r"(?i)\b(not\s+run|not\s+tested|untested|no\s+validation|none|n/a|na|skipped|missing|todo)\b"
    )
    positive = re.compile(r"(?i)\b(pass(?:ed)?|ok|green|verified|validated|checked|clean)\b")
    for line in text.splitlines():
        match = re.match(r"(?i)^\s*validation\s*:\s*(.+?)\s*$", line)
        if not match:
            continue
        value = match.group(1).strip()
        if not value or negative.search(value):
            continue
        if positive.search(value):
            return True
    return False


def stop_event_has_dirty_classification(event: Mapping[str, Any], message: str) -> bool:
    if event.get("dirty_worktree_classification") or event.get("git_status_digest"):
        return True
    return bool(re.search(r"(?im)^\s*(dirty|git state|worktree)\s*:", message or ""))


def agent_metrics_payload(config: Mapping[str, Any], agent_policy: Mapping[str, Any]) -> dict[str, Any]:
    with connect_state(config) as conn:
        rows = conn.execute("SELECT * FROM qwendex_agent_sessions ORDER BY updated_at DESC").fetchall()
        sessions = [session for row in rows if (session := row_to_agent_session(row))]
        locks = active_file_locks(conn)
    status_counts: dict[str, int] = {}
    validation_counts: dict[str, int] = {}
    required_incomplete = 0
    terminal_count = 0
    terminal_with_final_contract = 0
    for session in sessions:
        status = str(session.get("status") or "unknown")
        validation = str(session.get("validation_status") or "pending")
        status_counts[status] = status_counts.get(status, 0) + 1
        validation_counts[validation] = validation_counts.get(validation, 0) + 1
        if session_is_required(session) and status not in AGENT_TERMINAL_STATUSES:
            required_incomplete += 1
        if status in AGENT_TERMINAL_STATUSES:
            terminal_count += 1
            if session.get("artifacts") or str(session.get("stop_reason") or "") in {"final_report", "blocked_contract", "failed_contract"}:
                terminal_with_final_contract += 1
    active_writers = [lock for lock in locks if lock.get("lock_type") == "write"]
    final_contract_compliance = (
        round(terminal_with_final_contract / terminal_count, 4)
        if terminal_count
        else None
    )
    return {
        "schema_version": "qwendex.agent_metrics.v1",
        "agent_use": agent_policy.get("agent_use"),
        "agent_policy_hash": agent_policy.get("policy_hash"),
        "session_count": len(sessions),
        "active_count": status_counts.get("active", 0),
        "terminal_count": terminal_count,
        "status_counts": status_counts,
        "validation_counts": validation_counts,
        "required_incomplete_count": required_incomplete,
        "final_contract_compliance": final_contract_compliance,
        "active_file_lock_count": len(locks),
        "active_writer_count": len(active_writers),
        "managed_hook_event_count": len(MANAGED_AGENT_HOOKS),
        "built_in_profile_count": len(DEFAULT_AGENT_PROFILES),
        "raw_output_artifact_count": sum(1 for session in sessions for artifact in session.get("artifacts", []) if str(artifact).endswith("/raw-output.md")),
    }


def command_agent(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    agent_policy = resolve_agent_policy(
        config,
        cli_agent_use=getattr(args, "agent_use", ""),
        selected_manager_mode=selected_manager_mode_for_policy(config),
    )
    if agent_policy["errors"]:
        return stable_envelope(command="agent", status="blocked", summary="Invalid Qwendex agent policy.", errors=list(agent_policy["errors"]), data={"agent_policy": agent_policy})
    action = args.action or "status"
    if action == "hook":
        event = read_hook_event(args)
        status, hook_result, extra = evaluate_agent_hook(
            config,
            event_name=args.target or str(event.get("hookEventName") or event.get("event") or ""),
            event=event,
            agent_policy=agent_policy,
        )
        data = {"hook_result": hook_result, "event": event, "agent_policy": agent_policy, **extra}
        if getattr(args, "codex_hook_output", False):
            data["codex_hook_output"] = codex_hook_output(
                args.target or str(event.get("hookEventName") or event.get("event") or ""),
                hook_result,
            )
        return stable_envelope(
            command="agent",
            status=status,
            summary=f"Qwendex agent hook {args.target or event.get('event', '')} returned {status}.",
            errors=[hook_result.get("reason", "")] if status == "blocked" and hook_result.get("reason") else [],
            data=data,
        )
    if action == "policy":
        return stable_envelope(
            command="agent",
            status="pass",
            summary=f"Qwendex AgentPolicy is {agent_policy['agent_use']}.",
            data={"agent_policy": agent_policy},
        )
    if action == "hook-config":
        artifacts: list[str] = []
        codex_home = codex_home_from_env(os.environ)
        if getattr(args, "codex_home", ""):
            codex_home = Path(args.codex_home).expanduser()
        runtime_env = managed_hook_runtime_env(codex_home=codex_home)
        hook_payload = managed_agent_hook_config(command_base=getattr(args, "qwendex_command", ""), runtime_env=runtime_env)
        if getattr(args, "verify", False):
            status = hook_status_for_codex_home(codex_home)
            command_status = "pass" if status["verified"] else "blocked"
            return stable_envelope(
                command="agent",
                status=command_status,
                summary=(
                    "Verified Qwendex managed agent hook config."
                    if status["verified"]
                    else "Qwendex managed agent hooks are missing or incomplete."
                ),
                errors=[] if status["verified"] else [
                    f"missing managed hook events: {', '.join(status['missing_events']) or 'none detected'}",
                    f"missing runtime env events: {', '.join(status.get('missing_runtime_env_events', [])) or 'none detected'}",
                ],
                data={
                    "hook_status": status,
                    "hook_config": hook_payload,
                    "agent_policy": agent_policy,
                    "operator_action": "verify",
                },
            )
        if getattr(args, "install", False):
            try:
                written = write_managed_hook_config(hook_config_path_for_codex_home(codex_home), hook_payload, force=bool(getattr(args, "force", False)))
            except OSError as exc:
                return stable_envelope(
                    command="agent",
                    status="blocked",
                    summary="Managed hook config was not installed.",
                    errors=[str(exc)],
                    data={"hook_config": hook_payload, "agent_policy": agent_policy, "operator_action": "install"},
                )
            artifacts.append(str(written))
            status = hook_status_for_codex_home(codex_home)
            return stable_envelope(
                command="agent",
                status="pass" if status["verified"] else "warning",
                summary="Installed Qwendex managed agent hook config.",
                artifacts=artifacts,
                data={
                    "hook_config": hook_payload,
                    "hook_status": status,
                    "managed_events": sorted(MANAGED_AGENT_HOOKS),
                    "agent_policy": agent_policy,
                    "operator_action": "install",
                },
            )
        if getattr(args, "write", None):
            if not getattr(args, "approve", False):
                return stable_envelope(
                    command="agent",
                    status="blocked",
                    summary="Writing managed hook config requires --approve.",
                    errors=["explicit approval required"],
                    data={"hook_config": hook_payload, "agent_policy": agent_policy},
                )
            try:
                written = write_managed_hook_config(Path(args.write), hook_payload, force=bool(getattr(args, "force", False)))
            except OSError as exc:
                return stable_envelope(
                    command="agent",
                    status="blocked",
                    summary="Managed hook config was not written.",
                    errors=[str(exc)],
                    data={"hook_config": hook_payload, "agent_policy": agent_policy},
                )
            artifacts.append(str(written))
        return stable_envelope(
            command="agent",
            status="pass",
            summary="Rendered Qwendex managed agent hook config.",
            artifacts=artifacts,
            data={
                "hook_config": hook_payload,
                "managed_events": sorted(MANAGED_AGENT_HOOKS),
                "agent_policy": agent_policy,
            },
        )
    if action == "plan":
        prompt = str(getattr(args, "prompt", "") or "").strip()
        if not prompt:
            return stable_envelope(command="agent", status="blocked", summary="Agent plan requires --prompt.", errors=["missing prompt"], data={"agent_policy": agent_policy})
        with connect_state(config) as conn:
            local_status = local_subagent_status(config, enabled=current_local_enabled(config, conn), env=os.environ, probe=False)
        plan = build_agent_team_plan(
            config,
            prompt=prompt,
            task_id=str(getattr(args, "task_id", "") or ""),
            agent_policy=agent_policy,
            local_status=local_status,
        )
        return stable_envelope(
            command="agent",
            status="pass",
            summary="Built Qwendex agent team plan." if not plan["direct_work"] else "Built Qwendex direct-work plan.",
            next_actions=[assignment["assign_command"] for assignment in plan["assignments"]],
            data={"agent_plan": plan, "agent_policy": agent_policy},
        )
    if action == "metrics":
        metrics = agent_metrics_payload(config, agent_policy)
        return stable_envelope(
            command="agent",
            status="pass",
            summary=f"Loaded Qwendex agent metrics for {metrics['session_count']} sessions.",
            data={"agent_metrics": metrics, "agent_policy": agent_policy},
        )
    if action == "profiles":
        return stable_envelope(
            command="agent",
            status="pass",
            summary=f"Loaded {len(DEFAULT_AGENT_PROFILES)} Qwendex built-in agent profiles.",
            data={"profiles": DEFAULT_AGENT_PROFILES, "profile_order": sorted(DEFAULT_AGENT_PROFILES)},
        )
    if action == "team":
        return stable_envelope(
            command="agent",
            status="pass",
            summary="Loaded Qwendex default manager team.",
            data={"team": DEFAULT_MANAGER_TEAM, "profiles": DEFAULT_AGENT_PROFILES},
        )
    if action == "locks":
        locks = file_lock_summary(config)
        return stable_envelope(
            command="agent",
            status="pass" if locks["status"] in {"ready", "locked"} else "blocked",
            summary=f"Qwendex file-lock strategy is {locks['strategy']} with {locks['active_count']} active locks.",
            data={"write_safety": locks, "agent_policy": agent_policy},
        )
    now = utc_now()
    with connect_state(config) as conn:
        mode = current_manager_mode(config, conn)
        mode = policy_mode_for_manager(args, config, mode)
        local_status = local_subagent_status(config, enabled=current_local_enabled(config, conn), env=os.environ, probe=True)
        kaveman_enabled = current_kaveman_enabled(config, conn)
        stale_after = mode_stale_after_minutes(config, mode, args.stale_after_minutes)
        reconciliation = reconcile_stale_manager_sessions(conn, stale_after_minutes=stale_after, now=now)
        target = args.target or args.agent_id
        if action == "close":
            if not target:
                return stable_envelope(command="agent", status="blocked", summary="Agent close requires an agent id or all.", errors=["missing agent_id"])
            reason = args.reason or "operator_closed"
            close_timeout_ms = parse_timeout_ms(args.timeout, int(agent_policy["close_timeout_ms"]))
            if target == "all":
                rows = conn.execute("SELECT * FROM qwendex_agent_sessions WHERE status = 'active'").fetchall()
                ids = [str(row["agent_id"]) for row in rows]
            else:
                row = conn.execute("SELECT * FROM qwendex_agent_sessions WHERE agent_id = ?", (target,)).fetchone()
                if row is None:
                    return stable_envelope(command="agent", status="blocked", summary=f"Agent session not found: {target}", errors=[target])
                ids = [target]
            closed: list[dict[str, Any]] = []
            for agent_id in ids:
                close_receipt = make_id("close")
                conn.execute(
                    "UPDATE qwendex_agent_sessions SET status = 'closed', updated_at = ?, stop_reason = ?, close_receipt = ? WHERE agent_id = ?",
                    (now, reason, close_receipt, agent_id),
                )
                release_agent_locks(conn, agent_id, now=now)
                updated = conn.execute("SELECT * FROM qwendex_agent_sessions WHERE agent_id = ?", (agent_id,)).fetchone()
                session = row_to_agent_session(updated)
                if session:
                    closed.append(session)
            conn.commit()
            return stable_envelope(
                command="agent",
                status="pass",
                summary=f"Closed {len(closed)} Qwendex agent session{'s' if len(closed) != 1 else ''}.",
                data={
                    "closed": closed,
                    "closed_count": len(closed),
                    "close_timeout_ms": close_timeout_ms,
                    "bounded_close": True,
                    "agent_policy": agent_policy,
                },
            )
        if action == "tombstone":
            if not target or target == "all":
                return stable_envelope(command="agent", status="blocked", summary="Agent tombstone requires one agent id.", errors=["missing agent_id"])
            row = conn.execute("SELECT * FROM qwendex_agent_sessions WHERE agent_id = ?", (target,)).fetchone()
            if row is None:
                return stable_envelope(command="agent", status="blocked", summary=f"Agent session not found: {target}", errors=[target])
            reason = args.reason or "operator_tombstoned"
            close_receipt = make_id("tombstone")
            conn.execute(
                "UPDATE qwendex_agent_sessions SET status = 'tombstoned', updated_at = ?, stop_reason = ?, close_receipt = ? WHERE agent_id = ?",
                (now, reason, close_receipt, target),
            )
            release_agent_locks(conn, target, now=now)
            conn.commit()
            updated = conn.execute("SELECT * FROM qwendex_agent_sessions WHERE agent_id = ?", (target,)).fetchone()
            return stable_envelope(
                command="agent",
                status="warning",
                summary=f"Tombstoned Qwendex agent session {target}.",
                data={"agent_session": row_to_agent_session(updated), "agent_policy": agent_policy},
            )
        rows = conn.execute("SELECT * FROM qwendex_agent_sessions ORDER BY updated_at DESC LIMIT ?", (args.limit,)).fetchall()
        sessions = [session for row in rows if (session := row_to_agent_session(row))]
        if action in {"status", "list", "wait"}:
            data = manager_mode_payload(
                config,
                mode=mode,
                local_status=local_status,
                max_subagents=manager_mode_profile(config, mode)["max_subagents"],
                stale_after_minutes=stale_after,
                kaveman_enabled=kaveman_enabled,
                sessions=sessions,
                agent_policy=agent_policy,
            )
            data["agent_sessions"] = sessions
            data["state_db"] = str(state_db_path(config))
            data["stale_reconciliation"] = reconciliation
            if action == "list":
                return stable_envelope(command="agent", status="pass", summary=f"Loaded {len(sessions)} Qwendex agent sessions.", data=data)
            if action == "wait":
                active = data["active_subagents"]["count"]
                status = "standby" if active else "pass"
                return stable_envelope(
                    command="agent",
                    status=status,
                    summary=(
                        f"{active} Qwendex agent session{'s are' if active != 1 else ' is'} still active."
                        if active
                        else "No Qwendex agent sessions are active."
                    ),
                    data=data,
                )
            return stable_envelope(
                command="agent",
                status=data["manager_health"]["status"],
                summary=f"Qwendex Agent Use is {agent_policy['agent_use']}; loaded {len(sessions)} agent sessions.",
                next_actions=(
                    [data["manager_health"]["repair_command"]]
                    if data["manager_health"]["status"] in {"blocked", "warning"}
                    else data["next_actions"]
                ),
                data=data,
            )
        if action in {"inspect", "logs"}:
            if not target:
                return stable_envelope(command="agent", status="blocked", summary=f"Agent {action} requires an agent id.", errors=["missing agent_id"])
            row = conn.execute("SELECT * FROM qwendex_agent_sessions WHERE agent_id = ?", (target,)).fetchone()
            if row is None:
                return stable_envelope(command="agent", status="blocked", summary=f"Agent session not found: {target}", errors=[target])
            session = row_to_agent_session(row) or {}
            if action == "logs":
                return stable_envelope(
                    command="agent",
                    status="pass",
                    summary=f"Loaded Qwendex agent log metadata for {target}.",
                    artifacts=list(session.get("artifacts", [])),
                    data={
                        "agent_session": session,
                        "log_capture": "metadata-only",
                        "raw_output_artifacts": list(session.get("artifacts", [])),
                        "agent_policy": agent_policy,
                    },
                )
            return stable_envelope(
                command="agent",
                status="pass",
                summary=f"Loaded Qwendex agent session {target}.",
                data={"agent_session": session, "agent_policy": agent_policy},
            )
    return stable_envelope(command="agent", status="blocked", summary=f"Unknown agent action: {action}", errors=[action])


def command_manager_state(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any] | None:
    if not args.action:
        return None
    now = utc_now()
    with connect_state(config) as conn:
        mode = current_manager_mode(config, conn)
        agent_policy = resolve_agent_policy(config, cli_agent_use=getattr(args, "agent_use", ""), selected_manager_mode=mode)
        if agent_policy["errors"]:
            return stable_envelope(command="manager", status="blocked", summary="Invalid Qwendex agent policy.", errors=list(agent_policy["errors"]), data={"agent_policy": agent_policy})
        if args.action not in {"mode"}:
            mode = policy_mode_for_manager(args, config, mode)
        stale_after = mode_stale_after_minutes(config, mode, args.stale_after_minutes)
        max_subagents = args.max_subagents or manager_mode_profile(config, mode)["max_subagents"]
        local_status = local_subagent_status(config, enabled=current_local_enabled(config, conn), env=os.environ, probe=True)
        kaveman_enabled = current_kaveman_enabled(config, conn)
        reconciliation = {"closed_count": 0, "closed": [], "skipped_writer_count": 0, "skipped_writers": [], "stale_after_minutes": max(stale_after, 5)}
        if args.action in {"kaveman", "local", "estimate", "status"}:
            reconciliation = reconcile_stale_manager_sessions(conn, stale_after_minutes=stale_after, now=now)
        if args.action == "mode":
            if args.toggle:
                index = MANAGER_MODE_ORDER.index(mode) if mode in MANAGER_MODE_ORDER else 0
                mode = MANAGER_MODE_ORDER[(index + 1) % len(MANAGER_MODE_ORDER)]
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
            agent_policy = resolve_agent_policy(config, cli_agent_use=getattr(args, "agent_use", ""), selected_manager_mode=mode)
            if agent_policy["errors"]:
                return stable_envelope(command="manager", status="blocked", summary="Invalid Qwendex agent policy.", errors=list(agent_policy["errors"]), data={"agent_policy": agent_policy})
            stale_after = mode_stale_after_minutes(config, mode, args.stale_after_minutes)
            reconciliation = reconcile_stale_manager_sessions(conn, stale_after_minutes=stale_after, now=now)
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
                agent_policy=agent_policy,
            )
            data["state_db"] = str(state_db_path(config))
            data["codex_status_file"] = sync_codex_status_file_from_env(config)
            data["stale_reconciliation"] = reconciliation
            contract_status = data["manager_health"]["status"]
            mode_changed = bool(args.toggle or args.cycle or args.set)
            status = "pass" if mode_changed else contract_status
            return stable_envelope(
                command="manager",
                status=status,
                summary=(
                    f"Qwendex manager mode changed to {data['label']}."
                    if mode_changed
                    else f"Qwendex manager mode is {data['label']}."
                ),
                next_actions=(
                    ["Spawn/register at least one manager lane or set orchestration.manager_deploy_policy to disabled."]
                    if contract_status in {"blocked", "warning"}
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
                agent_policy=agent_policy,
            )
            data["state_db"] = str(state_db_path(config))
            data["codex_status_file"] = sync_codex_status_file_from_env(config)
            data["stale_reconciliation"] = reconciliation
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
                agent_policy=agent_policy,
            )
            data["state_db"] = str(state_db_path(config))
            data["codex_status_file"] = sync_codex_status_file_from_env(config)
            data["stale_reconciliation"] = reconciliation
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
                agent_policy=agent_policy,
            )
        if args.action == "preflight":
            prompt = str(args.prompt or "")
            prompt_known = bool(prompt)
            if args.prompt_file:
                try:
                    prompt = Path(args.prompt_file).expanduser().read_text(encoding="utf-8")
                except OSError as exc:
                    return stable_envelope(command="manager", status="blocked", summary="Manager preflight could not read prompt file.", errors=[str(exc)])
                prompt_known = True
            if args.interactive_prompt_unknown:
                prompt = ""
                prompt_known = False
            payload = manager_preflight_payload(
                config,
                prompt=prompt,
                prompt_known=prompt_known,
                dry_run=bool(args.dry_run),
                repo=Path(os.environ.get("QWENDEX_MANAGER_TARGET_REPO") or os.getcwd()),
                env=os.environ,
            )
            hook_status = payload["hook_status"]
            next_actions = (
                [hook_status["install_command"], hook_status["verify_command"]]
                if payload["stop_status"] == "STOP_MANAGER_BLOCKED_UNHOOKED"
                else []
            )
            return stable_envelope(
                command="manager",
                status="pass" if payload["ok"] else "blocked",
                summary=(
                    "Qwendex Manager preflight is ready."
                    if payload["ok"]
                    else "Qwendex Manager preflight blocked the launch."
                ),
                artifacts=list(payload.get("receipt_paths") or []) if not args.dry_run else [],
                next_actions=next_actions,
                errors=[] if payload["ok"] else [payload["routing_decision"]["routing_reason"]],
                data=payload,
            )
        if args.action == "decision":
            decision = latest_manager_decision(conn, ledger_id=args.agent_id, session_id=args.task_id)
            if decision is None:
                return stable_envelope(command="manager", status="blocked", summary="Manager decision ledger record not found.", errors=[args.agent_id or args.task_id or "latest"])
            return stable_envelope(
                command="manager",
                status="pass",
                summary=f"Loaded manager decision {decision['ledger_id']}.",
                data={"manager_decision": decision},
            )
        if args.action == "reconcile":
            rows = conn.execute("SELECT * FROM qwendex_agent_sessions ORDER BY updated_at DESC").fetchall()
            reconcile_sessions = [session for row in rows if (session := row_to_agent_session(row))]
            validation_reconcile = classify_manager_validation_sessions(reconcile_sessions, stale_after_minutes=stale_after)
            if args.repair and not args.dry_run:
                validation_reconcile["repair_performed"] = False
                validation_reconcile["repair_reason"] = "Qwendex does not mark stale sessions validated without explicit evidence."
            else:
                validation_reconcile["repair_performed"] = False
                validation_reconcile["dry_run"] = bool(args.dry_run)
            status_value = "warning" if validation_reconcile["pending_validation_count"] else "pass"
            return stable_envelope(
                command="manager",
                status=status_value,
                summary=(
                    f"Classified {validation_reconcile['pending_validation_count']} manager sessions with pending validation evidence."
                    if validation_reconcile["pending_validation_count"]
                    else "No pending manager validation debt found."
                ),
                next_actions=["Attach validation evidence before closing pending sessions."] if validation_reconcile["pending_validation_count"] else [],
                data={"validation_reconciliation": validation_reconcile},
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
                agent_policy=agent_policy,
            )
            data["agent_sessions"] = [session for session in sessions if session]
            data["state_db"] = str(state_db_path(config))
            data["stale_reconciliation"] = reconciliation
            status = data["manager_health"]["status"]
            return stable_envelope(
                command="manager",
                status=status,
                summary=f"Loaded {len(data['agent_sessions'])} Qwendex manager sessions.",
                next_actions=(
                    [data["manager_health"]["repair_command"]]
                    if status in {"blocked", "warning"}
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
            required = not bool(getattr(args, "optional", False))
            if getattr(args, "required", False):
                required = True
            context_packet = {
                "objective": args.objective or args.stop_condition,
                "task_class": task_class,
                "allowed_scope": args.write_surface,
                "required": required,
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
        if args.action == "close":
            if not args.agent_id:
                return stable_envelope(command="manager", status="blocked", summary="Manager close requires --agent-id.", errors=["missing agent_id"])
            row = conn.execute("SELECT * FROM qwendex_agent_sessions WHERE agent_id = ?", (args.agent_id,)).fetchone()
            if row is None:
                return stable_envelope(command="manager", status="blocked", summary=f"Agent session not found: {args.agent_id}", errors=[args.agent_id])
            close_receipt = make_id("close")
            reason = args.reason or "operator_closed"
            conn.execute(
                "UPDATE qwendex_agent_sessions SET status = 'closed', updated_at = ?, stop_reason = ?, close_receipt = ? WHERE agent_id = ?",
                (now, reason, close_receipt, args.agent_id),
            )
            release_agent_locks(conn, args.agent_id, now=now)
            conn.commit()
            row = conn.execute("SELECT * FROM qwendex_agent_sessions WHERE agent_id = ?", (args.agent_id,)).fetchone()
            return stable_envelope(
                command="manager",
                status="pass",
                summary=f"Closed agent session {args.agent_id}.",
                data={"agent_session": row_to_agent_session(row)},
            )
        if args.action == "close-stale":
            reconciliation = reconcile_stale_manager_sessions(conn, stale_after_minutes=stale_after, now=now)
            return stable_envelope(
                command="manager",
                status="pass",
                summary=f"Closed {reconciliation['closed_count']} stale Qwendex manager sessions.",
                data=reconciliation,
            )
        if args.action == "repair":
            if not args.safe:
                return stable_envelope(
                    command="manager",
                    status="blocked",
                    summary="Manager repair requires --safe.",
                    errors=["missing --safe"],
                )
            repair = repair_manager_sessions(conn, stale_after_minutes=stale_after, now=now, safe=True)
            repair["closed"] = [*repair["closed_read_only"], *repair["closed_writers"]]
            repair["skipped_writer_count"] = repair["manual_close_count"]
            repair["skipped_writers"] = repair["manual_close"]
            errors = [item["command"] for item in repair["manual_close"]]
            return stable_envelope(
                command="manager",
                status="blocked" if errors else "pass",
                summary=(
                    "Manager repair closed safe stale sessions; manual writer review remains."
                    if errors
                    else "Manager repair closed safe stale sessions."
                ),
                errors=errors,
                next_actions=errors,
                data=repair,
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
            agent_rows = conn.execute(
                "SELECT * FROM qwendex_agent_sessions WHERE task_id = ? ORDER BY updated_at DESC",
                (args.task_id,),
            ).fetchall()
            agent_sessions = [session for row in agent_rows if (session := row_to_agent_session(row))]
            active_locks = active_file_locks(conn)
            plan = {
                "task_id": args.task_id,
                "budget": budget,
                "summary": snapshot["objective"],
                "keep": [
                    "objective",
                    "decisions",
                    "open_files",
                    "evidence_refs",
                    "blocked_items",
                    "next_actions",
                    "agent_outcomes",
                    "file_locks",
                ],
                "decisions": snapshot["decisions"][:10],
                "open_files": snapshot["open_files"][:20],
                "evidence_refs": snapshot["evidence_refs"][:20],
                "blocked_items": snapshot["blocked_items"],
                "next_actions": snapshot["next_actions"][:10],
                "agent_outcomes": agent_outcomes_for_sessions(agent_sessions),
                "file_locks": active_locks,
                "raw_output_policy": "preserve raw child output in artifact paths; inject compact reports into root context",
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
            agent_rows = conn.execute(
                "SELECT * FROM qwendex_agent_sessions WHERE task_id = ? ORDER BY updated_at DESC LIMIT ?",
                (args.task_id, args.limit),
            ).fetchall()
            agent_sessions = [session for row in agent_rows if (session := row_to_agent_session(row))]
            return stable_envelope(
                command="context",
                status="pass",
                summary=f"Built context pack for {args.task_id}.",
                data={
                    "snapshot": snapshot,
                    "evidence": [row_to_evidence(row) for row in evidence_rows],
                    "handoffs": [row_to_handoff(row) for row in handoff_rows],
                    "agent_outcomes": agent_outcomes_for_sessions(agent_sessions),
                    "agent_sessions": agent_sessions,
                    "file_locks": active_file_locks(conn),
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
        agent_policy = resolve_agent_policy(config, cli_agent_use=getattr(args, "agent_use", ""), selected_manager_mode=mode)
        if agent_policy["errors"]:
            return stable_envelope(command="manager", status="blocked", summary="Invalid Qwendex agent policy.", errors=list(agent_policy["errors"]), data={"agent_policy": agent_policy})
        mode = policy_mode_for_manager(args, config, mode)
        local_status = local_subagent_status(config, enabled=current_local_enabled(config, conn), env=os.environ, probe=True)
        kaveman_enabled = current_kaveman_enabled(config, conn)
        stale_after = mode_stale_after_minutes(config, mode, args.stale_after_minutes)
        reconciliation = reconcile_stale_manager_sessions(conn, stale_after_minutes=stale_after, now=utc_now())
        rows = conn.execute("SELECT * FROM qwendex_agent_sessions ORDER BY updated_at DESC LIMIT ?", (args.limit,)).fetchall()
        sessions = [row_to_agent_session(row) for row in rows]
    profile = manager_mode_profile(config, mode)
    max_subagents = args.max_subagents or profile["max_subagents"]
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
        agent_policy=agent_policy,
    )
    data["close_stale"] = args.close_stale
    data["stale_reconciliation"] = reconciliation
    status = data["manager_health"]["status"]
    return stable_envelope(
        command="manager",
        status=status,
        summary=f"Qwendex manager mode is {data['label']}.",
        next_actions=(
            [data["manager_health"]["repair_command"]]
            if status in {"blocked", "warning"}
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
    parser.add_argument("--agent-use", default="", help="effective agent policy: Lite, Medium, Heavy, or Manager")
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check")
    check.add_argument("--health-mode", choices=["advisory", "strict"], default="advisory")
    check.add_argument("--json", action="store_true")

    doctor = sub.add_parser("doctor")
    doctor.add_argument("--health-mode", choices=["advisory", "strict"], default="advisory")
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

    agent = sub.add_parser("agent")
    agent.add_argument(
        "action",
        nargs="?",
        choices=["status", "list", "inspect", "logs", "wait", "close", "tombstone", "policy", "profiles", "team", "plan", "metrics", "hook", "hook-config", "locks"],
        default="status",
    )
    agent.add_argument("target", nargs="?")
    agent.add_argument("--agent-id", default="")
    agent.add_argument("--timeout", default="10s")
    agent.add_argument("--reason", default="")
    agent.add_argument("--event-json", default="")
    agent.add_argument("--qwendex-command", default="")
    agent.add_argument("--write", type=Path)
    agent.add_argument("--print", action="store_true")
    agent.add_argument("--install", action="store_true")
    agent.add_argument("--verify", action="store_true")
    agent.add_argument("--codex-home", default="")
    agent.add_argument("--approve", action="store_true")
    agent.add_argument("--force", action="store_true")
    agent.add_argument("--prompt", default="")
    agent.add_argument("--task-id", default="")
    agent.add_argument("--limit", type=int, default=20)
    agent.add_argument("--stale-after-minutes", type=int, default=0)
    agent.add_argument("--codex-hook-output", action="store_true")
    agent.add_argument("--json", action="store_true")

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
    manager.add_argument("action", nargs="?", choices=["status", "assign", "heartbeat", "close", "close-stale", "repair", "reconcile", "mode", "estimate", "preflight", "decision", "kaveman", "local"])
    manager.add_argument("--mode", choices=["manual", "off", "auto", "lite", "medium", "heavy", "manager", "manager_only"], default="")
    manager.add_argument("--set", default="")
    manager.add_argument("--cycle", action="store_true")
    manager.add_argument("--toggle", action="store_true")
    manager.add_argument("--prompt", default="")
    manager.add_argument("--prompt-file", default="")
    manager.add_argument("--interactive-prompt-unknown", action="store_true")
    manager.add_argument("--dry-run", action="store_true")
    manager.add_argument("--pending-validation", action="store_true")
    manager.add_argument("--repair", action="store_true")
    manager.add_argument("--max-subagents", type=int, default=0)
    manager.add_argument("--stale-after-minutes", type=int, default=0)
    manager.add_argument("--close-stale", action="store_true")
    manager.add_argument("--safe", action="store_true")
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
    manager.add_argument("--reason", default="")
    manager.add_argument("--expected-artifact", default="")
    manager.add_argument("--receipt-path", default="")
    manager.add_argument("--context-budget", type=int, default=0)
    manager.add_argument("--risk", choices=["low", "medium", "high"], default="")
    manager.add_argument("--review-requirement", default="manager review required")
    manager.add_argument("--artifact", action="append")
    manager.add_argument("--required", action="store_true")
    manager.add_argument("--optional", action="store_true")
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
    agent_policy = resolve_agent_policy(
        config,
        cli_agent_use=getattr(args, "agent_use", ""),
        selected_manager_mode=selected_manager_mode_for_policy(
            config,
            explicit=getattr(args, "mode", "") if getattr(args, "command", "") == "manager" else "",
        ),
    )
    if agent_policy["errors"]:
        return stable_envelope(
            command=getattr(args, "command", "unknown"),
            status="blocked",
            summary="Invalid Qwendex agent policy.",
            errors=list(agent_policy["errors"]),
            data={"agent_policy": agent_policy},
        )
    apply_agent_policy_env(agent_policy)
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
    if args.command == "agent":
        return command_agent(args, config)
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
    return 0 if data.get("status") in {"pass", "ready", "standby", "warning"} else 1


def main(argv: list[str] | None = None) -> int:
    parser = command_line()
    args = parser.parse_args(argv)
    try:
        data = run(args)
    except Exception as exc:  # pragma: no cover - defensive CLI boundary
        data = stable_envelope(command=getattr(args, "command", "unknown"), status="fail", summary=str(exc), errors=[str(exc)])
    if (
        args.command == "agent"
        and getattr(args, "action", "") == "hook"
        and getattr(args, "codex_hook_output", False)
    ):
        print(json.dumps(data.get("data", {}).get("codex_hook_output", {}), sort_keys=True))
        return 0 if data.get("status") in {"pass", "blocked"} else exit_code(data)
    if args.command == "codex-status" and not getattr(args, "json", False):
        print(data.get("data", {}).get("text", data["summary"]))
    elif getattr(args, "json", False):
        print_json(data)
    else:
        human_print(data)
    return exit_code(data)


if __name__ == "__main__":
    raise SystemExit(main())
