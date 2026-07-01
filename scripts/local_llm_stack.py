#!/usr/bin/env python3
"""State-aware local LLM stack operator console.

This is deliberately stdlib-only. Textual can be layered on top later without
changing the config, tmux, health, or provider launch logic.
"""

from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import os
import shlex
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config" / "local_llm_stack"
PUBLIC_CONFIG = CONFIG_DIR / "stack_manager.json"
SAMPLE_CONFIG = CONFIG_DIR / "stack_manager.sample.json"
LOCAL_CONFIG = CONFIG_DIR / "stack_manager.local.json"
DEFAULT_CONFIG = PUBLIC_CONFIG
CODEX_AUTH_FILE = Path.home() / ".codex" / "auth.json"
WINDOWS_LAUNCHER = r"C:\Windows\System32\open.ps1"
REPO_WINDOWS_LAUNCHER = ROOT / "scripts" / "windows" / "open.ps1"


class StackError(RuntimeError):
    pass


@dataclass(frozen=True)
class HealthCheckResult:
    state: str
    endpoint_responding: bool
    port_occupied: bool
    url: str
    status_code: int | None = None
    error: str = ""
    models: list[str] = field(default_factory=list)
    raw: dict[str, Any] | None = None


@dataclass(frozen=True)
class TmuxWindowState:
    name: str
    exists: bool
    live: bool
    pane_dead: bool
    command: str = ""
    count: int = 0


@dataclass(frozen=True)
class TmuxSessionState:
    name: str
    exists: bool
    windows: dict[str, TmuxWindowState]
    duplicate_sessions: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ServiceDefinition:
    name: str
    display_name: str
    window_name: str
    provider_type: str
    working_dir: str
    command: str
    health_url: str
    port: int
    startup_timeout_seconds: int
    pre_start_commands: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    log_lines: int = 200
    models_url: str = ""
    status_url: str = ""
    notes: str = ""


@dataclass(frozen=True)
class ProviderProfile:
    name: str
    display_name: str
    type: str
    launch_command: str = ""
    base_url: str = ""
    base_url_env: str = ""
    models_url: str = ""
    models_url_env: str = ""
    tmux_session: str = ""
    window_name: str = ""
    open_new_terminal: bool = False
    auto_start_services: bool = False
    requires_services: list[str] = field(default_factory=list)
    secret_env: list[str] = field(default_factory=list)
    allow_codex_auth_file: bool = False


@dataclass(frozen=True)
class ModelProfile:
    name: str
    provider: str
    default: bool = False


@dataclass(frozen=True)
class BackendProfile:
    name: str
    display_name: str
    backend_kind: str
    model_alias: str
    model_path: str = ""
    mtp_expected: bool | None = None
    notes: str = ""
    service_overrides: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass(frozen=True)
class ServiceRuntimeState:
    definition: ServiceDefinition
    health: HealthCheckResult
    tmux_window: TmuxWindowState
    runtime_state: str
    last_error: str = ""


@dataclass(frozen=True)
class ReconcilePlan:
    actions: list[str]
    warnings: list[str]
    destructive_actions: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ActionResult:
    ok: bool
    message: str
    details: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CustomTextgenOverride:
    env: dict[str, str]
    description: str = ""


@dataclass(frozen=True)
class TranscriptTarget:
    runtime_dir: Path
    transcript_dir: Path
    prompt_template_roots: list[Path]
    task_prompt_policy: dict[str, Any]


@dataclass(frozen=True)
class StackConfig:
    name: str
    tmux_session: str
    repo_root: Path
    safe_runtime_dir: Path
    transcript_dir: Path
    prompt_template_roots: list[Path]
    task_prompt_policy: dict[str, Any]
    user_state_file: Path
    default_project: Path
    services: list[ServiceDefinition]
    provider_profiles: list[ProviderProfile]
    model_profiles: list[ModelProfile]
    backend_profiles: list[BackendProfile]
    active_backend_profile: str
    default_backend_profile: str
    context_presets: list[int]
    chat_interfaces: dict[str, Any]
    raw_path: Path


def run(
    args: list[str],
    *,
    capture: bool = True,
    check: bool = False,
    cwd: Path | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            args,
            cwd=str(cwd) if cwd else None,
            text=True,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
            check=check,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else exc.stdout or ""
        stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else exc.stderr or ""
        return subprocess.CompletedProcess(args, 124, stdout, (stderr + f"\ntimeout after {timeout}s").strip())


def tmux_available() -> bool:
    return run(["bash", "-lc", "command -v tmux >/dev/null 2>&1"], capture=True).returncode == 0


def tmux(args: list[str], *, capture: bool = True, check: bool = False, timeout: float = 5.0) -> subprocess.CompletedProcess[str]:
    return run(["tmux", *args], capture=capture, check=check, timeout=timeout)


def format_template(value: str, cfg_vars: dict[str, str]) -> str:
    for key, replacement in cfg_vars.items():
        value = value.replace("{" + key + "}", replacement)
    return value


def expand_obj(obj: Any, cfg_vars: dict[str, str]) -> Any:
    if isinstance(obj, str):
        return format_template(obj, cfg_vars)
    if isinstance(obj, list):
        return [expand_obj(item, cfg_vars) for item in obj]
    if isinstance(obj, dict):
        return {key: expand_obj(value, cfg_vars) for key, value in obj.items()}
    return obj


def default_config_path() -> Path:
    override = os.environ.get("QWENDEX_LLMSTACK_CONFIG") or os.environ.get("LOCAL_LLM_STACK_CONFIG")
    if override:
        return Path(override).expanduser()
    if LOCAL_CONFIG.exists():
        return LOCAL_CONFIG
    return PUBLIC_CONFIG


def load_config(path: Path | None = None) -> StackConfig:
    path = path or default_config_path()
    data = json.loads(path.read_text())
    cfg_vars = {"repo_root": str(ROOT), "home": str(Path.home())}
    data = expand_obj(data, cfg_vars)
    services = [
        ServiceDefinition(
            name=item["name"],
            display_name=item["display_name"],
            window_name=item.get("window_name", item["name"]),
            provider_type=item.get("provider_type", ""),
            working_dir=item.get("working_dir", str(ROOT)),
            command=item["command"],
            health_url=item["health_url"],
            port=int(item["port"]),
            startup_timeout_seconds=int(item.get("startup_timeout_seconds", 120)),
            pre_start_commands=list(item.get("pre_start_commands", [])),
            depends_on=list(item.get("depends_on", [])),
            log_lines=int(item.get("log_lines", 200)),
            models_url=item.get("models_url", item["health_url"]),
            status_url=item.get("status_url", ""),
            notes=item.get("notes", ""),
        )
        for item in data.get("services", [])
    ]
    providers = [
        ProviderProfile(
            name=item["name"],
            display_name=item["display_name"],
            type=item["type"],
            launch_command=item.get("launch_command", ""),
            base_url=item.get("base_url", ""),
            base_url_env=item.get("base_url_env", ""),
            models_url=item.get("models_url", ""),
            models_url_env=item.get("models_url_env", ""),
            tmux_session=item.get("tmux_session", ""),
            window_name=item.get("window_name", ""),
            open_new_terminal=bool(item.get("open_new_terminal", False)),
            auto_start_services=bool(item.get("auto_start_services", False)),
            requires_services=list(item.get("requires_services", [])),
            secret_env=list(item.get("secret_env", [])),
            allow_codex_auth_file=bool(item.get("allow_codex_auth_file", False)),
        )
        for item in data.get("provider_profiles", [])
    ]
    models = [
        ModelProfile(name=item["name"], provider=item["provider"], default=bool(item.get("default", False)))
        for item in data.get("model_profiles", [])
    ]
    backend_profiles = [
        BackendProfile(
            name=item["name"],
            display_name=item["display_name"],
            backend_kind=item.get("backend_kind", ""),
            model_alias=item.get("model_alias", ""),
            model_path=item.get("model_path", ""),
            mtp_expected=item.get("mtp_expected", None),
            notes=item.get("notes", ""),
            service_overrides=dict(item.get("service_overrides", {})),
        )
        for item in data.get("backend_profiles", [])
    ]
    user_state_file = Path(data["user_state_file"]).expanduser()
    default_backend_profile = data.get("default_backend_profile", backend_profiles[0].name if backend_profiles else "")
    configured_default_backend_profile = default_backend_profile
    active_backend_profile = default_backend_profile
    user_default_backend_profile = default_backend_profile
    if user_state_file.exists():
        try:
            user_state = json.loads(user_state_file.read_text())
            user_default_backend_profile = str(user_state.get("default_backend_profile") or default_backend_profile)
            default_backend_profile = user_default_backend_profile
            active_backend_profile = str(user_state.get("active_backend_profile") or default_backend_profile)
        except Exception:
            active_backend_profile = default_backend_profile
    profile_names = {profile.name for profile in backend_profiles}
    if default_backend_profile not in profile_names:
        default_backend_profile = configured_default_backend_profile
    if active_backend_profile not in profile_names:
        active_backend_profile = default_backend_profile
    context_presets = [int(item) for item in data.get("context_presets", [32768, 49152, 65536, 81920, 88064])]
    cfg = StackConfig(
        name=data.get("name", "Local LLM Stack"),
        tmux_session=data["tmux_session"],
        repo_root=Path(data.get("repo_root", str(ROOT))).expanduser(),
        safe_runtime_dir=Path(data.get("safe_runtime_dir", str(Path.home() / ".local/state/local_llm_stack"))).expanduser(),
        transcript_dir=Path(data.get("transcript_dir", str(Path.home() / ".local/state/local_llm_stack/transcripts"))).expanduser(),
        prompt_template_roots=[Path(item).expanduser() for item in data.get("prompt_template_roots", [])],
        task_prompt_policy=dict(data.get("task_prompt_policy", {})),
        user_state_file=user_state_file,
        default_project=Path(data.get("default_project", str(ROOT))).expanduser(),
        services=services,
        provider_profiles=providers,
        model_profiles=models,
        backend_profiles=backend_profiles,
        active_backend_profile=active_backend_profile,
        default_backend_profile=default_backend_profile,
        context_presets=context_presets,
        chat_interfaces=dict(data.get("chat_interfaces", {})),
        raw_path=path,
    )
    return apply_backend_profile(cfg)


def backend_profile_by_name(cfg: StackConfig, name: str) -> BackendProfile:
    for profile in cfg.backend_profiles:
        if profile.name == name:
            return profile
    raise StackError(f"Unknown backend profile: {name}")


def apply_service_override(service: ServiceDefinition, override: dict[str, Any]) -> ServiceDefinition:
    updates: dict[str, Any] = {}
    for key in (
        "display_name",
        "window_name",
        "provider_type",
        "working_dir",
        "command",
        "health_url",
        "port",
        "startup_timeout_seconds",
        "pre_start_commands",
        "depends_on",
        "log_lines",
        "models_url",
        "status_url",
        "notes",
    ):
        if key in override:
            updates[key] = override[key]
    if "port" in updates:
        updates["port"] = int(updates["port"])
    if "startup_timeout_seconds" in updates:
        updates["startup_timeout_seconds"] = int(updates["startup_timeout_seconds"])
    if "log_lines" in updates:
        updates["log_lines"] = int(updates["log_lines"])
    if "pre_start_commands" in updates:
        updates["pre_start_commands"] = list(updates["pre_start_commands"])
    if "depends_on" in updates:
        updates["depends_on"] = list(updates["depends_on"])
    return replace(service, **updates)


def apply_backend_profile(cfg: StackConfig) -> StackConfig:
    if not cfg.backend_profiles:
        return cfg
    try:
        profile = backend_profile_by_name(cfg, cfg.active_backend_profile)
    except StackError:
        profile = backend_profile_by_name(cfg, cfg.default_backend_profile)
        cfg = replace(cfg, active_backend_profile=profile.name)
    services = []
    for service in cfg.services:
        override = profile.service_overrides.get(service.name, {})
        services.append(apply_service_override(service, override) if override else service)
    return replace(cfg, services=services)


def port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.4) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def wait_for_port_closed(port: int, *, timeout_seconds: float = 20.0) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if not port_open(port):
            return True
        time.sleep(0.5)
    return not port_open(port)


def parse_models(body: bytes) -> tuple[list[str], dict[str, Any] | None]:
    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return [], None
    models: list[str] = []
    if isinstance(data, dict):
        raw_models = data.get("data") or data.get("models") or []
        if isinstance(raw_models, list):
            for item in raw_models:
                if isinstance(item, dict):
                    model_id = item.get("id") or item.get("name") or item.get("model")
                    if model_id:
                        models.append(str(model_id))
                elif isinstance(item, str):
                    models.append(item)
    return models, data if isinstance(data, dict) else None


def http_health(url: str, port: int, timeout: float = 3.0) -> HealthCheckResult:
    occupied = port_open(port)
    req = urllib.request.Request(url, headers={"Authorization": "Bearer no-key"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(256_000)
            models, raw = parse_models(body)
            state = "healthy"
            if url.endswith("/v1/models") and not models:
                state = "unhealthy"
            return HealthCheckResult(
                state=state,
                endpoint_responding=True,
                port_occupied=True,
                url=url,
                status_code=getattr(resp, "status", None),
                models=models,
                raw=raw,
            )
    except urllib.error.HTTPError as exc:
        return HealthCheckResult(
            state="unhealthy",
            endpoint_responding=False,
            port_occupied=occupied,
            url=url,
            status_code=exc.code,
            error=f"HTTP {exc.code}",
        )
    except Exception as exc:
        state = "unhealthy" if occupied else "stopped"
        return HealthCheckResult(
            state=state,
            endpoint_responding=False,
            port_occupied=occupied,
            url=url,
            error=f"{type(exc).__name__}: {exc}",
        )


def tmux_session_state(cfg: StackConfig) -> TmuxSessionState:
    if not tmux_available():
        return TmuxSessionState(cfg.tmux_session, False, {}, [])
    sessions_cp = tmux(["list-sessions", "-F", "#S"], capture=True)
    sessions = sessions_cp.stdout.splitlines() if sessions_cp.returncode == 0 else []
    exists = cfg.tmux_session in sessions
    duplicates = [s for s in sessions if s != cfg.tmux_session and cfg.tmux_session in s]
    windows: dict[str, TmuxWindowState] = {}
    if not exists:
        return TmuxSessionState(cfg.tmux_session, False, windows, duplicates)
    cp = tmux(["list-windows", "-t", cfg.tmux_session, "-F", "#W"], capture=True)
    names = cp.stdout.splitlines() if cp.returncode == 0 else []
    for service in cfg.services:
        matching = [name for name in names if name == service.window_name]
        if not matching:
            windows[service.name] = TmuxWindowState(service.window_name, False, False, False)
            continue
        pane_cp = tmux(
            ["list-panes", "-t", f"{cfg.tmux_session}:{service.window_name}", "-F", "#{pane_dead}\t#{pane_current_command}"],
            capture=True,
        )
        pane_dead = False
        command = ""
        if pane_cp.returncode == 0 and pane_cp.stdout.strip():
            first = pane_cp.stdout.splitlines()[0].split("\t", 1)
            pane_dead = first[0] == "1"
            command = first[1] if len(first) > 1 else ""
        windows[service.name] = TmuxWindowState(
            service.window_name,
            True,
            not pane_dead,
            pane_dead,
            command=command,
            count=len(matching),
        )
    return TmuxSessionState(cfg.tmux_session, exists, windows, duplicates)


def service_runtime_state(service: ServiceDefinition, session: TmuxSessionState) -> ServiceRuntimeState:
    health = http_health(service.health_url, service.port)
    window = session.windows.get(service.name, TmuxWindowState(service.window_name, False, False, False))
    last_error = health.error
    if window.count > 1:
        runtime = "duplicate"
    elif window.exists and window.pane_dead:
        runtime = "stale"
    elif health.endpoint_responding and health.state == "healthy" and not window.exists:
        runtime = "externally-managed"
    elif health.endpoint_responding and health.state == "healthy":
        runtime = "healthy"
    elif window.live and not health.port_occupied:
        runtime = "starting"
    elif window.live and health.port_occupied:
        runtime = "unhealthy"
    elif health.port_occupied and not window.exists:
        runtime = "externally-managed"
        last_error = health.error or "port occupied but managed tmux window is missing"
    else:
        runtime = "stopped"
    return ServiceRuntimeState(service, health, window, runtime, last_error)


def collect_runtime(cfg: StackConfig) -> tuple[TmuxSessionState, list[ServiceRuntimeState]]:
    session = tmux_session_state(cfg)
    return session, [service_runtime_state(service, session) for service in cfg.services]


def config_errors(cfg: StackConfig) -> list[str]:
    errors: list[str] = []
    if not tmux_available():
        errors.append("tmux is not installed or not on PATH")
    for service in cfg.services:
        command_name = shlex.split(service.command)[0] if service.command else ""
        if command_name and command_name.startswith("scripts/"):
            script_path = cfg.repo_root / command_name
            if not script_path.exists():
                errors.append(f"{service.name} command script is missing: {script_path}")
        for pre_start in service.pre_start_commands:
            command_name = shlex.split(pre_start)[0] if pre_start else ""
            if command_name and command_name.startswith("scripts/"):
                script_path = cfg.repo_root / command_name
                if not script_path.exists():
                    errors.append(f"{service.name} pre-start script is missing: {script_path}")
    for profile in cfg.backend_profiles:
        if profile.model_path and not Path(profile.model_path).exists():
            errors.append(f"{profile.name} model file is missing: {profile.model_path}")
    if cfg.transcript_dir.resolve().is_relative_to(cfg.repo_root.resolve()):
        errors.append(f"transcript_dir must stay outside the repo: {cfg.transcript_dir}")
    return errors


def build_reconcile_plan(cfg: StackConfig) -> ReconcilePlan:
    session, states = collect_runtime(cfg)
    actions: list[str] = []
    warnings: list[str] = config_errors(cfg)
    destructive: list[str] = []
    if not tmux_available():
        warnings.append("tmux is not available; services cannot be managed persistently.")
        return ReconcilePlan(actions, warnings, destructive)
    if session.duplicate_sessions:
        warnings.append("duplicate-looking tmux sessions: " + ", ".join(session.duplicate_sessions))
    if not session.exists:
        actions.append(f"create tmux session {cfg.tmux_session}")
    for state in states:
        service = state.definition
        if state.tmux_window.count > 1:
            warnings.append(f"{service.name} has duplicate windows in {cfg.tmux_session}; no new window will be created.")
        if state.runtime_state == "stale":
            destructive.append(f"remove stale window {service.window_name} before respawn")
            actions.append(f"start {service.name}")
        elif state.runtime_state == "stopped":
            actions.append(f"start {service.name}")
        elif state.runtime_state == "externally-managed":
            warnings.append(f"{service.name} endpoint or port exists outside the managed tmux window; leaving it alone.")
        elif state.runtime_state == "unhealthy":
            warnings.append(f"{service.name} is live but unhealthy; use restart after reviewing logs.")
    return ReconcilePlan(actions, warnings, destructive)


def ensure_session(cfg: StackConfig) -> None:
    if not tmux_available():
        raise StackError("tmux is required for the stack manager.")
    if tmux(["has-session", "-t", cfg.tmux_session], capture=True).returncode == 0:
        enable_tmux_mouse(cfg.tmux_session)
        return
    tmux(["new-session", "-d", "-s", cfg.tmux_session, "-n", "control", f"cd {shlex.quote(str(cfg.repo_root))} && exec bash"], check=True)
    tmux(["set-option", "-t", cfg.tmux_session, "remain-on-exit", "on"], check=False)
    enable_tmux_mouse(cfg.tmux_session)
    tmux(["set-window-option", "-t", cfg.tmux_session, "remain-on-exit", "on"], check=False)


def enable_tmux_mouse(session_name: str) -> None:
    tmux(["set-option", "-t", session_name, "mouse", "on"], check=False)


def pane_process_matches(pane_pid: int, required_needles: list[str]) -> bool:
    if not required_needles:
        return True
    cp = run(["ps", "-eo", "pid=,ppid=,args="], capture=True)
    if cp.returncode != 0:
        return False
    children: dict[int, list[tuple[int, str]]] = {}
    for line in cp.stdout.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 3:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
        except ValueError:
            continue
        children.setdefault(ppid, []).append((pid, parts[2]))
    stack = [pane_pid]
    seen: set[int] = set()
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        for child_pid, args in children.get(current, []):
            if all(needle in args for needle in required_needles):
                return True
            stack.append(child_pid)
    return False


def tmux_session_option(session_name: str, option_name: str) -> str:
    cp = tmux(["show-options", "-t", session_name, "-qv", option_name], capture=True)
    return cp.stdout.strip() if cp.returncode == 0 else ""


def provider_session_ready(session_name: str, command: str, process_needles: list[str]) -> bool:
    if tmux(["has-session", "-t", session_name], capture=True).returncode != 0:
        return False
    if tmux_session_option(session_name, "@llmstack_command") != command:
        return False
    if not process_needles:
        return True
    pane_cp = tmux(["list-panes", "-t", session_name, "-F", "#{pane_dead}\t#{pane_pid}"], capture=True)
    if pane_cp.returncode != 0 or not pane_cp.stdout.strip():
        return False
    for raw_line in pane_cp.stdout.splitlines():
        parts = raw_line.split("\t")
        if len(parts) < 2 or parts[0] == "1":
            continue
        try:
            pane_pid = int(parts[1])
        except ValueError:
            continue
        if pane_process_matches(pane_pid, process_needles):
            return True
    return False


def wait_for_provider_session(session_name: str, command: str, process_needles: list[str], *, timeout_seconds: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if provider_session_ready(session_name, command, process_needles):
            return True
        time.sleep(0.5)
    return provider_session_ready(session_name, command, process_needles)


def ensure_named_session(
    session_name: str,
    window_name: str,
    command: str,
    cwd: str,
    *,
    process_needles: list[str] | None = None,
) -> ActionResult:
    if not tmux_available():
        return ActionResult(False, "tmux is required to open a persistent session.")
    if tmux(["has-session", "-t", session_name], capture=True).returncode == 0:
        if provider_session_ready(session_name, command, process_needles or []):
            enable_tmux_mouse(session_name)
            return ActionResult(True, f"tmux session already running requested command: {session_name}")
        tmux(["kill-session", "-t", session_name], check=False)
    tmux(["new-session", "-d", "-s", session_name, "-n", window_name, shell_command(command, cwd)], check=True)
    tmux(["set-option", "-t", session_name, "remain-on-exit", "on"], check=False)
    enable_tmux_mouse(session_name)
    tmux(["set-option", "-t", session_name, "@llmstack_command", command], check=False)
    tmux(["set-option", "-t", session_name, "@llmstack_cwd", cwd], check=False)
    tmux(["set-window-option", "-t", f"{session_name}:{window_name}", "remain-on-exit", "on"], check=False)
    if process_needles and not wait_for_provider_session(session_name, command, process_needles):
        return ActionResult(False, f"opened {session_name}, but the expected provider process did not become ready")
    return ActionResult(True, f"opened tmux session {session_name}:{window_name}")


def shell_command(command: str, cwd: str) -> str:
    return f"cd {shlex.quote(cwd)} && exec bash -lc {shlex.quote(command)}"


def window_exists(cfg: StackConfig, window: str) -> bool:
    return tmux(["list-windows", "-t", cfg.tmux_session, "-F", "#W"], capture=True).stdout.splitlines().count(window) > 0


def kill_window(cfg: StackConfig, window: str) -> None:
    if window_exists(cfg, window):
        tmux(["kill-window", "-t", f"{cfg.tmux_session}:{window}"], check=False)


def wait_for_service(service: ServiceDefinition) -> ActionResult:
    deadline = time.monotonic() + service.startup_timeout_seconds
    while time.monotonic() < deadline:
        health = http_health(service.health_url, service.port)
        if health.endpoint_responding and health.state == "healthy":
            return ActionResult(True, f"{service.name} is healthy at {service.health_url}")
        time.sleep(5)
    return ActionResult(False, f"{service.name} did not become healthy at {service.health_url}")


def service_by_name(cfg: StackConfig, name: str) -> ServiceDefinition:
    for service in cfg.services:
        if service.name == name:
            return service
    raise StackError(f"Unknown service: {name}")


def command_env_assignments(command: str) -> dict[str, str]:
    try:
        parts = shlex.split(command)
    except ValueError:
        return {}
    env: dict[str, str] = {}
    for part in parts:
        if "=" not in part or part.startswith("-"):
            break
        key, value = part.split("=", 1)
        if not key or not all(ch.isalnum() or ch == "_" for ch in key):
            break
        env[key] = value
    return env


def command_with_env_overrides(command: str, env: dict[str, str]) -> str:
    if not env:
        return command
    try:
        parts = shlex.split(command)
    except ValueError:
        prefix = " ".join(f"{key}={shlex.quote(value)}" for key, value in env.items())
        return f"{prefix} {command}"
    kept_assignments: list[str] = []
    command_parts: list[str] = []
    skipping_assignments = True
    for part in parts:
        if skipping_assignments and "=" in part and not part.startswith("-"):
            key, value = part.split("=", 1)
            if key and all(ch.isalnum() or ch == "_" for ch in key):
                if key in env:
                    continue
                kept_assignments.append(f"{key}={shlex.quote(value)}")
                continue
        skipping_assignments = False
        command_parts.append(part)
    prefix = [f"{key}={shlex.quote(value)}" for key, value in env.items()]
    pieces = [*prefix, *kept_assignments]
    if command_parts:
        pieces.append(shlex.join(command_parts))
    return " ".join(pieces)


def managed_windows_for_service(cfg: StackConfig, service_name: str) -> set[str]:
    windows = {service_by_name(cfg, service_name).window_name}
    for profile in cfg.backend_profiles:
        override = profile.service_overrides.get(service_name, {})
        if override.get("window_name"):
            windows.add(str(override["window_name"]))
    return windows


def start_one(cfg: StackConfig, name: str, *, wait: bool = False) -> ActionResult:
    service = service_by_name(cfg, name)
    session, states = collect_runtime(cfg)
    state_by_name = {item.definition.name: item for item in states}
    current = state_by_name[name]
    if current.runtime_state in {"healthy", "externally-managed"}:
        return ActionResult(True, f"{service.name} already {current.runtime_state}; no new window created.")
    if current.runtime_state == "duplicate":
        return ActionResult(False, f"{service.name} has duplicate tmux windows; refusing to create another.")
    if current.runtime_state == "stale":
        kill_window(cfg, service.window_name)
        wait_for_port_closed(service.port)
        session, states = collect_runtime(cfg)
        state_by_name = {item.definition.name: item for item in states}
        current = state_by_name[name]
    if current.health.port_occupied and not current.health.endpoint_responding and not current.tmux_window.exists:
        if wait_for_port_closed(service.port, timeout_seconds=10.0):
            session, states = collect_runtime(cfg)
            state_by_name = {item.definition.name: item for item in states}
            current = state_by_name[name]
        if current.health.port_occupied and not current.health.endpoint_responding and not current.tmux_window.exists:
            return ActionResult(False, f"{service.name} port {service.port} is occupied by an unmanaged process.")
    ensure_session(cfg)
    if current.tmux_window.exists and current.tmux_window.live:
        return ActionResult(True, f"{service.name} window is already live; restart it to replace the process.")
    for pre_start in service.pre_start_commands:
        cp = run(["bash", "-lc", pre_start], capture=True, cwd=cfg.repo_root)
        if cp.returncode != 0:
            return ActionResult(False, f"pre-start failed for {service.name}: {pre_start}", [cp.stderr.strip(), cp.stdout.strip()])
    tmux(
        ["new-window", "-d", "-t", f"{cfg.tmux_session}:", "-n", service.window_name, shell_command(service.command, service.working_dir)],
        check=True,
    )
    tmux(["set-window-option", "-t", f"{cfg.tmux_session}:{service.window_name}", "remain-on-exit", "on"], check=False)
    result = ActionResult(True, f"started {service.name} in {cfg.tmux_session}:{service.window_name}")
    if wait:
        waited = wait_for_service(service)
        return ActionResult(waited.ok, result.message + "; " + waited.message, waited.details)
    return result


def start_services(cfg: StackConfig, target: str, *, wait: bool = True) -> list[ActionResult]:
    names = [service.name for service in cfg.services] if target == "all" else [target]
    results: list[ActionResult] = []
    for name in names:
        service = service_by_name(cfg, name)
        for dep in service.depends_on:
            if dep not in names:
                dep_result = start_one(cfg, dep, wait=wait)
                results.append(dep_result)
                if not dep_result.ok:
                    return results
        result = start_one(cfg, name, wait=wait)
        results.append(result)
        if not result.ok:
            return results
    return results


def stop_services(cfg: StackConfig, target: str) -> list[ActionResult]:
    ensure_session(cfg)
    names = [service.name for service in reversed(cfg.services)] if target == "all" else [target]
    results: list[ActionResult] = []
    for name in names:
        service = service_by_name(cfg, name)
        windows = managed_windows_for_service(cfg, name) if target == "all" else {service.window_name}
        for window in sorted(windows):
            kill_window(cfg, window)
        wait_for_port_closed(service.port)
        results.append(ActionResult(True, f"stopped managed window(s) if present: {', '.join(sorted(windows))}"))
    return results


def restart_services(cfg: StackConfig, target: str) -> list[ActionResult]:
    results = stop_services(cfg, target)
    results.extend(start_services(cfg, target, wait=True))
    return results


def reset_stack(cfg: StackConfig) -> ActionResult:
    if not tmux_available():
        return ActionResult(False, "tmux is not available.")
    if tmux(["has-session", "-t", cfg.tmux_session], capture=True).returncode == 0:
        tmux(["kill-session", "-t", cfg.tmux_session], check=False)
        return ActionResult(True, f"killed tmux session: {cfg.tmux_session}")
    return ActionResult(True, f"tmux session was not running: {cfg.tmux_session}")


def attach_window(cfg: StackConfig, window: str) -> None:
    ensure_session(cfg)
    if not window_exists(cfg, window):
        raise StackError(f"No tmux window named {window}.")
    if os.environ.get("TMUX"):
        tmux(["switch-client", "-t", f"{cfg.tmux_session}:{window}"], capture=False, check=True)
    else:
        tmux(["select-window", "-t", f"{cfg.tmux_session}:{window}"], check=False)
        os.execvp("tmux", ["tmux", "attach-session", "-t", cfg.tmux_session])


def capture_logs(cfg: StackConfig, service_name: str) -> str:
    service = service_by_name(cfg, service_name)
    if not window_exists(cfg, service.window_name):
        return f"No managed window exists for {service.name}."
    cp = tmux(["capture-pane", "-p", "-t", f"{cfg.tmux_session}:{service.window_name}", "-S", f"-{service.log_lines}"], capture=True)
    return cp.stdout if cp.returncode == 0 else cp.stderr


class ProjectStore:
    def __init__(self, cfg: StackConfig):
        self.cfg = cfg
        self.path = cfg.user_state_file
        self.data = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"current": str(self.cfg.default_project), "recent": [str(self.cfg.default_project)]}
        try:
            data = json.loads(self.path.read_text())
            if not isinstance(data, dict):
                raise ValueError("project state is not a JSON object")
            data.setdefault("current", str(self.cfg.default_project))
            data.setdefault("recent", [str(self.cfg.default_project)])
            return data
        except Exception:
            return {"current": str(self.cfg.default_project), "recent": [str(self.cfg.default_project)]}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2) + "\n")

    def current(self) -> Path:
        return Path(self.data.get("current") or str(self.cfg.default_project)).expanduser()

    def recent(self) -> list[Path]:
        items = [Path(p).expanduser() for p in self.data.get("recent", [])]
        default = self.cfg.default_project
        if default not in items:
            items.insert(0, default)
        return items[:12]

    def resolve_project_path(self, raw_path: str) -> Path:
        text = raw_path.strip()
        if not text:
            raise StackError("Project folder cannot be empty.")
        path = Path(text).expanduser()
        if path.is_absolute() or text.startswith("~"):
            return path
        return Path.home() / path

    def set_current(self, path: Path) -> None:
        resolved = path.expanduser().resolve()
        if not resolved.exists() or not resolved.is_dir():
            raise StackError(f"Project folder does not exist: {resolved}")
        recent = [str(resolved)] + [str(p) for p in self.recent() if p.resolve() != resolved]
        self.data["current"] = str(resolved)
        self.data["recent"] = recent[:12]
        self.save()

    def active_backend_profile(self) -> str:
        return str(self.data.get("active_backend_profile") or self.cfg.active_backend_profile)

    def set_active_backend_profile(self, profile_name: str) -> None:
        backend_profile_by_name(self.cfg, profile_name)
        self.data["active_backend_profile"] = profile_name
        self.save()

    def set_default_backend_profile(self, profile_name: str, *, also_active: bool = False) -> None:
        backend_profile_by_name(self.cfg, profile_name)
        self.data["default_backend_profile"] = profile_name
        if also_active:
            self.data["active_backend_profile"] = profile_name
        self.save()


def provider_by_name(cfg: StackConfig, name: str) -> ProviderProfile:
    for provider in cfg.provider_profiles:
        if provider.name == name:
            return provider
    raise StackError(f"Unknown provider: {name}")


def provider_models(provider: ProviderProfile) -> list[str]:
    url = provider.models_url
    if provider.models_url_env:
        url = os.environ.get(provider.models_url_env, url)
    if not url:
        return []
    req = urllib.request.Request(url, headers={"Authorization": "Bearer no-key"})
    try:
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            models, _ = parse_models(resp.read(256_000))
            return models
    except Exception:
        return []


def transcript_target(cfg: StackConfig) -> TranscriptTarget:
    return TranscriptTarget(
        runtime_dir=cfg.safe_runtime_dir,
        transcript_dir=cfg.transcript_dir,
        prompt_template_roots=cfg.prompt_template_roots,
        task_prompt_policy=cfg.task_prompt_policy,
    )


def active_backend_profile(cfg: StackConfig) -> BackendProfile:
    return backend_profile_by_name(cfg, cfg.active_backend_profile)


def terminal_width(default: int = 132) -> int:
    try:
        return max(os.get_terminal_size().columns, default)
    except OSError:
        return default


def trunc(value: str, width: int) -> str:
    if width <= 0:
        return ""
    if len(value) <= width:
        return value
    if width <= 1:
        return value[:width]
    return value[: width - 1] + "~"


def yn_unknown(value: bool | None) -> str:
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return "unknown"


def current_gpu_summary() -> dict[str, Any]:
    cp = run(
        [
            "nvidia-smi",
            "--query-gpu=name,memory.total,memory.used,memory.free,utilization.gpu",
            "--format=csv,noheader,nounits",
        ],
        capture=True,
    )
    if cp.returncode != 0 or not cp.stdout.strip():
        return {"available": False, "error": cp.stderr.strip()}
    first = cp.stdout.splitlines()[0]
    parts = [part.strip() for part in first.split(",")]
    if len(parts) < 5:
        return {"available": False, "error": first}
    try:
        return {
            "available": True,
            "name": parts[0],
            "memory_total_mb": int(parts[1]),
            "memory_used_mb": int(parts[2]),
            "memory_free_mb": int(parts[3]),
            "gpu_utilization_percent": int(parts[4]),
        }
    except ValueError:
        return {"available": False, "error": first}


def model_role_hint(profile: BackendProfile) -> str:
    kind_hints = {
        "textgen": "TextGen/OpenAI-compatible backend profile",
        "textgen-exl3": "TextGen EXL3 backend profile",
        "llamacpp-gguf": "llama.cpp GGUF backend profile",
        "vllm-gguf": "vLLM GGUF backend profile",
        "koboldcpp-gguf": "KoboldCPP GGUF backend profile",
    }
    return kind_hints.get(profile.backend_kind, "")


def backend_profile_vision_flag(profile: BackendProfile) -> str:
    command = profile.service_overrides.get("textgen", {}).get("command", "")
    env = command_env_assignments(command)
    if profile.backend_kind in {"vllm-gguf", "llamacpp-gguf", "koboldcpp-gguf"}:
        return "off"
    disabled = env.get("TEXTGEN_DISABLE_VISION", "").lower() in {"1", "true", "yes", "on"}
    if disabled:
        return "off"
    if "TEXTGEN_MMPROJ" in env or "vision" in profile.name.lower() or "vision" in profile.display_name.lower():
        return "on"
    if profile.backend_kind.startswith("textgen") and profile.model_path:
        return "auto"
    return "-"


def receipt_score(path: Path, profile: BackendProfile, data: dict[str, Any]) -> int:
    stem = path.stem.lower().replace("-", "_")
    profile_key = profile.name.lower().replace("-", "_")
    score = 0
    if data.get("passed") is True:
        score += 50
    if "blocked" in stem:
        score -= 15
    if profile_key in stem:
        score += 20
    if "latest" in stem:
        score -= 5
    if "textgen_gguf" in stem and profile.backend_kind == "textgen-gguf":
        score += 5
    if "textgen_exl3" in stem and profile.backend_kind == "textgen":
        score += 5
    if "vllm_gguf" in stem and profile.backend_kind == "vllm-gguf":
        score += 5
    if "llamacpp_gguf" in stem and profile.backend_kind == "llamacpp-gguf":
        score += 5
    if "koboldcpp_gguf" in stem and profile.backend_kind == "koboldcpp-gguf":
        score += 5
    return score


def receipt_backend_kind_hint(path: Path) -> str | None:
    stem = path.stem.lower().replace("-", "_")
    hints = {
        "textgen_gguf": "textgen-gguf",
        "textgen_exl3": "textgen",
        "vllm_gguf": "vllm-gguf",
        "llamacpp_gguf": "llamacpp-gguf",
        "koboldcpp_gguf": "koboldcpp-gguf",
    }
    for needle, backend_kind in hints.items():
        if needle in stem:
            return backend_kind
    return None


def verification_receipt(cfg: StackConfig, profile: BackendProfile) -> dict[str, Any] | None:
    candidates: list[tuple[int, Path, dict[str, Any]]] = []
    for path in cfg.raw_path.parent.glob("local_model_verification*.json"):
        try:
            data = json.loads(path.read_text())
        except Exception:
            continue
        if data.get("model") != profile.model_alias:
            continue
        receipt_profile = data.get("backend_profile")
        if receipt_profile and receipt_profile != profile.name:
            continue
        receipt_kind = data.get("backend_kind") or receipt_backend_kind_hint(path)
        if receipt_kind and receipt_kind != profile.backend_kind:
            continue
        candidates.append((receipt_score(path, profile, data), path, data))
    if not candidates:
        return None
    _, path, data = sorted(candidates, key=lambda item: (item[0], str(item[1])), reverse=True)[0]
    return {"path": str(path), "data": data}


def verification_summary(cfg: StackConfig, profile: BackendProfile) -> dict[str, Any]:
    receipt = verification_receipt(cfg, profile)
    if not receipt:
        return {"available": False}
    data = receipt["data"]
    speed = data.get("speed") if isinstance(data.get("speed"), dict) else {}
    codex_exec = data.get("codex_exec") if isinstance(data.get("codex_exec"), dict) else {}
    gpu_after = data.get("gpu_after") if isinstance(data.get("gpu_after"), dict) else {}
    return {
        "available": True,
        "path": receipt["path"],
        "timestamp_utc": data.get("timestamp_utc", ""),
        "passed": bool(data.get("passed", False)),
        "tokens_per_second": speed.get("completion_tokens_per_s"),
        "codex_exec_seconds": codex_exec.get("elapsed_s"),
        "codex_exec_passed": codex_exec.get("passed"),
        "gpu_free_after_mb": gpu_after.get("memory_free_mb"),
        "gpu_used_after_mb": gpu_after.get("memory_used_mb"),
    }


def format_perf(summary: dict[str, Any]) -> str:
    if not summary.get("available"):
        return "no receipt"
    bits = ["pass" if summary.get("passed") else "fail"]
    tps = summary.get("tokens_per_second")
    if isinstance(tps, (int, float)):
        bits.append(f"{tps:.1f} tok/s")
    codex_s = summary.get("codex_exec_seconds")
    if isinstance(codex_s, (int, float)):
        bits.append(f"Codex {codex_s:.1f}s")
    free_mb = summary.get("gpu_free_after_mb")
    if isinstance(free_mb, int):
        bits.append(f"{free_mb}MB free")
    return ", ".join(bits)


def shell_format_launch(command: str, project: Path, model_alias: str = "") -> str:
    return command.format(project=shlex.quote(str(project)), model_alias=shlex.quote(model_alias))


def services_missing_for_provider(cfg: StackConfig, provider: ProviderProfile) -> list[str]:
    if not provider.requires_services:
        return []
    _, states = collect_runtime(cfg)
    states_by_name = {state.definition.name: state for state in states}
    return [
        name
        for name in provider.requires_services
        if states_by_name[name].runtime_state not in {"healthy", "externally-managed"}
    ]


def textgen_loaded_model_state(cfg: StackConfig) -> tuple[bool, str]:
    _, states = collect_runtime(cfg)
    for state in states:
        if state.definition.name != "textgen":
            continue
        models = list(state.health.models)
        if state.runtime_state in {"healthy", "externally-managed"} and state.health.state == "healthy" and models:
            return True, ", ".join(models[:3])
        detail = state.last_error or state.runtime_state
        return False, detail
    return False, "textgen service is not configured"


def open_terminal_for_tmux(session_name: str) -> ActionResult:
    ps_check = run(["bash", "-lc", "command -v powershell.exe >/dev/null 2>&1"], capture=True)
    if ps_check.returncode != 0:
        return ActionResult(False, "powershell.exe is not available from WSL; attach manually with tmux.")
    if tmux_available() and tmux(["has-session", "-t", session_name], capture=True).returncode == 0:
        enable_tmux_mouse(session_name)
    distro = os.environ.get("LOCAL_LLM_WINDOWS_DISTRO", "ubuntu")
    ps_distro = "'" + distro.replace("'", "''") + "'"
    ps_session = "'" + session_name.replace("'", "''") + "'"
    attach_command = (
        "$ErrorActionPreference = 'Continue'; "
        f"& wsl.exe -d {ps_distro} -e tmux attach-session -t {ps_session}; "
        "if ($LASTEXITCODE -ne 0) { "
        f"Write-Host 'tmux session is not available: {session_name}'; "
        "Read-Host 'Press Enter to close' | Out-Null "
        "}"
    )
    inner_encoded = base64.b64encode(attach_command.encode("utf-16le")).decode("ascii")
    ps_inner_literal = "'" + inner_encoded + "'"
    ps_script = (
        "$encoded = " + ps_inner_literal + "; "
        "Start-Process powershell.exe -ArgumentList @('-NoExit','-ExecutionPolicy','Bypass','-EncodedCommand',$encoded)"
    )
    encoded = base64.b64encode(ps_script.encode("utf-16le")).decode("ascii")
    cp = run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded], capture=True)
    if cp.returncode != 0:
        return ActionResult(False, f"failed to open PowerShell tmux terminal for {session_name}", [cp.stderr.strip(), cp.stdout.strip()])
    return ActionResult(True, f"opened new PowerShell window attached to tmux session {session_name}")


def attach_or_open_provider_session(session_name: str, *, open_new_terminal: bool) -> ActionResult:
    if open_new_terminal:
        opened = open_terminal_for_tmux(session_name)
        return opened
    if os.environ.get("TMUX"):
        cp = tmux(["switch-client", "-t", session_name], capture=True)
        if cp.returncode == 0:
            return ActionResult(True, f"switched to tmux session {session_name}")
        return ActionResult(False, f"failed to switch to tmux session {session_name}", [cp.stderr.strip()])
    return ActionResult(True, f"attach with: tmux attach-session -t {session_name}")


def provider_launch_command(cfg: StackConfig, provider: ProviderProfile, project: Path) -> str:
    return shell_format_launch(provider.launch_command, project, active_backend_profile(cfg).model_alias)


def provider_process_needles(cfg: StackConfig, provider: ProviderProfile) -> list[str]:
    if provider.name == "normal-codex":
        return []
    needles = ["codex"]
    if provider.name == "local-qwen-codex":
        needles.extend(["--local-provider", "qwen-local"])
    return needles


def provider_session_status(cfg: StackConfig, provider: ProviderProfile, project: Path) -> dict[str, Any]:
    session_name = provider.tmux_session or ("local-qwen-codex" if provider.name == "local-qwen-codex" else "gpt-codex")
    if not provider.launch_command:
        return {"provider": provider.name, "session": session_name, "state": "not-launchable", "reason": "no launch command"}
    command = provider_launch_command(cfg, provider, project)
    exists = tmux_available() and tmux(["has-session", "-t", session_name], capture=True).returncode == 0
    if not exists:
        return {"provider": provider.name, "session": session_name, "state": "missing", "command": command}
    configured_command = tmux_session_option(session_name, "@llmstack_command")
    ready = provider_session_ready(session_name, command, provider_process_needles(cfg, provider))
    if ready:
        state = "ready-current"
    elif configured_command and configured_command != command:
        state = "stale-different-command"
    else:
        state = "stale-or-idle"
    reason = ""
    if state == "stale-different-command":
        reason = "session exists but was launched for a different project/model command"
    elif state == "stale-or-idle":
        reason = "session exists but no matching Codex process was found"
    return {
        "provider": provider.name,
        "session": session_name,
        "state": state,
        "reason": reason,
        "command": command,
        "configured_command": configured_command,
    }


def provider_sessions_to_dict(cfg: StackConfig) -> list[dict[str, Any]]:
    project = ProjectStore(cfg).current()
    return [
        provider_session_status(cfg, provider, project)
        for provider in cfg.provider_profiles
        if provider.tmux_session or provider.launch_command
    ]


def launch_open_webui(cfg: StackConfig, interface_name: str = "open-webui", *, reload_backend: bool = False) -> ActionResult:
    interface = cfg.chat_interfaces.get(interface_name)
    if interface is None:
        return ActionResult(False, f"unknown chat interface: {interface_name}")

    details: list[str] = []
    backend_profile = str(interface.get("backend_profile", "")).strip()
    skip_stack_start = True
    if reload_backend and backend_profile:
        reload_results = reload_backend_profile(cfg, backend_profile)
        details.extend(result.message for result in reload_results)
        if not all(result.ok for result in reload_results):
            return ActionResult(False, f"failed to activate backend profile {backend_profile} for {interface_name}", details)
    elif backend_profile:
        model_running, model_detail = textgen_loaded_model_state(cfg)
        if model_running:
            details.append(f"preserved running TextGen model(s): {model_detail}")
        else:
            profile_result = set_backend_profile(cfg, backend_profile)
            details.append(profile_result.message)
            if not profile_result.ok:
                return ActionResult(False, f"failed to activate backend profile {backend_profile} for {interface_name}", details)
            open_webui_cfg = load_config(cfg.raw_path)
            start_result = start_one(open_webui_cfg, "textgen", wait=True)
            details.append(start_result.message)
            if not start_result.ok:
                return ActionResult(False, f"failed to start Open WebUI backend profile {backend_profile}", details)
            cfg = open_webui_cfg

    script = interface.get("windows_start_script", r"C:\open-webui\start-open-webui.ps1")
    ps_check = run(["bash", "-lc", "command -v powershell.exe >/dev/null 2>&1"], capture=True)
    if ps_check.returncode != 0:
        return ActionResult(False, "powershell.exe is not available from WSL; run C:\\open-webui\\start-open-webui.ps1 from Windows PowerShell.")
    ps_script_literal = "'" + str(script).replace("'", "''") + "'"
    arg_list = ["'-NoExit'", "'-ExecutionPolicy'", "'Bypass'", "'-File'", "$script"]
    backend_url = str(interface.get("backend_url", "")).strip()
    if backend_url:
        arg_list.extend(["'-OpenAIBaseUrl'", "'" + backend_url.replace("'", "''") + "'"])
    if "port" in interface:
        arg_list.extend(["'-Port'", "'" + str(interface["port"]).replace("'", "''") + "'"])
    data_dir = str(interface.get("data_dir", "")).strip()
    if data_dir:
        arg_list.extend(["'-DataDir'", "'" + data_dir.replace("'", "''") + "'"])
    webui_name = str(interface.get("webui_name", "")).strip()
    if webui_name:
        arg_list.extend(["'-WebUIName'", "'" + webui_name.replace("'", "''") + "'"])
    if skip_stack_start:
        arg_list.append("'-SkipStackStart'")
    ps_script = (
        "$script = " + ps_script_literal + "; "
        "Start-Process powershell.exe -ArgumentList @(" + ",".join(arg_list) + ")"
    )
    encoded = base64.b64encode(ps_script.encode("utf-16le")).decode("ascii")
    cp = run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded], capture=True)
    if cp.returncode != 0:
        details.extend(item for item in [cp.stderr.strip(), cp.stdout.strip()] if item)
        return ActionResult(False, "failed to open Open WebUI PowerShell launcher", details)
    target = interface.get("backend_url", "http://127.0.0.1:4000/v1")
    if reload_backend and backend_profile:
        return ActionResult(True, f"opened {interface_name}; backend target is {target} using profile {backend_profile}", details)
    if backend_profile and any(detail.startswith("started textgen") or detail.startswith("textgen already") for detail in details):
        return ActionResult(True, f"opened {interface_name}; backend target is {target} using profile {backend_profile}", details)
    return ActionResult(True, f"opened {interface_name}; backend target is {target} using current profile {cfg.active_backend_profile}", details)


def launch_provider(cfg: StackConfig, provider_name: str, project: Path, *, attach: bool = False, fresh: bool = False) -> ActionResult:
    provider = provider_by_name(cfg, provider_name)
    if provider.secret_env:
        has_env_secret = any(os.environ.get(env_name) for env_name in provider.secret_env)
        has_codex_auth = provider.allow_codex_auth_file and CODEX_AUTH_FILE.exists()
        if not has_env_secret and not has_codex_auth:
            names = ", ".join(provider.secret_env)
            return ActionResult(False, f"{provider.display_name} needs {names} or existing Codex auth outside the repo.")
    missing = services_missing_for_provider(cfg, provider)
    details: list[str] = []
    if missing and provider.auto_start_services:
        start_results = start_services(cfg, "all", wait=True)
        details.extend(result.message for result in start_results)
        if not all(result.ok for result in start_results):
            return ActionResult(False, "required services did not start cleanly", details)
        missing = services_missing_for_provider(cfg, provider)
    if missing:
        return ActionResult(False, f"Required services are not healthy: {', '.join(missing)}")
    if not provider.launch_command:
        return ActionResult(False, f"Provider {provider.name} has no launch command.")
    command = provider_launch_command(cfg, provider, project)
    session_name = provider.tmux_session or ("local-qwen-codex" if provider.name == "local-qwen-codex" else "gpt-codex")
    window_name = provider.window_name or "codex"
    process_needles = provider_process_needles(cfg, provider)
    if fresh and tmux_available() and tmux(["has-session", "-t", session_name], capture=True).returncode == 0:
        tmux(["kill-session", "-t", session_name], check=False)
        details.append(f"killed existing provider tmux session: {session_name}")
    result = ensure_named_session(session_name, window_name, command, str(cfg.repo_root), process_needles=process_needles)
    details.extend(result.details)
    details.append(result.message)
    if not result.ok:
        return ActionResult(False, result.message, details)
    if attach:
        attach_result = attach_or_open_provider_session(session_name, open_new_terminal=provider.open_new_terminal)
        details.append(attach_result.message)
        return ActionResult(attach_result.ok, f"{provider.display_name} ready in tmux session {session_name}", details)
    return ActionResult(True, f"{provider.display_name} ready in tmux session {session_name} for {project}", details)


def print_results(results: list[ActionResult]) -> int:
    code = 0
    for result in results:
        prefix = "OK" if result.ok else "ERR"
        print(f"{prefix}: {result.message}")
        for detail in result.details:
            if detail:
                print(detail)
        if result.ok and result.message.startswith("Local Qwen Codex ready in tmux session "):
            print(local_qwen_startup_copy_line())
        if not result.ok:
            code = 1
    return code


def local_qwen_startup_copy_line() -> str:
    return (
        "Copy into Local Qwen: read only the newest user request and the named files needed for the task; "
        "use bounded shell commands, avoid broad reads, and answer `STARTUP_READ_COMPACT_OK` after orientation."
    )


def health_to_dict(health: HealthCheckResult) -> dict[str, Any]:
    return {
        "state": health.state,
        "endpoint_responding": health.endpoint_responding,
        "port_occupied": health.port_occupied,
        "url": health.url,
        "status_code": health.status_code,
        "error": health.error,
        "models": health.models,
    }


def tmux_window_to_dict(window: TmuxWindowState) -> dict[str, Any]:
    return {
        "name": window.name,
        "exists": window.exists,
        "live": window.live,
        "pane_dead": window.pane_dead,
        "command": window.command,
        "count": window.count,
    }


def tmux_session_to_dict(session: TmuxSessionState) -> dict[str, Any]:
    return {
        "name": session.name,
        "exists": session.exists,
        "duplicate_sessions": session.duplicate_sessions,
        "windows": {name: tmux_window_to_dict(window) for name, window in session.windows.items()},
    }


def service_model_summary(cfg: StackConfig, state: ServiceRuntimeState) -> dict[str, Any]:
    """Separate the loaded backend model from proxy-advertised aliases."""
    advertised = list(state.health.models)
    active_profile = active_backend_profile(cfg)
    if state.definition.name == "textgen":
        summary = advertised[0] if advertised else active_profile.model_alias
        return {
            "kind": "loaded_backend",
            "loaded_model": summary,
            "active_alias": active_profile.model_alias,
            "advertised_models": advertised,
            "models_are_loaded": True,
            "summary": summary,
        }
    if state.definition.name in {"litellm", "bridge"}:
        return {
            "kind": "proxy_aliases",
            "loaded_model": "",
            "active_alias": active_profile.model_alias,
            "advertised_models": advertised,
            "models_are_loaded": False,
            "summary": f"proxy exposes active alias {active_profile.model_alias}; {len(advertised)} aliases listed, not loaded models",
        }
    summary = ", ".join(advertised[:3]) if advertised else state.last_error
    return {
        "kind": "endpoint_models",
        "loaded_model": advertised[0] if len(advertised) == 1 else "",
        "active_alias": active_profile.model_alias,
        "advertised_models": advertised,
        "models_are_loaded": len(advertised) == 1,
        "summary": summary,
    }


def service_state_to_dict(cfg: StackConfig, state: ServiceRuntimeState) -> dict[str, Any]:
    service = state.definition
    return {
        "name": service.name,
        "display_name": service.display_name,
        "provider_type": service.provider_type,
        "runtime_state": state.runtime_state,
        "last_error": state.last_error,
        "port": service.port,
        "health_url": service.health_url,
        "models_url": service.models_url,
        "tmux_window": tmux_window_to_dict(state.tmux_window),
        "health": health_to_dict(state.health),
        "model_summary": service_model_summary(cfg, state),
    }


def provider_to_dict(provider: ProviderProfile) -> dict[str, Any]:
    return {
        "name": provider.name,
        "display_name": provider.display_name,
        "type": provider.type,
        "base_url": provider.base_url,
        "base_url_env": provider.base_url_env,
        "models_url": provider.models_url,
        "models_url_env": provider.models_url_env,
        "tmux_session": provider.tmux_session,
        "window_name": provider.window_name,
        "open_new_terminal": provider.open_new_terminal,
        "auto_start_services": provider.auto_start_services,
        "requires_services": provider.requires_services,
        "secret_env": provider.secret_env,
        "allow_codex_auth_file": provider.allow_codex_auth_file,
        "models": provider_models(provider),
    }


def model_to_dict(model: ModelProfile) -> dict[str, Any]:
    return {"name": model.name, "provider": model.provider, "default": model.default}


def backend_profile_to_dict(profile: BackendProfile, *, active: bool = False, cfg: StackConfig | None = None) -> dict[str, Any]:
    data = {
        "name": profile.name,
        "display_name": profile.display_name,
        "backend_kind": profile.backend_kind,
        "model_alias": profile.model_alias,
        "model_path": profile.model_path,
        "model_exists": bool(profile.model_path and Path(profile.model_path).exists()) if profile.model_path else None,
        "mtp_expected": profile.mtp_expected,
        "notes": profile.notes,
        "active": active,
        "role_hint": model_role_hint(profile),
    }
    if cfg is not None:
        data["verification"] = verification_summary(cfg, profile)
    return data


def plan_to_dict(plan: ReconcilePlan) -> dict[str, Any]:
    return {
        "actions": plan.actions,
        "warnings": plan.warnings,
        "destructive_actions": plan.destructive_actions,
        "requires_force": bool(plan.destructive_actions),
    }


def action_result_to_dict(result: ActionResult) -> dict[str, Any]:
    return {"ok": result.ok, "message": result.message, "details": result.details}


def transcript_target_to_dict(target: TranscriptTarget) -> dict[str, Any]:
    return {
        "runtime_dir": str(target.runtime_dir),
        "transcript_dir": str(target.transcript_dir),
        "prompt_template_roots": [str(path) for path in target.prompt_template_roots],
        "task_prompt_policy": target.task_prompt_policy,
    }


def stack_state_to_dict(cfg: StackConfig) -> dict[str, Any]:
    session, states = collect_runtime(cfg)
    return {
        "schema_version": 1,
        "name": cfg.name,
        "config": str(cfg.raw_path),
        "repo_root": str(cfg.repo_root),
        "project": str(ProjectStore(cfg).current()),
        "active_backend_profile": cfg.active_backend_profile,
        "tmux": tmux_session_to_dict(session),
        "services": [service_state_to_dict(cfg, state) for state in states],
        "providers": [provider_to_dict(provider) for provider in cfg.provider_profiles],
        "provider_sessions": provider_sessions_to_dict(cfg),
        "models": [model_to_dict(model) for model in cfg.model_profiles],
        "backend_profiles": [
            backend_profile_to_dict(profile, active=profile.name == cfg.active_backend_profile, cfg=cfg)
            for profile in cfg.backend_profiles
        ],
        "reconcile_plan": plan_to_dict(build_reconcile_plan(cfg)),
        "transcript_target": transcript_target_to_dict(transcript_target(cfg)),
        "config_errors": config_errors(cfg),
        "chat_interfaces": cfg.chat_interfaces,
        "gpu": current_gpu_summary(),
    }


def print_json(data: Any) -> None:
    print(json.dumps(data, indent=2, sort_keys=True))


def script_module(module_name: str) -> Any:
    module_path = ROOT / "scripts" / f"{module_name}.py"
    spec = importlib.util.spec_from_file_location(f"{module_name}_runtime", module_path)
    if spec is None or spec.loader is None:
        raise StackError(f"cannot load script module: {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def harness_ledger_module() -> Any:
    return script_module("local_qwen_harness_ledger")


def harness_ledger_summary() -> dict[str, Any]:
    module = harness_ledger_module()
    return module.ledger_summary(module.DEFAULT_DB_PATH)


def skillopt_summary(project: Path) -> dict[str, Any]:
    try:
        module = script_module("local_qwen_skillopt_wrapper")
        return module.doctor_summary(project)
    except (OSError, StackError) as exc:
        return {"available": False, "error": str(exc), "adopt_exposed": False}


def harness_ledger_action(
    cfg: StackConfig,
    action: str,
    *,
    paths: list[Path] | None = None,
    limit: int = 1000,
    kind: str = "",
    marker: str = "",
    path_contains: str = "",
    note: str = "",
    run_id: str = "",
) -> dict[str, Any]:
    module = harness_ledger_module()
    if action == "init":
        conn = module.connect(module.DEFAULT_DB_PATH)
        module.init_db(conn)
        return module.ledger_summary(module.DEFAULT_DB_PATH)
    if action == "index":
        return module.index_paths(
            module.DEFAULT_DB_PATH,
            cfg.repo_root,
            paths or None,
            source="scripts/llm",
            note=note,
            limit=limit,
        )
    if action == "query":
        return module.query_artifacts(
            module.DEFAULT_DB_PATH,
            limit=limit,
            kind=kind,
            marker=marker,
            path_contains=path_contains,
        )
    if action == "summary":
        return module.ledger_summary(module.DEFAULT_DB_PATH)
    if action == "explain":
        return module.explain_run(module.DEFAULT_DB_PATH, run_id=run_id, limit=limit)
    raise StackError(f"unknown harness ledger action: {action}")


def harness_eval_action(
    cfg: StackConfig,
    *,
    case_id: str = "",
    run_all: bool = False,
    live: bool = False,
    results_root: Path | None = None,
) -> dict[str, Any]:
    module = script_module("local_qwen_harness_eval")
    ledger = harness_ledger_module()
    return module.run_harness_eval(
        repo_root=cfg.repo_root,
        results_root=results_root or module.DEFAULT_RESULTS_ROOT,
        ledger_db_path=ledger.DEFAULT_DB_PATH,
        case_id=case_id,
        run_all=run_all,
        live=live,
    )


def harness_gate_action(cfg: StackConfig) -> dict[str, Any]:
    module = script_module("local_qwen_harness_gate")
    return module.run_harness_gate(cfg.repo_root)


def hook_audit_action(cfg: StackConfig) -> dict[str, Any]:
    module = script_module("local_qwen_hook_audit")
    return module.audit_hooks(project_root=cfg.repo_root)


def skillopt_action(
    cfg: StackConfig,
    *,
    action: str,
    backend: str = "",
    source: str = "",
    allow_codex_budget: bool = False,
    json_output: bool = False,
) -> dict[str, Any]:
    module = script_module("local_qwen_skillopt_wrapper")
    return module.run_skillopt_action(
        action,
        project=cfg.repo_root,
        backend=backend,
        source=source,
        json_output=json_output,
        allow_codex_budget=allow_codex_budget,
    )


def print_harness_ledger(data: dict[str, Any]) -> None:
    print(f"status: {data.get('status', 'unknown')}")
    print(f"db: {data.get('db_path', '')}")
    if data.get("status") != "ready":
        print("canonical: receipt files and transcripts on disk")
        return
    counts = data.get("counts", {})
    print(
        "counts: "
        f"{counts.get('ingest_batches', 0)} batches, "
        f"{counts.get('artifact_observations', 0)} artifacts, "
        f"{counts.get('failure_marker_observations', 0)} marker rows"
    )
    latest = data.get("latest_batch") or {}
    if latest:
        print(f"latest batch: {latest.get('id')} at {latest.get('started_at')}")
    markers = data.get("failure_markers") or {}
    if markers:
        print("failure markers:")
        for marker, count in markers.items():
            print(f"  {marker}: {count}")
    if data.get("rows") is not None:
        for row in data.get("rows", []):
            print(f"{row['id']}: {row['path']} [{row['artifact_kind']}]")


def dashboard_text(cfg: StackConfig) -> str:
    session, states = collect_runtime(cfg)
    projects = ProjectStore(cfg)
    active_profile = active_backend_profile(cfg)
    active_perf = verification_summary(cfg, active_profile)
    gpu = current_gpu_summary()
    loaded_backend = ""
    for state in states:
        if state.definition.name == "textgen":
            loaded_backend = service_model_summary(cfg, state)["summary"]
            break
    lines = [
        cfg.name,
        "=" * len(cfg.name),
        f"config: {cfg.raw_path}",
        f"project: {projects.current()}",
        f"tmux: {cfg.tmux_session} ({'running' if session.exists else 'missing'})",
    ]
    lines.append("")
    lines.append("Active backend")
    lines.append("-" * 14)
    lines.append(f"profile: {active_profile.name} ({active_profile.backend_kind})")
    lines.append(f"display: {active_profile.display_name}")
    lines.append(f"alias:   {active_profile.model_alias}")
    if loaded_backend:
        lines.append(f"loaded:  {loaded_backend}")
    lines.append(f"MTP:     {yn_unknown(active_profile.mtp_expected)}")
    hint = model_role_hint(active_profile)
    if hint:
        lines.append(f"role:    {hint}")
    lines.append(f"receipt: {format_perf(active_perf)}")
    if gpu.get("available"):
        lines.append(
            f"GPU:     {gpu['name']} | used {gpu['memory_used_mb']}MB / {gpu['memory_total_mb']}MB | "
            f"free {gpu['memory_free_mb']}MB | util {gpu['gpu_utilization_percent']}%"
        )
    elif gpu.get("error"):
        lines.append(f"GPU:     unavailable ({gpu['error']})")
    lines.append("")
    lines.append("Provider sessions")
    lines.append("-" * 17)
    for item in provider_sessions_to_dict(cfg):
        if item["provider"] not in {"local-qwen-codex", "normal-codex"}:
            continue
        attach = f"tmux attach-session -t {item['session']}"
        lines.append(f"{item['provider']:<18} {item['state']:<24} {attach}")
    if session.duplicate_sessions:
        lines.append("")
        lines.append("warnings: duplicate-looking sessions: " + ", ".join(session.duplicate_sessions))
    errors = config_errors(cfg)
    if errors:
        lines.append("config errors: " + "; ".join(errors))
    lines.append("")
    lines.append("Managed services")
    lines.append("-" * 16)
    lines.append(f"{'service':<10} {'state':<20} {'tmux':<10} {'port':<6} {'model / aliases'}")
    lines.append("-" * 78)
    table_width = terminal_width()
    for state in states:
        service = state.definition
        tmux_state = "live" if state.tmux_window.live else "stale" if state.tmux_window.exists else "missing"
        model_summary = service_model_summary(cfg, state)["summary"]
        if not model_summary and state.last_error:
            model_summary = state.last_error[:42]
        model_summary = trunc(model_summary, max(16, table_width - 52))
        lines.append(f"{service.name:<10} {state.runtime_state:<20} {tmux_state:<10} {service.port:<6} {model_summary}")
    plan = build_reconcile_plan(cfg)
    if plan.warnings:
        lines.append("")
        lines.append("Warnings:")
        lines.extend(f"- {item}" for item in plan.warnings)
    return "\n".join(lines)


def print_models(cfg: StackConfig) -> None:
    for provider in cfg.provider_profiles:
        models = provider_models(provider)
        print(f"{provider.display_name} [{provider.name}]")
        if models:
            for model in models:
                print(f"  - {model}")
        else:
            print("  - no models reported or endpoint unavailable")


def print_model_overview(cfg: StackConfig) -> None:
    print("Loadable backend profiles")
    print("=========================")
    print_backend_profiles(cfg)
    print("")
    print("Provider-advertised models")
    print("==========================")
    print_models(cfg)
    print("")
    print("Note: TextGen is the loaded backend. LiteLLM and the bridge advertise aliases; those aliases are not separate running models.")


def models_to_dict(cfg: StackConfig) -> dict[str, Any]:
    return {
        "providers": [provider_to_dict(provider) for provider in cfg.provider_profiles],
        "model_profiles": [model_to_dict(model) for model in cfg.model_profiles],
        "backend_profiles": [
            backend_profile_to_dict(profile, active=profile.name == cfg.active_backend_profile, cfg=cfg)
            for profile in cfg.backend_profiles
        ],
    }


def print_backend_profiles(cfg: StackConfig) -> None:
    table_width = terminal_width()
    print(f"active: {cfg.active_backend_profile}")
    print(f"default: {cfg.default_backend_profile}")
    print(f"{'sel':<3} {'profile':<28} {'kind':<12} {'mtp':<3} {'vis':<4} {'file':<7} {'verified':<28} {'alias / role'}")
    print("-" * min(table_width, 132))
    for profile in cfg.backend_profiles:
        marker = "*" if profile.name == cfg.active_backend_profile else "D" if profile.name == cfg.default_backend_profile else " "
        exists = "-"
        if profile.model_path:
            exists = "present" if Path(profile.model_path).exists() else "missing"
        mtp = "Y" if profile.mtp_expected is True else "N" if profile.mtp_expected is False else "?"
        vision = backend_profile_vision_flag(profile)
        perf = format_perf(verification_summary(cfg, profile))
        role = model_role_hint(profile)
        detail = profile.model_alias or profile.display_name
        if role:
            detail = f"{detail} | {role}"
        detail = trunc(detail, max(12, table_width - 94))
        print(
            f"{marker:<3} {trunc(profile.name, 28):<28} {trunc(profile.backend_kind, 12):<12} "
            f"{mtp:<3} {vision:<4} {exists:<7} {trunc(perf, 28):<28} {detail}"
        )


def choose_backend_profile(cfg: StackConfig) -> BackendProfile | None:
    print("Backend model profiles")
    print("======================")
    print(f"active: {cfg.active_backend_profile}")
    print(f"default: {cfg.default_backend_profile}")
    table_width = terminal_width()
    print(f"{'#':<4} {'sel':<3} {'profile':<28} {'kind':<12} {'mtp':<3} {'vis':<4} {'file':<7} {'verified':<24} {'alias / role'}")
    print("-" * min(table_width, 132))
    for idx, profile in enumerate(cfg.backend_profiles, 1):
        marker = "*" if profile.name == cfg.active_backend_profile else "D" if profile.name == cfg.default_backend_profile else " "
        mtp = "Y" if profile.mtp_expected is True else "N" if profile.mtp_expected is False else "?"
        vision = backend_profile_vision_flag(profile)
        exists = "-"
        if profile.model_path:
            exists = "present" if Path(profile.model_path).exists() else "missing"
        role = model_role_hint(profile)
        perf = format_perf(verification_summary(cfg, profile))
        detail = profile.model_alias or profile.display_name
        if role:
            detail = f"{detail} | {role}"
        detail = trunc(detail, max(12, table_width - 95))
        print(
            f"{idx:<4} {marker:<3} {trunc(profile.name, 28):<28} {trunc(profile.backend_kind, 12):<12} "
            f"{mtp:<3} {vision:<4} {exists:<7} {trunc(perf, 24):<24} {detail}"
        )
    print("  b) back")
    choice = input("Choice: ").strip().lower()
    if choice in {"", "b"}:
        return None
    if choice.isdigit():
        index = int(choice) - 1
        if 0 <= index < len(cfg.backend_profiles):
            return cfg.backend_profiles[index]
    raise StackError("Invalid model choice.")


def backend_profiles_to_dict(cfg: StackConfig) -> dict[str, Any]:
    return {
        "active_backend_profile": cfg.active_backend_profile,
        "default_backend_profile": cfg.default_backend_profile,
        "context_presets": cfg.context_presets,
        "backend_profiles": [
            backend_profile_to_dict(profile, active=profile.name == cfg.active_backend_profile, cfg=cfg)
            for profile in cfg.backend_profiles
        ],
    }


def missing_backend_profiles(cfg: StackConfig) -> list[BackendProfile]:
    return [profile for profile in cfg.backend_profiles if profile.model_path and not Path(profile.model_path).exists()]


def local_qwen_contract_status(cfg: StackConfig) -> dict[str, Any]:
    script = cfg.repo_root / "scripts" / "validate_local_qwen_project_launchers.py"
    if not script.exists():
        return {
            "status": "skipped",
            "validator": str(script),
            "failures": [],
            "notes": ["no cross-project launcher validator is configured in this checkout"],
        }
    result = run(["python3", str(script), "--json"], cwd=cfg.repo_root, timeout=30)
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {
            "status": "fail",
            "validator": str(script),
            "returncode": result.returncode,
            "failures": [
                "local-Qwen contract validator did not emit JSON",
                result.stderr.strip(),
                result.stdout.strip(),
            ],
        }
    payload["validator"] = str(script)
    payload["returncode"] = result.returncode
    if result.returncode != 0 and not payload.get("failures"):
        payload["failures"] = [result.stderr.strip() or "validator exited non-zero"]
    return payload


def profile_audit_to_dict(cfg: StackConfig) -> dict[str, Any]:
    session, states = collect_runtime(cfg)
    active_profile = active_backend_profile(cfg)
    default_profile = backend_profile_by_name(cfg, cfg.default_backend_profile)
    missing_profiles = missing_backend_profiles(cfg)
    missing_names = {profile.name for profile in missing_profiles}
    missing_aliases = {profile.model_alias for profile in missing_profiles if profile.model_alias}
    remaining_aliases = {
        profile.model_alias
        for profile in cfg.backend_profiles
        if profile.name not in missing_names and profile.model_alias
    }
    stale_model_profiles = [
        model for model in cfg.model_profiles if model.name in missing_aliases and model.name not in remaining_aliases
    ]
    service_rows = []
    for state in states:
        service_rows.append(
            {
                "name": state.definition.name,
                "runtime_state": state.runtime_state,
                "health": state.health.state,
                "port": state.definition.port,
                "model_summary": service_model_summary(cfg, state)["summary"],
            }
        )
    return {
        "schema_version": 1,
        "repo_root": str(cfg.repo_root),
        "config": str(cfg.raw_path),
        "canonical_launcher": WINDOWS_LAUNCHER,
        "repo_launcher": str(REPO_WINDOWS_LAUNCHER),
        "tmux": {"session": cfg.tmux_session, "exists": session.exists, "duplicates": session.duplicate_sessions},
        "active_backend_profile": backend_profile_to_dict(active_profile, active=True, cfg=cfg),
        "default_backend_profile": backend_profile_to_dict(default_profile, active=default_profile.name == active_profile.name, cfg=cfg),
        "counts": {
            "backend_profiles": len(cfg.backend_profiles),
            "model_profiles": len(cfg.model_profiles),
            "provider_profiles": len(cfg.provider_profiles),
            "missing_backend_profiles": len(missing_profiles),
            "stale_model_profiles": len(stale_model_profiles),
        },
        "services": service_rows,
        "local_qwen_contract": local_qwen_contract_status(cfg),
        "harness_ledger": harness_ledger_summary(),
        "skillopt": skillopt_summary(cfg.repo_root),
        "missing_backend_profiles": [backend_profile_to_dict(profile, cfg=cfg) for profile in missing_profiles],
        "stale_model_profiles": [model_to_dict(model) for model in stale_model_profiles],
        "warnings": config_errors(cfg),
        "recommended_commands": [
            WINDOWS_LAUNCHER,
            str(REPO_WINDOWS_LAUNCHER),
            f"{WINDOWS_LAUNCHER} status",
            f"{WINDOWS_LAUNCHER} doctor",
            f"{WINDOWS_LAUNCHER} model-list",
            f"{WINDOWS_LAUNCHER} model-reload --concurrency 2",
            f"{WINDOWS_LAUNCHER} model-prune-missing",
        ],
    }


def print_profile_audit(cfg: StackConfig) -> None:
    audit = profile_audit_to_dict(cfg)
    active = audit["active_backend_profile"]
    default = audit["default_backend_profile"]
    print("LLM Stack Doctor")
    print("================")
    print(f"repo:     {audit['repo_root']}")
    print(f"config:   {audit['config']}")
    print(f"launcher: {audit['canonical_launcher']}")
    print(f"tmux:     {audit['tmux']['session']} ({'running' if audit['tmux']['exists'] else 'missing'})")
    print("")
    print("Profiles")
    print("--------")
    print(f"active:  {active['name']} | {active['backend_kind']} | {active['model_alias']}")
    print(f"default: {default['name']} | {default['backend_kind']} | {default['model_alias']}")
    print(
        "counts:  "
        f"{audit['counts']['backend_profiles']} backends, "
        f"{audit['counts']['model_profiles']} aliases, "
        f"{audit['counts']['provider_profiles']} providers"
    )
    print("")
    print("Services")
    print("--------")
    for service in audit["services"]:
        summary = trunc(str(service["model_summary"]), 72)
        print(f"{service['name']:<8} {service['runtime_state']:<20} {service['health']:<10} :{service['port']:<5} {summary}")
    print("")
    print("Local Qwen contract")
    print("-------------------")
    contract = audit["local_qwen_contract"]
    print(f"status: {contract.get('status', 'unknown')}")
    print(f"validator: {contract.get('validator', '')}")
    if contract.get("agents_file"):
        print(f"agents: {contract['agents_file']}")
    if contract.get("failures"):
        print("failures:")
        for failure in contract["failures"]:
            if failure:
                print(f"  - {failure}")
    print("")
    print("Harness ledger")
    print("--------------")
    ledger = audit["harness_ledger"]
    print(f"status: {ledger.get('status', 'unknown')}")
    print(f"db: {ledger.get('db_path', '')}")
    if ledger.get("status") == "ready":
        counts = ledger.get("counts", {})
        print(
            "counts: "
            f"{counts.get('ingest_batches', 0)} batches, "
            f"{counts.get('artifact_observations', 0)} artifacts, "
            f"{counts.get('failure_marker_observations', 0)} marker rows"
        )
    print("")
    print("SkillOpt")
    print("--------")
    skillopt = audit.get("skillopt", {})
    print(f"available: {skillopt.get('available', False)}")
    print(f"safe backend: {skillopt.get('safe_default_backend', 'mock')}")
    print(f"staged proposals: {skillopt.get('staged_proposals', 0)}")
    print("")
    print("Cleanup")
    print("-------")
    if audit["missing_backend_profiles"]:
        print("missing backend profiles:")
        for profile in audit["missing_backend_profiles"]:
            print(f"  - {profile['name']} -> {profile['model_path']}")
        print("run dry-run/apply:")
        print("  scripts/llm model-prune-missing")
        print("  scripts/llm model-prune-missing --apply")
    else:
        print("missing backend profiles: none")
    if audit["stale_model_profiles"]:
        print("stale model aliases:")
        for model in audit["stale_model_profiles"]:
            print(f"  - {model['name']} ({model['provider']})")
    if audit["warnings"]:
        print("")
        print("Warnings")
        print("--------")
        for warning in audit["warnings"]:
            print(f"- {warning}")
    print("")
    print("Common commands")
    print("---------------")
    for command in audit["recommended_commands"][:5]:
        print(f"- {command}")


def prune_missing_backend_profiles(cfg: StackConfig, *, apply: bool = False) -> ActionResult:
    missing_profiles = missing_backend_profiles(cfg)
    if not missing_profiles:
        return ActionResult(True, "no missing backend profiles found")
    protected = {cfg.active_backend_profile, cfg.default_backend_profile}
    protected_missing = [profile.name for profile in missing_profiles if profile.name in protected]
    if protected_missing:
        return ActionResult(False, "refusing to prune active/default missing backend profile(s)", protected_missing)

    missing_names = {profile.name for profile in missing_profiles}
    missing_aliases = {profile.model_alias for profile in missing_profiles if profile.model_alias}
    remaining_aliases = {
        profile.model_alias
        for profile in cfg.backend_profiles
        if profile.name not in missing_names and profile.model_alias
    }
    stale_aliases = missing_aliases - remaining_aliases
    details = [f"backend: {profile.name} -> {profile.model_path}" for profile in missing_profiles]
    details.extend(f"alias: {alias}" for alias in sorted(stale_aliases))
    if not apply:
        return ActionResult(True, f"dry-run: would prune {len(missing_names)} missing backend profile(s)", details)

    data = json.loads(cfg.raw_path.read_text())
    data["backend_profiles"] = [
        item for item in data.get("backend_profiles", []) if item.get("name") not in missing_names
    ]
    data["model_profiles"] = [
        item for item in data.get("model_profiles", []) if item.get("name") not in stale_aliases
    ]
    cfg.raw_path.write_text(json.dumps(data, indent=2) + "\n")
    return ActionResult(True, f"pruned {len(missing_names)} missing backend profile(s)", details)


def set_backend_profile(cfg: StackConfig, profile_name: str) -> ActionResult:
    profile = backend_profile_by_name(cfg, profile_name)
    if profile.model_path and not Path(profile.model_path).exists():
        return ActionResult(False, f"model file is missing for backend profile {profile_name}: {profile.model_path}")
    ProjectStore(cfg).set_active_backend_profile(profile_name)
    return ActionResult(True, f"active backend profile set to {profile_name}")


def set_default_backend_profile(cfg: StackConfig, profile_name: str, *, also_active: bool = False) -> ActionResult:
    profile = backend_profile_by_name(cfg, profile_name)
    if profile.model_path and not Path(profile.model_path).exists():
        return ActionResult(False, f"model file is missing for backend profile {profile_name}: {profile.model_path}")
    ProjectStore(cfg).set_default_backend_profile(profile_name, also_active=also_active)
    suffix = " and active backend profile" if also_active else ""
    return ActionResult(True, f"default{suffix} set to {profile_name}")


def reload_backend_profile(cfg: StackConfig, profile_name: str) -> list[ActionResult]:
    result = set_backend_profile(cfg, profile_name)
    if not result.ok:
        return [result]
    reloaded_cfg = load_config(cfg.raw_path)
    results = [result]
    results.extend(restart_services(reloaded_cfg, "all"))
    return results


def config_with_textgen_env_overrides(cfg: StackConfig, env: dict[str, str]) -> StackConfig:
    services: list[ServiceDefinition] = []
    for service in cfg.services:
        if service.name == "textgen":
            backend_env = {
                key: value
                for key, value in env.items()
                if (
                    key.startswith("TEXTGEN_")
                    or key.startswith("VLLM_")
                    or key.startswith("LLAMACPP_")
                    or key.startswith("KOBOLDCPP_")
                )
            }
            services.append(replace(service, command=command_with_env_overrides(service.command, backend_env)) if backend_env else service)
            continue
        if service.name == "bridge":
            bridge_env = {
                key: value
                for key, value in env.items()
                if key.startswith("CODEX_TEXTGEN_")
                or key
                in {
                    "LOCAL_QWEN_GUARD_PROFILE",
                    "LOCAL_QWEN_CODEX_MAX_TOOL_CALLS",
                    "LOCAL_QWEN_CODEX_MAX_WALL_TIME_SECONDS",
                }
            }
            services.append(replace(service, command=command_with_env_overrides(service.command, bridge_env)) if bridge_env else service)
            continue
        if service.name != "textgen":
            services.append(service)
            continue
    return replace(cfg, services=services)


def reload_backend_profile_custom(cfg: StackConfig, profile_name: str, custom: CustomTextgenOverride) -> list[ActionResult]:
    result = set_backend_profile(cfg, profile_name)
    if not result.ok:
        return [result]
    reloaded_cfg = load_config(cfg.raw_path)
    effective_cfg = config_with_textgen_env_overrides(reloaded_cfg, custom.env)
    details = [custom.description] if custom.description else []
    results = [ActionResult(True, f"active backend profile set to {profile_name} with temporary backend overrides", details)]
    results.extend(restart_services(effective_cfg, "all"))
    return results


def parse_extra_env_assignments(raw: str) -> dict[str, str]:
    env: dict[str, str] = {}
    if not raw.strip():
        return env
    try:
        parts = shlex.split(raw)
    except ValueError as exc:
        raise StackError(f"Invalid extra env syntax: {exc}") from exc
    for part in parts:
        if "=" not in part:
            raise StackError(f"Expected KEY=value assignment, got: {part}")
        key, value = part.split("=", 1)
        if not key or not all(ch.isalnum() or ch == "_" for ch in key):
            raise StackError(f"Invalid env key: {key}")
        env[key] = value
    return env


def prompt_custom_textgen_overrides(profile: BackendProfile) -> CustomTextgenOverride | None:
    override = profile.service_overrides.get("textgen", {})
    command = str(override.get("command", "scripts/run_textgen_qwen_exl3.sh"))
    current = command_env_assignments(command)
    fields = [
        ("TEXTGEN_MODEL", "model"),
        ("TEXTGEN_LOADER", "loader"),
        ("TEXTGEN_CACHE_TYPE", "cache type"),
        ("TEXTGEN_CTX_SIZE", "context tokens"),
        ("TEXTGEN_GPU_SPLIT", "GPU split"),
        ("TEXTGEN_EXLLAMAV3_MAX_CHUNK_SIZE", "ExLlamaV3 prefill chunk"),
        ("TEXTGEN_EXLLAMAV3_MAX_BATCH_SIZE", "ExLlamaV3 max batch"),
        ("TEXTGEN_EXLLAMAV3_MAX_Q_SIZE", "ExLlamaV3 max q"),
        ("TEXTGEN_MODEL_DIR", "model dir"),
        ("TEXTGEN_CHAT_TEMPLATE", "chat template"),
        ("TEXTGEN_MMPROJ", "mmproj"),
        ("TEXTGEN_SPEC_TYPE", "spec type"),
        ("TEXTGEN_MODEL_DRAFT", "draft model"),
        ("TEXTGEN_DRAFT_MAX", "draft tokens"),
        ("TEXTGEN_TOP_K", "top-k"),
        ("TEXTGEN_TOP_P", "top-p"),
        ("TEXTGEN_MIN_P", "min-p"),
        ("TEXTGEN_TEMPERATURE", "temperature"),
        ("TEXTGEN_REPETITION_PENALTY", "repetition penalty"),
        ("TEXTGEN_PRESENCE_PENALTY", "presence penalty"),
        ("TEXTGEN_FREQUENCY_PENALTY", "frequency penalty"),
        ("TEXTGEN_ENABLE_THINKING", "enable thinking"),
        ("TEXTGEN_PRESERVE_THINKING", "preserve thinking"),
        ("VLLM_MODEL_PATH", "vLLM GGUF path"),
        ("VLLM_TOKENIZER", "vLLM tokenizer"),
        ("VLLM_MAX_MODEL_LEN", "vLLM context tokens"),
        ("VLLM_GPU_MEMORY_UTILIZATION", "vLLM GPU memory utilization"),
        ("VLLM_DTYPE", "vLLM dtype"),
        ("VLLM_CHAT_TEMPLATE", "vLLM chat template"),
        ("VLLM_EXTRA_ARGS", "vLLM extra args"),
        ("LLAMACPP_MODEL_PATH", "llama.cpp GGUF path"),
        ("LLAMACPP_CTX_SIZE", "llama.cpp context tokens"),
        ("LLAMACPP_CACHE_TYPE_K", "llama.cpp K cache type"),
        ("LLAMACPP_CACHE_TYPE_V", "llama.cpp V cache type"),
        ("LLAMACPP_CACHE_PROMPT", "llama.cpp prompt cache"),
        ("LLAMACPP_CACHE_RAM", "llama.cpp prompt cache RAM MiB"),
        ("LLAMACPP_PARALLEL", "llama.cpp slots"),
        ("LLAMACPP_BATCH_SIZE", "llama.cpp batch"),
        ("LLAMACPP_UBATCH_SIZE", "llama.cpp ubatch"),
        ("LLAMACPP_GPU_LAYERS", "llama.cpp GPU layers"),
        ("LLAMACPP_TEMPERATURE", "llama.cpp temperature"),
        ("LLAMACPP_TOP_K", "llama.cpp top-k"),
        ("LLAMACPP_TOP_P", "llama.cpp top-p"),
        ("LLAMACPP_MIN_P", "llama.cpp min-p"),
        ("LLAMACPP_REASONING", "llama.cpp reasoning mode"),
        ("LLAMACPP_REASONING_FORMAT", "llama.cpp reasoning format"),
        ("LLAMACPP_REASONING_BUDGET", "llama.cpp reasoning budget"),
        ("CODEX_TEXTGEN_TOOL_TEMPERATURE", "bridge tool temperature"),
        ("LLAMACPP_EXTRA_ARGS", "llama.cpp extra args"),
    ]
    print("Temporary backend overrides")
    print("===========================")
    print("Blank keeps the selected profile value/default. These values are used for this reload only.")
    print("")
    env: dict[str, str] = {}
    for key, label in fields:
        default = current.get(key, "")
        prompt = f"{label} [{default or 'default'}]: "
        value = input(prompt).strip()
        if value:
            env[key] = value
    print("")
    extra = input("Extra env assignments, space-separated KEY=value (blank for none): ").strip()
    env.update(parse_extra_env_assignments(extra))
    if not env:
        print("No custom values entered.")
        return None
    description = "temporary backend env: " + " ".join(f"{key}={value}" for key, value in env.items())
    print("")
    print(description)
    confirm = input("Reload with these temporary values? [y/N] ").strip().lower()
    if confirm != "y":
        return None
    return CustomTextgenOverride(env=env, description=description)


def choose_context_preset(cfg: StackConfig) -> int | None:
    presets = sorted({int(value) for value in cfg.context_presets if int(value) >= 20480})
    if not presets:
        presets = [20480, 24576, 32768, 49152, 65536, 81920, 88064]
    print("Context presets")
    print("===============")
    for idx, value in enumerate(presets, 1):
        label = f"{value // 1024}k" if value % 1024 == 0 else str(value)
        print(f"  {idx}) {label} ({value})")
    print("  b) back")
    choice = input("Choice: ").strip().lower()
    if choice in {"", "b"}:
        return None
    if choice.isdigit():
        index = int(choice) - 1
        if 0 <= index < len(presets):
            return presets[index]
    raise StackError("Invalid context preset choice.")


def reload_backend_profile_with_context(cfg: StackConfig, profile_name: str, context_tokens: int) -> list[ActionResult]:
    if context_tokens < 20480:
        return [ActionResult(False, "context preset must be at least 20480 tokens")]
    profile = backend_profile_by_name(cfg, profile_name)
    if profile.backend_kind in {"vllm-gguf", "llamacpp-gguf", "koboldcpp-gguf"} and context_tokens < 32768:
        return [ActionResult(False, f"{profile.backend_kind} profiles require at least 32768 context tokens")]
    return reload_backend_profile_custom(
        cfg,
        profile_name,
        CustomTextgenOverride(
            env={
                "TEXTGEN_CTX_SIZE": str(context_tokens),
                "VLLM_MAX_MODEL_LEN": str(context_tokens),
                "LLAMACPP_CTX_SIZE": str(context_tokens),
                "KOBOLDCPP_CTX_SIZE": str(context_tokens),
            },
            description=f"temporary backend context preset: TEXTGEN_CTX_SIZE/VLLM_MAX_MODEL_LEN/LLAMACPP_CTX_SIZE/KOBOLDCPP_CTX_SIZE={context_tokens}",
        ),
    )


def concurrency_override_env(concurrency: int | None) -> dict[str, str]:
    if concurrency is None:
        return {}
    if concurrency < 1 or concurrency > 8:
        raise StackError("concurrency must be between 1 and 8")
    return {"LLAMACPP_PARALLEL": str(concurrency)}


def print_reconcile(cfg: StackConfig) -> None:
    plan = build_reconcile_plan(cfg)
    print("Reconcile preview")
    print("=================")
    if plan.actions:
        print("Actions:")
        for action in plan.actions:
            print(f"  - {action}")
    else:
        print("Actions: none")
    if plan.destructive_actions:
        print("Destructive/stale cleanup actions:")
        for action in plan.destructive_actions:
            print(f"  - {action}")
    if plan.warnings:
        print("Warnings:")
        for warning in plan.warnings:
            print(f"  - {warning}")


def print_provider_sessions(cfg: StackConfig) -> None:
    print("Provider sessions")
    print("=================")
    print(f"{'provider':<18} {'state':<24} {'session':<20} {'attach'}")
    print("-" * 88)
    for item in provider_sessions_to_dict(cfg):
        attach = f"tmux attach-session -t {item['session']}"
        print(f"{item['provider']:<18} {item['state']:<24} {item['session']:<20} {attach}")
        configured = item.get("configured_command")
        if configured and configured != item.get("command"):
            print(f"  configured: {configured}")
            print(f"  expected:   {item.get('command', '')}")
        elif item.get("reason"):
            print(f"  reason: {item['reason']}")


def choose_project(cfg: StackConfig) -> Path:
    store = ProjectStore(cfg)
    while True:
        print("\nProject folder")
        print("==============")
        print(f"current: {store.current()}")
        for idx, path in enumerate(store.recent(), 1):
            print(f"  {idx}) {path}")
        print("  n) enter a folder under ~")
        print("  b) back")
        choice = input("Choice: ").strip()
        if choice.lower() == "b" or choice == "":
            return store.current()
        if choice.lower() == "n":
            path = store.resolve_project_path(input("Folder: "))
            store.set_current(path)
            return store.current()
        if choice.isdigit():
            items = store.recent()
            index = int(choice) - 1
            if 0 <= index < len(items):
                store.set_current(items[index])
                return store.current()


def pause() -> None:
    input("\nPress Enter to continue...")


def run_menu_action(title: str, func) -> None:
    print("\033c", end="")
    print(title)
    print("=" * len(title))
    try:
        func()
    except StackError as exc:
        print(f"ERR: {exc}")
    pause()


def service_choice(cfg: StackConfig) -> str:
    print("Services:")
    for idx, service in enumerate(cfg.services, 1):
        print(f"  {idx}) {service.display_name} ({service.name})")
    print("  a) all")
    choice = input("Choice: ").strip().lower()
    if choice == "a" or choice == "":
        return "all"
    if choice.isdigit():
        index = int(choice) - 1
        if 0 <= index < len(cfg.services):
            return cfg.services[index].name
    raise StackError("Invalid service choice.")


def ui(cfg: StackConfig) -> None:
    while True:
        cfg = load_config(cfg.raw_path)
        print("\033c", end="")
        print(dashboard_text(cfg))
        print(
            """
Quick actions
  1) Reconcile preview        2) Start/reconcile stack     5) Swap/reload backend model
  c) Custom reload selected model       y) Reload active model with concurrency
  8) Open Local Qwen Codex (prints QWENDEX_STARTUP.md prompt)
  f) Fresh Local Qwen Codex    9) Open normal Codex
  w) Open Open WebUI chat

Service tools
  3) Restart service          4) Stop managed services     6) Attach service window
  7) Show service logs        s) Provider session status

Models and config
  m) List provider/backend models
  d) Change default backend model
  x) Reload selected model with context preset
  p) Select project folder
  h) Help/about
  r) Refresh
  q) Quit
"""
        )
        choice = input("Choice: ").strip().lower()
        if choice in {"q", "quit"}:
            return
        if choice in {"r", ""}:
            continue
        if choice == "1":
            run_menu_action("Reconcile preview", lambda cfg=cfg: print_reconcile(cfg))
        elif choice == "2":
            run_menu_action("Start full stack", lambda cfg=cfg: print_results(start_services(cfg, "all", wait=True)))
        elif choice == "3":
            def action(cfg: StackConfig = cfg) -> None:
                target = service_choice(cfg)
                print_results(restart_services(cfg, target))
            run_menu_action("Restart service", action)
        elif choice == "4":
            def action(cfg: StackConfig = cfg) -> None:
                target = service_choice(cfg)
                confirm = input(f"Stop {target}? [y/N] ").strip().lower()
                if confirm == "y":
                    print_results(stop_services(cfg, target))
            run_menu_action("Stop managed services", action)
        elif choice == "5":
            def action(cfg: StackConfig = cfg) -> None:
                profile = choose_backend_profile(cfg)
                if profile is None:
                    print("No change made.")
                    return
                print("")
                print(f"Selected: {profile.name}")
                print(f"Alias: {profile.model_alias or '(none)'}")
                print("This will restart the model backend, LiteLLM, and the bridge to load the selected profile.")
                confirm = input("Reload now? [y/N] ").strip().lower()
                if confirm == "y":
                    print_results(reload_backend_profile(cfg, profile.name))
                else:
                    print("No change made.")
            run_menu_action("Swap/reload backend model", action)
        elif choice == "c":
            def action(cfg: StackConfig = cfg) -> None:
                profile = choose_backend_profile(cfg)
                if profile is None:
                    print("No change made.")
                    return
                print("")
                print(f"Selected: {profile.name}")
                print(f"Alias: {profile.model_alias or '(none)'}")
                custom = prompt_custom_textgen_overrides(profile)
                if custom is None:
                    print("No change made.")
                    return
                print_results(reload_backend_profile_custom(cfg, profile.name, custom))
            run_menu_action("Custom reload selected model", action)
        elif choice == "y":
            def action(cfg: StackConfig = cfg) -> None:
                raw = input("llama.cpp concurrency slots [1]: ").strip() or "1"
                try:
                    concurrency = int(raw)
                    env = concurrency_override_env(concurrency)
                except (ValueError, StackError) as exc:
                    print(f"Invalid concurrency: {exc}")
                    return
                profile = cfg.active_backend_profile
                print(f"Reload active backend {profile} with LLAMACPP_PARALLEL={concurrency}")
                confirm = input("Reload now? [y/N] ").strip().lower()
                if confirm == "y":
                    print_results(
                        reload_backend_profile_custom(
                            cfg,
                            profile,
                            CustomTextgenOverride(env=env, description=f"temporary backend concurrency: LLAMACPP_PARALLEL={concurrency}"),
                        )
                    )
                else:
                    print("No change made.")
            run_menu_action("Reload active model with concurrency", action)
        elif choice == "d":
            def action(cfg: StackConfig = cfg) -> None:
                profile = choose_backend_profile(cfg)
                if profile is None:
                    print("No change made.")
                    return
                print("")
                print(f"Selected default: {profile.name}")
                print(f"Alias: {profile.model_alias or '(none)'}")
                also_active = input("Also make it active now? [y/N] ").strip().lower() == "y"
                print_results([set_default_backend_profile(cfg, profile.name, also_active=also_active)])
                if also_active:
                    print("Use option 5 to reload services with the new active/default model.")
            run_menu_action("Change default backend model", action)
        elif choice == "x":
            def action(cfg: StackConfig = cfg) -> None:
                profile = choose_backend_profile(cfg)
                if profile is None:
                    print("No change made.")
                    return
                context = choose_context_preset(cfg)
                if context is None:
                    print("No change made.")
                    return
                label = f"{context // 1024}k" if context % 1024 == 0 else str(context)
                print(f"Selected: {profile.name} at {label} context")
                confirm = input("Reload now with this temporary backend context preset? [y/N] ").strip().lower()
                if confirm == "y":
                    print_results(reload_backend_profile_with_context(cfg, profile.name, context))
                else:
                    print("No change made.")
            run_menu_action("Reload selected model with context preset", action)
        elif choice == "6":
            target = service_choice(cfg)
            if target == "all":
                target = "textgen"
            attach_window(cfg, service_by_name(cfg, target).window_name)
        elif choice == "7":
            def action(cfg: StackConfig = cfg) -> None:
                target = service_choice(cfg)
                if target == "all":
                    target = "textgen"
                print(capture_logs(cfg, target))
            run_menu_action("Service logs", action)
        elif choice == "8":
            def action(cfg: StackConfig = cfg) -> None:
                project = ProjectStore(cfg).current()
                print_results([launch_provider(cfg, "local-qwen-codex", project, attach=True)])
            run_menu_action("Open Local Qwen Codex", action)
        elif choice == "f":
            def action(cfg: StackConfig = cfg) -> None:
                project = ProjectStore(cfg).current()
                print("This only resets the Local Qwen Codex tmux session; it does not restart TextGen, LiteLLM, or the bridge.")
                confirm = input("Open a fresh Local Qwen Codex session? [y/N] ").strip().lower()
                if confirm == "y":
                    print_results([launch_provider(cfg, "local-qwen-codex", project, attach=True, fresh=True)])
                else:
                    print("No change made.")
            run_menu_action("Fresh Local Qwen Codex", action)
        elif choice == "9":
            def action(cfg: StackConfig = cfg) -> None:
                project = ProjectStore(cfg).current()
                print_results([launch_provider(cfg, "normal-codex", project, attach=True)])
            run_menu_action("Open normal Codex", action)
        elif choice == "w":
            run_menu_action("Open Open WebUI chat", lambda cfg=cfg: print_results([launch_open_webui(cfg)]))
        elif choice == "p":
            run_menu_action("Select project folder", lambda cfg=cfg: print(f"selected: {choose_project(cfg)}"))
        elif choice == "m":
            run_menu_action("Model overview", lambda cfg=cfg: print_model_overview(cfg))
        elif choice == "s":
            run_menu_action("Provider session status", lambda cfg=cfg: print_provider_sessions(cfg))
        elif choice == "h":
            run_menu_action("Help/about", lambda cfg=cfg: print_help(cfg))


def print_help(cfg: StackConfig) -> None:
    print("Managed by this tool:")
    print(f"  tmux session: {cfg.tmux_session}")
    for service in cfg.services:
        print(f"  - {service.name}: {service.command} on port {service.port}")
    print("\nNot managed by this tool:")
    print("  - science state, result ledgers, Phase files, evidence ledgers")
    print("  - API keys or provider secrets")
    print("  - external/manual processes already occupying configured ports")
    print("\nConfig:")
    print(f"  {cfg.raw_path}")
    print(f"  project list: {cfg.user_state_file}")
    print(f"  runtime dir: {cfg.safe_runtime_dir}")
    print(f"  transcript dir: {cfg.transcript_dir}")
    if cfg.chat_interfaces:
        print("  chat interfaces:")
        for name, interface in cfg.chat_interfaces.items():
            print(f"    {name}: {interface.get('url', '')} -> {interface.get('backend_url', '')}")
    print("\nPrompt policy:")
    print("  Reuse existing repo templates; the stack console does not invent project-specific lane gates.")
    print("\nAttach/detach:")
    print("  Ctrl-b d detaches from tmux and leaves services running.")


def command_line() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local LLM stack operator console")
    parser.add_argument("--config", type=Path, default=None)
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("ui")
    status = sub.add_parser("status")
    status.add_argument("--json", action="store_true")
    doctor = sub.add_parser("doctor")
    doctor.add_argument("--json", action="store_true")
    profile_audit = sub.add_parser("profile-audit")
    profile_audit.add_argument("--json", action="store_true")
    state = sub.add_parser("state")
    state.add_argument("--json", action="store_true")
    reconcile = sub.add_parser("reconcile")
    reconcile.add_argument("--apply", action="store_true")
    reconcile.add_argument("--force", action="store_true")
    reconcile.add_argument("--json", action="store_true")
    start = sub.add_parser("start")
    start.add_argument("service", nargs="?", default="all")
    start.add_argument("--json", action="store_true")
    stop = sub.add_parser("stop")
    stop.add_argument("service", nargs="?", default="all")
    stop.add_argument("--json", action="store_true")
    restart = sub.add_parser("restart")
    restart.add_argument("service", nargs="?", default="all")
    restart.add_argument("--concurrency", type=int, help="temporary llama.cpp request slots for this restart")
    restart.add_argument("--json", action="store_true")
    reset = sub.add_parser("reset")
    reset.add_argument("service", nargs="?", default="all")
    reset.add_argument("--json", action="store_true")
    attach = sub.add_parser("attach")
    attach.add_argument("service", nargs="?", default="textgen")
    logs = sub.add_parser("logs")
    logs.add_argument("service", nargs="?", default="textgen")
    models = sub.add_parser("models")
    models.add_argument("--json", action="store_true")
    sessions = sub.add_parser("sessions")
    sessions.add_argument("--json", action="store_true")
    model_list = sub.add_parser("model-list")
    model_list.add_argument("--json", action="store_true")
    model_prune = sub.add_parser("model-prune-missing")
    model_prune.add_argument("--apply", action="store_true")
    model_prune.add_argument("--json", action="store_true")
    model_use = sub.add_parser("model-use")
    model_use.add_argument("profile")
    model_use.add_argument("--json", action="store_true")
    model_default = sub.add_parser("model-default")
    model_default.add_argument("profile")
    model_default.add_argument("--also-active", action="store_true")
    model_default.add_argument("--json", action="store_true")
    model_reload = sub.add_parser("model-reload")
    model_reload.add_argument("profile", nargs="?")
    model_reload.add_argument("--context", type=int, help="temporary backend context preset, minimum 20480")
    model_reload.add_argument("--concurrency", type=int, help="temporary llama.cpp request slots for this reload")
    model_reload.add_argument("--set", action="append", default=[], metavar="KEY=VALUE", help="temporary backend env override; may be repeated")
    model_reload.add_argument("--json", action="store_true")
    projects = sub.add_parser("projects")
    projects.add_argument("--json", action="store_true")
    transcripts = sub.add_parser("transcripts")
    transcripts.add_argument("--json", action="store_true")
    harness_ledger = sub.add_parser("harness-ledger")
    harness_ledger.add_argument(
        "action",
        choices=["init", "index", "summary", "query", "explain"],
        nargs="?",
        default="summary",
    )
    harness_ledger.add_argument("--path", action="append", type=Path, default=[])
    harness_ledger.add_argument("--limit", type=int, default=1000)
    harness_ledger.add_argument("--kind", default="")
    harness_ledger.add_argument("--marker", default="")
    harness_ledger.add_argument("--path-contains", default="")
    harness_ledger.add_argument("--note", default="")
    harness_ledger.add_argument("--run-id", default="")
    harness_ledger.add_argument("--json", action="store_true")
    harness_eval = sub.add_parser("harness-eval")
    harness_eval.add_argument("--case", default="")
    harness_eval.add_argument("--all", action="store_true")
    harness_eval.add_argument("--live", action="store_true")
    harness_eval.add_argument("--results-root", type=Path)
    harness_eval.add_argument("--json", action="store_true")
    harness_gate = sub.add_parser("harness-gate")
    harness_gate.add_argument("--json", action="store_true")
    hook_audit = sub.add_parser("hook-audit")
    hook_audit.add_argument("--json", action="store_true")
    skillopt = sub.add_parser("skillopt")
    skillopt.add_argument(
        "action",
        choices=["status", "harvest", "dry-run", "run", "schedule", "unschedule", "proposal-summary"],
    )
    skillopt.add_argument("--backend", default="")
    skillopt.add_argument("--source", default="")
    skillopt.add_argument("--allow-codex-budget", action="store_true")
    skillopt.add_argument("--json", action="store_true")
    smoke = sub.add_parser("smoke")
    smoke.add_argument("--json", action="store_true")
    add_project = sub.add_parser("project-add")
    add_project.add_argument("path", type=Path)
    add_project.add_argument("--json", action="store_true")
    local = sub.add_parser("codex-local")
    local.add_argument("--cwd", type=Path)
    local.add_argument("--json", action="store_true")
    local.add_argument("--no-attach", action="store_true")
    local.add_argument("--fresh", action="store_true", help="replace the provider tmux session before opening")
    gpt = sub.add_parser("codex-gpt")
    gpt.add_argument("--cwd", type=Path)
    gpt.add_argument("--json", action="store_true")
    gpt.add_argument("--no-attach", action="store_true")
    gpt.add_argument("--fresh", action="store_true", help="replace the provider tmux session before opening")
    open_webui = sub.add_parser("open-webui")
    open_webui.add_argument("--interface", default="open-webui")
    open_webui.add_argument("--reload-backend", action="store_true", help="activate the interface backend profile before opening Open WebUI")
    open_webui.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = command_line()
    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    command = args.command or "ui"
    try:
        if command == "ui":
            ui(cfg)
            return 0
        if command in {"status", "state"}:
            if args.json:
                print_json(stack_state_to_dict(cfg))
                return 0
            print(dashboard_text(cfg))
            return 0
        if command in {"doctor", "profile-audit"}:
            if args.json:
                print_json(profile_audit_to_dict(cfg))
                return 0
            print_profile_audit(cfg)
            return 0
        if command == "reconcile":
            plan = build_reconcile_plan(cfg)
            if args.json and not args.apply:
                print_json({"reconcile_plan": plan_to_dict(plan)})
                return 0
            print_reconcile(cfg)
            if args.apply:
                if plan.destructive_actions and not args.force:
                    result = ActionResult(False, "reconcile plan contains destructive stale-window cleanup; rerun with --force after review")
                    if args.json:
                        print_json({"reconcile_plan": plan_to_dict(plan), "results": [action_result_to_dict(result)]})
                        return 1
                    return print_results([result])
                results = start_services(cfg, "all", wait=True)
                if args.json:
                    print_json({"reconcile_plan": plan_to_dict(plan), "results": [action_result_to_dict(result) for result in results]})
                    return 0 if all(result.ok for result in results) else 1
                return print_results(results)
            return 0
        if command == "start":
            results = start_services(cfg, args.service, wait=True)
            if args.json:
                print_json({"action": "start", "service": args.service, "results": [action_result_to_dict(result) for result in results]})
                return 0 if all(result.ok for result in results) else 1
            return print_results(results)
        if command == "stop":
            results = stop_services(cfg, args.service)
            if args.json:
                print_json({"action": "stop", "service": args.service, "results": [action_result_to_dict(result) for result in results]})
                return 0 if all(result.ok for result in results) else 1
            return print_results(results)
        if command in {"restart", "reset"}:
            if command == "reset" and args.service == "all":
                results = [reset_stack(cfg)]
            else:
                override_env = concurrency_override_env(getattr(args, "concurrency", None))
                effective_cfg = config_with_textgen_env_overrides(cfg, override_env) if override_env else cfg
                results = restart_services(effective_cfg, args.service)
            if args.json:
                print_json(
                    {
                        "action": command,
                        "service": args.service,
                        "concurrency": getattr(args, "concurrency", None),
                        "results": [action_result_to_dict(result) for result in results],
                    }
                )
                return 0 if all(result.ok for result in results) else 1
            return print_results(results)
        if command == "attach":
            attach_window(cfg, service_by_name(cfg, args.service).window_name)
            return 0
        if command == "logs":
            print(capture_logs(cfg, args.service))
            return 0
        if command == "models":
            if args.json:
                print_json(models_to_dict(cfg))
                return 0
            print_model_overview(cfg)
            return 0
        if command == "sessions":
            data = provider_sessions_to_dict(cfg)
            if args.json:
                print_json({"provider_sessions": data})
                return 0
            print_provider_sessions(cfg)
            return 0
        if command == "model-list":
            if args.json:
                print_json(backend_profiles_to_dict(cfg))
            else:
                print_backend_profiles(cfg)
            return 0
        if command == "model-prune-missing":
            result = prune_missing_backend_profiles(cfg, apply=args.apply)
            if args.json:
                print_json({"action": "model-prune-missing", "apply": args.apply, "result": action_result_to_dict(result)})
                return 0 if result.ok else 1
            return print_results([result])
        if command == "model-use":
            result = set_backend_profile(cfg, args.profile)
            if args.json:
                print_json({"action": "model-use", "profile": args.profile, "result": action_result_to_dict(result)})
                return 0 if result.ok else 1
            return print_results([result])
        if command == "model-default":
            result = set_default_backend_profile(cfg, args.profile, also_active=args.also_active)
            if args.json:
                print_json({"action": "model-default", "profile": args.profile, "also_active": args.also_active, "result": action_result_to_dict(result)})
                return 0 if result.ok else 1
            return print_results([result])
        if command == "model-reload":
            profile_name = args.profile or cfg.active_backend_profile
            override_env: dict[str, str] = {}
            if args.context is not None and args.context < 20480:
                results = [ActionResult(False, "context preset must be at least 20480 tokens")]
            else:
                profile = backend_profile_by_name(cfg, profile_name)
                if args.context is not None:
                    if profile.backend_kind in {"vllm-gguf", "llamacpp-gguf"} and args.context < 32768:
                        results = [ActionResult(False, f"{profile.backend_kind} profiles require at least 32768 context tokens")]
                    else:
                        override_env.update(
                            {
                                "TEXTGEN_CTX_SIZE": str(args.context),
                                "VLLM_MAX_MODEL_LEN": str(args.context),
                                "LLAMACPP_CTX_SIZE": str(args.context),
                            }
                        )
                        results = []
                else:
                    results = []
                if not results:
                    override_env.update(concurrency_override_env(args.concurrency))
                    if args.set:
                        override_env.update(parse_extra_env_assignments(" ".join(args.set)))
                    if override_env:
                        description = "temporary backend env: " + " ".join(f"{key}={value}" for key, value in sorted(override_env.items()))
                        results = reload_backend_profile_custom(cfg, profile_name, CustomTextgenOverride(env=override_env, description=description))
                    else:
                        results = reload_backend_profile(cfg, profile_name)
            if args.json:
                print_json(
                    {
                        "action": "model-reload",
                        "profile": profile_name,
                        "context": args.context,
                        "concurrency": args.concurrency,
                        "set": args.set,
                        "results": [action_result_to_dict(item) for item in results],
                    }
                )
                return 0 if all(item.ok for item in results) else 1
            return print_results(results)
        if command == "projects":
            store = ProjectStore(cfg)
            if args.json:
                print_json({"current": str(store.current()), "recent": [str(path) for path in store.recent()]})
                return 0
            print(f"current: {store.current()}")
            for path in store.recent():
                print(path)
            return 0
        if command == "transcripts":
            target = transcript_target(cfg)
            if args.json:
                print_json(transcript_target_to_dict(target))
            else:
                print(f"runtime_dir: {target.runtime_dir}")
                print(f"transcript_dir: {target.transcript_dir}")
                print("prompt_template_roots:")
                for path in target.prompt_template_roots:
                    print(f"  {path}")
            return 0
        if command == "harness-ledger":
            data = harness_ledger_action(
                cfg,
                args.action,
                paths=args.path,
                limit=args.limit,
                kind=args.kind,
                marker=args.marker,
                path_contains=args.path_contains,
                note=args.note,
                run_id=args.run_id,
            )
            if args.json:
                print_json(data)
            else:
                print_harness_ledger(data)
            return 0
        if command == "hook-audit":
            data = hook_audit_action(cfg)
            if args.json:
                print_json(data)
            else:
                print(f"status: {data.get('status')}")
                for source in data.get("sources", []):
                    print(f"{source['scope']}: {source['path']}")
            return 0
        if command == "harness-eval":
            data = harness_eval_action(
                cfg,
                case_id=args.case,
                run_all=args.all,
                live=args.live,
                results_root=args.results_root,
            )
            if args.json:
                print_json(data)
            else:
                print(f"success: {data.get('success')}")
                for path in data.get("receipts", []):
                    print(path)
            return 0 if data.get("success") else 1
        if command == "harness-gate":
            data = harness_gate_action(cfg)
            if args.json:
                print_json(data)
            else:
                print(f"functional_status: {data.get('functional_status')}")
                print(f"drift_status: {data.get('drift_status')}")
                for failure in data.get("failures", []):
                    print(f"- {failure}")
            return 0 if data.get("success") else 1
        if command == "skillopt":
            data = skillopt_action(
                cfg,
                action=args.action,
                backend=args.backend,
                source=args.source,
                allow_codex_budget=args.allow_codex_budget,
                json_output=args.json,
            )
            if args.json:
                print_json(data)
            else:
                print(f"status: {data.get('status')}")
                if data.get("message"):
                    print(data["message"])
            return 0 if data.get("status") in {"pass", "ready"} else 1
        if command == "smoke":
            errors = config_errors(cfg)
            data = {"ok": not errors, "config_errors": errors, "transcript_target": transcript_target_to_dict(transcript_target(cfg))}
            if args.json:
                print_json(data)
            else:
                print("OK: no config errors" if not errors else "ERR: config errors found")
                for error in errors:
                    print(f"- {error}")
            return 0 if not errors else 1
        if command == "project-add":
            store = ProjectStore(cfg)
            store.set_current(store.resolve_project_path(str(args.path)))
            if args.json:
                print_json({"current": str(store.current()), "recent": [str(path) for path in store.recent()]})
                return 0
            print(f"current: {store.current()}")
            return 0
        if command == "codex-local":
            project = args.cwd.expanduser().resolve() if args.cwd else ProjectStore(cfg).current()
            result = launch_provider(cfg, "local-qwen-codex", project, attach=not args.json and not args.no_attach, fresh=args.fresh)
            if args.json:
                print_json({"action": "codex-local", "project": str(project), "result": action_result_to_dict(result)})
                return 0 if result.ok else 1
            return print_results([result])
        if command == "codex-gpt":
            project = args.cwd.expanduser().resolve() if args.cwd else ProjectStore(cfg).current()
            result = launch_provider(cfg, "normal-codex", project, attach=not args.json and not args.no_attach, fresh=args.fresh)
            if args.json:
                print_json({"action": "codex-gpt", "project": str(project), "result": action_result_to_dict(result)})
                return 0 if result.ok else 1
            return print_results([result])
        if command == "open-webui":
            result = launch_open_webui(cfg, args.interface, reload_backend=args.reload_backend)
            if args.json:
                print_json({
                    "action": "open-webui",
                    "interface_name": args.interface,
                    "reload_backend": args.reload_backend,
                    "interface": cfg.chat_interfaces.get(args.interface, {}),
                    "result": action_result_to_dict(result),
                })
                return 0 if result.ok else 1
            return print_results([result])
    except StackError as exc:
        print(f"ERR: {exc}", file=sys.stderr)
        return 1
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
