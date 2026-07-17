import json
import os
import shlex
import subprocess
import textwrap
import tomllib
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
QDEX = ROOT / "scripts" / "qdex"
QWENDEX_DEV_ENV = ROOT / "scripts" / "qwendex_dev_env"


def write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def qdex_fixture(
    tmp_path: Path,
    *,
    agent_use: str,
    policy: dict[str, object],
) -> tuple[Path, dict[str, str], Path, Path]:
    dev_root = tmp_path / "dev"
    work_root = dev_root / ".qwendex-dev"
    scripts = dev_root / "scripts"
    meta_root = work_root / "results" / "meta"
    codex_home = work_root / "codex_home"
    runtime = work_root / "bin" / "fake-codex"
    repo = tmp_path / "repo"
    capture = tmp_path / "runtime-argv.json"
    status_calls = tmp_path / "status-calls.txt"
    for path in (scripts, meta_root, codex_home, runtime.parent, repo):
        path.mkdir(parents=True, exist_ok=True)

    write_executable(
        runtime,
        textwrap.dedent(
            """\
            #!/usr/bin/env python3
            import json
            import os
            import sys
            from pathlib import Path

            Path(os.environ["QDEX_RUNTIME_CAPTURE"]).write_text(
                json.dumps(sys.argv[1:]),
                encoding="utf-8",
            )
            print("fake codex")
            """
        ),
    )
    write_executable(
        scripts / "qwendex",
        textwrap.dedent(
            """\
            #!/usr/bin/env python3
            import json
            import os
            import sys
            from pathlib import Path

            args = sys.argv[1:]
            calls = Path(os.environ["QDEX_STATUS_CALLS"])
            calls.write_text(
                calls.read_text(encoding="utf-8") + " ".join(args) + "\\n"
                if calls.exists()
                else " ".join(args) + "\\n",
                encoding="utf-8",
            )
            if args[:1] == ["codex-status"]:
                if os.environ.get("TEST_CODEX_STATUS_FAIL") == "1":
                    print("status unavailable", file=sys.stderr)
                    raise SystemExit(3)
                target = Path(args[args.index("--write") + 1])
                agent_use = os.environ["TEST_AGENT_USE"]
                policy = json.loads(os.environ["TEST_AGENT_POLICY"])
                payload = {
                    "status": "pass",
                    "data": {
                        "agent_use": agent_use,
                        "agent_policy": policy,
                        "manager_preflight_required": policy.get("mode") == "manager",
                        "agent_policy_hash": policy.get("policy_hash", ""),
                    },
                }
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(json.dumps(payload), encoding="utf-8")
                print(json.dumps(payload))
                raise SystemExit(0)
            if args[:2] == ["manager", "preflight"]:
                print(json.dumps({
                    "status": "pass",
                    "data": {
                        "ok": True,
                        "ledger_id": "ledger-1",
                        "root_agent_id": "root-1",
                        "routing_decision": {"selected_route": "manager"},
                        "hook_status": {"verified": True},
                        "exports": {
                            "QWENDEX_MANAGER_ROOT_AGENT_ID": "root-1",
                            "QWENDEX_MANAGER_POLICY_HASH": os.environ.get(
                                "TEST_PREFLIGHT_POLICY_HASH",
                                str(json.loads(os.environ["TEST_AGENT_POLICY"]).get("policy_hash", "")),
                            ),
                        },
                    },
                }))
                raise SystemExit(0)
            raise SystemExit(2)
            """
        ),
    )

    exports = {
        "QWENDEX_DEV_ROOT": str(dev_root),
        "QWENDEX_CODEX_HOME": str(codex_home),
        "QWENDEX_CODEX_RUNTIME": str(runtime),
        "QWENDEX_CODEX_STATUS_FILE": str(work_root / "codex_status.json"),
        "QWENDEX_META_ROOT": str(meta_root),
    }
    (work_root / "env.sh").write_text(
        "".join(f"export {key}={shlex.quote(value)}\n" for key, value in exports.items()),
        encoding="utf-8",
    )
    env = {
        **os.environ,
        **exports,
        "QWENDEX_QDEX_DRY_RUN": "1",
        "QDEX_RUNTIME_CAPTURE": str(capture),
        "QDEX_STATUS_CALLS": str(status_calls),
        "TEST_AGENT_USE": agent_use,
        "TEST_AGENT_POLICY": json.dumps(policy),
    }
    return repo, env, capture, status_calls


def qdex_dry_run(repo: Path, env: dict[str, str], *args: str) -> dict[str, object]:
    result = subprocess.run(
        [str(QDEX), "--qdex-json", "-C", str(repo), *args],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout)


def command_config(command: list[str]) -> dict[str, object]:
    overrides: dict[str, object] = {}
    for index, item in enumerate(command[:-1]):
        if item != "--config":
            continue
        key, raw_value = command[index + 1].split("=", 1)
        overrides[key] = tomllib.loads(f"value = {raw_value}")["value"]
    return overrides


@pytest.mark.parametrize(
    (
        "agent_use",
        "policy",
        "expected_native_threads",
        "expected_wait_timeout_ms",
        "active_guidance",
    ),
    [
        ("Auto", {"mode": "auto", "max_threads": 4, "wait_timeout_ms": 120000}, 5, 120000, True),
        (
            "Manager",
            {
                "mode": "manager",
                "max_threads": 6,
                "native_max_concurrent_threads": 7,
                "wait_timeout_ms": 60000,
            },
            7,
            60000,
            True,
        ),
        ("Heavy", {"mode": "heavy", "max_threads": 3, "wait_timeout_ms": 90000}, 4, 90000, True),
        ("Medium", {"mode": "medium", "max_threads": 2, "wait_timeout_ms": 120000}, 3, 120000, True),
        ("Lite", {"mode": "lite", "max_threads": 1, "wait_timeout_ms": 90000}, 2, 90000, False),
        ("Off", {"mode": "off", "max_threads": 0, "wait_timeout_ms": 0}, 1, 0, False),
    ],
)
def test_qdex_dry_run_wires_agent_policy_into_supported_v2_config(
    tmp_path: Path,
    agent_use: str,
    policy: dict[str, object],
    expected_native_threads: int,
    expected_wait_timeout_ms: int,
    active_guidance: bool,
) -> None:
    repo, env, _, _ = qdex_fixture(tmp_path, agent_use=agent_use, policy=policy)

    payload = qdex_dry_run(repo, env, "--model", "selected-model", "--search")
    command = payload["command"]
    assert isinstance(command, list)
    overrides = command_config(command)

    assert overrides["suppress_unstable_features_warning"] is True
    assert overrides["features.multi_agent_v2.enabled"] is True
    assert (
        overrides["features.multi_agent_v2.max_concurrent_threads_per_session"]
        == expected_native_threads
    )
    assert overrides["features.multi_agent_v2.max_wait_timeout_ms"] == expected_wait_timeout_ms
    assert overrides["features.multi_agent_v2.min_wait_timeout_ms"] == (
        0 if expected_wait_timeout_ms == 0 else 10000
    )
    assert overrides["features.multi_agent_v2.default_wait_timeout_ms"] == (
        0 if expected_wait_timeout_ms == 0 else 30000
    )
    mode_hint = str(overrides["features.multi_agent_v2.multi_agent_mode_hint_text"])
    root_hint = str(overrides["features.multi_agent_v2.root_agent_usage_hint_text"])
    subagent_hint = str(overrides["features.multi_agent_v2.subagent_usage_hint_text"])
    if active_guidance:
        assert f"Qwendex {agent_use} delegation is active" in mode_hint
        assert "independently of reasoning effort" in mode_hint
        assert "explicit-only" not in mode_hint
    else:
        assert "explicit-only delegation" in mode_hint
        assert "proactive" in mode_hint.lower()
    assert "default sole writer" in root_hint
    assert "Do not ask workers to delegate recursively" in root_hint
    assert "do not spawn or manage subagents" in subagent_hint
    assert "structured FINAL_REPORT is optional" in subagent_hint
    if agent_use == "Manager":
        assert "never override the user's instruction" in mode_hint
    if agent_use == "Auto":
        assert "saves root context or user tokens" in mode_hint
    assert not any("gpt-" in hint.lower() for hint in (mode_hint, root_hint, subagent_hint))
    caller_args = ["-C", str(repo), "--model", "selected-model", "--search"]
    caller_start = command.index("-C")
    assert command[caller_start : caller_start + len(caller_args)] == caller_args
    assert caller_start + len(caller_args) == command.index("--config")


def test_qdex_immutable_policy_follows_exec_local_config_and_wins(tmp_path: Path) -> None:
    repo, env, _, _ = qdex_fixture(
        tmp_path,
        agent_use="Manager",
        policy={"mode": "manager", "max_threads": 4, "native_max_concurrent_threads": 5},
    )

    command = qdex_dry_run(
        repo,
        env,
        "exec",
        "-c",
        "features.multi_agent_v2.max_concurrent_threads_per_session=1",
        "-c",
        "features.multi_agent_v2.max_wait_timeout_ms=3600000",
        "-c",
        'model_reasoning_effort="medium"',
        "Inspect the repository",
    )["command"]
    assert isinstance(command, list)
    matching = [
        (index, value)
        for index, value in enumerate(command)
        if value.startswith("features.multi_agent_v2.max_concurrent_threads_per_session=")
    ]
    assert [value for _, value in matching] == [
        "features.multi_agent_v2.max_concurrent_threads_per_session=1",
        "features.multi_agent_v2.max_concurrent_threads_per_session=5",
    ]
    assert matching[0][0] < matching[1][0]
    assert command_config(command)["features.multi_agent_v2.max_concurrent_threads_per_session"] == 5
    wait_matching = [
        (index, value)
        for index, value in enumerate(command)
        if value.startswith("features.multi_agent_v2.max_wait_timeout_ms=")
    ]
    assert [value for _, value in wait_matching] == [
        "features.multi_agent_v2.max_wait_timeout_ms=3600000",
        "features.multi_agent_v2.max_wait_timeout_ms=60000",
    ]
    assert wait_matching[0][0] < wait_matching[1][0]
    assert command_config(command)["features.multi_agent_v2.max_wait_timeout_ms"] == 60000


def test_qdex_caps_policy_wait_at_product_ceiling(tmp_path: Path) -> None:
    repo, env, _, _ = qdex_fixture(
        tmp_path,
        agent_use="Heavy",
        policy={
            "mode": "heavy",
            "max_threads": 3,
            "wait_timeout_ms": 3600000,
        },
    )

    overrides = command_config(qdex_dry_run(repo, env)["command"])

    assert overrides["features.multi_agent_v2.min_wait_timeout_ms"] == 10000
    assert overrides["features.multi_agent_v2.default_wait_timeout_ms"] == 30000
    assert overrides["features.multi_agent_v2.max_wait_timeout_ms"] == 120000


def test_qdex_launches_with_advisory_when_preflight_policy_hash_drifted(tmp_path: Path) -> None:
    repo, env, capture, _ = qdex_fixture(
        tmp_path,
        agent_use="Manager",
        policy={
            "mode": "manager",
            "max_threads": 4,
            "native_max_concurrent_threads": 5,
            "policy_hash": "status-policy",
        },
    )
    env.pop("QWENDEX_QDEX_DRY_RUN")
    env["TEST_PREFLIGHT_POLICY_HASH"] = "different-preflight-policy"

    result = subprocess.run(
        [str(QDEX), "-C", str(repo)],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )

    assert result.returncode == 0
    assert "preflight policy hash does not match" in result.stderr
    assert "continuing without Manager bookkeeping" in result.stderr
    assert capture.exists()


def test_qdex_launches_with_safe_defaults_when_manager_status_is_unavailable(tmp_path: Path) -> None:
    repo, env, capture, _ = qdex_fixture(
        tmp_path,
        agent_use="Manager",
        policy={
            "mode": "manager",
            "max_threads": 4,
            "native_max_concurrent_threads": 5,
            "policy_hash": "status-policy",
        },
    )
    env.pop("QWENDEX_QDEX_DRY_RUN")
    env["TEST_CODEX_STATUS_FAIL"] = "1"

    result = subprocess.run(
        [str(QDEX), "-C", str(repo)],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )

    assert result.returncode == 0
    assert "continuing with safe delegation defaults" in result.stderr
    assert capture.exists()
    command = json.loads(capture.read_text(encoding="utf-8"))
    overrides = command_config(command)
    assert overrides["features.multi_agent_v2.enabled"] is True
    assert overrides["features.multi_agent_v2.max_concurrent_threads_per_session"] == 1


def test_qdex_concurrent_launches_use_isolated_metadata_files(tmp_path: Path) -> None:
    repo, env, _, _ = qdex_fixture(
        tmp_path,
        agent_use="Manager",
        policy={
            "mode": "manager",
            "max_threads": 4,
            "native_max_concurrent_threads": 5,
            "policy_hash": "shared-policy",
        },
    )
    other_repo = tmp_path / "other-repo"
    other_repo.mkdir()
    processes = [
        subprocess.Popen(
            [str(QDEX), "--qdex-json", "-C", str(target)],
            cwd=target,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for target in (repo, other_repo)
    ]

    results = [process.communicate(timeout=30) for process in processes]

    for process, (stdout, stderr) in zip(processes, results, strict=True):
        assert process.returncode == 0, stderr or stdout
        assert json.loads(stdout)["schema_version"] == "qwendex.qdex.dry_run.v1"
    launch_roots = sorted((tmp_path / "dev/.qwendex-dev/results/meta/qdex").iterdir())
    assert len(launch_roots) == 2
    assert all((root / "codex_status_write.json").is_file() for root in launch_roots)
    assert all((root / "manager_preflight.json").is_file() for root in launch_roots)


def test_qdex_preserves_native_ultra_proactive_mode_without_weakening_qwendex_policy(
    tmp_path: Path,
) -> None:
    repo, env, _, _ = qdex_fixture(
        tmp_path,
        agent_use="Manager",
        policy={"mode": "manager", "max_threads": 4, "native_max_concurrent_threads": 5},
    )

    payload = qdex_dry_run(
        repo,
        env,
        "exec",
        "-c",
        'model_reasoning_effort="ultra"',
        "Map the repository with bounded native workers",
    )
    command = payload["command"]
    assert isinstance(command, list)
    overrides = command_config(command)

    # A configured mode hint would replace Codex's effort-derived Proactive
    # mode. Qwendex keeps its root/child policy and thread ceiling, while
    # intentionally leaving this one field absent for Ultra.
    assert "features.multi_agent_v2.multi_agent_mode_hint_text" not in overrides
    assert overrides["features.multi_agent_v2.max_concurrent_threads_per_session"] == 5
    assert "root Qwendex orchestrator" in str(
        overrides["features.multi_agent_v2.root_agent_usage_hint_text"]
    )
    assert "After a wait timeout, inspect list_agents once" in str(
        overrides["features.multi_agent_v2.root_agent_usage_hint_text"]
    )
    assert "consider a bounded follow-up to that verifier" in str(
        overrides["features.multi_agent_v2.root_agent_usage_hint_text"]
    )
    assert "do not spawn or manage subagents" in str(
        overrides["features.multi_agent_v2.subagent_usage_hint_text"]
    )
    assert "respect any explicitly read-only scope" in str(
        overrides["features.multi_agent_v2.subagent_usage_hint_text"]
    )
    assert "structured FINAL_REPORT is optional" in str(
        overrides["features.multi_agent_v2.subagent_usage_hint_text"]
    )


def test_qdex_last_reasoning_override_controls_ultra_coexistence(tmp_path: Path) -> None:
    repo, env, _, _ = qdex_fixture(
        tmp_path,
        agent_use="Heavy",
        policy={"mode": "heavy", "max_threads": 3},
    )

    command = qdex_dry_run(
        repo,
        env,
        "exec",
        "--config=model_reasoning_effort='ultra'",
        "-c",
        'model_reasoning_effort="medium"',
        "Inspect the repository",
    )["command"]
    assert isinstance(command, list)
    assert "features.multi_agent_v2.multi_agent_mode_hint_text" in command_config(command)


def test_qdex_inserts_policy_before_option_barrier(tmp_path: Path) -> None:
    repo, env, _, _ = qdex_fixture(
        tmp_path,
        agent_use="Medium",
        policy={"mode": "medium", "max_threads": 2},
    )

    command = qdex_dry_run(repo, env, "exec", "--", "-literal-prompt")["command"]
    assert isinstance(command, list)
    barrier = command.index("--")
    assert command[barrier:] == ["--", "-literal-prompt"]
    assert command.index("features.multi_agent_v2.max_concurrent_threads_per_session=3") < barrier


def test_qdex_bounds_native_thread_cap_from_status_payload(tmp_path: Path) -> None:
    repo, env, _, _ = qdex_fixture(
        tmp_path,
        agent_use="Manager",
        policy={
            "mode": "manager",
            "max_threads": 100,
            "native_max_concurrent_threads": 101,
        },
    )

    command = qdex_dry_run(repo, env)["command"]
    assert isinstance(command, list)
    overrides = command_config(command)

    assert overrides["features.multi_agent_v2.max_concurrent_threads_per_session"] == 9
    assert "8 workers" in str(overrides["features.multi_agent_v2.multi_agent_mode_hint_text"])


@pytest.mark.parametrize("argv", [["--help"], ["--version"], ["-V"]])
def test_qdex_help_and_version_preserve_exact_runtime_argv(
    tmp_path: Path,
    argv: list[str],
) -> None:
    repo, env, capture, status_calls = qdex_fixture(
        tmp_path,
        agent_use="Medium",
        policy={"mode": "medium", "max_threads": 2},
    )
    env.pop("QWENDEX_QDEX_DRY_RUN")

    result = subprocess.run(
        [str(QDEX), *argv],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    assert json.loads(capture.read_text(encoding="utf-8")) == argv
    assert not status_calls.exists()


def test_generated_codex_config_has_safe_v2_baseline(tmp_path: Path) -> None:
    home = tmp_path / "home"
    dev_root = tmp_path / "generated-dev"
    home.mkdir()
    result = subprocess.run(
        [str(QWENDEX_DEV_ENV), "env"],
        cwd=ROOT,
        env={
            **os.environ,
            "HOME": str(home),
            "QWENDEX_DEV_ROOT": str(dev_root),
            "QWENDEX_DEV_SOURCE_ROOT": str(ROOT),
        },
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr or result.stdout

    config = tomllib.loads(
        (dev_root / ".qwendex-dev" / "codex_home" / "config.toml").read_text(
            encoding="utf-8"
        )
    )
    features = config["features"]
    v2 = features["multi_agent_v2"]
    assert config["suppress_unstable_features_warning"] is True
    assert features["multi_agent"] is True
    assert v2["enabled"] is True
    assert v2["max_concurrent_threads_per_session"] == 1
    assert "explicit-only delegation" in v2["multi_agent_mode_hint_text"]
    assert "root Qwendex orchestrator" in v2["root_agent_usage_hint_text"]
    assert "do not retry wait_agent" in v2["root_agent_usage_hint_text"]
    assert "do not spawn or manage subagents" in v2["subagent_usage_hint_text"]
    assert "structured FINAL_REPORT is optional" in v2["subagent_usage_hint_text"]
    guidance = " ".join(
        v2[key]
        for key in (
            "multi_agent_mode_hint_text",
            "root_agent_usage_hint_text",
            "subagent_usage_hint_text",
        )
    )
    assert "gpt-" not in guidance.lower()
    assert "agents" not in config
