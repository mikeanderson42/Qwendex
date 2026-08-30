import json
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
QDEX = ROOT / "scripts" / "qdex"


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o700)


def _fixture(tmp_path: Path) -> dict[str, str]:
    dev_root = tmp_path / "qwendex-dev"
    env_root = dev_root / ".qwendex-dev"
    env_root.mkdir(parents=True, exist_ok=True)
    (dev_root / "scripts").mkdir(parents=True, exist_ok=True)
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir(exist_ok=True)
    meta_root = tmp_path / "meta"
    meta_root.mkdir(exist_ok=True)
    runtime_record = tmp_path / "runtime.json"
    fake_qwendex = dev_root / "scripts" / "qwendex"
    _write_executable(
        fake_qwendex,
        """#!/usr/bin/env python3
import json
import os
import sys
native_threads = int(os.environ.get("FIXTURE_NATIVE_MAX_THREADS", "1"))
worker_threads = max(0, native_threads - 1)
payload = {
    "status": "pass",
    "summary": "fixture",
    "version": "fixture",
    "artifacts": [],
    "next_actions": [],
    "errors": [],
    "data": {
        "agent_use": "Off",
        "agent_policy": {
            "mode": "off",
            "max_threads": worker_threads,
            "native_max_concurrent_threads": native_threads,
            "wait_timeout_ms": 0,
            "policy_hash": "p" * 64,
        },
        "manager_preflight_required": False,
        "agent_policy_hash": "p" * 64,
        "local_enabled": False,
    },
}
print(json.dumps(payload))
""",
    )
    fake_runtime = tmp_path / "fake-runtime"
    _write_executable(
        fake_runtime,
        f"""#!/usr/bin/env python3
import json
from pathlib import Path
Path({str(runtime_record)!r}).write_text(json.dumps({{"args": __import__('sys').argv[1:]}}), encoding='utf-8')
""",
    )
    (env_root / "env.sh").write_text(
        "\n".join(
            [
                f"export QWENDEX_CODEX_HOME={codex_home}",
                f"export QWENDEX_CODEX_RUNTIME={fake_runtime}",
                f"export QWENDEX_QWENDEX_COMMAND={fake_qwendex}",
                f"export QWENDEX_META_ROOT={meta_root}",
                "export QWENDEX_RUNTIME_GENERATION_REQUIRED=0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("QWENDEX_") and key not in {"CODEX_AGENT_USE", "CODEX_HOME"}
    }
    environment.update(
        {
            "QWENDEX_DEV_ROOT": str(dev_root),
            "QWENDEX_QDEX_PERMISSION_MODE": "read-only",
            "QWENDEX_RUNTIME_GENERATION_REQUIRED": "0",
        }
    )
    return {"env": environment, "record": str(runtime_record)}


def _run(tmp_path: Path, *args: str, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    fixture = _fixture(tmp_path)
    env = dict(fixture["env"])
    env.update(extra_env or {})
    return subprocess.run(
        [str(QDEX), *args],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )


def test_invalid_shared_cap_fails_closed(tmp_path):
    result = _run(tmp_path, "--qdex-permission-mode", "workspace-write", extra_env={"QWENDEX_GLOBAL_WORKER_CAP": "not-an-int"})
    assert result.returncode == 2
    assert "invalid QWENDEX_GLOBAL_WORKER_CAP" in result.stderr


def test_shared_cap_without_strict_reservation_fails_closed(tmp_path):
    result = _run(
        tmp_path,
        "--qdex-permission-mode",
        "workspace-write",
        extra_env={"QWENDEX_GLOBAL_WORKER_CAP": "4"},
    )
    assert result.returncode == 2
    assert "requires strict native reservation mode" in result.stderr


def test_shared_cap_with_advisory_reservation_fails_closed(tmp_path):
    result = _run(
        tmp_path,
        "--qdex-permission-mode",
        "workspace-write",
        extra_env={
            "QWENDEX_GLOBAL_WORKER_CAP": "4",
            "QWENDEX_NATIVE_RESERVATION_MODE": "advisory",
        },
    )
    assert result.returncode == 2
    assert "requires strict native reservation mode" in result.stderr


def test_shared_cap_with_strict_reservation_is_admitted(tmp_path):
    fixture = _fixture(tmp_path)
    result = subprocess.run(
        [str(QDEX), "--sandbox", "read-only"],
        cwd=ROOT,
        env={
            **fixture["env"],
            "QWENDEX_GLOBAL_WORKER_CAP": "4",
            "QWENDEX_NATIVE_RESERVATION_MODE": "strict",
        },
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_strict_external_cap_clamps_advisory_native_status(tmp_path):
    fixture = _fixture(tmp_path)
    result = subprocess.run(
        [str(QDEX), "--sandbox", "read-only"],
        cwd=ROOT,
        env={
            **fixture["env"],
            "QWENDEX_GLOBAL_WORKER_CAP": "4",
            "QWENDEX_NATIVE_RESERVATION_MODE": "strict",
            "FIXTURE_NATIVE_MAX_THREADS": "8",
        },
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    record = json.loads(Path(fixture["record"]).read_text(encoding="utf-8"))
    config = " ".join(record["args"])
    assert "features.multi_agent_v2.max_concurrent_threads_per_session=5" in config


def test_read_only_rejects_conflicting_sandbox_and_yolo(tmp_path):
    conflicting = _run(tmp_path, "--sandbox", "workspace-write")
    assert conflicting.returncode == 2
    assert "read-only permission mode rejects sandbox=workspace-write" in conflicting.stderr

    short_conflicting = _run(tmp_path, "-s", "workspace-write")
    assert short_conflicting.returncode == 2
    assert "read-only permission mode rejects sandbox=workspace-write" in short_conflicting.stderr

    compact_conflicting = _run(tmp_path, "-sworkspace-write")
    assert compact_conflicting.returncode == 2
    assert "read-only permission mode rejects sandbox=workspace-write" in compact_conflicting.stderr

    compact_equal_conflicting = _run(tmp_path, "-s=workspace-write")
    assert compact_equal_conflicting.returncode == 2
    assert "read-only permission mode rejects sandbox=workspace-write" in compact_equal_conflicting.stderr

    bypass = _run(tmp_path, "--dangerously-bypass-approvals-and-sandbox")
    assert bypass.returncode == 2
    assert "read-only permission mode rejects the yolo bypass flag" in bypass.stderr


def test_read_only_preserves_private_wrapper_posture(tmp_path):
    fixture = _fixture(tmp_path)
    result = subprocess.run(
        [str(QDEX), "--sandbox", "read-only"],
        cwd=ROOT,
        env=fixture["env"],
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    record = json.loads(Path(fixture["record"]).read_text(encoding="utf-8"))
    assert record["args"].count("--sandbox") == 2
    assert record["args"].count("read-only") == 2
