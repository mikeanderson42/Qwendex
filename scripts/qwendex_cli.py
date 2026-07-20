#!/usr/bin/env python3
"""Public Qwendex CLI facade for Codex plus bounded local Qwen support."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.6.2"
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
    "scripts/qwendex_release_gate.py",
    "scripts/qwendex_install_deps",
    "scripts/qwendex_dev_env",
    "scripts/qwendex_runtime.py",
    "scripts/qwendex_manager_acceptance.py",
    "scripts/qwendex_manager_faults.py",
    "scripts/qwendex_manager_install_acceptance.py",
    "scripts/qwendex_manager_live.py",
    "scripts/qwendex_manager_security.py",
    "scripts/qwendex_manager_self_host.py",
    "scripts/qwendex_manager_soak.py",
    "scripts/qwendex_manager_state_migrations.py",
    "scripts/qwendex_routing_eval.py",
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
    "config/qwendex/manager-performance-budget.json",
    "config/qwendex/manager-routing-corpus.json",
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
    "scripts/run_llamacpp_qwen_gguf.sh",
    "scripts/run_vllm_qwen_gguf.sh",
    "scripts/run_koboldcpp_gguf.sh",
)

LLMSTACK_PRIVATE_PATTERNS = (
    re.compile(r"(?<![A-Za-z0-9_.-])/home/[A-Za-z0-9_.-]+(?=/)"),
    re.compile(r"(?<![A-Za-z0-9_.-])/mnt/[a-z]/Users/[A-Za-z0-9_.-]+(?=/)", re.IGNORECASE),
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
AGENT_TERMINAL_STATUSES = {"completed", "blocked", "failed", "closed", "tombstoned", "waived"}
STATE_BUSY_TIMEOUT_MS = 2000
# Version 3 adds the Qdex permission provenance recorded with each Manager
# decision. Bump the version so already-migrated v2 databases receive the
# additive columns before a live Qdex preflight writes them.
STATE_SCHEMA_VERSION = 3
STATE_MIGRATION_FAULT_ENV = "QWENDEX_STATE_MIGRATION_FAIL_AT"
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
    "SessionStart": {"matcher": "", "timeout": 5},
    "UserPromptSubmit": {"matcher": "", "timeout": 5},
    "SubagentStart": {"matcher": ".*", "timeout": 5},
    "SubagentStop": {"matcher": ".*", "timeout": 5},
    "Stop": {"matcher": "", "timeout": 5},
    "PreToolUse": {"matcher": ".*", "timeout": 5},
    "PostToolUse": {"matcher": ".*", "timeout": 5},
    "PreCompact": {"matcher": "", "timeout": 5},
    "PostCompact": {"matcher": "", "timeout": 5},
}
READ_ONLY_AGENT_PROFILES = {
    "audit",
    "docs_researcher",
    "explorer",
    "read-only",
    "readonly",
    "review",
    "reviewer",
    "verifier",
}
ROOT_ONLY_AGENT_TOOLS = {"spawn_agent", "close_agent", "wait_agent", "resume_agent", "agent_ledger_update_status"}
WRITE_TOOL_NAMES = {"write", "edit", "apply_patch", "create_file", "delete_file", "move_file"}
NON_FILESYSTEM_CONTROL_TOOL_NAMES = {"create_goal", "update_goal", "update_plan"}
READ_ONLY_NON_SHELL_TOOL_NAMES = {
    "finance",
    "find",
    "get_goal",
    "list_agents",
    "list_mcp_resource_templates",
    "list_mcp_resources",
    "open",
    "read",
    "read_mcp_resource",
    "screenshot",
    "search",
    "send_message",
    "sports",
    "status",
    "time",
    "view_image",
    "wait",
    "wait_agent",
    "weather",
    "web_fetch",
    "web_search",
}
READ_ONLY_INSPECTION_ACTIONS = {
    "check",
    "describe",
    "fetch",
    "find",
    "get",
    "inspect",
    "list",
    "lookup",
    "query",
    "read",
    "search",
    "show",
    "status",
    "view",
}
MUTATING_TOOL_ACTIONS = {
    "add",
    "apply",
    "approve",
    "assign",
    "close",
    "commit",
    "create",
    "delete",
    "edit",
    "exec",
    "execute",
    "merge",
    "modify",
    "move",
    "patch",
    "post",
    "publish",
    "push",
    "put",
    "remove",
    "rename",
    "reopen",
    "replace",
    "resolve",
    "run",
    "save",
    "send",
    "set",
    "submit",
    "update",
    "upload",
    "write",
}
COLLABORATION_LIFECYCLE_TOOL_NAMES = {
    "followup_task",
    "interrupt_agent",
    "list_agents",
    "send_message",
    "wait_agent",
}
READ_ONLY_EXECUTION_TOOL_NAMES = {
    "bash",
    "command",
    "exec",
    "exec_command",
    "fish",
    "ipython",
    "node",
    "perl",
    "php",
    "powershell",
    "pwsh",
    "python",
    "python3",
    "ruby",
    "run_command",
    "sh",
    "shell",
    "shell_command",
    "terminal",
    "zsh",
}
READ_ONLY_SIMPLE_COMMANDS = {"cat", "file", "grep", "head", "jq", "ls", "nl", "pwd", "stat", "tail", "wc"}
READ_ONLY_GIT_SUBCOMMANDS = {"diff", "log", "rev-parse", "show", "status"}
READ_ONLY_GIT_UNSAFE_OPTIONS = {"--ext-diff", "--output", "--textconv"}
READ_ONLY_FIND_UNSAFE_PREFIXES = (
    "-delete",
    "-exec",
    "-fls",
    "-fprint",
    "-fprintf",
    "-ok",
)
SHELL_MUTATING_COMMANDS = {
    "apply_patch",
    "chmod",
    "chgrp",
    "chown",
    "cp",
    "dd",
    "install",
    "ln",
    "mkdir",
    "mv",
    "patch",
    "rm",
    "rmdir",
    "tee",
    "touch",
    "truncate",
}
MANAGER_DECISION_ATTACH_WINDOW_MINUTES = 24 * 60
MANAGED_HOOK_RUNTIME_ENV_KEYS = (
    "CODEX_HOME",
    "QWENDEX_STATE_DB",
    "QWENDEX_PERFORMANCE_DB",
    "QWENDEX_RESULTS_ROOT",
    "QWENDEX_LEDGER_DB",
    "QWENDEX_DEV_ROOT",
    "QWENDEX_ROOT",
    "QWENDEX_RUNTIME_ROOT",
    "QWENDEX_RUNTIME_TREE",
    "QWENDEX_RUNTIME_GENERATION_DIR",
    "QWENDEX_RUNTIME_GENERATION_ID",
    "QWENDEX_RUNTIME_CONTRACT_SHA256",
    "QWENDEX_HOOK_GENERATION",
    "QWENDEX_QDEX_PERMISSION_MODE",
    "QWENDEX_QDEX_PERMISSION_SOURCE",
)
PERFORMANCE_DB_ENV = "QWENDEX_PERFORMANCE_DB"
MANAGER_SESSION_STATE_FILE_ENV = "QWENDEX_MANAGER_SESSION_STATE_FILE"
QDEX_LAUNCH_ID_ENV = "QWENDEX_QDEX_LAUNCH_ID"
QDEX_LAUNCH_POLICY_HASH_ENV = "QWENDEX_QDEX_LAUNCH_POLICY_HASH"
QDEX_LAUNCH_MODE_ENV = "QWENDEX_QDEX_LAUNCH_MODE"
QDEX_LAUNCH_AGENT_USE_ENV = "QWENDEX_QDEX_LAUNCH_AGENT_USE"
QDEX_LAUNCH_MAX_WORKERS_ENV = "QWENDEX_QDEX_LAUNCH_MAX_WORKERS"
QDEX_LAUNCH_LOCAL_ENABLED_ENV = "QWENDEX_QDEX_LAUNCH_LOCAL_ENABLED"
MANAGER_SESSION_STATE_SCHEMA = "qwendex.manager_session_state.v2"
DEFAULT_PERFORMANCE_DB = Path.home() / ".local" / "state" / "qwendex" / "qwendex-performance.sqlite"
PERFORMANCE_CAPTURE_MODES = {"off", "metadata"}
ENV_OPTIONS_WITH_VALUE = {"-u", "--unset", "-C", "--chdir", "-a", "--argv0"}
ENV_LONG_OPTIONS_WITH_VALUE = {"--unset", "--chdir", "--argv0"}
ENV_SPLIT_STRING_OPTIONS = {"-S", "--split-string"}
SHELL_COMMAND_WRAPPERS = {"bash", "sh", "zsh"}
SHELL_OPTIONS_WITH_VALUE = {"--init-file", "--rcfile", "-O", "+O", "-o", "+o"}
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
        "final_report_required": False,
        "default_required": False,
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
        "final_report_required": False,
        "default_required": False,
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
        "final_report_required": False,
        "default_required": False,
        "nickname_candidates": ["Audit", "FIDO", "Check"],
    },
    "reviewer": {
        "name": "reviewer",
        "description": "Read-only reviewer for architecture, security, release, and integration-risk findings.",
        "role": "review",
        "model_reasoning_effort": "high",
        "sandbox_mode": "read-only",
        "tools_allow": ["read", "search", "test", "status"],
        "tools_deny": ["write", "spawn_agent", "close_agent"],
        "can_spawn": False,
        "final_report_required": False,
        "default_required": False,
        "nickname_candidates": ["Review", "Sentinel", "Lens"],
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
        "final_report_required": False,
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
        "final_report_required": False,
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
    "members": ["explorer", "reviewer", "verifier", "docs_researcher"],
    # Keep the legacy key for consumers of the v1 shape, but do not represent
    # advisory delegation suggestions as authorization requirements.
    "required_lanes_by_task": {},
    "suggested_lanes_by_task": {
        "repo_exploration": ["explorer"],
        "code_edit_small": ["verifier"],
        "code_edit_complex": ["explorer", "verifier"],
        "docs_api_uncertainty": ["docs_researcher"],
        "release_publish": ["reviewer", "verifier"],
    },
    "routing_rules": [
        "quick questions go direct when no repo, docs, or edit work is needed",
        "read-heavy repo mapping uses explorer in Heavy/Manager",
        "the root is the sole default writer; read-only explorer and verifier lanes are useful for non-trivial edits",
        "release tasks often benefit from read-only reviewer and verifier lanes; Qwendex does not authorize publication",
    ],
}
MANAGER_DEPLOY_POLICIES = {"auto", "disabled"}
MANAGER_MAX_SUBAGENTS_LIMIT = 8
MANAGER_MODE_MAX_SUBAGENTS = {
    "off": 0,
    "auto": 4,
    "lite": 1,
    "medium": 2,
    "heavy": 3,
    "manager": 4,
}
MANAGER_DECISION_ROUTES = {"direct_single_writer", "manager_subagents", "blocked"}
MANAGER_STOP_STATUSES = {
    "STOP_MANAGER_PREFLIGHT_READY",
    "STOP_MANAGER_DIRECT_READY",
    "STOP_MANAGER_SUBAGENTS_READY",
    "STOP_MANAGER_BLOCKED_UNHOOKED",
    "STOP_MANAGER_PROMPT_ADMISSION_BLOCKED",
    "STOP_MANAGER_UNATTACHED",
    "STOP_MANAGER_VALIDATION_PENDING",
    "STOP_MANAGER_CLOSED",
}
MANAGER_PROMPT_UNKNOWN_SUMMARY = "interactive_prompt_unknown_prelaunch"
MANAGER_PROMPT_ADMISSION_SCHEMA = "qwendex.prompt_admission.v1"
MANAGER_PROMPT_SOURCE = "UserPromptSubmit"
MANAGER_ROOT_AGENT_ID_ENV = "QWENDEX_MANAGER_ROOT_AGENT_ID"
MANAGER_LAUNCH_PID_ENV = "QWENDEX_MANAGER_LAUNCH_PID"
MANAGER_LAUNCH_START_TICKS_ENV = "QWENDEX_MANAGER_LAUNCH_START_TICKS"
MANAGER_LAUNCH_NONCE_ENV = "QWENDEX_MANAGER_LAUNCH_NONCE"
MANAGER_LAUNCH_KEY_ENV = "QWENDEX_MANAGER_LAUNCH_KEY"
MANAGER_STATE_DB_IDENTITY_ENV = "QWENDEX_MANAGER_STATE_DB_IDENTITY"
MANAGER_LEDGER_DB_IDENTITY_ENV = "QWENDEX_MANAGER_LEDGER_DB_IDENTITY"
MANAGER_RUNTIME_IDENTITY_ENV = "QWENDEX_MANAGER_RUNTIME_IDENTITY"
MANAGER_ROOT_LOCK_PATH = "<repo-root>"
MANAGER_ROOT_TOOL_SEPARATOR = "--tool-"
QWENDEX_CODEX_PATCH_MARKER = "QWENDEX_CODEX_TUI_PATCH_V1"
QWENDEX_CODEX_STATUS_ITEM_ID = "qwendex-manager"
QWENDEX_CODEX_STATUS_FILE_ENV = "QWENDEX_CODEX_STATUS_FILE"
QWENDEX_MODELS_CACHE_FILE_ENV = "QWENDEX_MODELS_CACHE_FILE"
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
            {
                "path": "codex-rs/hooks/src/events/session_start.rs",
                "anchors": ["StartHookTarget", "SubagentStartCommandInput"],
            },
            {
                "path": "codex-rs/hooks/src/schema.rs",
                "anchors": ["SubagentStartCommandInput", "agent_type"],
            },
            {
                "path": "codex-rs/core/src/hook_runtime.rs",
                "anchors": ["StartHookTarget::SubagentStart", "SubAgentSource::ThreadSpawn"],
            },
            {
                "path": "codex-rs/core/src/tools/spec_plan.rs",
                "anchors": ["fn collab_tools_enabled", "MultiAgentVersion::V2"],
            },
            {
                "path": "codex-rs/core/src/tools/handlers/multi_agents_v2/wait.rs",
                "anchors": ["wait_for_activity", "WaitOutcome::TimedOut"],
            },
            {
                "path": "codex-rs/core/src/tools/handlers/multi_agents_spec.rs",
                "anchors": ["create_wait_agent_tool_v2", "Wait for a mailbox update"],
            },
            {
                "path": "codex-rs/core/src/config/mod.rs",
                "anchors": ["validate_multi_agent_v2_config", "effective_agent_max_threads"],
            },
            {
                "path": "codex-rs/core/src/config/config_tests.rs",
                "anchors": ["multi_agent_v2", "effective_agent_max_threads"],
            },
            {
                "path": "codex-rs/models-manager/src/manager.rs",
                "anchors": ["const MODEL_CACHE_FILE", "ModelsCacheManager::new(cache_path"],
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
            "Expose canonical task_name and parent_session_id on SubagentStart hook input for exact Qwendex ledger binding.",
            "Restrict native MultiAgentV2 collaboration management tools to the root thread.",
            "Return immediately from V2 wait_agent when no child is running and direct the root away from empty retry loops.",
            "Allow V2 to ignore a legacy agents.max_threads value while retaining its own per-session cap.",
            "Honor QWENDEX_MODELS_CACHE_FILE so mixed Codex versions do not overwrite one shared model catalog.",
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
CODEX_PATCH_MANIFESTS["0.144.0"] = {
    **CODEX_PATCH_MANIFESTS["0.143.0"],
    "codex_tag": "rust-v0.144.0",
}
CODEX_PATCH_MANIFESTS["0.144.4"] = {
    **CODEX_PATCH_MANIFESTS["0.144.0"],
    "codex_tag": "rust-v0.144.4",
}
CODEX_PATCH_MANIFESTS["0.144.6"] = {
    **CODEX_PATCH_MANIFESTS["0.144.4"],
    "codex_tag": "rust-v0.144.6",
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
    },
    "qdex": {
        "permission_mode": "workspace-write",
    },
    "receipts": {
        "dir": "results/qwendex",
        "ledger": "~/.local/state/qwendex/qwendex.sqlite",
    },
    "state": {
        "db": "~/.local/state/qwendex/qwendex.sqlite",
    },
    "performance": {
        "capture": "off",
        "retention_days": 14,
        "max_events": 50000,
        "query_fingerprints": True,
    },
    "eval": {
        "default_case": "all",
    },
    "learning": {
        "mode": "stage_only",
        "default_backend": "mock",
    },
    "orchestration": {
        "mode": "auto",
        "manager_deploy_policy": "auto",
        "local_subagents": {
            "enabled": True,
        },
        "kaveman": {
            "enabled": False,
            "directive": "Use terse output: short, direct, minimal prose, no optional explanation unless asked.",
        },
        "mode_profiles": {
            "off": {"label": "Off", "max_subagents": 0},
            "auto": {"label": "Auto", "max_subagents": 4},
            "lite": {"label": "Lite", "max_subagents": 1},
            "medium": {"label": "Medium", "max_subagents": 2},
            "heavy": {"label": "Heavy", "max_subagents": 3},
            "manager": {"label": "Manager Mode", "max_subagents": 4},
        },
        "local_qwen_eligibility": {
            "allowed_task_classes": [
                "repository_mapping",
                "read_heavy_investigation",
                "single_file_read",
                "small_edit",
                "test_or_regression",
                "read-heavy audit",
                "docs draft",
                "queue workflow",
                "bounded patch",
                "smoke probe",
                "artifact summary",
                "review",
            ],
            "denied_task_classes": [
                "cross_cutting_edit",
                "security_or_protocol",
                "release_or_publish",
                "live_acceptance",
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
    },
    "seats": {
        "primary": {
            "model": "gpt-5.5",
            "authority": "release_review",
            "backend": "codex",
            "context_window": 200000,
            "guard_profile": "balanced",
        },
        "qwen": {
            "authority": "bounded_operator",
            "backend": "local-responses-adapter",
            "context_window": 32768,
            "compact_limit": 28672,
            "guard_profile": "balanced",
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
            "authority": "isolated_probe",
            "backend": "local-responses-adapter",
            "context_window": 32768,
            "compact_limit": 28672,
            "guard_profile": "max_safety",
        },
    },
}

EXEC_TASK_CLASS_CHOICES = tuple(sorted({
    *DEFAULT_CONFIG["routing"]["prefer_for_task_classes"],
    *DEFAULT_CONFIG["routing"]["primary_required_for_task_classes"],
    "security review",
}))


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def atomic_write_text(path: Path, text: str) -> None:
    """Replace a small shared state file without exposing partial contents."""
    target = path.expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(
        f".{target.name}.{os.getpid()}.{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')}.tmp"
    )
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


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


def qdex_permission_posture(
    config: Mapping[str, Any],
    *,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Return the launcher-snapshotted Qdex permission posture without paths."""
    source_env = os.environ if env is None else env
    launched_mode = str(source_env.get("QWENDEX_QDEX_PERMISSION_MODE") or "").strip()
    launched_source = str(source_env.get("QWENDEX_QDEX_PERMISSION_SOURCE") or "").strip()
    valid_modes = {"workspace-write", "yolo"}
    if launched_mode:
        return {
            "mode": launched_mode,
            "source": launched_source or "launch-environment",
            "valid": launched_mode in valid_modes,
        }
    qdex = config.get("qdex") if isinstance(config.get("qdex"), Mapping) else {}
    published_mode = str(qdex.get("permission_mode") or "").strip()
    if published_mode:
        return {
            "mode": published_mode,
            "source": "published-config",
            "valid": published_mode in valid_modes,
        }
    return {"mode": "workspace-write", "source": "default", "valid": True}


def normalize_manager_mode(value: Any) -> str:
    text = str(value or "").strip().lower().replace(" ", "_")
    return MANAGER_MODE_ALIASES.get(text, text)


def normalize_agent_use_mode(value: Any) -> str:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return AGENT_USE_ALIASES.get(text, text)


def kaveman_enabled_for_policy(config: Mapping[str, Any], enabled: bool | None = None) -> bool:
    if enabled is not None:
        return bool(enabled)
    try:
        with connect_state(config) as conn:
            return current_kaveman_enabled(config, conn)
    except (OSError, sqlite3.Error, ValueError):
        return kaveman_default_enabled(config)


def kaveman_output_policy(config: Mapping[str, Any], enabled: bool | None = None) -> dict[str, Any]:
    active = kaveman_enabled_for_policy(config, enabled)
    directive = kaveman_directive(config) if active else ""
    return {
        "name": "kaveman" if active else "standard",
        "kaveman_enabled": active,
        "terse_output": active,
        "directive": directive,
        "optional_explanation": "only_when_requested" if active else "allowed",
        "enforced_by": (
            ["agent_policy", "managed_hook_context", "manager_workflow_receipts"]
            if active
            else []
        ),
    }


def agent_policy_env(policy: Mapping[str, Any]) -> dict[str, str]:
    output_policy = policy.get("output_policy", {})
    directive = str(output_policy.get("directive") or "") if isinstance(output_policy, Mapping) else ""
    kaveman_enabled = bool(output_policy.get("kaveman_enabled")) if isinstance(output_policy, Mapping) else False
    local_snapshot = policy.get("local_routing_snapshot", {})
    if not isinstance(local_snapshot, Mapping):
        local_snapshot = {}
    local_routing = local_snapshot.get("routing", {})
    if not isinstance(local_routing, Mapping):
        local_routing = {}
    return {
        "QWENDEX_EFFECTIVE_AGENT_USE": str(policy["agent_use"]),
        "QWENDEX_AGENT_POLICY_HASH": str(policy["policy_hash"]),
        "QWENDEX_AGENT_POLICY_SOURCE": str(policy["source"]),
        "QWENDEX_OUTPUT_POLICY": "kaveman" if kaveman_enabled else "standard",
        "QWENDEX_KAVEMAN_ENABLED": "1" if kaveman_enabled else "0",
        "QWENDEX_KAVEMAN_DIRECTIVE": directive,
        "QWENDEX_EFFECTIVE_LOCAL_SUBAGENTS": "1" if local_snapshot.get("enabled") else "0",
        "QWENDEX_EFFECTIVE_LOCAL_MODEL": str(local_routing.get("local_model") or ""),
    }


def attach_output_policy(
    policy: Mapping[str, Any],
    config: Mapping[str, Any],
    *,
    kaveman_enabled: bool | None = None,
) -> dict[str, Any]:
    updated = dict(policy)
    output_policy = kaveman_output_policy(config, kaveman_enabled)
    updated["output_policy"] = output_policy
    updated["kaveman_enabled"] = output_policy["kaveman_enabled"]
    updated["kaveman_directive"] = output_policy["directive"]
    updated.pop("policy_hash", None)
    updated["policy_hash"] = agent_policy_hash(updated)
    updated["env"] = agent_policy_env(updated)
    return updated


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
            "min_threads": 0,
            "max_threads": 4,
            "max_depth": 1,
            "root_can_spawn": True,
            "require_agent_ledger": False,
            "require_verifier_for_edits": False,
            "require_final_report_contract": False,
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
            "max_depth": 1,
            "root_can_spawn": True,
            "require_agent_ledger": False,
            "require_verifier_for_edits": False,
            "require_final_report_contract": False,
            "require_routing_reason": False,
            "forbid_fork_context": True,
            "default_fork_context": False,
            "max_inherited_context_bytes": 4096,
            "agent_idle_timeout_ms": 240000,
            "wait_timeout_ms": 90000,
            "close_timeout_ms": 7500,
            "max_resteer_attempts": 0,
        },
        "medium": {
            "min_threads": 0,
            "max_threads": 2,
            "max_depth": 1,
            "root_can_spawn": True,
            "require_agent_ledger": False,
            "require_verifier_for_edits": False,
            "require_final_report_contract": False,
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
            "min_threads": 0,
            "max_threads": 3,
            "max_depth": 1,
            "root_can_spawn": True,
            "require_agent_ledger": False,
            "require_verifier_for_edits": False,
            "require_final_report_contract": False,
            "require_routing_reason": False,
            "forbid_fork_context": True,
            "default_fork_context": False,
            "max_inherited_context_bytes": 8192,
            "agent_idle_timeout_ms": 240000,
            "wait_timeout_ms": 90000,
            "close_timeout_ms": 10000,
            "max_resteer_attempts": 1,
        },
        "manager": {
            "min_threads": 0,
            "max_threads": 4,
            "max_depth": 1,
            "root_can_spawn": True,
            "require_agent_ledger": False,
            "require_verifier_for_edits": False,
            "require_final_report_contract": False,
            "require_routing_reason": False,
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
        if key not in {"policy_hash", "source", "selector", "warnings", "errors", "env", "tool_surface"}
    }
    return hashlib.sha256(json.dumps(hashed, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def resolve_agent_policy(
    config: Mapping[str, Any],
    *,
    cli_agent_use: str = "",
    env: Mapping[str, str] | None = None,
    selected_manager_mode: str = "",
    kaveman_enabled: bool | None = None,
    selector_source_override: str = "",
) -> dict[str, Any]:
    source_env = os.environ if env is None else env
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
    if selector_source_override:
        selector_source = selector_source_override
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
    configured_capacity = int(manager_mode_profile(config, mode)["max_subagents"])
    policy["max_threads"] = configured_capacity
    policy["max_workers"] = configured_capacity
    policy["native_max_concurrent_threads"] = configured_capacity + 1
    policy["min_threads"] = 0
    policy["capacity_source"] = "orchestration.mode_profiles"
    policy.update({
        "source": selector_source,
        "selector": selector,
        "warnings": warnings,
        "errors": errors,
    })
    policy = attach_output_policy(policy, config, kaveman_enabled=kaveman_enabled)
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


def manager_launch_policy_snapshot(
    config: Mapping[str, Any],
    *,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any] | None:
    """Load the immutable AgentPolicy captured by the current Qdex launch."""
    source_env = os.environ if env is None else env
    ledger_id = str(source_env.get("QWENDEX_MANAGER_LEDGER_ID") or "").strip()
    session_id = str(source_env.get("QWENDEX_MANAGER_SESSION_ID") or "").strip()
    exported_hash = str(source_env.get("QWENDEX_MANAGER_POLICY_HASH") or "").strip()
    if not ledger_id or not session_id or not exported_hash:
        return None
    try:
        with connect_state(config) as conn:
            rows = conn.execute(
                """
                SELECT * FROM qwendex_manager_decisions
                WHERE (ledger_id = ? OR launch_ledger_id = ?)
                  AND session_id = ?
                ORDER BY CASE WHEN ledger_id = launch_ledger_id THEN 0 ELSE 1 END,
                         timestamp_created ASC
                LIMIT 2
                """,
                (ledger_id, ledger_id, session_id),
            ).fetchall()
    except (OSError, sqlite3.Error, ValueError):
        return None
    if not rows:
        return None
    decision = row_to_manager_decision(rows[0]) or {}
    snapshot = decision.get("policy_snapshot")
    if not isinstance(snapshot, Mapping):
        return None
    policy = dict(snapshot)
    if (
        str(decision.get("policy_hash") or "") != exported_hash
        or str(policy.get("policy_hash") or "") != exported_hash
        or agent_policy_hash(policy) != exported_hash
        or str(policy.get("mode") or "") not in AGENT_USE_ORDER
    ):
        return None
    return policy


def policy_mode_for_manager(args: argparse.Namespace, config: Mapping[str, Any], fallback_mode: str) -> str:
    if getattr(args, "mode", ""):
        return normalize_manager_mode(getattr(args, "mode", "")) or fallback_mode
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
        "max_subagents": profile.get(
            "max_subagents",
            MANAGER_MODE_MAX_SUBAGENTS.get(normalized, MANAGER_MODE_MAX_SUBAGENTS["auto"]),
        ),
    }


def manager_ui_indicator(config: Mapping[str, Any], mode: str) -> str:
    profile = manager_mode_profile(config, mode)
    return f"(Alt+M) Agent Manager: [ {profile['label']} ]"


def local_state_label(local_state: str) -> str:
    return {
        "ready": "Ready",
        "off": "Off",
        "unavailable": "Unavailable",
        "unknown": "Unavailable",
    }.get(local_state, "Unavailable")


def local_indicator(config: Mapping[str, Any], enabled: bool, local_state: str | None = None) -> str:
    state = local_state or ("ready" if enabled else "off")
    return f"(Alt+L) Local: [{local_state_label(state)}]"


def kaveman_default_enabled(config: Mapping[str, Any]) -> bool:
    kaveman = config.get("orchestration", {}).get("kaveman", {})
    if isinstance(kaveman, Mapping) and isinstance(kaveman.get("enabled"), bool):
        return bool(kaveman["enabled"])
    return False


def kaveman_indicator(config: Mapping[str, Any], enabled: bool) -> str:
    return f"(Alt+K) Kaveman: [{'Y' if enabled else 'N'}]"


def kaveman_directive(config: Mapping[str, Any]) -> str:
    kaveman = config.get("orchestration", {}).get("kaveman", {})
    if isinstance(kaveman, Mapping) and isinstance(kaveman.get("directive"), str):
        return kaveman["directive"]
    return "Use terse output: short, direct, minimal prose, no optional explanation unless asked."


def estimator_config(config: Mapping[str, Any]) -> dict[str, Any]:
    primary_model = str(config.get("seats", {}).get("primary", {}).get("model") or "gpt-5.5")
    return {
        "kind": "deterministic_heuristic",
        "implementation": "qwendex_cli_rules",
        "model_invoked": False,
        "skill_invoked": False,
        "recommendation_model": primary_model,
        "default_reasoning": "medium",
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
        "performance_db": str(state_root / "qwendex-performance.sqlite"),
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
    performance_capture = str(source.get("QWENDEX_PERFORMANCE_CAPTURE") or "").strip()
    if performance_capture:
        data["performance"] = {"capture": performance_capture}
    if source.get("QWENDEX_LEARNING_MODE"):
        data["learning"] = {"mode": source["QWENDEX_LEARNING_MODE"]}
    orchestration: dict[str, Any] = {}
    if source.get("QWENDEX_ORCHESTRATION_MODE"):
        orchestration["mode"] = source["QWENDEX_ORCHESTRATION_MODE"]
    if source.get("QWENDEX_MANAGER_MODE"):
        orchestration["mode"] = source["QWENDEX_MANAGER_MODE"]
    if source.get("QWENDEX_MANAGER_DEPLOY_POLICY"):
        orchestration["manager_deploy_policy"] = source["QWENDEX_MANAGER_DEPLOY_POLICY"]
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


def unknown_nested_config_keys(
    value: Mapping[str, Any],
    template: Mapping[str, Any],
    *,
    prefix: str = "",
) -> list[str]:
    unknown: list[str] = []
    for key, nested in value.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if key not in template:
            unknown.append(path)
            continue
        expected = template[key]
        if isinstance(nested, Mapping) and isinstance(expected, Mapping):
            unknown.extend(unknown_nested_config_keys(nested, expected, prefix=path))
    return unknown


def validate_qwendex_config(config: Mapping[str, Any]) -> list[str]:
    failures: list[str] = []
    allowed_top = set(DEFAULT_CONFIG)
    unknown = sorted(set(config) - allowed_top)
    failures.extend(f"unknown top-level key: {key}" for key in unknown)
    for key in sorted(set(config) & allowed_top - {"seats"}):
        value = config.get(key)
        expected = DEFAULT_CONFIG.get(key)
        if isinstance(value, Mapping) and isinstance(expected, Mapping):
            failures.extend(
                f"unknown config key: {path}"
                for path in unknown_nested_config_keys(value, expected, prefix=key)
            )
    required = {"schema_version", "version", "default_seat", "routing", "guard", "qdex", "receipts", "state", "eval", "learning", "seats"}
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
    else:
        try:
            local_qwen_base_url(config)
        except ValueError as exc:
            failures.append(str(exc))
    timeout = routing.get("probe_timeout_seconds")
    if not isinstance(timeout, int | float) or timeout <= 0 or timeout > 30:
        failures.append(f"invalid routing.probe_timeout_seconds: {timeout}")
    if routing.get("fallback_seat") not in config.get("seats", {}):
        failures.append(f"unknown routing.fallback_seat: {routing.get('fallback_seat')}")
    elif seat_uses_local_qwen(config, str(routing.get("fallback_seat") or "")):
        failures.append(
            f"routing.fallback_seat must use GPT/Codex authority, not local Qwen: {routing.get('fallback_seat')}"
        )
    for list_key in ("prefer_for_task_classes", "primary_required_for_task_classes"):
        values = routing.get(list_key)
        if not isinstance(values, list) or not all(isinstance(item, str) and item.strip() for item in values):
            failures.append(f"invalid routing.{list_key}: {values}")
    qdex = config.get("qdex", {})
    if not isinstance(qdex, Mapping):
        failures.append("invalid qdex")
    elif qdex.get("permission_mode") not in {"workspace-write", "yolo"}:
        failures.append(
            f"invalid qdex.permission_mode: {qdex.get('permission_mode')}"
        )
    state_db = config.get("state", {}).get("db")
    if not isinstance(state_db, str) or not state_db:
        failures.append(f"invalid state.db: {state_db}")
    performance = config.get("performance")
    if performance is None:
        # `performance` is optional in config v1. The loader merges its
        # default-safe values before normal command execution, while this
        # validator also remains usable against an older raw v1 document.
        pass
    elif not isinstance(performance, Mapping):
        failures.append("invalid performance")
    else:
        capture = performance.get("capture")
        if capture not in PERFORMANCE_CAPTURE_MODES:
            failures.append(f"invalid performance.capture: {capture}")
        retention_days = performance.get("retention_days")
        if (
            not isinstance(retention_days, int)
            or isinstance(retention_days, bool)
            or retention_days < 1
            or retention_days > 3650
        ):
            failures.append(f"invalid performance.retention_days: {retention_days}")
        max_events = performance.get("max_events")
        if (
            not isinstance(max_events, int)
            or isinstance(max_events, bool)
            or max_events < 1
            or max_events > 5_000_000
        ):
            failures.append(f"invalid performance.max_events: {max_events}")
        if not isinstance(performance.get("query_fingerprints"), bool):
            failures.append(
                "invalid performance.query_fingerprints: "
                f"{performance.get('query_fingerprints')}"
            )
    if config.get("learning", {}).get("mode") not in {"stage_only", "disabled"}:
        failures.append(f"invalid learning.mode: {config.get('learning', {}).get('mode')}")
    if config.get("learning", {}).get("default_backend") not in {"mock", "codex"}:
        failures.append(f"invalid learning.default_backend: {config.get('learning', {}).get('default_backend')}")
    orchestration = config.get("orchestration", {})
    if normalize_manager_mode(orchestration.get("mode")) not in set(MANAGER_MODE_ORDER):
        failures.append(f"invalid orchestration.mode: {config.get('orchestration', {}).get('mode')}")
    if orchestration.get("manager_deploy_policy", "auto") not in MANAGER_DEPLOY_POLICIES:
        failures.append(f"invalid orchestration.manager_deploy_policy: {orchestration.get('manager_deploy_policy')}")
    local_subagents = orchestration.get("local_subagents", {})
    if not isinstance(local_subagents, Mapping):
        failures.append("invalid orchestration.local_subagents")
    else:
        if not isinstance(local_subagents.get("enabled"), bool):
            failures.append(f"invalid orchestration.local_subagents.enabled: {local_subagents.get('enabled')}")
    kaveman = orchestration.get("kaveman", {})
    if not isinstance(kaveman, Mapping):
        failures.append("invalid orchestration.kaveman")
    else:
        if not isinstance(kaveman.get("enabled"), bool):
            failures.append(f"invalid orchestration.kaveman.enabled: {kaveman.get('enabled')}")
        if not isinstance(kaveman.get("directive"), str) or not kaveman.get("directive"):
            failures.append(f"invalid orchestration.kaveman.directive: {kaveman.get('directive')}")
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
            profile_max = profile.get("max_subagents")
            minimum = 0 if mode == "off" else 1
            if not isinstance(profile_max, int) or profile_max < minimum or profile_max > MANAGER_MAX_SUBAGENTS_LIMIT:
                failures.append(f"invalid orchestration.mode_profiles.{mode}.max_subagents: {profile_max}")
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
            threshold = stale_thresholds.get(mode)
            if not isinstance(threshold, int) or threshold < 5 or threshold > 240:
                failures.append(f"invalid orchestration.stale_session_thresholds_minutes.{mode}: {threshold}")
    seats = config.get("seats", {})
    expected_seats = set(DEFAULT_CONFIG["seats"])
    failures.extend(f"unknown seat: {seat}" for seat in sorted(set(seats) - expected_seats))
    for seat_name, seat_config in seats.items() if isinstance(seats, Mapping) else ():
        if not isinstance(seat_config, Mapping):
            failures.append(f"invalid seat config: {seat_name}")
            continue
        allowed_seat_keys = {
            "authority",
            "backend",
            "context_window",
            "compact_limit",
            "guard_profile",
        }
        if seat_name in {"primary", "audit", "release"}:
            allowed_seat_keys.add("model")
        for key in sorted(set(seat_config) - allowed_seat_keys):
            failures.append(f"unknown seats.{seat_name} key: {key}")
        expected_seat = DEFAULT_CONFIG["seats"].get(seat_name, {})
        expected_authority = expected_seat.get("authority")
        if expected_authority and seat_config.get("authority") != expected_authority:
            failures.append(
                f"invalid seats.{seat_name}.authority: {seat_config.get('authority')}"
            )
        expected_backend = expected_seat.get("backend")
        if expected_backend and seat_config.get("backend") != expected_backend:
            failures.append(
                f"invalid seats.{seat_name}.backend: {seat_config.get('backend')}"
            )
        context_window = seat_config.get("context_window")
        compact_limit = seat_config.get(
            "compact_limit", config.get("context", {}).get("compact_limit")
        )
        if (
            not isinstance(context_window, int)
            or isinstance(context_window, bool)
            or context_window < 1024
        ):
            failures.append(
                f"invalid seats.{seat_name}.context_window: {context_window}"
            )
        if (
            not isinstance(compact_limit, int)
            or isinstance(compact_limit, bool)
            or compact_limit < 1024
            or (
                isinstance(context_window, int)
                and not isinstance(context_window, bool)
                and compact_limit >= context_window
            )
        ):
            failures.append(
                f"invalid seats.{seat_name}.compact_limit: {compact_limit}"
            )
    for seat in ("primary", "qwen", "audit", "release", "sandbox"):
        if seat not in seats:
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


def performance_config(config: Mapping[str, Any]) -> dict[str, Any]:
    values = config.get("performance", {})
    return {
        "capture": str(values.get("capture") or "off"),
        "retention_days": int(values.get("retention_days") or 14),
        "max_events": int(values.get("max_events") or 50_000),
        "query_fingerprints": bool(values.get("query_fingerprints", True)),
    }


def performance_db_path(config: Mapping[str, Any], *, env: Mapping[str, str] | None = None) -> Path:
    """Resolve the isolated local telemetry store without sharing Manager state."""
    _ = config
    source = os.environ if env is None else env
    raw = str(source.get(PERFORMANCE_DB_ENV) or "").strip()
    if not raw:
        raw = qwendex_dev_paths_from_codex_home(source).get("performance_db") or str(DEFAULT_PERFORMANCE_DB)
    path = Path(raw).expanduser()
    return path if path.is_absolute() else ROOT / path


def performance_repository_scope_digest(
    repo: Path | str | None = None,
    *,
    event: Mapping[str, Any] | None = None,
    env: Mapping[str, str] | None = None,
) -> str:
    root = canonical_manager_repo_root(repo, event=event, env=env)
    return "sha256:" + sha256_text(root)


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


def local_qwen_base_url(config: Mapping[str, Any]) -> str:
    probe_url = str(config.get("routing", {}).get("local_probe_url") or "").strip()
    parsed = urllib.parse.urlsplit(probe_url)
    suffix = "/v1/models"
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.query
        or parsed.fragment
        or not parsed.path.endswith(suffix)
    ):
        raise ValueError(
            "routing.local_probe_url must end with /v1/models and contain no query or fragment"
        )
    base_path = parsed.path[: -len(suffix)].rstrip("/")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, base_path, "", ""))


def seat_uses_local_qwen(config: Mapping[str, Any], seat: str) -> bool:
    seat_config = config.get("seats", {}).get(seat, {})
    if not isinstance(seat_config, Mapping):
        return False
    return (
        str(seat_config.get("backend") or "") == "local-responses-adapter"
        or str(seat_config.get("model") or "") == routing_policy(config)["local_model"]
    )


def seat_runtime_model(config: Mapping[str, Any], seat: str) -> str:
    if seat_uses_local_qwen(config, seat):
        return routing_policy(config)["local_model"]
    return str(config.get("seats", {}).get(seat, {}).get("model", ""))


def authority_fallback_seat(config: Mapping[str, Any]) -> str:
    seats = config.get("seats", {})
    candidate = routing_policy(config)["fallback_seat"]
    if candidate in seats and not seat_uses_local_qwen(config, candidate):
        return candidate
    for seat in ("primary", "audit", "release"):
        if seat in seats and not seat_uses_local_qwen(config, seat):
            return seat
    return str(config.get("default_seat") or "primary")


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


def attach_local_routing_snapshot(
    policy: Mapping[str, Any],
    config: Mapping[str, Any],
    *,
    enabled: bool,
) -> dict[str, Any]:
    """Seal launch-relevant local routing configuration into AgentPolicy."""
    eligibility = config.get("orchestration", {}).get("local_qwen_eligibility", {})
    updated = dict(policy)
    updated["local_routing_snapshot"] = {
        "schema_version": "qwendex.local_routing_snapshot.v1",
        "enabled": bool(enabled),
        "routing": routing_policy(config),
        "eligibility": dict(eligibility) if isinstance(eligibility, Mapping) else {},
    }
    updated.pop("policy_hash", None)
    updated["policy_hash"] = agent_policy_hash(updated)
    updated["env"] = agent_policy_env(updated)
    return updated


def attach_native_proactive_source(
    policy: Mapping[str, Any],
    *,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Seal the supported native proactive source into launch policy hashing."""
    source_env = os.environ if env is None else env
    native_source = str(source_env.get("QWENDEX_NATIVE_PROACTIVE_SOURCE") or "").strip()
    updated = dict(policy)
    if native_source != "native_ultra":
        return updated
    updated["native_proactive_source"] = native_source
    updated.pop("policy_hash", None)
    updated["policy_hash"] = agent_policy_hash(updated)
    updated["env"] = agent_policy_env(updated)
    return updated


def qdex_launch_mode() -> str:
    mode = normalize_manager_mode(os.environ.get(QDEX_LAUNCH_MODE_ENV) or "")
    return mode if mode in MANAGER_MODE_ORDER else ""


def qdex_launch_local_enabled() -> bool | None:
    return normalize_local_toggle(os.environ.get(QDEX_LAUNCH_LOCAL_ENABLED_ENV))


def session_turn_policy_projection(
    config: Mapping[str, Any],
    conn: sqlite3.Connection,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Resolve requested controls into the policy usable by the current launch.

    A Qdex process has immutable native capacity and local-routing inputs. Its
    Kaveman output policy is intentionally refreshed only when a new root turn
    is accepted. This projection makes both boundaries explicit instead of
    presenting a requested TUI value as an already-active runtime policy.
    """
    requested_mode = current_manager_mode(config, conn)
    requested_kaveman = current_kaveman_enabled(config, conn)
    requested_local_enabled = current_local_enabled(config, conn)
    requested_policy = resolve_agent_policy(
        config,
        selected_manager_mode=requested_mode,
        kaveman_enabled=requested_kaveman,
    )
    requested_policy = attach_local_routing_snapshot(
        requested_policy,
        config,
        enabled=requested_local_enabled,
    )
    requested_policy = attach_native_proactive_source(requested_policy)

    launch_mode = qdex_launch_mode()
    launch_local_enabled = qdex_launch_local_enabled()
    effective_mode = launch_mode or str(requested_policy.get("mode") or requested_mode)
    if launch_local_enabled is None:
        launch_local_enabled = requested_local_enabled
    mode_restart_required = bool(launch_mode and effective_mode != requested_mode)
    local_restart_required = bool(
        manager_session_state_path() is not None
        and qdex_launch_local_enabled() is not None
        and launch_local_enabled != requested_local_enabled
    )

    if effective_mode == str(requested_policy.get("mode") or "") and not local_restart_required:
        effective_policy = requested_policy
    else:
        effective_policy = resolve_agent_policy(
            config,
            selected_manager_mode=effective_mode,
            kaveman_enabled=requested_kaveman,
            env={},
            selector_source_override="qwendex-launch-snapshot",
        )
        effective_policy = attach_local_routing_snapshot(
            effective_policy,
            config,
            enabled=bool(launch_local_enabled),
        )
        effective_policy = attach_native_proactive_source(effective_policy)

    transition = {
        "scope": "per_launch_session" if manager_session_state_path() is not None else "repository_default",
        "requested_mode": requested_mode,
        "requested_policy_hash": str(requested_policy.get("policy_hash") or ""),
        "effective_turn_mode": str(effective_policy.get("mode") or ""),
        "launch_mode": launch_mode or None,
        "requested_local_enabled": requested_local_enabled,
        "effective_local_enabled": bool(launch_local_enabled),
        "kaveman_enabled": requested_kaveman,
        "mode_restart_required": mode_restart_required,
        "local_restart_required": local_restart_required,
        "restart_required": mode_restart_required or local_restart_required,
        "kaveman_applies_at": "next_user_prompt" if manager_session_state_path() is not None else "immediate",
        "mode_applies_at": "next_qdex_launch" if mode_restart_required else "next_user_prompt",
    }
    return effective_policy, transition


def manager_session_policy_surface(
    config: Mapping[str, Any],
    conn: sqlite3.Connection,
    *,
    selected_manager_mode: str = "",
    cli_agent_use: str = "",
    kaveman_enabled: bool | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any] | None]:
    """Return requested and usable policy data for a Manager status surface.

    Normal Qdex controls are constrained by the launch snapshot. Explicit CLI
    selectors remain one-command inspection inputs and intentionally do not
    claim to mutate that running launch.
    """
    current_mode = current_manager_mode(config, conn)
    requested_mode = normalize_manager_mode(selected_manager_mode) or current_mode
    requested_kaveman = (
        current_kaveman_enabled(config, conn)
        if kaveman_enabled is None
        else bool(kaveman_enabled)
    )
    requested_local_enabled = current_local_enabled(config, conn)
    requested_policy = resolve_agent_policy(
        config,
        cli_agent_use=cli_agent_use,
        selected_manager_mode=requested_mode,
        kaveman_enabled=requested_kaveman,
    )
    requested_policy = attach_local_routing_snapshot(
        requested_policy,
        config,
        enabled=requested_local_enabled,
    )
    requested_policy = attach_native_proactive_source(requested_policy)
    session_state = manager_session_control_state(config, conn) or {}
    accepted = session_state.get("accepted_turn")
    accepted_turn = dict(accepted) if isinstance(accepted, Mapping) else None

    # A one-command selector must not be reported as the live Qdex policy.
    # Likewise, an explicit manager-mode request is an inspection override.
    explicit_override = bool(cli_agent_use) or requested_mode != current_mode
    if explicit_override:
        transition = {
            "scope": "command_override",
            "requested_mode": str(requested_policy.get("mode") or requested_mode),
            "requested_policy_hash": str(requested_policy.get("policy_hash") or ""),
            "effective_turn_mode": str(requested_policy.get("mode") or requested_mode),
            "launch_mode": qdex_launch_mode() or None,
            "requested_local_enabled": requested_local_enabled,
            "effective_local_enabled": requested_local_enabled,
            "kaveman_enabled": requested_kaveman,
            "mode_restart_required": False,
            "local_restart_required": False,
            "restart_required": False,
            "kaveman_applies_at": "command_invocation",
            "mode_applies_at": "command_invocation",
        }
        return requested_policy, requested_policy, transition, accepted_turn

    effective_policy, transition = session_turn_policy_projection(config, conn)
    return requested_policy, effective_policy, transition, accepted_turn


def manager_decision_local_status(
    config: Mapping[str, Any],
    decision: Mapping[str, Any],
) -> dict[str, Any]:
    """Recover immutable prompt-routing state from the launch decision."""
    policy_snapshot = decision.get("policy_snapshot", {})
    if not isinstance(policy_snapshot, Mapping):
        policy_snapshot = {}
    local_snapshot = policy_snapshot.get("local_routing_snapshot", {})
    if not isinstance(local_snapshot, Mapping):
        local_snapshot = {}
    local_routing = local_snapshot.get("routing", {})
    if not isinstance(local_routing, Mapping):
        local_routing = {}
    enabled = bool(local_snapshot.get("enabled", decision.get("local_enabled")))
    usable = enabled and bool(decision.get("local_usable"))
    return {
        "enabled": enabled,
        "available": usable,
        "usable": usable,
        "local_enabled": enabled,
        "local_available": usable,
        "local_usable": usable,
        "local_state": "ready" if usable else ("unavailable" if enabled else "off"),
        "source": "manager_launch_snapshot",
        "reason": "immutable_launch_snapshot",
        "model": str(local_routing.get("local_model") or routing_policy(config)["local_model"]),
    }


def risk_rank(risk: str) -> int:
    return {"low": 1, "medium": 2, "high": 3}.get(risk.strip().lower(), 2)


def text_has_any_term(text: str, terms: tuple[str, ...]) -> bool:
    normalized = text.lower()
    for term in terms:
        escaped = re.escape(term).replace(r"\ ", r"\s+")
        if re.search(rf"(?<![A-Za-z0-9_]){escaped}(?![A-Za-z0-9_])", normalized):
            return True
    return False


def infer_task_class(prompt: str) -> str:
    text = prompt.lower()
    if text_has_any_term(text, ("security", "credential", "credentials", "auth", "authentication", "threat")):
        return "security"
    if text_has_any_term(text, ("release", "publish", "ship", "acceptance")):
        return "release acceptance"
    if text_has_any_term(text, ("architecture", "protocol", "migration", "schema")):
        return "architecture"
    if text_has_any_term(
        text,
        (
            "public doc",
            "public docs",
            "public documentation",
            "public readme",
            "public-facing doc",
            "public-facing documentation",
            "public-facing readme",
            "public claim",
            "public claims",
            "release-facing doc",
        ),
    ):
        return "public docs claims"
    if text_has_any_term(text, ("receipt", "receipts", "artifact", "artifacts", "summarize", "summary")):
        return "artifact summary"
    if text_has_any_term(text, ("doc", "docs", "documentation", "readme", "typo", "copy")):
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
        selected_reasoning = estimator["default_reasoning"]
        source = "default_policy"
        escalation = ""
    selected_model = (
        str(config.get("routing", {}).get("local_model", "qwen-local"))
        if local_usable
        else estimator["recommendation_model"]
    )
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
    primary_required = task_class_matches(task_class, policy["primary_required_for_task_classes"]) or text_contains_any(
        task_class,
        policy["primary_required_for_task_classes"],
    )
    fallback_seat = authority_fallback_seat(config)
    if requested != "auto":
        seat = requested if requested in seats else str(config.get("default_seat", "primary"))
        explicit_local_qwen = seat_uses_local_qwen(config, seat)
        if explicit_local_qwen and primary_required:
            seat = fallback_seat
            reason = "primary_authority_required"
            reasoning_source = "primary_authority_policy"
        elif explicit_local_qwen and not bool(local_status.get("enabled")):
            seat = fallback_seat
            reason = "local_subagents_disabled"
            reasoning_source = "fallback_policy"
        else:
            reason = "explicit_seat"
            reasoning_source = "explicit_seat"
        return {
            "requested_seat": requested,
            "seat": seat,
            "model": seat_runtime_model(config, seat),
            "selected_model": seat_runtime_model(config, seat),
            "selected_reasoning": "user-selected",
            "reasoning_source": reasoning_source,
            "escalation_reason": "",
            "token_saver_used": False,
            "local_qwen_eligible": explicit_local_qwen and bool(local_status.get("enabled")) and not primary_required,
            "task_class": task_class,
            "reason": reason,
            "local_qwen": {
                "available": None,
                "source": "not_probed",
                "model": policy["local_model"],
                "reason": reason,
            },
            "local_subagents": local_status,
            "routing": policy,
        }
    if policy["mode"] == "primary_only":
        seat = fallback_seat
        return {
            "requested_seat": requested,
            "seat": seat,
            "model": seat_runtime_model(config, seat),
            "selected_model": seat_runtime_model(config, seat),
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
        local_default = seat_uses_local_qwen(config, seat)
        if local_default and (primary_required or not bool(local_status.get("enabled"))):
            seat = fallback_seat
            manual_reason = "primary_authority_required" if primary_required else "local_subagents_disabled"
        else:
            manual_reason = "routing_manual_default"
        return {
            "requested_seat": requested,
            "seat": seat,
            "model": seat_runtime_model(config, seat),
            "selected_model": seat_runtime_model(config, seat),
            "selected_reasoning": "medium",
            "reasoning_source": "primary_authority_policy" if manual_reason == "primary_authority_required" else "routing_manual_default",
            "escalation_reason": "",
            "token_saver_used": False,
            "local_qwen_eligible": local_default and bool(local_status.get("enabled")) and not primary_required,
            "task_class": task_class,
            "reason": manual_reason,
            "local_qwen": {"available": None, "source": "not_probed", "model": policy["local_model"], "reason": manual_reason},
            "local_subagents": local_status,
            "routing": policy,
        }
    local_intent = not primary_required and (
        prefer_local
        or (
            policy["prefer_local_qwen_when_available"]
            and task_class_matches(task_class, policy["prefer_for_task_classes"])
        )
    )
    should_prefer_local = bool(local_status["enabled"]) and local_intent
    local_qwen = probe_local_qwen(config, env=env) if should_prefer_local else {
        "available": None,
        "source": "not_probed",
        "model": policy["local_model"],
        "reason": (
            "primary_authority_required"
            if primary_required
            else "local_subagents_disabled"
            if local_intent
            else "task_class_not_preferred"
        ),
    }
    seat = "qwen" if should_prefer_local and local_qwen.get("available") else fallback_seat
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
        "model": seat_runtime_model(config, seat),
        "selected_model": seat_runtime_model(config, seat),
        "selected_reasoning": "low" if seat == "qwen" else "medium",
        "reasoning_source": (
            "local_qwen_token_saver"
            if seat == "qwen"
            else "primary_authority_policy"
            if primary_required
            else "fallback_policy"
        ),
        "escalation_reason": "",
        "token_saver_used": seat == "qwen",
        "local_qwen_eligible": should_prefer_local,
        "task_class": task_class,
        "reason": (
            "local_qwen_available"
            if seat == "qwen"
            else "primary_authority_required"
            if primary_required
            else "local_subagents_disabled"
            if disabled_by_toggle
            else "fallback_seat"
        ),
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


def scoped_storage_id(kind: str, repo_root: str, public_id: str) -> str:
    digest = sha256_text(f"{repo_root}\0{public_id}")
    return f"{kind}_row_{digest}"


def codex_home_from_env(env: Mapping[str, str] | None = None) -> Path:
    source = env or os.environ
    raw = str(source.get("CODEX_HOME") or Path.home() / ".codex")
    return Path(raw).expanduser()


def path_digest_policy(path: Path) -> str:
    return "sha256:" + sha256_text(str(path.expanduser().resolve(strict=False)))


def manager_runtime_identity(env: Mapping[str, str] | None = None) -> str:
    """Return the immutable generation binding for one Qdex launch.

    Legacy/non-generated fixtures retain the path identity for compatibility.
    A real generated Qdex launch binds both the generation id and the sealed
    source/patch/binary/config contract, so source edits and later activation
    cannot change the code executed by an attached session.
    """
    source = os.environ if env is None else env
    generation_id = str(source.get("QWENDEX_RUNTIME_GENERATION_ID") or "").strip()
    contract_sha256 = str(source.get("QWENDEX_RUNTIME_CONTRACT_SHA256") or "").strip()
    if generation_id and contract_sha256:
        return f"generation:{generation_id}:sha256:{contract_sha256}"
    return path_digest_policy(Path(__file__).resolve())


def manager_runtime_generation_metadata(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    source = os.environ if env is None else env
    generation_id = str(source.get("QWENDEX_RUNTIME_GENERATION_ID") or "").strip()
    hook_generation = str(source.get("QWENDEX_HOOK_GENERATION") or generation_id).strip()
    contract_sha256 = str(source.get("QWENDEX_RUNTIME_CONTRACT_SHA256") or "").strip()
    generation_dir = Path(str(source.get("QWENDEX_RUNTIME_GENERATION_DIR") or "")).expanduser()
    manifest: dict[str, Any] = {}
    if generation_id and generation_dir.is_dir():
        manifest_path = generation_dir / "generation.json"
        try:
            loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and loaded.get("generation_id") == generation_id:
                manifest = loaded
        except (OSError, json.JSONDecodeError):
            manifest = {}
    codex = manifest.get("codex") if isinstance(manifest.get("codex"), Mapping) else {}
    contract = manifest.get("contract") if isinstance(manifest.get("contract"), Mapping) else {}
    return {
        "runtime_generation": generation_id,
        "hook_generation": hook_generation,
        "runtime_contract_sha256": contract_sha256,
        "patched_binary_sha256": str(codex.get("binary_sha256") or contract.get("patched_binary_sha256") or ""),
        "codex_patch_sha256": str(codex.get("patch_sha256") or contract.get("codex_patch_sha256") or ""),
        "config_sha256": str(manifest.get("config_digest") or contract.get("config_sha256") or ""),
        "runtime_state_schema_version": int(contract.get("state_schema_version") or 0),
    }


def manager_store_identities(config: Mapping[str, Any]) -> tuple[str, str]:
    return (
        path_digest_policy(state_db_path(config)),
        path_digest_policy(configured_ledger_path(config)),
    )


def manager_launch_key(
    *,
    repo_root: str,
    launch_pid: int,
    launch_start_ticks: str,
    launch_nonce: str,
    codex_home_identity: str,
    state_db_identity: str,
    runtime_identity: str,
) -> str:
    material = {
        "repo_root": repo_root,
        "launch_pid": launch_pid,
        "launch_start_ticks": launch_start_ticks,
        "launch_nonce": launch_nonce,
        "codex_home_identity": codex_home_identity,
        "state_db_identity": state_db_identity,
        "runtime_identity": runtime_identity,
    }
    return "sha256:" + sha256_text(json.dumps(material, sort_keys=True, separators=(",", ":")))


def prompt_digest_and_summary(prompt: str, *, known: bool) -> tuple[str, str]:
    if not known:
        return "", MANAGER_PROMPT_UNKNOWN_SUMMARY
    # Manager receipts are metadata-only. Never persist even a redacted prompt
    # excerpt here: redaction is not a dependable privacy boundary for operator
    # text, commands, paths, or credentials.
    return sha256_text(prompt), "privacy_safe_prompt_metadata"


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


def canonical_manager_repo_root(
    repo: Path | str | None = None,
    *,
    event: Mapping[str, Any] | None = None,
    env: Mapping[str, str] | None = None,
) -> str:
    source = os.environ if env is None else env
    event_cwd = str((event or {}).get("cwd") or "").strip()
    raw = str(
        repo
        or event_cwd
        or source.get("QWENDEX_MANAGER_TARGET_REPO")
        or source.get("QWENDEX_EXEC_CWD")
        or os.getcwd()
    ).strip()
    candidate = Path(raw or os.getcwd()).expanduser().resolve(strict=False)
    try:
        result = subprocess.run(
            ["git", "-C", str(candidate), "rev-parse", "--show-toplevel"],
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            candidate = Path(result.stdout.strip()).expanduser().resolve(strict=False)
    except (OSError, subprocess.SubprocessError):
        pass
    return str(candidate)


def sessions_for_repo(sessions: list[dict[str, Any]], repo_root: str) -> list[dict[str, Any]]:
    expected = str(Path(repo_root).expanduser().resolve(strict=False)) if repo_root else ""
    return [
        session
        for session in sessions
        if str(session.get("repo_root") or "") == expected
    ]


def manager_receipt_path(config: Mapping[str, Any], ledger_id: str) -> Path:
    return results_root(config) / "manager" / f"{safe_artifact_component(ledger_id, 'manager_decision')}.json"


def write_manager_decision_receipt(config: Mapping[str, Any], payload: Mapping[str, Any]) -> str:
    ledger_id = str(payload.get("ledger_id") or "manager_decision")
    path = manager_receipt_path(config, ledger_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    receipt = dict(redact_obj(payload))
    receipt["manager_schema_version"] = int(receipt.get("schema_version") or 1)
    receipt["schema_version"] = "qwendex.manager_decision.v1"
    receipt["version"] = VERSION
    receipt["run_id"] = ledger_id
    receipt["started_at"] = str(
        receipt.get("timestamp_created") or receipt.get("timestamp") or utc_now()
    )
    receipt["repo_root"] = str(receipt.get("repo_root") or "")
    receipt["sha256"] = ""
    receipt["sha256"] = digest_json(receipt)
    atomic_write_text(path, json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def state_schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("PRAGMA user_version").fetchone()
    return int(row[0] if row is not None else 0)


def state_has_qwendex_schema(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name LIKE 'qwendex_%'"
    ).fetchone()
    return bool(row and int(row[0] or 0))


def state_migration_directory(path: Path) -> Path:
    return path.parent / "migrations" / path.name


def backup_state_for_migration(
    conn: sqlite3.Connection,
    path: Path,
    *,
    from_version: int,
    to_version: int,
) -> Path | None:
    if not state_has_qwendex_schema(conn):
        return None
    directory = state_migration_directory(path)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    backup_path = directory / f"state-v{from_version}-to-v{to_version}-{stamp}.sqlite"
    backup_conn = sqlite3.connect(backup_path)
    try:
        conn.backup(backup_conn)
    finally:
        backup_conn.close()
    backup_path.chmod(0o600)
    return backup_path


def write_state_migration_failure(
    path: Path,
    *,
    from_version: int,
    backup_path: Path | None,
    error: BaseException,
) -> None:
    directory = state_migration_directory(path)
    directory.mkdir(parents=True, exist_ok=True)
    run_id = str(os.environ.get("QWENDEX_RUN_ID") or "unbound").strip()
    fingerprint = sha256_text(
        "\0".join(
            [
                str(from_version),
                str(STATE_SCHEMA_VERSION),
                type(error).__name__,
                str(error),
                str(os.environ.get(STATE_MIGRATION_FAULT_ENV) or ""),
                run_id,
            ]
        )
    )[:20]
    receipt_path = directory / f"migration-failed-{fingerprint}.json"
    previous: dict[str, Any] = {}
    try:
        loaded = json.loads(receipt_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            previous = loaded
    except (OSError, json.JSONDecodeError):
        previous = {}
    payload = {
        "schema_version": "qwendex.state_migration_failure.v1",
        "generated_at": utc_now(),
        "first_generated_at": str(previous.get("first_generated_at") or previous.get("generated_at") or utc_now()),
        "occurrences": int(previous.get("occurrences") or 0) + 1,
        "run_id": run_id,
        "status": "blocked",
        "from_version": from_version,
        "target_version": STATE_SCHEMA_VERSION,
        "backup_path": str(backup_path or ""),
        "error_type": type(error).__name__,
        "error": str(error),
        "recovery": "Restore the preserved backup or repair the database from stock Codex or a shell before retrying.",
    }
    atomic_write_text(receipt_path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
    receipt_path.chmod(0o600)


def connect_state(config: Mapping[str, Any]) -> sqlite3.Connection:
    path = state_db_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=STATE_BUSY_TIMEOUT_MS / 1000)
    conn.row_factory = sqlite3.Row
    backup_path: Path | None = None
    from_version = 0
    try:
        conn.execute(f"PRAGMA busy_timeout = {STATE_BUSY_TIMEOUT_MS}")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        from_version = state_schema_version(conn)
        if from_version > STATE_SCHEMA_VERSION:
            raise RuntimeError(
                f"state schema v{from_version} is newer than supported v{STATE_SCHEMA_VERSION}"
            )
        if from_version < STATE_SCHEMA_VERSION:
            quick_check = conn.execute("PRAGMA quick_check").fetchone()
            if not quick_check or str(quick_check[0]) != "ok":
                raise RuntimeError(f"state database integrity check failed: {quick_check[0] if quick_check else 'no result'}")
            backup_path = backup_state_for_migration(
                conn,
                path,
                from_version=from_version,
                to_version=STATE_SCHEMA_VERSION,
            )
            if os.environ.get(STATE_MIGRATION_FAULT_ENV) == "after_backup":
                raise RuntimeError("injected state migration failure after backup")
        ensure_state_schema(conn, backup_path=backup_path)
    except BaseException as exc:
        if conn.in_transaction:
            conn.rollback()
        conn.close()
        try:
            write_state_migration_failure(
                path,
                from_version=from_version,
                backup_path=backup_path,
                error=exc,
            )
        except OSError:
            pass
        raise
    return conn


def begin_immediate(conn: sqlite3.Connection) -> str:
    """Start a bounded serialized write transaction, returning a busy error if unavailable."""
    if conn.in_transaction:
        return ""
    try:
        conn.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError as exc:
        message = str(exc)
        if "locked" in message.lower() or "busy" in message.lower():
            return message
        raise
    return ""


def ensure_table_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def repair_legacy_scoped_public_ids(conn: sqlite3.Connection) -> None:
    """Finish the prior lazy public-id backfill without rerunning DDL."""
    tables = {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN ('qwendex_handoffs', 'qwendex_evidence')"
        ).fetchall()
    }
    if tables != {"qwendex_handoffs", "qwendex_evidence"}:
        return
    handoff_debt = conn.execute(
        "SELECT 1 FROM qwendex_handoffs WHERE public_id = '' LIMIT 1"
    ).fetchone()
    evidence_debt = conn.execute(
        "SELECT 1 FROM qwendex_evidence WHERE public_id = '' LIMIT 1"
    ).fetchone()
    if not handoff_debt and not evidence_debt:
        return
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("UPDATE qwendex_handoffs SET public_id = handoff_id WHERE public_id = ''")
    conn.execute("UPDATE qwendex_evidence SET public_id = evidence_id WHERE public_id = ''")
    conn.commit()


def ensure_state_schema(conn: sqlite3.Connection, *, backup_path: Path | None = None) -> None:
    current_version = state_schema_version(conn)
    if current_version == STATE_SCHEMA_VERSION:
        repair_legacy_scoped_public_ids(conn)
        return
    if current_version > STATE_SCHEMA_VERSION:
        raise RuntimeError(
            f"state schema v{current_version} is newer than supported v{STATE_SCHEMA_VERSION}"
        )
    conn.execute("BEGIN IMMEDIATE")
    # Another process may have completed the migration while this connection
    # waited for the writer lock.
    current_version = state_schema_version(conn)
    if current_version == STATE_SCHEMA_VERSION:
        conn.commit()
        return
    schema_sql = """
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
          close_receipt TEXT NOT NULL,
          context_packet_json TEXT NOT NULL DEFAULT '{}',
          routing_json TEXT NOT NULL DEFAULT '{}',
          validation_status TEXT NOT NULL DEFAULT 'pending',
          repo_root TEXT NOT NULL DEFAULT '',
          session_id TEXT NOT NULL DEFAULT '',
          turn_id TEXT NOT NULL DEFAULT '',
          assignment TEXT NOT NULL DEFAULT '',
          policy_hash TEXT NOT NULL DEFAULT '',
          origin TEXT NOT NULL DEFAULT 'qwendex',
          final_report_present INTEGER NOT NULL DEFAULT 0,
          completed_at TEXT NOT NULL DEFAULT ''
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
          unresolved_risks_json TEXT NOT NULL,
          qdex_permission_mode TEXT NOT NULL DEFAULT 'workspace-write',
          qdex_permission_source TEXT NOT NULL DEFAULT 'default'
        );
        CREATE TABLE IF NOT EXISTS qwendex_state_migrations (
          migration_id TEXT PRIMARY KEY,
          from_version INTEGER NOT NULL,
          to_version INTEGER NOT NULL,
          status TEXT NOT NULL,
          backup_path TEXT NOT NULL,
          started_at TEXT NOT NULL,
          completed_at TEXT NOT NULL
        );
        """
    for statement in schema_sql.split(";"):
        if statement.strip():
            conn.execute(statement)
    ensure_table_column(conn, "qwendex_agent_sessions", "context_packet_json", "TEXT NOT NULL DEFAULT '{}'")
    ensure_table_column(conn, "qwendex_agent_sessions", "routing_json", "TEXT NOT NULL DEFAULT '{}'")
    ensure_table_column(conn, "qwendex_agent_sessions", "validation_status", "TEXT NOT NULL DEFAULT 'pending'")
    ensure_table_column(conn, "qwendex_agent_sessions", "repo_root", "TEXT NOT NULL DEFAULT ''")
    ensure_table_column(conn, "qwendex_manager_decisions", "repo_root", "TEXT NOT NULL DEFAULT ''")
    ensure_table_column(conn, "qwendex_manager_decisions", "launch_ledger_id", "TEXT NOT NULL DEFAULT ''")
    ensure_table_column(conn, "qwendex_manager_decisions", "turn_id", "TEXT NOT NULL DEFAULT ''")
    ensure_table_column(conn, "qwendex_manager_decisions", "agent_task_id", "TEXT NOT NULL DEFAULT ''")
    ensure_table_column(conn, "qwendex_manager_decisions", "launch_pid", "INTEGER NOT NULL DEFAULT 0")
    ensure_table_column(conn, "qwendex_manager_decisions", "launch_start_ticks", "TEXT NOT NULL DEFAULT ''")
    ensure_table_column(conn, "qwendex_manager_decisions", "launch_nonce", "TEXT NOT NULL DEFAULT ''")
    ensure_table_column(conn, "qwendex_manager_decisions", "launch_key", "TEXT NOT NULL DEFAULT ''")
    ensure_table_column(conn, "qwendex_manager_decisions", "root_session_id", "TEXT NOT NULL DEFAULT ''")
    ensure_table_column(conn, "qwendex_manager_decisions", "state_db_identity", "TEXT NOT NULL DEFAULT ''")
    ensure_table_column(conn, "qwendex_manager_decisions", "ledger_db_identity", "TEXT NOT NULL DEFAULT ''")
    ensure_table_column(conn, "qwendex_manager_decisions", "runtime_identity", "TEXT NOT NULL DEFAULT ''")
    ensure_table_column(conn, "qwendex_manager_decisions", "runtime_generation", "TEXT NOT NULL DEFAULT ''")
    ensure_table_column(conn, "qwendex_manager_decisions", "hook_generation", "TEXT NOT NULL DEFAULT ''")
    ensure_table_column(conn, "qwendex_manager_decisions", "runtime_contract_sha256", "TEXT NOT NULL DEFAULT ''")
    ensure_table_column(conn, "qwendex_manager_decisions", "patched_binary_sha256", "TEXT NOT NULL DEFAULT ''")
    ensure_table_column(conn, "qwendex_manager_decisions", "codex_patch_sha256", "TEXT NOT NULL DEFAULT ''")
    ensure_table_column(conn, "qwendex_manager_decisions", "config_sha256", "TEXT NOT NULL DEFAULT ''")
    ensure_table_column(conn, "qwendex_manager_decisions", "qdex_permission_mode", "TEXT NOT NULL DEFAULT 'workspace-write'")
    ensure_table_column(conn, "qwendex_manager_decisions", "qdex_permission_source", "TEXT NOT NULL DEFAULT 'default'")
    ensure_table_column(conn, "qwendex_manager_decisions", "runtime_state_schema_version", "INTEGER NOT NULL DEFAULT 0")
    ensure_table_column(conn, "qwendex_manager_decisions", "selected_mode", "TEXT NOT NULL DEFAULT ''")
    ensure_table_column(conn, "qwendex_manager_decisions", "effective_turn_mode", "TEXT NOT NULL DEFAULT ''")
    ensure_table_column(conn, "qwendex_manager_decisions", "task_class", "TEXT NOT NULL DEFAULT ''")
    ensure_table_column(conn, "qwendex_manager_decisions", "agent_plan_json", "TEXT NOT NULL DEFAULT '{}'")
    ensure_table_column(conn, "qwendex_manager_decisions", "policy_snapshot_json", "TEXT NOT NULL DEFAULT '{}'")
    ensure_table_column(conn, "qwendex_manager_decisions", "desired_global_policy_hash", "TEXT NOT NULL DEFAULT ''")
    ensure_table_column(conn, "qwendex_manager_decisions", "prompt_source", "TEXT NOT NULL DEFAULT ''")
    ensure_table_column(conn, "qwendex_manager_decisions", "prompt_length", "INTEGER NOT NULL DEFAULT 0")
    ensure_table_column(conn, "qwendex_manager_decisions", "prompt_schema_version", "TEXT NOT NULL DEFAULT ''")
    ensure_table_column(conn, "qwendex_manager_decisions", "admission_error_code", "TEXT NOT NULL DEFAULT ''")
    ensure_table_column(conn, "qwendex_agent_sessions", "session_id", "TEXT NOT NULL DEFAULT ''")
    ensure_table_column(conn, "qwendex_agent_sessions", "turn_id", "TEXT NOT NULL DEFAULT ''")
    ensure_table_column(conn, "qwendex_agent_sessions", "assignment", "TEXT NOT NULL DEFAULT ''")
    ensure_table_column(conn, "qwendex_agent_sessions", "policy_hash", "TEXT NOT NULL DEFAULT ''")
    ensure_table_column(conn, "qwendex_agent_sessions", "origin", "TEXT NOT NULL DEFAULT 'qwendex'")
    ensure_table_column(conn, "qwendex_agent_sessions", "final_report_present", "INTEGER NOT NULL DEFAULT 0")
    ensure_table_column(conn, "qwendex_agent_sessions", "completed_at", "TEXT NOT NULL DEFAULT ''")
    ensure_table_column(conn, "qwendex_agent_sessions", "runtime_generation", "TEXT NOT NULL DEFAULT ''")
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS qwendex_manager_decisions_launch_key
        ON qwendex_manager_decisions(launch_key)
        WHERE launch_key <> '' AND ledger_id = launch_ledger_id
        """
    )
    ensure_table_column(conn, "qwendex_agent_file_locks", "repo_root", "TEXT NOT NULL DEFAULT ''")
    ensure_table_column(conn, "qwendex_context_snapshots", "repo_root", "TEXT NOT NULL DEFAULT ''")
    ensure_table_column(conn, "qwendex_handoffs", "repo_root", "TEXT NOT NULL DEFAULT ''")
    ensure_table_column(conn, "qwendex_evidence", "repo_root", "TEXT NOT NULL DEFAULT ''")
    ensure_table_column(conn, "qwendex_handoffs", "public_id", "TEXT NOT NULL DEFAULT ''")
    ensure_table_column(conn, "qwendex_evidence", "public_id", "TEXT NOT NULL DEFAULT ''")
    conn.execute("UPDATE qwendex_handoffs SET public_id = handoff_id WHERE public_id = ''")
    conn.execute("UPDATE qwendex_evidence SET public_id = evidence_id WHERE public_id = ''")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS qwendex_handoffs_repo_public_id ON qwendex_handoffs(repo_root, public_id)"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS qwendex_evidence_repo_public_id ON qwendex_evidence(repo_root, public_id)"
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS qwendex_agent_sessions_scope_status
        ON qwendex_agent_sessions(repo_root, session_id, turn_id, task_id, status)
        """
    )
    if os.environ.get(STATE_MIGRATION_FAULT_ENV) == "after_schema":
        raise RuntimeError("injected state migration failure after schema changes")
    completed_at = utc_now()
    migration_id = make_id("state_migration")
    conn.execute(
        """
        INSERT INTO qwendex_state_migrations
          (migration_id, from_version, to_version, status, backup_path, started_at, completed_at)
        VALUES (?, ?, ?, 'pass', ?, ?, ?)
        """,
        (
            migration_id,
            current_version,
            STATE_SCHEMA_VERSION,
            str(backup_path or ""),
            completed_at,
            completed_at,
        ),
    )
    conn.execute(f"PRAGMA user_version = {STATE_SCHEMA_VERSION}")
    if os.environ.get(STATE_MIGRATION_FAULT_ENV) == "before_commit":
        raise RuntimeError("injected state migration failure before commit")
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
    data["final_report_present"] = bool(int(data.get("final_report_present") or 0))
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
    for key in ("agent_plan", "policy_snapshot"):
        raw = data.pop(f"{key}_json", "{}")
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = {}
        data[key] = parsed if isinstance(parsed, dict) else {}
    data["root_agent_id"] = manager_decision_root_agent_id(data)
    return data


def latest_manager_decision(
    conn: sqlite3.Connection,
    *,
    repo_root: str,
    ledger_id: str = "",
    session_id: str = "",
    task_id: str = "",
) -> dict[str, Any] | None:
    if ledger_id:
        row = conn.execute(
            "SELECT * FROM qwendex_manager_decisions WHERE ledger_id = ? AND repo_root = ?",
            (ledger_id, repo_root),
        ).fetchone()
        return row_to_manager_decision(row)
    if session_id:
        row = conn.execute(
            """
            SELECT * FROM qwendex_manager_decisions
            WHERE session_id = ? AND repo_root = ?
            ORDER BY timestamp_updated DESC LIMIT 1
            """,
            (session_id, repo_root),
        ).fetchone()
        return row_to_manager_decision(row)
    if task_id:
        row = conn.execute(
            """
            SELECT * FROM qwendex_manager_decisions
            WHERE repo_root = ?
              AND (agent_task_id = ? OR (agent_task_id = '' AND session_id = ?))
            ORDER BY timestamp_updated DESC LIMIT 1
            """,
            (repo_root, task_id, task_id),
        ).fetchone()
        return row_to_manager_decision(row)
    row = conn.execute(
        """
        SELECT * FROM qwendex_manager_decisions
        WHERE repo_root = ?
        ORDER BY timestamp_updated DESC LIMIT 1
        """,
        (repo_root,),
    ).fetchone()
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
    storage_id = str(data.pop("handoff_id", "") or "")
    data["handoff_id"] = str(data.pop("public_id", "") or storage_id)
    data["evidence_refs"] = json_loads_list(data.pop("evidence_refs_json", "[]"))
    data["next_actions"] = json_loads_list(data.pop("next_actions_json", "[]"))
    return data


def row_to_evidence(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row)
    storage_id = str(data.pop("evidence_id", "") or "")
    data["evidence_id"] = str(data.pop("public_id", "") or storage_id)
    return data


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
            "session_id": session.get("session_id", ""),
            "turn_id": session.get("turn_id", ""),
            "assignment": session.get("assignment", ""),
            "policy_hash": session.get("policy_hash", ""),
            "origin": session.get("origin", ""),
            "status": session.get("status", ""),
            "validation_status": session.get("validation_status", ""),
            "final_report_present": bool(session.get("final_report_present")),
            "completed_at": session.get("completed_at", ""),
            "waiver_reason": (
                str((session.get("context_packet") or {}).get("waiver_reason") or "")
                if str(session.get("status") or "") == "waived"
                else ""
            ),
            "attention_flagged": session_attention_flagged(session),
            "raw_output_artifact": artifact_for_kind(artifacts, "/raw-output.md"),
            "compact_report_artifact": artifact_for_kind(artifacts, "/compact-report.json"),
            "aggregate_raw_output_artifact": artifact_for_kind(artifacts, "/raw-agent-output.md"),
            "artifacts": artifacts,
        })
    return outcomes


def normalize_lock_path(value: str, *, repo_root: str = "") -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if raw == MANAGER_ROOT_LOCK_PATH:
        return raw
    path = Path(raw).expanduser()
    if repo_root:
        root = Path(repo_root).expanduser().resolve()
        candidate = path if path.is_absolute() else root / path
        try:
            normalized = candidate.resolve().relative_to(root).as_posix()
        except (OSError, ValueError):
            return ""
        return normalized or MANAGER_ROOT_LOCK_PATH
    if path.is_absolute():
        try:
            return rel(path.resolve())
        except ValueError:
            return str(path.resolve())
    normalized = Path(raw).as_posix()
    return normalized.removeprefix("./")


def event_file_path_values(event: Mapping[str, Any]) -> list[Any]:
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
    return values


def event_file_paths(event: Mapping[str, Any], *, repo_root: str = "") -> list[str]:
    normalized: list[str] = []
    for item in event_file_path_values(event):
        if isinstance(item, Mapping):
            item = item.get("path") or item.get("file") or item.get("file_path") or ""
        path = normalize_lock_path(str(item), repo_root=repo_root)
        if path and path not in normalized:
            normalized.append(path)
    return normalized


def registered_session_lock_paths(session: Mapping[str, Any]) -> list[str]:
    repo_root = str(session.get("repo_root") or "")
    packet = session.get("context_packet")
    exact_files = packet.get("exact_files") if isinstance(packet, Mapping) else []
    if isinstance(exact_files, list) and exact_files:
        paths = [
            normalize_lock_path(str(item), repo_root=repo_root)
            for item in exact_files
        ]
        paths = [path for path in paths if path]
        return list(dict.fromkeys(paths))
    write_surface = str(session.get("write_surface") or "").strip()
    if write_surface in {"", "read-only", "readonly"}:
        return []
    if write_surface == ".qwendex/runs":
        return [".qwendex/runs/<opaque>"]
    if write_surface != "declared-scope":
        normalized = normalize_lock_path(write_surface, repo_root=repo_root)
        return [normalized] if normalized else []
    return [MANAGER_ROOT_LOCK_PATH]


def registered_session_path_allowed(session: Mapping[str, Any], path: str) -> bool:
    repo_root = str(session.get("repo_root") or "")
    normalized = normalize_lock_path(path, repo_root=repo_root)
    if not normalized:
        return False
    packet = session.get("context_packet")
    exact_files = packet.get("exact_files") if isinstance(packet, Mapping) else []
    if isinstance(exact_files, list) and exact_files:
        exact_scopes = {
            normalize_lock_path(str(item), repo_root=repo_root)
            for item in exact_files
        }
        exact_scopes.discard("")
        return normalized in exact_scopes
    if not exact_files and str(session.get("write_surface") or "") == "declared-scope":
        return normalized != MANAGER_ROOT_LOCK_PATH
    for scope in registered_session_lock_paths(session):
        if scope == MANAGER_ROOT_LOCK_PATH:
            return True
        if scope == ".qwendex/runs/<opaque>":
            return scribe_path_allowed(normalized, repo_root=repo_root)
        normalized_scope = normalize_lock_path(scope, repo_root=repo_root).rstrip("/")
        if normalized == normalized_scope or normalized.startswith(f"{normalized_scope}/"):
            return True
    return False


def active_file_locks(conn: sqlite3.Connection, *, repo_root: str = "") -> list[dict[str, Any]]:
    if repo_root:
        rows = conn.execute(
            """
            SELECT * FROM qwendex_agent_file_locks
            WHERE released_at = '' AND (repo_root = ? OR repo_root = '')
            ORDER BY acquired_at, path
            """,
            (repo_root,),
        ).fetchall()
    else:
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


def scribe_path_allowed(path: str, *, repo_root: str = "") -> bool:
    normalized = normalize_lock_path(path, repo_root=repo_root)
    return normalized.startswith(".qwendex/runs/")


def safe_artifact_component(value: str, fallback: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip()).strip(".-")
    return (text[:96] or fallback).strip(".-") or fallback


def safe_native_agent_name(value: str, fallback: str = "worker") -> str:
    """Return one Codex AgentPath segment accepted by native MultiAgentV2."""
    text = re.sub(r"[^a-z0-9_]+", "_", str(value or "").strip().lower()).strip("_")
    fallback_text = re.sub(r"[^a-z0-9_]+", "_", str(fallback or "worker").strip().lower()).strip("_")
    name = (text[:96].rstrip("_") or fallback_text[:96].rstrip("_") or "worker")
    return f"{name}_worker" if name == "root" else name


def manager_root_agent_id(ledger_id: str, session_id: str) -> str:
    identity = safe_artifact_component(ledger_id or session_id, "unattached")
    return f"manager-root-{identity}"


def manager_decision_root_agent_id(decision: Mapping[str, Any]) -> str:
    return manager_root_agent_id(
        str(decision.get("launch_ledger_id") or decision.get("ledger_id") or ""),
        str(decision.get("session_id") or ""),
    )


def manager_root_tool_agent_id(root_agent_id: str, tool_use_id: str) -> str:
    tool_id = safe_artifact_component(tool_use_id, "") if tool_use_id else ""
    return (
        f"{root_agent_id}{MANAGER_ROOT_TOOL_SEPARATOR}{tool_id}"
        if root_agent_id and tool_id
        else root_agent_id
    )


def manager_root_owner_family(agent_id: str) -> str:
    text = str(agent_id or "")
    if text.startswith("manager-root-"):
        return text.split(MANAGER_ROOT_TOOL_SEPARATOR, 1)[0]
    return text


def process_start_ticks(pid: int) -> str:
    if pid <= 0:
        return ""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return ""
    closing = stat.rfind(")")
    fields = stat[closing + 2:].split() if closing >= 0 else []
    return fields[19] if len(fields) > 19 else ""


def process_identity_alive(pid: int, start_ticks: str) -> bool:
    if pid <= 0:
        return False
    current_ticks = process_start_ticks(pid)
    if current_ticks:
        return not start_ticks or current_ticks == str(start_ticks)
    try:
        os.kill(pid, 0)
    except (OSError, ValueError):
        return False
    # On a non-/proc host, a live PID is safer to retain than reclaim.
    return True


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
    repo_root = str(session.get("repo_root") or "").strip()
    if repo_root:
        run_id = f"repo-{sha256_text(repo_root)[:12]}-{run_id}"
    safe_agent_id = safe_artifact_component(agent_id, "agent")
    artifact_root = Path(
        os.environ.get("QWENDEX_AGENT_ARTIFACT_ROOT") or ROOT / ".qwendex"
    ).expanduser().resolve(strict=False)
    run_dir = artifact_root / "runs" / run_id
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
    entry = (
        f"\n## {agent_id} - {session.get('lane', '')} - {now}\n\n"
        f"Raw output: {rel(raw_path)}\n\n"
        f"Compact report: {rel(compact_path)}\n"
    )
    # Multiple SubagentStop hooks can complete together. Serialize the
    # aggregate append so one read-modify-write cycle cannot erase another
    # agent's index entry.
    import fcntl

    with aggregate_path.open("a+", encoding="utf-8") as aggregate:
        fcntl.flock(aggregate.fileno(), fcntl.LOCK_EX)
        aggregate.seek(0, os.SEEK_END)
        if aggregate.tell() == 0:
            aggregate.write("# Raw Agent Outputs\n")
        aggregate.write(entry + "\n")
        aggregate.flush()
        os.fsync(aggregate.fileno())
        fcntl.flock(aggregate.fileno(), fcntl.LOCK_UN)
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
    repo_root: str,
) -> dict[str, Any]:
    normalized_paths = [
        path
        for path in (
            normalize_lock_path(item, repo_root=repo_root)
            for item in paths
        )
        if path
    ]
    if busy_error := begin_immediate(conn):
        return {
            "acquired": [],
            "conflicts": [],
            "active_locks": [],
            "repo_root": repo_root,
            "busy_error": busy_error,
        }
    reclaimed_root_locks = release_reclaimable_manager_root_locks(
        conn,
        repo_root=repo_root,
        now=now,
    )
    active = active_file_locks(conn, repo_root=repo_root)
    conflicts: list[dict[str, Any]] = []
    if lock_type == "write":
        for lock in active:
            lock_agent_id = str(lock.get("agent_id") or "")
            same_root_family = (
                agent_id.startswith("manager-root-")
                and manager_root_owner_family(lock_agent_id)
                == manager_root_owner_family(agent_id)
            )
            if lock_agent_id == agent_id or same_root_family:
                continue
            same_path = lock.get("path") in normalized_paths
            other_writer = lock.get("lock_type") == "write"
            if same_path or other_writer:
                conflicts.append(lock)
    if conflicts:
        return {
            "acquired": [],
            "conflicts": conflicts,
            "active_locks": active,
            "reclaimed_root_locks": reclaimed_root_locks,
        }
    acquired: list[dict[str, Any]] = []
    for path in normalized_paths:
        existing = conn.execute(
            """
            SELECT * FROM qwendex_agent_file_locks
            WHERE agent_id = ? AND path = ? AND lock_type = ? AND released_at = '' AND repo_root = ?
            """,
            (agent_id, path, lock_type, repo_root),
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
            (lock_id, agent_id, path, lock_type, acquired_at, released_at, reason, repo_root)
            VALUES (?, ?, ?, ?, ?, '', ?, ?)
            """,
            (lock_id, agent_id, path, lock_type, now, reason, repo_root),
        )
        row = conn.execute("SELECT * FROM qwendex_agent_file_locks WHERE lock_id = ?", (lock_id,)).fetchone()
        lock = row_to_file_lock(row)
        if lock:
            acquired.append(lock)
    return {
        "acquired": acquired,
        "conflicts": [],
        "active_locks": active_file_locks(conn, repo_root=repo_root),
        "repo_root": repo_root,
        "reclaimed_root_locks": reclaimed_root_locks,
    }


def file_lock_summary(config: Mapping[str, Any]) -> dict[str, Any]:
    repo_root = canonical_manager_repo_root()
    try:
        with connect_state(config) as conn:
            active = active_file_locks(conn, repo_root=repo_root)
            all_active = active_file_locks(conn)
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
        "repo_root": repo_root,
        "ledger_active_count": len(all_active),
        "legacy_unscoped_count": sum(1 for lock in all_active if not lock.get("repo_root")),
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


def manager_session_state_path() -> Path | None:
    raw = str(os.environ.get(MANAGER_SESSION_STATE_FILE_ENV) or "").strip()
    return Path(raw).expanduser() if raw else None


def manager_control_default_values(
    config: Mapping[str, Any],
    conn: sqlite3.Connection,
) -> dict[str, Any]:
    stored_mode = normalize_manager_mode(get_manager_setting(conn, "selected_mode", ""))
    stored_local = normalize_local_toggle(get_manager_setting(conn, "local_subagents_enabled", None))
    stored_kaveman = normalize_local_toggle(get_manager_setting(conn, "kaveman_enabled", None))
    return {
        "selected_mode": stored_mode
        if stored_mode in MANAGER_MODE_ORDER
        else normalize_manager_mode(config.get("orchestration", {}).get("mode")) or "auto",
        "local_subagents_enabled": local_subagents_default_enabled(config)
        if stored_local is None
        else stored_local,
        "kaveman_enabled": kaveman_default_enabled(config)
        if stored_kaveman is None
        else stored_kaveman,
    }


def normalize_manager_turn_snapshot(raw: Any) -> dict[str, Any]:
    """Return one complete, privacy-safe policy snapshot for an accepted turn."""
    source = raw if isinstance(raw, Mapping) else {}
    policy = source.get("agent_policy")
    if not isinstance(policy, Mapping):
        return {}
    snapshot = dict(policy)
    policy_hash = str(snapshot.get("policy_hash") or "").strip()
    if (
        not policy_hash
        or str(snapshot.get("mode") or "") not in AGENT_USE_ORDER
        or agent_policy_hash(snapshot) != policy_hash
    ):
        return {}
    root_session_id = str(source.get("root_session_id") or "").strip()
    turn_id = str(source.get("turn_id") or "").strip()
    if not root_session_id or not turn_id:
        return {}
    return {
        "root_session_id": root_session_id,
        "turn_id": turn_id,
        "accepted_at": str(source.get("accepted_at") or utc_now()),
        "policy_hash": policy_hash,
        "agent_policy": snapshot,
    }


def normalize_manager_session_state(
    raw: Any,
    *,
    defaults: Mapping[str, Any],
) -> dict[str, Any]:
    now = utc_now()
    source = raw if isinstance(raw, Mapping) else {}
    selected_mode = normalize_manager_mode(source.get("selected_mode"))
    if selected_mode not in MANAGER_MODE_ORDER:
        selected_mode = str(defaults["selected_mode"])
    local_enabled = normalize_local_toggle(source.get("local_subagents_enabled"))
    kaveman_enabled = normalize_local_toggle(source.get("kaveman_enabled"))
    accepted_turn = normalize_manager_turn_snapshot(source.get("accepted_turn"))
    return {
        "schema_version": MANAGER_SESSION_STATE_SCHEMA,
        "session_id": str(source.get("session_id") or os.environ.get(QDEX_LAUNCH_ID_ENV) or ""),
        "created_at": str(source.get("created_at") or now),
        "updated_at": str(source.get("updated_at") or now),
        "selected_mode": selected_mode,
        "local_subagents_enabled": (
            bool(defaults["local_subagents_enabled"])
            if local_enabled is None
            else local_enabled
        ),
        "kaveman_enabled": (
            bool(defaults["kaveman_enabled"])
            if kaveman_enabled is None
            else kaveman_enabled
        ),
        "accepted_turn": accepted_turn,
        "source": str(source.get("source") or "launch_default"),
    }


def manager_session_control_state(
    config: Mapping[str, Any],
    conn: sqlite3.Connection,
    *,
    updates: Mapping[str, Any] | None = None,
    accepted_turn: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Load or atomically update the private control record for one Qdex launch."""
    path = manager_session_state_path()
    if path is None:
        return None
    defaults = manager_control_default_values(config, conn)
    lock_path = path.with_name(f".{path.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import fcntl

        with lock_path.open("a+", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                try:
                    raw = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    raw = {}
                state = normalize_manager_session_state(raw, defaults=defaults)
                if updates:
                    for key, value in updates.items():
                        if key == "selected_mode":
                            normalized = normalize_manager_mode(value)
                            if normalized in MANAGER_MODE_ORDER:
                                state[key] = normalized
                        elif key in {"local_subagents_enabled", "kaveman_enabled"}:
                            normalized = normalize_local_toggle(value)
                            if normalized is not None:
                                state[key] = normalized
                    state["updated_at"] = utc_now()
                    state["source"] = "tui_session_control"
                if accepted_turn is not None:
                    snapshot = normalize_manager_turn_snapshot(accepted_turn)
                    if snapshot:
                        state["accepted_turn"] = snapshot
                        state["updated_at"] = utc_now()
                if raw != state:
                    atomic_write_text(path, json_dumps(state) + "\n")
                    path.chmod(0o600)
                return state
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    except OSError:
        # A status surface should remain diagnostic even when its private
        # launch record is temporarily unavailable.
        return None


def manager_control_state_metadata() -> dict[str, Any]:
    path = manager_session_state_path()
    if path is None:
        return {
            "schema_version": MANAGER_SESSION_STATE_SCHEMA,
            "scope": "repository_default",
            "session_id": "",
            "state_file_configured": False,
        }
    return {
        "schema_version": MANAGER_SESSION_STATE_SCHEMA,
        "scope": "per_launch_session",
        "session_id": str(os.environ.get(QDEX_LAUNCH_ID_ENV) or ""),
        "state_file_configured": True,
    }


def set_current_manager_control_setting(
    config: Mapping[str, Any],
    conn: sqlite3.Connection,
    key: str,
    value: Any,
) -> dict[str, Any] | None:
    state = manager_session_control_state(config, conn, updates={key: value})
    if state is not None:
        return state
    set_manager_setting(conn, key, value)
    return None


def manager_session_accept_turn_policy(
    config: Mapping[str, Any],
    conn: sqlite3.Connection,
    *,
    event: Mapping[str, Any],
    agent_policy: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Atomically freeze a policy at root prompt admission for this launch."""
    root_session_id = str(event.get("session_id") or "").strip()
    turn_id = str(event.get("turn_id") or "").strip()
    if not root_session_id or not turn_id:
        return None
    snapshot = {
        "root_session_id": root_session_id,
        "turn_id": turn_id,
        "accepted_at": utc_now(),
        "policy_hash": str(agent_policy.get("policy_hash") or ""),
        "agent_policy": dict(agent_policy),
    }
    state = manager_session_control_state(
        config,
        conn,
        accepted_turn=snapshot,
    )
    accepted = state.get("accepted_turn") if isinstance(state, Mapping) else None
    if not isinstance(accepted, Mapping):
        return None
    policy = accepted.get("agent_policy")
    return dict(policy) if isinstance(policy, Mapping) else None


def manager_session_active_turn_policy(
    config: Mapping[str, Any],
    conn: sqlite3.Connection,
    *,
    event: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Return the accepted root-turn policy for a matching root or child hook."""
    state = manager_session_control_state(config, conn)
    accepted = state.get("accepted_turn") if isinstance(state, Mapping) else None
    if not isinstance(accepted, Mapping):
        return None
    root_session_id = str(
        event.get("parent_session_id")
        or event.get("session_id")
        or ""
    ).strip()
    if root_session_id and root_session_id != str(accepted.get("root_session_id") or ""):
        return None
    policy = accepted.get("agent_policy")
    if not isinstance(policy, Mapping):
        return None
    policy_hash = str(policy.get("policy_hash") or "")
    if not policy_hash or agent_policy_hash(policy) != policy_hash:
        return None
    return dict(policy)


def current_manager_mode(config: Mapping[str, Any], conn: sqlite3.Connection, explicit: str = "") -> str:
    if explicit:
        return normalize_manager_mode(explicit)
    if state := manager_session_control_state(config, conn):
        mode = normalize_manager_mode(state.get("selected_mode"))
        if mode in MANAGER_MODE_ORDER:
            return mode
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
    if state := manager_session_control_state(config, conn):
        value = normalize_local_toggle(state.get("local_subagents_enabled"))
        if value is not None:
            return value
    stored = get_manager_setting(conn, "local_subagents_enabled", None)
    parsed = normalize_local_toggle(stored)
    return local_subagents_default_enabled(config) if parsed is None else parsed


def current_kaveman_enabled(config: Mapping[str, Any], conn: sqlite3.Connection) -> bool:
    if state := manager_session_control_state(config, conn):
        value = normalize_local_toggle(state.get("kaveman_enabled"))
        if value is not None:
            return value
    stored = get_manager_setting(conn, "kaveman_enabled", None)
    parsed = normalize_local_toggle(stored)
    return kaveman_default_enabled(config) if parsed is None else parsed


def stale_age_seconds(row: Mapping[str, Any]) -> float:
    try:
        return (datetime.now(UTC) - parse_utc(str(row["heartbeat_at"]))).total_seconds()
    except (KeyError, ValueError):
        return float("inf")


def manager_session_is_stale(session: Mapping[str, Any], *, stale_after_minutes: int) -> bool:
    return stale_age_seconds(session) >= stale_after_minutes * 60


def manager_session_is_read_only(session: Mapping[str, Any]) -> bool:
    return str(session.get("write_surface") or "").strip().lower() in {"read-only", "readonly"}


def reconcile_stale_manager_sessions(
    conn: sqlite3.Connection,
    *,
    stale_after_minutes: int,
    now: str,
    repo_root: str = "",
) -> dict[str, Any]:
    if repo_root:
        rows = conn.execute(
            "SELECT * FROM qwendex_agent_sessions WHERE status IN ('active', 'reserved', 'close_requested') AND repo_root = ?",
            (repo_root,),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM qwendex_agent_sessions WHERE status IN ('active', 'reserved', 'close_requested')").fetchall()
    close_requested: list[dict[str, Any]] = []
    tombstoned: list[dict[str, Any]] = []
    skipped_writers: list[dict[str, Any]] = []
    for row in rows:
        session = row_to_agent_session(row)
        if not session or not manager_session_is_stale(session, stale_after_minutes=stale_after_minutes):
            continue
        if not manager_session_is_read_only(session):
            skipped_writers.append(session)
            continue
        if str(session.get("status") or "") == "close_requested":
            updated_session = transition_agent_session(
                conn,
                agent_id=str(session["agent_id"]),
                status="tombstoned",
                validation_status="fail",
                now=now,
                reason="bounded_close_timeout",
                final_report_present=None,
                close_receipt=make_id("tombstone"),
            )
        else:
            conn.execute(
                """
                UPDATE qwendex_agent_sessions
                SET status = 'close_requested', heartbeat_at = ?, updated_at = ?,
                    stop_reason = 'stale_close_requested', close_receipt = ?
                WHERE agent_id = ?
                """,
                (now, now, make_id("close-request"), session["agent_id"]),
            )
            updated = conn.execute("SELECT * FROM qwendex_agent_sessions WHERE agent_id = ?", (session["agent_id"],)).fetchone()
            updated_session = row_to_agent_session(updated)
        if updated_session:
            (tombstoned if str(updated_session.get("status") or "") == "tombstoned" else close_requested).append(updated_session)
    conn.commit()
    return {
        "closed_count": len(tombstoned),
        "closed": tombstoned,
        "close_requested_count": len(close_requested),
        "close_requested": close_requested,
        "tombstoned_count": len(tombstoned),
        "tombstoned": tombstoned,
        "skipped_writer_count": len(skipped_writers),
        "skipped_writers": skipped_writers,
        "stale_after_minutes": max(stale_after_minutes, 5),
        "repo_root": repo_root,
    }


def summarize_agent_sessions(
    sessions: list[dict[str, Any]],
    *,
    stale_after_minutes: int,
) -> dict[str, Any]:
    active_all = [
        session for session in sessions
        if str(session.get("status") or "") not in AGENT_TERMINAL_STATUSES
    ]
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


def load_manager_session_views(
    conn: sqlite3.Connection,
    *,
    limit: int,
    repo_root: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    rows = conn.execute(
        "SELECT * FROM qwendex_agent_sessions ORDER BY updated_at DESC"
    ).fetchall()
    ledger_sessions = [
        session for row in rows if (session := row_to_agent_session(row))
    ]
    scoped_sessions = sessions_for_repo(ledger_sessions, repo_root)
    return scoped_sessions[:limit], scoped_sessions, ledger_sessions


def classify_manager_validation_sessions(
    sessions: list[dict[str, Any]],
    *,
    stale_after_minutes: int,
    sample_limit: int = 20,
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
    sampled = {key: value[: max(1, sample_limit)] for key, value in buckets.items()}
    return {
        "classifications": sampled,
        "counts": counts,
        "sample_limit": max(1, sample_limit),
        "truncated": {
            key: max(0, counts[key] - len(sampled[key])) for key in buckets
        },
        "total_session_count": len(sessions),
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


def manager_deployment_contract(
    mode: str,
    policy: str,
    active_count: int,
    *,
    session_status: Mapping[str, Any] | None = None,
    stale_writer_count: int = 0,
) -> dict[str, Any]:
    """Describe whether the attached turn, rather than idle capacity, needs lanes."""
    normalized_mode = normalize_manager_mode(mode)
    attached = dict(session_status or {})
    prompt_known = bool(attached.get("prompt_known"))
    unstarted_suggested_lanes = list(attached.get("unstarted_suggested_lanes") or [])
    unresolved_suggested_lanes = list(attached.get("unresolved_suggested_lanes") or [])
    common = {
        "policy": policy,
        "active_count": active_count,
        "attached_prompt": prompt_known,
        "unstarted_suggested_lanes": unstarted_suggested_lanes,
        "unresolved_suggested_lanes": unresolved_suggested_lanes,
    }
    if policy == "disabled":
        return {
            **common,
            "blocking": False,
            "advisory": True,
            "healthy": True,
            "status": "ready",
            "summary": "Manager deployment is disabled by policy.",
        }
    if normalized_mode != "manager":
        return {
            **common,
            "blocking": False,
            "advisory": True,
            "healthy": True,
            "status": "ready",
            "summary": "Manager deployment is not enabled for this mode.",
        }
    if stale_writer_count:
        return {
            **common,
            "blocking": False,
            "advisory": True,
            "healthy": True,
            "status": "warning",
            "summary": "Stale manager writer sessions are recorded for operator review.",
        }
    if not prompt_known:
        return {
            **common,
            "blocking": False,
            "advisory": True,
            "healthy": True,
            "status": "standby",
            "summary": "Manager Mode is healthy and standing by for an attached prompt.",
        }
    if unresolved_suggested_lanes:
        return {
            **common,
            "blocking": False,
            "advisory": True,
            "healthy": True,
            "status": "warning",
            "summary": "Manager Mode has suggested lanes with unresolved lifecycle state.",
        }
    if str(attached.get("route") or "") == "direct" and attached.get("direct_reason"):
        return {
            **common,
            "blocking": False,
            "advisory": True,
            "healthy": True,
            "status": "ready",
            "summary": "Manager Mode allows direct work for the attached trivial turn.",
        }
    if int(attached.get("suggested_lane_count") or 0):
        return {
            **common,
            "blocking": False,
            "advisory": True,
            "healthy": True,
            "status": "ready",
            "summary": "Manager Mode has advisory lane suggestions for the attached turn.",
        }
    return {
        **common,
        "blocking": False,
        "advisory": True,
        "healthy": True,
        "status": "ready",
        "summary": "Manager Mode has an attached direct-work turn.",
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
    ledger_sessions: list[dict[str, Any]] | None = None,
    repo_root: str = "",
    session_status: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    summary = summarize_agent_sessions(sessions, stale_after_minutes=stale_after_minutes)
    authoritative_ledger = sessions if ledger_sessions is None else ledger_sessions
    contract = manager_deployment_contract(
        normalize_manager_mode(mode),
        manager_deploy_policy(config),
        int(summary["active_subagents"]["count"]),
        session_status=session_status,
        stale_writer_count=int(summary["stale_writer_sessions"]["count"]),
    )
    # Manager lifecycle data is advisory. It can describe stale workers,
    # incomplete lanes, and validation debt, but it must not turn general
    # Qwendex health into an authorization gate.
    issues: list[str] = []
    warnings: list[str] = []
    ledger_warnings: list[str] = []
    validation_debt = classify_manager_validation_sessions(
        authoritative_ledger,
        stale_after_minutes=stale_after_minutes,
    )
    scope_validation_debt = classify_manager_validation_sessions(
        sessions,
        stale_after_minutes=stale_after_minutes,
    )
    if summary["stale_writer_sessions"]["count"]:
        ids = ", ".join(str(session.get("agent_id")) for session in summary["stale_writer_sessions"]["agents"])
        warnings.append(f"stale manager writer sessions are available for review or repair: {ids}")
    if scope_validation_debt["pending_validation_count"]:
        warnings.append(
            f"{scope_validation_debt['pending_validation_count']} manager sessions in this repository scope have pending or missing validation evidence; run scripts/qwendex manager reconcile --pending-validation --json."
        )
    if validation_debt["pending_validation_count"] > scope_validation_debt["pending_validation_count"]:
        ledger_warnings.append(
            f"The shared ledger has {validation_debt['pending_validation_count']} total sessions with pending or missing validation evidence across all scopes; this does not change scoped health."
        )
    if contract["status"] == "warning":
        warnings.append(contract["summary"])
    if warnings:
        status = "warning"
    else:
        status = contract["status"]
    return {
        "status": status,
        "health_mode": normalize_health_mode(health_mode),
        "issues": issues,
        "warnings": warnings,
        "ledger_warnings": ledger_warnings,
        "validation_debt": validation_debt,
        "scope_validation_debt": scope_validation_debt,
        "ledger_scope": {
            "repo_root": repo_root,
            "scoped_session_count": len(sessions),
            "total_session_count": len(authoritative_ledger),
            "legacy_unscoped_count": sum(
                1 for session in authoritative_ledger if not session.get("repo_root")
            ),
            "other_repo_session_count": sum(
                1
                for session in authoritative_ledger
                if session.get("repo_root") and str(session.get("repo_root")) != repo_root
            ),
        },
        "deployment_contract": contract,
        "repair_command": "scripts/qwendex manager repair --safe --json",
    }


def manager_health_issues(
    config: Mapping[str, Any],
    sessions: list[dict[str, Any]],
    *,
    mode: str,
    stale_after_minutes: int,
    session_status: Mapping[str, Any] | None = None,
) -> list[str]:
    health = manager_health_summary(
        config,
        sessions,
        mode=mode,
        stale_after_minutes=stale_after_minutes,
        health_mode="strict",
        session_status=session_status,
    )
    issues = list(health["issues"])
    contract = health["deployment_contract"]
    if not contract["healthy"] and contract["summary"] not in issues:
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
    repo_root: str = "",
) -> dict[str, Any]:
    if repo_root:
        rows = conn.execute(
            "SELECT * FROM qwendex_agent_sessions WHERE status IN ('active', 'reserved', 'close_requested') AND repo_root = ?",
            (repo_root,),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM qwendex_agent_sessions WHERE status IN ('active', 'reserved', 'close_requested')").fetchall()
    closed_read_only: list[dict[str, Any]] = []
    closed_writers: list[dict[str, Any]] = []
    close_requested: list[dict[str, Any]] = []
    manual_close: list[dict[str, Any]] = []
    for row in rows:
        session = row_to_agent_session(row)
        if not session or not manager_session_is_stale(session, stale_after_minutes=stale_after_minutes):
            continue
        if str(session.get("status") or "") == "close_requested":
            reason = "bounded_close_timeout"
        elif manager_session_is_read_only(session):
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
        if str(session.get("status") or "") == "close_requested":
            closed = transition_agent_session(
                conn,
                agent_id=str(session["agent_id"]),
                status="tombstoned",
                validation_status="fail",
                now=now,
                reason="bounded_close_timeout",
                final_report_present=None,
                close_receipt=make_id("tombstone"),
            ) or {}
        else:
            conn.execute(
                """
                UPDATE qwendex_agent_sessions
                SET status = 'close_requested', heartbeat_at = ?, updated_at = ?,
                    stop_reason = ?, close_receipt = ?
                WHERE agent_id = ?
                """,
                (now, now, f"{reason}_close_requested", make_id("close-request"), session["agent_id"]),
            )
            updated = conn.execute("SELECT * FROM qwendex_agent_sessions WHERE agent_id = ?", (session["agent_id"],)).fetchone()
            requested = row_to_agent_session(updated)
            if requested:
                close_requested.append(requested)
            continue
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
        "close_requested_count": len(close_requested),
        "manual_close_count": len(manual_close),
        "closed_read_only": closed_read_only,
        "closed_writers": closed_writers,
        "close_requested": close_requested,
        "manual_close": manual_close,
        "stale_after_minutes": max(stale_after_minutes, 5),
        "repo_root": repo_root,
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
    high_risk = text_has_any_term(
        lower,
        ("security", "credential", "credentials", "release", "protocol", "architecture", "migration"),
    )
    many_files = text_has_any_term(lower, ("several", "multiple", "across", "many"))
    validation_heavy = text_has_any_term(lower, ("test", "tests", "eval", "release", "security", "protocol"))
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
        scope = "many_files" if many_files else "few_files"
    elif text_has_any_term(lower, ("typo", "small", "one file", "single")):
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


def manager_session_status_payload(
    config: Mapping[str, Any],
    *,
    repo_root: str,
    desired_policy: Mapping[str, Any],
) -> dict[str, Any]:
    if not repo_root:
        return {}
    launch_ledger_id = str(os.environ.get("QWENDEX_MANAGER_LEDGER_ID") or "").strip()
    with connect_state(config) as conn:
        if launch_ledger_id:
            row = conn.execute(
                """
                SELECT * FROM qwendex_manager_decisions
                WHERE repo_root = ? AND (ledger_id = ? OR launch_ledger_id = ?)
                ORDER BY CASE final_status
                    WHEN 'preflight_ready' THEN 0
                    WHEN 'validation_pending' THEN 1
                    ELSE 2 END,
                    timestamp_updated DESC
                LIMIT 1
                """,
                (repo_root, launch_ledger_id, launch_ledger_id),
            ).fetchone()
            decision = row_to_manager_decision(row)
        else:
            decision = latest_manager_decision(conn, repo_root=repo_root)
        if decision is None:
            return {}
        task_id = str(decision.get("agent_task_id") or decision.get("session_id") or "")
        rows = conn.execute(
            """
            SELECT * FROM qwendex_agent_sessions
            WHERE repo_root = ? AND task_id = ?
            ORDER BY updated_at DESC
            """,
            (repo_root, task_id),
        ).fetchall()
        sessions = [session for row in rows if (session := row_to_agent_session(row))]
    plan = dict(decision.get("agent_plan") or {})
    legacy_required_lanes = list(plan.get("required_lanes") or [])
    optional_lanes = list(plan.get("optional_lanes") or [])
    suggested_lanes: list[dict[str, Any]] = []
    seen_suggestions: set[tuple[str, str]] = set()
    for item in [*legacy_required_lanes, *optional_lanes]:
        if not isinstance(item, Mapping):
            continue
        suggestion = dict(item)
        key = (
            str(suggestion.get("lane") or "").strip().lower(),
            str(suggestion.get("profile") or "").strip().lower(),
        )
        if key in seen_suggestions:
            continue
        seen_suggestions.add(key)
        suggested_lanes.append(suggestion)
    active = [item for item in sessions if str(item.get("status") or "") not in AGENT_TERMINAL_STATUSES]
    terminal = [item for item in sessions if str(item.get("status") or "") in AGENT_TERMINAL_STATUSES]
    reservations = [item for item in sessions if str(item.get("status") or "") == "reserved"]
    close_requests = [item for item in sessions if str(item.get("status") or "") == "close_requested"]
    waivers = [item for item in sessions if str(item.get("status") or "") == "waived"]
    session_hash = str(decision.get("policy_hash") or "")
    desired_hash = str(desired_policy.get("policy_hash") or "")
    drift = bool(session_hash and desired_hash and session_hash != desired_hash)
    internal_route = str(decision.get("selected_route") or "")
    route = {
        "manager_subagents": "orchestrated_single_writer",
        "direct_single_writer": "direct",
    }.get(internal_route, internal_route)
    direct_reason = str(decision.get("routing_reason") or "") if internal_route == "direct_single_writer" else None
    admission_error = str(decision.get("admission_error_code") or "") or None
    why_no_agent = None
    if not sessions:
        if direct_reason:
            why_no_agent = direct_reason
        elif admission_error:
            why_no_agent = f"advisory delegation bookkeeping unavailable: {admission_error}"
        elif suggested_lanes:
            why_no_agent = "suggested lanes have not been registered"
    sessions_by_lane: dict[str, list[dict[str, Any]]] = {}
    for session in sessions:
        lane = str(session.get("lane") or "")
        if lane:
            sessions_by_lane.setdefault(lane, []).append(session)
    unstarted_suggested_lanes = [
        lane for lane in suggested_lanes
        if str(lane.get("lane") or "") not in sessions_by_lane
    ]
    unresolved_suggested_lanes: list[dict[str, Any]] = []
    for lane in suggested_lanes:
        lane_name = str(lane.get("lane") or "")
        lane_sessions = sessions_by_lane.get(lane_name, [])
        if not lane_sessions or any(
            str(item.get("status") or "") not in AGENT_TERMINAL_STATUSES
            for item in lane_sessions
        ):
            continue
        resolved = any(
            str(item.get("status") or "") == "waived"
            or (
                str(item.get("status") or "") == "completed"
                and str(item.get("validation_status") or "") == "pass"
            )
            for item in lane_sessions
        )
        if not resolved:
            unresolved_suggested_lanes.append(lane)
    return {
        "schema_version": "qwendex.manager_session_status.v2",
        "session_id": decision.get("session_id"),
        "turn_id": decision.get("turn_id") or None,
        "repo_root": repo_root,
        "selected_mode": decision.get("selected_mode") or decision.get("mode"),
        "effective_turn_mode": decision.get("effective_turn_mode") or decision.get("agent_use"),
        "policy_hash": session_hash,
        "desired_global_policy_hash": desired_hash,
        "policy_drift": drift,
        "session_policy_valid": bool(session_hash),
        "restart_required": drift,
        "prompt_known": bool(decision.get("prompt_known")),
        "prompt_source": decision.get("prompt_source") or None,
        "prompt_length": int(decision.get("prompt_length") or 0),
        "task_class": decision.get("task_class") or None,
        "route": route,
        "native_agent_capability": "configured" if int((decision.get("policy_snapshot") or {}).get("max_workers") or 0) else "disabled",
        "native_proactive_source": (
            str(plan.get("native_proactive_source") or "qwendex_custom_policy")
            if internal_route == "manager_subagents"
            else "none"
        ),
        "suggested_lane_count": len(suggested_lanes),
        "planned_lane_count": len(suggested_lanes),
        "registered_agent_count": len(sessions),
        "active_agent_count": len(active),
        "terminal_agent_count": len(terminal),
        "reserved_agent_count": len(reservations),
        "close_requested_agent_count": len(close_requests),
        "waiver_count": len(waivers),
        "waivers": [
            {
                "lane": item.get("lane"),
                "reason": (item.get("context_packet") or {}).get("waiver_reason") or item.get("stop_reason"),
                "completed_at": item.get("completed_at"),
            }
            for item in waivers
        ],
        "direct_reason": direct_reason,
        "last_admission_error": admission_error,
        "suggested_lanes": suggested_lanes,
        "unstarted_suggested_lanes": unstarted_suggested_lanes,
        "unresolved_suggested_lanes": unresolved_suggested_lanes,
        "why_no_agent": why_no_agent,
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
    scope_sessions: list[dict[str, Any]] | None = None,
    ledger_sessions: list[dict[str, Any]] | None = None,
    repo_root: str = "",
) -> dict[str, Any]:
    profile = manager_mode_profile(config, mode)
    resolved_agent_policy = dict(agent_policy or resolve_agent_policy(config, selected_manager_mode=profile["mode"], kaveman_enabled=kaveman_enabled))
    if bool(resolved_agent_policy.get("output_policy", {}).get("kaveman_enabled")) != bool(kaveman_enabled):
        resolved_agent_policy = attach_output_policy(resolved_agent_policy, config, kaveman_enabled=kaveman_enabled)
    resolved_agent_policy = attach_local_routing_snapshot(
        resolved_agent_policy,
        config,
        enabled=bool(local_status.get("enabled")),
    )
    displayed_sessions = sessions or []
    operational_sessions = displayed_sessions if scope_sessions is None else scope_sessions
    authoritative_ledger = operational_sessions if ledger_sessions is None else ledger_sessions
    summary = summarize_agent_sessions(operational_sessions, stale_after_minutes=stale_after_minutes)
    summary["agent_outcomes"] = agent_outcomes_for_sessions(displayed_sessions)
    session_status = manager_session_status_payload(
        config,
        repo_root=repo_root,
        desired_policy=resolved_agent_policy,
    )
    qdex_permission = qdex_permission_posture(config)
    data = {
        "mode": profile["mode"],
        "label": profile["label"],
        "agent_use": resolved_agent_policy["agent_use"],
        "agent_policy": resolved_agent_policy,
        "agent_policy_hash": resolved_agent_policy["policy_hash"],
        "agent_policy_source": resolved_agent_policy["source"],
        "agent_policy_warnings": list(resolved_agent_policy.get("warnings", [])),
        "qdex_permission_mode": qdex_permission["mode"],
        "qdex_permission_source": qdex_permission["source"],
        "qdex_permission_valid": qdex_permission["valid"],
        "qdex_permission": qdex_permission,
        "output_policy": resolved_agent_policy.get("output_policy", {}),
        "control_state": manager_control_state_metadata(),
        "status_authority": status_authority_payload(resolved_agent_policy),
        "write_safety": file_lock_summary(config),
        "legacy_mode": legacy_mode,
        "ui_indicator": manager_ui_indicator(config, profile["mode"]),
        "kaveman_indicator": kaveman_indicator(config, kaveman_enabled),
        "kaveman_enabled": kaveman_enabled,
        "kaveman_directive": kaveman_directive(config) if kaveman_enabled else "",
        "local_indicator": local_status["indicator"],
        "local_subagents": local_status,
        "hotkeys": {
            "source": "codex_tui_keymap",
            "manager": "Alt+M",
            "local": "Alt+L",
            "kaveman": "Alt+K",
            "configurable_in_qwendex": False,
        },
        "manager_deploy_policy": manager_deploy_policy(config),
        "max_subagents": max_subagents,
        "stale_after_minutes": stale_after_minutes,
        "reasoning_policy": reasoning_policy(config, local_status),
        "lane_template": [],
        "next_actions": ["Run scripts/qwendex manager estimate --prompt '...' --json"],
        "high_value_add": high_value_add_lines(local_status),
        "repo_root": repo_root,
        "displayed_session_count": len(displayed_sessions),
        "scoped_session_count": len(operational_sessions),
        "ledger_session_count": len(authoritative_ledger),
        "agent_outcomes_truncated": max(0, len(operational_sessions) - len(displayed_sessions)),
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
        session_status=session_status,
        stale_writer_count=int(data["stale_writer_sessions"]["count"]),
    )
    health = manager_health_summary(
        config,
        operational_sessions,
        mode=profile["mode"],
        stale_after_minutes=stale_after_minutes,
        health_mode=health_mode,
        ledger_sessions=authoritative_ledger,
        repo_root=repo_root,
        session_status=session_status,
    )
    data["manager_health"] = health
    data["manager_estimate"] = manager_self_estimate(
        config,
        mode=profile["mode"],
        local_status=local_status,
        stale_pressure="high" if data["stale_sessions"]["count"] else "none",
    )
    if session_status:
        data["session_status"] = session_status
    return data


def apply_manager_session_policy_surface(
    data: dict[str, Any],
    *,
    requested_policy: Mapping[str, Any],
    effective_policy: Mapping[str, Any],
    transition: Mapping[str, Any],
    accepted_turn: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Make requested, launch-effective, and accepted-turn policy explicit."""
    requested = dict(requested_policy)
    effective = dict(effective_policy)
    transition_data = dict(transition)
    data["requested_agent_policy"] = requested
    data["requested_agent_policy_hash"] = str(requested.get("policy_hash") or "")
    data["agent_policy"] = effective
    data["agent_policy_hash"] = str(effective.get("policy_hash") or "")
    data["agent_policy_source"] = str(effective.get("source") or "")
    data["agent_policy_warnings"] = list(effective.get("warnings") or [])
    data["agent_use"] = str(effective.get("agent_use") or "")
    data["output_policy"] = dict(effective.get("output_policy") or {})
    data["effective_turn_mode"] = str(
        transition_data.get("effective_turn_mode") or effective.get("mode") or ""
    )
    data["effective_max_subagents"] = max(0, int(effective.get("max_threads") or 0))
    data["effective_local_enabled"] = bool(
        transition_data.get("effective_local_enabled")
    )
    data["runtime_launch_mode"] = transition_data.get("launch_mode")
    data["policy_transition"] = transition_data
    data["status_authority"] = status_authority_payload(
        effective,
        transition=transition_data,
        accepted_turn=accepted_turn,
    )
    return data


def manager_status_surface_text(
    label: str,
    local_state: str,
    kaveman_enabled: bool,
    *,
    local_restart_required: bool = False,
) -> str:
    local_label = local_state_label(local_state)
    if local_restart_required:
        local_label = f"{local_label} (restart)"
    return (
        f"{{Qwendex}} Agent Manager: [{label}] | Kaveman: [{'Y' if kaveman_enabled else 'N'}] "
        f"| Local: [{local_label}] (Alt+M/K/L)"
    )


def status_authority_payload(
    agent_policy: Mapping[str, Any],
    *,
    transition: Mapping[str, Any] | None = None,
    accepted_turn: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    control_state = manager_control_state_metadata()
    next_turn_policy_hash = str(agent_policy.get("policy_hash") or "")
    launch_policy_hash = str(os.environ.get(QDEX_LAUNCH_POLICY_HASH_ENV) or "").strip()
    per_launch = control_state["scope"] == "per_launch_session"
    transition_data = dict(transition or {})
    desired_policy_hash = str(
        transition_data.get("requested_policy_hash")
        or next_turn_policy_hash
    )
    accepted = accepted_turn if isinstance(accepted_turn, Mapping) else {}
    accepted_policy = accepted.get("agent_policy") if isinstance(accepted, Mapping) else None
    active_policy_hash = (
        str(accepted_policy.get("policy_hash") or "")
        if isinstance(accepted_policy, Mapping)
        else ""
    )
    drift = bool(launch_policy_hash and desired_policy_hash and launch_policy_hash != desired_policy_hash)
    return {
        "scope": "per_launch_session" if per_launch else "aggregate_compatibility",
        "authoritative_for_open_session": per_launch,
        "session_id": control_state["session_id"] if per_launch else "",
        "effective_policy_hash": active_policy_hash or next_turn_policy_hash,
        "next_turn_policy_hash": next_turn_policy_hash,
        "desired_policy_hash": desired_policy_hash,
        "launch_policy_hash": launch_policy_hash or None,
        "active_turn_policy_hash": active_policy_hash or None,
        "active_turn": {
            "root_session_id": str(accepted.get("root_session_id") or "") or None,
            "turn_id": str(accepted.get("turn_id") or "") or None,
            "accepted_at": str(accepted.get("accepted_at") or "") or None,
        } if active_policy_hash else None,
        "requested_mode": transition_data.get("requested_mode"),
        "effective_turn_mode": transition_data.get("effective_turn_mode"),
        "launch_mode": transition_data.get("launch_mode"),
        "kaveman_applies_at": transition_data.get("kaveman_applies_at"),
        "mode_applies_at": transition_data.get("mode_applies_at"),
        "policy_drift": drift,
        "restart_required": bool(transition_data.get("restart_required")),
        "mode_restart_required": bool(transition_data.get("mode_restart_required")),
        "local_restart_required": bool(transition_data.get("local_restart_required")),
        "aggregate_status_is_not_session_truth": not per_launch,
    }


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
    qdex_permission = qdex_permission_posture(config)
    with connect_state(config) as conn:
        selected_mode = current_manager_mode(config, conn)
        mode = selected_mode
        kaveman_enabled = current_kaveman_enabled(config, conn)
        requested_agent_policy = resolve_agent_policy(
            config,
            selected_manager_mode=selected_mode,
            kaveman_enabled=kaveman_enabled,
        )
        if requested_agent_policy["source"] not in {"default", "manager-mode"}:
            mode = str(requested_agent_policy["mode"])
        stale_after = mode_stale_after_minutes(config, mode)
        reconcile_stale_manager_sessions(
            conn,
            stale_after_minutes=stale_after,
            now=utc_now(),
            repo_root=canonical_manager_repo_root(),
        )
        local_enabled = current_local_enabled(config, conn)
        local_status = local_subagent_status(config, enabled=local_enabled, env=os.environ, probe=True)
        agent_policy, policy_transition = session_turn_policy_projection(config, conn)
        session_state = manager_session_control_state(config, conn) or {}
        accepted_turn = session_state.get("accepted_turn") if isinstance(session_state, Mapping) else {}
    base_hook_status = hook_status_for_codex_home(
        codex_home_from_env(os.environ),
        write_gating=False,
    )
    hook_override = False
    hook_status = dict(base_hook_status)
    hook_status["override"] = hook_override
    hook_status["override_reason"] = None
    manager_preflight_required = str(agent_policy.get("mode") or "") != "off"
    profile = manager_mode_profile(config, mode)
    effective_profile = manager_mode_profile(
        config,
        str(policy_transition.get("effective_turn_mode") or mode),
    )
    status_label = profile["label"]
    if policy_transition.get("mode_restart_required"):
        status_label = f"Requested {profile['label']} → active {effective_profile['label']} (restart)"
    text = manager_status_surface_text(
        status_label,
        str(local_status.get("local_state") or "unknown"),
        kaveman_enabled,
        local_restart_required=bool(policy_transition.get("local_restart_required")),
    )
    data = {
        "text": text,
        "mode": profile["mode"],
        "label": profile["label"],
        "selected_manager_mode": selected_mode,
        "manager_preflight_required": manager_preflight_required,
        "agent_use": agent_policy["agent_use"],
        "agent_policy": agent_policy,
        "agent_policy_hash": agent_policy["policy_hash"],
        "agent_policy_source": agent_policy["source"],
        "requested_agent_policy": requested_agent_policy,
        "requested_agent_policy_hash": requested_agent_policy["policy_hash"],
        "effective_turn_mode": policy_transition["effective_turn_mode"],
        "runtime_launch_mode": policy_transition["launch_mode"],
        "policy_transition": policy_transition,
        "output_policy": agent_policy.get("output_policy", {}),
        "control_state": manager_control_state_metadata(),
        "status_authority": status_authority_payload(
            agent_policy,
            transition=policy_transition,
            accepted_turn=accepted_turn if isinstance(accepted_turn, Mapping) else None,
        ),
        "kaveman": "Y" if kaveman_enabled else "N",
        "kaveman_enabled": kaveman_enabled,
        "kaveman_directive": kaveman_directive(config) if kaveman_enabled else "",
        "local": "Y" if local_status.get("enabled") else "N",
        "local_enabled": bool(local_status.get("enabled")),
        "effective_local_enabled": bool(policy_transition["effective_local_enabled"]),
        "local_available": local_status.get("available"),
        "local_usable": bool(local_status.get("usable")),
        "local_state": local_status.get("local_state"),
        "hook_status": hook_status,
        "hook_source_count": hook_status["hook_source_count"],
        "qdex_permission_mode": qdex_permission["mode"],
        "qdex_permission_source": qdex_permission["source"],
        "qdex_permission_valid": qdex_permission["valid"],
        "qdex_permission": qdex_permission,
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
        atomic_write_text(target, json.dumps(file_data, indent=2, sort_keys=True) + "\n")
        data["status_file"] = str(target)
    return data


def sync_codex_status_file_from_env(config: Mapping[str, Any]) -> str:
    raw = os.environ.get(QWENDEX_CODEX_STATUS_FILE_ENV, "").strip()
    if not raw:
        return ""
    return str(codex_status_payload(config, write_path=Path(raw)).get("status_file") or "")


def sync_codex_status_or_restore_setting(
    config: Mapping[str, Any],
    conn: sqlite3.Connection,
    *,
    setting_key: str,
    previous_value: Any,
) -> dict[str, Any]:
    try:
        return {
            "status_file": sync_codex_status_file_from_env(config),
            "error": "",
            "state_restored": False,
        }
    except OSError as exc:
        set_current_manager_control_setting(config, conn, setting_key, previous_value)
        conn.commit()
        restore_error = ""
        try:
            sync_codex_status_file_from_env(config)
        except OSError as restore_exc:
            restore_error = str(restore_exc)
        return {
            "status_file": "",
            "error": str(exc),
            "state_restored": True,
            "restore_sync_error": restore_error,
        }


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
    missing_patch_markers: list[str] = []
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
        else:
            missing_patch_markers.append(rel)
        files.append({
            "path": rel,
            "exists": True,
            "anchors_ok": not absent,
            "patched": patched,
            "missing_anchors": absent,
        })
    expected_file_count = len(files)
    applied = bool(expected_file_count) and not missing_files and not missing_patch_markers
    return {
        "root": str(root),
        "files": files,
        "missing_files": missing_files,
        "missing_anchors": missing_anchors,
        "patch_marker_hits": marker_hits,
        "missing_patch_markers": missing_patch_markers,
        "anchors_ok": not missing_files and not missing_anchors,
        "partially_applied": bool(marker_hits) and not applied,
        "applied": applied,
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
            }
            StatusLineItem::QwendexManager => {
                "Qwendex manager mode, Kaveman, and local routing state"
            }
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
            StatusLineItem::FastMode
            | StatusLineItem::RawOutput
            | StatusLineItem::QwendexManager => Self::Mode,
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
            StatusSurfacePreviewItem::QwendexManager => {
                "{Qwendex} Agent Manager: [Manager Mode] | Kaveman: [N] | Local: [Ready] (Alt+M/K/L)"
            }
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
                (
                    "qwendex_toggle_manager",
                    self.app.qwendex_toggle_manager.as_slice(),
                ),
                (
                    "qwendex_toggle_kaveman",
                    self.app.qwendex_toggle_kaveman.as_slice(),
                ),
                (
                    "qwendex_toggle_local",
                    self.app.qwendex_toggle_local.as_slice(),
                ),
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
                (
                    "qwendex_toggle_manager",
                    self.app.qwendex_toggle_manager.as_slice(),
                ),
                (
                    "qwendex_toggle_kaveman",
                    self.app.qwendex_toggle_kaveman.as_slice(),
                ),
                (
                    "qwendex_toggle_local",
                    self.app.qwendex_toggle_local.as_slice(),
                ),
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
                (
                    "qwendex_toggle_manager",
                    self.app.qwendex_toggle_manager.as_slice(),
                ),
                (
                    "qwendex_toggle_kaveman",
                    self.app.qwendex_toggle_kaveman.as_slice(),
                ),
                (
                    "qwendex_toggle_local",
                    self.app.qwendex_toggle_local.as_slice(),
                ),
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
                self.chat_widget
                    .add_to_history(history_cell::new_info_event(
                        format!("Qwendex {{label}} toggled."),
                        None,
                    ));
            }}
            Ok(output) => {{
                let stderr = String::from_utf8_lossy(&output.stderr);
                self.chat_widget
                    .add_to_history(history_cell::new_error_event(format!(
                        "Qwendex {{label}} toggle failed: {{stderr}}"
                    )));
            }}
            Err(err) => {{
                self.chat_widget
                    .add_to_history(history_cell::new_error_event(format!(
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
    let visualization_enabled = config
        .features
        .enabled(Feature::TerminalVisualizationInstructions);
    let kaveman_directive = qwendex_kaveman_directive();
    if !visualization_enabled && kaveman_directive.is_none() {{
        return control_instructions;
    }}

    let mut blocks = Vec::new();
    let existing_instructions = if visualization_enabled {{
        control_instructions.or_else(|| config.developer_instructions.clone())
    }} else {{
        control_instructions
    }};
    if let Some(existing) = existing_instructions {{
        if !existing.trim().is_empty() {{
            blocks.push(existing);
        }}
    }}
    if visualization_enabled {{
        blocks.push(TERMINAL_VISUALIZATION_INSTRUCTIONS.to_string());
    }}
    if let Some(directive) = kaveman_directive {{
        blocks.push(directive);
    }}
    (!blocks.is_empty()).then(|| blocks.join("\\n\\n"))
}}
""",
                ),
            ],
        },
        {
            "path": "codex-rs/hooks/src/events/session_start.rs",
            "replacements": [
                (
                    """    SubagentStart {
        turn_id: String,
        agent_id: String,
        agent_type: String,
    },
""",
                    f"""    SubagentStart {{
        turn_id: String,
        agent_id: String,
        agent_type: String,
        {marker}
        task_name: String,
        parent_session_id: String,
    }},
""",
                ),
                (
                    """        StartHookTarget::SubagentStart {
            turn_id: subagent_turn_id,
            agent_id,
            agent_type,
        } => {
""",
                    """        StartHookTarget::SubagentStart {
            turn_id: subagent_turn_id,
            agent_id,
            agent_type,
            task_name,
            parent_session_id,
        } => {
""",
                ),
                (
                    """                agent_id,
                agent_type,
            };
""",
                    """                agent_id,
                agent_type,
                task_name,
                parent_session_id,
            };
""",
                ),
            ],
        },
        {
            "path": "codex-rs/hooks/src/schema.rs",
            "replacements": [
                (
                    """    pub agent_id: String,
    pub agent_type: String,
}

#[derive(Debug, Clone, Serialize, JsonSchema)]
#[serde(deny_unknown_fields)]
#[schemars(rename = "user-prompt-submit.command.input")]
""",
                    f"""    pub agent_id: String,
    pub agent_type: String,
    {marker}
    /// Canonical V2 task path supplied to spawn_agent.
    pub task_name: String,
    /// Root/parent thread id that authorized this spawn.
    pub parent_session_id: String,
}}

#[derive(Debug, Clone, Serialize, JsonSchema)]
#[serde(deny_unknown_fields)]
#[schemars(rename = "user-prompt-submit.command.input")]
""",
                ),
            ],
        },
        {
            "path": "codex-rs/core/src/hook_runtime.rs",
            "replacements": [
                (
                    """            SessionSource::SubAgent(SubAgentSource::ThreadSpawn { agent_role, .. })
                if matches!(
                    session_start_source,
                    codex_hooks::SessionStartSource::Startup
                ) =>
            {
                let context = subagent_hook_context(sess, agent_role);
                StartHookTarget::SubagentStart {
                    turn_id: turn_context.sub_id.clone(),
                    agent_id: context.agent_id,
                    agent_type: context.agent_type,
                }
            }
""",
                    f"""            SessionSource::SubAgent(SubAgentSource::ThreadSpawn {{
                parent_thread_id,
                agent_path,
                agent_role,
                ..
            }}) if matches!(
                session_start_source,
                codex_hooks::SessionStartSource::Startup
            ) =>
            {{
                let context = subagent_hook_context(sess, agent_role);
                {marker}
                StartHookTarget::SubagentStart {{
                    turn_id: turn_context.sub_id.clone(),
                    agent_id: context.agent_id,
                    agent_type: context.agent_type,
                    task_name: agent_path
                        .as_ref()
                        .map(ToString::to_string)
                        .unwrap_or_default(),
                    parent_session_id: parent_thread_id.to_string(),
                }}
            }}
""",
                ),
            ],
        },
        {
            "path": "codex-rs/core/src/tools/spec_plan.rs",
            "replacements": [
                (
                    """        MultiAgentVersion::V2 => true,
""",
                    f"""        {marker}
        MultiAgentVersion::V2 => !turn_context.session_source.is_non_root_agent(),
""",
                ),
            ],
        },
        {
            "path": "codex-rs/core/src/tools/handlers/multi_agents_v2/wait.rs",
            "replacements": [
                (
                    """use super::*;
use crate::session::InputQueueActivity;
""",
                    f"""use super::*;
use crate::agent::control::ListedAgent;
use crate::session::InputQueueActivity;

{marker}
""",
                ),
                (
                    """        let deadline = Instant::now() + Duration::from_millis(timeout_ms as u64);
        let outcome = wait_for_activity(&mut activity_rx, pending_activity, deadline).await;
        let result = WaitAgentResult::from_outcome(outcome);
""",
                    """        let outcome = if pending_activity.is_some() {
            let deadline = Instant::now() + Duration::from_millis(timeout_ms as u64);
            wait_for_activity(&mut activity_rx, pending_activity, deadline).await
        } else {
            session
                .services
                .agent_control
                .register_session_root(session.thread_id, turn.parent_thread_id);
            let agents = session
                .services
                .agent_control
                .list_agents(&turn.session_source, None)
                .await
                .map_err(collab_spawn_error)?;
            if has_running_worker(&agents) {
                let deadline = Instant::now() + Duration::from_millis(timeout_ms as u64);
                wait_for_activity(&mut activity_rx, pending_activity, deadline).await
            } else {
                WaitOutcome::NoRunningAgents
            }
        };
        let result = WaitAgentResult::from_outcome(outcome);
""",
                ),
                (
                    """        let message = match outcome {
            WaitOutcome::MailboxActivity => "Wait completed.",
            WaitOutcome::Steered => "Wait interrupted by new input.",
            WaitOutcome::TimedOut => "Wait timed out.",
        };
""",
                    """        let message = match outcome {
            WaitOutcome::MailboxActivity => "Wait completed.",
            WaitOutcome::Steered => "Wait interrupted by new input.",
            WaitOutcome::TimedOut => {
                "Wait timed out. Inspect list_agents before any retry; do not retry when no child is running."
            }
            WaitOutcome::NoRunningAgents => {
                "No child agent is running. Do not retry wait_agent; integrate terminal results, finalize, or use one explicitly bounded followup_task for revalidation."
            }
        };
""",
                ),
                (
                    """enum WaitOutcome {
    MailboxActivity,
    Steered,
    TimedOut,
}

async fn wait_for_activity(
""",
                    """enum WaitOutcome {
    MailboxActivity,
    Steered,
    TimedOut,
    NoRunningAgents,
}

fn has_running_worker(agents: &[ListedAgent]) -> bool {
    agents.iter().any(|agent| {
        agent.agent_name != AgentPath::ROOT
            && matches!(
                agent.agent_status,
                AgentStatus::PendingInit | AgentStatus::Running
            )
    })
}

async fn wait_for_activity(
""",
                ),
                (
                    """        Ok(Err(_)) | Err(_) => WaitOutcome::TimedOut,
    }
}
""",
                    """        Ok(Err(_)) | Err(_) => WaitOutcome::TimedOut,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn listed_agent(name: &str, status: AgentStatus) -> ListedAgent {
        ListedAgent {
            agent_name: name.to_string(),
            agent_status: status,
            last_task_message: None,
        }
    }

    #[test]
    fn running_worker_excludes_root_and_terminal_children() {
        let terminal = vec![
            listed_agent(AgentPath::ROOT, AgentStatus::Running),
            listed_agent("/ROOT/verifier", AgentStatus::Completed(None)),
        ];
        assert!(!has_running_worker(&terminal));

        let active = vec![
            listed_agent(AgentPath::ROOT, AgentStatus::Running),
            listed_agent("/ROOT/verifier", AgentStatus::PendingInit),
        ];
        assert!(has_running_worker(&active));
    }

    #[test]
    fn no_running_agent_result_forbids_wait_retry() {
        let result = WaitAgentResult::from_outcome(WaitOutcome::NoRunningAgents);
        assert!(!result.timed_out);
        assert!(result.message.contains("Do not retry wait_agent"));
        assert!(result.message.contains("followup_task"));
    }
}
""".replace("/ROOT/", "/" + "root" + "/"),
                ),
            ],
        },
        {
            "path": "codex-rs/core/src/tools/handlers/multi_agents_spec.rs",
            "replacements": [
                (
                    """        description: "Wait for a mailbox update from any live agent, including queued messages and final-status notifications. The wait also ends early when new user input is steered into the active turn. Does not return the content; returns either a summary of which agents have updates (if any), an interruption summary for steered input, or a timeout summary if no activity arrives before the deadline."
            .to_string(),
""",
                    f"""        {marker}
        description: "Wait for a mailbox update from any running child agent, including queued messages and final-status notifications. Returns immediately when no child is running. The wait also ends early when new user input is steered into the active turn. Does not return agent content. After a timeout, inspect list_agents once and do not retry wait_agent unless a child is still running."
            .to_string(),
""",
                ),
            ],
        },
        {
            "path": "codex-rs/core/src/config/mod.rs",
            "replacements": [
                (
                    """    pub(crate) fn validate_multi_agent_v2_config(&self) -> std::io::Result<()> {
        if self.features.enabled(Feature::MultiAgentV2) && self.agent_max_threads.is_some() {
            Err(std::io::Error::new(
                std::io::ErrorKind::InvalidInput,
                "agents.max_threads cannot be set when features.multi_agent_v2 is enabled",
            ))
        } else {
            Ok(())
        }
    }
""",
                    f"""    pub(crate) fn validate_multi_agent_v2_config(&self) -> std::io::Result<()> {{
        {marker}
        // V2 owns its per-session thread ceiling and already ignores the legacy
        // agents.max_threads value in effective_agent_max_threads(). Accepting a
        // downstream legacy setting keeps V2 launches backward compatible.
        Ok(())
    }}
""",
                ),
            ],
        },
        {
            "path": "codex-rs/core/src/config/config_tests.rs",
            "replacements": [
                (
                    """#[tokio::test]
async fn multi_agent_v2_feature_rejects_agents_max_threads() -> std::io::Result<()> {
    let codex_home = TempDir::new()?;
    std::fs::write(
        codex_home.path().join(CONFIG_TOML_FILE),
        r#"[features.multi_agent_v2]
enabled = true

[agents]
max_threads = 3
"#,
    )?;

    let config = ConfigBuilder::without_managed_config_for_tests()
        .codex_home(codex_home.path().to_path_buf())
        .fallback_cwd(Some(codex_home.path().to_path_buf()))
        .build()
        .await?;
    let err = config
        .validate_multi_agent_v2_config()
        .expect_err("agents.max_threads should conflict with multi_agent_v2");

    assert_eq!(err.kind(), std::io::ErrorKind::InvalidInput);
    assert_eq!(
        err.to_string(),
        "agents.max_threads cannot be set when features.multi_agent_v2 is enabled"
    );
    assert_eq!(
        config.effective_agent_max_threads(MultiAgentVersion::V2),
        Some(3)
    );

    Ok(())
}
""",
                    f"""#[tokio::test]
async fn multi_agent_v2_ignores_legacy_agents_max_threads() -> std::io::Result<()> {{
    let codex_home = TempDir::new()?;
    std::fs::write(
        codex_home.path().join(CONFIG_TOML_FILE),
        r#"[features.multi_agent_v2]
enabled = true

[agents]
max_threads = 2
"#,
    )?;

    let config = ConfigBuilder::without_managed_config_for_tests()
        .codex_home(codex_home.path().to_path_buf())
        .fallback_cwd(Some(codex_home.path().to_path_buf()))
        .build()
        .await?;
    {marker}
    config.validate_multi_agent_v2_config()?;
    assert_eq!(
        config.effective_agent_max_threads(MultiAgentVersion::V2),
        Some(3)
    );

    Ok(())
}}
""",
                ),
            ],
        },
        {
            "path": "codex-rs/models-manager/src/manager.rs",
            "replacements": [
                (
                    """        let cache_path = codex_home.join(MODEL_CACHE_FILE);
""",
                    f"""        {marker}
        let cache_file = std::env::var_os(\"{QWENDEX_MODELS_CACHE_FILE_ENV}\")
            .filter(|value| !value.is_empty())
            .unwrap_or_else(|| MODEL_CACHE_FILE.into());
        let cache_path = codex_home.join(cache_file);
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
            "models_cache_file_env": QWENDEX_MODELS_CACHE_FILE_ENV,
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
    execution = seat_execution_policy(config, seat, seat_config)
    return {
        "seat": seat,
        "guard": {
            "profile": execution["guard_profile"],
            "max_wall_time_seconds": execution["max_wall_time_seconds"],
            "max_tool_calls": execution["max_tool_calls"],
            "markers": list(config.get("guard", {}).get("markers", [])),
        },
        "sandbox": {
            "mode": execution["sandbox_mode"],
        },
        "tool_surface": execution["tool_surface"],
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
    if schema not in {
        "qwendex.receipt.v1",
        "qwendex.manager_decision.v1",
        "local_qwen_harness_eval.v1",
    }:
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
    if schema == "qwendex.manager_decision.v1":
        for field in (
            "ledger_id",
            "launch_ledger_id",
            "session_id",
            "turn_id",
            "agent_task_id",
            "record_type",
            "started_at",
            "final_status",
            "stop_status",
            "routing_decision",
        ):
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
        local_enabled = current_local_enabled(config, conn)
        local_status = local_subagent_status(config, enabled=local_enabled, env=os.environ, probe=False)
        agent_policy = attach_local_routing_snapshot(
            agent_policy,
            config,
            enabled=local_enabled,
        )
        stale_after = mode_stale_after_minutes(config, mode)
        repo_root = canonical_manager_repo_root()
        reconcile_stale_manager_sessions(
            conn,
            stale_after_minutes=stale_after,
            now=utc_now(),
            repo_root=repo_root,
        )
        rows = conn.execute("SELECT * FROM qwendex_agent_sessions ORDER BY updated_at DESC").fetchall()
        ledger_sessions = [session for row in rows if (session := row_to_agent_session(row))]
        sessions = sessions_for_repo(ledger_sessions, repo_root)
        session_status = manager_session_status_payload(
            config,
            repo_root=repo_root,
            desired_policy=agent_policy,
        )
        manager_health = manager_health_summary(
            config,
            sessions,
            mode=mode,
            stale_after_minutes=stale_after,
            health_mode=health_mode,
            ledger_sessions=ledger_sessions,
            repo_root=repo_root,
            session_status=session_status,
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
            "qdex_permission": qdex_permission_posture(config),
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
        local_enabled = current_local_enabled(config, conn)
        local_status = local_subagent_status(config, enabled=local_enabled, env=os.environ, probe=False)
        agent_policy = attach_local_routing_snapshot(
            agent_policy,
            config,
            enabled=local_enabled,
        )
        stale_after = mode_stale_after_minutes(config, mode)
        repo_root = canonical_manager_repo_root()
        reconcile_stale_manager_sessions(
            conn,
            stale_after_minutes=stale_after,
            now=utc_now(),
            repo_root=repo_root,
        )
        rows = conn.execute("SELECT * FROM qwendex_agent_sessions ORDER BY updated_at DESC").fetchall()
        ledger_sessions = [session for row in rows if (session := row_to_agent_session(row))]
        sessions = sessions_for_repo(ledger_sessions, repo_root)
        session_status = manager_session_status_payload(
            config,
            repo_root=repo_root,
            desired_policy=agent_policy,
        )
        manager_health = manager_health_summary(
            config,
            sessions,
            mode=mode,
            stale_after_minutes=stale_after,
            health_mode=health_mode,
            ledger_sessions=ledger_sessions,
            repo_root=repo_root,
            session_status=session_status,
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
                "routing": routing_policy(config),
                "qdex_permission": qdex_permission_posture(config),
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


def codex_mcp_trusted_roots(exec_cwd: Path) -> str:
    return os.environ.get("QWENDEX_MCP_TRUSTED_ROOTS", "").strip() or str(exec_cwd)


def codex_mcp_override_args(exec_cwd: Path) -> list[str]:
    trusted_roots = codex_mcp_trusted_roots(exec_cwd)
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


def seat_execution_policy(
    config: Mapping[str, Any],
    seat: str,
    seat_config: Mapping[str, Any],
) -> dict[str, Any]:
    configured_mode = str(config.get("sandbox", {}).get("mode") or "workspace-write")
    authority = str(seat_config.get("authority") or "")
    guard = config.get("guard", {})
    context = config.get("context", {})
    routing = routing_policy(config)
    sandbox_mode = (
        "read-only"
        if authority in {"read_only_review", "isolated_probe"}
        else configured_mode
    )
    local_backend = seat in {"qwen", "sandbox"} or str(seat_config.get("backend") or "") == "local-responses-adapter"
    isolated_read_only = sandbox_mode == "read-only"
    local_harness_enabled = bool(seat) and not local_backend and not isolated_read_only
    context_window = int(seat_config.get("context_window") or 0)
    compact_limit = int(seat_config.get("compact_limit") or context.get("compact_limit") or 0)
    tool_output_limit = int(context.get("tool_output_token_limit") or 0)
    max_output_tokens = int(context.get("max_output_tokens") or 0)
    max_wall_time = int(guard.get("max_wall_time_seconds") or -1)
    max_tool_calls = int(guard.get("max_tool_calls") or -1)
    guard_profile = str(seat_config.get("guard_profile") or "balanced")
    runtime_model = seat_runtime_model(config, seat) if seat else ""
    local_base_url = local_qwen_base_url(config)
    child_env = {
        "QWENDEX_GUARD_PROFILE": guard_profile,
        "QWENDEX_MAX_WALL_TIME_SECONDS": str(max_wall_time),
        "QWENDEX_MAX_TOOL_CALLS": str(max_tool_calls),
        "QWENDEX_CONTEXT_WINDOW": str(context_window),
        "QWENDEX_COMPACT_LIMIT": str(compact_limit),
        "QWENDEX_MAX_OUTPUT_TOKENS": str(max_output_tokens),
        "QWENDEX_TOOL_OUTPUT_TOKEN_LIMIT": str(tool_output_limit),
        "LOCAL_QWEN_GUARD_PROFILE": guard_profile,
        "LOCAL_QWEN_CODEX_MAX_WALL_TIME_SECONDS": str(max_wall_time),
        "LOCAL_QWEN_CODEX_MAX_TOOL_CALLS": str(max_tool_calls),
        "LOCAL_QWEN_CODEX_CONTEXT_WINDOW": str(context_window),
        "LOCAL_QWEN_CODEX_AUTO_COMPACT_LIMIT": str(compact_limit),
        "LOCAL_QWEN_TOOL_OUTPUT_TOKEN_LIMIT": str(tool_output_limit),
        "LOCAL_QWEN_BASE": local_base_url,
        "LOCAL_QWEN_MODEL": routing["local_model"],
    }
    return {
        "sandbox_mode": sandbox_mode,
        "authority": authority,
        "ignore_user_config": isolated_read_only or local_backend,
        "local_harness_mcp_enabled": local_harness_enabled,
        "guard_profile": guard_profile,
        "runtime_model": runtime_model,
        "local_probe_url": routing["local_probe_url"],
        "local_base_url": local_base_url,
        "local_model": routing["local_model"],
        "max_wall_time_seconds": max_wall_time,
        "max_tool_calls": max_tool_calls,
        "context_window": context_window,
        "compact_limit": compact_limit,
        "max_output_tokens_declared": max_output_tokens,
        "tool_output_token_limit": tool_output_limit,
        "child_env": child_env,
        "enforcement": {
            "sandbox": "command_argument",
            "max_wall_time_seconds": "parent_timeout_and_local_wrapper",
            "max_tool_calls": "local_bridge" if local_backend else "child_environment_contract",
            "context_window": "command_config",
            "compact_limit": "command_config",
            "tool_output_token_limit": "command_config",
            "max_output_tokens": "declared_not_enforced",
        },
        "tool_surface": {
            "source": (
                "config_default"
                if not seat
                else "codex_builtin_read_only"
                if isolated_read_only
                else "local_minimal_builtin"
                if local_backend
                else "codex_plus_local_harness"
            ),
            "local_harness_mcp_enabled": local_harness_enabled,
            "user_config_enabled": not (isolated_read_only or local_backend),
            "write_capable": sandbox_mode == "workspace-write",
        },
    }


def exec_command_for_seat(
    seat: str,
    seat_config: Mapping[str, Any],
    prompt: str,
    *,
    execution_policy: Mapping[str, Any],
    cwd: Path | None = None,
) -> list[str]:
    exec_cwd = cwd or qwendex_exec_cwd()
    sandbox_mode = str(execution_policy.get("sandbox_mode") or "read-only")
    if seat in {"qwen", "sandbox"}:
        return [
            str(ROOT / "scripts" / "run_local_qwen_codex.sh"),
            "--cwd",
            str(exec_cwd),
            "--sandbox",
            sandbox_mode,
            "--minimal",
            "--ephemeral",
            "--exec",
            prompt,
        ]
    command = [
        "codex",
        "exec",
        "--sandbox",
        sandbox_mode,
    ]
    if execution_policy.get("ignore_user_config"):
        command.extend(["--ignore-user-config", "-c", "mcp_servers={}"])
    if execution_policy.get("local_harness_mcp_enabled"):
        command.extend(codex_mcp_override_args(exec_cwd))
    for key, value in (
        ("tool_output_token_limit", execution_policy.get("tool_output_token_limit")),
        ("model_context_window", execution_policy.get("context_window")),
        ("model_auto_compact_token_limit", execution_policy.get("compact_limit")),
    ):
        if isinstance(value, int) and value > 0:
            command.extend(["-c", f"{key}={value}"])
    command.extend([
        "-m",
        str(execution_policy.get("runtime_model") or seat_config.get("model", "gpt-5.5")),
        "-C",
        str(exec_cwd),
        prompt,
    ])
    return command


def exec_observation(status: str) -> dict[str, Any]:
    return {
        "tool_calls": {"status": status, "count": None, "items": []},
        "files_touched": {"status": status, "items": []},
    }


def command_exec(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    prompt = " ".join(args.prompt).strip()
    if args.synthetic and not is_exact_qwendex_ok(prompt):
        return stable_envelope(
            command="exec",
            status="blocked",
            summary="Synthetic exec supports only the exact QWENDEX_OK offline marker.",
            errors=["--synthetic requires: Reply exactly QWENDEX_OK"],
            data={"execution_performed": False, "availability_evidence": False},
        )
    inferred_task_class = "exec" if is_exact_qwendex_ok(prompt) else infer_task_class(prompt)
    primary_classes = routing_policy(config)["primary_required_for_task_classes"]
    inferred_primary_required = task_class_matches(
        inferred_task_class, primary_classes
    ) or text_contains_any(inferred_task_class, primary_classes)
    task_class = str(
        inferred_task_class
        if inferred_primary_required
        else args.task_class or inferred_task_class
    )
    task_class_source = (
        "prompt_primary_guard"
        if inferred_primary_required
        else "explicit"
        if args.task_class
        else "exact_marker"
        if is_exact_qwendex_ok(prompt)
        else "prompt_inference"
    )
    exec_cwd = qwendex_exec_cwd(args.cwd)
    with connect_state(config) as conn:
        local_enabled = current_local_enabled(config, conn)
    route = resolve_route(
        config,
        requested_seat=args.seat or "auto",
        task_class=task_class,
        env=os.environ,
        prefer_local=args.prefer_local,
        local_enabled=local_enabled,
    )
    seat = route["seat"]
    seat_config = config["seats"].get(seat, config["seats"]["primary"])
    base_execution_policy = seat_execution_policy(config, seat, seat_config)
    configured_timeout = int(base_execution_policy.get("max_wall_time_seconds") or -1)
    timeout_candidates = [
        value for value in (int(args.timeout), configured_timeout) if value > 0
    ]
    effective_timeout = min(timeout_candidates) if timeout_candidates else None
    execution_policy = {
        **base_execution_policy,
        "requested_timeout_seconds": int(args.timeout),
        "effective_timeout_seconds": effective_timeout,
        "mcp_trusted_roots": (
            codex_mcp_trusted_roots(exec_cwd).split(":")
            if base_execution_policy.get("local_harness_mcp_enabled")
            else []
        ),
    }
    runtime_model = str(execution_policy.get("runtime_model") or seat_config.get("model", ""))
    if args.synthetic:
        path = write_receipt(
            config,
            "exec",
            {
                "seat": seat,
                "model": runtime_model,
                "profile": seat,
                "task_class": task_class,
                "task_class_source": task_class_source,
                "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                **exec_observation("not_executed"),
                "markers": [],
                "eval_result": "synthetic_not_evidence",
                "review_status": "synthetic_offline_only",
                "routing": route,
                "execution_policy": execution_policy,
                "execution_performed": False,
                "availability_evidence": False,
                "limitations": [
                    "offline synthetic marker; not model, tool, sandbox, or availability evidence"
                ],
                "output": "QWENDEX_OK",
            },
        )
        return stable_envelope(
            command="exec",
            status="pass",
            summary="QWENDEX_OK (synthetic offline marker; no execution evidence)",
            artifacts=[str(path)],
            next_actions=["Run a normal exec or live eval before making availability claims."],
            data={
                "seat": seat,
                "model": runtime_model,
                "output": "QWENDEX_OK",
                "task_class": task_class,
                "task_class_source": task_class_source,
                "routing": route,
                "execution_policy": execution_policy,
                "execution_performed": False,
                "availability_evidence": False,
            },
        )
    cmd = exec_command_for_seat(
        seat,
        seat_config,
        prompt,
        execution_policy=execution_policy,
        cwd=exec_cwd,
    )
    if args.dry_run:
        return stable_envelope(
            command="exec",
            status="pass",
            summary="Qwendex exec dry run is ready; no execution evidence was produced.",
            data={
                "status": "ready",
                "command": cmd,
                "seat": seat,
                "model": runtime_model,
                "task_class": task_class,
                "task_class_source": task_class_source,
                "routing": route,
                "execution_policy": execution_policy,
                "execution_performed": False,
                "availability_evidence": False,
            },
            next_actions=["Start the stack with scripts/qwendex up before live exec."],
        )
    try:
        result = subprocess.run(
            cmd,
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
            timeout=effective_timeout,
            env={**os.environ, **execution_policy["child_env"]},
        )
    except subprocess.TimeoutExpired as exc:
        path = write_receipt(
            config,
            "exec",
            {
                "seat": seat,
                "model": runtime_model,
                "profile": seat,
                "task_class": task_class,
                "task_class_source": task_class_source,
                "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                **exec_observation("not_observed"),
                "markers": ["QWENDEX_TIMEOUT"],
                "eval_result": "fail",
                "review_status": "timeout",
                "routing": route,
                "execution_policy": execution_policy,
                "execution_performed": True,
                "availability_evidence": False,
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
            next_actions=["Retry with a smaller prompt or a larger bounded timeout."],
            errors=[subprocess_failure_tail(exc) or "timeout"],
            data={
                "seat": seat,
                "model": runtime_model,
                "markers": ["QWENDEX_TIMEOUT"],
                "task_class": task_class,
                "task_class_source": task_class_source,
                "routing": route,
                "execution_policy": execution_policy,
                "execution_performed": True,
                "availability_evidence": False,
            },
        )
    markers = [
        marker
        for marker in config["guard"]["markers"]
        if marker in (result.stdout + result.stderr)
    ]
    status = "pass" if result.returncode == 0 and not markers else "fail"
    local_review_required = seat_uses_local_qwen(config, seat)
    failure_error = (
        f"guard markers detected: {', '.join(markers)}"
        if markers
        else subprocess_failure_tail(result)
    )
    path = write_receipt(
        config,
        "exec",
        {
            "seat": seat,
            "model": runtime_model,
            "profile": seat,
            "task_class": task_class,
            "task_class_source": task_class_source,
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            **exec_observation("not_observed"),
            "markers": markers,
            "eval_result": status,
            "review_status": "requires_gpt_review" if local_review_required else f"{seat}_review",
            "routing": route,
            "execution_policy": execution_policy,
            "execution_performed": True,
            "availability_evidence": result.returncode == 0 and not markers,
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
        next_actions=["Review the receipt before accepting model output."],
        errors=[] if status == "pass" else [failure_error],
        data={
            "seat": seat,
            "model": runtime_model,
            "markers": markers,
            "task_class": task_class,
            "task_class_source": task_class_source,
            "routing": route,
            "execution_policy": execution_policy,
            "execution_performed": True,
            "availability_evidence": result.returncode == 0 and not markers,
        },
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
    if route["seat"] == "qwen":
        next_actions = ["Review Qwen receipts with a GPT/Codex authority seat before release acceptance."]
    elif route["reason"] == "primary_authority_required":
        next_actions = ["Keep this task on a GPT/Codex authority seat."]
    elif route["reason"] == "local_subagents_disabled":
        next_actions = ["Enable Local only if this bounded task should use the local Qwen seat."]
    elif route.get("local_qwen", {}).get("available") is False:
        next_actions = ["Start the local stack with scripts/qwendex up if you want auto routing to prefer Qwen."]
    else:
        next_actions = []
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
            data={"configured_seats": sorted(config["seats"])},
        )
    seat_config = config["seats"][args.seat]
    review_status = "configured_requires_gpt_review" if args.seat == "qwen" else "configured_only"
    execution_policy = seat_execution_policy(config, args.seat, seat_config)
    path = write_receipt(
        config,
        "seat",
        {
            "seat": args.seat,
            "model": seat_config.get("model", ""),
            "profile": args.seat,
            "task_class": "seat_configuration",
            **exec_observation("not_executed"),
            "markers": [],
            "eval_result": "not_run",
            "review_status": review_status,
            "authority": seat_config.get("authority", ""),
            "execution_policy": execution_policy,
            "availability": {"status": "not_probed", "evidence": False},
        },
    )
    return stable_envelope(
        command="seat",
        status="pass",
        summary=f"Qwendex seat {args.seat} is configured; availability was not probed.",
        artifacts=[str(path)],
        next_actions=["Run scripts/qwendex eval --json"],
        data={
            "seat": args.seat,
            "profile": seat_config,
            "review_status": review_status,
            "execution_policy": execution_policy,
            "availability": {"status": "not_probed", "evidence": False},
        },
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


def is_learning_preflight_path_allowed(path: Path) -> bool:
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
    learning = config.get("learning", {})
    learning_mode = str(learning.get("mode") or "stage_only")
    effective_backend = str(args.backend or learning.get("default_backend") or "mock").strip()
    backend_source = "cli" if args.backend else "config_default"
    if learning_mode == "disabled":
        if args.action == "status":
            return stable_envelope(
                command="learn",
                status="pass",
                summary="Qwendex learning is disabled by configuration.",
                data={
                    "status": "disabled",
                    "source": "builtin_status",
                    "learning_mode": learning_mode,
                    "backend": effective_backend,
                    "backend_source": backend_source,
                    "execution_performed": False,
                    "mutation_performed": False,
                    "proposal_generated": False,
                    "adoption_performed": False,
                },
            )
        return stable_envelope(
            command="learn",
            status="blocked",
            summary="Qwendex learning is disabled by configuration.",
            errors=["learning.mode=disabled"],
            data={
                "status": "disabled",
                "learning_mode": learning_mode,
                "backend": effective_backend,
                "backend_source": backend_source,
                "execution_performed": False,
                "mutation_performed": False,
                "proposal_generated": False,
                "adoption_performed": False,
            },
        )
    if (
        args.action == "dry-run"
        and effective_backend == "mock"
        and shutil.which("skillopt-sleep") is None
    ):
        return stable_envelope(
            command="learn",
            status="pass",
            summary=(
                "Qwendex built-in mock learning dry-run contract passed; "
                "no external execution, proposal generation, or adoption occurred."
            ),
            next_actions=["Install skillopt-sleep only if an external learning proposal run is needed."],
            data={
                "status": "pass",
                "source": "builtin_mock",
                "action": "dry-run",
                "learning_mode": learning_mode,
                "backend": effective_backend,
                "backend_source": backend_source,
                "external_tool": {"name": "skillopt-sleep", "available": False},
                "execution_performed": False,
                "mutation_performed": False,
                "proposal_generated": False,
                "adoption_performed": False,
            },
        )
    if args.action in {"stage", "audit", "proposal-summary"}:
        module = script_module("local_qwen_skillopt_wrapper")
        data = module.proposal_summary(ROOT)
        data.update({
            "source": "builtin_staging_inspection",
            "learning_mode": learning_mode,
            "execution_performed": False,
            "mutation_performed": False,
            "proposal_generated": False,
            "adoption_performed": False,
        })
        if args.action == "audit":
            data["preflight_denied_paths"] = [
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
            summary="Qwendex inspected the learning proposal staging area; no proposal was generated or adopted.",
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
            if not is_learning_preflight_path_allowed(path)
        ]
        if not args.approve:
            return stable_envelope(
                command="learn",
                status="blocked",
                summary="Learning allowlist preflight requires explicit approval; no adoption is performed.",
                errors=["explicit preflight approval required"],
                data={
                    "proposal": str(proposal),
                    "unsafe_paths": unsafe,
                    "proposal_errors": report["errors"],
                    "preflight_performed": False,
                    "adoption_performed": False,
                    "mutation_performed": False,
                },
            )
        if report["errors"]:
            return stable_envelope(
                command="learn",
                status="blocked",
                summary="Learning allowlist preflight requires a valid proposal with path metadata; no adoption was performed.",
                errors=list(report["errors"]),
                data={
                    "proposal": str(proposal),
                    "preflight_performed": True,
                    "preflight_status": "blocked",
                    "adoption_performed": False,
                    "mutation_performed": False,
                },
            )
        if unsafe:
            return stable_envelope(
                command="learn",
                status="blocked",
                summary="Learning allowlist preflight rejected denied paths; no adoption was performed.",
                errors=unsafe,
                data={
                    "proposal": str(proposal),
                    "unsafe_paths": unsafe,
                    "preflight_performed": True,
                    "preflight_status": "blocked",
                    "adoption_performed": False,
                    "mutation_performed": False,
                },
            )
        return stable_envelope(
            command="learn",
            status="pass",
            summary="Learning proposal passed the allowlist preflight; no files were adopted.",
            artifacts=[str(proposal)] if proposal else [],
            next_actions=["Review and apply any desired proposal changes manually."],
            data={
                "proposal": str(proposal),
                "paths": [repo_relative_candidate(path) or path.as_posix() for path in paths],
                "preflight_performed": True,
                "preflight_status": "pass",
                "allowlisted": True,
                "adoption_performed": False,
                "mutation_performed": False,
            },
        )
    if args.action == "rollback":
        return stable_envelope(
            command="learn",
            status="blocked",
            summary="Learning rollback is unavailable because Qwendex performs allowlist preflight only and does not adopt files.",
            errors=["no Qwendex adoption operation exists to roll back"],
            data={"adoption_performed": False, "mutation_performed": False},
        )
    module = script_module("local_qwen_skillopt_wrapper")
    data = module.run_skillopt_action(
        args.action,
        project=ROOT,
        backend=effective_backend,
        source=args.source,
        json_output=args.json,
        allow_codex_budget=args.allow_codex_budget,
        execute=not args.no_execute,
    )
    status = "pass" if data.get("status") in {"pass", "ready"} else data.get("status", "fail")
    data["learning_mode"] = learning_mode
    data["backend"] = data.get("backend") or effective_backend
    data["backend_source"] = backend_source
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
    return 30


def manager_override_errors(args: argparse.Namespace) -> list[str]:
    errors: list[str] = []
    max_subagents = int(getattr(args, "max_subagents", 0) or 0)
    stale_after = int(getattr(args, "stale_after_minutes", 0) or 0)
    limit = int(getattr(args, "limit", 20) or 0)
    if max_subagents and not 1 <= max_subagents <= MANAGER_MAX_SUBAGENTS_LIMIT:
        errors.append(
            f"max_subagents must be between 1 and {MANAGER_MAX_SUBAGENTS_LIMIT}: {max_subagents}"
        )
    if stale_after and not 5 <= stale_after <= 240:
        errors.append(f"stale_after_minutes must be between 5 and 240: {stale_after}")
    if limit < 1 or limit > 1000:
        errors.append(f"limit must be between 1 and 1000: {limit}")
    return errors


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
    resolved_agent_policy = attach_local_routing_snapshot(
        resolved_agent_policy,
        config,
        enabled=bool(local_status.get("enabled")),
    )
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


def command_performance(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    """Expose local-only aggregate exploration telemetry without raw-event export."""
    action = str(getattr(args, "action", "") or "status")
    settings = performance_config(config)
    module = performance_module()
    database = performance_db_path(config)
    if action == "status":
        return stable_envelope(
            command="performance",
            status="pass",
            summary="Loaded local Qwendex performance telemetry status.",
            data={
                "capture": settings["capture"],
                "retention_days": settings["retention_days"],
                "max_events": settings["max_events"],
                "query_fingerprints": settings["query_fingerprints"],
                "telemetry": module.status(database),
            },
        )
    if action == "summary":
        repo_root = str(getattr(args, "repo_root", "") or "").strip() or canonical_manager_repo_root()
        repository_scope_digest = performance_repository_scope_digest(repo_root)
        payload = module.summary(
            database,
            retention_days=settings["retention_days"],
            max_events=settings["max_events"],
            repository_scope_digest=repository_scope_digest,
            since_days=max(0, int(getattr(args, "since_days", 0) or 0)),
        )
        return stable_envelope(
            command="performance",
            status="pass",
            summary="Built deterministic local Qwendex performance summary.",
            data={"summary": payload, "capture": settings["capture"]},
        )
    if action == "runs":
        payload = module.runs(
            database,
            limit=max(1, min(100, int(getattr(args, "limit", 20) or 20))),
            repository_scope_digest=performance_repository_scope_digest(),
        )
        return stable_envelope(
            command="performance",
            status="pass",
            summary=f"Loaded {len(payload)} local Qwendex performance run summaries.",
            data={"runs": payload, "capture": settings["capture"]},
        )
    if action == "purge":
        if not bool(getattr(args, "approve", False)):
            return stable_envelope(
                command="performance",
                status="blocked",
                summary="Purging local performance telemetry requires --approve.",
                errors=["explicit approval required"],
            )
        return stable_envelope(
            command="performance",
            status="pass",
            summary="Purged local Qwendex performance telemetry.",
            data={"purge": module.purge(database)},
        )
    if action == "benchmark":
        if str(getattr(args, "suite", "") or "") != "exploration":
            return stable_envelope(
                command="performance",
                status="blocked",
                summary="Unsupported Qwendex performance benchmark suite.",
                errors=[str(getattr(args, "suite", "") or "missing suite")],
            )
        payload = module.benchmark()
        return stable_envelope(
            command="performance",
            status=str(payload.get("status") or "blocked"),
            summary="Ran isolated Qwendex exploration telemetry benchmark.",
            data={"benchmark": payload},
        )
    if action == "lab":
        lab_action = str(getattr(args, "lab_action", "") or "")
        module = optimization_lab_module()
        if lab_action == "validate":
            payload = module.validate_workload(Path(getattr(args, "manifest", "")))
            return stable_envelope(
                command="performance",
                status=str(payload.get("status") or "fail"),
                summary="Validated the frozen Qwendex optimization-lab workload manifest.",
                errors=list(payload.get("errors") or []),
                data={"lab": payload},
            )
        if lab_action == "baseline":
            try:
                payload = module.baseline_capture(
                    Path(getattr(args, "manifest", "")),
                    output_root=(Path(args.output_root) if str(getattr(args, "output_root", "") or "") else None),
                )
            except (OSError, ValueError) as exc:
                return stable_envelope(
                    command="performance",
                    status="fail",
                    summary="Could not capture the Qwendex optimization-lab baseline.",
                    errors=[str(exc)],
                )
            return stable_envelope(
                command="performance",
                status=str(payload.get("status") or "fail"),
                summary=str(payload.get("summary") or "Captured Qwendex optimization-lab baseline."),
                data={"lab": payload.get("data", {})},
            )
        if lab_action == "run":
            try:
                payload = module.paired_run(
                    Path(getattr(args, "manifest", "")),
                    candidate_id=str(getattr(args, "candidate", "") or ""),
                    output_root=(Path(args.output_root) if str(getattr(args, "output_root", "") or "") else None),
                )
            except (OSError, ValueError) as exc:
                return stable_envelope(
                    command="performance",
                    status="fail",
                    summary="Could not run the Qwendex optimization-lab paired evaluation.",
                    errors=[str(exc)],
                )
            return stable_envelope(
                command="performance",
                status=str(payload.get("status") or "fail"),
                summary=str(payload.get("summary") or "Ran Qwendex optimization-lab paired evaluation."),
                data={"lab": payload.get("data", {})},
            )
        if lab_action == "live-run":
            try:
                payload = module.live_paired_run(
                    Path(getattr(args, "manifest", "")),
                    candidate_id=str(getattr(args, "candidate", "") or ""),
                    auth_source=Path(getattr(args, "auth_source", "")),
                    supervisor_policy=Path(getattr(args, "supervisor_policy", "")),
                    output_root=(Path(args.output_root) if str(getattr(args, "output_root", "") or "") else None),
                )
            except (OSError, ValueError) as exc:
                return stable_envelope(
                    command="performance",
                    status="fail",
                    summary="Could not run the Qwendex held-out live-agent paired evaluation.",
                    errors=[str(exc)],
                )
            return stable_envelope(
                command="performance",
                status=str(payload.get("status") or "fail"),
                summary=str(payload.get("summary") or "Ran Qwendex held-out live-agent paired evaluation."),
                data={"lab": payload.get("data", {})},
            )
        if lab_action == "calibrate":
            try:
                payload = module.live_runtime_calibration(
                    Path(getattr(args, "manifest", "")),
                    auth_source=Path(getattr(args, "auth_source", "")),
                    task_id=str(getattr(args, "task_id", "") or ""),
                    secondary_task_id=str(getattr(args, "secondary_task_id", "") or ""),
                    output_root=(Path(args.output_root) if str(getattr(args, "output_root", "") or "") else None),
                )
            except (OSError, ValueError) as exc:
                return stable_envelope(
                    command="performance",
                    status="blocked",
                    summary="Could not calibrate the Qwendex live-runtime supervisor.",
                    errors=[str(exc)],
                )
            return stable_envelope(
                command="performance",
                status=str(payload.get("status") or "blocked"),
                summary=str(payload.get("summary") or "Calibrated the Qwendex live-runtime supervisor."),
                data={"lab": payload.get("data", {})},
            )
        if lab_action == "runtime-closeout":
            try:
                validation_path = Path(getattr(args, "validation_summary", "")) if str(getattr(args, "validation_summary", "") or "") else None
                validation_summary = json.loads(validation_path.read_text(encoding="utf-8")) if validation_path else {"supervisor_tests": "not_observed"}
                if not isinstance(validation_summary, Mapping):
                    raise ValueError("validation summary must be a JSON object")
                payload = module.live_runtime_stability_closeout(
                    prior_run_dir=Path(getattr(args, "prior_run", "")),
                    calibration_run_dir=Path(getattr(args, "calibration_run", "")),
                    validation_summary=validation_summary,
                    output_root=(Path(args.output_root) if str(getattr(args, "output_root", "") or "") else None),
                )
            except (OSError, ValueError) as exc:
                return stable_envelope(
                    command="performance",
                    status="blocked",
                    summary="Could not write the Qwendex live-runtime stability closeout.",
                    errors=[str(exc)],
                )
            return stable_envelope(
                command="performance",
                status=str(payload.get("status") or "blocked"),
                summary=str(payload.get("summary") or "Wrote the Qwendex live-runtime stability closeout."),
                data={"lab": payload.get("data", {})},
            )
        if lab_action == "compare":
            payload = module.compare_run(Path(getattr(args, "run_dir", "")))
            return stable_envelope(
                command="performance",
                status=str(payload.get("status") or "fail"),
                summary=str(payload.get("summary") or "Compared Qwendex optimization-lab paired evaluation artifacts."),
                errors=list(payload.get("errors") or []),
                data={"lab": payload.get("data", {})},
            )
        return stable_envelope(
            command="performance",
            status="blocked",
            summary=f"Unknown Qwendex optimization-lab action: {lab_action}",
            errors=[lab_action or "missing lab action"],
        )
    return stable_envelope(
        command="performance",
        status="blocked",
        summary=f"Unknown Qwendex performance action: {action}",
        errors=[action],
    )


def command_search(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    """Expose explicit, default-off experimental search evidence compaction."""
    _ = config
    action = str(getattr(args, "action", "") or "")
    mode = "literal" if bool(getattr(args, "literal", False)) else "regex"
    module = qwendex_search_module()
    try:
        if action == "content":
            payload = module.content_search_payload(
                str(getattr(args, "pattern", "") or ""),
                root=Path(getattr(args, "root", "")),
                mode=mode,
                include_ignored=bool(getattr(args, "include_ignored", False)),
                max_files=max(1, int(getattr(args, "max_files", 100_000) or 100_000)),
                per_file_ranges=max(1, int(getattr(args, "per_file_ranges", 12) or 12)),
                total_ranges=max(1, int(getattr(args, "total_ranges", 96) or 96)),
                max_files_evidence=max(1, int(getattr(args, "max_evidence_files", 64) or 64)),
                page_size=max(1, int(getattr(args, "page_size", 96) or 96)),
                page_token=str(getattr(args, "page_token", "") or ""),
                candidate_id=str(getattr(args, "candidate", "v1") or "v1"),
            )
            return stable_envelope(
                command="search",
                status="pass",
                summary="Ran explicit experimental Qwendex compact content search.",
                data={"search": payload},
            )
        if action == "next":
            payload = module.content_search_next_payload(
                str(getattr(args, "pattern", "") or ""),
                root=Path(getattr(args, "root", "")),
                mode=mode,
                cursor=str(getattr(args, "cursor", "") or ""),
                include_ignored=bool(getattr(args, "include_ignored", False)),
                max_files=max(1, int(getattr(args, "max_files", 100_000) or 100_000)),
                per_file_ranges=max(1, int(getattr(args, "per_file_ranges", 12) or 12)),
                total_ranges=max(1, int(getattr(args, "total_ranges", 96) or 96)),
                max_files_evidence=max(1, int(getattr(args, "max_evidence_files", 64) or 64)),
                page_size=max(1, int(getattr(args, "page_size", 96) or 96)),
                candidate_id=str(getattr(args, "candidate", "v2") or "v2"),
            )
            return stable_envelope(
                command="search",
                status="pass",
                summary="Retrieved the next explicit recall-preserving Qwendex search page.",
                data={"search": payload},
            )
        if action == "paths":
            payload = module.path_search_payload(
                str(getattr(args, "pattern", "") or ""),
                root=Path(getattr(args, "root", "")),
                mode=mode,
                include_ignored=bool(getattr(args, "include_ignored", False)),
                max_files=max(1, int(getattr(args, "max_files", 100_000) or 100_000)),
                page_size=max(1, int(getattr(args, "page_size", 100) or 100)),
                page_token=str(getattr(args, "page_token", "") or ""),
            )
            return stable_envelope(
                command="search",
                status="pass",
                summary="Ran explicit experimental Qwendex repository-bounded path search.",
                data={"search": payload},
            )
    except (OSError, ValueError) as exc:
        return stable_envelope(
            command="search",
            status="blocked",
            summary="Experimental Qwendex search request was rejected.",
            errors=[str(exc)],
        )
    return stable_envelope(
        command="search",
        status="blocked",
        summary=f"Unknown Qwendex search action: {action}",
        errors=[action or "missing search action"],
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


def routing_assignment_label(routing: Mapping[str, Any]) -> str:
    model = str(routing.get("selected_model") or "user-selected")
    reasoning = str(routing.get("selected_reasoning") or "user-selected")
    token_saver = bool(routing.get("token_saver_used"))
    suffix = " with token_saver=true" if token_saver else ""
    return f"model={model}, reasoning={reasoning}{suffix}"


def spawn_instruction(agent_id: str, routing: Mapping[str, Any]) -> str:
    reasoning = str(routing.get("selected_reasoning") or "inherited")
    return (
        f"spawn_agent for {agent_id} using the Qwendex lane assignment; "
        f"keep model selection inherited from Codex and use reasoning={reasoning} only when the native profile supports it."
    )


def kaveman_context(config: Mapping[str, Any]) -> str:
    try:
        with connect_state(config) as conn:
            enabled = current_kaveman_enabled(config, conn)
    except Exception:
        return ""
    directive = kaveman_directive(config) if enabled else ""
    return f"Kaveman directive: {directive}" if directive else ""


def agent_output_policy_context(agent_policy: Mapping[str, Any], config: Mapping[str, Any] | None = None) -> str:
    output_policy = agent_policy.get("output_policy", {})
    if isinstance(output_policy, Mapping) and output_policy.get("kaveman_enabled"):
        directive = str(output_policy.get("directive") or "")
        return f"Qwendex output policy: Kaveman enabled. Kaveman directive: {directive}" if directive else "Qwendex output policy: Kaveman enabled."
    return ""


def hook_local_subagent_status(config: Mapping[str, Any]) -> dict[str, Any]:
    try:
        with connect_state(config) as conn:
            enabled = current_local_enabled(config, conn)
        return local_subagent_status(config, enabled=enabled, env=os.environ, probe=True)
    except Exception as exc:
        policy = routing_policy(config)
        return {
            "enabled": False,
            "available": False,
            "usable": False,
            "local_enabled": False,
            "local_available": False,
            "local_usable": False,
            "local_state": "unavailable",
            "indicator": local_indicator(config, False, "off"),
            "source": "fallback",
            "reason": redact_text(str(exc) or exc.__class__.__name__),
            "model": policy["local_model"],
        }


def agent_mode_context(
    agent_policy: Mapping[str, Any],
    *,
    config: Mapping[str, Any] | None = None,
) -> str:
    mode = str(agent_policy.get("mode") or "medium")
    contracts = {
        "off": "Off mode: do not spawn subagents unless explicitly requested; keep work in the main session.",
        "auto": "Auto mode: use the task estimate to decide whether bounded specialist lanes are useful.",
        "lite": "Lite mode: prefer direct work. Do not spawn subagents unless explicitly requested or required by policy.",
        "medium": "Medium mode: use a small number of specialists when exploration or verification materially improves quality.",
        "heavy": "Heavy mode: prefer scoped specialist lanes for non-trivial repo work and use a verifier when it materially improves confidence.",
        "manager": (
            "Manager Mode: you are the root orchestrator and context curator. "
            "For non-trivial repo work, use scoped specialists when they save context or improve quality. "
            "The Qwendex plan and ledger are advisory aids; the user's instruction and your judgment control the work. "
            "A worker's final response is delivered directly to you; integrate it before deciding whether follow-up is useful. "
            "If a verifier reports stale evidence after remediation, a bounded follow-up to that verifier is often more efficient than creating duplicate verification work. "
            "Call wait_agent only while list_agents shows a running worker. After a wait timeout, inspect list_agents once; if no worker is running, do not retry wait_agent and instead integrate terminal evidence or finalize. "
            "Concise worker outcomes and validation evidence are useful for review but never gate the user's prompt, tools, publication commands, or final response. "
            "Small or tightly coupled tasks may be handled directly."
        ),
    }
    parts = [
        f"Active Qwendex agent mode: {agent_policy.get('agent_use')}. {contracts.get(mode, contracts['medium'])}",
    ]
    if config is not None:
        if output_context := agent_output_policy_context(agent_policy, config=config):
            parts.append(output_context)
    if search_context := experimental_search_candidate_context():
        parts.append(search_context)
    return " ".join(parts)


def event_model_reasoning(event: Mapping[str, Any]) -> dict[str, Any]:
    for key in ("model_reasoning_assignment", "routing"):
        value = event.get(key)
        if isinstance(value, Mapping):
            return dict(value)
    model = str(event.get("selected_model") or event.get("model") or "")
    reasoning = str(event.get("selected_reasoning") or event.get("reasoning") or "")
    if model or reasoning:
        return {
            "selected_model": model or "user-selected",
            "selected_reasoning": reasoning or "user-selected",
            "token_saver_used": bool(event.get("token_saver_used")),
            "reasoning_source": str(event.get("reasoning_source") or "event"),
        }
    return {}


def session_model_reasoning(config: Mapping[str, Any], agent_id: str) -> dict[str, Any]:
    if not agent_id:
        return {}
    try:
        with connect_state(config) as conn:
            row = conn.execute("SELECT * FROM qwendex_agent_sessions WHERE agent_id = ?", (agent_id,)).fetchone()
    except Exception:
        return {}
    session = row_to_agent_session(row) or {}
    routing = session.get("routing")
    if isinstance(routing, Mapping) and routing:
        return dict(routing)
    packet = session.get("context_packet")
    if isinstance(packet, Mapping) and isinstance(packet.get("model_reasoning_assignment"), Mapping):
        return dict(packet["model_reasoning_assignment"])
    return {}


def subagent_start_context(
    config: Mapping[str, Any],
    event: Mapping[str, Any],
    agent_policy: Mapping[str, Any],
) -> str:
    agent_id = str(event.get("agent_id") or "unknown")
    agent_type = str(event.get("agent_type") or event.get("profile") or "unknown")
    task_name = str(event.get("task_name") or event.get("task") or "assigned task")
    routing = event_model_reasoning(event) or session_model_reasoning(config, agent_id)
    assignment = (
        f" Use the registered Qwendex lane assignment with reasoning={routing.get('selected_reasoning') or 'inherited'}; model selection remains inherited from Codex."
        if routing
        else " Use the lane assignment supplied by the manager/root context; model selection remains inherited from Codex."
    )
    output_context = agent_output_policy_context(agent_policy, config=config)
    output_sentence = f" {output_context}" if output_context else ""
    return (
        f"You are Qwendex subagent {agent_id} of type {agent_type}. "
        f"Parent mode is {agent_policy.get('agent_use')}. Execute {task_name} now. "
        f"{assignment}{output_sentence} "
        "Do not merely acknowledge or stand by. Do not spawn subagents. "
        "Stay within the assigned lane and summarize the outcome, evidence, changed paths, and remaining risk concisely. "
        "A structured FINAL_REPORT is welcome when convenient, but ordinary clear output is accepted."
    )


def parse_worker_final_status(message: str) -> dict[str, Any]:
    text = message or ""
    has_final_report = re.search(r"(?im)^\s*FINAL_REPORT\s*$", text) is not None
    if not has_final_report and re.search(r"(?im)^\s*BLOCKED(?:\s*:.*)?\s*$", text):
        return {"has_contract": True, "final_report_present": False, "status": "blocked", "validation_status": "fail", "reason": "blocked_contract"}
    if not has_final_report and re.search(r"(?im)^\s*FAILED(?:\s*:.*)?\s*$", text):
        return {"has_contract": True, "final_report_present": False, "status": "failed", "validation_status": "fail", "reason": "failed_contract"}
    if not has_final_report:
        return {"has_contract": False, "final_report_present": False, "status": "", "validation_status": "pending", "reason": "missing_final_contract"}
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
    return {"has_contract": True, "final_report_present": True, "status": status, "validation_status": validation, "reason": "final_report"}


def transition_agent_session(
    conn: sqlite3.Connection,
    *,
    agent_id: str,
    status: str,
    validation_status: str,
    now: str,
    reason: str,
    final_report_present: bool | None = None,
    close_receipt: str = "",
    artifacts: list[str] | None = None,
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM qwendex_agent_sessions WHERE agent_id = ?",
        (agent_id,),
    ).fetchone()
    if row is None:
        return None
    stored_artifacts = json_loads_list(row["artifacts_json"])
    for artifact in artifacts or []:
        if artifact and artifact not in stored_artifacts:
            stored_artifacts.append(artifact)
    terminal = status in AGENT_TERMINAL_STATUSES
    conn.execute(
        """
        UPDATE qwendex_agent_sessions
        SET status = ?, validation_status = ?, heartbeat_at = ?, updated_at = ?,
            stop_reason = ?, artifacts_json = ?,
            final_report_present = CASE WHEN ? IS NULL THEN final_report_present ELSE ? END,
            completed_at = ?,
            close_receipt = CASE WHEN ? = '' THEN close_receipt ELSE ? END
        WHERE agent_id = ?
        """,
        (
            status,
            validation_status,
            now,
            now,
            reason,
            json_dumps(stored_artifacts),
            None if final_report_present is None else int(final_report_present),
            0 if final_report_present is None else int(final_report_present),
            now if terminal else "",
            close_receipt,
            close_receipt,
            agent_id,
        ),
    )
    if terminal:
        release_agent_locks(conn, agent_id, now=now)
    updated = conn.execute(
        "SELECT * FROM qwendex_agent_sessions WHERE agent_id = ?",
        (agent_id,),
    ).fetchone()
    return row_to_agent_session(updated)


def update_agent_from_final_contract(
    conn: sqlite3.Connection,
    *,
    agent_id: str,
    final_status: Mapping[str, Any],
    now: str,
    artifacts: list[str] | None = None,
) -> dict[str, Any] | None:
    if not agent_id:
        return None
    updated = transition_agent_session(
        conn,
        agent_id=agent_id,
        status=str(final_status.get("status") or "completed"),
        validation_status=str(final_status.get("validation_status") or "pending"),
        now=now,
        reason=str(final_status.get("reason") or "final_report"),
        final_report_present=bool(final_status.get("final_report_present")),
        artifacts=artifacts,
    )
    conn.commit()
    return updated


def session_attention_flagged(session: Mapping[str, Any]) -> bool:
    packet = session.get("context_packet", {})
    return bool(isinstance(packet, Mapping) and packet.get("required"))


def session_lane(session: Mapping[str, Any]) -> str:
    return str(session.get("lane") or "").strip().lower()


def waived_lanes(sessions: list[dict[str, Any]]) -> set[str]:
    return {
        session_lane(session)
        for session in sessions
        if str(session.get("status") or "") == "waived" and session_lane(session)
    }


def verifier_waived(sessions: list[dict[str, Any]]) -> bool:
    return any(
        str(session.get("status") or "") == "waived" and session_is_verifier(session)
        for session in sessions
    )


def missing_planned_required_lanes(
    decision: Mapping[str, Any],
    sessions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    plan = decision.get("agent_plan")
    if not isinstance(plan, Mapping):
        return []
    required = plan.get("required_lanes")
    if not isinstance(required, list):
        return []
    registered = {
        str(session.get("lane") or "").strip().lower()
        for session in sessions
        if str(session.get("lane") or "").strip()
    }
    missing: list[dict[str, Any]] = []
    for item in required:
        if not isinstance(item, Mapping):
            continue
        lane = str(item.get("lane") or "").strip().lower()
        if lane and lane not in registered:
            missing.append(dict(item))
    return missing


def planned_assignment_key(assignment: Mapping[str, Any]) -> tuple[str, str]:
    return (
        str(assignment.get("lane") or "").strip().lower(),
        str(assignment.get("profile") or "").strip().lower(),
    )


def registered_assignment_keys(sessions: list[dict[str, Any]]) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    for session in sessions:
        packet = session.get("context_packet")
        packet = packet if isinstance(packet, Mapping) else {}
        lane = str(session.get("lane") or packet.get("planned_lane") or "").strip().lower()
        profile = str(packet.get("planned_profile") or session.get("owner") or "").strip().lower()
        if lane:
            keys.add((lane, profile))
            keys.add((lane, ""))
    return keys


def reserve_manager_native_spawn(
    config: Mapping[str, Any],
    event: Mapping[str, Any],
    agent_policy: Mapping[str, Any],
    decision: Mapping[str, Any],
) -> dict[str, Any]:
    """Reserve one persisted planned lane before native Codex creates it."""
    repo_root = str(decision.get("repo_root") or canonical_manager_repo_root(event=event))
    task_id = str(decision.get("agent_task_id") or decision.get("session_id") or "")
    plan = decision.get("agent_plan")
    plan = plan if isinstance(plan, Mapping) else {}
    assignments = [
        dict(item)
        for item in list(plan.get("assignments") or [])
        if isinstance(item, Mapping)
    ]
    if str(decision.get("selected_route") or "") != "manager_subagents" or not assignments:
        return {
            "decision": "block",
            "event": "manager.unplanned_spawn_rejected",
            "reason": "This admitted turn has no planned agent lane; continue directly or start a new turn whose prompt justifies delegation.",
        }
    tool_use_id = str(event.get("tool_use_id") or "").strip()
    if not tool_use_id:
        return {
            "decision": "block",
            "event": "manager.spawn_identity_missing",
            "reason": "Native spawn admission requires tool_use_id so the worker can be reserved without duplication.",
        }
    now = utc_now()
    with connect_state(config) as conn:
        if busy_error := begin_immediate(conn):
            return {
                "decision": "block",
                "event": "manager.state_busy",
                "reason": "Qwendex manager state remained busy during native spawn admission.",
                "busy_error": busy_error,
            }
        rows = conn.execute(
            """
            SELECT * FROM qwendex_agent_sessions
            WHERE repo_root = ? AND task_id = ?
            ORDER BY created_at ASC
            """,
            (repo_root, task_id),
        ).fetchall()
        sessions = [item for row in rows if (item := row_to_agent_session(row))]
        for session in sessions:
            packet = session.get("context_packet")
            packet = packet if isinstance(packet, Mapping) else {}
            if (
                str(session.get("status") or "") == "reserved"
                and str(session.get("origin") or "") == "qwendex"
                and str(packet.get("pending_tool_use_id") or "") == tool_use_id
            ):
                conn.commit()
                return {
                    "event": "manager.native_spawn_reserved",
                    "reservation": session,
                    "idempotent_reuse": True,
                }
        active_count = sum(
            1 for session in sessions
            if str(session.get("status") or "") not in AGENT_TERMINAL_STATUSES
        )
        policy_snapshot = decision.get("policy_snapshot")
        policy_snapshot = policy_snapshot if isinstance(policy_snapshot, Mapping) else agent_policy
        max_workers = int(
            plan.get("max_workers")
            or policy_snapshot.get("max_workers")
            or policy_snapshot.get("max_threads")
            or 0
        )
        if max_workers <= 0 or active_count >= max_workers:
            conn.rollback()
            return {
                "decision": "block",
                "event": "manager.capacity_reached",
                "reason": f"Native worker capacity is {max_workers}; {active_count} lane(s) are already active or reserved.",
                "active_count": active_count,
                "max_workers": max_workers,
            }
        registered = registered_assignment_keys(sessions)
        remaining = [
            assignment
            for assignment in assignments
            if planned_assignment_key(assignment) not in registered
            and (planned_assignment_key(assignment)[0], "") not in registered
        ]
        if not remaining:
            conn.rollback()
            return {
                "decision": "block",
                "event": "manager.planned_lanes_satisfied",
                "reason": "Every planned lane is already registered; duplicate native workers are denied.",
            }
        tool_input = event.get("tool_input")
        tool_input = tool_input if isinstance(tool_input, Mapping) else {}
        requested_task_name = str(tool_input.get("task_name") or "").strip()
        matched = [
            assignment
            for assignment in remaining
            if requested_task_name == str(assignment.get("agent_id") or "").strip()
        ]
        if len(matched) == 1:
            assignment = matched[0]
        else:
            conn.rollback()
            expected = ", ".join(str(item.get("agent_id") or item.get("lane") or "") for item in remaining)
            return {
                "decision": "block",
                "event": "manager.spawn_lane_identity_mismatch",
                "reason": f"Spawn one exact planned lane by using its planned agent id as task_name: {expected}.",
                "remaining_lane_count": len(remaining),
            }
        pending_agent_id = f"native-pending-{sha256_text(tool_use_id)[:20]}"
        lane = str(assignment.get("lane") or "")
        profile = str(assignment.get("profile") or "worker")
        required = bool(assignment.get("required"))
        context_packet = {
            "objective": str(assignment.get("assignment") or assignment.get("stop_condition") or ""),
            "task_class": str(plan.get("task_class") or ""),
            "allowed_scope": "read-only",
            "required": required,
            "exact_files": [],
            "needed_docs": [],
            "stop_condition": str(assignment.get("stop_condition") or "return a concise worker outcome"),
            "expected_artifact": "worker outcome",
            "receipt_path": "",
            "context_budget": int(agent_policy.get("max_inherited_context_bytes") or 0),
            "model_reasoning_assignment": dict(assignment.get("routing") or {}),
            "review_requirement": "root review suggested",
            "risk": str((plan.get("estimate") or {}).get("risk") or "medium"),
            "planned_agent_id": str(assignment.get("agent_id") or ""),
            "planned_lane": lane,
            "planned_profile": profile,
            "pending_tool_use_id": tool_use_id,
            "launch_ledger_id": str(decision.get("launch_ledger_id") or decision.get("ledger_id") or ""),
            "root_session_id": str(decision.get("root_session_id") or ""),
            "runtime": "native_v2",
            "runtime_state": "reserved",
        }
        conn.execute(
            """
            INSERT INTO qwendex_agent_sessions
            (agent_id, lane, task_id, owner, write_surface, stop_condition,
             artifacts_json, status, heartbeat_at, created_at, updated_at,
             stop_reason, close_receipt, context_packet_json, routing_json,
             validation_status, repo_root, session_id, turn_id, assignment,
             policy_hash, origin, final_report_present, completed_at, runtime_generation)
            VALUES (?, ?, ?, ?, 'read-only', ?, '[]', 'reserved', ?, ?, ?, '', '', ?, ?,
                    'pending', ?, ?, ?, ?, ?, 'qwendex', 0, '', ?)
            """,
            (
                pending_agent_id,
                lane,
                task_id,
                profile,
                str(assignment.get("stop_condition") or "return a concise worker outcome"),
                now,
                now,
                now,
                json_dumps(context_packet),
                json_dumps(dict(assignment.get("routing") or {})),
                repo_root,
                str(decision.get("session_id") or ""),
                str(decision.get("turn_id") or ""),
                str(assignment.get("assignment") or ""),
                str(decision.get("policy_hash") or agent_policy.get("policy_hash") or ""),
                str(decision.get("runtime_generation") or os.environ.get("QWENDEX_RUNTIME_GENERATION_ID") or ""),
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM qwendex_agent_sessions WHERE agent_id = ?",
            (pending_agent_id,),
        ).fetchone()
        reservation = row_to_agent_session(row) or {}
    return {
        "event": "manager.native_spawn_reserved",
        "reservation": reservation,
        "idempotent_reuse": False,
    }


def activate_manager_native_worker(
    config: Mapping[str, Any],
    event: Mapping[str, Any],
    agent_policy: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, str]:
    """Bind a native SubagentStart runtime id to its pre-spawn reservation."""
    runtime_agent_id = str(event.get("agent_id") or "").strip()
    if not runtime_agent_id:
        return None, "native_agent_id_missing"
    task_name = str(event.get("task_name") or event.get("agent_path") or "").strip()
    parent_session_id = str(event.get("parent_session_id") or "").strip()
    if not task_name or not parent_session_id:
        return None, "native_spawn_identity_missing"
    repo_root = canonical_manager_repo_root(event=event)
    launch_ledger_id = str(os.environ.get("QWENDEX_MANAGER_LEDGER_ID") or "").strip()
    policy_hash = str(os.environ.get("QWENDEX_MANAGER_POLICY_HASH") or agent_policy.get("policy_hash") or "").strip()
    now = utc_now()
    with connect_state(config) as conn:
        if busy_error := begin_immediate(conn):
            return None, busy_error
        existing_row = conn.execute(
            "SELECT * FROM qwendex_agent_sessions WHERE agent_id = ?",
            (runtime_agent_id,),
        ).fetchone()
        existing = row_to_agent_session(existing_row)
        if existing is not None:
            conn.commit()
            packet = existing.get("context_packet")
            packet = packet if isinstance(packet, Mapping) else {}
            if (
                str(existing.get("origin") or "") == "qwendex"
                and str(packet.get("runtime") or "") == "native_v2"
                and str(packet.get("runtime_state") or "") == "active"
            ):
                return existing, ""
            return None, "native_agent_id_collision"
        rows = conn.execute(
            """
            SELECT * FROM qwendex_agent_sessions
            WHERE repo_root = ? AND status = 'reserved' AND origin = 'qwendex'
              AND policy_hash = ?
            ORDER BY created_at ASC
            """,
            (repo_root, policy_hash),
        ).fetchall()
        candidates: list[dict[str, Any]] = []
        for row in rows:
            session = row_to_agent_session(row) or {}
            packet = session.get("context_packet")
            packet = packet if isinstance(packet, Mapping) else {}
            planned_agent_id = str(packet.get("planned_agent_id") or "")
            task_matches = task_name == planned_agent_id or task_name.endswith(f"/{planned_agent_id}")
            if (
                str(packet.get("launch_ledger_id") or "") == launch_ledger_id
                and str(packet.get("root_session_id") or "") == parent_session_id
                and task_matches
            ):
                candidates.append(session)
        if len(candidates) > 1:
            conn.rollback()
            return None, "native_spawn_reservation_ambiguous"
        pending = candidates[0] if candidates else None
        if pending is None:
            # Codex V2 collaboration calls do not consistently traverse the
            # generic PreToolUse hook path. SubagentStart still provides the
            # canonical task name, parent root session, child runtime id, and
            # repository. Bind directly to one exact planned assignment when
            # all immutable launch identities agree; never guess by lane order.
            decision_row = conn.execute(
                """
                SELECT * FROM qwendex_manager_decisions
                WHERE repo_root = ? AND root_session_id = ? AND policy_hash = ?
                  AND launch_ledger_id = ? AND selected_route = 'manager_subagents'
                  AND final_status IN ('preflight_ready', 'validation_pending')
                ORDER BY timestamp_updated DESC LIMIT 1
                """,
                (repo_root, parent_session_id, policy_hash, launch_ledger_id),
            ).fetchone()
            decision = row_to_manager_decision(decision_row) or {}
            plan = decision.get("agent_plan")
            plan = plan if isinstance(plan, Mapping) else {}
            assignments = [
                dict(item)
                for item in list(plan.get("assignments") or [])
                if isinstance(item, Mapping)
            ]
            matched = [
                assignment
                for assignment in assignments
                if task_name == str(assignment.get("agent_id") or "").strip()
                or task_name.endswith(f"/{str(assignment.get('agent_id') or '').strip()}")
            ]
            if len(matched) != 1:
                conn.rollback()
                return None, "native_spawn_reservation_missing"
            assignment = matched[0]
            task_id = str(decision.get("agent_task_id") or decision.get("session_id") or "")
            existing_rows = conn.execute(
                """
                SELECT * FROM qwendex_agent_sessions
                WHERE repo_root = ? AND task_id = ?
                ORDER BY created_at ASC
                """,
                (repo_root, task_id),
            ).fetchall()
            existing_sessions = [
                item for row in existing_rows
                if (item := row_to_agent_session(row))
            ]
            assignment_key = planned_assignment_key(assignment)
            registered = registered_assignment_keys(existing_sessions)
            if assignment_key in registered or (assignment_key[0], "") in registered:
                conn.rollback()
                return None, "native_spawn_assignment_duplicate"
            active_count = sum(
                1 for session in existing_sessions
                if str(session.get("status") or "") not in AGENT_TERMINAL_STATUSES
            )
            policy_snapshot = decision.get("policy_snapshot")
            policy_snapshot = policy_snapshot if isinstance(policy_snapshot, Mapping) else agent_policy
            max_workers = int(
                plan.get("max_workers")
                or policy_snapshot.get("max_workers")
                or policy_snapshot.get("max_threads")
                or 0
            )
            if max_workers <= 0 or active_count >= max_workers:
                conn.rollback()
                return None, "native_spawn_capacity_reached"
            lane = str(assignment.get("lane") or "")
            profile = str(assignment.get("profile") or "worker")
            packet = {
                "objective": str(assignment.get("assignment") or assignment.get("stop_condition") or ""),
                "task_class": str(plan.get("task_class") or decision.get("task_class") or ""),
                "allowed_scope": "read-only",
                "required": bool(assignment.get("required")),
                "exact_files": [],
                "needed_docs": [],
                "stop_condition": str(assignment.get("stop_condition") or "return a concise worker outcome"),
                "expected_artifact": "worker outcome",
                "receipt_path": "",
                "context_budget": int(agent_policy.get("max_inherited_context_bytes") or 0),
                "model_reasoning_assignment": dict(assignment.get("routing") or {}),
                "review_requirement": "root review suggested",
                "risk": str((plan.get("estimate") or {}).get("risk") or "medium"),
                "planned_agent_id": str(assignment.get("agent_id") or ""),
                "planned_lane": lane,
                "planned_profile": profile,
                "pending_tool_use_id": "",
                "launch_ledger_id": launch_ledger_id,
                "root_session_id": parent_session_id,
                "runtime": "native_v2",
                "runtime_state": "active",
                "registration_source": "SubagentStart",
                "native_session_id": str(event.get("session_id") or ""),
                "native_turn_id": str(event.get("turn_id") or ""),
                "native_agent_type": str(event.get("agent_type") or ""),
                "native_task_name": task_name,
                "parent_session_id": parent_session_id,
            }
            conn.execute(
                """
                INSERT INTO qwendex_agent_sessions
                (agent_id, lane, task_id, owner, write_surface, stop_condition,
                 artifacts_json, status, heartbeat_at, created_at, updated_at,
                 stop_reason, close_receipt, context_packet_json, routing_json,
                 validation_status, repo_root, session_id, turn_id, assignment,
                 policy_hash, origin, final_report_present, completed_at, runtime_generation)
                VALUES (?, ?, ?, ?, 'read-only', ?, '[]', 'active', ?, ?, ?, '', '', ?, ?,
                        'pending', ?, ?, ?, ?, ?, 'qwendex', 0, '', ?)
                """,
                (
                    runtime_agent_id,
                    lane,
                    task_id,
                    profile,
                    str(assignment.get("stop_condition") or "return a concise worker outcome"),
                    now,
                    now,
                    now,
                    json_dumps(packet),
                    json_dumps(dict(assignment.get("routing") or {})),
                    repo_root,
                    str(decision.get("session_id") or ""),
                    str(decision.get("turn_id") or ""),
                    str(assignment.get("assignment") or ""),
                    policy_hash,
                    str(decision.get("runtime_generation") or os.environ.get("QWENDEX_RUNTIME_GENERATION_ID") or ""),
                ),
            )
            conn.execute(
                """
                UPDATE qwendex_manager_decisions
                SET subagents_used = 1, timestamp_updated = ?
                WHERE ledger_id = ?
                """,
                (now, str(decision.get("ledger_id") or "")),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM qwendex_agent_sessions WHERE agent_id = ?",
                (runtime_agent_id,),
            ).fetchone()
            return row_to_agent_session(row), ""
        packet = dict(pending.get("context_packet") or {})
        packet.update({
            "runtime_state": "active",
            "native_session_id": str(event.get("session_id") or ""),
            "native_turn_id": str(event.get("turn_id") or ""),
            "native_agent_type": str(event.get("agent_type") or ""),
            "native_task_name": task_name,
            "parent_session_id": parent_session_id,
        })
        try:
            conn.execute(
                """
                UPDATE qwendex_agent_sessions
                SET agent_id = ?, status = 'active', context_packet_json = ?,
                    heartbeat_at = ?, updated_at = ?
                WHERE agent_id = ? AND origin = 'qwendex' AND status = 'reserved'
                """,
                (
                    runtime_agent_id,
                    json_dumps(packet),
                    now,
                    now,
                    str(pending.get("agent_id") or ""),
                ),
            )
        except sqlite3.IntegrityError:
            conn.rollback()
            return None, "native_agent_id_collision"
        conn.execute(
            """
            UPDATE qwendex_manager_decisions
            SET subagents_used = 1, timestamp_updated = ?
            WHERE repo_root = ? AND agent_task_id = ?
              AND final_status IN ('preflight_ready', 'validation_pending')
            """,
            (now, repo_root, str(pending.get("task_id") or "")),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM qwendex_agent_sessions WHERE agent_id = ?",
            (runtime_agent_id,),
        ).fetchone()
        return row_to_agent_session(row), ""


def session_is_verifier(session: Mapping[str, Any]) -> bool:
    lane_text = " ".join([
        str(session.get("lane") or ""),
        str(session.get("context_packet", {}).get("task_class") or ""),
        str(session.get("owner") or ""),
    ]).lower()
    return "verif" in lane_text


def verifier_passed(sessions: list[dict[str, Any]]) -> bool:
    for session in sessions:
        if session_is_verifier(session):
            if (
                str(session.get("status") or "") in AGENT_TERMINAL_STATUSES
                and str(session.get("validation_status") or "") == "pass"
                and bool(session.get("artifacts") or session.get("context_packet", {}).get("receipt_path"))
            ):
                return True
    return False


def final_mentions_agent_outcomes(message: str) -> bool:
    text = message or ""
    return all(
        re.search(pattern, text) is not None
        for pattern in (
            r"(?i)\b(agent outcomes?|agent ledger|subagents?)\b",
            r"(?i)\b(validation|verified|tests?)\b",
            r"(?i)\b(risks?|remaining risk|unresolved)\b",
        )
    )


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


def event_uses_read_only_profile(event: Mapping[str, Any], profile: str) -> bool:
    if profile in READ_ONLY_AGENT_PROFILES:
        return True
    for source in (event, event.get("tool_input"), event.get("profile_config")):
        if not isinstance(source, Mapping):
            continue
        for key in ("sandbox_mode", "write_surface"):
            value = str(source.get(key) or "").strip().lower()
            if value in {"read-only", "readonly"}:
                return True
    return False


def event_agent_id(event: Mapping[str, Any]) -> str:
    for key in ("agent_id", "owner_agent_id", "session_agent_id"):
        if isinstance(event.get(key), str) and event.get(key):
            return str(event[key]).strip()
    return ""


def event_is_codex_root(event: Mapping[str, Any]) -> bool:
    """Identify the root-only shape emitted by Codex lifecycle hooks.

    Codex deliberately omits top-level agent_id for the root session and emits
    it for subagents. Tool input is agent-controlled, so it is not an identity
    authority; session_id plus cwd distinguish the real root hook envelope from
    incomplete synthetic events.
    """
    return bool(
        not event_agent_id(event)
        and not str(event.get("agent_type") or "").strip()
        and str(event.get("session_id") or "").strip()
        and str(event.get("cwd") or "").strip()
    )


def event_is_codex_subagent(event: Mapping[str, Any]) -> bool:
    return bool(
        event_agent_id(event)
        and str(event.get("agent_type") or "").strip()
        and str(event.get("session_id") or "").strip()
        and str(event.get("cwd") or "").strip()
    )


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


def command_name(token: str) -> str:
    return Path(token).name


def command_token_at(tokens: list[str], index: int) -> str:
    return command_name(tokens[index]) if 0 <= index < len(tokens) else ""


def token_is_python_command(token: str) -> bool:
    name = command_name(token)
    return name == "python" or name == "python3" or bool(re.match(r"^python3?\.\d+$", name))


def token_is_sed_in_place_option(token: str) -> bool:
    return token == "--in-place" or token.startswith("--in-place=") or (token.startswith("-i") and token != "-")


def token_is_perl_in_place_option(token: str) -> bool:
    if token == "--in-place" or token.startswith("--in-place="):
        return True
    return bool(re.match(r"^-[A-Za-z]*i", token))


def segment_has_write_command(tokens: list[str]) -> bool:
    if not tokens:
        return False
    command_indexes = [0] + [index + 1 for index, token in enumerate(tokens[:-1]) if token == "|"]
    for index in command_indexes:
        command = command_token_at(tokens, index)
        args = tokens[index + 1:]
        if command in SHELL_MUTATING_COMMANDS:
            return True
        if command == "git" and command_token_at(tokens, index + 1) == "apply":
            return True
        if command == "sed" and any(token_is_sed_in_place_option(token) for token in args):
            return True
        if command == "perl" and any(token_is_perl_in_place_option(token) for token in args):
            return True
        python_code = " ".join(args)
        if token_is_python_command(command) and (
            any(marker in python_code for marker in (".write_text(", ".write_bytes(", "os.O_WRONLY", "os.O_RDWR"))
            or re.search(r"\bopen\s*\([^)]*,\s*(?:mode\s*=\s*)?['\"][wax+]", python_code)
            or re.search(r"\.open\s*\([^)]*(?:mode\s*=\s*)?['\"][wax+]", python_code)
        ):
            return True
    return False


def normalized_event_tool_name(tool: str) -> str:
    name = re.split(r"[/:]", tool.strip().lower())[-1].replace("-", "_")
    dotted_name = name.rsplit(".", 1)[-1]
    known_names = READ_ONLY_EXECUTION_TOOL_NAMES | WRITE_TOOL_NAMES | ROOT_ONLY_AGENT_TOOLS
    return dotted_name if dotted_name in known_names else name


def event_tool_components(tool: str) -> list[str]:
    camel_split = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", tool.strip())
    return [part for part in re.split(r"[^a-z0-9]+", camel_split.lower()) if part]


def event_tool_leaf_name(tool: str) -> str:
    parts = [part for part in re.split(r"__|[/:.]", tool.strip().lower()) if part]
    return parts[-1].replace("-", "_") if parts else ""


def event_tool_is_collaboration_lifecycle(tool: str) -> bool:
    raw = tool.strip().lower().replace("-", "_")
    leaf = event_tool_leaf_name(tool)
    if leaf not in COLLABORATION_LIFECYCLE_TOOL_NAMES:
        return False
    if raw == leaf:
        return True
    return bool(re.search(r"(?:^|__|[/:.])collaboration(?:__|[/:.])", raw))


def event_tool_is_non_filesystem_control(tool: str) -> bool:
    raw = tool.strip().lower().replace("-", "_")
    trusted_names = set(NON_FILESYSTEM_CONTROL_TOOL_NAMES)
    trusted_names.update(f"functions.{name}" for name in NON_FILESYSTEM_CONTROL_TOOL_NAMES)
    trusted_names.update(f"functions__{name}" for name in NON_FILESYSTEM_CONTROL_TOOL_NAMES)
    return raw in trusted_names


def event_tool_is_mutating(tool: str) -> bool:
    if event_tool_is_non_filesystem_control(tool):
        return False
    if event_tool_is_collaboration_lifecycle(tool):
        return False
    if normalized_event_tool_name(tool) in WRITE_TOOL_NAMES:
        return True
    return bool(set(event_tool_components(tool)) & MUTATING_TOOL_ACTIONS)


def read_only_non_shell_tool_allowed(tool: str) -> bool:
    leaf = event_tool_leaf_name(tool)
    if leaf in READ_ONLY_NON_SHELL_TOOL_NAMES or event_tool_is_collaboration_lifecycle(tool):
        return True
    components = set(event_tool_components(tool))
    if components & MUTATING_TOOL_ACTIONS:
        return False
    return bool(components & READ_ONLY_INSPECTION_ACTIONS)


def event_uses_managed_shell(tool_lower: str, command: str) -> bool:
    name = normalized_event_tool_name(tool_lower)
    execution_name = bool(
        re.search(
            r"(?:^|_)(?:bash|command|exec|execute|fish|ipython|node|perl|php|powershell|pwsh|python3?(?:\.\d+)?|ruby|run|sh|shell|terminal|zsh)(?:$|_)",
            name,
        )
    )
    return bool(command.strip()) or name in READ_ONLY_EXECUTION_TOOL_NAMES or execution_name


def read_only_shell_segments(command: str) -> list[list[str]] | None:
    """Parse the deliberately small shell grammar accepted for read-only agents.

    Lists and pipelines are supported, but expansion and control-flow syntax are
    rejected before shlex removes quoting information. This parser classifies a
    managed hook event; it is not intended to be a general shell parser.
    """

    if not command.strip() or "\x00" in command:
        return None
    raw_segments: list[str] = []
    current: list[str] = []
    quote = ""
    index = 0
    needs_command = False

    def flush_segment(*, required: bool) -> bool:
        nonlocal current
        raw = "".join(current).strip()
        if not raw:
            return not required
        raw_segments.append(raw)
        current = []
        return True

    while index < len(command):
        char = command[index]
        if quote == "'":
            current.append(char)
            if char == "'":
                quote = ""
            index += 1
            continue
        if quote == '"':
            if char in {"$", "`"}:
                return None
            current.append(char)
            if char == '"':
                quote = ""
                index += 1
                continue
            if char == "\\":
                if index + 1 >= len(command) or command[index + 1] == "\n":
                    return None
                current.append(command[index + 1])
                index += 2
                continue
            index += 1
            continue

        if char in {"'", '"'}:
            quote = char
            current.append(char)
            index += 1
            continue
        if char == "\\":
            if index + 1 >= len(command) or command[index + 1] == "\n":
                return None
            current.extend((char, command[index + 1]))
            index += 2
            continue
        if char in {"$", "`", "#", "<", ">", "(", ")", "{", "}", "!", "*", "?", "["}:
            return None
        if char == "\n":
            if needs_command and not "".join(current).strip():
                index += 1
                continue
            if not flush_segment(required=needs_command):
                return None
            needs_command = False
            index += 1
            continue
        if char == ";":
            if not flush_segment(required=True):
                return None
            needs_command = False
            index += 1
            continue
        if char == "&":
            if index + 1 >= len(command) or command[index + 1] != "&":
                return None
            if not flush_segment(required=True):
                return None
            needs_command = True
            index += 2
            continue
        if char == "|":
            operator_length = 2 if index + 1 < len(command) and command[index + 1] == "|" else 1
            if not flush_segment(required=True):
                return None
            needs_command = True
            index += operator_length
            continue
        current.append(char)
        index += 1

    if quote or not flush_segment(required=needs_command) or not raw_segments:
        return None
    segments: list[list[str]] = []
    for raw in raw_segments:
        try:
            tokens = shlex.split(raw, posix=True, comments=False)
        except ValueError:
            return None
        if not tokens:
            return None
        segments.append(tokens)
    return segments


def read_only_git_command_allowed(tokens: list[str]) -> bool:
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if token == "--no-pager":
            index += 1
            continue
        if token == "-C":
            if index + 1 >= len(tokens):
                return False
            index += 2
            continue
        if token.startswith("-C") and token != "-C":
            index += 1
            continue
        break
    if index >= len(tokens) or tokens[index] not in READ_ONLY_GIT_SUBCOMMANDS:
        return False
    for token in tokens[index + 1:]:
        if token in READ_ONLY_GIT_UNSAFE_OPTIONS:
            return False
        if any(token.startswith(f"{option}=") for option in READ_ONLY_GIT_UNSAFE_OPTIONS):
            return False
    return True


def read_only_rg_command_allowed(tokens: list[str]) -> bool:
    for token in tokens[1:]:
        if token in {"--hostname-bin", "--pre", "--pre-glob"}:
            return False
        if token.startswith(("--hostname-bin=", "--pre=", "--pre-glob=")):
            return False
    return True


def read_only_find_command_allowed(tokens: list[str]) -> bool:
    return not any(
        token.startswith(prefix)
        for token in tokens[1:]
        for prefix in READ_ONLY_FIND_UNSAFE_PREFIXES
    )


def read_only_file_command_allowed(tokens: list[str]) -> bool:
    return not any(
        token == "--compile"
        or token.startswith("--compile=")
        or bool(re.match(r"^-[^-]*C", token))
        for token in tokens[1:]
    )


def read_only_sed_command_allowed(tokens: list[str]) -> bool:
    """Allow only the common non-mutating `sed -n N[,N]p file...` form."""
    if len(tokens) < 3 or tokens[1] not in {"-n", "--quiet", "--silent"}:
        return False
    return bool(re.fullmatch(r"\d+(?:,\d+)?p", tokens[2]))


def read_only_verification_segment_allowed(tokens: list[str]) -> bool:
    validation_tokens = list(tokens)
    if validation_tokens[0] == "PYTHONDONTWRITEBYTECODE=1":
        validation_tokens = validation_tokens[1:]
    if not validation_tokens:
        return False
    command = validation_tokens[0]
    if "/" in command or shell_assignment_name(command):
        return False
    if command in {"pytest", "py.test"}:
        return not any(
            token == "--cache-clear"
            or token.startswith(("--basetemp=", "--rootdir="))
            for token in validation_tokens[1:]
        )
    if token_is_python_command(command):
        python_args = validation_tokens[1:]
        if python_args[:1] == ["-B"]:
            python_args = python_args[1:]
        return len(python_args) >= 2 and python_args[:2] == ["-m", "pytest"] and not any(
            token == "--cache-clear"
            or token.startswith(("--basetemp=", "--rootdir="))
            for token in python_args[2:]
        )
    return False


def read_only_segment_allowed(tokens: list[str], *, allow_validation: bool = False) -> bool:
    command = tokens[0]
    if allow_validation and read_only_verification_segment_allowed(tokens):
        return True
    if "/" in command or shell_assignment_name(command):
        return False
    if token_is_python_command(command):
        return len(tokens) == 2 and tokens[1] in {"-V", "-VV", "--version"}
    if command == "true":
        return len(tokens) == 1
    if command == "sed":
        return read_only_sed_command_allowed(tokens)
    if command == "file":
        return read_only_file_command_allowed(tokens)
    if command in READ_ONLY_SIMPLE_COMMANDS:
        return True
    if command == "rg":
        return read_only_rg_command_allowed(tokens)
    if command == "find":
        return read_only_find_command_allowed(tokens)
    if command == "git":
        return read_only_git_command_allowed(tokens)
    return False


def read_only_shell_command_allowed(command: str, *, allow_validation: bool = False) -> bool:
    segments = read_only_shell_segments(command)
    return bool(segments) and all(
        read_only_segment_allowed(tokens, allow_validation=allow_validation)
        for tokens in segments
    )


def event_is_write_attempt(tool_lower: str, command: str) -> bool:
    if event_tool_is_mutating(tool_lower):
        return True
    tokens = command_tokens(command)
    return bool(
        command_has_shell_redirection(command)
        or segment_has_write_command(tokens)
        or any(segment_has_write_command(segment) for segment in command_segments(command))
    )


def _performance_tool_tokens(event: Mapping[str, Any]) -> list[str]:
    command = event_command_text(event)
    for segment in command_segments(command):
        tokens = strip_command_prefixes(segment)
        if tokens:
            return tokens
    return []


def _performance_event_is_write(event: Mapping[str, Any]) -> bool:
    """Classify telemetry without weakening the stricter hook safety gate.

    A shell-capable tool is conservatively write-capable for Manager safety,
    even when its command is a read-only search. Telemetry instead classifies
    its concrete command so `rg`, `git status`, and validation commands remain
    measurable in their real families. Unknown shell syntax stays `other`.
    """
    tool = event_tool_name(event)
    command = event_command_text(event)
    if normalized_event_tool_name(tool) in WRITE_TOOL_NAMES:
        return True
    if event_uses_managed_shell(tool, command):
        tokens = command_tokens(command)
        return bool(
            command_has_shell_redirection(command)
            or segment_has_write_command(tokens)
            or any(segment_has_write_command(segment) for segment in command_segments(command))
        )
    return event_tool_is_mutating(tool)


def _performance_tool_family(event: Mapping[str, Any]) -> str:
    tool = event_tool_name(event)
    if _performance_event_is_write(event):
        return "edit"
    tokens = _performance_tool_tokens(event)
    executable = command_name(tokens[0]).lower() if tokens else ""
    if executable in {"rg", "grep", "ag", "ack", "find", "fd", "fdfind", "locate", "git-grep"}:
        return "search"
    if executable == "git" and len(tokens) > 1 and tokens[1] in {"grep", "ls-files"}:
        return "search"
    if executable in {"cat", "bat", "sed", "head", "tail", "less", "more", "awk"}:
        return "read"
    if executable in {"pytest", "ruff", "mypy", "pyright", "shellcheck", "flake8", "cargo", "npm", "pnpm", "yarn"}:
        return "validation"
    if executable == "python3" and any("py_compile" in token for token in tokens[1:]):
        return "validation"
    if executable == "bash" and "-n" in tokens[1:]:
        return "validation"
    if executable in {"qwendex", "qwendex_cli.py"} and any(
        token in {"check", "doctor", "eval", "verify"} for token in tokens[1:]
    ):
        return "validation"
    if event_tool_is_collaboration_lifecycle(tool):
        return "collaboration"
    normalized = normalized_event_tool_name(tool)
    if normalized in {"read", "open", "read_mcp_resource", "screenshot"}:
        return "read"
    if normalized in {"search", "find"}:
        return "search"
    return "other"


def _performance_query_details(event: Mapping[str, Any], tool_family: str) -> tuple[str, str]:
    if tool_family != "search":
        return "not_applicable", ""
    tokens = _performance_tool_tokens(event)
    executable = command_name(tokens[0]).lower() if tokens else ""
    lowered = {token.lower() for token in tokens[1:]}
    if executable in {"find", "fd", "fdfind", "locate"} or "--files" in lowered or "--files-with-matches" in lowered:
        query_class = "path_lookup"
    elif executable in {"rg", "grep", "ag", "ack", "git-grep"} or executable == "git":
        query_class = "literal" if {"-f", "--fixed-strings", "--fixed-string"} & lowered else "regex"
    else:
        query_class = "unknown"
    tool_input = event.get("tool_input")
    if isinstance(tool_input, Mapping):
        for key in ("query", "pattern", "search_query", "needle", "term"):
            value = tool_input.get(key)
            if isinstance(value, str) and value:
                return query_class, value
    option_values = {
        "-a", "-b", "-c", "-e", "-g", "-m", "-t", "-tadd", "--after-context",
        "--before-context", "--context", "--glob", "--max-count", "--regexp", "--type",
        "--type-add", "--pre", "--pre-glob",
    }
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            return query_class, tokens[index + 1] if index + 1 < len(tokens) else ""
        if token in {"-e", "--regexp"}:
            return query_class, tokens[index + 1] if index + 1 < len(tokens) else ""
        if token in option_values:
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        return query_class, token
    return query_class, ""


def _performance_wait_timeout_bucket(event: Mapping[str, Any]) -> str:
    """Reduce a collaboration wait timeout to a fixed, non-identifying bucket.

    Hook input can contain arbitrary content, so this function inspects only
    the numeric timeout field transiently. The database receives neither the
    raw input object nor the numeric value.
    """

    tool = normalized_event_tool_name(event_tool_name(event))
    if tool not in {"wait", "wait_agent"}:
        return "not_applicable"
    tool_input = event.get("tool_input")
    if not isinstance(tool_input, Mapping) or "timeout_ms" not in tool_input:
        return "not_provided"
    raw = tool_input.get("timeout_ms")
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        return "invalid"
    timeout_ms = float(raw)
    if not math.isfinite(timeout_ms) or timeout_ms <= 0:
        return "invalid"
    if timeout_ms <= 30_000:
        return "at_most_30s"
    if timeout_ms <= 60_000:
        return "31_to_60s"
    if timeout_ms <= 120_000:
        return "61_to_120s"
    return "over_120s"


def _performance_input_size_bucket(event: Mapping[str, Any], query_material: str) -> str:
    size = len(event_command_text(event).encode("utf-8", "replace")) + len(query_material.encode("utf-8", "replace"))
    if size <= 0:
        return "none"
    if size <= 32:
        return "1-32"
    if size <= 128:
        return "33-128"
    if size <= 512:
        return "129-512"
    return "513+"


def _performance_output_value(event: Mapping[str, Any]) -> Any:
    for key in ("updatedMCPToolOutput", "tool_output", "output", "result", "response"):
        if key in event:
            return event.get(key)
    tool_result = event.get("tool_result")
    return tool_result if tool_result is not None else None


def _performance_value_bytes(value: Any, *, depth: int = 0) -> int:
    if depth > 8 or value is None:
        return 0
    if isinstance(value, bytes):
        return len(value)
    if isinstance(value, str):
        return len(value.encode("utf-8", "replace"))
    if isinstance(value, Mapping):
        return sum(
            len(str(key).encode("utf-8", "replace")) + _performance_value_bytes(item, depth=depth + 1)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return sum(_performance_value_bytes(item, depth=depth + 1) for item in value)
    return len(str(value).encode("utf-8", "replace"))


def _performance_result_count(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        for key in ("results", "items", "matches"):
            nested = value.get(key)
            if isinstance(nested, (list, tuple)):
                return len(nested)
        return None
    if isinstance(value, (list, tuple)):
        return len(value)
    if isinstance(value, str):
        return len(value.splitlines()) if value else 0
    return None


def _performance_success(event: Mapping[str, Any]) -> bool | None:
    for key in ("success", "ok"):
        value = event.get(key)
        if isinstance(value, bool):
            return value
    status = str(event.get("status") or "").strip().lower()
    if status in {"success", "completed", "pass", "passed", "ok"}:
        return True
    if status in {"error", "failed", "fail", "blocked", "cancelled", "canceled"}:
        return False
    return None


def _performance_truncated(event: Mapping[str, Any]) -> bool | None:
    for key in ("truncated", "is_truncated", "output_truncated"):
        value = event.get(key)
        if isinstance(value, bool):
            return value
    return None


def _performance_scope_class(event: Mapping[str, Any]) -> str:
    cwd = str(event.get("cwd") or "").strip()
    if not cwd:
        return "unspecified"
    root = Path(canonical_manager_repo_root(event=event))
    candidate = Path(cwd).expanduser().resolve(strict=False)
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        return "outside_repo"
    return "repository_root" if not relative.parts else "known_subtree"


def _performance_agent_role(event: Mapping[str, Any]) -> str:
    if event_is_codex_root(event):
        return "root"
    if event_is_codex_subagent(event) or event_agent_id(event):
        return "worker"
    return "unknown"


def _performance_material(event: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = event.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _performance_run_material(event: Mapping[str, Any]) -> str:
    return str(
        os.environ.get("QWENDEX_RUN_ID")
        or _performance_material(event, "session_id", "run_id", "manager_session_id")
        or os.environ.get("QWENDEX_MANAGER_SESSION_ID")
        or make_id("performance-run")
    )


def _performance_event_key_material(event: Mapping[str, Any], canonical: str) -> str:
    material = _performance_material(event, "tool_use_id", "tool_call_id", "event_id", "id")
    return material or f"{canonical}:{make_id('performance-event')}"


_PERFORMANCE_MODULE: Any | None = None
_OPTIMIZATION_LAB_MODULE: Any | None = None
_QWENDEX_SEARCH_MODULE: Any | None = None


def performance_module() -> Any:
    global _PERFORMANCE_MODULE
    if _PERFORMANCE_MODULE is None:
        _PERFORMANCE_MODULE = script_module("qwendex_performance")
    return _PERFORMANCE_MODULE


def optimization_lab_module() -> Any:
    global _OPTIMIZATION_LAB_MODULE
    if _OPTIMIZATION_LAB_MODULE is None:
        _OPTIMIZATION_LAB_MODULE = script_module("qwendex_optimization_lab")
    return _OPTIMIZATION_LAB_MODULE


def qwendex_search_module() -> Any:
    global _QWENDEX_SEARCH_MODULE
    if _QWENDEX_SEARCH_MODULE is None:
        _QWENDEX_SEARCH_MODULE = script_module("qwendex_search")
    return _QWENDEX_SEARCH_MODULE


def experimental_search_candidate_context() -> str:
    module = qwendex_search_module()
    candidate_id = module.selected_candidate_from_environment()
    if not candidate_id:
        return ""
    for candidate in module.candidate_registry().get("candidates", []):
        if isinstance(candidate, Mapping) and str(candidate.get("candidate_id") or "") == candidate_id:
            return str(module.managed_instruction_for_candidate(candidate_id))
    return ""


def capture_performance_hook_event(
    config: Mapping[str, Any],
    *,
    event_name: str,
    event: Mapping[str, Any],
) -> dict[str, Any]:
    """Record only normalized hook metadata; capture failures never affect hooks."""
    settings = performance_config(config)
    if settings["capture"] != "metadata":
        return {"enabled": False, "capture": settings["capture"]}
    canonical = event_name or str(event.get("hookEventName") or event.get("event") or "")
    lifecycle = {
        "SessionStart": ("startup", "startup", "startup_observation", "startup"),
        "UserPromptSubmit": ("lifecycle", "session", "prompt_submit", "other"),
        "SubagentStart": ("lifecycle", "subagent", "subagent_start", "collaboration"),
        "SubagentStop": ("lifecycle", "subagent", "subagent_stop", "collaboration"),
        "Stop": ("stop", "stop", "run_stop", "other"),
        "PreCompact": ("lifecycle", "compaction", "context_pressure", "context"),
        "PostCompact": ("lifecycle", "compaction", "context_pressure", "context"),
    }
    if canonical == "PreToolUse":
        action, phase, event_kind = "tool_start", "tool", "tool_call"
        tool_family = _performance_tool_family(event)
    elif canonical == "PostToolUse":
        action, phase, event_kind = "tool_finish", "tool", "tool_call"
        tool_family = _performance_tool_family(event)
    elif canonical in lifecycle:
        action, phase, event_kind, tool_family = lifecycle[canonical]
    else:
        return {"enabled": True, "capture": "metadata", "captured": False, "reason": "unsupported_hook_event"}
    query_class, query_material = _performance_query_details(event, tool_family)
    record: dict[str, Any] = {
        "action": action,
        "repository_scope_digest": performance_repository_scope_digest(event=event),
        "run_material": _performance_run_material(event),
        "manager_launch_material": (
            os.environ.get("QWENDEX_MANAGER_LEDGER_ID")
            or _performance_material(event, "manager_ledger_id", "ledger_id", "launch_ledger_id")
        ),
        "turn_material": (
            _performance_material(event, "turn_id", "agent_task_id", "session_id")
            or os.environ.get("QWENDEX_MANAGER_SESSION_ID")
        ),
        "event_key_material": _performance_event_key_material(event, canonical),
        "agent_role": _performance_agent_role(event),
        "phase": phase,
        "event_kind": event_kind,
        "tool_family": tool_family,
        "query_class": query_class,
        "scope_class": _performance_scope_class(event),
        "input_size_bucket": _performance_input_size_bucket(event, query_material),
        "wait_timeout_bucket": _performance_wait_timeout_bucket(event),
        "query_material": query_material,
        "query_fingerprints": settings["query_fingerprints"],
    }
    if canonical == "PostToolUse":
        output = _performance_output_value(event)
        record.update({
            "completed_at": utc_now(),
            "output_bytes": _performance_value_bytes(output),
            "result_count": _performance_result_count(output),
            "success": _performance_success(event),
            "truncated": _performance_truncated(event),
        })
    elif canonical == "SessionStart":
        duration = event.get("duration_ms")
        if isinstance(duration, int | float) and not isinstance(duration, bool):
            record["duration_ms"] = float(duration)
    try:
        result = performance_module().record_event(performance_db_path(config), record)
    except Exception:
        return {"enabled": True, "capture": "metadata", "captured": False, "reason": "instrumentation_unavailable"}
    return {
        "enabled": True,
        "capture": "metadata",
        "captured": bool(result.get("captured")),
        "matched_pre_event": bool(result.get("matched_pre_event")),
        "instrumentation_duration_ms": result.get("instrumentation_duration_ms"),
        "reason": result.get("reason", ""),
    }


def command_tokens(command: str) -> list[str]:
    try:
        return shlex.split(command, posix=True)
    except ValueError:
        return []


def shell_assignment_name(token: str) -> str:
    match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=", token)
    return match.group(1) if match else ""


def env_option_consumes_next(token: str) -> bool:
    return token in ENV_OPTIONS_WITH_VALUE


def env_option_has_inline_value(token: str) -> bool:
    if any(token.startswith(f"{option}=") for option in ENV_LONG_OPTIONS_WITH_VALUE):
        return True
    return any(token.startswith(option) and token != option for option in {"-u", "-C", "-a"})


def env_split_string_value(tokens: list[str]) -> str:
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            return ""
        if token in ENV_SPLIT_STRING_OPTIONS:
            return tokens[index + 1] if index + 1 < len(tokens) else ""
        if token.startswith("--split-string="):
            return token.split("=", 1)[1]
        if token.startswith("-S") and token != "-S":
            return token[2:].lstrip("=")
        if env_option_consumes_next(token):
            index += 2
            continue
        if env_option_has_inline_value(token) or token.startswith("-") or shell_assignment_name(token):
            index += 1
            continue
        return ""
    return ""


def env_split_string_tokens(tokens: list[str]) -> list[str]:
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            return []
        if token in ENV_SPLIT_STRING_OPTIONS:
            if index + 1 >= len(tokens):
                return []
            return [*command_tokens(tokens[index + 1]), *tokens[index + 2:]]
        if token.startswith("--split-string="):
            return [*command_tokens(token.split("=", 1)[1]), *tokens[index + 1:]]
        if token.startswith("-S") and token != "-S":
            return [*command_tokens(token[2:].lstrip("=")), *tokens[index + 1:]]
        if env_option_consumes_next(token):
            index += 2
            continue
        if env_option_has_inline_value(token) or token.startswith("-") or shell_assignment_name(token):
            index += 1
            continue
        return []
    return []


def env_prefix_end(tokens: list[str]) -> int:
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            return index + 1
        if token in ENV_SPLIT_STRING_OPTIONS:
            return len(tokens)
        if token.startswith("--split-string=") or (token.startswith("-S") and token != "-S"):
            return index + 1
        if env_option_consumes_next(token):
            index += 2
            continue
        if env_option_has_inline_value(token):
            index += 1
            continue
        if token.startswith("-"):
            index += 1
            continue
        if shell_assignment_name(token):
            index += 1
            continue
        return index
    return index


def command_wrapper_tokens(tokens: list[str]) -> list[str]:
    if not tokens or command_name(tokens[0]) != "command":
        return tokens
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            return tokens[index + 1:]
        if token.startswith("-"):
            options = token[1:]
            if options and set(options) <= {"p"}:
                index += 1
                continue
            # `command -v/-V` inspects names; it does not execute them.
            return []
        return tokens[index:]
    return []


def strip_command_prefixes(tokens: list[str]) -> list[str]:
    remaining = list(tokens)
    for _ in range(16):
        while remaining and shell_assignment_name(remaining[0]) and not remaining[0].startswith("-"):
            remaining = remaining[1:]
        if not remaining:
            return []
        executable = command_name(remaining[0])
        if executable == "command":
            unwrapped = command_wrapper_tokens(remaining)
            if not unwrapped or unwrapped == remaining:
                return unwrapped
            remaining = unwrapped
            continue
        if executable == "env":
            split_tokens = env_split_string_tokens(remaining)
            remaining = split_tokens if split_tokens else remaining[env_prefix_end(remaining):]
            continue
        break
    return remaining


def shell_parenthesized_end(command: str, opening_index: int) -> int:
    depth = 1
    quote = ""
    index = opening_index + 1
    while index < len(command):
        char = command[index]
        if quote:
            if quote != "'" and char == "\\" and index + 1 < len(command):
                index += 2
                continue
            if char == quote:
                quote = ""
            index += 1
            continue
        if char in {"'", '"', "`"}:
            quote = char
        elif char == "\\" and index + 1 < len(command):
            index += 2
            continue
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return 0


def shell_embedded_payloads(command: str) -> list[str]:
    """Extract executable command substitutions while ignoring single-quoted literals."""
    payloads: list[str] = []
    single_quoted = False
    double_quoted = False
    index = 0
    while index < len(command):
        char = command[index]
        if single_quoted:
            if char == "'":
                single_quoted = False
            index += 1
            continue
        if char == "\\" and index + 1 < len(command):
            index += 2
            continue
        if char == "'" and not double_quoted:
            single_quoted = True
            index += 1
            continue
        if char == '"':
            double_quoted = not double_quoted
            index += 1
            continue
        if char == "$" and index + 1 < len(command) and command[index + 1] == "(":
            end = shell_parenthesized_end(command, index + 1)
            if end:
                payloads.append(command[index + 2:end - 1])
                index = end
                continue
        if char == "`":
            end = index + 1
            while end < len(command):
                if command[end] == "\\" and end + 1 < len(command):
                    end += 2
                    continue
                if command[end] == "`":
                    payloads.append(command[index + 1:end])
                    index = end + 1
                    break
                end += 1
            else:
                index += 1
            continue
        index += 1
    return payloads


def shell_control_parts(command: str) -> list[tuple[str, str]]:
    """Return top-level shell segments paired with their preceding control operator."""
    parts: list[tuple[str, str]] = []
    current: list[str] = []
    quote = ""
    preceding_control = ""
    index = 0
    while index < len(command):
        char = command[index]
        if quote:
            current.append(char)
            if quote != "'" and char == "\\" and index + 1 < len(command):
                index += 1
                current.append(command[index])
            elif char == quote:
                quote = ""
            index += 1
            continue
        if char in {"'", '"', "`"}:
            quote = char
            current.append(char)
            index += 1
            continue
        if char == "$" and index + 1 < len(command) and command[index + 1] == "(":
            end = shell_parenthesized_end(command, index + 1)
            if end:
                current.append(command[index:end])
                index = end
                continue
        if char == "\\" and index + 1 < len(command):
            current.extend((char, command[index + 1]))
            index += 2
            continue
        if char == "#" and (index == 0 or command[index - 1].isspace() or command[index - 1] in ";&|"):
            while index < len(command) and command[index] != "\n":
                index += 1
            continue
        redirection_control = (
            (char == "&" and index + 1 < len(command) and command[index + 1] == ">")
            or (char in {"&", "|"} and index > 0 and command[index - 1] == ">")
        )
        if (char in ";&|\n") and not redirection_control:
            raw = "".join(current).strip()
            if raw:
                parts.append((raw, preceding_control))
            current = []
            control = [char]
            index += 1
            while index < len(command) and command[index] in ";&|":
                control.append(command[index])
                index += 1
            preceding_control = "".join(control)
            continue
        current.append(char)
        index += 1
    raw = "".join(current).strip()
    if raw:
        parts.append((raw, preceding_control))
    return parts


def shell_control_segments(command: str) -> list[str]:
    return [raw for raw, _ in shell_control_parts(command)]


def command_segments(command: str) -> list[list[str]]:
    segments: list[list[str]] = []
    for raw_segment in shell_control_segments(command):
        tokens = strip_command_prefixes(command_tokens(raw_segment))
        if tokens and command_name(tokens[0]) == "export":
            continue
        if tokens:
            segments.append(tokens)
    return segments


def shell_command_payload(tokens: list[str]) -> str | None:
    if not tokens or command_name(tokens[0]) not in SHELL_COMMAND_WRAPPERS:
        return None
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            return None
        if token in SHELL_OPTIONS_WITH_VALUE:
            index += 2
            continue
        if token == "-c" or (
            token.startswith("-")
            and not token.startswith("--")
            and "c" in token[1:]
        ):
            return tokens[index + 1] if index + 1 < len(tokens) else ""
        if token.startswith(("-", "+")):
            index += 1
            continue
        return None
    return None


def eval_command_payload(tokens: list[str]) -> str | None:
    if not tokens or command_name(tokens[0]) != "eval":
        return None
    return " ".join(tokens[1:])


def shell_reads_standard_input(tokens: list[str]) -> bool:
    if not tokens or command_name(tokens[0]) not in SHELL_COMMAND_WRAPPERS:
        return False
    if shell_command_payload(tokens) is not None:
        return False
    explicit_stdin = False
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            return explicit_stdin or index + 1 >= len(tokens)
        if token in SHELL_OPTIONS_WITH_VALUE:
            index += 2
            continue
        if token == "-s" or (
            token.startswith("-")
            and not token.startswith("--")
            and "s" in token[1:]
        ):
            explicit_stdin = True
            index += 1
            continue
        if token.startswith(("-", "+")):
            index += 1
            continue
        return explicit_stdin
    return True


def command_has_piped_shell(command: str) -> bool:
    for raw_segment, preceding_control in shell_control_parts(command):
        if preceding_control not in {"|", "|&"}:
            continue
        tokens = strip_command_prefixes(command_tokens(raw_segment))
        if shell_reads_standard_input(tokens):
            return True
    return False


def pre_tool_gate(config: Mapping[str, Any], event: Mapping[str, Any], agent_policy: Mapping[str, Any]) -> dict[str, Any]:
    tool = event_tool_name(event)
    tool_lower = tool.lower()
    tool_key = normalized_event_tool_name(tool_lower)
    depth = int(event.get("depth") or event.get("spawn_depth") or 0)
    profile = event_profile(event)
    read_only_profile = event_uses_read_only_profile(event, profile)
    agent_id = event_agent_id(event)
    codex_root = event_is_codex_root(event)
    codex_subagent = event_is_codex_subagent(event)
    root_agent_id = ""
    ownership_source = "hook_agent_id" if agent_id else ""
    event_repo_root = canonical_manager_repo_root(event=event)
    command = event_command_text(event)
    managed_shell_event = event_uses_managed_shell(tool_key, command)
    manager_mode_active = (
        bool(os.environ.get(MANAGER_ROOT_AGENT_ID_ENV))
        or str(agent_policy.get("mode") or "") == "manager"
        or selected_manager_mode_for_policy(config) == "manager"
    )
    native_envelope = bool(
        str(event.get("session_id") or "").strip()
        and str(event.get("cwd") or "").strip()
    )
    if (
        manager_mode_active
        and native_envelope
        and bool(agent_id) != bool(str(event.get("agent_type") or "").strip())
    ):
        return {
            "decision": "block",
            "event": "agent.identity_malformed",
            "reason": "Manager Mode native hooks must provide both agent_id and agent_type for a child, or neither for root.",
        }
    registered_agent_session: dict[str, Any] = {}
    if manager_mode_active and codex_subagent and agent_id:
        with connect_state(config) as conn:
            registered_agent_session = row_to_agent_session(
                conn.execute(
                    "SELECT * FROM qwendex_agent_sessions WHERE agent_id = ?",
                    (agent_id,),
                ).fetchone()
            ) or {}
        if manager_session_is_read_only(registered_agent_session):
            read_only_profile = True
    allow_read_only_validation = (
        read_only_profile
        and (
            profile == "verifier"
            or session_is_verifier(registered_agent_session)
        )
    )
    allowlisted_inspection = managed_shell_event and read_only_shell_command_allowed(
        command,
        allow_validation=allow_read_only_validation,
    )
    write_attempt = False if allowlisted_inspection else (
        event_is_write_attempt(tool, command) or managed_shell_event
    )
    if manager_mode_active and codex_root and tool_key == "spawn_agent":
        try:
            spawn_resolution = resolve_manager_decision(
                config,
                event,
                agent_policy,
                allow_turn_binding=True,
            )
        except Exception as exc:
            reason_code = f"bookkeeping_unavailable:{redact_text(str(exc) or exc.__class__.__name__)}"
            return {
                "event": "manager.subagent_plan_unavailable",
                "reason": "Qwendex spawn bookkeeping is unavailable; Codex may still spawn the worker.",
                "reason_code": reason_code,
            }
        if spawn_resolution.get("status") != "attached":
            reason_code = str(spawn_resolution.get("reason") or "decision_not_found")
            return {
                "event": "manager.subagent_plan_unavailable",
                "reason": f"Qwendex could not attach advisory spawn bookkeeping ({reason_code}); Codex may still spawn the worker.",
                "reason_code": reason_code,
                "manager_resolution": manager_resolution_diagnostic(spawn_resolution),
            }
        try:
            reservation = reserve_manager_native_spawn(
                config,
                event,
                agent_policy,
                dict(spawn_resolution.get("decision") or {}),
            )
        except Exception as exc:
            reason_code = f"bookkeeping_unavailable:{redact_text(str(exc) or exc.__class__.__name__)}"
            return {
                "event": "manager.subagent_plan_unavailable",
                "reason": "Qwendex spawn reservation is unavailable; Codex may still spawn the worker.",
                "reason_code": reason_code,
            }
        if reservation.get("decision") == "block":
            return {
                "event": "manager.subagent_plan_advisory",
                "reason": str(reservation.get("reason") or "The requested worker is outside the advisory Qwendex plan."),
                "reason_code": str(reservation.get("event") or "manager.subagent_plan_advisory"),
            }
        return reservation
    if codex_root:
        # Manager is a delegation aid, not a second authorization layer for
        # the root. Codex permissions and the live user instruction govern
        # root tools; Qwendex observes child lanes only.
        return {}
    if (codex_subagent or depth > 0) and tool_key in ROOT_ONLY_AGENT_TOOLS:
        return {
            "decision": "block",
            "event": "agent.spawn_rejected",
            "reason": f"Child agents cannot use root-only management tool {tool}.",
        }
    read_only_shell_rejected = (
        read_only_profile
        and managed_shell_event
        and not allowlisted_inspection
    )
    read_only_tool_rejected = (
        read_only_profile
        and not managed_shell_event
        and not read_only_non_shell_tool_allowed(tool)
    )
    if read_only_profile and (write_attempt or read_only_shell_rejected or read_only_tool_rejected):
        return {
            "decision": "block",
            "event": "agent.write_rejected",
            "reason": (
                f"Read-only profile {profile or 'read-only'} may run only managed allowlisted inspection commands; "
                "shell expansion, wrappers, interpreters, redirection, and mutating commands are blocked."
            ),
        }
    if not agent_id and depth == 0 and not read_only_profile:
        # Incomplete synthetic/root-shaped events cannot establish child
        # ownership. Treat them as direct root work instead of inventing a
        # Qwendex authorization requirement.
        return {}
    if write_attempt:
        raw_path_values = event_file_path_values(event)
        paths = event_file_paths(event, repo_root=event_repo_root)
        invalid_paths: list[str] = []
        for raw_path in raw_path_values:
            if isinstance(raw_path, Mapping):
                raw_path = raw_path.get("path") or raw_path.get("file") or raw_path.get("file_path") or ""
            raw_text = str(raw_path or "").strip()
            if raw_text and not normalize_lock_path(raw_text, repo_root=event_repo_root):
                invalid_paths.append(raw_text)
        if invalid_paths and codex_subagent and manager_mode_active:
            return {
                "decision": "block",
                "event": "agent.path_scope_mismatch",
                "reason": "Native write path escapes the active repository scope.",
                "denied_paths": invalid_paths,
            }
        if not agent_id:
            return {
                "decision": "block",
                "event": "agent.write_lock_rejected",
                "reason": "Write attempts must include agent_id so Qwendex can record file ownership.",
            }
        if not paths and not codex_subagent:
            return {
                "decision": "block",
                "event": "agent.write_lock_rejected",
                "reason": "Write attempts must include at least one target file path for Qwendex file-lock tracking.",
            }
        with connect_state(config) as conn:
            session_row = conn.execute(
                "SELECT * FROM qwendex_agent_sessions WHERE agent_id = ?",
                (agent_id,),
            ).fetchone()
            session = row_to_agent_session(session_row) or {}
            if codex_subagent and manager_mode_active and session_row is None:
                return {
                    "decision": "block",
                    "event": "agent.unregistered",
                    "reason": "Manager Mode subagent must be registered with its exact runtime agent_id before writing.",
                }
            elif codex_subagent and manager_mode_active and str(session.get("status") or "") != "active":
                return {
                    "decision": "block",
                    "event": "agent.inactive",
                    "reason": "Manager Mode subagent must have an active registered session before writing.",
                }
            elif codex_subagent and manager_mode_active and manager_session_is_read_only(session):
                return {
                    "decision": "block",
                    "event": "agent.write_rejected",
                    "reason": "Manager Mode subagent registration is read-only and cannot acquire write locks.",
                }
            elif session_row is not None and not session_row["repo_root"]:
                return {
                    "decision": "block",
                    "event": "agent.legacy_scope_unresolved",
                    "reason": "Legacy-unscoped agent must be claimed with manager assign before writing.",
                }
            elif session_row is not None and str(session_row["repo_root"]) != event_repo_root:
                return {
                    "decision": "block",
                    "event": "agent.repository_scope_mismatch",
                    "reason": "Write event repository does not match the registered agent scope.",
                }
            else:
                repo_root = (
                    str(session_row["repo_root"] or "")
                    if session_row is not None and session_row["repo_root"]
                    else event_repo_root
                )
            if codex_subagent and manager_mode_active:
                task_resolution = resolve_manager_decision(
                    config,
                    event,
                    agent_policy,
                    require_turn=False,
                    agent_task_id=str(session.get("task_id") or ""),
                )
                decision = task_resolution.get("decision")
                if task_resolution.get("status") != "attached" or not isinstance(decision, Mapping):
                    reason_code = str(task_resolution.get("reason") or "decision_not_found")
                    return {
                        "decision": "block",
                        "event": "agent.unattached",
                        "reason": f"Manager Mode subagent admission failed ({reason_code}).",
                        "reason_code": reason_code,
                        "manager_resolution": manager_resolution_diagnostic(task_resolution),
                    }
                if isinstance(decision, Mapping):
                    expected_task_id = str(
                        decision.get("agent_task_id")
                        or decision.get("session_id")
                        or ""
                    )
                    if expected_task_id and str(session.get("task_id") or "") != expected_task_id:
                        return {
                            "decision": "block",
                            "event": "agent.task_scope_mismatch",
                            "reason": "Manager Mode subagent write does not belong to the active manager task.",
                        }
                if not paths:
                    paths = registered_session_lock_paths(session)
                else:
                    denied_paths = [
                        path for path in paths
                        if not registered_session_path_allowed(session, path)
                    ]
                    if denied_paths:
                        return {
                            "decision": "block",
                            "event": "agent.path_scope_mismatch",
                            "reason": "Manager Mode subagent write exceeds its registered path scope.",
                            "denied_paths": denied_paths,
                        }
            if not paths:
                return {
                    "decision": "block",
                    "event": "agent.write_lock_rejected",
                    "reason": "Write attempts must resolve at least one registered target scope for Qwendex file-lock tracking.",
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
            lock_result = acquire_file_locks(
                conn,
                agent_id=agent_id,
                paths=paths,
                lock_type="write",
                now=utc_now(),
                reason=f"{tool or 'tool'} PreToolUse",
                repo_root=repo_root,
            )
            if lock_result.get("busy_error"):
                return {
                    "decision": "block",
                    "event": "agent.state_busy",
                    "reason": "Qwendex manager state remained busy after the bounded lock wait.",
                    **lock_result,
                }
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
            "root_agent_id": root_agent_id or None,
            "ownership_source": ownership_source,
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
    if canonical == "SessionStart":
        return "pass", {
            "hookSpecificOutput": {
                "hookEventName": canonical,
                "additionalContext": agent_mode_context(agent_policy, config=config),
            }
        }, {}
    if canonical == "UserPromptSubmit":
        selected_mode = selected_manager_mode_for_policy(config)
        manager_enforced = (
            str(agent_policy.get("mode") or "") == "manager"
            or selected_mode == "manager"
            or bool(os.environ.get("QWENDEX_MANAGER_LEDGER_ID") or os.environ.get("QWENDEX_MANAGER_SESSION_ID"))
        )
        manager_resolution: dict[str, Any] | None = None
        if manager_enforced and not (event.get("agent_id") or event.get("agent_type")):
            try:
                manager_resolution = resolve_manager_decision(
                    config,
                    event,
                    agent_policy,
                    allow_turn_binding=True,
                )
            except Exception as exc:
                reason_code = f"bookkeeping_unavailable:{redact_text(str(exc) or exc.__class__.__name__)}"
                return "pass", {
                    "hookSpecificOutput": {
                        "hookEventName": canonical,
                        "additionalContext": (
                            f"{agent_mode_context(agent_policy, config=config)} "
                            f"Qwendex Manager bookkeeping is unavailable ({reason_code}); continue normally."
                        ),
                    },
                }, {"manager_resolution": {"status": "unavailable", "reason": reason_code}}
            if manager_resolution["status"] != "attached":
                reason_code = str(manager_resolution.get("reason") or "decision_not_found")
                return "pass", {
                    "hookSpecificOutput": {
                        "hookEventName": canonical,
                        "additionalContext": (
                            f"{agent_mode_context(agent_policy, config=config)} "
                            f"Qwendex could not attach advisory turn bookkeeping ({reason_code}); continue normally."
                        ),
                    },
                    "event": "manager.prompt_bookkeeping_unavailable",
                    "reason": f"Manager turn bookkeeping was not attached ({reason_code}).",
                    "reason_code": reason_code,
                }, {"manager_resolution": manager_resolution_diagnostic(manager_resolution)}
            attached_decision = dict(manager_resolution.get("decision") or {})
            admission_error = prompt_admission_error_code(event)
            if not attached_decision.get("policy_snapshot"):
                admission_error = admission_error or "policy_snapshot_missing"
            if admission_error:
                try:
                    updated = record_manager_prompt_admission_failure(
                        config,
                        attached_decision,
                        error_code=admission_error,
                    )
                except Exception:
                    updated = None
                return "pass", {
                    "hookSpecificOutput": {
                        "hookEventName": canonical,
                        "additionalContext": (
                            f"{agent_mode_context(agent_policy, config=config)} "
                            f"Qwendex could not classify this prompt for advisory planning ({admission_error}); continue normally."
                        ),
                    },
                    "event": "manager.prompt_bookkeeping_unavailable",
                    "reason_code": admission_error,
                }, {
                    "manager_decision": updated or attached_decision,
                    "manager_resolution": manager_resolution_diagnostic(manager_resolution),
                }
        try:
            prompt_update = update_manager_decision_from_prompt(
                config,
                event,
                agent_policy,
                resolved_decision=(manager_resolution or {}).get("decision"),
            )
        except Exception:
            prompt_update = None
        if manager_resolution is not None and prompt_update is None:
            attached_decision = dict(manager_resolution.get("decision") or {})
            error_code = "session_lookup_failed"
            try:
                record_manager_prompt_admission_failure(
                    config,
                    attached_decision,
                    error_code=error_code,
                )
            except Exception:
                pass
        additional_context = agent_mode_context(agent_policy, config=config)
        if prompt_update is not None:
            decision = prompt_update["manager_decision"]
            plan = prompt_update["agent_plan"]
            assignments = list(plan.get("assignments") or [])
            if assignments:
                lane_summary = "; ".join(
                    f"{item.get('agent_id')}: {item.get('lane')} (suggested, read-only)"
                    for item in assignments
                )
                additional_context += (
                    f" Manager decision {decision.get('ledger_id')} selected manager_subagents for task "
                    f"{decision.get('agent_task_id') or decision.get('session_id')}. Planned lanes: {lane_summary}. "
                    "These lanes are suggestions; spawn the workers that materially help and let Qwendex attach lifecycle bookkeeping when possible."
                )
            else:
                additional_context += (
                    f" Manager decision {decision.get('ledger_id')} recorded a direct-work exception: "
                    f"{decision.get('routing_reason')}."
                )
        return "pass", {
            "hookSpecificOutput": {
                "hookEventName": canonical,
                "additionalContext": additional_context,
            }
        }, {
            **(prompt_update or {}),
            **(
                {"manager_resolution": manager_resolution_diagnostic(manager_resolution)}
                if manager_resolution is not None
                else {}
            ),
        }
    if canonical == "SubagentStart":
        registered: dict[str, Any] | None = None
        if os.environ.get("QWENDEX_MANAGER_LEDGER_ID"):
            try:
                registered, registration_error = activate_manager_native_worker(
                    config,
                    event,
                    agent_policy,
                )
            except Exception as exc:
                registration_error = f"bookkeeping_unavailable:{redact_text(str(exc) or exc.__class__.__name__)}"
            if registration_error:
                reason = f"Qwendex could not attach advisory worker bookkeeping ({registration_error})."
                return "pass", {
                    "hookSpecificOutput": {
                        "hookEventName": "SubagentStart",
                        "additionalContext": (
                            f"{subagent_start_context(config, event, agent_policy)} {reason} Continue the assigned task normally."
                        ),
                    },
                    "event": "manager.native_worker_bookkeeping_unavailable",
                    "reason_code": registration_error,
                }, {}
        return "pass", {
            "hookSpecificOutput": {
                "hookEventName": "SubagentStart",
                "additionalContext": subagent_start_context(config, event, agent_policy),
            }
        }, {"agent_session": registered} if registered is not None else {}
    if canonical == "SubagentStop":
        final_message = str(event.get("last_assistant_message") or event.get("message") or event.get("raw_output") or "")
        raw_message = str(event.get("raw_output") or event.get("transcript") or final_message)
        final_status = parse_worker_final_status(final_message)
        if not final_status["has_contract"]:
            final_status = {
                **final_status,
                "status": "completed",
                "validation_status": "pending",
                "reason": "unstructured_worker_outcome",
            }
        updated: dict[str, Any] | None = None
        capture: dict[str, Any] = {}
        advisories: list[str] = []
        agent_id = str(event.get("agent_id") or "")
        if agent_id:
            try:
                with connect_state(config) as conn:
                    row = conn.execute("SELECT * FROM qwendex_agent_sessions WHERE agent_id = ?", (agent_id,)).fetchone()
                    session = row_to_agent_session(row) or {}
                    if row is None:
                        advisories.append("worker was not registered in the Qwendex ledger")
                    else:
                        session_repo = str(session.get("repo_root") or "")
                        event_repo = canonical_manager_repo_root(event=event)
                        if not session_repo:
                            advisories.append("worker has legacy unscoped lifecycle state")
                        elif session_repo != event_repo:
                            advisories.append("worker stop repository differs from its recorded scope")
                    if (
                        final_status.get("status") == "completed"
                        and session_is_verifier(session)
                        and not stop_event_has_validation_evidence(event, final_message, config=config)
                    ):
                        final_status = {
                            **final_status,
                            "validation_status": "pending",
                            "reason": "verifier_evidence_not_recorded",
                        }
                    now = utc_now()
                    if row is not None:
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
                            advisories.append(f"worker output capture failed: {redact_text(str(exc))}")
                        updated = update_agent_from_final_contract(
                            conn,
                            agent_id=agent_id,
                            final_status=final_status,
                            now=now,
                            artifacts=list(capture.get("artifacts", [])),
                        )
            except Exception as exc:
                advisories.append(f"worker lifecycle bookkeeping unavailable: {redact_text(str(exc) or exc.__class__.__name__)}")
        return "pass", {
            "event": f"agent.{final_status['status']}",
            "status": final_status["status"],
            "agent_id": agent_id,
            "artifacts": list(capture.get("artifacts", [])),
            "advisories": advisories,
        }, {"final_status": final_status, "agent_session": updated, "advisories": advisories, **capture}
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
        try:
            manager_resolution = resolve_manager_decision(
                config,
                event,
                agent_policy,
                allow_turn_binding=False,
            )
        except Exception as exc:
            reason_code = f"bookkeeping_unavailable:{redact_text(str(exc) or exc.__class__.__name__)}"
            return "pass", {
                "continue": True,
                "event": "manager.untrusted_stop_allowed",
                "reason_code": reason_code,
            }, {"launch_health": {"trusted": False, "reason": reason_code}}
        resolved_decision = manager_resolution.get("decision")
        if manager_resolution.get("status") != "attached" or not isinstance(resolved_decision, Mapping):
            reason_code = str(manager_resolution.get("reason") or "decision_not_found")
            return "pass", {
                "continue": True,
                "event": "manager.untrusted_stop_allowed",
                "reason_code": reason_code,
            }, {
                "manager_resolution": manager_resolution_diagnostic(manager_resolution),
                "launch_health": {"trusted": False, "reason": reason_code},
            }
        decision = dict(resolved_decision)
        advisories: list[str] = []
        sessions: list[dict[str, Any]] = []
        released_root_locks: list[dict[str, Any]] = []
        last_message = str(event.get("last_assistant_message") or "")
        edit_happened = bool(event.get("edit_happened") or event.get("files_changed"))
        try:
            with connect_state(config) as conn:
                decision_session_id = str(
                    decision.get("agent_task_id")
                    or decision.get("session_id")
                    or ""
                )
                decision_repo_root = str(decision.get("repo_root") or "")
                rows = conn.execute(
                    """
                    SELECT * FROM qwendex_agent_sessions
                    WHERE task_id = ? AND repo_root = ?
                    ORDER BY updated_at DESC
                    """,
                    (decision_session_id, decision_repo_root),
                ).fetchall()
                sessions = [session for row in rows if (session := row_to_agent_session(row))]
                released_root_locks = release_manager_root_locks(
                    conn,
                    decision,
                    now=utc_now(),
                )
                conn.commit()

            selected_route = str(decision.get("selected_route") or "")
            if selected_route == "blocked" or decision.get("stop_status") == "STOP_MANAGER_BLOCKED_UNHOOKED":
                advisories.append("legacy Manager preflight was not ready")
            if edit_happened and not stop_event_has_validation_evidence(event, last_message, config=config):
                advisories.append("post-edit validation evidence was not recorded")
            if edit_happened and not stop_event_has_dirty_classification(event, last_message):
                advisories.append("dirty worktree classification was not recorded")
            if edit_happened and not final_mentions_agent_outcomes(last_message):
                advisories.append("agent outcomes or remaining risks were not summarized")

            missing_lanes = missing_planned_required_lanes(decision, sessions)
            if missing_lanes:
                names = ", ".join(str(item.get("lane") or "unknown") for item in missing_lanes)
                advisories.append(f"suggested lanes were not started: {names}")
            incomplete = [
                session for session in sessions
                if str(session.get("status") or "") not in AGENT_TERMINAL_STATUSES
            ]
            if incomplete:
                names = ", ".join(str(item.get("agent_id") or "unknown") for item in incomplete[:5])
                advisories.append(f"workers were still active at root stop: {names}")
            failed = [
                session for session in sessions
                if str(session.get("status") or "") in {"blocked", "failed", "tombstoned"}
                or str(session.get("validation_status") or "") == "fail"
            ]
            if failed:
                names = ", ".join(str(item.get("agent_id") or "unknown") for item in failed[:5])
                advisories.append(f"workers reported blocked, failed, or failed validation: {names}")
            pending_validation = [
                session for session in sessions
                if str(session.get("validation_status") or "pending") != "pass"
            ]
            if pending_validation:
                names = ", ".join(str(item.get("agent_id") or "unknown") for item in pending_validation[:5])
                advisories.append(f"worker validation evidence was not recorded: {names}")
            if selected_route == "manager_subagents" and not sessions:
                advisories.append("the advisory subagent plan produced no recorded worker sessions")

            with connect_state(config) as conn:
                updated_decision = update_manager_decision_terminal(
                    conn,
                    decision,
                    config=config,
                    final_status="closed",
                    validation_result=(
                        "pass"
                        if stop_event_has_validation_evidence(event, last_message, config=config)
                        or not edit_happened
                        else "not_recorded"
                    ),
                    stop_status="STOP_MANAGER_CLOSED",
                    unresolved_risks=advisories,
                    subagents_used=bool(sessions),
                )
        except Exception as exc:
            advisories.append(f"Manager stop bookkeeping was incomplete: {redact_text(str(exc) or exc.__class__.__name__)}")
            updated_decision = decision
        return "pass", {
            "continue": True,
            "event": "manager.finalized_with_advisories" if advisories else "manager.finalized",
            "stop_status": "STOP_MANAGER_CLOSED",
            "ledger_id": decision.get("ledger_id"),
            "advisories": advisories,
        }, {
            "manager_decision": updated_decision or decision,
            "agent_sessions": sessions,
            "released_root_locks": released_root_locks,
            "advisories": advisories,
        }
    if canonical == "PreToolUse":
        result = pre_tool_gate(config, event, agent_policy)
        return ("blocked" if result.get("decision") == "block" else "pass"), result, {}
    if canonical == "PostToolUse":
        released_root_locks: list[dict[str, Any]] = []
        cleanup_warning = ""
        if event_is_codex_root(event) and os.environ.get(MANAGER_ROOT_AGENT_ID_ENV):
            try:
                root_agent_id, _decision, root_error = manager_root_cleanup_identity_for_event(
                    config,
                    event,
                    agent_policy,
                )
                if root_error:
                    cleanup_warning = root_error
                else:
                    tool_agent_id = manager_root_tool_agent_id(
                        root_agent_id,
                        str(event.get("tool_use_id") or ""),
                    )
                    with connect_state(config) as conn:
                        released_root_locks = release_agent_locks(
                            conn,
                            tool_agent_id,
                            now=utc_now(),
                        )
                        conn.commit()
            except Exception as exc:
                cleanup_warning = (
                    "root tool cleanup bookkeeping unavailable: "
                    f"{redact_text(str(exc) or exc.__class__.__name__)}"
                )
        return "pass", {
            "event": "agent.PostToolUse",
            "status": "recorded",
        }, {
            "released_root_locks": released_root_locks,
            "cleanup_warning": cleanup_warning,
        }
    if canonical in {"PreCompact", "PostCompact"}:
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
    performance_db = str(source.get(PERFORMANCE_DB_ENV) or dev_paths.get("performance_db") or "").strip()
    if performance_db:
        values[PERFORMANCE_DB_ENV] = performance_db
    results_root = str(source.get("QWENDEX_RESULTS_ROOT") or dev_paths.get("results_root") or "").strip()
    if results_root:
        values["QWENDEX_RESULTS_ROOT"] = results_root
    ledger_db = str(source.get("QWENDEX_LEDGER_DB") or dev_paths.get("ledger_db") or "").strip()
    if ledger_db:
        values["QWENDEX_LEDGER_DB"] = ledger_db
    # The Qdex process owns this value. Do not bake a status-file path into a
    # generated hook command: a static `env` assignment would make concurrent
    # TUIs read one last-writer-wins compatibility file instead of the private
    # status file inherited from their own launch.
    dev_root = str(source.get("QWENDEX_DEV_ROOT") or "").strip()
    if not dev_root and work_root is not None and work_root.name == ".qwendex-dev":
        dev_root = str(work_root.parent)
    if dev_root:
        values["QWENDEX_DEV_ROOT"] = dev_root
    runtime_tree = str(source.get("QWENDEX_RUNTIME_TREE") or "").strip()
    values["QWENDEX_ROOT"] = str(source.get("QWENDEX_ROOT") or runtime_tree or ROOT)
    for key in (
        "QWENDEX_RUNTIME_ROOT",
        "QWENDEX_RUNTIME_TREE",
        "QWENDEX_RUNTIME_GENERATION_DIR",
        "QWENDEX_RUNTIME_GENERATION_ID",
        "QWENDEX_RUNTIME_CONTRACT_SHA256",
        "QWENDEX_HOOK_GENERATION",
    ):
        value = str(source.get(key) or "").strip()
        if value:
            values[key] = value
    return {key: values[key] for key in MANAGED_HOOK_RUNTIME_ENV_KEYS if values.get(key)}


def shell_env_prefix(runtime_env: Mapping[str, str]) -> str:
    if not runtime_env:
        return ""
    return "env " + " ".join(f"{key}={shlex.quote(str(value))}" for key, value in runtime_env.items())


def managed_agent_hook_command_base(
    command_base: str,
    runtime_env: Mapping[str, str] | None,
) -> str:
    explicit = str(command_base or "").strip()
    if explicit:
        return explicit
    runtime_tree = str((runtime_env or {}).get("QWENDEX_RUNTIME_TREE") or "").strip()
    if runtime_tree:
        return str(Path(runtime_tree).expanduser() / "scripts" / "qwendex")
    dev_root = str((runtime_env or {}).get("QWENDEX_DEV_ROOT") or "").strip()
    if dev_root:
        dev_command = Path(dev_root).expanduser() / "scripts" / "qwendex"
        if dev_command.is_file():
            return str(dev_command)
    return str(ROOT / "scripts" / "qwendex")


def managed_agent_hook_config(command_base: str = "", runtime_env: Mapping[str, str] | None = None) -> dict[str, Any]:
    base = managed_agent_hook_command_base(command_base, runtime_env)
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


def install_managed_hook_config(path: Path, payload: Mapping[str, Any], *, force: bool) -> Path:
    target = path.expanduser()
    if force or not target.exists():
        return write_managed_hook_config(target, payload, force=force)
    try:
        loaded = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"existing hook config is unreadable; repair it or retry with --force: {target}: {exc}"
        ) from exc
    if not isinstance(loaded, dict) or not isinstance(loaded.get("hooks", {}), Mapping):
        raise ValueError(f"existing hook config has no mergeable hooks object: {target}")
    merged = dict(loaded)
    merged_hooks = dict(loaded.get("hooks") or {})
    generated_hooks = payload.get("hooks") if isinstance(payload.get("hooks"), Mapping) else {}
    for event_name, generated_entries in generated_hooks.items():
        retained_entries: list[Any] = []
        existing_entries = merged_hooks.get(event_name)
        if isinstance(existing_entries, list):
            for entry in existing_entries:
                if not isinstance(entry, Mapping):
                    retained_entries.append(entry)
                    continue
                hooks = entry.get("hooks")
                if not isinstance(hooks, list):
                    retained_entries.append(dict(entry))
                    continue
                retained_hooks = [
                    hook
                    for hook in hooks
                    if not (
                        isinstance(hook, Mapping)
                        and is_qwendex_agent_hook_command(str(hook.get("command") or ""))
                    )
                ]
                if retained_hooks:
                    retained_entries.append({**dict(entry), "hooks": retained_hooks})
        retained_entries.extend(list(generated_entries) if isinstance(generated_entries, list) else [])
        merged_hooks[str(event_name)] = retained_entries
    merged["hooks"] = merged_hooks
    atomic_write_text(target, json_dumps(merged) + "\n")
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


def managed_hook_command_base(command: str) -> str:
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return ""
    if not tokens:
        return ""
    index = 0
    if tokens[0] == "env":
        index = 1
        while index < len(tokens):
            token = tokens[index]
            if "=" not in token or token.startswith("-"):
                break
            key, _value = token.split("=", 1)
            if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
                break
            index += 1
    return tokens[index] if index < len(tokens) else ""


def managed_hook_uses_command_base(command: str, expected_base: str) -> bool:
    actual_base = managed_hook_command_base(command)
    if not actual_base or not expected_base:
        return False
    return Path(actual_base).expanduser().resolve(strict=False) == Path(expected_base).expanduser().resolve(strict=False)


def hook_status_for_codex_home(
    codex_home: Path,
    *,
    write_gating: bool = False,
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
    status_file_override_events = sorted(
        event
        for event in managed_events
        if any(
            "QWENDEX_CODEX_STATUS_FILE" in runtime_env
            for runtime_env in runtime_env_by_event.get(event, [])
        )
    )
    expected_runtime_env = managed_hook_runtime_env(codex_home=codex_home)
    expected_runtime_base = managed_agent_hook_command_base("", expected_runtime_env)
    expected_dev_root = str(expected_runtime_env.get("QWENDEX_DEV_ROOT") or "").strip()
    runtime_command_mismatch_events = sorted(
        event
        for event in managed_events
        if expected_dev_root
        and not any(
            managed_hook_uses_command_base(command, expected_runtime_base)
            for command in commands.get(event, [])
            if is_codex_compatible_agent_hook_command(command)
        )
    )
    configured = target.is_file() and hook_source_count > 0
    verified = (
        configured
        and compatible_hook_source_count > 0
        and not missing_events
        and not incompatible_events
        and not missing_runtime_env_events
        and not status_file_override_events
        and not runtime_command_mismatch_events
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
        "status_file_override_events": status_file_override_events,
        "runtime_command_mismatch_events": runtime_command_mismatch_events,
        "runtime_env_keys_by_event": runtime_env_keys_by_event,
        "runtime_env_state_db_by_event": runtime_env_state_db_by_event,
        "write_gating": write_gating,
        "advisory_for_lifecycle": True,
        "override": override,
        "override_reason": override_reason or None,
        "parse_error": parse_error,
        "install_command": f'scripts/qwendex agent hook-config --install --codex-home "{codex_home.expanduser()}" --json',
        "verify_command": f'scripts/qwendex agent hook-config --verify --codex-home "{codex_home.expanduser()}" --json',
    }


def prompt_requests_team(prompt: str) -> bool:
    return bool(re.search(r"(?i)\b(team|squad|manager mode|subagents?|use agents?|spawn agents?|fan out)\b", prompt or ""))


def prompt_is_trivial(prompt: str, task_class: str) -> bool:
    words = re.findall(r"\w+", prompt or "")
    risky = task_class in {"security", "release acceptance", "architecture"}
    work_verbs = re.search(r"(?i)\b(add|change|edit|implement|refactor|test|verify|release|publish|ship|write)\b", prompt or "")
    return len(words) <= 12 and not risky and not work_verbs


def classify_manager_turn(prompt: str) -> str:
    """Classify a turn from observable prompt features only.

    This classifier intentionally does not call a model. Its output is the
    stable routing authority used by Auto and the lane planner.
    """
    text = " ".join((prompt or "").strip().split())
    lower = text.lower()
    words = re.findall(r"\w+", lower)
    edit_text = re.sub(
        r"\b(?:do\s+not|don't|no|without)\s+(?:make\s+)?(?:any\s+)?"
        r"(?:edits?|editing|changes?|changing|modifications?|modifying)\b",
        "",
        lower,
    )
    edit_intent = bool(re.search(
        r"\b(add|build|change|create|edit|fix|implement|migrate|patch|refactor|remove|repair|replace|update|write)\b",
        edit_text,
    ))
    cross_cutting = bool(re.search(
        r"\b(across|cross[- ](?:cutting|file)|end[- ]to[- ]end|many|multiple|repo[- ]wide|several|subsystems?)\b",
        lower,
    ))
    explicit_single_file_read = bool(re.search(
        r"^(?:please\s+)?(?:read|inspect|check|show|summarize)\s+"
        r"(?:(?:the\s+)?(?:single|one)\s+file\s+)?"
        r"\S+\.(?:py|rs|js|ts|md|toml|json)\b",
        lower,
    ))
    release_tag_intent = bool(re.search(
        r"\b(?:git|release|version)\s+tags?\b|\btags?\s+(?:a\s+)?(?:release|version)\b",
        lower,
    ))
    if explicit_single_file_read:
        return "single_file_read"
    if re.search(r"\b(release|publish|ship|distribution)\b", lower) or release_tag_intent:
        return "release_or_publish"
    if re.search(r"\b(security|credential|protocol|privacy|authentication|authorization|threat)\b", lower):
        return "security_or_protocol"
    if re.search(r"\b(live acceptance|live test|end[- ]to[- ]end acceptance|production acceptance)\b", lower):
        return "live_acceptance"
    if edit_intent and cross_cutting:
        return "cross_cutting_edit"
    if edit_intent and re.search(r"\b(implementation|non[- ]trivial|refactor|migrate|repair|feature|runtime)\b", lower):
        return "nontrivial_edit"
    if edit_intent:
        return "small_edit"
    if re.search(r"\b(regression|test|tests|pytest|unittest|verify|validation)\b", lower):
        return "test_or_regression"
    if re.search(r"\b(map|mapping|trace|inventory|overview|where|locate)\b", lower) and re.search(
        r"\b(repo|repository|codebase|files?|implementation|feature|flow)\b", lower
    ):
        return "repository_mapping"
    if re.search(r"\b(audit|analy[sz]e|investigate|research|review|inspect|understand|diagnose)\b", lower):
        return "read_heavy_investigation"
    if re.search(r"\b(single|one)\s+file\b|\bread\s+\S+\.(?:py|rs|js|ts|md|toml|json)\b", lower):
        return "single_file_read"
    if len(words) <= 12:
        return "trivial_direct"
    return "read_heavy_investigation"


def effective_manager_turn_mode(selected_mode: str, task_class: str) -> str:
    selected = normalize_manager_mode(selected_mode) or "medium"
    if selected != "auto":
        return selected
    if task_class in {"trivial_direct", "single_file_read", "small_edit"}:
        return "lite"
    if task_class in {"repository_mapping", "read_heavy_investigation", "test_or_regression"}:
        return "medium"
    if task_class == "nontrivial_edit":
        return "heavy"
    return "manager"


def manager_turn_lane_specs(
    prompt: str,
    *,
    selected_mode: str,
    effective_mode: str,
    task_class: str,
) -> tuple[list[dict[str, Any]], str]:
    explicit_team = prompt_requests_team(prompt)
    if effective_mode == "off":
        return [], "agent_use_off"
    if task_class == "trivial_direct" and not explicit_team:
        return [], "trivial_direct"
    if effective_mode == "lite":
        if explicit_team or task_class in {"repository_mapping", "read_heavy_investigation"}:
            return [{"profile": "explorer", "lane": "exploration", "required": False}], "lite_read_only_lookup"
        return [], f"{task_class}_direct_under_lite"
    if effective_mode == "medium":
        if task_class in {"repository_mapping", "read_heavy_investigation", "single_file_read"}:
            return [{"profile": "explorer", "lane": "exploration", "required": False}], "medium_read_mapping"
        if task_class == "test_or_regression":
            return [{"profile": "verifier", "lane": "verification", "required": False}], "medium_independent_check"
        if task_class in {"nontrivial_edit", "cross_cutting_edit", "security_or_protocol", "release_or_publish", "live_acceptance"}:
            return [
                {"profile": "explorer", "lane": "exploration", "required": False},
                {"profile": "verifier", "lane": "verification", "required": False},
            ], "medium_bounded_edit_support"
        return [], f"{task_class}_direct_under_medium"

    if task_class in {"repository_mapping", "read_heavy_investigation", "single_file_read"}:
        return [{"profile": "explorer", "lane": "exploration", "required": False}], "read_only_exploration"
    if task_class in {"small_edit", "test_or_regression"}:
        return [{"profile": "verifier", "lane": "verification", "required": False}], "post_edit_verification"
    if task_class in {"security_or_protocol", "release_or_publish", "live_acceptance"}:
        specs = [
            {"profile": "reviewer", "lane": "review", "required": False},
            {"profile": "verifier", "lane": "verification", "required": False},
        ]
        if effective_mode == "manager":
            specs.insert(0, {"profile": "explorer", "lane": "exploration", "required": False})
        return specs, "risk_review_and_verification"
    specs = [
        {"profile": "explorer", "lane": "exploration", "required": False},
        {"profile": "verifier", "lane": "verification", "required": False},
    ]
    if task_class == "cross_cutting_edit":
        specs.append({"profile": "reviewer", "lane": "review", "required": False})
    return specs, "orchestrated_single_writer"


def agent_plan_profiles(prompt: str, mode: str, estimate: Mapping[str, Any]) -> tuple[list[str], str]:
    del estimate
    task_class = classify_manager_turn(prompt)
    effective_mode = effective_manager_turn_mode(mode, task_class)
    specs, reason = manager_turn_lane_specs(
        prompt,
        selected_mode=mode,
        effective_mode=effective_mode,
        task_class=task_class,
    )
    return [str(spec["profile"]) for spec in specs], reason


def agent_profile_lane(profile: str) -> str:
    return {
        "explorer": "exploration",
        "reviewer": "review",
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
    repo_root: str = "",
) -> dict[str, Any]:
    estimate = estimate_task(config, prompt=prompt, local_status=local_status)
    selected_mode = str(agent_policy.get("mode") or "medium")
    task_class = classify_manager_turn(prompt)
    effective_mode = effective_manager_turn_mode(selected_mode, task_class)
    lane_specs, reason = manager_turn_lane_specs(
        prompt,
        selected_mode=selected_mode,
        effective_mode=effective_mode,
        task_class=task_class,
    )
    max_workers = int(agent_policy.get("max_workers") or agent_policy.get("max_threads") or 0)
    required_specs = [spec for spec in lane_specs if spec.get("required")]
    optional_specs = [spec for spec in lane_specs if not spec.get("required")]
    lane_specs = (required_specs + optional_specs)[:max_workers]
    profiles = [str(spec["profile"]) for spec in lane_specs]
    effective_task_id = task_id or make_id("task")
    effective_repo_root = canonical_manager_repo_root(repo_root or None)
    assignments: list[dict[str, Any]] = []
    task_slug = safe_artifact_component(effective_task_id, "task")
    for index, spec in enumerate(lane_specs, start=1):
        profile = str(spec["profile"])
        lane = str(spec.get("lane") or agent_profile_lane(profile))
        required = bool(spec.get("required"))
        agent_id = safe_native_agent_name(f"{task_slug}_{profile}_{index}", f"{profile}_{index}")
        stop_condition = (
            "return a concise outcome with evidence, changed paths, blockers, and remaining risk when available"
            if profile != "scribe"
            else "record run decisions and artifact paths under .qwendex/runs"
        )
        command = [
            "qwendex",
            "manager",
            "assign",
            "--agent-id",
            agent_id,
            "--lane",
            lane,
            "--task-id",
            effective_task_id,
            "--repo-root",
            effective_repo_root,
            "--owner",
            profile,
            "--write-surface",
            agent_profile_write_surface(profile),
            "--stop-condition",
            stop_condition,
        ]
        command.append("--required" if required else "--optional")
        command.append("--json")
        routing = lane_model_reasoning(
            config,
            task_class=task_class,
            lane=lane,
            risk=str(estimate.get("risk") or "medium"),
            local_status=local_status,
        )
        assignments.append({
            "agent_id": agent_id,
            "profile": profile,
            "lane": lane,
            "required": required,
            "write_surface": agent_profile_write_surface(profile),
            "stop_condition": stop_condition,
            "assignment": f"Perform the bounded {lane} lane for {task_class}; remain read-only and return a concise outcome. A structured report is optional.",
            "assign_command": " ".join(shlex.quote(part) for part in command),
            "spawn_instruction": spawn_instruction(agent_id, routing),
            "routing": routing,
        })
    direct_work = not assignments
    return {
        "schema_version": "qwendex.agent_plan.v1",
        "mode": selected_mode,
        "selected_mode": AGENT_USE_LABELS.get(selected_mode, selected_mode),
        "effective_turn_mode": AGENT_USE_LABELS.get(effective_mode, effective_mode),
        "agent_use": agent_policy.get("agent_use"),
        "output_policy": agent_policy.get("output_policy", {}),
        "task_id": effective_task_id,
        "repo_root": effective_repo_root,
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "task_class": task_class,
        "estimate": estimate,
        "routing_reason": reason,
        "route": "direct" if direct_work else "orchestrated_single_writer",
        "native_proactive_source": (
            str(agent_policy.get("native_proactive_source") or "qwendex_custom_policy")
            if not direct_work
            else "none"
        ),
        "direct_allowed": direct_work,
        "direct_reason": reason if direct_work else None,
        "required_lanes": [
            {"lane": item["lane"], "profile": item["profile"], "write": False}
            for item in assignments if item["required"]
        ],
        "optional_lanes": [
            {"lane": item["lane"], "profile": item["profile"], "write": False}
            for item in assignments if not item["required"]
        ],
        "max_workers": max_workers,
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
         mode, agent_use, policy_source, policy_hash, codex_home_digest_or_path_policy, codex_home, repo_root,
         hook_source_count, hook_configured, hook_verified, hook_override, hook_override_reason,
         local_enabled, local_usable, cloud_usable, prompt_known, prompt_digest, prompt_summary,
         estimate_id, selected_route, routing_reason, subagents_allowed, subagents_used,
         direct_work_exception, verifier_required, validation_plan, branch, git_status_digest,
         final_status, validation_result, stop_status, receipt_paths_json, unresolved_risks_json)
        VALUES (?, ?, 'manager_decision', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
          repo_root=excluded.repo_root,
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
            str(decision.get("repo_root") or ""),
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
    conn.execute(
        """
        UPDATE qwendex_manager_decisions
        SET launch_ledger_id = ?, turn_id = ?, agent_task_id = ?,
            launch_pid = ?, launch_start_ticks = ?, launch_nonce = ?, launch_key = ?,
            root_session_id = ?, state_db_identity = ?,
            ledger_db_identity = ?, runtime_identity = ?, runtime_generation = ?,
            hook_generation = ?, runtime_contract_sha256 = ?, patched_binary_sha256 = ?,
            codex_patch_sha256 = ?, config_sha256 = ?, qdex_permission_mode = ?,
            qdex_permission_source = ?, runtime_state_schema_version = ?, selected_mode = ?,
            effective_turn_mode = ?, task_class = ?, agent_plan_json = ?,
            policy_snapshot_json = ?, desired_global_policy_hash = ?,
            prompt_source = ?, prompt_length = ?, prompt_schema_version = ?,
            admission_error_code = ?
        WHERE ledger_id = ?
        """,
        (
            str(decision.get("launch_ledger_id") or ledger_id),
            str(decision.get("turn_id") or ""),
            str(decision.get("agent_task_id") or decision.get("session_id") or ""),
            int(decision.get("launch_pid") or 0),
            str(decision.get("launch_start_ticks") or ""),
            str(decision.get("launch_nonce") or ""),
            str(decision.get("launch_key") or ""),
            str(decision.get("root_session_id") or ""),
            str(decision.get("state_db_identity") or ""),
            str(decision.get("ledger_db_identity") or ""),
            str(decision.get("runtime_identity") or ""),
            str(decision.get("runtime_generation") or ""),
            str(decision.get("hook_generation") or ""),
            str(decision.get("runtime_contract_sha256") or ""),
            str(decision.get("patched_binary_sha256") or ""),
            str(decision.get("codex_patch_sha256") or ""),
            str(decision.get("config_sha256") or ""),
            str(decision.get("qdex_permission_mode") or "workspace-write"),
            str(decision.get("qdex_permission_source") or "default"),
            int(decision.get("runtime_state_schema_version") or 0),
            str(decision.get("selected_manager_mode") or decision.get("selected_mode") or decision.get("mode") or ""),
            str(decision.get("effective_turn_mode") or decision.get("effective_agent_mode") or ""),
            str(decision.get("task_class") or ""),
            json_dumps(dict(decision.get("agent_plan") or {})),
            json_dumps(dict(decision.get("policy_snapshot") or {})),
            str(decision.get("desired_global_policy_hash") or decision.get("policy_hash") or ""),
            str(decision.get("prompt_source") or ""),
            int(decision.get("prompt_length") or 0),
            str(decision.get("prompt_schema_version") or ""),
            str(decision.get("admission_error_code") or ""),
            ledger_id,
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
    selected_mode: str = "",
) -> dict[str, Any]:
    source_env = os.environ if env is None else env
    qdex_permission = qdex_permission_posture(config, env=source_env)
    repo_root = canonical_manager_repo_root(repo, env=source_env)
    with connect_state(config) as conn:
        mode = current_manager_mode(config, conn, explicit=selected_mode)
        kaveman_enabled = current_kaveman_enabled(config, conn)
        agent_policy = resolve_agent_policy(config, selected_manager_mode=mode, env=source_env, kaveman_enabled=kaveman_enabled)
        local_enabled = current_local_enabled(config, conn)
        local_status = local_subagent_status(config, enabled=local_enabled, env=source_env, probe=True)
        agent_policy = attach_local_routing_snapshot(
            agent_policy,
            config,
            enabled=local_enabled,
        )
        agent_policy = attach_native_proactive_source(agent_policy, env=source_env)
    codex_home = codex_home_from_env(source_env)
    base_hook_status = hook_status_for_codex_home(
        codex_home,
        write_gating=False,
    )
    override = False
    hook_status = dict(base_hook_status)
    hook_status["override"] = override
    hook_status["override_reason"] = None
    timestamp = utc_now()
    try:
        launch_pid = max(0, int(source_env.get(MANAGER_LAUNCH_PID_ENV) or 0))
    except (TypeError, ValueError):
        launch_pid = 0
    launch_start_ticks = str(source_env.get(MANAGER_LAUNCH_START_TICKS_ENV) or "").strip()
    launch_nonce = str(source_env.get(MANAGER_LAUNCH_NONCE_ENV) or make_id("launch")).strip()
    codex_home_identity = path_digest_policy(codex_home)
    state_db_identity, ledger_db_identity = manager_store_identities(config)
    runtime_identity = manager_runtime_identity(source_env)
    runtime_generation = manager_runtime_generation_metadata(source_env)
    launch_key = (
        manager_launch_key(
            repo_root=repo_root,
            launch_pid=launch_pid,
            launch_start_ticks=launch_start_ticks,
            launch_nonce=launch_nonce,
            codex_home_identity=codex_home_identity,
            state_db_identity=state_db_identity,
            runtime_identity=runtime_identity,
        )
        if launch_pid and launch_start_ticks
        else ""
    )
    existing_launch: dict[str, Any] | None = None
    if launch_key and not dry_run:
        with connect_state(config) as conn:
            rows = conn.execute(
                """
                SELECT * FROM qwendex_manager_decisions
                WHERE launch_key = ? AND ledger_id = launch_ledger_id
                LIMIT 2
                """,
                (launch_key,),
            ).fetchall()
        if len(rows) == 1:
            existing_launch = row_to_manager_decision(rows[0])
    desired_agent_policy = dict(agent_policy)
    recorded_policy_snapshot = dict((existing_launch or {}).get("policy_snapshot") or {})
    if recorded_policy_snapshot:
        agent_policy = recorded_policy_snapshot
    session_id = str(
        source_env.get("QWENDEX_MANAGER_SESSION_ID")
        or (existing_launch or {}).get("session_id")
        or make_id("mgrsess")
    )
    ledger_id = str(
        source_env.get("QWENDEX_MANAGER_LEDGER_ID")
        or (existing_launch or {}).get("ledger_id")
        or make_id("mgrldg")
    )
    root_agent_id = manager_root_agent_id(ledger_id, session_id)
    prompt_digest, prompt_summary = prompt_digest_and_summary(prompt, known=prompt_known)
    estimate_id = ""
    estimate: dict[str, Any] | None = None
    validation_plan = "focused"
    plan: dict[str, Any] | None = None
    if prompt_known:
        estimate_id = make_id("estimate")
        estimate = estimate_task(config, prompt=prompt, local_status=local_status)
        validation_plan = str(estimate.get("validation_depth") or validation_plan)
    manager_required = mode == "manager" or str(agent_policy.get("mode") or "") == "manager"
    existing_policy_drift = bool(
        existing_launch
        and str(existing_launch.get("policy_hash") or "")
        and str(existing_launch.get("policy_hash") or "") != str(desired_agent_policy.get("policy_hash") or "")
    )
    launch_already_consumed = bool(
        existing_launch
        and (
            existing_launch.get("turn_id")
            or existing_launch.get("root_session_id")
            or existing_launch.get("prompt_known")
            or str(existing_launch.get("final_status") or "") != "preflight_ready"
        )
    )
    if launch_already_consumed:
        selected_route = "direct_single_writer"
        routing_reason = "This Qdex launch identity already belongs to another root turn; continue without Manager bookkeeping."
        stop_status = "STOP_MANAGER_UNATTACHED"
        direct_work_exception = True
        subagents_allowed = False
        final_status = str((existing_launch or {}).get("final_status") or "unattached")
        ok = False
    elif not qdex_permission["valid"]:
        selected_route = "direct_single_writer"
        routing_reason = "Qdex permission metadata is invalid; continue without Manager bookkeeping."
        stop_status = "STOP_MANAGER_BLOCKED_QDEX_PERMISSION"
        direct_work_exception = True
        subagents_allowed = False
        final_status = "unattached"
        ok = False
    elif prompt_known:
        plan = build_agent_team_plan(
            config,
            prompt=prompt,
            task_id=session_id,
            agent_policy=agent_policy,
            local_status=local_status,
            repo_root=repo_root,
        )
        if plan["assignments"]:
            selected_route = "manager_subagents"
            routing_reason = "manager plan suggests bounded subagent lanes that may save context or improve quality"
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
        routing_reason = "interactive prompt unknown before Codex launch; hooks may update advisory bookkeeping when the prompt is available"
        stop_status = "STOP_MANAGER_PREFLIGHT_READY"
        direct_work_exception = True
        subagents_allowed = False
        final_status = "preflight_ready"
        ok = True
    branch, git_digest = git_branch_and_status_digest(Path(repo_root))
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
        "launch_ledger_id": ledger_id,
        "root_agent_id": root_agent_id,
        "launch_pid": launch_pid,
        "launch_start_ticks": launch_start_ticks,
        "launch_nonce": launch_nonce,
        "launch_key": launch_key,
        "root_session_id": "",
        "state_db_identity": state_db_identity,
        "ledger_db_identity": ledger_db_identity,
        "runtime_identity": runtime_identity,
        **runtime_generation,
        "qdex_permission_mode": qdex_permission["mode"],
        "qdex_permission_source": qdex_permission["source"],
        "qdex_permission": qdex_permission,
        "turn_id": "",
        "agent_task_id": session_id,
        "timestamp": timestamp,
        "timestamp_created": str((existing_launch or {}).get("timestamp_created") or timestamp),
        "timestamp_updated": timestamp,
        "mode": "manager" if manager_required else str(agent_policy.get("mode") or mode),
        "selected_manager_mode": mode,
        "effective_agent_mode": str(agent_policy.get("mode") or ""),
        "effective_turn_mode": str((plan or {}).get("effective_turn_mode") or agent_policy.get("agent_use") or ""),
        "agent_use": str(agent_policy.get("agent_use") or ""),
        "policy_source": str(agent_policy.get("source") or ""),
        "policy_hash": str(agent_policy.get("policy_hash") or ""),
        "policy_snapshot": dict(agent_policy),
        "desired_global_policy_hash": str(desired_agent_policy.get("policy_hash") or ""),
        "policy_drift": existing_policy_drift,
        "session_policy_valid": True,
        "restart_required": existing_policy_drift,
        "output_policy": agent_policy.get("output_policy", {}),
        "codex_home": str(codex_home),
        "codex_home_digest_or_path_policy": codex_home_identity,
        "repo_root": repo_root,
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
        "prompt_source": "manager_preflight" if prompt_known else "",
        "prompt_length": len(prompt) if prompt_known else 0,
        "prompt_schema_version": MANAGER_PROMPT_ADMISSION_SCHEMA if prompt_known else "",
        "admission_error_code": "" if prompt_known else "prompt_pending",
        "manager_estimate": {
            "created": prompt_known,
            "estimate_id": estimate_id or None,
            "reason": "" if prompt_known else MANAGER_PROMPT_UNKNOWN_SUMMARY,
            "estimate": estimate,
        },
        "agent_plan": plan,
        "task_class": str((plan or {}).get("task_class") or ""),
        "routing_decision": {
            "selected_route": selected_route,
            "routing_reason": routing_reason,
            "subagents_allowed": subagents_allowed,
            "subagents_used": False,
            "direct_work_exception": direct_work_exception,
            "verifier_suggested": any(
                isinstance(item, Mapping)
                and str(item.get("profile") or "").strip().lower() == "verifier"
                for item in list((plan or {}).get("assignments") or [])
            ),
            "validation_plan": validation_plan,
        },
        "branch": branch,
        "git_status_digest": git_digest,
        "final_status": final_status,
        "validation_result": "",
        "stop_status": stop_status,
        "receipt_paths": [receipt_ref],
        "unresolved_risks": (
            ["managed hook context is unavailable; delegation bookkeeping may be partial"]
            if not hook_status.get("verified")
            else []
        ),
        "dry_run": dry_run,
        "idempotent_reuse": bool(existing_launch and not launch_already_consumed),
        "manager_required": manager_required,
        "exports": {
            **{
                str(key): str(value)
                for key, value in dict(agent_policy.get("env", {})).items()
            },
            "QWENDEX_MANAGER_SESSION_ID": session_id,
            "QWENDEX_MANAGER_LEDGER_ID": ledger_id,
            MANAGER_ROOT_AGENT_ID_ENV: root_agent_id,
            MANAGER_LAUNCH_PID_ENV: str(launch_pid) if launch_pid else "",
            MANAGER_LAUNCH_START_TICKS_ENV: launch_start_ticks,
            MANAGER_LAUNCH_NONCE_ENV: launch_nonce,
            MANAGER_LAUNCH_KEY_ENV: launch_key,
            MANAGER_STATE_DB_IDENTITY_ENV: state_db_identity,
            MANAGER_LEDGER_DB_IDENTITY_ENV: ledger_db_identity,
            MANAGER_RUNTIME_IDENTITY_ENV: runtime_identity,
            "QWENDEX_RUNTIME_GENERATION_ID": str(runtime_generation.get("runtime_generation") or ""),
            "QWENDEX_RUNTIME_CONTRACT_SHA256": str(runtime_generation.get("runtime_contract_sha256") or ""),
            "QWENDEX_HOOK_GENERATION": str(runtime_generation.get("hook_generation") or ""),
            "QWENDEX_QDEX_PERMISSION_MODE": str(qdex_permission["mode"]),
            "QWENDEX_QDEX_PERMISSION_SOURCE": str(qdex_permission["source"]),
            "QWENDEX_MANAGER_POLICY_HASH": str(agent_policy.get("policy_hash") or ""),
            "QWENDEX_MANAGER_STOP_STATUS": stop_status,
            "QWENDEX_OUTPUT_POLICY": str(agent_policy.get("env", {}).get("QWENDEX_OUTPUT_POLICY") or "standard"),
            "QWENDEX_KAVEMAN_ENABLED": str(agent_policy.get("env", {}).get("QWENDEX_KAVEMAN_ENABLED") or "0"),
            "QWENDEX_KAVEMAN_DIRECTIVE": str(agent_policy.get("env", {}).get("QWENDEX_KAVEMAN_DIRECTIVE") or ""),
        },
    }
    if not dry_run and not launch_already_consumed:
        with connect_state(config) as conn:
            if launch_key:
                if busy_error := begin_immediate(conn):
                    raise sqlite3.OperationalError(busy_error)
                row = conn.execute(
                    """
                    SELECT * FROM qwendex_manager_decisions
                    WHERE launch_key = ? AND ledger_id = launch_ledger_id
                    """,
                    (launch_key,),
                ).fetchone()
                concurrent_launch = row_to_manager_decision(row)
                if concurrent_launch is not None:
                    concurrent_ledger = str(concurrent_launch.get("ledger_id") or "")
                    concurrent_session = str(concurrent_launch.get("session_id") or "")
                    payload["ledger_id"] = concurrent_ledger
                    payload["launch_ledger_id"] = concurrent_ledger
                    payload["session_id"] = concurrent_session
                    payload["agent_task_id"] = concurrent_session
                    payload["root_agent_id"] = manager_root_agent_id(concurrent_ledger, concurrent_session)
                    payload["timestamp_created"] = str(concurrent_launch.get("timestamp_created") or timestamp)
                    payload["idempotent_reuse"] = True
                    concurrent_receipt = str(manager_receipt_path(config, concurrent_ledger))
                    try:
                        concurrent_receipt = str(Path(concurrent_receipt).relative_to(ROOT))
                    except ValueError:
                        pass
                    payload["receipt_paths"] = [concurrent_receipt]
                    payload["exports"]["QWENDEX_MANAGER_LEDGER_ID"] = concurrent_ledger
                    payload["exports"]["QWENDEX_MANAGER_SESSION_ID"] = concurrent_session
                    payload["exports"][MANAGER_ROOT_AGENT_ID_ENV] = payload["root_agent_id"]
            persisted = persist_manager_decision(conn, payload)
        payload["decision_ledger"] = persisted
        write_manager_decision_receipt(config, payload)
    return payload


def manager_decision_identity(
    event: Mapping[str, Any],
    *,
    env: Mapping[str, str] | None = None,
) -> tuple[str, str]:
    source_env = os.environ if env is None else env
    ledger_id = str(
        source_env.get("QWENDEX_MANAGER_LEDGER_ID")
        or event.get("manager_ledger_id")
        or event.get("ledger_id")
        or ""
    ).strip()
    session_id = str(
        source_env.get("QWENDEX_MANAGER_SESSION_ID")
        or event.get("manager_session_id")
        or ""
    ).strip()
    return ledger_id, session_id


def manager_resolution_result(
    *,
    status: str,
    reason: str,
    candidate_count: int = 0,
    decision: Mapping[str, Any] | None = None,
    mismatch_details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "reason": reason,
        "candidate_count": max(0, int(candidate_count)),
        "decision": dict(decision) if decision is not None and status == "attached" else None,
        "mismatch_details": dict(mismatch_details or {}),
    }


def manager_resolution_diagnostic(resolution: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "status": str(resolution.get("status") or "unattached"),
        "reason": str(resolution.get("reason") or "decision_not_found"),
        "candidate_count": int(resolution.get("candidate_count") or 0),
        "mismatch_details": dict(resolution.get("mismatch_details") or {}),
    }


def manager_health_resolution_reason(reason: str) -> str:
    return {
        "qwendex_identity_missing": "missing_launch_identity",
        "qwendex_identity_stale": "process_identity_mismatch",
        "qwendex_repo_mismatch": "repo_mismatch",
        "qwendex_decision_inactive": "decision_inactive",
        "qwendex_route_untrusted": "route_untrusted",
        "qwendex_hooks_untrusted": "hook_untrusted",
        "qwendex_policy_mismatch": "policy_mismatch",
        "qwendex_qdex_permission_mismatch": "qdex_permission_mismatch",
        "qwendex_ledger_mismatch": "decision_not_found",
        "qwendex_session_mismatch": "session_mismatch",
        "qwendex_root_mismatch": "session_mismatch",
        "qwendex_codex_home_mismatch": "codex_home_mismatch",
        "qwendex_decision_ambiguous": "decision_ambiguous",
    }.get(reason, reason or "decision_not_found")


def manager_decision_static_mismatch(
    config: Mapping[str, Any],
    decision: Mapping[str, Any],
    *,
    env: Mapping[str, str],
) -> tuple[str, dict[str, Any]]:
    state_identity, ledger_identity = manager_store_identities(config)
    runtime_identity = manager_runtime_identity(env)
    expected_state = str(env.get(MANAGER_STATE_DB_IDENTITY_ENV) or "").strip()
    expected_ledger = str(env.get(MANAGER_LEDGER_DB_IDENTITY_ENV) or "").strip()
    expected_runtime = str(env.get(MANAGER_RUNTIME_IDENTITY_ENV) or "").strip()
    expected_launch_key = str(env.get(MANAGER_LAUNCH_KEY_ENV) or "").strip()
    expected_launch_nonce = str(env.get(MANAGER_LAUNCH_NONCE_ENV) or "").strip()
    recorded_state = str(decision.get("state_db_identity") or "").strip()
    recorded_ledger = str(decision.get("ledger_db_identity") or "").strip()
    recorded_runtime = str(decision.get("runtime_identity") or "").strip()
    configured_generation = str(env.get("QWENDEX_RUNTIME_GENERATION_ID") or "").strip()
    configured_hook_generation = str(env.get("QWENDEX_HOOK_GENERATION") or configured_generation).strip()
    recorded_generation = str(decision.get("runtime_generation") or "").strip()
    recorded_hook_generation = str(decision.get("hook_generation") or recorded_generation).strip()
    recorded_launch_key = str(decision.get("launch_key") or "").strip()
    recorded_launch_nonce = str(decision.get("launch_nonce") or "").strip()
    configured_qdex_permission = qdex_permission_posture(config, env=env)
    has_recorded_qdex_permission = (
        "qdex_permission_mode" in decision
        or "qdex_permission_source" in decision
    )
    recorded_qdex_mode = str(decision.get("qdex_permission_mode") or "").strip()
    recorded_qdex_source = str(decision.get("qdex_permission_source") or "").strip()
    details = {
        "state_db_match": bool(expected_state and expected_state == state_identity and (not recorded_state or recorded_state == state_identity)),
        "ledger_db_match": bool(expected_ledger and expected_ledger == ledger_identity and (not recorded_ledger or recorded_ledger == ledger_identity)),
        "runtime_match": bool(expected_runtime and expected_runtime == runtime_identity and (not recorded_runtime or recorded_runtime == runtime_identity)),
        "runtime_generation_match": bool(
            not recorded_generation
            or configured_generation == recorded_generation
        ),
        "hook_generation_match": bool(
            not recorded_hook_generation
            or configured_hook_generation == recorded_hook_generation
        ),
        "launch_key_match": bool(expected_launch_key and expected_launch_key == recorded_launch_key),
        "launch_nonce_match": bool(expected_launch_nonce and expected_launch_nonce == recorded_launch_nonce),
        "qdex_permission_match": bool(
            not has_recorded_qdex_permission
            or (
                configured_qdex_permission["valid"]
                and recorded_qdex_mode
                and recorded_qdex_source
                and configured_qdex_permission["mode"] == recorded_qdex_mode
                and configured_qdex_permission["source"] == recorded_qdex_source
            )
        ),
    }
    if not details["state_db_match"]:
        return "state_db_mismatch", details
    if not details["ledger_db_match"]:
        return "ledger_db_mismatch", details
    if not details["runtime_match"]:
        return "runtime_mismatch", details
    if not details["runtime_generation_match"] or not details["hook_generation_match"]:
        return "runtime_mismatch", details
    if not details["launch_key_match"] or not details["launch_nonce_match"]:
        return "missing_launch_identity", details
    if not details["qdex_permission_match"]:
        return "qdex_permission_mismatch", details
    return "", details


def bind_manager_decision_turn(
    conn: sqlite3.Connection,
    decision: Mapping[str, Any],
    *,
    root_session_id: str,
    turn_id: str,
) -> tuple[str, dict[str, Any] | None]:
    if not root_session_id or not turn_id:
        return "turn_unattached", None
    ledger_id = str(decision.get("ledger_id") or "")
    if begin_immediate(conn):
        return "state_db_busy", None
    row = conn.execute(
        "SELECT * FROM qwendex_manager_decisions WHERE ledger_id = ?",
        (ledger_id,),
    ).fetchone()
    current = row_to_manager_decision(row)
    if current is None:
        conn.rollback()
        return "decision_not_found", None
    current_session = str(current.get("root_session_id") or "")
    current_turn = str(current.get("turn_id") or "")
    if current_session and current_session != root_session_id:
        conn.rollback()
        return "session_mismatch", None
    if current_turn and current_turn != turn_id:
        conn.rollback()
        return "turn_mismatch", None
    if current_session == root_session_id and current_turn == turn_id:
        conn.commit()
        return "attached", current
    conn.execute(
        """
        UPDATE qwendex_manager_decisions
        SET root_session_id = ?, turn_id = ?, timestamp_updated = ?
        WHERE ledger_id = ?
          AND root_session_id IN ('', ?)
          AND turn_id IN ('', ?)
        """,
        (root_session_id, turn_id, utc_now(), ledger_id, root_session_id, turn_id),
    )
    row = conn.execute(
        "SELECT * FROM qwendex_manager_decisions WHERE ledger_id = ?",
        (ledger_id,),
    ).fetchone()
    updated = row_to_manager_decision(row)
    if (
        updated is None
        or str(updated.get("root_session_id") or "") != root_session_id
        or str(updated.get("turn_id") or "") != turn_id
    ):
        conn.rollback()
        return "turn_mismatch", None
    conn.commit()
    return "attached", updated


def resolve_manager_decision(
    config: Mapping[str, Any],
    event: Mapping[str, Any],
    agent_policy: Mapping[str, Any],
    *,
    allow_turn_binding: bool = False,
    require_turn: bool = True,
    agent_task_id: str = "",
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    source_env = os.environ if env is None else env
    ledger_id, session_id = manager_decision_identity(event, env=source_env)
    root_session_id = str(event.get("session_id") or "").strip()
    turn_id = str(event.get("turn_id") or "").strip()
    repo_root = canonical_manager_repo_root(event=event, env=source_env)
    required_identity = (
        ledger_id,
        session_id,
        str(source_env.get(MANAGER_ROOT_AGENT_ID_ENV) or "").strip(),
        str(source_env.get(MANAGER_LAUNCH_PID_ENV) or "").strip(),
        str(source_env.get(MANAGER_LAUNCH_START_TICKS_ENV) or "").strip(),
        str(source_env.get(MANAGER_LAUNCH_NONCE_ENV) or "").strip(),
        str(source_env.get(MANAGER_LAUNCH_KEY_ENV) or "").strip(),
        str(source_env.get(MANAGER_STATE_DB_IDENTITY_ENV) or "").strip(),
        str(source_env.get(MANAGER_LEDGER_DB_IDENTITY_ENV) or "").strip(),
        str(source_env.get(MANAGER_RUNTIME_IDENTITY_ENV) or "").strip(),
    )
    if not all(required_identity):
        return manager_resolution_result(
            status="unattached",
            reason="missing_launch_identity",
            mismatch_details={"turn_present": bool(turn_id), "root_session_present": bool(root_session_id)},
        )
    current_state_identity, current_ledger_identity = manager_store_identities(config)
    configured_state_identity = str(source_env.get(MANAGER_STATE_DB_IDENTITY_ENV) or "").strip()
    configured_ledger_identity = str(source_env.get(MANAGER_LEDGER_DB_IDENTITY_ENV) or "").strip()
    configured_runtime_identity = str(source_env.get(MANAGER_RUNTIME_IDENTITY_ENV) or "").strip()
    current_runtime_identity = manager_runtime_identity(source_env)
    if configured_state_identity != current_state_identity:
        return manager_resolution_result(
            status="unattached",
            reason="state_db_mismatch",
            mismatch_details={"state_db_match": False},
        )
    if configured_ledger_identity != current_ledger_identity:
        return manager_resolution_result(
            status="unattached",
            reason="ledger_db_mismatch",
            mismatch_details={"state_db_match": True, "ledger_db_match": False},
        )
    if configured_runtime_identity != current_runtime_identity:
        return manager_resolution_result(
            status="unattached",
            reason="runtime_mismatch",
            mismatch_details={"state_db_match": True, "ledger_db_match": True, "runtime_match": False},
        )
    if require_turn and not agent_task_id and (not turn_id or not root_session_id):
        return manager_resolution_result(
            status="unattached",
            reason="turn_unattached",
            mismatch_details={"turn_present": bool(turn_id), "root_session_present": bool(root_session_id)},
        )
    try:
        launch_pid = int(source_env.get(MANAGER_LAUNCH_PID_ENV) or 0)
    except (TypeError, ValueError):
        launch_pid = 0
    with connect_state(config) as conn:
        identity_rows = conn.execute(
            """
            SELECT * FROM qwendex_manager_decisions
            WHERE ledger_id = ? OR launch_ledger_id = ?
            ORDER BY timestamp_created ASC
            """,
            (ledger_id, ledger_id),
        ).fetchall()
        identity_candidates = [
            item for row in identity_rows
            if (item := row_to_manager_decision(row)) is not None
        ]
        repo_candidates = [
            item for item in identity_candidates
            if str(item.get("repo_root") or "") == repo_root
        ]
        session_candidates = [
            item for item in repo_candidates
            if str(item.get("session_id") or "") == session_id
        ]
        if not identity_candidates:
            return manager_resolution_result(status="unattached", reason="decision_not_found")
        if not repo_candidates:
            return manager_resolution_result(
                status="unattached",
                reason="repo_mismatch",
                candidate_count=len(identity_candidates),
                mismatch_details={"repo_match": False},
            )
        if not session_candidates:
            return manager_resolution_result(
                status="unattached",
                reason="session_mismatch",
                candidate_count=len(repo_candidates),
                mismatch_details={"repo_match": True, "manager_session_match": False},
            )
        if agent_task_id:
            task_candidates = [
                item for item in session_candidates
                if str(item.get("agent_task_id") or item.get("session_id") or "") == agent_task_id
                and str(item.get("final_status") or "") in {"preflight_ready", "validation_pending"}
            ]
            if len(task_candidates) > 1:
                return manager_resolution_result(
                    status="ambiguous",
                    reason="decision_ambiguous",
                    candidate_count=len(task_candidates),
                    mismatch_details={"agent_task_match": True},
                )
            if not task_candidates:
                return manager_resolution_result(
                    status="unattached",
                    reason="decision_not_found",
                    mismatch_details={"agent_task_match": False},
                )
            task_candidate = task_candidates[0]
            static_reason, static_details = manager_decision_static_mismatch(
                config,
                task_candidate,
                env=source_env,
            )
            if static_reason:
                return manager_resolution_result(
                    status="unattached",
                    reason=static_reason,
                    candidate_count=1,
                    mismatch_details=static_details,
                )
            task_health = manager_launch_health(
                config,
                pid=launch_pid,
                repo_root=repo_root,
                decision=task_candidate,
                env=source_env,
                agent_policy=agent_policy,
                require_environment_identity=True,
            )
            if not task_health["trusted"]:
                return manager_resolution_result(
                    status="unattached",
                    reason=manager_health_resolution_reason(str(task_health.get("reason") or "")),
                    candidate_count=1,
                    mismatch_details={
                        **static_details,
                        "pid_alive": bool(task_health.get("pid_alive")),
                        "repo_match": bool(task_health.get("repo_match")),
                        "policy_match": bool(task_health.get("policy_match")),
                        "hook_trusted": bool(task_health.get("hook_trusted")),
                    },
                )
            return manager_resolution_result(
                status="attached",
                reason="attached",
                candidate_count=1,
                decision=task_candidate,
                mismatch_details={**static_details, "agent_task_match": True},
            )
        exact_turn = [item for item in session_candidates if turn_id and str(item.get("turn_id") or "") == turn_id]
        if len(exact_turn) > 1:
            return manager_resolution_result(
                status="ambiguous",
                reason="decision_ambiguous",
                candidate_count=len(exact_turn),
                mismatch_details={"turn_match": True},
            )
        candidate = exact_turn[0] if exact_turn else None
        clone_for_turn = False
        unbound = [
            item for item in session_candidates
            if not str(item.get("turn_id") or "")
            and not str(item.get("root_session_id") or "")
            and str(item.get("final_status") or "") == "preflight_ready"
            and str(item.get("ledger_id") or "") == str(item.get("launch_ledger_id") or "")
        ]
        if candidate is None and len(unbound) > 1:
            return manager_resolution_result(
                status="ambiguous",
                reason="decision_ambiguous",
                candidate_count=len(unbound),
                mismatch_details={"turn_match": False, "unbound_launches": len(unbound)},
            )
        if candidate is None and len(unbound) == 1:
            candidate = unbound[0]
        elif candidate is None:
            open_other_turn = [
                item for item in session_candidates
                if str(item.get("final_status") or "") in {"preflight_ready", "validation_pending"}
            ]
            launch_rows = [
                item for item in session_candidates
                if str(item.get("ledger_id") or "") == str(item.get("launch_ledger_id") or "")
            ]
            if allow_turn_binding and not open_other_turn and len(launch_rows) == 1:
                candidate = launch_rows[0]
                clone_for_turn = True
            else:
                return manager_resolution_result(
                    status="unattached",
                    reason="turn_mismatch" if open_other_turn else "decision_not_found",
                    candidate_count=len(open_other_turn) or len(launch_rows),
                    mismatch_details={"turn_match": False, "open_turn_count": len(open_other_turn)},
                )

        static_reason, static_details = manager_decision_static_mismatch(
            config,
            candidate,
            env=source_env,
        )
        if static_reason:
            return manager_resolution_result(
                status="unattached",
                reason=static_reason,
                candidate_count=1,
                mismatch_details=static_details,
            )
        launch_health = manager_launch_health(
            config,
            pid=launch_pid,
            repo_root=repo_root,
            decision=candidate,
            env=source_env,
            agent_policy=agent_policy,
            require_environment_identity=True,
        )
        if not launch_health["trusted"]:
            return manager_resolution_result(
                status="unattached",
                reason=manager_health_resolution_reason(str(launch_health.get("reason") or "")),
                candidate_count=1,
                mismatch_details={
                    **static_details,
                    "pid_alive": bool(launch_health.get("pid_alive")),
                    "repo_match": bool(launch_health.get("repo_match")),
                    "policy_match": bool(launch_health.get("policy_match")),
                    "hook_trusted": bool(launch_health.get("hook_trusted")),
                },
            )
        if clone_for_turn:
            suffix = sha256_text(f"{root_session_id}\0{turn_id}")[:16]
            turn_ledger_id = f"{ledger_id}.turn.{suffix}"
            turn_task_id = f"{session_id}:turn:{suffix}"
            receipt_path = str(manager_receipt_path(config, turn_ledger_id))
            try:
                receipt_ref = str(Path(receipt_path).relative_to(ROOT))
            except ValueError:
                receipt_ref = receipt_path
            try:
                candidate = clone_manager_decision_for_turn(
                    conn,
                    candidate,
                    ledger_id=turn_ledger_id,
                    turn_id=turn_id,
                    root_session_id=root_session_id,
                    agent_task_id=turn_task_id,
                    receipt_path=receipt_ref,
                    now=utc_now(),
                )
            except sqlite3.IntegrityError:
                row = conn.execute(
                    "SELECT * FROM qwendex_manager_decisions WHERE ledger_id = ?",
                    (turn_ledger_id,),
                ).fetchone()
                candidate = row_to_manager_decision(row)
            if candidate is None:
                return manager_resolution_result(
                    status="unattached",
                    reason="decision_not_found",
                    candidate_count=1,
                    mismatch_details={**static_details, "turn_bound": False},
                )
            static_details = {**static_details, "turn_bound": True, "turn_binding": "created"}

        candidate_session = str(candidate.get("root_session_id") or "")
        candidate_turn = str(candidate.get("turn_id") or "")
        if candidate_session and candidate_session != root_session_id:
            return manager_resolution_result(
                status="unattached",
                reason="session_mismatch",
                candidate_count=1,
                mismatch_details={**static_details, "root_session_match": False},
            )
        if candidate_turn and candidate_turn != turn_id:
            return manager_resolution_result(
                status="unattached",
                reason="turn_mismatch",
                candidate_count=1,
                mismatch_details={**static_details, "turn_match": False},
            )
        if (not candidate_session or not candidate_turn) and not allow_turn_binding:
            return manager_resolution_result(
                status="unattached",
                reason="turn_unattached",
                candidate_count=1,
                mismatch_details={**static_details, "turn_bound": False},
            )
        if not candidate_session or not candidate_turn:
            bind_status, bound = bind_manager_decision_turn(
                conn,
                candidate,
                root_session_id=root_session_id,
                turn_id=turn_id,
            )
            if bind_status != "attached" or bound is None:
                return manager_resolution_result(
                    status="unattached" if bind_status != "decision_ambiguous" else "ambiguous",
                    reason=bind_status,
                    candidate_count=1,
                    mismatch_details={**static_details, "turn_bound": False},
                )
            candidate = bound
            static_details = {**static_details, "turn_bound": True, "turn_binding": "created"}
        else:
            static_details = {**static_details, "turn_bound": True, "turn_binding": "reused"}
    return manager_resolution_result(
        status="attached",
        reason="attached",
        candidate_count=1,
        decision=candidate,
        mismatch_details=static_details,
    )


def manager_launch_health(
    config: Mapping[str, Any],
    *,
    pid: int,
    repo_root: str,
    decision: Mapping[str, Any] | None = None,
    env: Mapping[str, str] | None = None,
    agent_policy: Mapping[str, Any] | None = None,
    require_environment_identity: bool = False,
) -> dict[str, Any]:
    """Validate the immutable Qdex process/preflight binding.

    The returned projection is deliberately safe for generic downstream health
    consumers: it contains no prompts, environment values, ledger identifiers,
    credentials, or raw decision records.
    """
    source_env = os.environ if env is None else env
    canonical_repo = canonical_manager_repo_root(repo_root, env=source_env)
    candidate = dict(decision) if decision is not None else None
    ambiguous = False
    if candidate is None and pid > 0:
        with connect_state(config) as conn:
            rows = conn.execute(
                """
                SELECT * FROM qwendex_manager_decisions
                WHERE launch_pid = ? AND ledger_id = launch_ledger_id
                ORDER BY CASE final_status
                    WHEN 'preflight_ready' THEN 0
                    WHEN 'validation_pending' THEN 1
                    WHEN 'closed' THEN 2
                    ELSE 3 END,
                    timestamp_updated DESC
                LIMIT 2
                """,
                (pid,),
            ).fetchall()
        candidates = [item for row in rows if (item := row_to_manager_decision(row))]
        matching = [item for item in candidates if str(item.get("repo_root") or "") == canonical_repo]
        selected = matching if matching else candidates
        ambiguous = len(selected) > 1
        candidate = selected[0] if len(selected) == 1 else None

    expected_pid = int(candidate.get("launch_pid") or 0) if candidate else 0
    expected_start = str(candidate.get("launch_start_ticks") or "") if candidate else ""
    pid_alive = process_identity_alive(pid, expected_start) if pid > 0 and expected_start else False
    repo_match = bool(candidate) and str(candidate.get("repo_root") or "") == canonical_repo
    pid_match = bool(candidate) and expected_pid == pid and pid > 0
    start_ticks_match = pid_match and bool(expected_start) and process_start_ticks(pid) == expected_start
    decision_state = str(candidate.get("final_status") or "missing") if candidate else "missing"
    active_state = decision_state in {"preflight_ready", "validation_pending", "closed"}
    route_trusted = bool(candidate) and str(candidate.get("selected_route") or "") in {
        "direct_single_writer",
        "manager_subagents",
    }
    hook_trusted = bool(candidate) and bool(candidate.get("hook_verified") or candidate.get("hook_override"))

    recorded_policy = str(candidate.get("policy_hash") or "") if candidate else ""
    launch_policy_hash = str(source_env.get("QWENDEX_MANAGER_POLICY_HASH") or "").strip()
    session_policy_valid = bool(recorded_policy) and (
        not require_environment_identity
        or bool(launch_policy_hash and launch_policy_hash == recorded_policy)
    )
    try:
        with connect_state(config) as policy_conn:
            desired_mode = current_manager_mode(config, policy_conn)
            desired_kaveman = current_kaveman_enabled(config, policy_conn)
            desired_local_enabled = current_local_enabled(config, policy_conn)
        desired_policy = resolve_agent_policy(
            config,
            selected_manager_mode=desired_mode,
            kaveman_enabled=desired_kaveman,
            env={},
        )
        desired_policy = attach_local_routing_snapshot(
            desired_policy,
            config,
            enabled=desired_local_enabled,
        )
        desired_policy_hash = str(desired_policy.get("policy_hash") or "")
    except (OSError, sqlite3.Error, ValueError):
        desired_policy_hash = ""
    policy_drift = bool(
        recorded_policy
        and desired_policy_hash
        and recorded_policy != desired_policy_hash
    )
    policy_match = session_policy_valid
    configured_qdex_permission = qdex_permission_posture(config, env=source_env)
    recorded_qdex_mode = str(candidate.get("qdex_permission_mode") or "") if candidate else ""
    recorded_qdex_source = str(candidate.get("qdex_permission_source") or "") if candidate else ""
    qdex_permission_match = (
        not require_environment_identity
        or bool(
            configured_qdex_permission["valid"]
            and recorded_qdex_mode
            and recorded_qdex_source
            and configured_qdex_permission["mode"] == recorded_qdex_mode
            and configured_qdex_permission["source"] == recorded_qdex_source
        )
    )

    launch_ledger = str(candidate.get("launch_ledger_id") or candidate.get("ledger_id") or "") if candidate else ""
    recorded_session = str(candidate.get("session_id") or "") if candidate else ""
    expected_root = manager_decision_root_agent_id(candidate or {}) if candidate else ""
    identity_present = bool(launch_ledger and recorded_session and expected_root)
    ledger_match = True
    session_match = True
    root_match = True
    environment_pid_match = True
    environment_start_match = True
    codex_home_match = True
    if require_environment_identity:
        configured_ledger = str(source_env.get("QWENDEX_MANAGER_LEDGER_ID") or "").strip()
        configured_session = str(source_env.get("QWENDEX_MANAGER_SESSION_ID") or "").strip()
        configured_root = str(source_env.get(MANAGER_ROOT_AGENT_ID_ENV) or "").strip()
        configured_pid = str(source_env.get(MANAGER_LAUNCH_PID_ENV) or "").strip()
        configured_start = str(source_env.get(MANAGER_LAUNCH_START_TICKS_ENV) or "").strip()
        configured_home = str(source_env.get("CODEX_HOME") or "").strip()
        ledger_match = bool(configured_ledger) and configured_ledger == launch_ledger
        session_match = bool(configured_session) and configured_session == recorded_session
        root_match = bool(configured_root) and configured_root == expected_root
        environment_pid_match = bool(configured_pid) and configured_pid == str(expected_pid)
        environment_start_match = bool(configured_start) and configured_start == expected_start
        recorded_home = str(candidate.get("codex_home_digest_or_path_policy") or "") if candidate else ""
        codex_home_match = bool(configured_home and recorded_home) and path_digest_policy(Path(configured_home)) == recorded_home
        identity_present = identity_present and all(
            (configured_ledger, configured_session, configured_root, configured_pid, configured_start)
        )

    checks = {
        "decision_found": candidate is not None,
        "identity_present": identity_present,
        "pid_alive": pid_alive,
        "pid_match": pid_match,
        "start_ticks_match": start_ticks_match,
        "repo_match": repo_match,
        "decision_active": active_state,
        "route_trusted": route_trusted,
        "policy_match": policy_match,
        "qdex_permission_match": qdex_permission_match,
        "ledger_match": ledger_match,
        "session_match": session_match,
        "root_match": root_match,
        "environment_pid_match": environment_pid_match,
        "environment_start_match": environment_start_match,
        "codex_home_match": codex_home_match,
    }
    trusted = all(checks.values())
    reason_map = (
        ("decision_found", "qwendex_identity_missing"),
        ("identity_present", "qwendex_identity_missing"),
        ("pid_alive", "qwendex_identity_stale"),
        ("pid_match", "qwendex_identity_stale"),
        ("start_ticks_match", "qwendex_identity_stale"),
        ("repo_match", "qwendex_repo_mismatch"),
        ("decision_active", "qwendex_decision_inactive"),
        ("route_trusted", "qwendex_route_untrusted"),
        ("policy_match", "qwendex_policy_mismatch"),
        ("qdex_permission_match", "qwendex_qdex_permission_mismatch"),
        ("ledger_match", "qwendex_ledger_mismatch"),
        ("session_match", "qwendex_session_mismatch"),
        ("root_match", "qwendex_root_mismatch"),
        ("environment_pid_match", "qwendex_identity_stale"),
        ("environment_start_match", "qwendex_identity_stale"),
        ("codex_home_match", "qwendex_codex_home_mismatch"),
    )
    reason = (
        "trusted"
        if trusted
        else "qwendex_decision_ambiguous"
        if ambiguous
        else next(code for key, code in reason_map if not checks[key])
    )
    return {
        "trusted": trusted,
        "pid_alive": pid_alive,
        "repo_match": repo_match,
        "decision_state": decision_state,
        "reason": reason,
        "recovery_command": f"qdex -C {shlex.quote(canonical_repo)}",
        "identity_present": identity_present,
        "policy_match": policy_match,
        "qdex_permission_match": qdex_permission_match,
        "qdex_permission_mode": recorded_qdex_mode or "workspace-write",
        "qdex_permission_source": recorded_qdex_source or "default",
        "session_policy_hash": recorded_policy,
        "desired_global_policy_hash": desired_policy_hash,
        "policy_drift": policy_drift,
        "session_policy_valid": session_policy_valid,
        "restart_required": policy_drift,
        "hook_trusted": hook_trusted,
    }


def manager_root_cleanup_identity_for_event(
    config: Mapping[str, Any],
    event: Mapping[str, Any],
    agent_policy: Mapping[str, Any],
) -> tuple[str, dict[str, Any] | None, str]:
    """Resolve optional root lock-cleanup identity from Qdex lifecycle state.

    A mismatch only skips best-effort cleanup; it never controls root work.
    """
    configured_root_id = str(os.environ.get(MANAGER_ROOT_AGENT_ID_ENV) or "").strip()
    repo_root = canonical_manager_repo_root(event=event)
    resolution = resolve_manager_decision(
        config,
        event,
        agent_policy,
        allow_turn_binding=True,
    )
    decision = resolution.get("decision")
    if resolution.get("status") != "attached" or not isinstance(decision, Mapping):
        reason = str(resolution.get("reason") or "decision_not_found")
        return "", None, f"Qwendex root lock cleanup skipped: lifecycle association unavailable ({reason})."
    decision = dict(decision)
    launch_health = manager_launch_health(
        config,
        pid=int(decision.get("launch_pid") or 0),
        repo_root=repo_root,
        decision=decision,
        env=os.environ,
        agent_policy=agent_policy,
        require_environment_identity=True,
    )
    if not launch_health["trusted"]:
        reason = str(launch_health.get("reason") or "qwendex_identity_missing")
        messages = {
            "qwendex_identity_missing": "Qwendex root lock cleanup skipped: launch identity is unavailable.",
            "qwendex_identity_stale": "Qwendex root lock cleanup skipped: launcher identity is stale.",
            "qwendex_repo_mismatch": "Qwendex root lock cleanup skipped: repository scope differs.",
            "qwendex_policy_mismatch": "Qwendex root lock cleanup skipped: policy snapshot differs.",
            "qwendex_qdex_permission_mismatch": "Qwendex root lock cleanup skipped: Qdex permission metadata differs.",
            "qwendex_codex_home_mismatch": "Qwendex root lock cleanup skipped: Codex home metadata differs.",
            "qwendex_hooks_untrusted": "Qwendex root lock cleanup skipped: hook metadata is incomplete.",
        }
        return "", decision, messages.get(
            reason,
            "Qwendex root lock cleanup skipped: launch metadata differs.",
        )
    expected_root_id = manager_decision_root_agent_id(decision)
    if configured_root_id != expected_root_id:
        return "", decision, "Qwendex root lock cleanup skipped: root identity differs."
    launch_pid = int(decision.get("launch_pid") or 0)
    launch_start_ticks = str(decision.get("launch_start_ticks") or "")
    if launch_pid:
        configured_pid = str(os.environ.get(MANAGER_LAUNCH_PID_ENV) or "").strip()
        configured_start = str(os.environ.get(MANAGER_LAUNCH_START_TICKS_ENV) or "").strip()
        if configured_pid != str(launch_pid) or configured_start != launch_start_ticks:
            return "", decision, "Qwendex root lock cleanup skipped: launcher process metadata differs."
        if not process_identity_alive(launch_pid, launch_start_ticks):
            return "", decision, "Qwendex root lock cleanup skipped: launcher process is no longer active."
    if str(decision.get("repo_root") or "") != repo_root:
        return "", decision, "Qwendex root lock cleanup skipped: repository scope differs."
    if str(decision.get("final_status") or "") not in {"preflight_ready", "validation_pending"}:
        return "", decision, "Qwendex root lock cleanup skipped: lifecycle decision is no longer active."
    if str(decision.get("selected_route") or "") not in {"direct_single_writer", "manager_subagents"}:
        return "", decision, "Qwendex root lock cleanup skipped: lifecycle route is not recognized."
    expected_home = path_digest_policy(codex_home_from_env(os.environ))
    actual_home = str(decision.get("codex_home_digest_or_path_policy") or "")
    if actual_home and actual_home != expected_home:
        return "", decision, "Qwendex root lock cleanup skipped: Codex home metadata differs."
    # Output-policy changes are accepted at the root-turn boundary and do not
    # alter the immutable launch identity that owns writer leases.
    launch_policy = manager_launch_policy_snapshot(config)
    expected_policy = str(
        (launch_policy or {}).get("policy_hash")
        or agent_policy.get("policy_hash")
        or ""
    )
    actual_policy = str(decision.get("policy_hash") or "")
    if expected_policy and actual_policy and expected_policy != actual_policy:
        return "", decision, "Qwendex root lock cleanup skipped: policy snapshot differs."
    if not bool(decision.get("hook_verified")) and not bool(decision.get("hook_override")):
        return "", decision, "Qwendex root lock cleanup skipped: hook metadata is incomplete."
    return configured_root_id, decision, ""


def release_manager_root_locks(
    conn: sqlite3.Connection,
    decision: Mapping[str, Any],
    *,
    now: str,
) -> list[dict[str, Any]]:
    root_agent_id = manager_decision_root_agent_id(decision)
    prefix = f"{root_agent_id}{MANAGER_ROOT_TOOL_SEPARATOR}"
    rows = conn.execute(
        """
        SELECT * FROM qwendex_agent_file_locks
        WHERE released_at = ''
          AND (agent_id = ? OR substr(agent_id, 1, ?) = ?)
        """,
        (root_agent_id, len(prefix), prefix),
    ).fetchall()
    conn.execute(
        """
        UPDATE qwendex_agent_file_locks
        SET released_at = ?
        WHERE released_at = ''
          AND (agent_id = ? OR substr(agent_id, 1, ?) = ?)
        """,
        (now, root_agent_id, len(prefix), prefix),
    )
    return [lock for row in rows if (lock := row_to_file_lock(row))]


def release_reclaimable_manager_root_locks(
    conn: sqlite3.Connection,
    *,
    repo_root: str,
    now: str,
) -> list[dict[str, Any]]:
    root_locks = [
        lock
        for lock in active_file_locks(conn, repo_root=repo_root)
        if str(lock.get("agent_id") or "").startswith("manager-root-")
    ]
    if not root_locks:
        return []
    rows = conn.execute(
        """
        SELECT * FROM qwendex_manager_decisions
        WHERE repo_root = ?
        ORDER BY timestamp_updated DESC
        """,
        (repo_root,),
    ).fetchall()
    decisions = [
        decision
        for row in rows
        if (decision := row_to_manager_decision(row)) is not None
    ]
    families = {
        manager_root_owner_family(str(lock.get("agent_id") or ""))
        for lock in root_locks
    }
    reclaimed: list[dict[str, Any]] = []
    for family in families:
        launch_decisions = [
            decision
            for decision in decisions
            if manager_decision_root_agent_id(decision) == family
        ]
        if not launch_decisions:
            continue
        active_decisions = [
            decision
            for decision in launch_decisions
            if str(decision.get("final_status") or "")
            in {"preflight_ready", "validation_pending"}
        ]
        reason = ""
        if not active_decisions:
            reason = "terminal_manager_launch"
        else:
            identities = [
                (
                    int(decision.get("launch_pid") or 0),
                    str(decision.get("launch_start_ticks") or ""),
                )
                for decision in active_decisions
                if int(decision.get("launch_pid") or 0) > 0
            ]
            if identities and not any(
                process_identity_alive(pid, start_ticks)
                for pid, start_ticks in identities
            ):
                reason = "dead_manager_launch"
            elif not identities:
                timestamps = [
                    str(
                        decision.get("timestamp_updated")
                        or decision.get("timestamp_created")
                        or ""
                    )
                    for decision in active_decisions
                ]
                try:
                    stale = timestamps and all(
                        (datetime.now(UTC) - parse_utc(timestamp)).total_seconds()
                        > MANAGER_DECISION_ATTACH_WINDOW_MINUTES * 60
                        for timestamp in timestamps
                    )
                except (TypeError, ValueError):
                    stale = False
                if stale:
                    reason = "stale_manager_launch"
        if not reason:
            continue
        released = release_manager_root_locks(
            conn,
            launch_decisions[0],
            now=now,
        )
        reclaimed.extend({**lock, "reclaim_reason": reason} for lock in released)
    return reclaimed


def clone_manager_decision_for_turn(
    conn: sqlite3.Connection,
    decision: Mapping[str, Any],
    *,
    ledger_id: str,
    turn_id: str,
    root_session_id: str,
    agent_task_id: str,
    receipt_path: str,
    now: str,
) -> dict[str, Any] | None:
    columns = [str(row["name"]) for row in conn.execute("PRAGMA table_info(qwendex_manager_decisions)")]
    overrides: dict[str, Any] = {
        "ledger_id": ledger_id,
        "timestamp_created": now,
        "timestamp_updated": now,
        "launch_ledger_id": str(decision.get("launch_ledger_id") or decision.get("ledger_id") or ""),
        "turn_id": turn_id,
        "root_session_id": root_session_id,
        "agent_task_id": agent_task_id,
        "prompt_known": 0,
        "prompt_digest": "",
        "prompt_summary": "",
        "prompt_source": "",
        "prompt_length": 0,
        "prompt_schema_version": "",
        "admission_error_code": "prompt_pending",
        "estimate_id": "",
        "effective_turn_mode": "",
        "task_class": "",
        "agent_plan_json": "{}",
        "subagents_used": 0,
        "final_status": "preflight_ready",
        "validation_result": "",
        "stop_status": "STOP_MANAGER_PREFLIGHT_READY",
        "receipt_paths_json": json_dumps([receipt_path]),
        "unresolved_risks_json": "[]",
    }
    select_parts: list[str] = []
    values: list[Any] = []
    for column in columns:
        if column in overrides:
            select_parts.append("?")
            values.append(overrides[column])
        else:
            select_parts.append(f'"{column}"')
    quoted_columns = ", ".join(f'"{column}"' for column in columns)
    source_ledger_id = str(decision.get("ledger_id") or "")
    if busy_error := begin_immediate(conn):
        raise sqlite3.OperationalError(busy_error)
    conn.execute(
        f"""
        INSERT OR IGNORE INTO qwendex_manager_decisions ({quoted_columns})
        SELECT {', '.join(select_parts)}
        FROM qwendex_manager_decisions WHERE ledger_id = ?
        """,
        (*values, source_ledger_id),
    )
    conn.commit()
    return latest_manager_decision(
        conn,
        repo_root=str(decision.get("repo_root") or ""),
        ledger_id=ledger_id,
    )


def prompt_admission_error_code(event: Mapping[str, Any]) -> str:
    if "prompt" not in event:
        return "prompt_field_missing"
    if not isinstance(event.get("prompt"), str):
        return "hook_payload_schema_mismatch"
    if not str(event.get("prompt") or "").strip():
        return "prompt_field_missing"
    return ""


def record_manager_prompt_admission_failure(
    config: Mapping[str, Any],
    decision: Mapping[str, Any],
    *,
    error_code: str,
) -> dict[str, Any] | None:
    now = utc_now()
    with connect_state(config) as conn:
        conn.execute(
            """
            UPDATE qwendex_manager_decisions
            SET timestamp_updated = ?, prompt_known = 0, prompt_digest = '',
                prompt_summary = ?, prompt_source = ?, prompt_length = 0,
                prompt_schema_version = ?, admission_error_code = ?,
                selected_route = ?, routing_reason = ?, subagents_allowed = 0,
                direct_work_exception = ?, final_status = ?, stop_status = ?
            WHERE ledger_id = ?
            """,
            (
                now,
                "privacy_safe_prompt_metadata_unavailable",
                MANAGER_PROMPT_SOURCE,
                MANAGER_PROMPT_ADMISSION_SCHEMA,
                error_code,
                "direct_single_writer",
                f"Manager advisory prompt bookkeeping unavailable: {error_code}",
                1,
                "preflight_ready",
                "STOP_MANAGER_DIRECT_READY",
                str(decision.get("ledger_id") or ""),
            ),
        )
        conn.commit()
        updated = latest_manager_decision(
            conn,
            repo_root=str(decision.get("repo_root") or ""),
            ledger_id=str(decision.get("ledger_id") or ""),
        )
    if updated is not None:
        write_manager_decision_receipt(config, manager_decision_receipt_payload(updated))
    return updated


def update_manager_decision_from_prompt(
    config: Mapping[str, Any],
    event: Mapping[str, Any],
    agent_policy: Mapping[str, Any],
    *,
    resolved_decision: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Update only the root turn already selected by the canonical resolver."""
    if event.get("agent_id") or event.get("agent_type"):
        return None
    prompt = str(event.get("prompt") or "").strip()
    if not prompt:
        return None
    if resolved_decision is None:
        return None
    event_repo = canonical_manager_repo_root(event=event)
    with connect_state(config) as conn:
        row = conn.execute(
            "SELECT * FROM qwendex_manager_decisions WHERE ledger_id = ? AND repo_root = ?",
            (str(resolved_decision.get("ledger_id") or ""), event_repo),
        ).fetchone()
        decision = row_to_manager_decision(row)
        if decision is None or str(decision.get("final_status") or "") != "preflight_ready":
            return None
        turn_id = str(event.get("turn_id") or "").strip()
        root_session_id = str(event.get("session_id") or "").strip()
        if (
            not turn_id
            or not root_session_id
            or str(decision.get("turn_id") or "") != turn_id
            or str(decision.get("root_session_id") or "") != root_session_id
        ):
            return None
        launch_id = str(decision.get("launch_ledger_id") or decision.get("ledger_id") or "")
        decision_repo = str(decision.get("repo_root") or "")
        if decision_repo and event_repo != decision_repo:
            return None
        local_status = manager_decision_local_status(config, decision)
        estimate = estimate_task(config, prompt=prompt, local_status=local_status)
        effective_turn_id = turn_id
        agent_task_id = str(
            decision.get("agent_task_id")
            or decision.get("session_id")
            or ""
        )
        plan = build_agent_team_plan(
            config,
            prompt=prompt,
            task_id=agent_task_id,
            agent_policy=agent_policy,
            local_status=local_status,
            repo_root=decision_repo or event_repo,
        )
        if plan["assignments"]:
            selected_route = "manager_subagents"
            routing_reason = "interactive turn selected bounded manager lanes"
            stop_status = "STOP_MANAGER_SUBAGENTS_READY"
            direct_work_exception = 0
            subagents_allowed = 1
        else:
            selected_route = "direct_single_writer"
            routing_reason = str(
                plan.get("direct_work_exception")
                or plan.get("routing_reason")
                or "interactive turn selected direct work"
            )
            stop_status = "STOP_MANAGER_DIRECT_READY"
            direct_work_exception = 1
            subagents_allowed = 0
        prompt_digest, prompt_summary = prompt_digest_and_summary(prompt, known=True)
        now = utc_now()
        estimate_id = make_id("estimate")
        conn.execute(
            """
            UPDATE qwendex_manager_decisions
            SET timestamp_updated = ?, prompt_known = 1, prompt_digest = ?, prompt_summary = ?,
                estimate_id = ?, selected_route = ?, routing_reason = ?, subagents_allowed = ?,
                subagents_used = 0, direct_work_exception = ?, verifier_required = ?,
                validation_plan = ?, final_status = 'preflight_ready', validation_result = '',
                stop_status = ?, launch_ledger_id = ?, turn_id = ?, agent_task_id = ?,
                selected_mode = ?, effective_turn_mode = ?, task_class = ?,
                agent_plan_json = ?, prompt_source = ?, prompt_length = ?,
                prompt_schema_version = ?, admission_error_code = ''
            WHERE ledger_id = ?
            """,
            (
                now,
                prompt_digest,
                prompt_summary,
                estimate_id,
                selected_route,
                routing_reason,
                subagents_allowed,
                direct_work_exception,
                1 if agent_policy.get("require_verifier_for_edits") else 0,
                str(estimate.get("validation_depth") or "focused"),
                stop_status,
                launch_id,
                effective_turn_id,
                agent_task_id,
                str(agent_policy.get("mode") or ""),
                str(plan.get("effective_turn_mode") or agent_policy.get("agent_use") or ""),
                str(plan.get("task_class") or ""),
                json_dumps(plan),
                MANAGER_PROMPT_SOURCE,
                len(prompt),
                MANAGER_PROMPT_ADMISSION_SCHEMA,
                str(decision.get("ledger_id") or ""),
            ),
        )
        conn.commit()
        updated = latest_manager_decision(
            conn,
            repo_root=decision_repo or event_repo,
            ledger_id=str(decision.get("ledger_id") or ""),
        )
    if updated is None:
        return None
    write_manager_decision_receipt(config, manager_decision_receipt_payload(updated))
    return {"manager_decision": updated, "agent_plan": plan, "estimate": estimate}


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
    subagents_used: bool | None = None,
) -> dict[str, Any] | None:
    paths = list(receipt_paths if receipt_paths is not None else decision.get("receipt_paths") or [])
    risks = list(unresolved_risks if unresolved_risks is not None else decision.get("unresolved_risks") or [])
    conn.execute(
        """
        UPDATE qwendex_manager_decisions
        SET timestamp_updated = ?, final_status = ?, validation_result = ?, stop_status = ?,
            receipt_paths_json = ?, unresolved_risks_json = ?,
            subagents_used = COALESCE(?, subagents_used)
        WHERE ledger_id = ?
        """,
        (
            utc_now(),
            final_status,
            validation_result,
            stop_status,
            json_dumps(paths),
            json_dumps(risks),
            None if subagents_used is None else (1 if subagents_used else 0),
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
    desired_policy_hash = str(decision.get("desired_global_policy_hash") or decision.get("policy_hash") or "")
    session_policy_hash = str(decision.get("policy_hash") or "")
    policy_drift = bool(desired_policy_hash and session_policy_hash and desired_policy_hash != session_policy_hash)
    return {
        "ok": str(decision.get("selected_route") or "") != "blocked",
        "schema_version": int(decision.get("schema_version") or 1),
        "record_type": "manager_decision",
        "session_id": decision.get("session_id"),
        "ledger_id": decision.get("ledger_id"),
        "launch_ledger_id": decision.get("launch_ledger_id") or decision.get("ledger_id"),
        "root_agent_id": manager_decision_root_agent_id(decision),
        "launch_pid": int(decision.get("launch_pid") or 0),
        "launch_start_ticks": decision.get("launch_start_ticks") or "",
        "launch_nonce": decision.get("launch_nonce") or "",
        "launch_key": decision.get("launch_key") or "",
        "root_session_id": decision.get("root_session_id") or "",
        "state_db_identity": decision.get("state_db_identity") or "",
        "ledger_db_identity": decision.get("ledger_db_identity") or "",
        "runtime_identity": decision.get("runtime_identity") or "",
        "runtime_generation": decision.get("runtime_generation") or "",
        "hook_generation": decision.get("hook_generation") or "",
        "runtime_contract_sha256": decision.get("runtime_contract_sha256") or "",
        "patched_binary_sha256": decision.get("patched_binary_sha256") or "",
        "codex_patch_sha256": decision.get("codex_patch_sha256") or "",
        "config_sha256": decision.get("config_sha256") or "",
        "runtime_state_schema_version": int(decision.get("runtime_state_schema_version") or 0),
        "qdex_permission": {
            "mode": decision.get("qdex_permission_mode") or "workspace-write",
            "source": decision.get("qdex_permission_source") or "default",
        },
        "turn_id": decision.get("turn_id") or "",
        "agent_task_id": decision.get("agent_task_id") or decision.get("session_id"),
        "timestamp": decision.get("timestamp_updated"),
        "timestamp_created": decision.get("timestamp_created"),
        "timestamp_updated": decision.get("timestamp_updated"),
        "mode": decision.get("mode"),
        "agent_use": decision.get("agent_use"),
        "policy_source": decision.get("policy_source"),
        "policy_hash": decision.get("policy_hash"),
        "desired_global_policy_hash": desired_policy_hash,
        "policy_drift": policy_drift,
        "session_policy_valid": bool(session_policy_hash),
        "restart_required": policy_drift,
        "codex_home": decision.get("codex_home"),
        "codex_home_digest_or_path_policy": decision.get("codex_home_digest_or_path_policy"),
        "repo_root": decision.get("repo_root"),
        "hook_status": {
            "hook_source_count": decision.get("hook_source_count"),
            "configured": decision.get("hook_configured"),
            "verified": decision.get("hook_verified"),
            "override": decision.get("hook_override"),
            "override_reason": decision.get("hook_override_reason") or None,
            "write_gating": False,
            "advisory_for_lifecycle": True,
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
            "prompt_source": decision.get("prompt_source") or None,
            "prompt_length": int(decision.get("prompt_length") or 0),
            "schema_version": decision.get("prompt_schema_version") or None,
            "admission_error_code": decision.get("admission_error_code") or None,
        },
        "selected_mode": decision.get("selected_mode") or decision.get("mode"),
        "effective_turn_mode": decision.get("effective_turn_mode") or decision.get("agent_use"),
        "task_class": decision.get("task_class") or None,
        "agent_plan": dict(decision.get("agent_plan") or {}),
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
            "verifier_suggested": any(
                isinstance(item, Mapping)
                and str(item.get("profile") or "").strip().lower() == "verifier"
                for item in list((decision.get("agent_plan") or {}).get("assignments") or [])
            ),
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


def validation_text_state(text: str) -> bool | None:
    negative = re.compile(
        r"(?i)\b(?:fail(?:ed|ure|ures|ing)?|error(?:ed|s)?|missing|"
        r"not\s+(?:run|tested|executed|performed|available)|untested|"
        r"no\s+validation|none|n/a|na|skipped|todo)\b"
    )
    positive = re.compile(
        r"(?i)\b(?:pass(?:ed|es|ing)?|success(?:ful|fully)?|succeeded|ok(?:ay)?|"
        r"green|verified|validated|checked|clean)\b"
    )
    subject = re.compile(
        r"(?i)\b(?:pytest|unittest|ruff|py_compile|qwendex-dev\s+verify|"
        r"scripts/qwendex|receipts?|tests?|checks?)\b"
    )
    explicit_lines: list[str] = []
    relevant_lines: list[str] = []
    for line in (text or "").splitlines():
        match = re.match(r"(?i)^\s*validation(?:_status)?\s*:\s*(.+?)\s*$", line)
        if match:
            explicit_lines.append(match.group(1).strip())
        elif re.match(r"(?i)^\s*(?:[-*]\s*)?(?:outcome|result)\s*:\s*", line):
            # Native workers commonly put the command on one commands_run
            # bullet and its return summary on the following Outcome bullet.
            # Treat that explicitly labeled result as validation evidence;
            # free-form claims elsewhere remain insufficient.
            relevant_lines.append(line.strip())
        elif subject.search(line):
            relevant_lines.append(line.strip())
    # A terminal report may include a failed exploratory invocation followed by
    # the canonical passing validation command. Require the worker to classify
    # that mixed history explicitly instead of letting an incidental earlier
    # diagnostic permanently mask the final validation outcome.
    for line in explicit_lines:
        without_benign_negatives = re.sub(
            r"(?i)\b(?:no|zero|0)\s+(?:errors?|failures?)\b",
            "",
            line,
        )
        if negative.search(without_benign_negatives):
            return False
    if explicit_lines and any(positive.search(line) for line in explicit_lines):
        return True
    for line in relevant_lines:
        without_benign_negatives = re.sub(
            r"(?i)\b(?:no|zero|0)\s+(?:errors?|failures?)\b",
            "",
            line,
        )
        if negative.search(without_benign_negatives):
            return False
    for line in relevant_lines:
        if line and positive.search(line):
            return True
    return None


def structured_validation_state(value: Any) -> bool | None:
    if isinstance(value, str):
        return validation_text_state(value)
    if isinstance(value, Mapping):
        for key in ("success", "passed", "ok"):
            if key in value:
                return value.get(key) is True
        if "returncode" in value:
            returncode = value.get("returncode")
            return isinstance(returncode, int) and not isinstance(returncode, bool) and returncode == 0
        for key in ("status", "validation_status", "result"):
            if key not in value:
                continue
            status = str(value.get(key) or "").strip().lower()
            if status in {"pass", "passed", "success", "successful", "ok", "verified", "validated"}:
                return True
            if status in {"fail", "failed", "failure", "error", "blocked", "missing", "skipped", "pending"}:
                return False
        combined = "\n".join(
            str(value.get(key) or "")
            for key in ("command", "summary", "message", "stdout", "stderr", "evidence")
            if value.get(key)
        )
        return validation_text_state(combined)
    if isinstance(value, (list, tuple)):
        if not value:
            return None
        states = [structured_validation_state(item) for item in value]
        if any(state is False for state in states):
            return False
        return True if states and all(state is True for state in states) else None
    return None


def validation_receipt_state(value: Any, config: Mapping[str, Any]) -> bool:
    raw_path = value.get("path") if isinstance(value, Mapping) else value
    if not isinstance(raw_path, str) or not raw_path.strip():
        return False
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    if not is_trusted_receipt_path(path, config) or not path.is_file() or path.is_symlink():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    verification = verify_receipt_data(payload)
    if not verification.get("verified"):
        return False
    if "status" in payload:
        return payload.get("status") == "pass"
    if "success" in payload:
        return payload.get("success") is True
    return False


def stop_event_has_validation_evidence(
    event: Mapping[str, Any],
    message: str,
    *,
    config: Mapping[str, Any] | None = None,
) -> bool:
    evidence_value = event.get("validation_evidence")
    if evidence_value is not None and structured_validation_state(evidence_value) is not True:
        return False
    command_value = event.get("commands_run")
    if command_value is not None and structured_validation_state(command_value) is not True:
        return False
    receipt_values = event.get("receipt_paths")
    if receipt_values is not None:
        receipt_items = list(receipt_values) if isinstance(receipt_values, (list, tuple)) else [receipt_values]
        effective_config = config or load_qwendex_config(env=os.environ)
        if not receipt_items or not all(
            validation_receipt_state(item, effective_config) for item in receipt_items
        ):
            return False
    structured_present = any(
        event.get(key) is not None
        for key in ("validation_evidence", "commands_run", "receipt_paths")
    )
    if structured_present:
        return True
    return validation_text_state(message or "") is True


def stop_event_has_dirty_classification(event: Mapping[str, Any], message: str) -> bool:
    if event.get("dirty_worktree_classification") or event.get("git_status_digest"):
        return True
    return bool(re.search(r"(?im)^\s*(dirty|git state|worktree)\s*:", message or ""))


def agent_metrics_payload(config: Mapping[str, Any], agent_policy: Mapping[str, Any]) -> dict[str, Any]:
    repo_root = canonical_manager_repo_root()
    with connect_state(config) as conn:
        rows = conn.execute("SELECT * FROM qwendex_agent_sessions ORDER BY updated_at DESC").fetchall()
        ledger_sessions = [session for row in rows if (session := row_to_agent_session(row))]
        sessions = sessions_for_repo(ledger_sessions, repo_root)
        locks = active_file_locks(conn, repo_root=repo_root)
        ledger_locks = active_file_locks(conn)
    status_counts: dict[str, int] = {}
    validation_counts: dict[str, int] = {}
    attention_flagged_incomplete = 0
    terminal_count = 0
    structured_outcome_observed = 0
    for session in sessions:
        status = str(session.get("status") or "unknown")
        validation = str(session.get("validation_status") or "pending")
        status_counts[status] = status_counts.get(status, 0) + 1
        validation_counts[validation] = validation_counts.get(validation, 0) + 1
        if session_attention_flagged(session) and status not in AGENT_TERMINAL_STATUSES:
            attention_flagged_incomplete += 1
        if status in AGENT_TERMINAL_STATUSES:
            terminal_count += 1
            if bool(session.get("final_report_present")) or str(session.get("stop_reason") or "") in {
                "final_report",
                "blocked_contract",
                "failed_contract",
            }:
                structured_outcome_observed += 1
    active_writers = [lock for lock in locks if lock.get("lock_type") == "write"]
    structured_outcome_observation_rate = (
        round(structured_outcome_observed / terminal_count, 4)
        if terminal_count
        else None
    )
    return {
        "schema_version": "qwendex.agent_metrics.v2",
        "agent_use": agent_policy.get("agent_use"),
        "agent_policy_hash": agent_policy.get("policy_hash"),
        "session_count": len(sessions),
        "ledger_session_count": len(ledger_sessions),
        "repo_root": repo_root,
        "legacy_unscoped_session_count": sum(1 for session in ledger_sessions if not session.get("repo_root")),
        "active_count": status_counts.get("active", 0),
        "terminal_count": terminal_count,
        "status_counts": status_counts,
        "validation_counts": validation_counts,
        "attention_flagged_incomplete_count": attention_flagged_incomplete,
        "structured_outcome_observed_count": structured_outcome_observed,
        "structured_outcome_observation_rate": structured_outcome_observation_rate,
        "active_file_lock_count": len(locks),
        "ledger_active_file_lock_count": len(ledger_locks),
        "active_writer_count": len(active_writers),
        "managed_hook_event_count": len(MANAGED_AGENT_HOOKS),
        "built_in_profile_count": len(DEFAULT_AGENT_PROFILES),
        "raw_output_artifact_count": sum(1 for session in sessions for artifact in session.get("artifacts", []) if str(artifact).endswith("/raw-output.md")),
    }


def command_agent(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    if override_errors := manager_override_errors(args):
        return stable_envelope(
            command="agent",
            status="blocked",
            summary="Qwendex agent override values are outside configured bounds.",
            errors=override_errors,
        )
    action = args.action or "status"
    event: dict[str, Any] = {}
    hook_event_name = ""
    if action == "hook":
        event = read_hook_event(args)
        hook_event_name = args.target or str(event.get("hookEventName") or event.get("event") or "")
    launch_policy_active = bool(
        action == "hook"
        and os.environ.get("QWENDEX_MANAGER_LEDGER_ID")
        and os.environ.get("QWENDEX_MANAGER_SESSION_ID")
    )
    launch_agent_use = (
        str(os.environ.get("QWENDEX_EFFECTIVE_AGENT_USE") or "").strip()
        if launch_policy_active
        else ""
    )
    launch_policy_source = (
        str(os.environ.get("QWENDEX_AGENT_POLICY_SOURCE") or "").strip()
        if launch_policy_active
        else ""
    )
    launch_kaveman = (
        env_flag(os.environ.get("QWENDEX_KAVEMAN_ENABLED"))
        if launch_policy_active
        else None
    )
    agent_policy = resolve_agent_policy(
        config,
        cli_agent_use=launch_agent_use or getattr(args, "agent_use", ""),
        selected_manager_mode=selected_manager_mode_for_policy(config),
        kaveman_enabled=launch_kaveman,
        selector_source_override=launch_policy_source,
    )
    policy_transition: dict[str, Any] | None = None
    accepted_turn_policy: dict[str, Any] | None = None
    if action == "hook" and manager_session_state_path() is not None:
        try:
            with connect_state(config) as conn:
                projected_policy, policy_transition = session_turn_policy_projection(config, conn)
                if hook_event_name == "UserPromptSubmit" and not (
                    event.get("agent_id") or event.get("agent_type")
                ):
                    accepted_turn_policy = manager_session_accept_turn_policy(
                        config,
                        conn,
                        event=event,
                        agent_policy=projected_policy,
                    )
                    agent_policy = accepted_turn_policy or projected_policy
                else:
                    accepted_turn_policy = manager_session_active_turn_policy(
                        config,
                        conn,
                        event=event,
                    )
                    agent_policy = accepted_turn_policy or projected_policy
        except Exception as exc:
            agent_policy = {
                **agent_policy,
                "warnings": [
                    *list(agent_policy.get("warnings") or []),
                    (
                        "Per-launch session policy state is unavailable; continuing with the resolved advisory policy: "
                        f"{redact_text(str(exc) or exc.__class__.__name__)}"
                    ),
                ],
            }
    elif launch_policy_active:
        recorded_policy = manager_launch_policy_snapshot(config)
        if recorded_policy is None:
            agent_policy = {
                **agent_policy,
                "warnings": [
                    *list(agent_policy.get("warnings") or []),
                    "Manager launch policy snapshot is unavailable; continuing with the resolved advisory policy.",
                ],
            }
        else:
            agent_policy = recorded_policy
    else:
        try:
            with connect_state(config) as conn:
                local_enabled = current_local_enabled(config, conn)
        except Exception as exc:
            local_enabled = False
            agent_policy = {
                **agent_policy,
                "warnings": [
                    *list(agent_policy.get("warnings") or []),
                    (
                        "Local routing state is unavailable; continuing with local delegation disabled: "
                        f"{redact_text(str(exc) or exc.__class__.__name__)}"
                    ),
                ],
            }
        agent_policy = attach_local_routing_snapshot(
            agent_policy,
            config,
            enabled=local_enabled,
        )
    if agent_policy["errors"]:
        return stable_envelope(command="agent", status="blocked", summary="Invalid Qwendex agent policy.", errors=list(agent_policy["errors"]), data={"agent_policy": agent_policy})
    apply_agent_policy_env(agent_policy)
    if action == "hook":
        status, hook_result, extra = evaluate_agent_hook(
            config,
            event_name=hook_event_name,
            event=event,
            agent_policy=agent_policy,
        )
        if status == "blocked":
            performance_capture = {
                "enabled": performance_config(config)["capture"] == "metadata",
                "capture": performance_config(config)["capture"],
                "captured": False,
                "reason": "hook_blocked",
            }
        else:
            performance_capture = capture_performance_hook_event(
                config,
                event_name=hook_event_name,
                event=event,
            )
        data = {
            "hook_result": hook_result,
            "event": event,
            "agent_policy": agent_policy,
            "policy_transition": policy_transition,
            "accepted_turn_policy_hash": (
                str(accepted_turn_policy.get("policy_hash") or "")
                if accepted_turn_policy is not None
                else None
            ),
            "performance_capture": performance_capture,
            **extra,
        }
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
                    "hook commands with a static status-file override: "
                    f"{', '.join(status.get('status_file_override_events', [])) or 'none detected'}",
                    "managed hook runtime mismatch events: "
                    f"{', '.join(status.get('runtime_command_mismatch_events', [])) or 'none detected'}",
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
                written = install_managed_hook_config(
                    hook_config_path_for_codex_home(codex_home),
                    hook_payload,
                    force=bool(getattr(args, "force", False)),
                )
            except (OSError, ValueError) as exc:
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
        repo_root = canonical_manager_repo_root()
        reconciliation = reconcile_stale_manager_sessions(
            conn,
            stale_after_minutes=stale_after,
            now=now,
            repo_root=repo_root,
        )
        target = args.target or args.agent_id
        if action == "close":
            if not target:
                return stable_envelope(command="agent", status="blocked", summary="Agent close requires an agent id or all.", errors=["missing agent_id"])
            reason = args.reason or "operator_closed"
            close_timeout_ms = parse_timeout_ms(args.timeout, int(agent_policy["close_timeout_ms"]))
            if target == "all":
                rows = conn.execute(
                    "SELECT * FROM qwendex_agent_sessions WHERE status IN ('active', 'reserved', 'close_requested') AND repo_root = ?",
                    (repo_root,),
                ).fetchall()
                ids = [str(row["agent_id"]) for row in rows]
            else:
                row = conn.execute("SELECT * FROM qwendex_agent_sessions WHERE agent_id = ?", (target,)).fetchone()
                if row is None:
                    return stable_envelope(command="agent", status="blocked", summary=f"Agent session not found: {target}", errors=[target])
                if str(row["repo_root"] or "") != repo_root:
                    return stable_envelope(command="agent", status="blocked", summary="Agent session is legacy-unscoped or belongs to a different repository; claim it with manager assign before mutation.", errors=[target])
                ids = [target]
            closed: list[dict[str, Any]] = []
            for agent_id in ids:
                close_receipt = make_id("close-request")
                conn.execute(
                    """
                    UPDATE qwendex_agent_sessions
                    SET status = 'close_requested', heartbeat_at = ?, updated_at = ?,
                        stop_reason = ?, close_receipt = ?, completed_at = ''
                    WHERE agent_id = ? AND status NOT IN ('completed', 'blocked', 'failed', 'closed', 'tombstoned', 'waived')
                    """,
                    (now, now, reason, close_receipt, agent_id),
                )
                updated = conn.execute("SELECT * FROM qwendex_agent_sessions WHERE agent_id = ?", (agent_id,)).fetchone()
                session = row_to_agent_session(updated)
                if session:
                    closed.append(session)
            conn.commit()
            return stable_envelope(
                command="agent",
                status="pass",
                summary=f"Requested bounded native close for {len(closed)} Qwendex agent session{'s' if len(closed) != 1 else ''}; capacity remains held until terminal confirmation or tombstone.",
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
            if str(row["repo_root"] or "") != repo_root:
                return stable_envelope(command="agent", status="blocked", summary="Agent session is legacy-unscoped or belongs to a different repository; claim it with manager assign before mutation.", errors=[target])
            reason = args.reason or "operator_tombstoned"
            close_receipt = make_id("tombstone")
            updated = transition_agent_session(
                conn,
                agent_id=target,
                status="tombstoned",
                validation_status="fail",
                now=now,
                reason=reason,
                final_report_present=None,
                close_receipt=close_receipt,
            )
            conn.commit()
            return stable_envelope(
                command="agent",
                status="warning",
                summary=f"Tombstoned Qwendex agent session {target}.",
                data={"agent_session": updated, "agent_policy": agent_policy},
            )
        sessions, scope_sessions, ledger_sessions = load_manager_session_views(
            conn,
            limit=args.limit,
            repo_root=repo_root,
        )
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
                scope_sessions=scope_sessions,
                ledger_sessions=ledger_sessions,
                repo_root=repo_root,
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
            if str(row["repo_root"] or "") != repo_root:
                return stable_envelope(command="agent", status="blocked", summary="Agent session is legacy-unscoped or belongs to a different repository scope.", errors=[target])
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
    if override_errors := manager_override_errors(args):
        return stable_envelope(
            command="manager",
            status="blocked",
            summary="Qwendex manager override values are outside configured bounds.",
            errors=override_errors,
        )
    now = utc_now()
    repo_root = canonical_manager_repo_root(args.repo_root or None)
    with connect_state(config) as conn:
        mode = current_manager_mode(config, conn)
        selected_manager_mode = mode
        agent_policy = resolve_agent_policy(config, cli_agent_use=getattr(args, "agent_use", ""), selected_manager_mode=mode)
        if agent_policy["errors"]:
            return stable_envelope(command="manager", status="blocked", summary="Invalid Qwendex agent policy.", errors=list(agent_policy["errors"]), data={"agent_policy": agent_policy})
        if args.action == "launch-status":
            agent_policy = attach_local_routing_snapshot(
                agent_policy,
                config,
                enabled=current_local_enabled(config, conn),
            )
            launch_repo = canonical_manager_repo_root(args.repo_root or os.getcwd())
            health = manager_launch_health(
                config,
                pid=max(0, int(args.pid or 0)),
                repo_root=launch_repo,
                agent_policy=agent_policy,
            )
            return stable_envelope(
                command="manager",
                status="pass" if health["trusted"] else "blocked",
                summary=(
                    "Qwendex launch identity is trusted."
                    if health["trusted"]
                    else f"Qwendex launch identity is not trusted: {health['reason']}."
                ),
                next_actions=[] if health["trusted"] else [health["recovery_command"]],
                errors=[] if health["trusted"] else [health["reason"]],
                data=health,
            )
        if args.action not in {"mode"}:
            mode = policy_mode_for_manager(args, config, mode)
        stale_after = mode_stale_after_minutes(config, mode, args.stale_after_minutes)
        max_subagents = args.max_subagents or manager_mode_profile(config, mode)["max_subagents"]
        local_status = local_subagent_status(config, enabled=current_local_enabled(config, conn), env=os.environ, probe=True)
        kaveman_enabled = current_kaveman_enabled(config, conn)
        reconciliation = {"closed_count": 0, "closed": [], "skipped_writer_count": 0, "skipped_writers": [], "stale_after_minutes": max(stale_after, 5)}
        if args.action in {"kaveman", "local", "estimate", "status"}:
            reconciliation = reconcile_stale_manager_sessions(
                conn,
                stale_after_minutes=stale_after,
                now=now,
                repo_root=repo_root,
            )
        if args.action == "mode":
            previous_mode = mode
            if args.toggle:
                index = MANAGER_MODE_ORDER.index(mode) if mode in MANAGER_MODE_ORDER else 0
                mode = MANAGER_MODE_ORDER[(index + 1) % len(MANAGER_MODE_ORDER)]
                set_current_manager_control_setting(config, conn, "selected_mode", mode)
                conn.commit()
            elif args.cycle:
                index = MANAGER_MODE_ORDER.index(mode) if mode in MANAGER_MODE_ORDER else 0
                mode = MANAGER_MODE_ORDER[(index + 1) % len(MANAGER_MODE_ORDER)]
                set_current_manager_control_setting(config, conn, "selected_mode", mode)
                conn.commit()
            elif args.set:
                requested = normalize_manager_mode(args.set)
                if requested not in MANAGER_MODE_ORDER:
                    return stable_envelope(command="manager", status="blocked", summary=f"Unknown manager mode: {args.set}", errors=[args.set])
                mode = requested
                set_current_manager_control_setting(config, conn, "selected_mode", mode)
                conn.commit()
            requested_agent_policy, effective_agent_policy, policy_transition, accepted_turn = manager_session_policy_surface(
                config,
                conn,
                selected_manager_mode=mode,
                cli_agent_use=getattr(args, "agent_use", ""),
            )
            if requested_agent_policy["errors"]:
                return stable_envelope(command="manager", status="blocked", summary="Invalid Qwendex agent policy.", errors=list(requested_agent_policy["errors"]), data={"agent_policy": requested_agent_policy})
            stale_after = mode_stale_after_minutes(config, mode, args.stale_after_minutes)
            reconciliation = reconcile_stale_manager_sessions(
                conn,
                stale_after_minutes=stale_after,
                now=now,
                repo_root=repo_root,
            )
            sessions, scope_sessions, ledger_sessions = load_manager_session_views(
                conn,
                limit=args.limit,
                repo_root=repo_root,
            )
            data = manager_mode_payload(
                config,
                mode=mode,
                local_status=local_status,
                max_subagents=args.max_subagents or manager_mode_profile(config, mode)["max_subagents"],
                stale_after_minutes=mode_stale_after_minutes(config, mode, args.stale_after_minutes),
                kaveman_enabled=kaveman_enabled,
                sessions=sessions,
                agent_policy=requested_agent_policy,
                scope_sessions=scope_sessions,
                ledger_sessions=ledger_sessions,
                repo_root=repo_root,
            )
            data = apply_manager_session_policy_surface(
                data,
                requested_policy=requested_agent_policy,
                effective_policy=effective_agent_policy,
                transition=policy_transition,
                accepted_turn=accepted_turn,
            )
            data["state_db"] = str(state_db_path(config))
            status_sync = sync_codex_status_or_restore_setting(
                config,
                conn,
                setting_key="selected_mode",
                previous_value=previous_mode,
            )
            data["codex_status_file"] = status_sync["status_file"]
            data["status_sync"] = status_sync
            data["stale_reconciliation"] = reconciliation
            if status_sync["error"]:
                return stable_envelope(
                    command="manager",
                    status="blocked",
                    summary="Manager mode change was rolled back because the Codex status file could not be synchronized.",
                    errors=[status_sync["error"]],
                    data=data,
                )
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
                next_actions=data["next_actions"],
                data=data,
            )
        if args.action == "kaveman":
            previous_kaveman_enabled = kaveman_enabled
            enabled = kaveman_enabled
            if args.toggle:
                enabled = not enabled
            elif args.set:
                parsed = normalize_local_toggle(args.set)
                if parsed is None:
                    return stable_envelope(command="manager", status="blocked", summary=f"Unknown Kaveman toggle: {args.set}", errors=[args.set])
                enabled = parsed
            set_current_manager_control_setting(config, conn, "kaveman_enabled", enabled)
            conn.commit()
            kaveman_enabled = enabled
            requested_agent_policy, effective_agent_policy, policy_transition, accepted_turn = manager_session_policy_surface(
                config,
                conn,
                selected_manager_mode=mode,
                cli_agent_use=getattr(args, "agent_use", ""),
                kaveman_enabled=kaveman_enabled,
            )
            if requested_agent_policy["errors"]:
                return stable_envelope(command="manager", status="blocked", summary="Invalid Qwendex agent policy.", errors=list(requested_agent_policy["errors"]), data={"agent_policy": requested_agent_policy})
            sessions, scope_sessions, ledger_sessions = load_manager_session_views(
                conn,
                limit=args.limit,
                repo_root=repo_root,
            )
            data = manager_mode_payload(
                config,
                mode=mode,
                local_status=local_status,
                max_subagents=max_subagents,
                stale_after_minutes=stale_after,
                kaveman_enabled=kaveman_enabled,
                sessions=sessions,
                agent_policy=requested_agent_policy,
                scope_sessions=scope_sessions,
                ledger_sessions=ledger_sessions,
                repo_root=repo_root,
            )
            data = apply_manager_session_policy_surface(
                data,
                requested_policy=requested_agent_policy,
                effective_policy=effective_agent_policy,
                transition=policy_transition,
                accepted_turn=accepted_turn,
            )
            data["state_db"] = str(state_db_path(config))
            status_sync = sync_codex_status_or_restore_setting(
                config,
                conn,
                setting_key="kaveman_enabled",
                previous_value=previous_kaveman_enabled,
            )
            data["codex_status_file"] = status_sync["status_file"]
            data["status_sync"] = status_sync
            data["stale_reconciliation"] = reconciliation
            if status_sync["error"]:
                return stable_envelope(
                    command="manager",
                    status="blocked",
                    summary="Kaveman change was rolled back because the Codex status file could not be synchronized.",
                    errors=[status_sync["error"]],
                    data=data,
                )
            return stable_envelope(
                command="manager",
                status="pass",
                summary=f"Qwendex Kaveman mode is {'enabled' if enabled else 'disabled'}.",
                next_actions=data["next_actions"],
                data=data,
            )
        if args.action == "local":
            enabled = current_local_enabled(config, conn)
            previous_local_enabled = enabled
            if args.toggle:
                enabled = not enabled
            elif args.set:
                parsed = normalize_local_toggle(args.set)
                if parsed is None:
                    return stable_envelope(command="manager", status="blocked", summary=f"Unknown local toggle: {args.set}", errors=[args.set])
                enabled = parsed
            set_current_manager_control_setting(config, conn, "local_subagents_enabled", enabled)
            conn.commit()
            local_status = local_subagent_status(config, enabled=enabled, env=os.environ, probe=True)
            requested_agent_policy, effective_agent_policy, policy_transition, accepted_turn = manager_session_policy_surface(
                config,
                conn,
                selected_manager_mode=mode,
                cli_agent_use=getattr(args, "agent_use", ""),
                kaveman_enabled=kaveman_enabled,
            )
            if requested_agent_policy["errors"]:
                return stable_envelope(command="manager", status="blocked", summary="Invalid Qwendex agent policy.", errors=list(requested_agent_policy["errors"]), data={"agent_policy": requested_agent_policy})
            sessions, scope_sessions, ledger_sessions = load_manager_session_views(
                conn,
                limit=args.limit,
                repo_root=repo_root,
            )
            data = manager_mode_payload(
                config,
                mode=mode,
                local_status=local_status,
                max_subagents=max_subagents,
                stale_after_minutes=stale_after,
                kaveman_enabled=kaveman_enabled,
                sessions=sessions,
                agent_policy=requested_agent_policy,
                scope_sessions=scope_sessions,
                ledger_sessions=ledger_sessions,
                repo_root=repo_root,
            )
            data = apply_manager_session_policy_surface(
                data,
                requested_policy=requested_agent_policy,
                effective_policy=effective_agent_policy,
                transition=policy_transition,
                accepted_turn=accepted_turn,
            )
            data["state_db"] = str(state_db_path(config))
            status_sync = sync_codex_status_or_restore_setting(
                config,
                conn,
                setting_key="local_subagents_enabled",
                previous_value=previous_local_enabled,
            )
            data["codex_status_file"] = status_sync["status_file"]
            data["status_sync"] = status_sync
            data["stale_reconciliation"] = reconciliation
            if status_sync["error"]:
                return stable_envelope(
                    command="manager",
                    status="blocked",
                    summary="Local toggle was rolled back because the Codex status file could not be synchronized.",
                    errors=[status_sync["error"]],
                    data=data,
                )
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
                selected_mode=normalize_manager_mode(getattr(args, "mode", "")) or selected_manager_mode,
            )
            return stable_envelope(
                command="manager",
                status="pass" if payload["ok"] else "warning",
                summary=(
                    "Qwendex Manager preflight is ready."
                    if payload["ok"]
                    else "Qwendex Manager bookkeeping is unavailable; Qdex may continue without it."
                ),
                artifacts=list(payload.get("receipt_paths") or []) if not args.dry_run else [],
                next_actions=[],
                errors=[],
                data=payload,
            )
        if args.action == "decision":
            decision = latest_manager_decision(
                conn,
                repo_root=repo_root,
                ledger_id=args.agent_id,
                session_id=args.task_id,
            )
            if decision is None:
                return stable_envelope(command="manager", status="blocked", summary="Manager decision ledger record not found.", errors=[args.agent_id or args.task_id or "latest"])
            return stable_envelope(
                command="manager",
                status="pass",
                summary=f"Loaded manager decision {decision['ledger_id']}.",
                data={"manager_decision": decision},
            )
        if args.action == "reconcile":
            _, reconcile_sessions, ledger_sessions = load_manager_session_views(
                conn,
                limit=args.limit,
                repo_root=repo_root,
            )
            validation_reconcile = classify_manager_validation_sessions(
                reconcile_sessions,
                stale_after_minutes=stale_after,
            )
            ledger_validation_debt = classify_manager_validation_sessions(
                ledger_sessions,
                stale_after_minutes=stale_after,
            )
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
                data={
                    "repo_root": repo_root,
                    "validation_reconciliation": validation_reconcile,
                    "ledger_validation_debt": ledger_validation_debt,
                    "legacy_unscoped_count": sum(
                        1 for session in ledger_sessions if not session.get("repo_root")
                    ),
                },
            )
        if args.action == "status":
            requested_agent_policy, effective_agent_policy, policy_transition, accepted_turn = manager_session_policy_surface(
                config,
                conn,
                selected_manager_mode=mode,
                cli_agent_use=getattr(args, "agent_use", ""),
                kaveman_enabled=kaveman_enabled,
            )
            if requested_agent_policy["errors"]:
                return stable_envelope(command="manager", status="blocked", summary="Invalid Qwendex agent policy.", errors=list(requested_agent_policy["errors"]), data={"agent_policy": requested_agent_policy})
            sessions, scope_sessions, ledger_sessions = load_manager_session_views(
                conn,
                limit=args.limit,
                repo_root=repo_root,
            )
            data = manager_mode_payload(
                config,
                mode=mode,
                local_status=local_status,
                max_subagents=max_subagents,
                stale_after_minutes=stale_after,
                kaveman_enabled=kaveman_enabled,
                sessions=sessions,
                agent_policy=requested_agent_policy,
                scope_sessions=scope_sessions,
                ledger_sessions=ledger_sessions,
                repo_root=repo_root,
            )
            data = apply_manager_session_policy_surface(
                data,
                requested_policy=requested_agent_policy,
                effective_policy=effective_agent_policy,
                transition=policy_transition,
                accepted_turn=accepted_turn,
            )
            data["agent_sessions"] = sessions
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
        if args.action == "waive":
            if not args.task_id or not args.lane or not args.reason:
                return stable_envelope(
                    command="manager",
                    status="blocked",
                    summary="Manager waive requires --task-id, --lane, and --reason.",
                    errors=["missing task_id, lane, or reason"],
                )
            decision_rows = conn.execute(
                """
                SELECT * FROM qwendex_manager_decisions
                WHERE repo_root = ? AND agent_task_id = ?
                  AND final_status IN ('preflight_ready', 'validation_pending')
                ORDER BY timestamp_updated DESC
                LIMIT 2
                """,
                (repo_root, args.task_id),
            ).fetchall()
            if len(decision_rows) != 1:
                return stable_envelope(
                    command="manager",
                    status="blocked",
                    summary="Manager waiver must resolve exactly one active turn decision.",
                    errors=["decision_not_found" if not decision_rows else "decision_ambiguous"],
                    data={"candidate_count": len(decision_rows)},
                )
            decision = row_to_manager_decision(decision_rows[0]) or {}
            plan = decision.get("agent_plan")
            plan = plan if isinstance(plan, Mapping) else {}
            lane_key = args.lane.strip().lower()
            planned = [
                dict(item)
                for item in list(plan.get("required_lanes") or [])
                if isinstance(item, Mapping)
                and str(item.get("lane") or "").strip().lower() == lane_key
            ]
            if len(planned) != 1:
                return stable_envelope(
                    command="manager",
                    status="blocked",
                    summary="Only one exact required planned lane can be waived.",
                    errors=[args.lane],
                    data={"required_lanes": list(plan.get("required_lanes") or [])},
                )
            existing_waiver_row = conn.execute(
                """
                SELECT * FROM qwendex_agent_sessions
                WHERE repo_root = ? AND task_id = ? AND lower(lane) = ?
                  AND status = 'waived'
                ORDER BY completed_at DESC
                LIMIT 1
                """,
                (repo_root, args.task_id, lane_key),
            ).fetchone()
            if existing_waiver_row is not None:
                existing_waiver = row_to_agent_session(existing_waiver_row) or {}
                return stable_envelope(
                    command="manager",
                    status="warning",
                    summary=f"Required lane {args.lane} already has a visible waiver.",
                    data={"agent_session": existing_waiver, "idempotent_reuse": True},
                )
            now = utc_now()
            waiver_id = make_id("waiver")
            receipt_id = make_id("waiver-receipt")
            assignment = f"Waiver for required lane {args.lane}: {args.reason}"
            context_packet = {
                "required": True,
                "planned_lane": args.lane,
                "planned_profile": str(planned[0].get("profile") or ""),
                "waiver_reason": args.reason,
                "remaining_risk": args.reason,
                "launch_ledger_id": str(decision.get("launch_ledger_id") or decision.get("ledger_id") or ""),
            }
            conn.execute(
                """
                INSERT INTO qwendex_agent_sessions
                (agent_id, lane, task_id, owner, write_surface, stop_condition,
                 artifacts_json, status, heartbeat_at, created_at, updated_at,
                 stop_reason, close_receipt, context_packet_json, routing_json,
                 validation_status, repo_root, session_id, turn_id, assignment,
                 policy_hash, origin, final_report_present, completed_at, runtime_generation)
                VALUES (?, ?, ?, 'root', 'read-only', 'explicit root waiver', '[]',
                        'waived', ?, ?, ?, ?, ?, ?, '{}', 'waived', ?, ?, ?, ?, ?,
                        'root_waiver', 0, ?, ?)
                """,
                (
                    waiver_id,
                    args.lane,
                    args.task_id,
                    now,
                    now,
                    now,
                    args.reason,
                    receipt_id,
                    json_dumps(context_packet),
                    repo_root,
                    str(decision.get("session_id") or ""),
                    str(decision.get("turn_id") or ""),
                    assignment,
                    str(decision.get("policy_hash") or ""),
                    now,
                    str(decision.get("runtime_generation") or os.environ.get("QWENDEX_RUNTIME_GENERATION_ID") or ""),
                ),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM qwendex_agent_sessions WHERE agent_id = ?",
                (waiver_id,),
            ).fetchone()
            return stable_envelope(
                command="manager",
                status="warning",
                summary=f"Recorded a legacy Manager lane waiver for {args.lane}; the remaining risk is advisory.",
                data={"agent_session": row_to_agent_session(row), "manager_decision": decision},
            )
        if args.action == "assign":
            if not args.agent_id or not args.lane:
                return stable_envelope(command="manager", status="blocked", summary="Manager assign requires --agent-id and --lane.", errors=["missing agent_id or lane"])
            repo_root = canonical_manager_repo_root(getattr(args, "repo_root", "") or None)
            if busy_error := begin_immediate(conn):
                return stable_envelope(
                    command="manager",
                    status="blocked",
                    summary="Qwendex manager state remained busy after the bounded assignment wait.",
                    errors=[busy_error],
                    data={"repo_root": repo_root, "busy_timeout_ms": STATE_BUSY_TIMEOUT_MS},
                )
            existing = conn.execute(
                "SELECT * FROM qwendex_agent_sessions WHERE agent_id = ?",
                (args.agent_id,),
            ).fetchone()
            if existing is not None and str(existing["repo_root"] or "") not in {"", repo_root}:
                return stable_envelope(
                    command="manager",
                    status="blocked",
                    summary="Agent id already belongs to a different repository scope.",
                    errors=[args.agent_id],
                    data={"requested_repo_root": repo_root, "existing_repo_root": existing["repo_root"]},
                )
            if existing is not None and str(existing["status"] or "") in AGENT_TERMINAL_STATUSES:
                conn.rollback()
                return stable_envelope(
                    command="manager",
                    status="blocked",
                    summary="Terminal agent ids cannot be reopened; create a new planned worker identity.",
                    errors=[args.agent_id],
                    data={"existing_status": str(existing["status"] or "")},
                )
            if existing is not None and str(existing["status"] or "") != "active":
                conn.rollback()
                return stable_envelope(
                    command="manager",
                    status="blocked",
                    summary="Reserved or close-requested agent ids cannot be reassigned.",
                    errors=[args.agent_id],
                    data={"existing_status": str(existing["status"] or "")},
                )
            decision: dict[str, Any] | None = None
            planned_assignment: dict[str, Any] | None = None
            assignment_advisories: list[str] = []
            if args.task_id:
                decision_rows = conn.execute(
                    """
                    SELECT * FROM qwendex_manager_decisions
                    WHERE repo_root = ?
                      AND (agent_task_id = ? OR (agent_task_id = '' AND session_id = ?))
                      AND final_status IN ('preflight_ready', 'validation_pending')
                    ORDER BY timestamp_updated DESC
                    LIMIT 2
                    """,
                    (repo_root, args.task_id, args.task_id),
                ).fetchall()
                if len(decision_rows) > 1:
                    assignment_advisories.append(
                        "task identity is ambiguous across active Manager turns; recording an unattached worker session"
                    )
                    decision_rows = []
                if not decision_rows and os.environ.get("QWENDEX_MANAGER_LEDGER_ID"):
                    assignment_advisories.append(
                        "assignment does not match the active Manager turn; recording it as advisory lifecycle data"
                    )
                decision = row_to_manager_decision(decision_rows[0]) if decision_rows else None
                plan = (decision or {}).get("agent_plan")
                if isinstance(plan, Mapping):
                    planned = [
                        dict(item)
                        for item in list(plan.get("assignments") or [])
                        if isinstance(item, Mapping)
                    ]
                    lane_matches = [
                        item for item in planned
                        if str(item.get("lane") or "").strip().lower() == args.lane.strip().lower()
                    ]
                    if len(lane_matches) == 1:
                        planned_assignment = lane_matches[0]
                        expected_agent_id = str(planned_assignment.get("agent_id") or "")
                        if expected_agent_id and args.agent_id != expected_agent_id:
                            assignment_advisories.append(
                                f"worker id differs from the suggested plan id {expected_agent_id}"
                            )
                    elif planned and str((decision or {}).get("selected_route") or "") == "manager_subagents":
                        assignment_advisories.append(
                            "worker lane differs from the advisory Manager plan"
                        )
            if args.task_id:
                active_count = int(
                    conn.execute(
                        """
                        SELECT COUNT(*) AS count FROM qwendex_agent_sessions
                        WHERE status NOT IN ('completed', 'blocked', 'failed', 'closed', 'tombstoned', 'waived')
                          AND repo_root = ? AND task_id = ?
                        """,
                        (repo_root, args.task_id),
                    ).fetchone()["count"]
                )
            else:
                active_count = int(
                    conn.execute(
                        """
                        SELECT COUNT(*) AS count FROM qwendex_agent_sessions
                        WHERE status NOT IN ('completed', 'blocked', 'failed', 'closed', 'tombstoned', 'waived')
                          AND repo_root = ?
                        """,
                        (repo_root,),
                    ).fetchone()["count"]
                )
            consumes_slot = (
                existing is None
                or str(existing["status"] or "") != "active"
                or str(existing["repo_root"] or "") != repo_root
            )
            decision_policy = (decision or {}).get("policy_snapshot")
            decision_policy = decision_policy if isinstance(decision_policy, Mapping) else agent_policy
            decision_plan = (decision or {}).get("agent_plan")
            decision_plan = decision_plan if isinstance(decision_plan, Mapping) else {}
            assignment_cap = int(
                decision_plan.get("max_workers")
                or decision_policy.get("max_workers")
                or decision_policy.get("max_threads")
                or max_subagents
            )
            if consumes_slot and active_count >= assignment_cap:
                assignment_advisories.append(
                    f"recorded workers ({active_count}) already meet the suggested capacity ({assignment_cap})"
                )
            artifacts = args.artifact or []
            task_class = args.task_class or infer_task_class(args.lane)
            risk = args.risk or ("high" if task_class in {"security", "architecture", "release acceptance"} else "medium")
            routing = lane_model_reasoning(config, task_class=task_class, lane=args.lane, risk=risk, local_status=local_status)
            required = bool(getattr(args, "required", False))
            if getattr(args, "optional", False):
                required = False
            if planned_assignment is not None:
                required = bool(planned_assignment.get("required"))
            assignment_text = str(
                (planned_assignment or {}).get("assignment")
                or args.objective
                or args.stop_condition
            )
            session_id = str((decision or {}).get("session_id") or "")
            turn_id = str((decision or {}).get("turn_id") or "")
            policy_hash = str((decision or {}).get("policy_hash") or agent_policy.get("policy_hash") or "")
            origin = str(existing["origin"] or "") if existing is not None else "qwendex_manual"
            if not origin:
                origin = "qwendex_manual"
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
                "spawn_instruction": spawn_instruction(args.agent_id, routing),
                "review_requirement": args.review_requirement,
                "risk": risk,
                "planned_agent_id": str((planned_assignment or {}).get("agent_id") or ""),
                "planned_lane": str((planned_assignment or {}).get("lane") or args.lane),
                "planned_profile": str((planned_assignment or {}).get("profile") or args.owner),
                "launch_ledger_id": str((decision or {}).get("launch_ledger_id") or (decision or {}).get("ledger_id") or ""),
            }
            conn.execute(
                """
                INSERT INTO qwendex_agent_sessions
                (agent_id, lane, task_id, owner, write_surface, stop_condition, artifacts_json, status, heartbeat_at, created_at, updated_at, stop_reason, close_receipt, context_packet_json, routing_json, validation_status, repo_root,
                 session_id, turn_id, assignment, policy_hash, origin, final_report_present, completed_at, runtime_generation)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, '', '', ?, ?, 'pending', ?, ?, ?, ?, ?, ?, 0, '', ?)
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
                  validation_status='pending',
                  repo_root=excluded.repo_root,
                  session_id=excluded.session_id,
                  turn_id=excluded.turn_id,
                  assignment=excluded.assignment,
                  policy_hash=excluded.policy_hash,
                  origin=CASE WHEN qwendex_agent_sessions.origin = '' THEN excluded.origin ELSE qwendex_agent_sessions.origin END,
                  final_report_present=0,
                  completed_at='',
                  runtime_generation=CASE
                    WHEN qwendex_agent_sessions.runtime_generation = '' THEN excluded.runtime_generation
                    ELSE qwendex_agent_sessions.runtime_generation
                  END
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
                    repo_root,
                    session_id,
                    turn_id,
                    assignment_text,
                    policy_hash,
                    origin,
                    str((decision or {}).get("runtime_generation") or os.environ.get("QWENDEX_RUNTIME_GENERATION_ID") or ""),
                ),
            )
            conn.execute(
                """
                UPDATE qwendex_agent_file_locks
                SET repo_root = ?
                WHERE agent_id = ? AND repo_root = ''
                """,
                (repo_root, args.agent_id),
            )
            if args.task_id:
                conn.execute(
                    """
                    UPDATE qwendex_manager_decisions
                    SET subagents_used = 1, timestamp_updated = ?
                    WHERE (agent_task_id = ? OR (agent_task_id = '' AND session_id = ?))
                      AND repo_root = ?
                      AND final_status IN ('preflight_ready', 'validation_pending')
                    """,
                    (now, args.task_id, args.task_id, repo_root),
                )
            conn.commit()
            row = conn.execute("SELECT * FROM qwendex_agent_sessions WHERE agent_id = ?", (args.agent_id,)).fetchone()
            return stable_envelope(
                command="manager",
                status="warning" if assignment_advisories else "pass",
                summary=(
                    f"Recorded agent session {args.agent_id} in lane {args.lane} with advisory differences."
                    if assignment_advisories
                    else f"Recorded agent session {args.agent_id} in lane {args.lane}."
                ),
                next_actions=["Review subagent output before treating it as authoritative."],
                data={
                    "agent_session": row_to_agent_session(row),
                    "advisories": assignment_advisories,
                },
            )
        if args.action == "heartbeat":
            row = conn.execute("SELECT * FROM qwendex_agent_sessions WHERE agent_id = ?", (args.agent_id,)).fetchone()
            if row is None:
                return stable_envelope(command="manager", status="blocked", summary=f"Agent session not found: {args.agent_id}", errors=[args.agent_id])
            if str(row["repo_root"] or "") != repo_root:
                return stable_envelope(command="manager", status="blocked", summary="Agent session is legacy-unscoped or belongs to a different repository; claim it with manager assign before mutation.", errors=[args.agent_id])
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
            if str(row["repo_root"] or "") != repo_root:
                return stable_envelope(command="manager", status="blocked", summary="Agent session is legacy-unscoped or belongs to a different repository; claim it with manager assign before mutation.", errors=[args.agent_id])
            close_receipt = make_id("close-request")
            reason = args.reason or "operator_closed"
            conn.execute(
                """
                UPDATE qwendex_agent_sessions
                SET status = 'close_requested', heartbeat_at = ?, updated_at = ?,
                    stop_reason = ?, close_receipt = ?, completed_at = ''
                WHERE agent_id = ?
                  AND status NOT IN ('completed', 'blocked', 'failed', 'closed', 'tombstoned', 'waived')
                """,
                (now, now, reason, close_receipt, args.agent_id),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM qwendex_agent_sessions WHERE agent_id = ?", (args.agent_id,)).fetchone()
            return stable_envelope(
                command="manager",
                status="pass",
                summary=f"Requested bounded native close for agent session {args.agent_id}; capacity remains held until terminal confirmation or tombstone.",
                data={"agent_session": row_to_agent_session(row)},
            )
        if args.action == "close-stale":
            reconciliation = reconcile_stale_manager_sessions(
                conn,
                stale_after_minutes=stale_after,
                now=now,
                repo_root=repo_root,
            )
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
            repair = repair_manager_sessions(
                conn,
                stale_after_minutes=stale_after,
                now=now,
                safe=True,
                repo_root=repo_root,
            )
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


def latest_snapshot(conn: sqlite3.Connection, task_id: str, repo_root: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT * FROM qwendex_context_snapshots
        WHERE task_id = ? AND repo_root = ?
        ORDER BY created_at DESC LIMIT 1
        """,
        (task_id, repo_root),
    ).fetchone()
    return row_to_context_snapshot(row)


def command_context(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    now = utc_now()
    repo_root = canonical_manager_repo_root()
    with connect_state(config) as conn:
        if args.action == "snapshot":
            snapshot_id = make_id("ctx")
            conn.execute(
                """
                INSERT INTO qwendex_context_snapshots
                (snapshot_id, task_id, objective, decisions_json, open_files_json, evidence_refs_json, blocked_items_json, next_actions_json, budget, created_at, repo_root)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    repo_root,
                ),
            )
            conn.commit()
            snapshot = latest_snapshot(conn, args.task_id, repo_root)
            return stable_envelope(
                command="context",
                status="pass",
                summary=f"Created context snapshot {snapshot_id}.",
                data={"snapshot": snapshot},
            )
        snapshot = latest_snapshot(conn, args.task_id, repo_root)
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
                """
                SELECT * FROM qwendex_agent_sessions
                WHERE task_id = ? AND repo_root = ?
                ORDER BY updated_at DESC
                """,
                (args.task_id, repo_root),
            ).fetchall()
            agent_sessions = [session for row in agent_rows if (session := row_to_agent_session(row))]
            active_locks = active_file_locks(conn, repo_root=repo_root)
            manager_decision = latest_manager_decision(
                conn,
                repo_root=repo_root,
                task_id=args.task_id,
            )
            plan = {
                "task_id": args.task_id,
                "repo_root": repo_root,
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
                "manager_decision": manager_decision,
                "raw_output_policy": "preserve raw child output in artifact paths; inject compact reports into root context",
            }
            return stable_envelope(command="context", status="pass", summary=f"Built compact plan for {args.task_id}.", data={"compact_plan": plan})
        if args.action == "pack":
            evidence_rows = conn.execute(
                """
                SELECT * FROM qwendex_evidence
                WHERE task_id = ? AND repo_root = ?
                ORDER BY created_at DESC LIMIT ?
                """,
                (args.task_id, repo_root, args.limit),
            ).fetchall()
            handoff_rows = conn.execute(
                """
                SELECT * FROM qwendex_handoffs
                WHERE task_id = ? AND repo_root = ?
                ORDER BY created_at DESC LIMIT ?
                """,
                (args.task_id, repo_root, args.limit),
            ).fetchall()
            agent_rows = conn.execute(
                """
                SELECT * FROM qwendex_agent_sessions
                WHERE task_id = ? AND repo_root = ?
                ORDER BY updated_at DESC LIMIT ?
                """,
                (args.task_id, repo_root, args.limit),
            ).fetchall()
            agent_sessions = [session for row in agent_rows if (session := row_to_agent_session(row))]
            manager_decision = latest_manager_decision(
                conn,
                repo_root=repo_root,
                task_id=args.task_id,
            )
            return stable_envelope(
                command="context",
                status="pass",
                summary=f"Built context pack for {args.task_id}.",
                data={
                    "snapshot": snapshot,
                    "repo_root": repo_root,
                    "evidence": [row_to_evidence(row) for row in evidence_rows],
                    "handoffs": [row_to_handoff(row) for row in handoff_rows],
                    "manager_decision": manager_decision,
                    "agent_outcomes": agent_outcomes_for_sessions(agent_sessions),
                    "agent_sessions": agent_sessions,
                    "file_locks": active_file_locks(conn, repo_root=repo_root),
                },
            )
    return stable_envelope(command="context", status="blocked", summary=f"Unknown context action: {args.action}", errors=[args.action])


def command_handoff(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    now = utc_now()
    repo_root = canonical_manager_repo_root()
    with connect_state(config) as conn:
        if args.action == "create":
            handoff_id = args.handoff_id or make_id("handoff")
            storage_id = scoped_storage_id("handoff", repo_root, handoff_id)
            try:
                conn.execute(
                    """
                    INSERT INTO qwendex_handoffs
                    (handoff_id, task_id, status, summary, evidence_refs_json, next_actions_json,
                     created_at, repo_root, public_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        storage_id,
                        args.task_id,
                        args.status,
                        args.summary,
                        json_dumps(args.evidence or []),
                        json_dumps(args.next_action or []),
                        now,
                        repo_root,
                        handoff_id,
                    ),
                )
            except sqlite3.IntegrityError:
                conn.rollback()
                existing = conn.execute(
                    "SELECT 1 FROM qwendex_handoffs WHERE public_id = ? AND repo_root = ?",
                    (handoff_id, repo_root),
                ).fetchone()
                if existing is not None:
                    return stable_envelope(
                        command="handoff",
                        status="blocked",
                        summary=f"Handoff id already exists in this repository: {handoff_id}.",
                        errors=[f"duplicate handoff_id: {handoff_id}"],
                        data={"handoff_id": handoff_id, "repo_root": repo_root},
                    )
                return stable_envelope(
                    command="handoff",
                    status="blocked",
                    summary="Handoff storage key collision prevented creation.",
                    errors=[handoff_id],
                    data={"handoff_id": handoff_id, "repo_root": repo_root},
                )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM qwendex_handoffs WHERE public_id = ? AND repo_root = ?",
                (handoff_id, repo_root),
            ).fetchone()
            return stable_envelope(command="handoff", status="pass", summary=f"Created handoff {handoff_id}.", data={"handoff": row_to_handoff(row)})
        if args.handoff_id:
            row = conn.execute(
                "SELECT * FROM qwendex_handoffs WHERE public_id = ? AND repo_root = ?",
                (args.handoff_id, repo_root),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT * FROM qwendex_handoffs
                WHERE task_id = ? AND repo_root = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (args.task_id, repo_root),
            ).fetchone()
        if row is None:
            target = args.handoff_id or args.task_id
            return stable_envelope(command="handoff", status="blocked", summary=f"Handoff not found: {target}", errors=[target])
        return stable_envelope(command="handoff", status="pass", summary="Loaded Qwendex handoff.", data={"handoff": row_to_handoff(row)})


def command_evidence(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    now = utc_now()
    repo_root = canonical_manager_repo_root()
    with connect_state(config) as conn:
        if args.action == "add":
            evidence_id = args.evidence_id or make_id("ev")
            storage_id = scoped_storage_id("evidence", repo_root, evidence_id)
            path = Path(args.path).expanduser()
            digest = args.sha256 or (sha256_file(path) if path.exists() and path.is_file() else hashlib.sha256(str(path).encode("utf-8")).hexdigest())
            try:
                conn.execute(
                    """
                    INSERT INTO qwendex_evidence
                    (evidence_id, task_id, claim, path, sha256, kind, created_at, repo_root, public_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (storage_id, args.task_id, args.claim, str(path), digest, args.kind, now, repo_root, evidence_id),
                )
            except sqlite3.IntegrityError:
                conn.rollback()
                existing = conn.execute(
                    "SELECT 1 FROM qwendex_evidence WHERE public_id = ? AND repo_root = ?",
                    (evidence_id, repo_root),
                ).fetchone()
                if existing is not None:
                    return stable_envelope(
                        command="evidence",
                        status="blocked",
                        summary=f"Evidence id already exists in this repository: {evidence_id}.",
                        errors=[f"duplicate evidence_id: {evidence_id}"],
                        data={"evidence_id": evidence_id, "repo_root": repo_root},
                    )
                return stable_envelope(
                    command="evidence",
                    status="blocked",
                    summary="Evidence storage key collision prevented creation.",
                    errors=[evidence_id],
                    data={"evidence_id": evidence_id, "repo_root": repo_root},
                )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM qwendex_evidence WHERE public_id = ? AND repo_root = ?",
                (evidence_id, repo_root),
            ).fetchone()
            return stable_envelope(command="evidence", status="pass", summary=f"Added evidence {evidence_id}.", data={"evidence": row_to_evidence(row)})
        params: list[Any] = []
        where: list[str] = ["repo_root = ?"]
        params.append(repo_root)
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
    if override_errors := manager_override_errors(args):
        return stable_envelope(
            command="manager",
            status="blocked",
            summary="Qwendex manager override values are outside configured bounds.",
            errors=override_errors,
        )
    repo_root = canonical_manager_repo_root(args.repo_root or None)
    with connect_state(config) as conn:
        mode = current_manager_mode(config, conn, explicit=args.mode)
        agent_policy = resolve_agent_policy(config, cli_agent_use=getattr(args, "agent_use", ""), selected_manager_mode=mode)
        if agent_policy["errors"]:
            return stable_envelope(command="manager", status="blocked", summary="Invalid Qwendex agent policy.", errors=list(agent_policy["errors"]), data={"agent_policy": agent_policy})
        mode = policy_mode_for_manager(args, config, mode)
        local_status = local_subagent_status(config, enabled=current_local_enabled(config, conn), env=os.environ, probe=True)
        kaveman_enabled = current_kaveman_enabled(config, conn)
        stale_after = mode_stale_after_minutes(config, mode, args.stale_after_minutes)
        reconciliation = reconcile_stale_manager_sessions(
            conn,
            stale_after_minutes=stale_after,
            now=utc_now(),
            repo_root=repo_root,
        )
        sessions, scope_sessions, ledger_sessions = load_manager_session_views(
            conn,
            limit=args.limit,
            repo_root=repo_root,
        )
    profile = manager_mode_profile(config, mode)
    max_subagents = args.max_subagents or profile["max_subagents"]
    errors: list[str] = []
    minimum_capacity = 0 if normalize_manager_mode(mode) == "off" else 1
    if not isinstance(max_subagents, int) or max_subagents < minimum_capacity or max_subagents > MANAGER_MAX_SUBAGENTS_LIMIT:
        errors.append(
            f"max_subagents must be between {minimum_capacity} and {MANAGER_MAX_SUBAGENTS_LIMIT}: {max_subagents}"
        )
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
                "allowed": {"max_subagents": [minimum_capacity, MANAGER_MAX_SUBAGENTS_LIMIT], "stale_after_minutes": [5, 240]},
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
        sessions=sessions,
        agent_policy=agent_policy,
        scope_sessions=scope_sessions,
        ledger_sessions=ledger_sessions,
        repo_root=repo_root,
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


def command_runtime(args: argparse.Namespace) -> dict[str, Any]:
    module = script_module("qwendex_runtime")
    return module.command(args)


def command_manager_accept(args: argparse.Namespace) -> dict[str, Any]:
    module = script_module("qwendex_manager_acceptance")
    return module.command(args)


def command_manager_evidence(args: argparse.Namespace) -> dict[str, Any]:
    module = script_module("qwendex_manager_acceptance")
    return module.command_evidence(args)


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
    exec_parser.add_argument("--task-class", choices=EXEC_TASK_CLASS_CHOICES, default="")
    exec_parser.add_argument("--timeout", type=int, default=600)
    exec_parser.add_argument("--cwd", default="")
    exec_parser.add_argument("--dry-run", action="store_true")
    exec_parser.add_argument("--synthetic", action="store_true")
    exec_parser.add_argument("--json", action="store_true")

    route = sub.add_parser("route")
    route.add_argument("--seat", choices=["auto", *sorted(DEFAULT_CONFIG["seats"])], default="auto")
    route.add_argument("--task-class", default="exec")
    route.add_argument("--prefer-local", action="store_true")
    route.add_argument("--json", action="store_true")

    estimate = sub.add_parser("estimate")
    estimate.add_argument("--prompt", default="")
    estimate.add_argument("--json", action="store_true")

    performance = sub.add_parser("performance")
    performance_sub = performance.add_subparsers(dest="action", required=True)
    performance_status = performance_sub.add_parser("status")
    performance_status.add_argument("--json", action="store_true")
    performance_summary = performance_sub.add_parser("summary")
    performance_summary.add_argument("--repo-root", default="")
    performance_summary.add_argument("--since-days", type=int, default=0)
    performance_summary.add_argument("--json", action="store_true")
    performance_runs = performance_sub.add_parser("runs")
    performance_runs.add_argument("--limit", type=int, default=20)
    performance_runs.add_argument("--json", action="store_true")
    performance_purge = performance_sub.add_parser("purge")
    performance_purge.add_argument("--approve", action="store_true")
    performance_purge.add_argument("--json", action="store_true")
    performance_benchmark = performance_sub.add_parser("benchmark")
    performance_benchmark.add_argument("--suite", choices=["exploration"], required=True)
    performance_benchmark.add_argument("--json", action="store_true")
    performance_lab = performance_sub.add_parser("lab")
    performance_lab_sub = performance_lab.add_subparsers(dest="lab_action", required=True)
    performance_lab_validate = performance_lab_sub.add_parser("validate")
    performance_lab_validate.add_argument("--manifest", type=Path, required=True)
    performance_lab_validate.add_argument("--json", action="store_true")
    performance_lab_baseline = performance_lab_sub.add_parser("baseline")
    performance_lab_baseline.add_argument("--manifest", type=Path, required=True)
    performance_lab_baseline.add_argument("--output-root", default="")
    performance_lab_baseline.add_argument("--json", action="store_true")
    performance_lab_run = performance_lab_sub.add_parser("run")
    performance_lab_run.add_argument("--manifest", type=Path, required=True)
    performance_lab_run.add_argument("--candidate", required=True)
    performance_lab_run.add_argument("--output-root", default="")
    performance_lab_run.add_argument("--json", action="store_true")
    performance_lab_live_run = performance_lab_sub.add_parser("live-run")
    performance_lab_live_run.add_argument("--manifest", type=Path, required=True)
    performance_lab_live_run.add_argument("--candidate", required=True)
    performance_lab_live_run.add_argument("--auth-source", type=Path, required=True)
    performance_lab_live_run.add_argument("--supervisor-policy", type=Path, required=True)
    performance_lab_live_run.add_argument("--output-root", default="")
    performance_lab_live_run.add_argument("--json", action="store_true")
    performance_lab_calibrate = performance_lab_sub.add_parser("calibrate")
    performance_lab_calibrate.add_argument("--manifest", type=Path, required=True)
    performance_lab_calibrate.add_argument("--auth-source", type=Path, required=True)
    performance_lab_calibrate.add_argument("--task-id", required=True)
    performance_lab_calibrate.add_argument("--secondary-task-id", default="")
    performance_lab_calibrate.add_argument("--output-root", default="")
    performance_lab_calibrate.add_argument("--json", action="store_true")
    performance_lab_runtime_closeout = performance_lab_sub.add_parser("runtime-closeout")
    performance_lab_runtime_closeout.add_argument("--prior-run", type=Path, required=True)
    performance_lab_runtime_closeout.add_argument("--calibration-run", type=Path, required=True)
    performance_lab_runtime_closeout.add_argument("--validation-summary", type=Path, default="")
    performance_lab_runtime_closeout.add_argument("--output-root", default="")
    performance_lab_runtime_closeout.add_argument("--json", action="store_true")
    performance_lab_compare = performance_lab_sub.add_parser("compare")
    performance_lab_compare.add_argument("--run-dir", type=Path, required=True)
    performance_lab_compare.add_argument("--json", action="store_true")

    search = sub.add_parser("search")
    search_sub = search.add_subparsers(dest="action", required=True)
    search_content = search_sub.add_parser("content")
    search_content.add_argument("pattern")
    search_content.add_argument("--root", type=Path, required=True)
    search_content_mode = search_content.add_mutually_exclusive_group(required=True)
    search_content_mode.add_argument("--literal", action="store_true")
    search_content_mode.add_argument("--regex", action="store_true")
    search_content.add_argument("--include-ignored", action="store_true")
    search_content.add_argument("--max-files", type=int, default=100_000)
    search_content.add_argument("--per-file-ranges", type=int, default=12)
    search_content.add_argument("--total-ranges", type=int, default=96)
    search_content.add_argument("--max-evidence-files", type=int, default=64)
    search_content.add_argument("--page-size", type=int, default=96)
    search_content.add_argument("--page-token", default="")
    search_content.add_argument("--candidate", choices=["v1", "v2"], default="v1")
    search_content.add_argument("--json", action="store_true")
    search_next = search_sub.add_parser("next")
    search_next.add_argument("pattern")
    search_next.add_argument("--root", type=Path, required=True)
    search_next_mode = search_next.add_mutually_exclusive_group(required=True)
    search_next_mode.add_argument("--literal", action="store_true")
    search_next_mode.add_argument("--regex", action="store_true")
    search_next.add_argument("--cursor", required=True)
    search_next.add_argument("--include-ignored", action="store_true")
    search_next.add_argument("--max-files", type=int, default=100_000)
    search_next.add_argument("--per-file-ranges", type=int, default=12)
    search_next.add_argument("--total-ranges", type=int, default=96)
    search_next.add_argument("--max-evidence-files", type=int, default=64)
    search_next.add_argument("--page-size", type=int, default=96)
    search_next.add_argument("--candidate", choices=["v2"], default="v2")
    search_next.add_argument("--json", action="store_true")
    search_paths = search_sub.add_parser("paths")
    search_paths.add_argument("pattern")
    search_paths.add_argument("--root", type=Path, required=True)
    search_paths_mode = search_paths.add_mutually_exclusive_group()
    search_paths_mode.add_argument("--literal", action="store_true")
    search_paths_mode.add_argument("--regex", action="store_true")
    search_paths.add_argument("--include-ignored", action="store_true")
    search_paths.add_argument("--max-files", type=int, default=100_000)
    search_paths.add_argument("--page-size", type=int, default=100)
    search_paths.add_argument("--page-token", default="")
    search_paths.add_argument("--json", action="store_true")

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
    manager.add_argument("action", nargs="?", choices=["status", "assign", "waive", "heartbeat", "close", "close-stale", "repair", "reconcile", "mode", "estimate", "preflight", "decision", "launch-status", "kaveman", "local", "accept", "evidence"])
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
    manager.add_argument("--repo-root", default="")
    manager.add_argument("--pid", type=int, default=0)
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
    manager.add_argument("--review-requirement", default="root review suggested")
    manager.add_argument("--artifact", action="append")
    manager.add_argument(
        "--required",
        action="store_true",
        help="legacy attention metadata for persisted assignments; never a completion gate",
    )
    manager.add_argument(
        "--optional",
        action="store_true",
        help="mark a manual assignment as an advisory suggestion",
    )
    manager.add_argument("--limit", type=int, default=20)
    manager.add_argument("--shortcut", action="store_true")
    manager.add_argument("--profile", choices=["offline", "live", "production"], default="offline")
    manager.add_argument("--run-id", default="")
    manager.add_argument("--results-root", default="")
    manager.add_argument("--json", action="store_true")

    runtime = sub.add_parser("runtime")
    runtime.add_argument(
        "action",
        choices=["status", "generations", "build", "activate", "rollback", "prune"],
        nargs="?",
        default="status",
    )
    runtime.add_argument("--candidate", default="")
    runtime.add_argument("--source-root", default="")
    runtime.add_argument("--runtime-root", default="")
    runtime.add_argument("--codex-bin", default="")
    runtime.add_argument("--code-mode-host", default="")
    runtime.add_argument("--safe", action="store_true")
    runtime.add_argument("--json", action="store_true")

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
    # Performance telemetry is deliberately outside the Manager data plane.
    # Keep its status, summary, purge, and isolated benchmark commands from
    # creating or migrating the Manager state database just to resolve policy.
    if args.command == "performance":
        return command_performance(args, config)
    if args.command == "search":
        return command_search(args, config)
    # Runtime status and rollback must remain usable when Manager state or the
    # selected generation is corrupt. Keep this standard-library recovery lane
    # outside AgentPolicy and state-schema initialization.
    if args.command == "runtime":
        return command_runtime(args)
    if args.command == "manager" and getattr(args, "action", "") == "accept":
        return command_manager_accept(args)
    if args.command == "manager" and getattr(args, "action", "") == "evidence":
        return command_manager_evidence(args)
    manager_hook_launch = bool(
        getattr(args, "command", "") == "agent"
        and getattr(args, "action", "") == "hook"
        and os.environ.get("QWENDEX_MANAGER_LEDGER_ID")
        and os.environ.get("QWENDEX_MANAGER_SESSION_ID")
    )
    launch_agent_use = (
        str(os.environ.get("QWENDEX_EFFECTIVE_AGENT_USE") or "").strip()
        if manager_hook_launch
        else ""
    )
    launch_policy_source = (
        str(os.environ.get("QWENDEX_AGENT_POLICY_SOURCE") or "").strip()
        if manager_hook_launch
        else ""
    )
    launch_kaveman = (
        env_flag(os.environ.get("QWENDEX_KAVEMAN_ENABLED"))
        if manager_hook_launch
        else None
    )
    agent_policy = resolve_agent_policy(
        config,
        cli_agent_use=launch_agent_use or getattr(args, "agent_use", ""),
        selected_manager_mode=selected_manager_mode_for_policy(
            config,
            explicit=getattr(args, "mode", "") if getattr(args, "command", "") == "manager" else "",
        ),
        kaveman_enabled=launch_kaveman,
        selector_source_override=launch_policy_source,
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
