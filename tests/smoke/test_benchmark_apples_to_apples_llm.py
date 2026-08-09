from __future__ import annotations

import importlib.util
import json
import shutil
import socket
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "benchmark_apples_to_apples_llm.py"


def load_benchmark_module():
    spec = importlib.util.spec_from_file_location("benchmark_apples_to_apples_llm", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_model_code_runs_in_isolated_restricted_runner() -> None:
    benchmark = load_benchmark_module()

    result = benchmark.run_python_function_tests(
        "def double(values):\n    return [value * 2 for value in values]",
        "double",
        [(([1, 2, 3],), [2, 4, 6])],
    )

    assert result["passed"] is True
    assert all(case["passed"] for case in result["case_results"])


def test_structured_tool_content_accepts_only_object_arguments() -> None:
    benchmark = load_benchmark_module()

    content = benchmark.structured_tool_content(
        {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "submit_final",
                                    "arguments": '{"answer":39,"work":"verified"}',
                                }
                            }
                        ]
                    }
                }
            ]
        }
    )
    rejected = benchmark.structured_tool_content(
        {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {"function": {"name": "submit_final", "arguments": '["not", "an", "object"]'}}
                        ]
                    }
                }
            ]
        }
    )

    assert json.loads(content) == {"answer": 39, "work": "verified"}
    assert rejected == ""


def test_structured_json_mode_is_limited_to_declared_contract_tasks() -> None:
    benchmark = load_benchmark_module()

    structured_task_names = [task.name for task in benchmark.build_tasks() if task.structured_tool]

    assert structured_task_names == ["gsm8k_arithmetic", "planning_dependencies"]


def test_extract_code_dedents_uniformly_indented_fenced_python() -> None:
    benchmark = load_benchmark_module()
    text = """```python
   import re
   from collections import Counter

   def word_frequencies(text):
       return dict(Counter(re.findall(r'[a-z0-9]+', text.lower())))
```"""

    code = benchmark.extract_code(text)

    assert code.startswith("import re\nfrom collections import Counter\n")
    assert "\ndef word_frequencies(text):\n    return" in code
    result = benchmark.run_python_function_tests(
        code,
        "word_frequencies",
        [(("Red red BLUE",), {"red": 2, "blue": 1})],
    )
    assert result["passed"] is True


def test_merge_interval_hidden_inputs_match_declared_list_contract() -> None:
    benchmark = load_benchmark_module()
    passed, details = benchmark.grade_merge_intervals(
        """```python
def merge_intervals(intervals):
    if not intervals:
        return []
    intervals.sort(key=lambda interval: interval[0])
    merged = [intervals[0]]
    for start, end in intervals[1:]:
        if start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged
```"""
    )

    assert passed is True
    assert len(details["case_results"]) == 4


def test_structured_json_task_uses_declared_schema_without_changing_grader(monkeypatch: pytest.MonkeyPatch) -> None:
    benchmark = load_benchmark_module()
    task = next(task for task in benchmark.build_tasks() if task.name == "gsm8k_arithmetic")
    captured: dict[str, object] = {}

    def fake_post_json(_url: str, payload: dict[str, object], _timeout: int) -> tuple[float, dict[str, object]]:
        captured["payload"] = payload
        return 0.01, {
            "choices": [
                {
                    "message": {
                        "content": "wrong ordinary content",
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "submit_final",
                                    "arguments": '{"answer":39,"work":"(18 * 7 - 9) / 3 = 39"}',
                                }
                            }
                        ],
                    }
                }
            ],
            "usage": {"completion_tokens": 10},
        }

    monkeypatch.setattr(benchmark, "post_json", fake_post_json)
    args = SimpleNamespace(
        model="qwen-local",
        max_token_scale=1.0,
        min_task_max_tokens=0,
        structured_json_tools=True,
        chat_template_enable_thinking="false",
        thinking_budget_tokens=None,
        top_p=0.95,
        top_k=20,
        min_p=0.0,
        chat_base="http://127.0.0.1:4000/v1",
        timeout=10,
        temperature=0.2,
        seed=4242,
    )

    result = benchmark.run_task(args, task)

    request = captured["payload"]
    assert isinstance(request, dict)
    assert request["tools"][0]["function"]["parameters"] == task.response_schema
    assert request["seed"] == 4242
    assert "function submit_final as the only response" in request["messages"][0]["content"]
    assert result["passed"] is True
    assert result["response_transport"] == "tool_call"


def test_model_code_rejects_host_access_before_execution() -> None:
    benchmark = load_benchmark_module()

    result = benchmark.run_python_function_tests(
        "import os\ndef probe(values):\n    return os.listdir('/')",
        "probe",
        [(([],), [])],
    )

    assert result["passed"] is False
    assert "outside the allowlist" in result["error"]


def test_model_code_rejects_private_module_escape_before_execution() -> None:
    benchmark = load_benchmark_module()

    result = benchmark.run_python_function_tests(
        "import collections\ndef probe():\n    return collections._sys.modules['builtins'].open('/etc/passwd').read()",
        "probe",
        [((), "")],
    )

    assert result["passed"] is False
    assert result["error"] == "model code accesses a private attribute"


def test_model_code_allowed_re_export_runs_in_sandbox() -> None:
    benchmark = load_benchmark_module()

    result = benchmark.run_python_function_tests(
        "import re\ndef tokens(value):\n    return re.findall(r'[a-z]+', value.lower())",
        "tokens",
        [(("Red BLUE 42",), ["red", "blue"])],
    )

    assert result["passed"] is True


def test_model_code_preserves_tuple_list_distinction() -> None:
    benchmark = load_benchmark_module()

    result = benchmark.run_python_function_tests(
        "def pair():\n    return (1, 2)",
        "pair",
        [((), [1, 2])],
    )

    assert result["passed"] is False
    assert result["case_results"][0]["passed"] is False


def test_model_code_fails_closed_without_bubblewrap(monkeypatch: pytest.MonkeyPatch) -> None:
    benchmark = load_benchmark_module()
    monkeypatch.setattr(benchmark.shutil, "which", lambda _name: None)

    result = benchmark.run_python_function_tests("def identity(value):\n    return value", "identity", [((1,), 1)])

    assert result["passed"] is False
    assert "sandbox is unavailable" in result["error"]


def test_model_code_rejects_untrusted_bubblewrap_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    benchmark = load_benchmark_module()
    fake_bubblewrap = tmp_path / "bwrap"
    fake_bubblewrap.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_bubblewrap.chmod(0o777)
    monkeypatch.setattr(benchmark.shutil, "which", lambda _name: str(fake_bubblewrap))

    assert benchmark.model_sandbox_command() is None


def test_bubblewrap_command_has_fail_closed_isolation_controls() -> None:
    benchmark = load_benchmark_module()
    command = benchmark.model_sandbox_command()
    assert command is not None

    assert command[0] == "/usr/bin/bwrap"
    for required in (
        "--unshare-all",
        "--unshare-user",
        "--disable-userns",
        "--die-with-parent",
        "--new-session",
        "--clearenv",
        "--ro-bind",
        "--tmpfs",
        "--cap-drop",
    ):
        assert required in command
    assert command[command.index("--ro-bind") + 1 : command.index("--ro-bind") + 3] == ["/usr", "/usr"]
    symlink_pairs = [
        command[index + 1 : index + 3]
        for index, argument in enumerate(command)
        if argument == "--symlink"
    ]
    assert ["usr/lib", "/lib"] in symlink_pairs
    assert ["usr/lib64", "/lib64"] in symlink_pairs
    assert command[-2] == "--"
    assert benchmark.trusted_root_owned_executable(
        command[-1],
        allowed_roots=(Path("/usr"),),
    ) == Path(command[-1])


def test_bubblewrap_command_uses_system_python_when_runner_is_from_toolcache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    benchmark = load_benchmark_module()
    monkeypatch.setattr(
        benchmark.sys,
        "executable",
        "/opt/hostedtoolcache/Python/3.11.15/x64/bin/python3",
    )

    command = benchmark.model_sandbox_command()

    assert command is not None
    assert command[-2:] == ["--", str(Path("/usr/bin/python3").resolve())]


def test_sandbox_python_selection_fails_closed_when_both_candidates_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    benchmark = load_benchmark_module()
    candidates: list[str] = []

    def reject_candidate(value, *, allowed_roots):
        candidates.append(str(value))
        return None

    monkeypatch.setattr(benchmark, "trusted_root_owned_executable", reject_candidate)

    assert benchmark.trusted_model_sandbox_python() is None
    assert candidates == [benchmark.sys.executable, "/usr/bin/python3"]


def test_model_runner_cannot_append_an_unvalidated_python(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    benchmark = load_benchmark_module()
    sandbox_command = ["/usr/bin/bwrap", "--", "/usr/bin/python3"]
    captured: dict[str, object] = {}

    class FakeProcess:
        returncode = 0

        def communicate(self, input_payload, timeout):
            captured["input"] = input_payload
            captured["timeout"] = timeout
            return json.dumps({"passed": True, "case_results": []}), None

    def capture_popen(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(benchmark, "model_sandbox_command", lambda: sandbox_command)
    monkeypatch.setattr(benchmark.subprocess, "Popen", capture_popen)

    result = benchmark.run_python_function_tests(
        "def identity(value):\n    return value",
        "identity",
        [((1,), 1)],
    )

    assert result["passed"] is True
    assert captured["argv"] == [*sandbox_command, "-I", "-c", benchmark.MODEL_CODE_RUNNER]


@pytest.mark.skipif(shutil.which("bwrap") is None, reason="bubblewrap is required for the Linux sandbox boundary")
def test_bubblewrap_boundary_hides_host_state(tmp_path: Path) -> None:
    benchmark = load_benchmark_module()
    command = benchmark.model_sandbox_command()
    assert command is not None
    host_sentinel = tmp_path / "host-state-sentinel"
    host_sentinel.write_text("host state must not be mounted into the sandbox", encoding="utf-8")

    probe = (
        "import json, os\n"
        "from pathlib import Path\n"
        "print(json.dumps({'uid': os.getuid(), 'etc_passwd': Path('/etc/passwd').exists(), "
        f"'host_sentinel': Path({str(host_sentinel)!r}).exists(), 'home_env': os.environ.get('HOME')}}))\n"
    )
    result = subprocess.run(
        [*command, "-I", "-c", probe],
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )

    assert result.returncode == 0
    assert json.loads(result.stdout) == {
        "uid": benchmark.MODEL_CODE_SANDBOX_UID,
        "etc_passwd": False,
        "host_sentinel": False,
        "home_env": None,
    }


@pytest.mark.skipif(shutil.which("bwrap") is None, reason="bubblewrap is required for the Linux sandbox boundary")
def test_bubblewrap_boundary_cannot_reach_host_loopback() -> None:
    benchmark = load_benchmark_module()
    command = benchmark.model_sandbox_command()
    assert command is not None

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        probe = (
            "import socket\n"
            "client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
            f"print(client.connect_ex(('127.0.0.1', {port})))\n"
        )
        result = subprocess.run(
            [*command, "-I", "-c", probe],
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )

    assert result.returncode == 0
    assert int(result.stdout.strip()) != 0


def test_model_code_infinite_loop_is_bounded() -> None:
    benchmark = load_benchmark_module()

    result = benchmark.run_python_function_tests(
        "def spin(values):\n    while True:\n        pass",
        "spin",
        [(([],), [])],
    )

    assert result["passed"] is False
    assert result["error"] in {
        "model code execution timed out",
        "isolated model code runner exited with 137",
        "isolated model code runner exited with -9",
        "isolated model code runner exited with -24",
    }
