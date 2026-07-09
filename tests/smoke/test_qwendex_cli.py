import importlib.util
import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
QWENDEX = ROOT / "scripts" / "qwendex"
QWENDEX_MODULE = ROOT / "scripts" / "qwendex_cli.py"


def load_qwendex():
    spec = importlib.util.spec_from_file_location("qwendex_cli_test", QWENDEX_MODULE)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def run_qwendex(*args, env=None):
    result = subprocess.run(
        [str(QWENDEX), *args],
        cwd=ROOT,
        env={**os.environ, **(env or {})},
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    return result


def json_result(*args, env=None):
    result = run_qwendex(*args, env=env)
    assert result.returncode == 0, result.stderr or result.stdout
    return parse_json_result(result)


def parse_json_result(result):
    data = json.loads(result.stdout)
    for key in ("status", "summary", "version", "artifacts", "next_actions", "errors"):
        assert key in data
    return data


def test_qwendex_parser_exposes_public_commands():
    qwendex = load_qwendex()
    parser = qwendex.command_line()

    assert parser.parse_args(["check"]).command == "check"
    assert parser.parse_args(["check", "--health-mode", "advisory"]).health_mode == "advisory"
    assert parser.parse_args(["check", "--health-mode", "strict"]).health_mode == "strict"
    assert parser.parse_args(["up", "--dry-run"]).command == "up"
    assert parser.parse_args(["down", "--dry-run"]).command == "down"
    assert parser.parse_args(["restart", "--dry-run"]).command == "restart"
    assert parser.parse_args(["doctor"]).command == "doctor"
    assert parser.parse_args(["doctor", "--health-mode", "advisory"]).health_mode == "advisory"
    assert parser.parse_args(["doctor", "--health-mode", "strict"]).health_mode == "strict"
    assert parser.parse_args(["exec", "Reply exactly QWENDEX_OK"]).command == "exec"
    assert parser.parse_args(["exec", "Reply exactly QWENDEX_OK", "--seat", "auto"]).seat == "auto"
    assert parser.parse_args(["exec", "Reply exactly QWENDEX_OK", "--cwd", "/tmp/qwendex-project"]).cwd == "/tmp/qwendex-project"
    assert parser.parse_args(["eval", "--case", "exact_marker"]).command == "eval"
    assert parser.parse_args(["receipt", "latest"]).command == "receipt"
    assert parser.parse_args(["route"]).command == "route"
    assert parser.parse_args(["seat", "qwen"]).command == "seat"
    assert parser.parse_args(["task", "create", "--title", "T"]).command == "task"
    assert parser.parse_args(["context", "snapshot", "--task-id", "task_1"]).command == "context"
    assert parser.parse_args(["handoff", "create", "--task-id", "task_1"]).command == "handoff"
    assert parser.parse_args(["evidence", "query", "--task-id", "task_1"]).command == "evidence"
    assert parser.parse_args(["queue", "status"]).command == "queue"
    assert parser.parse_args(["learn", "dry-run", "--backend", "mock"]).command == "learn"
    assert parser.parse_args(["manager", "--mode", "manager_only"]).command == "manager"
    assert parser.parse_args(["manager", "mode", "--set", "auto"]).action == "mode"
    assert parser.parse_args(["manager", "mode", "--toggle"]).toggle is True
    assert parser.parse_args(["manager", "kaveman", "--toggle"]).action == "kaveman"
    assert parser.parse_args(["manager", "local", "--set", "off"]).action == "local"
    assert parser.parse_args(["manager", "estimate", "--prompt", "Fix a typo"]).action == "estimate"
    assert parser.parse_args(["manager", "preflight", "--interactive-prompt-unknown", "--dry-run"]).action == "preflight"
    assert parser.parse_args(["manager", "reconcile", "--pending-validation", "--dry-run"]).action == "reconcile"
    assert parser.parse_args(["manager", "repair", "--safe"]).action == "repair"
    assert parser.parse_args(["manager", "repair", "--safe"]).safe is True
    assert parser.parse_args(["--agent-use", "Manager", "agent", "policy"]).agent_use == "Manager"
    assert parser.parse_args(["agent", "status"]).command == "agent"
    assert parser.parse_args(["agent", "inspect", "agent-1"]).target == "agent-1"
    assert parser.parse_args(["agent", "close", "all", "--timeout", "1s"]).action == "close"
    assert parser.parse_args(["agent", "profiles"]).action == "profiles"
    assert parser.parse_args(["agent", "team"]).action == "team"
    assert parser.parse_args(["agent", "plan", "--prompt", "Ship it"]).action == "plan"
    assert parser.parse_args(["agent", "hook", "Stop", "--event-json", "{}"]).action == "hook"
    assert parser.parse_args(["agent", "hook", "Stop", "--codex-hook-output"]).codex_hook_output is True
    assert parser.parse_args(["agent", "hook-config"]).action == "hook-config"
    assert parser.parse_args(["agent", "hook-config", "--install", "--codex-home", "/tmp/codex"]).install is True
    assert parser.parse_args(["agent", "hook-config", "--verify", "--codex-home", "/tmp/codex"]).verify is True
    assert parser.parse_args(["agent", "locks"]).action == "locks"
    assert parser.parse_args(["codex-status", "--write", "/tmp/qwendex-status.json"]).command == "codex-status"
    assert parser.parse_args(["codex-patch", "preflight", "--codex-bin", "codex"]).command == "codex-patch"
    assert parser.parse_args(["codex-patch", "apply", "--source", "/tmp/codex", "--dry-run"]).dry_run is True
    assert parser.parse_args(["estimate", "--prompt", "Fix a typo"]).command == "estimate"
    assert parser.parse_args(["llmstack", "check"]).command == "llmstack"
    assert parser.parse_args(["llmstack", "check"]).action == "check"
    assert parser.parse_args(["llmstack", "doctor"]).action == "doctor"
    assert parser.parse_args(["llmstack", "up", "--dry-run"]).action == "up"
    assert parser.parse_args(["llmstack", "down", "--dry-run"]).action == "down"
    restart = parser.parse_args(["llmstack", "restart", "bridge", "--dry-run"])
    assert restart.action == "restart"
    assert restart.service == "bridge"
    assert parser.parse_args(["version"]).command == "version"


def test_qwendex_version_matches_public_config_metadata():
    qwendex = load_qwendex()
    project_config = json.loads((ROOT / "config" / "qwendex" / "qwendex.json").read_text(encoding="utf-8"))
    sample_config = json.loads((ROOT / "config" / "qwendex" / "qwendex.sample.json").read_text(encoding="utf-8"))
    version = json_result("version", "--json")

    assert qwendex.VERSION == "0.3.0"
    assert version["data"]["version"] == qwendex.VERSION
    assert project_config["version"] == qwendex.VERSION
    assert sample_config["version"] == qwendex.VERSION
    assert f"v{qwendex.VERSION}" in (ROOT / "README.md").read_text(encoding="utf-8")
    assert f"## {qwendex.VERSION}" in (ROOT / "public" / "qwendex" / "release-notes.md").read_text(encoding="utf-8")


def test_qwendex_check_and_doctor_emit_stable_json(tmp_path):
    env = {"QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite")}
    check = json_result("check", "--json", env=env)
    doctor = json_result("doctor", "--json", env=env)

    assert check["status"] == "pass"
    assert doctor["status"] == "pass"
    assert doctor["data"]["critical_issues"] == []
    assert "public/qwendex/README.md" in doctor["artifacts"]
    assert check["data"]["manager_estimate"]["mode"] == "auto"
    assert doctor["data"]["manager_estimate"]["reasoning_policy"]["main_session"]["reasoning_source"] == "user_selected"
    assert 1 <= len(check["data"]["high_value_add"]) <= 2
    assert 1 <= len(doctor["data"]["high_value_add"]) <= 2


def test_qwendex_check_and_doctor_health_mode_handles_stale_writer(tmp_path):
    state_db = tmp_path / "qwendex.sqlite"
    env = {
        "QWENDEX_STATE_DB": str(state_db),
        "QWENDEX_MANAGER_DEPLOY_POLICY": "disabled",
    }

    json_result(
        "manager",
        "assign",
        "--agent-id",
        "stale-writer",
        "--lane",
        "implementation",
        "--write-surface",
        "tests/smoke/test_qwendex_cli.py",
        "--json",
        env=env,
    )
    with sqlite3.connect(state_db) as conn:
        conn.execute("UPDATE qwendex_agent_sessions SET heartbeat_at = '2000-01-01T00:00:00Z'")

    advisory_check = run_qwendex("check", "--health-mode", "advisory", "--json", env=env)
    advisory_doctor = run_qwendex("doctor", "--health-mode", "advisory", "--json", env=env)
    strict_check = run_qwendex("check", "--health-mode", "strict", "--json", env=env)
    strict_doctor = run_qwendex("doctor", "--health-mode", "strict", "--json", env=env)

    advisory_check_data = parse_json_result(advisory_check)
    advisory_doctor_data = parse_json_result(advisory_doctor)
    strict_check_data = parse_json_result(strict_check)
    strict_doctor_data = parse_json_result(strict_doctor)

    assert advisory_check.returncode == 0
    assert advisory_check_data["status"] == "pass"
    assert advisory_check_data["data"]["manager_health_mode"] == "advisory"
    assert "stale manager writer sessions" in " ".join(advisory_check_data["data"]["manager_health_issues"])
    assert advisory_doctor.returncode == 0
    assert advisory_doctor_data["status"] == "pass"
    assert advisory_doctor_data["data"]["manager_health_mode"] == "advisory"
    assert "stale manager writer sessions" in " ".join(advisory_doctor_data["data"]["manager_health_issues"])
    assert strict_check.returncode != 0
    assert strict_check_data["status"] == "fail"
    assert strict_check_data["data"]["manager_health_mode"] == "strict"
    assert "stale manager writer sessions" in " ".join(strict_check_data["errors"])
    assert strict_doctor.returncode != 0
    assert strict_doctor_data["status"] == "fail"
    assert strict_doctor_data["data"]["manager_health_mode"] == "strict"
    assert "stale manager writer sessions" in " ".join(strict_doctor_data["errors"])


def test_qwendex_llmstack_public_contract_and_dry_run_json(tmp_path):
    env = {"QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite")}

    check = json_result("llmstack", "check", "--json", env=env)
    doctor = json_result("llmstack", "doctor", "--json", env=env)
    restart = json_result("llmstack", "restart", "bridge", "--dry-run", "--json", env=env)

    assert check["command"] == "llmstack"
    assert check["status"] == "pass"
    assert doctor["status"] == "pass"
    contract = check["data"]["contract"]
    for key in (
        "services_configured",
        "backend_endpoint",
        "model_alias",
        "codex_bridge",
        "guard_config",
        "receipts_results",
        "missing_optional_host_programs",
    ):
        assert key in contract
    assert contract["config"]["sample_config"] == "config/local_llm_stack/stack_manager.sample.json"
    assert contract["config"]["local_config"] == "config/local_llm_stack/stack_manager.local.json"
    assert contract["config"]["local_config_ignored"] is True
    assert contract["public_boundary"]["bundles_host_programs"] is False
    assert contract["public_boundary"]["bundles_model_weights"] is False
    assert contract["private_hits"] == []
    assert {"textgen", "litellm", "bridge"} <= set(contract["services_configured"])
    assert restart["status"] == "pass"
    assert restart["data"]["delegate"]["status"] == "ready"
    assert restart["data"]["delegate"]["command"][:3] == [str(ROOT / "scripts" / "llm"), "restart", "bridge"]


def test_llmstack_public_configs_are_copy_safe_and_connected():
    qwendex = load_qwendex()
    public_paths = [
        ROOT / "config/local_llm_stack/stack_manager.json",
        ROOT / "config/local_llm_stack/stack_manager.sample.json",
        ROOT / "config/local_llm_stack/profiles.example.json",
        ROOT / "config/local_llm_stack/litellm.local.yaml",
        ROOT / "config/local_llm_stack/litellm.textgen.local.yaml",
        ROOT / "config/local_llm_stack/textgen_cmd_flags.txt",
        ROOT / "scripts/run_textgen_safe_no_model.sh",
        ROOT / "scripts/run_llamacpp_qwopucode_gguf.sh",
        ROOT / "scripts/run_vllm_qwopucode_gguf.sh",
        ROOT / "scripts/run_koboldcpp_gguf.sh",
        ROOT / "scripts/qwendex_testbench",
        ROOT / "public/qwendex/testbench.md",
        ROOT / "llmstack",
        ROOT / "scripts/windows/open.ps1",
    ]
    forbidden_patterns = (
        r"/home/tweak",
        r"/mnt/c/Users/Tweak",
        r"\bAnderson\b",
        r"\bSTAR\b",
        r"\bGTM\b",
        r"Qwopus",
        r"Qwopucode",
        r"Heretic",
        r"Jackrong",
        r"llmfan",
        r"simonycl",
        r"qwen36-27Bb",
    )

    for path in public_paths:
        assert path.exists(), path
        text = path.read_text(encoding="utf-8")
        for pattern in forbidden_patterns:
            assert re.search(pattern, text) is None, f"{pattern} leaked through {path.relative_to(ROOT)}"

    sample = json.loads((ROOT / "config/local_llm_stack/stack_manager.sample.json").read_text(encoding="utf-8"))
    active = json.loads((ROOT / "config/local_llm_stack/stack_manager.json").read_text(encoding="utf-8"))
    profiles = json.loads((ROOT / "config/local_llm_stack/profiles.example.json").read_text(encoding="utf-8"))

    assert active == sample
    assert sample["default_backend_profile"] == "example-llamacpp-qwen-coder-gguf-32k"
    assert {service["name"] for service in sample["services"]} == {"textgen", "litellm", "bridge"}
    assert {profile["backend_kind"] for profile in profiles["backend_profiles"]} >= {"textgen", "llamacpp-gguf", "vllm-gguf", "koboldcpp-gguf"}
    assert qwendex.llmstack_public_contract()["status"] == "pass"


def test_qwendex_config_precedence_and_profiles(tmp_path):
    qwendex = load_qwendex()
    project_config = tmp_path / "qwendex.json"
    project_config.write_text(
        json.dumps({"default_seat": "audit", "seats": {"qwen": {"context_window": 32768}}}),
        encoding="utf-8",
    )

    cfg = qwendex.load_qwendex_config(
        cli_overrides={"default_seat": "release"},
        env={"QWENDEX_DEFAULT_SEAT": "qwen"},
        project_config=project_config,
        user_config=tmp_path / "missing-user.json",
    )

    assert cfg["default_seat"] == "release"
    assert cfg["seats"]["qwen"]["context_window"] == 32768
    assert {"primary", "qwen", "audit", "release", "sandbox"} <= set(cfg["seats"])
    assert cfg["learning"]["mode"] == "stage_only"
    assert cfg["orchestration"]["mode"] == "auto"
    assert cfg["routing"]["mode"] == "token_saver"
    assert "state" in cfg


def test_qwendex_config_blocks_unknown_keys_and_secret_values(tmp_path):
    qwendex = load_qwendex()
    unknown_config = tmp_path / "unknown.json"
    secret_config = tmp_path / "secret.json"
    unknown_config.write_text(json.dumps({"unknown": True}), encoding="utf-8")
    secret_config.write_text(json.dumps({"receipts": {"dir": "password=supersecretvalue123"}}), encoding="utf-8")

    try:
        qwendex.load_qwendex_config(project_config=unknown_config, user_config=tmp_path / "missing.json")
    except ValueError as exc:
        assert "unknown top-level key" in str(exc)
    else:
        raise AssertionError("unknown config key should fail")

    try:
        qwendex.load_qwendex_config(project_config=secret_config, user_config=tmp_path / "missing.json")
    except ValueError as exc:
        assert "secret-like keys or values" in str(exc)
    else:
        raise AssertionError("secret-like config value should fail")


def test_qwendex_exact_exec_and_qwen_seat_write_reviewable_receipts(tmp_path):
    env = {
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_RESULTS_ROOT": str(tmp_path),
        "QWENDEX_FORCE_LOCAL_QWEN_AVAILABLE": "1",
    }

    exec_data = json_result("exec", "Reply exactly QWENDEX_OK", "--json", env=env)
    primary_data = json_result("exec", "Reply exactly QWENDEX_OK", "--seat", "primary", "--json", env=env)
    seat_data = json_result("seat", "qwen", "--json", env=env)

    exec_receipt = json.loads(Path(exec_data["artifacts"][0]).read_text(encoding="utf-8"))
    primary_receipt = json.loads(Path(primary_data["artifacts"][0]).read_text(encoding="utf-8"))
    seat_receipt = json.loads(Path(seat_data["artifacts"][0]).read_text(encoding="utf-8"))

    assert exec_data["data"]["output"] == "QWENDEX_OK"
    assert exec_receipt["task_class"] == "exec"
    assert exec_receipt["model"] == "qwen-local"
    assert exec_receipt["review_status"] == "synthetic_exact_marker"
    assert primary_receipt["seat"] == "primary"
    assert primary_receipt["model"] == "gpt-5.5"
    assert primary_receipt["review_status"] == "seat_exact_marker"
    assert seat_receipt["seat"] == "qwen"
    assert seat_receipt["review_status"] == "requires_gpt_review"
    assert seat_receipt["markers"] == []
    assert seat_receipt["files_touched"] == []
    assert exec_receipt["effective_policy"]["sandbox"]["mode"] == "workspace-write"
    assert "guard" in exec_receipt["effective_policy"]


def test_qwendex_exec_dry_run_respects_cwd_and_mcp_override(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    env = {
        "QWENDEX_RESULTS_ROOT": str(tmp_path / "receipts"),
        "QWENDEX_MCP_TRUSTED_ROOTS": f"{ROOT}:{project}",
        "QWENDEX_FORCE_LOCAL_QWEN_AVAILABLE": "1",
    }

    primary = json_result(
        "exec",
        "Summarize the project.",
        "--seat",
        "primary",
        "--cwd",
        str(project),
        "--dry-run",
        "--json",
        env=env,
    )
    qwen = json_result(
        "exec",
        "Summarize the project.",
        "--seat",
        "qwen",
        "--cwd",
        str(project),
        "--dry-run",
        "--json",
        env=env,
    )

    primary_cmd = primary["data"]["command"]
    qwen_cmd = qwen["data"]["command"]

    assert primary["data"]["seat"] == "primary"
    assert qwen["data"]["seat"] == "qwen"
    assert primary_cmd[primary_cmd.index("-C") + 1] == str(project)
    assert 'mcp_servers.local-harness.command="python3"' in " ".join(primary_cmd)
    assert "mcp_servers.local-harness.args" in " ".join(primary_cmd)
    assert str(ROOT / "scripts" / "artifact_queue_mcp.py") in " ".join(primary_cmd)
    assert f"{ROOT}:{project}" in " ".join(primary_cmd)
    assert qwen_cmd[qwen_cmd.index("--cwd") + 1] == str(project)
    assert "--minimal" in qwen_cmd
    assert "--ephemeral" in qwen_cmd


def test_qwendex_testbench_public_surface_is_visible_and_sandboxed():
    script = ROOT / "scripts" / "qwendex_testbench"
    text = script.read_text(encoding="utf-8")

    assert script.exists()
    assert ">_ OpenAI Codex (v%s) /w Qwendex" in text
    assert "--no-alt-screen" in text
    assert "qwendex-local" in text
    assert "qwendex-full" in text
    assert "$BENCH_ROOT/bin" in text
    assert "qwendex-bench" in text
    assert "qwebdex-bench is a typo" in text
    assert "llmstack" in text
    assert "QWENDEX_BENCH_PROJECT" in text
    assert "QWENDEX_BENCH_ROOT" in text
    assert "QWENDEX_CODEX_STATUS_FILE" in text
    assert "$QWENDEX\" codex-status --plain" in text
    assert "Agent Manager: [Manager Mode] | Kaveman: [N] | Local: [Ready]" not in text
    assert "Local: [Y]" not in text
    assert "QWENDEX_MCP_TRUSTED_ROOTS" in text
    assert "codex-patch preflight" in text
    assert "codex-status --write" in text
    assert "'$BENCH_CMD' env" in text
    assert "exec bash" in text


def test_qwendex_dev_env_public_surface_is_visible_and_isolated():
    script = ROOT / "scripts" / "qwendex_dev_env"
    text = script.read_text(encoding="utf-8")
    installer = ROOT / "scripts" / "qwendex_install_deps"
    installer_text = installer.read_text(encoding="utf-8")
    dependencies = json.loads((ROOT / "config" / "qwendex" / "dependencies.json").read_text(encoding="utf-8"))

    assert script.exists()
    assert installer.exists()
    assert os.access(installer, os.X_OK)
    assert dependencies["schema_version"] == "qwendex.dependencies.v1"
    assert {"bash", "python3", "git", "rsync", "curl", "codex"} <= set(dependencies["required_commands"])
    assert {"pytest", "ruff"} <= set(dependencies["validation_python_modules"])
    assert "npm install -g --prefix \"$HOME/.local\" @openai/codex" in installer_text
    assert "python3 -m pip install --user --upgrade pytest ruff" in installer_text
    assert "cargo install ripgrep --locked" in installer_text
    assert "QWENDEX_DEV_ROOT" in text
    assert "$HOME/qwendex-dev" in text
    assert "WORK_ROOT=\"$DEV_ROOT/.qwendex-dev\"" in text
    assert "INSTALL_DEPS_JSON" in text
    assert "qwendex_install_deps" in text
    assert "install_deps.json" in text
    assert "$WORK_ROOT/env.sh" in text
    assert "$HOME/.local/bin" in text
    assert "codex-main" in text
    assert "QWENDEX_DEV_CODEX_BIN" in text
    assert "senior Qwendex product maintainer" in text
    assert "After context compaction" in text
    assert "resume from the newest user request" in text
    assert "qwendex-dev snapshot" in text
    assert "context snapshot/reminder/compact-plan" in text
    assert "At the start of substantial tasks" in text
    assert "use manager planning/preflight first" in text
    assert "write-surface separation" in text
    assert "treat subagent output as advisory" in text
    assert "direct-work reason and validation path" in text
    assert "close spawned agents and matching Qwendex manager sessions" in text
    assert "cmd_verify" in text
    assert "cmd_bootstrap" in text
    assert "cmd_doctor" in text
    assert "cmd_status_json" in text
    assert "hook_source_count" in text
    assert "active dev CODEX_HOME has no hooks" in text
    assert "cmd_codex_source" in text
    assert "cmd_clean" in text
    assert "cmd_repair_copy" in text
    assert "cmd_promote" in text
    assert "cmd_stage" in text
    assert "cmd_snapshot" in text
    assert "cmd_open_yolo" in text
    assert "QWENDEX_CODEX_YOLO_FLAG" in text
    assert "--dangerously-bypass-approvals-and-sandbox" in text
    assert "verify --tier quick|full|live|release" in text
    assert "QWENDEX_LEDGER_DB" in text
    assert "LOCAL_QWEN_HARNESS_LEDGER_DB" in text
    assert "release_verify_qwendex.sqlite" in text
    assert "dev_status.json" in text
    assert "release_validation_summary.json" in text
    assert "contract_marker_counts" in text
    assert "expected_marker_counts" in text
    assert "llmstack_check.json" in text
    assert "path.resolve() == out.resolve()" in text
    assert "codex-patch apply" in text
    assert "cargo build --release -p codex-cli --bin codex" in text
    assert "status_line = [\"model-with-reasoning\", \"current-dir\", \"qwendex-manager\"]" in text
    assert "qwendex_toggle_manager = \"alt-m\"" in text
    assert "qwendex_toggle_kaveman = \"alt-k\"" in text
    assert "qwendex_toggle_local = \"alt-l\"" in text
    assert "codex-source sync|patch|build|preflight" in text
    assert "open.ps1" in text
    assert "--exclude '.git/'" in text
    assert "--exclude 'results/'" in text
    assert "--exclude '/bin/'" in text
    assert "git -C \"$DEV_ROOT\" add -A" in text

    doc = (ROOT / "public" / "qwendex" / "dev-environment.md").read_text(encoding="utf-8")
    assert "scripts/qwendex_dev_env sync" in doc
    assert "scripts/qwendex_install_deps --install" in doc
    assert "install_deps.json" in doc
    assert "qwendex-dev bootstrap" in doc
    assert "qwendex-dev doctor" in doc
    assert "qwendex-dev verify --tier release" in doc
    assert "qwendex-dev stage" in doc
    assert "qwendex-dev codex-source patch" in doc
    assert "Bare `qwendex-dev` opens Codex" in doc
    assert "--dangerously-bypass-approvals-and-sandbox" in doc
    assert "repair-copy" in doc
    assert "~/qwendex-dev" in doc
    assert "fallback execution plane" in doc

    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "Connectedness Rule" in agents
    assert "state source or config field" in agents
    assert "Stop and repair" in agents

    startup = (ROOT / "QWENDEX_STARTUP.md").read_text(encoding="utf-8")
    assert "Connectedness Check" in startup
    assert "patched Codex build" in startup
    assert "Bare `qwendex-dev` opens Codex" in startup

    maintainer_skill = (ROOT / ".codex" / "skills" / "qwendex-dev-maintainer" / "SKILL.md").read_text(encoding="utf-8")
    assert "connectedness chain" in maintainer_skill
    assert "Stop-The-Line Conditions" in maintainer_skill

    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".qwendex-dev/" in gitignore

    for rel in (
        "docs/development/architecture-map.md",
        "docs/development/failure-modes.md",
        "docs/development/release-runbook.md",
        "docs/development/contribution-workflow.md",
        "docs/development/decision-log.md",
        ".codex/skills/qwendex-dev-maintainer/SKILL.md",
        ".codex/skills/qwendex-release-gate/SKILL.md",
        ".codex/skills/qwendex-local-bridge-triage/SKILL.md",
        ".codex/skills/qwendex-codex-patch/SKILL.md",
    ):
        assert (ROOT / rel).exists(), rel


def test_qwendex_dev_default_launches_repo_with_yolo_codex(tmp_path):
    fake_home = tmp_path / "home"
    dev_root = tmp_path / "qwendex-dev"
    fake_bin = tmp_path / "bin"
    fake_codex = fake_bin / "codex"
    args_file = tmp_path / "codex-args.json"
    cwd_file = tmp_path / "codex-cwd.txt"

    fake_bin.mkdir()
    fake_home.mkdir()
    (fake_home / ".codex").mkdir()
    (fake_home / ".codex" / "hooks.json").write_text('{"hooks":{"PreToolUse":[]}}\n', encoding="utf-8")
    fake_codex.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

if sys.argv[1:] == ["--version"]:
    print("codex-cli 0.143.0")
    raise SystemExit(0)

Path(os.environ["QWENDEX_FAKE_CODEX_ARGS"]).write_text(json.dumps(sys.argv[1:]), encoding="utf-8")
Path(os.environ["QWENDEX_FAKE_CODEX_CWD"]).write_text(os.getcwd(), encoding="utf-8")
""",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)

    env = {
        **os.environ,
        "HOME": str(fake_home),
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "QWENDEX_DEV_ROOT": str(dev_root),
        "QWENDEX_DEV_SOURCE_ROOT": str(ROOT),
        "QWENDEX_MAIN_CODEX_BIN": str(fake_codex),
        "QWENDEX_DEV_CODEX_BIN": str(fake_codex),
        "QWENDEX_FAKE_CODEX_ARGS": str(args_file),
        "QWENDEX_FAKE_CODEX_CWD": str(cwd_file),
    }

    sync = subprocess.run(
        [str(ROOT / "scripts" / "qwendex_dev_env"), "sync"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert sync.returncode == 0, sync.stderr or sync.stdout

    launched = subprocess.run(
        [str(ROOT / "scripts" / "qwendex_dev_env")],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )

    assert launched.returncode == 0, launched.stderr or launched.stdout
    args = json.loads(args_file.read_text(encoding="utf-8"))
    config = (dev_root / ".qwendex-dev" / "codex_home" / "config.toml").read_text(encoding="utf-8")
    assert cwd_file.read_text(encoding="utf-8") == str(dev_root)
    assert args[:2] == ["--no-alt-screen", "--dangerously-bypass-approvals-and-sandbox"]
    assert args[args.index("-C") + 1] == str(dev_root)
    assert "qwendex-manager" in config
    assert "qwendex_toggle_manager = \"alt-m\"" in config
    assert "qwendex_toggle_kaveman = \"alt-k\"" in config
    assert "qwendex_toggle_local = \"alt-l\"" in config
    dev_status = json.loads((dev_root / ".qwendex-dev" / "results" / "meta" / "dev_status.json").read_text(encoding="utf-8"))
    assert dev_status["codex"]["hook_source_count"] == 0
    assert dev_status["codex"]["global_hook_source_count"] == 1
    assert "active dev CODEX_HOME has no hooks" in " ".join(dev_status["warnings"])


def test_qdex_manager_preflight_blocks_and_exports_env_before_launch(tmp_path):
    fake_home = tmp_path / "home"
    dev_root = tmp_path / "qwendex-dev"
    fake_bin = tmp_path / "bin"
    fake_codex = fake_bin / "codex"
    args_file = tmp_path / "qdex-codex-call.json"

    fake_bin.mkdir()
    fake_home.mkdir()
    fake_codex.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

if sys.argv[1:] == ["--version"]:
    print("codex-cli 0.143.0")
    raise SystemExit(0)

Path(os.environ["QWENDEX_FAKE_CODEX_ARGS"]).write_text(json.dumps({
    "args": sys.argv[1:],
    "manager_session_id": os.environ.get("QWENDEX_MANAGER_SESSION_ID", ""),
    "manager_ledger_id": os.environ.get("QWENDEX_MANAGER_LEDGER_ID", ""),
    "manager_policy_hash": os.environ.get("QWENDEX_MANAGER_POLICY_HASH", ""),
}), encoding="utf-8")
""",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)

    env = {
        **os.environ,
        "HOME": str(fake_home),
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "QWENDEX_DEV_ROOT": str(dev_root),
        "QWENDEX_DEV_SOURCE_ROOT": str(ROOT),
        "QWENDEX_MAIN_CODEX_BIN": str(fake_codex),
        "QWENDEX_DEV_CODEX_BIN": str(fake_codex),
        "QWENDEX_FAKE_CODEX_ARGS": str(args_file),
    }
    for key in (
        "CODEX_HOME",
        "QWENDEX_AGENT_USE",
        "CODEX_AGENT_USE",
        "QWENDEX_MANAGER_ALLOW_UNHOOKED",
        "QWENDEX_MANAGER_UNHOOKED_REASON",
    ):
        env.pop(key, None)
    sync = subprocess.run(
        [str(ROOT / "scripts" / "qwendex_dev_env"), "sync"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert sync.returncode == 0, sync.stderr or sync.stdout
    qdex = fake_home / ".local" / "bin" / "qdex"
    assert qdex.exists()

    set_mode = subprocess.run(
        [
            "bash",
            "-lc",
            'source "$QWENDEX_DEV_ROOT/.qwendex-dev/env.sh"; "$QWENDEX_DEV_ROOT/scripts/qwendex" manager mode --set manager --json',
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert set_mode.returncode == 0, set_mode.stderr or set_mode.stdout

    blocked = subprocess.run(
        [str(qdex), "--repo", str(ROOT)],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert blocked.returncode != 0
    assert "Qwendex Manager preflight: blocked" in blocked.stderr
    assert "STOP_MANAGER_BLOCKED_UNHOOKED" in blocked.stderr
    assert not args_file.exists()

    env_override_blocked = subprocess.run(
        [str(qdex), "--repo", str(ROOT)],
        cwd=ROOT,
        env={**env, "QWENDEX_AGENT_USE": "Heavy"},
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert env_override_blocked.returncode != 0
    assert "Qwendex Manager preflight: blocked" in env_override_blocked.stderr
    assert "STOP_MANAGER_BLOCKED_UNHOOKED" in env_override_blocked.stderr
    assert not args_file.exists()

    launched = subprocess.run(
        [str(qdex), "--repo", str(ROOT)],
        cwd=ROOT,
        env={**env, "QWENDEX_MANAGER_ALLOW_UNHOOKED": "1"},
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert launched.returncode == 0, launched.stderr or launched.stdout
    call = json.loads(args_file.read_text(encoding="utf-8"))
    assert call["args"][:2] == ["--no-alt-screen", "--dangerously-bypass-approvals-and-sandbox"]
    assert call["args"][call["args"].index("-C") + 1] == str(ROOT)
    assert call["manager_session_id"].startswith("mgrsess_")
    assert call["manager_ledger_id"].startswith("mgrldg_")
    assert call["manager_policy_hash"]


def test_qwendex_codex_status_tracks_manager_state_and_writes_surface_file(tmp_path):
    status_file = tmp_path / "codex_status.json"
    env = {
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_CODEX_STATUS_FILE": str(status_file),
        "QWENDEX_FORCE_LOCAL_QWEN_AVAILABLE": "1",
    }

    blocked_mode = run_qwendex("manager", "mode", "--set", "manager", "--json", env=env)
    blocked_mode_data = parse_json_result(blocked_mode)
    assert blocked_mode.returncode == 0
    assert blocked_mode_data["status"] == "pass"
    assert blocked_mode_data["data"]["deployment_contract"]["status"] == "standby"
    assert blocked_mode_data["data"]["manager_health"]["status"] == "standby"
    json_result(
        "manager",
        "assign",
        "--agent-id",
        "agent-footer",
        "--lane",
        "footer-status",
        "--task-id",
        "footer-status",
        "--objective",
        "Keep Manager Mode footer status connected to an active lane",
        "--json",
        env=env,
    )
    json_result("manager", "local", "--set", "on", "--json", env=env)
    status = json_result("codex-status", "--write", str(status_file), "--json", env=env)
    plain = run_qwendex("codex-status", "--plain", env=env)

    assert status["data"]["text"] == "{Qwendex} Agent Manager: [Manager Mode] | Kaveman: [N] | Local: [Ready] (Alt+M/K/L)"
    assert status["data"]["mode"] == "manager"
    assert status["data"]["agent_use"] == "Manager"
    assert status["data"]["agent_policy_source"] == "manager-mode"
    assert plain.stdout.strip() == "{Qwendex} Agent Manager: [Manager Mode] | Kaveman: [N] | Local: [Ready] (Alt+M/K/L)"
    written = json.loads(status_file.read_text(encoding="utf-8"))
    assert written["text"] == "{Qwendex} Agent Manager: [Manager Mode] | Kaveman: [N] | Local: [Ready] (Alt+M/K/L)"
    assert written["agent_use"] == "Manager"
    assert written["agent_policy_source"] == "manager-mode"
    assert written["kaveman_enabled"] is False
    assert written["local_usable"] is True

    kaveman = json_result("manager", "kaveman", "--toggle", "--json", env=env)
    assert kaveman["data"]["kaveman_enabled"] is True
    written_after_kaveman = json.loads(status_file.read_text(encoding="utf-8"))
    assert written_after_kaveman["text"] == "{Qwendex} Agent Manager: [Manager Mode] | Kaveman: [Y] | Local: [Ready] (Alt+M/K/L)"
    assert written_after_kaveman["kaveman_directive"]

    toggled = json_result("manager", "mode", "--toggle", "--json", env=env)
    assert toggled["data"]["mode"] == "off"
    written_after_toggle = json.loads(status_file.read_text(encoding="utf-8"))
    assert written_after_toggle["text"] == "{Qwendex} Agent Manager: [Off] | Kaveman: [Y] | Local: [Ready] (Alt+M/K/L)"


def test_qwendex_codex_status_warns_on_state_db_mismatch(tmp_path):
    status_file = tmp_path / "codex_status.json"
    env_a = {
        "QWENDEX_STATE_DB": str(tmp_path / "state-a.sqlite"),
        "QWENDEX_CODEX_STATUS_FILE": str(status_file),
        "QWENDEX_FORCE_LOCAL_QWEN_AVAILABLE": "1",
    }
    env_b = {
        "QWENDEX_STATE_DB": str(tmp_path / "state-b.sqlite"),
        "QWENDEX_CODEX_STATUS_FILE": str(status_file),
        "QWENDEX_FORCE_LOCAL_QWEN_AVAILABLE": "1",
    }

    json_result("manager", "mode", "--set", "manager", "--json", env=env_a)
    json_result("manager", "kaveman", "--set", "on", "--json", env=env_a)
    json_result("codex-status", "--write", str(status_file), "--json", env=env_a)

    mismatch = json_result("codex-status", "--json", env=env_b)
    diagnostics = mismatch["data"]["status_file_diagnostics"]

    assert mismatch["data"]["text"] == "{Qwendex} Agent Manager: [Auto] | Kaveman: [N] | Local: [Ready] (Alt+M/K/L)"
    assert diagnostics["status_file_state_mismatch"] is True
    assert diagnostics["status_file_state_db"] == env_a["QWENDEX_STATE_DB"]
    assert mismatch["data"]["warnings"]
    assert "codex-status --write" in " ".join(mismatch["data"]["next_actions"])

    refreshed = json_result("codex-status", "--write", str(status_file), "--json", env=env_b)
    written = json.loads(status_file.read_text(encoding="utf-8"))

    assert refreshed["data"]["status_file_diagnostics"]["status_file_state_mismatch"] is True
    assert written["status_file_diagnostics"]["status_file_state_mismatch"] is False
    assert written["status_file_diagnostics"]["status_file_state_db"] == env_b["QWENDEX_STATE_DB"]
    assert written["warnings"] == []


def test_qwendex_codex_status_reports_unusable_local_state(tmp_path):
    status_file = tmp_path / "codex_status.json"
    env = {
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_CODEX_STATUS_FILE": str(status_file),
        "QWENDEX_FORCE_LOCAL_QWEN_AVAILABLE": "0",
    }

    json_result("manager", "local", "--set", "on", "--json", env=env)
    status = json_result("codex-status", "--write", str(status_file), "--json", env=env)
    manager = json_result("manager", "status", "--json", env=env)

    assert status["data"]["text"] == "{Qwendex} Agent Manager: [Auto] | Kaveman: [N] | Local: [Unavailable] (Alt+M/K/L)"
    assert status["data"]["local_enabled"] is True
    assert status["data"]["local_usable"] is False
    assert status["data"]["local_state"] == "unavailable"
    assert manager["data"]["local_subagents"]["enabled"] is True


def test_qwendex_codex_patch_preflight_version_manifest(tmp_path):
    fake_codex = tmp_path / "codex"
    fake_codex.write_text("#!/usr/bin/env bash\nprintf 'codex-cli 0.143.0\\n'\n", encoding="utf-8")
    fake_codex.chmod(0o755)

    data = json_result("codex-patch", "preflight", "--codex-bin", str(fake_codex), "--json")

    assert data["status"] == "pass"
    assert data["data"]["version"]["version"] == "0.143.0"
    assert data["data"]["supported"] is True
    assert data["data"]["manifest"]["status_line_item"] == "qwendex-manager"
    assert "qwendex_toggle_manager" in data["data"]["manifest"]["keymap_actions"]
    assert "qwendex_toggle_kaveman" in data["data"]["manifest"]["keymap_actions"]
    assert "qwendex_toggle_local" in data["data"]["manifest"]["keymap_actions"]


def test_qwendex_codex_patch_apply_updates_supported_source_checkout(tmp_path):
    qwendex = load_qwendex()
    source = tmp_path / "codex"
    anchors_by_path = {
        str(spec["path"]): "\n".join(str(anchor) for anchor in spec["anchors"])
        for spec in qwendex.CODEX_PATCH_MANIFESTS["0.143.0"]["source_anchors"]
    }
    for spec in qwendex.codex_source_patch_specs("0.143.0"):
        rel = str(spec["path"])
        path = source / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        old_fragments = "\n".join(old for old, _new in spec["replacements"])
        path.write_text(f"{anchors_by_path.get(rel, '')}\n{old_fragments}\n", encoding="utf-8")

    fake_codex = tmp_path / "codex-bin"
    fake_codex.write_text("#!/usr/bin/env bash\nprintf 'codex-cli 0.143.0\\n'\n", encoding="utf-8")
    fake_codex.chmod(0o755)

    applied = json_result("codex-patch", "apply", "--codex-bin", str(fake_codex), "--source", str(source), "--json")
    preflight = json_result(
        "codex-patch",
        "preflight",
        "--codex-bin",
        str(fake_codex),
        "--source",
        str(source),
        "--require-applied",
        "--json",
    )
    reapplied = json_result("codex-patch", "apply", "--codex-bin", str(fake_codex), "--source", str(source), "--json")

    assert applied["status"] == "pass"
    assert applied["data"]["apply"]["changed"] is True
    assert preflight["status"] == "pass"
    assert preflight["data"]["applied"] is True
    assert reapplied["status"] == "pass"
    assert reapplied["data"]["apply"]["changed"] is False
    assert qwendex.QWENDEX_CODEX_PATCH_MARKER in (
        source / "codex-rs/tui/src/app/input.rs"
    ).read_text(encoding="utf-8")
    assert "qwendex_toggle_manager" in (
        source / "codex-rs/tui/src/keymap.rs"
    ).read_text(encoding="utf-8")
    assert "qwendex_toggle_kaveman" in (
        source / "codex-rs/tui/src/keymap.rs"
    ).read_text(encoding="utf-8")
    terminal_instructions = (
        source / "codex-rs/tui/src/terminal_visualization_instructions.rs"
    ).read_text(encoding="utf-8")
    status_preview = (
        source / "codex-rs/tui/src/bottom_pane/status_surface_preview.rs"
    ).read_text(encoding="utf-8")
    assert "Local: [Ready]" in status_preview
    assert "Local: [Y]" not in status_preview
    assert "qwendex_kaveman_directive" in terminal_instructions
    assert "QWENDEX_CODEX_STATUS_FILE" in terminal_instructions
    assert "Qwendex Kaveman directive" in terminal_instructions


def test_qwendex_route_command_and_auto_exec_prefer_local_qwen_when_available(tmp_path):
    env = {
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_RESULTS_ROOT": str(tmp_path),
        "QWENDEX_FORCE_LOCAL_QWEN_AVAILABLE": "1",
    }

    route = json_result("route", "--task-class", "exec", "--json", env=env)
    exec_data = json_result("exec", "Reply exactly QWENDEX_OK", "--seat", "auto", "--json", env=env)
    receipt = json.loads(Path(exec_data["artifacts"][0]).read_text(encoding="utf-8"))

    assert route["status"] == "pass"
    assert route["data"]["seat"] == "qwen"
    assert route["data"]["requested_seat"] == "auto"
    assert route["data"]["local_qwen"]["available"] is True
    assert route["data"]["local_subagents"]["local_state"] == "ready"
    assert route["data"]["local_subagents"]["local_available"] is True
    assert route["data"]["local_subagents"]["local_usable"] is True
    assert route["data"]["local_subagents"]["indicator"] == "(Alt+L) Local: [Ready]"
    assert route["data"]["routing"]["prefer_local_qwen_when_available"] is True
    assert exec_data["data"]["seat"] == "qwen"
    assert exec_data["data"]["routing"]["seat"] == "qwen"
    assert receipt["routing"]["seat"] == "qwen"
    assert receipt["model"] == "qwen-local"


def test_qwendex_auto_route_falls_back_to_primary_when_local_qwen_is_unavailable(tmp_path):
    env = {
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_RESULTS_ROOT": str(tmp_path),
        "QWENDEX_FORCE_LOCAL_QWEN_UNAVAILABLE": "1",
    }

    route = json_result("route", "--task-class", "exec", "--json", env=env)
    exec_data = json_result("exec", "Reply exactly QWENDEX_OK", "--seat", "auto", "--json", env=env)
    receipt = json.loads(Path(exec_data["artifacts"][0]).read_text(encoding="utf-8"))

    assert route["status"] == "pass"
    assert route["data"]["seat"] == "primary"
    assert route["data"]["local_qwen"]["available"] is False
    assert route["data"]["local_qwen_eligible"] is True
    assert route["data"]["local_subagents"]["enabled"] is True
    assert route["data"]["local_subagents"]["local_state"] == "unavailable"
    assert route["data"]["local_subagents"]["local_available"] is False
    assert route["data"]["local_subagents"]["local_usable"] is False
    assert route["data"]["local_subagents"]["indicator"] == "(Alt+L) Local: [Unavailable]"
    assert route["data"]["local_subagents"]["usable"] is False
    assert exec_data["data"]["seat"] == "primary"
    assert receipt["seat"] == "primary"
    assert receipt["model"] == "gpt-5.5"


def test_qwendex_route_unavailable_probe_keeps_local_intent_and_falls_back_primary(tmp_path):
    env = {
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_FORCE_LOCAL_QWEN_UNAVAILABLE": "1",
    }

    route = json_result("route", "--task-class", "artifact summary", "--prefer-local", "--json", env=env)

    assert route["data"]["seat"] == "primary"
    assert route["data"]["reason"] == "fallback_seat"
    assert route["data"]["reasoning_source"] == "fallback_policy"
    assert route["data"]["token_saver_used"] is False
    assert route["data"]["local_qwen_eligible"] is True
    assert route["data"]["local_qwen"]["available"] is False
    assert route["data"]["local_qwen"]["source"] == "env"
    assert route["data"]["local_qwen"]["reason"] == "forced_unavailable"
    assert route["data"]["local_subagents"]["enabled"] is True
    assert route["data"]["local_subagents"]["available"] is False
    assert route["data"]["local_subagents"]["local_state"] == "unavailable"
    assert route["data"]["local_subagents"]["local_available"] is False
    assert route["data"]["local_subagents"]["local_usable"] is False
    assert route["data"]["local_subagents"]["usable"] is False


def test_qwendex_route_force_available_false_falls_back_to_primary(tmp_path):
    env = {
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_FORCE_LOCAL_QWEN_AVAILABLE": "0",
    }

    local = json_result("manager", "local", "--set", "on", "--json", env=env)
    route = json_result("route", "--task-class", "artifact summary", "--prefer-local", "--json", env=env)

    assert local["data"]["local_subagents"]["enabled"] is True
    assert local["data"]["local_subagents"]["local_state"] == "unavailable"
    assert local["data"]["local_subagents"]["available"] is False
    assert route["data"]["seat"] == "primary"
    assert route["data"]["local_qwen"]["available"] is False
    assert route["data"]["local_qwen"]["reason"] == "forced_unavailable"
    assert route["data"]["token_saver_used"] is False
    assert route["data"]["local_qwen_eligible"] is True


def test_qwendex_route_prefer_local_respects_local_toggle_off(tmp_path):
    env = {
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_FORCE_LOCAL_QWEN_AVAILABLE": "1",
    }

    local = json_result("manager", "local", "--set", "off", "--json", env=env)
    route = json_result("route", "--task-class", "artifact summary", "--prefer-local", "--json", env=env)

    assert local["data"]["local_subagents"]["enabled"] is False
    assert route["data"]["seat"] == "primary"
    assert route["data"]["reason"] == "local_subagents_disabled"
    assert route["data"]["reasoning_source"] == "fallback_policy"
    assert route["data"]["token_saver_used"] is False
    assert route["data"]["local_qwen_eligible"] is False
    assert route["data"]["local_qwen"]["source"] == "not_probed"
    assert route["data"]["local_subagents"]["enabled"] is False
    assert route["data"]["local_subagents"]["usable"] is False


def test_qwendex_local_off_route_never_selects_qwen(tmp_path):
    env = {
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_RESULTS_ROOT": str(tmp_path / "receipts"),
        "QWENDEX_FORCE_LOCAL_QWEN_AVAILABLE": "1",
    }

    local = json_result("manager", "local", "--set", "off", "--json", env=env)
    route = json_result("route", "--task-class", "exec", "--prefer-local", "--json", env=env)
    exec_data = json_result("exec", "Reply exactly QWENDEX_OK", "--seat", "auto", "--json", env=env)
    receipt = json.loads(Path(exec_data["artifacts"][0]).read_text(encoding="utf-8"))

    assert local["data"]["local_subagents"]["enabled"] is False
    assert route["data"]["seat"] == "primary"
    assert route["data"]["local_qwen_eligible"] is False
    assert route["data"]["token_saver_used"] is False
    assert exec_data["data"]["seat"] == "primary"
    assert exec_data["data"]["routing"]["seat"] == "primary"
    assert exec_data["data"]["routing"]["local_qwen_eligible"] is False
    assert receipt["seat"] == "primary"
    assert receipt["model"] == "gpt-5.5"


def test_qwendex_receipt_blocks_outside_json_reads(tmp_path):
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps({"not": "a qwendex receipt"}), encoding="utf-8")

    result = run_qwendex("receipt", str(outside), "--json")
    data = parse_json_result(result)

    assert result.returncode != 0
    assert data["status"] == "blocked"
    assert "trusted receipt roots" in data["summary"]


def test_qwendex_receipt_verifies_schema_and_digest(tmp_path):
    env = {
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_RESULTS_ROOT": str(tmp_path),
        "QWENDEX_FORCE_LOCAL_QWEN_AVAILABLE": "1",
    }
    exec_data = json_result("exec", "Reply exactly QWENDEX_OK", "--json", env=env)
    receipt_path = Path(exec_data["artifacts"][0])

    loaded = json_result("receipt", str(receipt_path), "--json", env=env)
    assert loaded["data"]["verification"]["verified"] is True

    tampered = json.loads(receipt_path.read_text(encoding="utf-8"))
    tampered["output"] = "CHANGED"
    receipt_path.write_text(json.dumps(tampered, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    result = run_qwendex("receipt", str(receipt_path), "--json", env=env)
    data = parse_json_result(result)

    assert result.returncode != 0
    assert data["status"] == "blocked"
    assert "verification failed" in data["summary"]
    assert "sha256 mismatch" in " ".join(data["errors"])


def test_qwendex_eval_defaults_to_full_suite(monkeypatch):
    qwendex = load_qwendex()
    calls = {}

    class FakeEvalModule:
        DEFAULT_RESULTS_ROOT = ROOT / "results" / "fake"

        @staticmethod
        def run_harness_eval(**kwargs):
            calls.update(kwargs)
            return {
                "success": True,
                "case_ids": ["a", "b"],
                "receipts": ["results/fake/a.json", "results/fake/b.json"],
                "failures": [],
            }

    monkeypatch.setattr(qwendex, "script_module", lambda name: FakeEvalModule)
    cfg = qwendex.load_qwendex_config(project_config=ROOT / "config/qwendex/qwendex.json", user_config=ROOT / "missing-user.json")
    args = qwendex.command_line().parse_args(["eval"])

    data = qwendex.command_eval(args, cfg)

    assert data["status"] == "pass"
    assert calls["case_id"] == ""
    assert calls["run_all"] is True
    assert data["data"]["metrics"]["total_cases"] == 2
    assert data["data"]["metrics"]["passed_cases"] == 2
    assert data["data"]["manager_estimate"]["release_risk"] in {"low", "medium", "high"}


def test_qwendex_learning_denies_unsafe_auto_adopt_paths(tmp_path):
    qwendex = load_qwendex()
    unsafe_paths = [
        Path("hooks/hooks.json"),
        Path(".codex/config.toml"),
        Path("config/local_llm_stack/local_harness.env"),
        Path("scripts/local_qwen_bridge/server.py"),
        Path("state/research/example.csv"),
        Path("public/qwendex/security.md"),
    ]

    for path in unsafe_paths:
        assert qwendex.is_auto_adopt_allowed(path) is False

    assert qwendex.is_auto_adopt_allowed(Path("tests/smoke/../../hooks/hooks.json")) is False
    assert qwendex.is_auto_adopt_allowed(Path("/tmp/qwendex-not-in-repo/SKILL.md")) is False
    assert qwendex.is_auto_adopt_allowed(Path(".codex/skills/qwendex-note/SKILL.md")) is True
    result = run_qwendex("learn", "adopt", "--proposal", str(tmp_path / "missing.json"), "--json")
    data = parse_json_result(result)
    assert result.returncode != 0
    assert data["status"] == "blocked"
    assert "explicit approval" in data["summary"]

    approved_missing = run_qwendex("learn", "adopt", "--proposal", str(tmp_path / "missing.json"), "--approve", "--json")
    approved_missing_data = parse_json_result(approved_missing)
    assert approved_missing.returncode != 0
    assert approved_missing_data["status"] == "blocked"
    assert "valid proposal" in approved_missing_data["summary"]

    proposal = tmp_path / "proposal.json"
    proposal.write_text(json.dumps({"changed_files": ["tests/smoke/test_allowed.py"]}), encoding="utf-8")
    approved = json_result("learn", "adopt", "--proposal", str(proposal), "--approve", "--json")
    assert approved["status"] == "pass"
    assert approved["data"]["paths"] == ["tests/smoke/test_allowed.py"]

    malformed = tmp_path / "malformed-proposal.json"
    malformed.write_text(
        json.dumps({"changed_files": ["tests/smoke/test_allowed.py", {"path": "hooks/hooks.json"}]}),
        encoding="utf-8",
    )
    malformed_result = run_qwendex("learn", "adopt", "--proposal", str(malformed), "--approve", "--json")
    malformed_data = parse_json_result(malformed_result)
    assert malformed_result.returncode != 0
    assert malformed_data["status"] == "blocked"
    assert "metadata" in " ".join(malformed_data["errors"])


def test_qwendex_redacts_secret_like_values_from_output_and_receipts(tmp_path):
    qwendex = load_qwendex()
    cfg = qwendex.load_qwendex_config(
        cli_overrides={"receipts": {"dir": str(tmp_path)}},
        project_config=ROOT / "config/qwendex/qwendex.json",
        user_config=tmp_path / "missing.json",
    )
    secret_text = "password=supersecretvalue123 secret=anothersecretvalue456 api_key=fakeapikeyvalue789"

    envelope = qwendex.stable_envelope(command="test", status="fail", summary=secret_text, errors=[secret_text], data={"stdout": secret_text})
    receipt = qwendex.write_receipt(cfg, "redaction", {"stdout_tail": secret_text})
    receipt_data = json.loads(receipt.read_text(encoding="utf-8"))

    assert "[redacted]" in envelope["summary"]
    assert "[redacted]" in envelope["errors"][0]
    assert "[redacted]" in envelope["data"]["stdout"]
    assert "[redacted]" in receipt_data["stdout_tail"]
    assert "supersecretvalue123" not in json.dumps(envelope) + json.dumps(receipt_data)


def test_qwendex_manager_mode_cycles_status_and_legacy_alias(tmp_path):
    env = {
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_FORCE_LOCAL_QWEN_AVAILABLE": "1",
    }

    auto = json_result("manager", "mode", "--set", "auto", "--json", env=env)
    cycled = json_result("manager", "mode", "--cycle", "--json", env=env)
    status = json_result("manager", "status", "--json", env=env)
    legacy_result = run_qwendex("manager", "--mode", "manager_only", "--max-subagents", "6", "--stale-after-minutes", "45", "--shortcut", "--json", env=env)
    legacy = parse_json_result(legacy_result)
    disabled = json_result(
        "manager",
        "--mode",
        "manager_only",
        "--max-subagents",
        "10",
        "--stale-after-minutes",
        "45",
        "--shortcut",
        "--json",
        env={**env, "QWENDEX_MANAGER_DEPLOY_POLICY": "disabled"},
    )

    assert auto["data"]["mode"] == "auto"
    assert auto["data"]["label"] == "Auto"
    assert auto["data"]["ui_indicator"] == "(Alt+M) Agent Manager: [ Auto ]"
    assert auto["data"]["kaveman_indicator"] == "(Alt+K) Kaveman: [N]"
    assert auto["data"]["kaveman_enabled"] is False
    assert auto["data"]["local_indicator"] == "(Alt+L) Local: [Ready]"
    assert auto["data"]["offload_target"] == "auto"
    assert cycled["data"]["mode"] == "lite"
    assert status["data"]["mode"] == "lite"
    assert status["data"]["active_subagents"]["count"] == 0
    assert status["data"]["stale_sessions"]["count"] == 0
    assert len(status["data"]["high_value_add"]) <= 2

    assert legacy_result.returncode == 0
    assert legacy["status"] == "standby"
    assert legacy["data"]["mode"] == "manager"
    assert legacy["data"]["legacy_mode"] == "manager_only"
    assert legacy["data"]["label"] == "Manager Mode"
    assert legacy["data"]["manager_deploy_policy"] == "auto"
    assert legacy["data"]["deployment_contract"]["required"] is True
    assert legacy["data"]["deployment_contract"]["healthy"] is False
    assert legacy["data"]["shortcut"] == "Alt+M"
    assert legacy["data"]["shortcut_command"] == "scripts/qwendex manager mode --toggle --json"
    assert legacy["data"]["kaveman_shortcut"] == "Alt+K"
    assert legacy["data"]["kaveman_shortcut_command"] == "scripts/qwendex manager kaveman --toggle --json"
    assert legacy["data"]["max_subagents"] == 6
    assert legacy["data"]["stale_after_minutes"] == 45
    assert "LangGraph persistence" in " ".join(legacy["data"]["borrowed_patterns"])
    assert {"selected_model", "selected_reasoning", "reasoning_source", "escalation_reason", "token_saver_used", "local_qwen_eligible"} <= set(legacy["data"]["lane_template"][0])

    assert disabled["status"] == "ready"
    assert disabled["data"]["manager_deploy_policy"] == "disabled"
    assert disabled["data"]["deployment_contract"]["required"] is False
    assert disabled["data"]["deployment_contract"]["healthy"] is True
    assert disabled["data"]["max_subagents"] == 10


def test_qwendex_manager_mode_toggle_cycles_full_duty_order(tmp_path):
    env = {"QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite")}

    json_result("manager", "mode", "--set", "off", "--json", env=env)
    seen = []
    for _ in range(6):
        result = run_qwendex("manager", "mode", "--toggle", "--json", env=env)
        toggled = parse_json_result(result)
        assert result.returncode == 0
        assert toggled["status"] == "pass"
        seen.append(toggled["data"]["mode"])

    assert seen == ["auto", "lite", "medium", "heavy", "manager", "off"]

    status_result = run_qwendex("manager", "status", "--json", env=env)
    status = parse_json_result(status_result)
    assert status_result.returncode == 0
    assert status["data"]["mode"] == "off"
    assert status["data"]["agent_policy"]["mode"] == "off"
    assert status["data"]["agent_policy_source"] == "manager-mode"
    assert status["data"]["agent_policy"]["root_can_spawn"] is False
    assert status["data"]["deployment_contract"]["status"] == "ready"


def test_qwendex_selected_manager_mode_drives_agent_policy_and_hooks(tmp_path):
    env = {
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_RESULTS_ROOT": str(tmp_path / "results"),
        "CODEX_HOME": str(tmp_path / "codex_home"),
        "QWENDEX_MANAGER_ALLOW_UNHOOKED": "1",
    }

    selected = json_result("manager", "mode", "--set", "manager", "--json", env=env)
    policy = json_result("agent", "policy", "--json", env=env)
    status = json_result("manager", "status", "--json", env=env)

    assert selected["data"]["mode"] == "manager"
    assert selected["data"]["agent_policy"]["mode"] == "manager"
    assert selected["data"]["agent_policy_source"] == "manager-mode"
    assert policy["data"]["agent_policy"]["mode"] == "manager"
    assert policy["data"]["agent_policy"]["source"] == "manager-mode"
    assert status["data"]["agent_use"] == "Manager"
    assert status["data"]["agent_policy"]["require_agent_ledger"] is True

    json_result(
        "manager",
        "assign",
        "--agent-id",
        "selected-manager-required",
        "--lane",
        "review",
        "--task-id",
        "selected-manager",
        "--objective",
        "prove selected manager mode gates finalization",
        "--required",
        "--json",
        env=env,
    )
    preflight = json_result(
        "manager",
        "preflight",
        "--prompt",
        "Use manager mode with subagents to prove selected manager mode gates finalization",
        "--json",
        env=env,
    )
    manager_env = {**env, **preflight["data"]["exports"]}
    blocked_stop_result = run_qwendex(
        "agent",
        "hook",
        "Stop",
        "--event-json",
        json.dumps({"last_assistant_message": "Done."}),
        "--json",
        env=manager_env,
    )
    blocked_stop = parse_json_result(blocked_stop_result)

    assert blocked_stop_result.returncode != 0
    assert blocked_stop["data"]["agent_policy"]["mode"] == "manager"
    assert blocked_stop["data"]["agent_policy"]["source"] == "manager-mode"
    assert blocked_stop["data"]["hook_result"]["event"] == "manager.stop_gate_continued"
    assert blocked_stop["data"]["manager_decision"]["ledger_id"] == preflight["data"]["ledger_id"]

    off = json_result("manager", "mode", "--set", "off", "--json", env=env)
    spawn_result = run_qwendex(
        "agent",
        "hook",
        "PreToolUse",
        "--event-json",
        json.dumps({"tool_name": "spawn_agent"}),
        "--json",
        env=env,
    )
    spawn = parse_json_result(spawn_result)

    assert off["data"]["agent_policy"]["mode"] == "off"
    assert spawn_result.returncode != 0
    assert spawn["data"]["agent_policy"]["mode"] == "off"
    assert spawn["data"]["hook_result"]["event"] == "agent.spawn_rejected"

    override = json_result("agent", "policy", "--json", env={**env, "QWENDEX_AGENT_USE": "Heavy"})
    assert override["data"]["agent_policy"]["mode"] == "heavy"
    assert override["data"]["agent_policy"]["source"] == "qwendex-env"


def test_qwendex_manager_stop_requires_preflight_ledger(tmp_path):
    env = {"QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite")}

    json_result("manager", "mode", "--set", "manager", "--json", env=env)
    stop_result = run_qwendex(
        "agent",
        "hook",
        "Stop",
        "--event-json",
        json.dumps({"last_assistant_message": "Done."}),
        "--json",
        env=env,
    )
    stop = parse_json_result(stop_result)

    assert stop_result.returncode != 0
    assert stop["data"]["hook_result"]["event"] == "manager.unattached"
    assert stop["data"]["hook_result"]["stop_status"] == "STOP_MANAGER_UNATTACHED"




def test_qwendex_manager_stop_recovers_latest_preflight_without_exported_env(tmp_path):
    env = {
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_RESULTS_ROOT": str(tmp_path / "results"),
        "CODEX_HOME": str(tmp_path / "codex_home"),
    }

    json_result("agent", "hook-config", "--install", "--codex-home", env["CODEX_HOME"], "--json", env=env)
    json_result("manager", "mode", "--set", "manager", "--json", env=env)
    preflight = json_result("manager", "preflight", "--interactive-prompt-unknown", "--json", env=env)
    stop = json_result(
        "agent",
        "hook",
        "Stop",
        "--event-json",
        json.dumps({"last_assistant_message": "No edits. Validation: not required. Risks: none."}),
        "--json",
        env=env,
    )

    repeated_stop = run_qwendex(
        "agent",
        "hook",
        "Stop",
        "--event-json",
        json.dumps({"last_assistant_message": "No edits. Validation: already closed. Risks: none."}),
        "--codex-hook-output",
        env=env,
    )

    assert preflight["data"]["routing_decision"]["direct_work_exception"] is True
    assert stop["data"]["hook_result"]["event"] == "manager.finalized"
    assert stop["data"]["manager_decision"]["ledger_id"] == preflight["data"]["ledger_id"]
    assert repeated_stop.returncode == 0
    assert json.loads(repeated_stop.stdout) == {}


def test_qwendex_manager_stop_uses_generated_runtime_env_when_state_env_is_dropped(tmp_path):
    qwendex = load_qwendex()
    real_root = tmp_path / "real"
    link_root = tmp_path / "linked"
    work_root = real_root / ".qwendex-dev"
    codex_home = work_root / "codex_home"
    real_root.mkdir()
    link_root.symlink_to(real_root, target_is_directory=True)
    linked_codex_home = link_root / ".qwendex-dev" / "codex_home"
    env = {
        "CODEX_HOME": str(linked_codex_home),
        "QWENDEX_AGENT_USE": "Manager",
        "QWENDEX_STATE_DB": "",
        "QWENDEX_LEDGER_DB": "",
        "QWENDEX_RESULTS_ROOT": "",
    }

    installed = json_result("agent", "hook-config", "--install", "--codex-home", str(codex_home), "--json", env=env)
    json_result("manager", "mode", "--set", "manager", "--json", env=env)
    preflight = json_result("manager", "preflight", "--interactive-prompt-unknown", "--json", env=env)
    payload = json.loads((codex_home / "hooks.json").read_text(encoding="utf-8"))
    stop_command = payload["hooks"]["Stop"][0]["hooks"][0]["command"]
    hook_env = {
        "HOME": os.environ.get("HOME", ""),
        "PATH": os.environ.get("PATH", ""),
        "CODEX_HOME": str(codex_home),
        "QWENDEX_AGENT_USE": "Manager",
        "QWENDEX_STATE_DB": "",
        "QWENDEX_LEDGER_DB": "",
        "QWENDEX_RESULTS_ROOT": "",
    }
    stop = subprocess.run(
        stop_command,
        cwd=ROOT,
        env=hook_env,
        input=json.dumps({"last_assistant_message": "No edits. Validation: not required. Risks: none."}),
        text=True,
        shell=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    decision = json_result("manager", "decision", "--json", env=env)["data"]["manager_decision"]

    assert installed["data"]["hook_status"]["missing_runtime_env_events"] == []
    assert "QWENDEX_STATE_DB=" in stop_command
    assert stop.returncode == 0, stop.stderr or stop.stdout
    assert json.loads(stop.stdout) == {}
    assert preflight["data"]["ledger_id"] == decision["ledger_id"]
    assert qwendex.path_digest_policy(codex_home) == qwendex.path_digest_policy(linked_codex_home)
    assert decision["codex_home_digest_or_path_policy"] == qwendex.path_digest_policy(codex_home)
    assert decision["stop_status"] == "STOP_MANAGER_CLOSED"
    assert (work_root / "state" / "qwendex.sqlite").is_file()
    receipt_paths = decision["receipt_paths"]
    assert receipt_paths
    assert all(".qwendex-dev/results/qwendex" in receipt for receipt in receipt_paths)


def test_qwendex_manager_preflight_records_decision_ledger_and_hook_status(tmp_path):
    qwendex = load_qwendex()
    env = {
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_RESULTS_ROOT": str(tmp_path / "results"),
        "CODEX_HOME": str(tmp_path / "codex_home"),
        "QWENDEX_FORCE_LOCAL_QWEN_AVAILABLE": "0",
    }

    json_result("manager", "mode", "--set", "manager", "--json", env=env)
    blocked_result = run_qwendex("manager", "preflight", "--interactive-prompt-unknown", "--dry-run", "--json", env=env)
    blocked = parse_json_result(blocked_result)
    assert blocked_result.returncode != 0
    assert blocked["data"]["mode"] == "manager"
    assert blocked["data"]["agent_use"] == "Manager"
    assert blocked["data"]["policy_source"] == "manager-mode"
    assert blocked["data"]["hook_status"]["hook_source_count"] == 0
    assert blocked["data"]["routing_decision"]["selected_route"] == "blocked"
    assert blocked["data"]["stop_status"] == "STOP_MANAGER_BLOCKED_UNHOOKED"

    partial_codex_home = tmp_path / "partial_codex_home"
    partial_codex_home.mkdir()
    (partial_codex_home / "hooks.json").write_text(
        json.dumps({
            "hooks": {
                "Stop": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": "scripts/qwendex agent hook Stop --json",
                            }
                        ]
                    }
                ]
            }
        }),
        encoding="utf-8",
    )
    partial_result = run_qwendex(
        "manager",
        "preflight",
        "--interactive-prompt-unknown",
        "--dry-run",
        "--json",
        env={**env, "CODEX_HOME": str(partial_codex_home)},
    )
    partial = parse_json_result(partial_result)
    assert partial_result.returncode != 0
    assert partial["data"]["hook_status"]["hook_source_count"] == 1
    assert partial["data"]["hook_status"]["compatible_hook_source_count"] == 0
    assert partial["data"]["hook_status"]["verified"] is False
    assert partial["data"]["hook_status"]["incompatible_events"] == ["Stop"]
    assert "UserPromptSubmit" in partial["data"]["hook_status"]["missing_events"]
    assert partial["data"]["routing_decision"]["selected_route"] == "blocked"
    assert partial["data"]["stop_status"] == "STOP_MANAGER_BLOCKED_UNHOOKED"

    stale_codex_home = tmp_path / "stale_codex_home"
    stale_codex_home.mkdir()
    stale_hooks = {
        "hooks": {
            event_name: [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": f"scripts/qwendex agent hook {event_name} --json",
                        }
                    ]
                }
            ]
            for event_name in qwendex.MANAGED_AGENT_HOOKS
        }
    }
    (stale_codex_home / "hooks.json").write_text(json.dumps(stale_hooks), encoding="utf-8")
    stale_result = run_qwendex(
        "manager",
        "preflight",
        "--interactive-prompt-unknown",
        "--dry-run",
        "--json",
        env={**env, "CODEX_HOME": str(stale_codex_home)},
    )
    stale = parse_json_result(stale_result)
    assert stale_result.returncode != 0
    assert stale["data"]["hook_status"]["hook_source_count"] == len(qwendex.MANAGED_AGENT_HOOKS)
    assert stale["data"]["hook_status"]["compatible_hook_source_count"] == 0
    assert stale["data"]["hook_status"]["verified"] is False
    assert set(stale["data"]["hook_status"]["incompatible_events"]) == set(qwendex.MANAGED_AGENT_HOOKS)
    assert stale["data"]["routing_decision"]["selected_route"] == "blocked"
    assert stale["data"]["stop_status"] == "STOP_MANAGER_BLOCKED_UNHOOKED"

    plain_codex_home = tmp_path / "plain_codex_home"
    plain_codex_home.mkdir()
    plain_hooks = {
        "hooks": {
            event_name: [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": f"scripts/qwendex agent hook {event_name} --codex-hook-output",
                        }
                    ]
                }
            ]
            for event_name in qwendex.MANAGED_AGENT_HOOKS
        }
    }
    (plain_codex_home / "hooks.json").write_text(json.dumps(plain_hooks), encoding="utf-8")
    plain_result = run_qwendex(
        "manager",
        "preflight",
        "--interactive-prompt-unknown",
        "--dry-run",
        "--json",
        env={**env, "CODEX_HOME": str(plain_codex_home)},
    )
    plain = parse_json_result(plain_result)
    assert plain_result.returncode != 0
    assert plain["data"]["hook_status"]["hook_source_count"] == len(qwendex.MANAGED_AGENT_HOOKS)
    assert plain["data"]["hook_status"]["compatible_hook_source_count"] == len(qwendex.MANAGED_AGENT_HOOKS)
    assert plain["data"]["hook_status"]["verified"] is False
    assert set(plain["data"]["hook_status"]["missing_runtime_env_events"]) == set(qwendex.MANAGED_AGENT_HOOKS)
    assert plain["data"]["routing_decision"]["selected_route"] == "blocked"
    assert plain["data"]["stop_status"] == "STOP_MANAGER_BLOCKED_UNHOOKED"

    installed = json_result("agent", "hook-config", "--install", "--codex-home", env["CODEX_HOME"], "--json", env=env)
    assert installed["data"]["hook_status"]["verified"] is True
    assert installed["data"]["hook_status"]["compatible_hook_source_count"] == len(qwendex.MANAGED_AGENT_HOOKS)

    ready = json_result(
        "manager",
        "preflight",
        "--prompt",
        "Small edit with validation evidence",
        "--json",
        env={**env, "QWENDEX_MANAGER_ALLOW_UNHOOKED": "1"},
    )
    assert ready["data"]["ok"] is True
    assert ready["data"]["hook_status"]["verified"] is True
    assert ready["data"]["hook_status"]["override"] is False
    assert ready["data"]["ledger_id"].startswith("mgrldg_")
    assert ready["data"]["prompt"]["known"] is True
    assert ready["data"]["prompt"]["prompt_digest"]
    assert ready["data"]["prompt"]["prompt_summary"] == "Small edit with validation evidence"
    assert ready["data"]["manager_estimate"]["created"] is True
    assert ready["data"]["routing_decision"]["selected_route"] in {"direct_single_writer", "manager_subagents"}
    assert ready["data"]["routing_decision"]["verifier_required"] is True
    assert ready["data"]["routing_decision"]["validation_plan"]
    receipt = Path(ready["data"]["receipt_paths"][0])
    assert receipt.exists()

    decision = json_result("manager", "decision", "--agent-id", ready["data"]["ledger_id"], "--json", env=env)
    assert decision["data"]["manager_decision"]["record_type"] == "manager_decision"
    assert decision["data"]["manager_decision"]["ledger_id"] == ready["data"]["ledger_id"]

    env_override = json_result(
        "manager",
        "preflight",
        "--interactive-prompt-unknown",
        "--dry-run",
        "--json",
        env={**env, "QWENDEX_AGENT_USE": "Heavy"},
    )
    assert env_override["data"]["manager_required"] is True
    assert env_override["data"]["selected_manager_mode"] == "manager"
    assert env_override["data"]["effective_agent_mode"] == "heavy"
    assert env_override["data"]["policy_source"] == "qwendex-env"


def test_qwendex_manager_local_toggle_controls_local_lane_eligibility(tmp_path):
    env = {
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_FORCE_LOCAL_QWEN_AVAILABLE": "1",
    }

    off = json_result("manager", "local", "--set", "off", "--json", env=env)
    estimate_off = json_result("manager", "estimate", "--prompt", "Summarize receipts from results/qwendex.", "--json", env=env)
    on = json_result("manager", "local", "--toggle", "--json", env=env)
    estimate_on = json_result("manager", "estimate", "--prompt", "Summarize receipts from results/qwendex.", "--json", env=env)

    assert off["data"]["local_indicator"] == "(Alt+L) Local: [Off]"
    assert off["data"]["local_subagents"]["enabled"] is False
    assert estimate_off["data"]["local_subagents"]["usable"] is False
    assert estimate_off["data"]["estimate"]["higher_reasoning_lanes"] == []
    assert estimate_off["data"]["reasoning_policy"]["default_lane"]["local_qwen_eligible"] is False

    assert on["data"]["local_indicator"] == "(Alt+L) Local: [Ready]"
    assert on["data"]["local_subagents"]["enabled"] is True
    assert estimate_on["data"]["local_subagents"]["usable"] is True
    assert estimate_on["data"]["reasoning_policy"]["default_lane"]["local_qwen_eligible"] is True


def test_qwendex_manager_estimate_is_bounded_and_reasoning_agnostic():
    simple = json_result("manager", "estimate", "--prompt", "Fix a typo in public docs.", "--json")
    top_level = json_result("estimate", "--prompt", "Fix a typo in public docs.", "--json")
    heavy = json_result(
        "manager",
        "estimate",
        "--prompt",
        "Change security architecture, protocol routing, docs, and release tests across several files.",
        "--json",
    )

    assert simple["data"]["estimate"]["recommended_mode"] in {"auto", "lite"}
    assert top_level["command"] == "estimate"
    assert top_level["data"]["estimate"] == simple["data"]["estimate"]
    assert top_level["data"]["reasoning_policy"] == simple["data"]["reasoning_policy"]
    assert simple["data"]["estimator"]["model"] == "gpt-5.5"
    assert simple["data"]["estimator"]["reasoning"] == "medium"
    assert simple["data"]["reasoning_policy"]["main_session"]["reasoning_source"] == "user_selected"
    for field in (
        "task_complexity",
        "risk",
        "likely_file_scope",
        "validation_depth",
        "subagent_usefulness",
        "recommended_mode",
        "confidence",
        "higher_reasoning_lanes",
    ):
        assert field in simple["data"]["estimate"]

    assert heavy["data"]["estimate"]["recommended_mode"] in {"heavy", "manager"}
    assert heavy["data"]["estimate"]["higher_reasoning_lanes"]
    escalated = heavy["data"]["estimate"]["higher_reasoning_lanes"][0]
    assert escalated["selected_reasoning"] in {"high", "xhigh"}
    assert escalated["escalation_reason"]
    assert len(heavy["data"]["high_value_add"]) <= 2


def test_qwendex_agent_policy_selector_precedence_fallback_and_strict(tmp_path):
    env = {
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_AGENT_USE": "manager-mode",
    }
    env_policy = json_result("agent", "policy", "--json", env=env)
    cli_policy = json_result(
        "--agent-use",
        "Lite",
        "agent",
        "policy",
        "--json",
        env={**env, "QWENDEX_AGENT_USE": "Manager"},
    )
    fallback = json_result(
        "agent",
        "policy",
        "--json",
        env={"QWENDEX_STATE_DB": str(tmp_path / "fallback.sqlite"), "QWENDEX_AGENT_USE": "sideways"},
    )
    strict_result = run_qwendex(
        "agent",
        "policy",
        "--json",
        env={
            "QWENDEX_STATE_DB": str(tmp_path / "strict.sqlite"),
            "QWENDEX_AGENT_USE": "sideways",
            "QWENDEX_AGENT_USE_STRICT": "1",
        },
    )
    strict = parse_json_result(strict_result)

    assert env_policy["data"]["agent_policy"]["mode"] == "manager"
    assert env_policy["data"]["agent_policy"]["source"] == "qwendex-env"
    assert len(env_policy["data"]["agent_policy"]["policy_hash"]) == 64
    assert env_policy["data"]["agent_policy"]["env"]["QWENDEX_EFFECTIVE_AGENT_USE"] == "Manager"
    assert env_policy["data"]["agent_policy"]["require_agent_ledger"] is True
    assert env_policy["data"]["agent_policy"]["child_can_spawn"] is False
    assert "spawn_agent" in env_policy["data"]["agent_policy"]["tool_surface"]["root_management_tools"]
    assert "spawn_agent" in env_policy["data"]["agent_policy"]["tool_surface"]["denied_child_tools"]

    assert cli_policy["data"]["agent_policy"]["mode"] == "lite"
    assert cli_policy["data"]["agent_policy"]["source"] == "cli"
    assert cli_policy["data"]["agent_policy"]["root_can_spawn"] is False

    assert fallback["data"]["agent_policy"]["mode"] == "medium"
    assert fallback["data"]["agent_policy"]["warnings"]
    assert fallback["data"]["agent_policy"]["source"] == "qwendex-env-fallback"

    assert strict_result.returncode != 0
    assert strict["status"] == "blocked"
    assert "invalid agent use selector" in " ".join(strict["errors"])


def test_qwendex_agent_status_alias_tracks_manager_ledger_and_bounded_close(tmp_path):
    env = {
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_FORCE_LOCAL_QWEN_AVAILABLE": "1",
    }

    assigned = json_result(
        "manager",
        "assign",
        "--agent-id",
        "agent-ledger-1",
        "--lane",
        "verification",
        "--task-id",
        "task-agent",
        "--write-surface",
        "read-only",
        "--json",
        env=env,
    )
    status = json_result("--agent-use", "Manager", "agent", "status", "--json", env=env)
    inspected = json_result("agent", "inspect", "agent-ledger-1", "--json", env=env)
    closed = json_result("agent", "close", "agent-ledger-1", "--timeout", "1s", "--reason", "integrated", "--json", env=env)
    after = json_result("--agent-use", "Manager", "agent", "status", "--json", env=env)

    assert assigned["data"]["agent_session"]["status"] == "active"
    assert status["data"]["mode"] == "manager"
    assert status["data"]["agent_use"] == "Manager"
    assert status["data"]["agent_policy"]["source"] == "cli"
    assert status["data"]["active_subagents"]["count"] == 1
    assert inspected["data"]["agent_session"]["agent_id"] == "agent-ledger-1"
    assert closed["data"]["closed_count"] == 1
    assert closed["data"]["bounded_close"] is True
    assert closed["data"]["close_timeout_ms"] == 1000
    assert closed["data"]["closed"][0]["status"] == "closed"
    assert closed["data"]["closed"][0]["stop_reason"] == "integrated"
    assert after["data"]["active_subagents"]["count"] == 0


def test_qwendex_agent_profiles_and_team_are_visible():
    profiles = json_result("agent", "profiles", "--json")
    team = json_result("agent", "team", "--json")

    assert {"explorer", "implementer", "verifier", "docs_researcher", "release_manager", "scribe"} <= set(profiles["data"]["profiles"])
    assert profiles["data"]["profiles"]["explorer"]["sandbox_mode"] == "read-only"
    assert profiles["data"]["profiles"]["explorer"]["can_spawn"] is False
    assert "publish" in profiles["data"]["profiles"]["release_manager"]["tools_deny"]
    assert team["data"]["team"]["default_mode"] == "Manager"
    assert "verifier" in team["data"]["team"]["required_lanes_by_task"]["code_edit_complex"]


def test_qwendex_agent_plan_routes_direct_team_and_release(tmp_path):
    env = {"QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite")}

    direct = json_result(
        "--agent-use",
        "Lite",
        "agent",
        "plan",
        "--prompt",
        "What command lists active Qwendex agents?",
        "--task-id",
        "task-direct",
        "--json",
        env=env,
    )
    team = json_result(
        "--agent-use",
        "Manager",
        "agent",
        "plan",
        "--prompt",
        "Team, change manager routing across code and tests.",
        "--task-id",
        "task-team",
        "--json",
        env=env,
    )
    release = json_result(
        "--agent-use",
        "Heavy",
        "agent",
        "plan",
        "--prompt",
        "Prepare release notes and version bump for publish.",
        "--task-id",
        "task-release",
        "--json",
        env=env,
    )

    direct_plan = direct["data"]["agent_plan"]
    team_plan = team["data"]["agent_plan"]
    release_plan = release["data"]["agent_plan"]

    assert direct_plan["direct_work"] is True
    assert direct_plan["assignments"] == []
    assert "trivial" in direct_plan["direct_work_exception"]

    assert team_plan["direct_work"] is False
    assert {"explorer", "implementer", "verifier", "scribe"} <= set(team_plan["profiles"])
    assert all("scripts/qwendex manager assign" in item["assign_command"] for item in team_plan["assignments"])
    assert any("--required" in item["assign_command"] for item in team_plan["assignments"])
    assert any(item["profile"] == "scribe" and item["required"] is False for item in team_plan["assignments"])

    assert release_plan["profiles"] == ["release_manager", "verifier"]
    assert release_plan["assignments"][0]["routing"]["selected_reasoning"] in {"high", "xhigh"}
    assert "task-release" in release_plan["assignments"][0]["assign_command"]


def test_qwendex_agent_metrics_track_ledger_and_artifacts(tmp_path):
    env = {"QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite")}

    empty = json_result("agent", "metrics", "--json", env=env)
    assert empty["data"]["agent_metrics"]["session_count"] == 0
    assert empty["data"]["agent_metrics"]["final_contract_compliance"] is None

    json_result(
        "manager",
        "assign",
        "--agent-id",
        "metrics-agent",
        "--lane",
        "verification",
        "--task-id",
        "task-metrics",
        "--required",
        "--json",
        env=env,
    )
    active = json_result("agent", "metrics", "--json", env=env)
    assert active["data"]["agent_metrics"]["active_count"] == 1
    assert active["data"]["agent_metrics"]["required_incomplete_count"] == 1

    json_result(
        "agent",
        "hook",
        "SubagentStop",
        "--event-json",
        json.dumps({
            "agent_id": "metrics-agent",
            "last_assistant_message": "FINAL_REPORT\nstatus: completed\nsummary: metric proof\nevidence:\n- ok",
        }),
        "--json",
        env=env,
    )
    metrics = json_result("agent", "metrics", "--json", env=env)["data"]["agent_metrics"]
    assert metrics["terminal_count"] == 1
    assert metrics["required_incomplete_count"] == 0
    assert metrics["final_contract_compliance"] == 1.0
    assert metrics["raw_output_artifact_count"] == 1
    assert metrics["managed_hook_event_count"] >= 5


def test_qwendex_agent_hooks_enforce_final_contract_and_manager_stop_gate(tmp_path):
    env = {
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_RESULTS_ROOT": str(tmp_path / "results"),
        "CODEX_HOME": str(tmp_path / "codex_home"),
        "QWENDEX_MANAGER_ALLOW_UNHOOKED": "1",
    }

    prompt_hook = json_result(
        "--agent-use",
        "Manager",
        "agent",
        "hook",
        "UserPromptSubmit",
        "--event-json",
        "{}",
        "--json",
        env=env,
    )
    raw_prompt_hook = run_qwendex(
        "--agent-use",
        "Manager",
        "agent",
        "hook",
        "UserPromptSubmit",
        "--event-json",
        "{}",
        "--codex-hook-output",
        env=env,
    )
    raw_prompt = json.loads(raw_prompt_hook.stdout)
    assert raw_prompt_hook.returncode == 0
    assert set(raw_prompt) == {"hookSpecificOutput"}
    assert "root orchestrator" in raw_prompt["hookSpecificOutput"]["additionalContext"]
    assert "status" not in raw_prompt
    assigned = json_result(
        "manager",
        "assign",
        "--agent-id",
        "agent-hook-verifier",
        "--lane",
        "verification",
        "--task-id",
        "task-hook",
        "--required",
        "--json",
        env=env,
    )
    preflight = json_result(
        "manager",
        "preflight",
        "--prompt",
        "Use manager mode with subagents and verifier evidence for this edit",
        "--json",
        env=env,
    )
    manager_env = {**env, **preflight["data"]["exports"]}
    blocked_stop_result = run_qwendex(
        "--agent-use",
        "Manager",
        "agent",
        "hook",
        "Stop",
        "--event-json",
        json.dumps({"last_assistant_message": "Done.", "edit_happened": True}),
        "--json",
        env=manager_env,
    )
    missing_contract_result = run_qwendex(
        "agent",
        "hook",
        "SubagentStop",
        "--event-json",
        json.dumps({"agent_id": "agent-hook-verifier", "last_assistant_message": "Ready when you are."}),
        "--json",
        env=env,
    )
    completed = json_result(
        "agent",
        "hook",
        "SubagentStop",
        "--event-json",
        json.dumps({
            "agent_id": "agent-hook-verifier",
            "last_assistant_message": "FINAL_REPORT\nstatus: completed\nagent_id: agent-hook-verifier\nevidence:\n- pytest passed",
        }),
        "--json",
        env=env,
    )
    missing_summary_result = run_qwendex(
        "--agent-use",
        "Manager",
        "agent",
        "hook",
        "Stop",
        "--event-json",
        json.dumps({"last_assistant_message": "Done.", "edit_happened": True}),
        "--json",
        env=manager_env,
    )
    passed_stop = json_result(
        "--agent-use",
        "Manager",
        "agent",
        "hook",
        "Stop",
        "--event-json",
        json.dumps({"last_assistant_message": "Agent outcomes: verifier passed.\nValidation: pytest.\nRisks: none.", "edit_happened": True}),
        "--json",
        env=manager_env,
    )

    blocked_stop = parse_json_result(blocked_stop_result)
    missing_contract = parse_json_result(missing_contract_result)
    missing_summary = parse_json_result(missing_summary_result)

    assert "root orchestrator" in prompt_hook["data"]["hook_result"]["hookSpecificOutput"]["additionalContext"]
    assert assigned["data"]["agent_session"]["context_packet"]["required"] is True
    assert blocked_stop_result.returncode != 0
    assert blocked_stop["data"]["hook_result"]["event"] == "manager.stop_gate_continued"
    assert "agent-hook-verifier:active" in blocked_stop["data"]["hook_result"]["reason"]
    assert blocked_stop["data"]["manager_decision"]["ledger_id"] == preflight["data"]["ledger_id"]
    assert missing_contract_result.returncode != 0
    assert missing_contract["data"]["hook_result"]["event"] == "agent.final_contract_missing"
    assert completed["data"]["hook_result"]["event"] == "agent.completed"
    assert completed["data"]["agent_session"]["status"] == "completed"
    assert completed["data"]["agent_session"]["validation_status"] == "pass"
    artifacts = completed["data"]["agent_session"]["artifacts"]
    raw_artifact = next(path for path in artifacts if path.endswith("/raw-output.md"))
    compact_artifact = next(path for path in artifacts if path.endswith("/compact-report.json"))
    assert "FINAL_REPORT" in (ROOT / raw_artifact).read_text(encoding="utf-8")
    compact_report = json.loads((ROOT / compact_artifact).read_text(encoding="utf-8"))
    assert compact_report["agent_id"] == "agent-hook-verifier"
    assert compact_report["status"] == "completed"
    logs = json_result("agent", "logs", "agent-hook-verifier", "--json", env=env)
    assert raw_artifact in logs["data"]["raw_output_artifacts"]
    assert missing_summary_result.returncode != 0
    assert missing_summary["data"]["hook_result"]["event"] == "manager.final_contract_missing"
    assert passed_stop["data"]["hook_result"]["event"] == "manager.finalized"
    assert passed_stop["data"]["manager_decision"]["stop_status"] == "STOP_MANAGER_CLOSED"


def test_qwendex_manager_direct_work_exception_requires_validation_evidence(tmp_path):
    env = {
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_RESULTS_ROOT": str(tmp_path / "results"),
        "CODEX_HOME": str(tmp_path / "codex_home"),
        "QWENDEX_MANAGER_ALLOW_UNHOOKED": "1",
    }

    json_result("manager", "mode", "--set", "manager", "--json", env=env)
    preflight = json_result("manager", "preflight", "--interactive-prompt-unknown", "--json", env=env)
    manager_env = {**env, **preflight["data"]["exports"]}
    assert preflight["data"]["routing_decision"]["selected_route"] == "direct_single_writer"
    assert preflight["data"]["routing_decision"]["direct_work_exception"] is True

    missing_validation_result = run_qwendex(
        "agent",
        "hook",
        "Stop",
        "--event-json",
        json.dumps({
            "last_assistant_message": "Agent outcomes: direct writer.\nRisks: none.",
            "edit_happened": True,
            "dirty_worktree_classification": "in-scope",
        }),
        "--json",
        env=manager_env,
    )
    missing_validation = parse_json_result(missing_validation_result)
    assert missing_validation_result.returncode != 0
    assert missing_validation["data"]["hook_result"]["event"] == "manager.validation_pending"
    assert missing_validation["data"]["hook_result"]["stop_status"] == "STOP_MANAGER_VALIDATION_PENDING"

    negative_validation_result = run_qwendex(
        "agent",
        "hook",
        "Stop",
        "--event-json",
        json.dumps({
            "last_assistant_message": "Agent outcomes: direct writer.\nValidation: not run.\nDirty: in-scope docs only.\nRisks: none.",
            "edit_happened": True,
            "dirty_worktree_classification": "in-scope",
        }),
        "--json",
        env=manager_env,
    )
    negative_validation = parse_json_result(negative_validation_result)
    assert negative_validation_result.returncode != 0
    assert negative_validation["data"]["hook_result"]["event"] == "manager.validation_pending"
    assert negative_validation["data"]["hook_result"]["stop_status"] == "STOP_MANAGER_VALIDATION_PENDING"

    closed = json_result(
        "agent",
        "hook",
        "Stop",
        "--event-json",
        json.dumps({
            "last_assistant_message": "Agent outcomes: direct writer.\nValidation: pytest.\nDirty: in-scope docs only.\nRisks: none.",
            "edit_happened": True,
            "dirty_worktree_classification": "in-scope",
            "validation_evidence": ["pytest"],
        }),
        "--json",
        env=manager_env,
    )
    assert closed["data"]["hook_result"]["event"] == "manager.finalized"
    assert closed["data"]["manager_decision"]["final_status"] == "closed"
    assert closed["data"]["manager_decision"]["stop_status"] == "STOP_MANAGER_CLOSED"
    receipt_payload = json.loads(Path(closed["data"]["manager_decision"]["receipt_paths"][0]).read_text(encoding="utf-8"))
    assert receipt_payload["stop_status"] == "STOP_MANAGER_CLOSED"
    assert receipt_payload["routing_decision"]["direct_work_exception"] is True


def test_qwendex_agent_hook_config_generation_and_write_gate(tmp_path):
    target = tmp_path / "hooks.json"
    codex_home = tmp_path / "codex_home"

    env = {
        "CODEX_HOME": str(codex_home),
        "QWENDEX_STATE_DB": str(tmp_path / "state" / "qwendex.sqlite"),
        "QWENDEX_LEDGER_DB": str(tmp_path / "state" / "qwendex_ledger.sqlite"),
        "QWENDEX_RESULTS_ROOT": str(tmp_path / "results" / "qwendex"),
        "QWENDEX_CODEX_STATUS_FILE": str(tmp_path / "codex_status.json"),
    }

    rendered = json_result("agent", "hook-config", "--qwendex-command", "scripts/qwendex", "--json", env=env)
    hooks = rendered["data"]["hook_config"]["hooks"]
    stop_command = hooks["Stop"][0]["hooks"][0]["command"]
    assert {"UserPromptSubmit", "SubagentStart", "SubagentStop", "Stop", "PreToolUse"} <= set(hooks)
    assert stop_command.startswith("env ")
    assert "QWENDEX_STATE_DB=" in stop_command
    assert stop_command.endswith("scripts/qwendex agent hook Stop --codex-hook-output")
    assert hooks["PreToolUse"][0]["hooks"][0]["timeout"] == 5

    blocked_result = run_qwendex("agent", "hook-config", "--write", str(target), "--json", env=env)
    blocked = parse_json_result(blocked_result)
    assert blocked_result.returncode != 0
    assert blocked["status"] == "blocked"
    assert not target.exists()

    written = json_result(
        "agent",
        "hook-config",
        "--qwendex-command",
        "scripts/qwendex",
        "--write",
        str(target),
        "--approve",
        "--json",
        env=env,
    )
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert written["artifacts"] == [str(target)]
    assert "QWENDEX_STATE_DB=" in payload["hooks"]["SubagentStop"][0]["hooks"][0]["command"]
    assert payload["hooks"]["SubagentStop"][0]["hooks"][0]["command"].endswith("scripts/qwendex agent hook SubagentStop --codex-hook-output")

    overwrite_result = run_qwendex("agent", "hook-config", "--write", str(target), "--approve", "--json", env=env)
    overwrite = parse_json_result(overwrite_result)
    assert overwrite_result.returncode != 0
    assert overwrite["status"] == "blocked"

    verify_missing_result = run_qwendex("agent", "hook-config", "--verify", "--codex-home", str(codex_home), "--json", env=env)
    verify_missing = parse_json_result(verify_missing_result)
    assert verify_missing_result.returncode != 0
    assert verify_missing["data"]["hook_status"]["hook_source_count"] == 0
    assert verify_missing["data"]["hook_status"]["compatible_hook_source_count"] == 0

    installed = json_result("agent", "hook-config", "--install", "--codex-home", str(codex_home), "--json", env=env)
    assert installed["data"]["operator_action"] == "install"
    assert installed["data"]["hook_status"]["verified"] is True
    assert installed["data"]["hook_status"]["compatible_hook_source_count"] >= 7
    verified = json_result("agent", "hook-config", "--verify", "--codex-home", str(codex_home), "--json", env=env)
    assert verified["data"]["hook_status"]["hook_source_count"] >= 7
    assert verified["data"]["hook_status"]["compatible_hook_source_count"] >= 7
    assert verified["data"]["hook_status"]["missing_runtime_env_events"] == []
    assert verified["data"]["hook_status"]["verified"] is True


def test_qwendex_agent_pre_tool_hook_denies_unsafe_actions(tmp_path):
    env = {"QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite")}

    child_spawn = run_qwendex(
        "--agent-use",
        "Manager",
        "agent",
        "hook",
        "PreToolUse",
        "--event-json",
        json.dumps({"tool_name": "spawn_agent", "depth": 1}),
        "--json",
        env=env,
    )
    read_only_write = run_qwendex(
        "agent",
        "hook",
        "PreToolUse",
        "--event-json",
        json.dumps({"tool_name": "apply_patch", "profile": "explorer"}),
        "--json",
        env=env,
    )
    release_publish = run_qwendex(
        "agent",
        "hook",
        "PreToolUse",
        "--event-json",
        json.dumps({"tool_name": "exec_command", "command": "git push --tags"}),
        "--json",
        env=env,
    )
    release_approved = json_result(
        "agent",
        "hook",
        "PreToolUse",
        "--event-json",
        json.dumps({"tool_name": "exec_command", "command": "git push --tags", "release_approved": True}),
        "--json",
        env=env,
    )
    inline_release_approved = json_result(
        "agent",
        "hook",
        "PreToolUse",
        "--event-json",
        json.dumps({"tool_name": "exec_command", "command": "QWENDEX_RELEASE_APPROVED=1 gh release create v0.3.1"}),
        "--json",
        env=env,
    )
    env_release_approved = json_result(
        "agent",
        "hook",
        "PreToolUse",
        "--event-json",
        json.dumps({"tool_name": "exec_command", "command": "env QWENDEX_RELEASE_APPROVED=1 gh release upload v0.3.1 dist.tgz"}),
        "--json",
        env=env,
    )
    env_ignore_release_approved = json_result(
        "agent",
        "hook",
        "PreToolUse",
        "--event-json",
        json.dumps({"tool_name": "exec_command", "command": "env -i QWENDEX_RELEASE_APPROVED=1 gh release upload v0.3.1 dist.tgz"}),
        "--json",
        env=env,
    )
    env_unset_release_approved = json_result(
        "agent",
        "hook",
        "PreToolUse",
        "--event-json",
        json.dumps({"tool_name": "exec_command", "command": "env -u HOME QWENDEX_RELEASE_APPROVED=1 gh release upload v0.3.1 dist.tgz"}),
        "--json",
        env=env,
    )
    env_split_release_approved = json_result(
        "agent",
        "hook",
        "PreToolUse",
        "--event-json",
        json.dumps({"tool_name": "exec_command", "command": "env -S 'QWENDEX_RELEASE_APPROVED=1 gh release upload v0.3.1 dist.tgz'"}),
        "--json",
        env=env,
    )
    env_long_split_release_approved = json_result(
        "agent",
        "hook",
        "PreToolUse",
        "--event-json",
        json.dumps({"tool_name": "exec_command", "command": "env --split-string='QWENDEX_RELEASE_APPROVED=1 gh release upload v0.3.1 dist.tgz'"}),
        "--json",
        env=env,
    )
    export_release_approved = json_result(
        "agent",
        "hook",
        "PreToolUse",
        "--event-json",
        json.dumps({"tool_name": "exec_command", "command": "export QWENDEX_RELEASE_APPROVED=1; gh release edit v0.3.1 --draft=false"}),
        "--json",
        env=env,
    )
    parent_env_release_approved = json_result(
        "agent",
        "hook",
        "PreToolUse",
        "--event-json",
        json.dumps({"tool_name": "exec_command", "command": "gh release delete v0.3.1 --yes"}),
        "--json",
        env={**env, "QWENDEX_RELEASE_APPROVED": "true"},
    )
    dev_branch_push = json_result(
        "agent",
        "hook",
        "PreToolUse",
        "--event-json",
        json.dumps({"tool_name": "exec_command", "command": "git push origin dev/some-branch"}),
        "--json",
        env=env,
    )
    equivalent_release_results = [
        run_qwendex(
            "agent",
            "hook",
            "PreToolUse",
            "--event-json",
            json.dumps({"tool_name": "exec_command", "command": command}),
            "--json",
            env=env,
        )
        for command in (
            "gh release upload v0.3.1 dist.tgz",
            "gh release edit v0.3.1 --draft=false",
            "gh release delete v0.3.1 --yes",
            "env -i gh release upload v0.3.1 dist.tgz",
            "env -u HOME gh release upload v0.3.1 dist.tgz",
            "env -S 'gh release upload v0.3.1 dist.tgz'",
            "env --split-string='gh release upload v0.3.1 dist.tgz'",
            "gh api repos/owner/repo/releases -X POST -f tag_name=v0.3.1",
            "gh api repos/owner/repo/releases/123 --method PATCH -f name=v0.3.1",
            "QWENDEX_RELEASE_APPROVED=1 echo ok && gh release create v0.3.1",
            "twine upload",
            "python -m twine upload",
            "python3 -m twine upload",
            "poetry publish",
            "uv publish",
            "hatch publish",
            "git push origin v0.3.1",
            "git push origin refs/tags/v0.3.1",
        )
    ]
    raw_child_spawn = run_qwendex(
        "--agent-use",
        "Manager",
        "agent",
        "hook",
        "PreToolUse",
        "--event-json",
        json.dumps({"tool_name": "spawn_agent", "depth": 1}),
        "--codex-hook-output",
        env=env,
    )
    raw_child_spawn_data = json.loads(raw_child_spawn.stdout)

    child_spawn_data = parse_json_result(child_spawn)
    read_only_write_data = parse_json_result(read_only_write)
    release_publish_data = parse_json_result(release_publish)

    assert child_spawn.returncode != 0
    assert raw_child_spawn.returncode == 0
    assert raw_child_spawn_data["decision"] == "block"
    assert "Child agents cannot use" in raw_child_spawn_data["reason"]
    assert "status" not in raw_child_spawn_data
    assert child_spawn_data["data"]["hook_result"]["event"] == "agent.spawn_rejected"
    assert read_only_write.returncode != 0
    assert read_only_write_data["data"]["hook_result"]["event"] == "agent.write_rejected"
    assert release_publish.returncode != 0
    assert release_publish_data["data"]["hook_result"]["event"] == "agent.release_command_rejected"
    assert release_approved["data"]["hook_result"] == {}
    assert inline_release_approved["data"]["hook_result"] == {}
    assert env_release_approved["data"]["hook_result"] == {}
    assert env_ignore_release_approved["data"]["hook_result"] == {}
    assert env_unset_release_approved["data"]["hook_result"] == {}
    assert env_split_release_approved["data"]["hook_result"] == {}
    assert env_long_split_release_approved["data"]["hook_result"] == {}
    assert export_release_approved["data"]["hook_result"] == {}
    assert parent_env_release_approved["data"]["hook_result"] == {}
    assert dev_branch_push["data"]["hook_result"] == {}
    for result in equivalent_release_results:
        result_data = parse_json_result(result)
        assert result.returncode != 0
        assert result_data["data"]["hook_result"]["event"] == "agent.release_command_rejected"
    comparison_command = "python3 -c \"print(1 >= 0); print(2 > 1)\""
    comparison = json_result(
        "agent",
        "hook",
        "PreToolUse",
        "--event-json",
        json.dumps({"tool_name": "exec_command", "command": comparison_command}),
        "--json",
        env=env,
    )
    real_redirect = run_qwendex(
        "agent",
        "hook",
        "PreToolUse",
        "--event-json",
        json.dumps({"tool_name": "exec_command", "command": "echo ok > out.txt"}),
        "--json",
        env=env,
    )
    fd_redirect = run_qwendex(
        "agent",
        "hook",
        "PreToolUse",
        "--event-json",
        json.dumps({"tool_name": "exec_command", "command": "echo err 2> err.log"}),
        "--json",
        env=env,
    )
    real_redirect_data = parse_json_result(real_redirect)
    fd_redirect_data = parse_json_result(fd_redirect)
    assert comparison["data"]["hook_result"] == {}
    assert real_redirect.returncode != 0
    assert real_redirect_data["data"]["hook_result"]["event"] == "agent.write_lock_rejected"
    assert fd_redirect.returncode != 0
    assert fd_redirect_data["data"]["hook_result"]["event"] == "agent.write_lock_rejected"


def test_qwendex_agent_file_locks_enforce_single_writer_and_release_on_final_report(tmp_path):
    env = {"QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite")}

    json_result(
        "manager",
        "assign",
        "--agent-id",
        "writer-a",
        "--lane",
        "implementation",
        "--write-surface",
        "scripts/qwendex_cli.py",
        "--json",
        env=env,
    )
    json_result(
        "manager",
        "assign",
        "--agent-id",
        "writer-b",
        "--lane",
        "implementation",
        "--write-surface",
        "tests/smoke/test_qwendex_cli.py",
        "--json",
        env=env,
    )
    acquired = json_result(
        "agent",
        "hook",
        "PreToolUse",
        "--event-json",
        json.dumps({
            "tool_name": "apply_patch",
            "agent_id": "writer-a",
            "profile": "implementer",
            "path": "scripts/qwendex_cli.py",
        }),
        "--json",
        env=env,
    )
    locks = json_result("agent", "locks", "--json", env=env)
    conflict_result = run_qwendex(
        "agent",
        "hook",
        "PreToolUse",
        "--event-json",
        json.dumps({
            "tool_name": "apply_patch",
            "agent_id": "writer-b",
            "profile": "implementer",
            "path": "tests/smoke/test_qwendex_cli.py",
        }),
        "--json",
        env=env,
    )
    completed = json_result(
        "agent",
        "hook",
        "SubagentStop",
        "--event-json",
        json.dumps({
            "agent_id": "writer-a",
            "last_assistant_message": "FINAL_REPORT\nstatus: completed\nagent_id: writer-a\nevidence:\n- done",
        }),
        "--json",
        env=env,
    )
    acquired_after_release = json_result(
        "agent",
        "hook",
        "PreToolUse",
        "--event-json",
        json.dumps({
            "tool_name": "apply_patch",
            "agent_id": "writer-b",
            "profile": "implementer",
            "path": "tests/smoke/test_qwendex_cli.py",
        }),
        "--json",
        env=env,
    )
    status = json_result("agent", "status", "--json", env=env)
    scribe_reject_result = run_qwendex(
        "agent",
        "hook",
        "PreToolUse",
        "--event-json",
        json.dumps({
            "tool_name": "write",
            "agent_id": "scribe-1",
            "profile": "scribe",
            "path": "README.md",
        }),
        "--json",
        env=env,
    )

    conflict = parse_json_result(conflict_result)
    scribe_reject = parse_json_result(scribe_reject_result)

    assert acquired["data"]["hook_result"]["event"] == "agent.file_locks_acquired"
    assert acquired["data"]["hook_result"]["acquired"][0]["path"] == "scripts/qwendex_cli.py"
    assert locks["data"]["write_safety"]["active_writer_count"] == 1
    assert conflict_result.returncode != 0
    assert conflict["data"]["hook_result"]["event"] == "agent.file_lock_conflict"
    assert conflict["data"]["hook_result"]["conflicts"][0]["agent_id"] == "writer-a"
    assert completed["data"]["agent_session"]["status"] == "completed"
    assert acquired_after_release["data"]["hook_result"]["event"] == "agent.file_locks_acquired"
    assert acquired_after_release["data"]["hook_result"]["acquired"][0]["agent_id"] == "writer-b"
    assert status["data"]["write_safety"]["active_writer_count"] == 1
    assert status["data"]["write_safety"]["active_writers"][0]["agent_id"] == "writer-b"
    assert scribe_reject_result.returncode != 0
    assert scribe_reject["data"]["hook_result"]["event"] == "agent.write_rejected"
    assert "Scribe can write only" in scribe_reject["data"]["hook_result"]["reason"]


def test_qwendex_manager_assign_generates_context_packet_and_routing(tmp_path):
    env = {"QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite")}

    assigned = json_result(
        "manager",
        "assign",
        "--agent-id",
        "agent-sec",
        "--lane",
        "security-review",
        "--task-id",
        "task-sec",
        "--objective",
        "Review manager routing risk",
        "--task-class",
        "security",
        "--file",
        "scripts/qwendex_cli.py",
        "--needed-doc",
        "public/qwendex/security.md",
        "--expected-artifact",
        "compact findings",
        "--receipt-path",
        "results/qwendex/security-review.json",
        "--context-budget",
        "12000",
        "--risk",
        "high",
        "--review-requirement",
        "main session validates receipt",
        "--json",
        env=env,
    )
    session = assigned["data"]["agent_session"]
    packet = session["context_packet"]
    routing = packet["model_reasoning_assignment"]

    assert packet["objective"] == "Review manager routing risk"
    assert packet["task_class"] == "security"
    assert packet["exact_files"] == ["scripts/qwendex_cli.py"]
    assert packet["needed_docs"] == ["public/qwendex/security.md"]
    assert packet["receipt_path"] == "results/qwendex/security-review.json"
    assert packet["context_budget"] == 12000
    assert routing["selected_model"] == "gpt-5.5"
    assert routing["selected_reasoning"] in {"high", "xhigh"}
    assert routing["reasoning_source"] == "lane_escalation"
    assert routing["local_qwen_eligible"] is False
    assert routing["token_saver_used"] is False
    assert routing["escalation_reason"]

    status = json_result("manager", "status", "--json", env=env)
    assert status["data"]["active_subagents"]["count"] == 1
    assert status["data"]["deployment_contract"]["status"] == "ready"
    assert status["data"]["subagent_state"]["receipts"] == ["results/qwendex/security-review.json"]
    assert status["data"]["subagent_state"]["validation_status"]["pending"] == 1


def test_qwendex_manager_reconciles_stale_read_only_and_blocks_stale_writers(tmp_path):
    state_db = tmp_path / "qwendex.sqlite"
    env = {
        "QWENDEX_STATE_DB": str(state_db),
        "QWENDEX_MANAGER_DEPLOY_POLICY": "disabled",
    }

    json_result(
        "manager",
        "assign",
        "--agent-id",
        "stale-reader",
        "--lane",
        "review",
        "--write-surface",
        "read-only",
        "--json",
        env=env,
    )
    json_result(
        "manager",
        "assign",
        "--agent-id",
        "stale-writer",
        "--lane",
        "implementation",
        "--write-surface",
        "scripts",
        "--json",
        env=env,
    )
    with sqlite3.connect(state_db) as conn:
        conn.execute("UPDATE qwendex_agent_sessions SET heartbeat_at = '2000-01-01T00:00:00Z'")

    status_result = run_qwendex("manager", "status", "--stale-after-minutes", "5", "--json", env=env)
    status = parse_json_result(status_result)
    assert status_result.returncode == 0
    assert status["data"]["stale_reconciliation"]["closed_count"] == 1
    assert status["data"]["stale_reconciliation"]["closed"][0]["agent_id"] == "stale-reader"
    assert status["data"]["stale_reconciliation"]["skipped_writer_count"] == 1
    assert status["data"]["active_subagents"]["count"] == 0
    assert status["data"]["stale_writer_sessions"]["count"] == 1
    assert "stale writer lane" in " ".join(status["data"]["subagent_state"]["blockers"])

    closed = json_result("manager", "close", "--agent-id", "stale-writer", "--reason", "integrated", "--json", env=env)
    closed_session = closed["data"]["agent_session"]
    assert closed_session["status"] == "closed"
    assert closed_session["stop_reason"] == "integrated"
    assert closed_session["close_receipt"]

    cleared = json_result("manager", "status", "--stale-after-minutes", "5", "--json", env=env)
    assert cleared["data"]["stale_writer_sessions"]["count"] == 0


def test_qwendex_manager_repair_safe_closes_only_harmless_stale_sessions(tmp_path):
    state_db = tmp_path / "qwendex.sqlite"
    env = {
        "QWENDEX_STATE_DB": str(state_db),
        "QWENDEX_MANAGER_DEPLOY_POLICY": "disabled",
    }

    json_result(
        "manager",
        "assign",
        "--agent-id",
        "stale-reader",
        "--lane",
        "review",
        "--write-surface",
        "read-only",
        "--json",
        env=env,
    )
    json_result(
        "manager",
        "assign",
        "--agent-id",
        "empty-writer",
        "--lane",
        "empty-writer",
        "--write-surface",
        "tests/smoke/test_qwendex_cli.py",
        "--json",
        env=env,
    )
    json_result(
        "manager",
        "assign",
        "--agent-id",
        "nonempty-writer",
        "--lane",
        "implementation",
        "--write-surface",
        "tests/smoke/test_qwendex_cli.py",
        "--file",
        "tests/smoke/test_qwendex_cli.py",
        "--artifact",
        "results/qwendex/nonempty.json",
        "--json",
        env=env,
    )
    with sqlite3.connect(state_db) as conn:
        conn.execute("UPDATE qwendex_agent_sessions SET heartbeat_at = '2000-01-01T00:00:00Z'")

    repair_result = run_qwendex("manager", "repair", "--safe", "--stale-after-minutes", "5", "--json", env=env)
    repair = parse_json_result(repair_result)
    status = run_qwendex("manager", "status", "--stale-after-minutes", "5", "--json", env=env)
    status_data = parse_json_result(status)

    assert repair_result.returncode != 0
    assert repair["status"] == "blocked"
    assert repair["data"]["safe"] is True
    assert repair["data"]["closed_count"] == 2
    assert {session["agent_id"] for session in repair["data"]["closed"]} == {"stale-reader", "empty-writer"}
    assert repair["data"]["skipped_writer_count"] == 1
    assert repair["data"]["skipped_writers"][0]["agent_id"] == "nonempty-writer"
    assert "nonempty-writer" in " ".join(repair["errors"])
    assert status.returncode == 0
    assert status_data["status"] == "warning"
    assert status_data["data"]["stale_writer_sessions"]["count"] == 1
    assert status_data["data"]["stale_writer_sessions"]["agents"][0]["agent_id"] == "nonempty-writer"


def test_qwendex_auto_manager_estimator_skill_contract():
    path = ROOT / ".codex" / "skills" / "qwendex-auto-manager-estimator" / "SKILL.md"
    text = path.read_text(encoding="utf-8")

    assert "qwendex-auto-manager-estimator" in text
    assert "GPT-5.5" in text
    assert "medium" in text
    assert len(text.splitlines()) <= 90
    for field in (
        "task_complexity",
        "risk",
        "likely_file_scope",
        "validation_depth",
        "subagent_usefulness",
        "recommended_mode",
        "confidence",
        "higher_reasoning_lanes",
    ):
        assert field in text


def test_qwendex_manager_rejects_out_of_range_cli_overrides():
    result = run_qwendex("manager", "--max-subagents", "999", "--stale-after-minutes", "-1", "--json")
    data = parse_json_result(result)

    assert result.returncode != 0
    assert data["status"] == "blocked"
    assert "max_subagents" in " ".join(data["errors"])
    assert "stale_after_minutes" in " ".join(data["errors"])


def test_qwendex_state_plane_tracks_task_context_handoff_evidence_and_agent_session(tmp_path):
    state_env = {"QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite")}

    task = json_result(
        "task",
        "create",
        "--title",
        "Ship routing",
        "--priority",
        "P1",
        "--owner",
        "main",
        "--phase",
        "build",
        "--json",
        env=state_env,
    )
    task_id = task["data"]["task"]["task_id"]
    assert task["data"]["task"]["status"] == "open"

    started = json_result("task", "start", "--task-id", task_id, "--json", env=state_env)
    assert started["data"]["task"]["status"] == "in_progress"

    assigned = json_result(
        "manager",
        "assign",
        "--agent-id",
        "agent-1",
        "--lane",
        "review",
        "--task-id",
        task_id,
        "--owner",
        "Rawls",
        "--write-surface",
        "read-only",
        "--stop-condition",
        "return findings",
        "--json",
        env=state_env,
    )
    assert assigned["data"]["agent_session"]["status"] == "active"

    heartbeat = json_result("manager", "heartbeat", "--agent-id", "agent-1", "--json", env=state_env)
    assert heartbeat["data"]["agent_session"]["agent_id"] == "agent-1"

    snapshot = json_result(
        "context",
        "snapshot",
        "--task-id",
        task_id,
        "--objective",
        "finish Qwendex",
        "--decision",
        "prefer qwen when available",
        "--open-file",
        "scripts/qwendex_cli.py",
        "--evidence",
        "results/qwendex/example.json",
        "--next-action",
        "run tests",
        "--json",
        env=state_env,
    )
    assert snapshot["data"]["snapshot"]["task_id"] == task_id

    report = json_result(
        "agent",
        "hook",
        "SubagentStop",
        "--event-json",
        json.dumps({
            "agent_id": "agent-1",
            "last_assistant_message": (
                "FINAL_REPORT\n"
                "status: completed\n"
                "agent_id: agent-1\n"
                "task_name: review state plane\n"
                "summary: verified context pack keeps compact agent output\n"
                "evidence:\n"
                "- context pack smoke passed"
            ),
        }),
        "--json",
        env=state_env,
    )
    assert report["data"]["agent_session"]["status"] == "completed"

    compact = json_result("context", "compact-plan", "--task-id", task_id, "--budget", "12000", "--json", env=state_env)
    assert "summary" in compact["data"]["compact_plan"]
    outcome = compact["data"]["compact_plan"]["agent_outcomes"][0]
    assert outcome["agent_id"] == "agent-1"
    assert outcome["raw_output_artifact"].endswith("/raw-output.md")
    assert compact["data"]["compact_plan"]["raw_output_policy"].startswith("preserve raw child output")

    reminder = json_result(
        "context",
        "reminder",
        "--task-id",
        task_id,
        "--tool-calls",
        "55",
        "--phase",
        "after-milestone",
        "--json",
        env=state_env,
    )
    assert reminder["data"]["reminder"]["recommendation"] == "compact_now"
    assert "compact-plan" in reminder["data"]["reminder"]["next_command"]

    handoff = json_result("handoff", "create", "--task-id", task_id, "--status", "ready", "--next-action", "review", "--json", env=state_env)
    handoff_id = handoff["data"]["handoff"]["handoff_id"]
    shown = json_result("handoff", "show", "--handoff-id", handoff_id, "--json", env=state_env)
    assert shown["data"]["handoff"]["task_id"] == task_id

    pack = json_result("context", "pack", "--task-id", task_id, "--json", env=state_env)
    assert pack["data"]["agent_outcomes"][0]["compact_report_artifact"].endswith("/compact-report.json")

    evidence_path = tmp_path / "evidence.txt"
    evidence_path.write_text("evidence", encoding="utf-8")
    evidence = json_result(
        "evidence",
        "add",
        "--task-id",
        task_id,
        "--claim",
        "routing verified",
        "--path",
        str(evidence_path),
        "--json",
        env=state_env,
    )
    assert evidence["data"]["evidence"]["sha256"]
    queried = json_result("evidence", "query", "--task-id", task_id, "--json", env=state_env)
    assert queried["data"]["evidence"][0]["claim"] == "routing verified"

    closed = json_result("manager", "close-stale", "--stale-after-minutes", "5", "--json", env=state_env)
    assert closed["data"]["closed_count"] == 0


def test_qwendex_queue_facade_delegates_to_artifact_queue(tmp_path):
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir()
    env = {"ARTIFACT_QUEUE_MCP_TRUSTED_ROOTS": str(queue_dir)}

    init = json_result("queue", "init", "--dir", str(queue_dir), "--item", "one.md::First", "--item", "two.md::Second", "--json", env=env)
    start = json_result("queue", "start", "--dir", str(queue_dir), "--file", "one.md", "--json", env=env)
    (queue_dir / "one.md").write_text("done\n", encoding="utf-8")
    done = json_result("queue", "done", "--dir", str(queue_dir), "--file", "one.md", "--json", env=env)
    next_item = json_result("queue", "next", "--dir", str(queue_dir), "--json", env=env)

    assert init["data"]["queue"]["counts"]["pending"] == 2
    assert start["data"]["queue"]["started"]["file"] == "one.md"
    assert done["data"]["queue"]["completed"]["file"] == "one.md"
    assert next_item["data"]["queue"]["status"] == "next"
    assert next_item["data"]["queue"]["next"]["file"] == "two.md"


def test_qwendex_public_docs_and_naming_audit_pass():
    qwendex = load_qwendex()

    audit = qwendex.public_docs_audit(ROOT / "public" / "qwendex")

    assert audit["status"] == "pass"
    assert audit["missing"] == []
    assert audit["dead_links"] == []
    assert audit["secret_hits"] == []
    assert audit["naming_hits"] == []
    assert "security.md" in audit["files"]
    assert "staging-receipt.md" in audit["files"]
