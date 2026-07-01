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
    assert parser.parse_args(["manager", "repair", "--safe"]).action == "repair"
    assert parser.parse_args(["manager", "repair", "--safe"]).safe is True
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
    env = {"QWENDEX_RESULTS_ROOT": str(tmp_path), "QWENDEX_FORCE_LOCAL_QWEN_AVAILABLE": "1"}

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
    assert "spawn bounded subagents early" in text
    assert "disjoint write scopes" in text
    assert "treat subagent output as advisory" in text
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
    print("codex-cli 0.142.4")
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
    assert plain.stdout.strip() == "{Qwendex} Agent Manager: [Manager Mode] | Kaveman: [N] | Local: [Ready] (Alt+M/K/L)"
    written = json.loads(status_file.read_text(encoding="utf-8"))
    assert written["text"] == "{Qwendex} Agent Manager: [Manager Mode] | Kaveman: [N] | Local: [Ready] (Alt+M/K/L)"
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
    fake_codex.write_text("#!/usr/bin/env bash\nprintf 'codex-cli 0.142.4\\n'\n", encoding="utf-8")
    fake_codex.chmod(0o755)

    data = json_result("codex-patch", "preflight", "--codex-bin", str(fake_codex), "--json")

    assert data["status"] == "pass"
    assert data["data"]["version"]["version"] == "0.142.4"
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
        for spec in qwendex.CODEX_PATCH_MANIFESTS["0.142.4"]["source_anchors"]
    }
    for spec in qwendex.codex_source_patch_specs("0.142.4"):
        rel = str(spec["path"])
        path = source / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        old_fragments = "\n".join(old for old, _new in spec["replacements"])
        path.write_text(f"{anchors_by_path.get(rel, '')}\n{old_fragments}\n", encoding="utf-8")

    fake_codex = tmp_path / "codex-bin"
    fake_codex.write_text("#!/usr/bin/env bash\nprintf 'codex-cli 0.142.4\\n'\n", encoding="utf-8")
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
    env = {"QWENDEX_RESULTS_ROOT": str(tmp_path), "QWENDEX_FORCE_LOCAL_QWEN_AVAILABLE": "1"}

    route = json_result("route", "--task-class", "exec", "--json", env=env)
    exec_data = json_result("exec", "Reply exactly QWENDEX_OK", "--seat", "auto", "--json", env=env)
    receipt = json.loads(Path(exec_data["artifacts"][0]).read_text(encoding="utf-8"))

    assert route["status"] == "pass"
    assert route["data"]["seat"] == "qwen"
    assert route["data"]["requested_seat"] == "auto"
    assert route["data"]["local_qwen"]["available"] is True
    assert route["data"]["routing"]["prefer_local_qwen_when_available"] is True
    assert exec_data["data"]["seat"] == "qwen"
    assert exec_data["data"]["routing"]["seat"] == "qwen"
    assert receipt["routing"]["seat"] == "qwen"
    assert receipt["model"] == "qwen-local"


def test_qwendex_auto_route_falls_back_to_primary_when_local_qwen_is_unavailable(tmp_path):
    env = {"QWENDEX_RESULTS_ROOT": str(tmp_path), "QWENDEX_FORCE_LOCAL_QWEN_UNAVAILABLE": "1"}

    route = json_result("route", "--task-class", "exec", "--json", env=env)
    exec_data = json_result("exec", "Reply exactly QWENDEX_OK", "--seat", "auto", "--json", env=env)
    receipt = json.loads(Path(exec_data["artifacts"][0]).read_text(encoding="utf-8"))

    assert route["status"] == "pass"
    assert route["data"]["seat"] == "primary"
    assert route["data"]["local_qwen"]["available"] is False
    assert route["data"]["local_qwen_eligible"] is True
    assert route["data"]["local_subagents"]["enabled"] is True
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
    env = {"QWENDEX_RESULTS_ROOT": str(tmp_path), "QWENDEX_FORCE_LOCAL_QWEN_AVAILABLE": "1"}
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
    env = {"QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite")}

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
    assert status["data"]["deployment_contract"]["status"] == "ready"


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

    compact = json_result("context", "compact-plan", "--task-id", task_id, "--budget", "12000", "--json", env=state_env)
    assert "summary" in compact["data"]["compact_plan"]

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
