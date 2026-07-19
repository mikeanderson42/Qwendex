from __future__ import annotations

import json
import os
import shutil
import subprocess
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "qwendex_testbench"


def write_executable(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def fake_qwendex_tree(tmp_path: Path) -> tuple[Path, Path]:
    qwendex_root = tmp_path / "Qwendex source space 'single' \"double\""
    script = qwendex_root / "scripts" / "qwendex_testbench"
    script.parent.mkdir(parents=True)
    shutil.copy2(SCRIPT, script)
    write_executable(
        qwendex_root / "scripts" / "qwendex",
        """#!/usr/bin/env python3
import json
import sys

print(json.dumps({
    "command": "fake-qwendex",
    "status": "pass",
    "summary": "fixture command passed",
    "data": {"args": sys.argv[1:]},
}))
""",
    )
    write_executable(
        qwendex_root / "llmstack",
        "#!/usr/bin/env bash\nprintf '%s\\n' 'fake llmstack'\n",
    )
    (qwendex_root / "scripts" / "artifact_queue_mcp.py").write_text(
        "# fixture MCP entry point\n", encoding="utf-8"
    )
    return qwendex_root, script


def bench_env(
    *,
    qwendex_root: Path,
    project: Path,
    bench: Path,
    home: Path,
    fake_bin: Path,
) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "PATH": f"{fake_bin}:{env['PATH']}",
            "QWENDEX_BENCH_PROJECT": str(project),
            "QWENDEX_BENCH_ROOT": str(bench),
        }
    )
    script = qwendex_root / "scripts" / "qwendex_testbench"
    result = subprocess.run(
        [str(script), "env"],
        cwd=project,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["bash", "-n"],
        input=result.stdout,
        text=True,
        capture_output=True,
        check=True,
    )
    probe = subprocess.run(
        ["bash"],
        input=(
            result.stdout
            + "\npython3 - <<'PY'\n"
            + "import json, os\n"
            + "print(json.dumps(dict(os.environ), sort_keys=True))\n"
            + "PY\n"
        ),
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(probe.stdout)


def test_testbench_defaults_to_current_directory_even_when_home_has_thehub(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    project = tmp_path / "selected cwd"
    bench = tmp_path / "bench"
    fake_bin = tmp_path / "bin"
    (home / "thehub").mkdir(parents=True)
    project.mkdir()
    fake_bin.mkdir()
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "PATH": f"{fake_bin}:{env['PATH']}",
            "QWENDEX_BENCH_ROOT": str(bench),
        }
    )
    env.pop("QWENDEX_BENCH_PROJECT", None)

    result = subprocess.run(
        [str(SCRIPT), "env"],
        cwd=project,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    probe = subprocess.run(
        ["bash"],
        input=result.stdout + "\nprintf '%s' \"$QWENDEX_EXEC_CWD\"\n",
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )

    assert probe.stdout == str(project)
    assert "$HOME/thehub" not in SCRIPT.read_text(encoding="utf-8")


def test_testbench_quotes_paths_and_limits_trusted_write_roots(tmp_path: Path) -> None:
    qwendex_root, script = fake_qwendex_tree(tmp_path)
    project = tmp_path / "project space 'single' \"double\""
    bench = tmp_path / "bench space 'single' \"double\""
    home = tmp_path / "home space 'single' \"double\""
    fake_bin = tmp_path / "fake bin 'single' \"double\""
    capture = tmp_path / "codex capture 'single' \"double\".json"
    project.mkdir()
    home.mkdir()
    fake_bin.mkdir()
    write_executable(
        fake_bin / "codex",
        """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

if sys.argv[1:] == ["--version"]:
    print("codex-cli 0.144.6")
    raise SystemExit(0)
Path(os.environ["QWENDEX_TEST_CODEX_CAPTURE"]).write_text(
    json.dumps({
        "args": sys.argv[1:],
        "cwd": os.getcwd(),
        "env": {
            name: os.environ[name]
            for name in (
                "QWENDEX_EXEC_CWD",
                "QWENDEX_MCP_TRUSTED_ROOTS",
                "LOCAL_QWEN_CODEX_CWD",
                "LOCAL_QWEN_CODEX_ADD_DIRS",
                "LOCAL_QWEN_LOCAL_HARNESS_TRUSTED_ROOTS",
            )
        },
    }, sort_keys=True),
    encoding="utf-8",
)
""",
    )

    exported = bench_env(
        qwendex_root=qwendex_root,
        project=project,
        bench=bench,
        home=home,
        fake_bin=fake_bin,
    )
    trusted_roots = f"{bench}:{project}"
    assert exported["QWENDEX_EXEC_CWD"] == str(project)
    assert exported["LOCAL_QWEN_CODEX_CWD"] == str(project)
    assert exported["QWENDEX_MCP_TRUSTED_ROOTS"] == trusted_roots
    assert exported["LOCAL_QWEN_CODEX_ADD_DIRS"] == trusted_roots
    assert exported["LOCAL_QWEN_LOCAL_HARNESS_TRUSTED_ROOTS"] == trusted_roots
    for name in (
        "QWENDEX_MCP_TRUSTED_ROOTS",
        "LOCAL_QWEN_CODEX_ADD_DIRS",
        "LOCAL_QWEN_LOCAL_HARNESS_TRUSTED_ROOTS",
    ):
        assert str(qwendex_root) not in exported[name]

    config = tomllib.loads((bench / "codex_home" / "config.toml").read_text(encoding="utf-8"))
    assert config["projects"] == {
        str(project): {"trust_level": "trusted"},
        str(bench): {"trust_level": "trusted"},
    }
    assert str(qwendex_root) not in config["projects"]

    for wrapper in (bench / "bin").iterdir():
        subprocess.run(["bash", "-n", str(wrapper)], check=True, capture_output=True)
    wrapped = subprocess.run(
        [str(bench / "bin" / "qwendex"), "quoted-path-probe"],
        text=True,
        capture_output=True,
        check=True,
    )
    assert json.loads(wrapped.stdout)["data"]["args"] == ["quoted-path-probe"]
    subprocess.run(
        [str(bench / "bin" / "qwendex-bench"), "env"],
        cwd=project,
        env={
            **os.environ,
            "HOME": str(home),
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "QWENDEX_BENCH_PROJECT": str(project),
            "QWENDEX_BENCH_ROOT": str(bench),
        },
        text=True,
        capture_output=True,
        check=True,
    )

    run_env = {
        **os.environ,
        "HOME": str(home),
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "QWENDEX_BENCH_PROJECT": str(project),
        "QWENDEX_BENCH_ROOT": str(bench),
        "QWENDEX_TEST_CODEX_CAPTURE": str(capture),
    }
    subprocess.run(
        [str(script), "open-full", "quoted path prompt"],
        cwd=tmp_path,
        env=run_env,
        text=True,
        capture_output=True,
        check=True,
    )
    invocation = json.loads(capture.read_text(encoding="utf-8"))
    args = invocation["args"]
    add_dirs = [args[index + 1] for index, arg in enumerate(args) if arg == "--add-dir"]
    overrides = [args[index + 1] for index, arg in enumerate(args) if arg == "-c"]
    for override in overrides:
        tomllib.loads(override)
    assert add_dirs == [str(bench), str(project)]
    assert str(qwendex_root) not in add_dirs
    assert args[args.index("-C") + 1] == str(project)
    assert (
        "mcp_servers.local-harness.cwd=" + json.dumps(str(project), ensure_ascii=False)
        in overrides
    )
    assert (
        "mcp_servers.local-harness.env.ARTIFACT_QUEUE_MCP_TRUSTED_ROOTS="
        + json.dumps(trusted_roots, ensure_ascii=False)
        in overrides
    )
    assert invocation["env"]["QWENDEX_MCP_TRUSTED_ROOTS"] == trusted_roots
    assert invocation["env"]["LOCAL_QWEN_CODEX_ADD_DIRS"] == trusted_roots

    capture.unlink()
    subprocess.run(
        [str(script), "mcp"],
        cwd=tmp_path,
        env=run_env,
        text=True,
        capture_output=True,
        check=True,
    )
    mcp_invocation = json.loads(capture.read_text(encoding="utf-8"))
    assert mcp_invocation["cwd"] == str(project)
    assert mcp_invocation["args"][-2:] == ["mcp", "list"]


def test_testbench_script_and_public_wording_match_isolation_contract() -> None:
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True, capture_output=True)
    script = SCRIPT.read_text(encoding="utf-8")
    docs = (ROOT / "public" / "qwendex" / "testbench.md").read_text(encoding="utf-8")

    assert "selected model path" not in docs
    assert "current working directory" in docs
    assert 'mcp_servers.local-harness.cwd=$(toml_quote "$PROJECT_ROOT")' in script
    assert '--add-dir "$QWENDEX_ROOT"' not in script
    assert '[projects."$QWENDEX_ROOT"]' not in script
