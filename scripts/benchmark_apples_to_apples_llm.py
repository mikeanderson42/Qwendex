#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import textwrap
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

DEFAULT_CHAT_BASE = "http://127.0.0.1:4000/v1"
SAFE_MODEL_IMPORTS = {
    "collections": frozenset({"ChainMap", "Counter", "OrderedDict", "defaultdict", "deque", "namedtuple"}),
    "re": frozenset(
        {
            "A",
            "ASCII",
            "DOTALL",
            "I",
            "IGNORECASE",
            "L",
            "LOCALE",
            "M",
            "MULTILINE",
            "Match",
            "NOFLAG",
            "Pattern",
            "S",
            "U",
            "UNICODE",
            "VERBOSE",
            "X",
            "compile",
            "escape",
            "findall",
            "finditer",
            "fullmatch",
            "match",
            "search",
            "split",
            "sub",
            "subn",
        }
    ),
}
UNSAFE_MODEL_CALLS = {
    "__import__",
    "breakpoint",
    "compile",
    "delattr",
    "dir",
    "eval",
    "exec",
    "getattr",
    "globals",
    "help",
    "input",
    "locals",
    "open",
    "setattr",
    "vars",
}
MODEL_CODE_WALL_TIMEOUT_SECONDS = 5
MODEL_CODE_SANDBOX_UID = 65534
MODEL_CODE_SANDBOX_GID = 65534
MODEL_CODE_RUNNER = r'''
import collections as _collections
import json
import re as _re
import resource
import sys

payload = json.load(sys.stdin)
resource.setrlimit(resource.RLIMIT_CPU, (2, 2))
resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
resource.setrlimit(resource.RLIMIT_NOFILE, (16, 16))
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
if hasattr(resource, "RLIMIT_NPROC"):
    resource.setrlimit(resource.RLIMIT_NPROC, (1, 1))


class SafeModule:
    def __init__(self, exports):
        object.__setattr__(self, "_SafeModule__exports", exports)

    def __getattribute__(self, name):
        if name.startswith("_"):
            raise AttributeError(f"private module attribute is not available: {name}")
        exports = object.__getattribute__(self, "_SafeModule__exports")
        try:
            return exports[name]
        except KeyError as exc:
            raise AttributeError(f"module attribute is not allowed: {name}") from exc


SAFE_MODULES = {
    "collections": SafeModule(
        {
            "ChainMap": _collections.ChainMap,
            "Counter": _collections.Counter,
            "OrderedDict": _collections.OrderedDict,
            "defaultdict": _collections.defaultdict,
            "deque": _collections.deque,
            "namedtuple": _collections.namedtuple,
        }
    ),
    "re": SafeModule(
        {
            "A": _re.A,
            "ASCII": _re.ASCII,
            "DOTALL": _re.DOTALL,
            "I": _re.I,
            "IGNORECASE": _re.IGNORECASE,
            "L": _re.L,
            "LOCALE": _re.LOCALE,
            "M": _re.M,
            "MULTILINE": _re.MULTILINE,
            "Match": _re.Match,
            "NOFLAG": _re.NOFLAG,
            "Pattern": _re.Pattern,
            "S": _re.S,
            "U": _re.U,
            "UNICODE": _re.UNICODE,
            "VERBOSE": _re.VERBOSE,
            "X": _re.X,
            "compile": _re.compile,
            "escape": _re.escape,
            "findall": _re.findall,
            "finditer": _re.finditer,
            "fullmatch": _re.fullmatch,
            "match": _re.match,
            "search": _re.search,
            "split": _re.split,
            "sub": _re.sub,
            "subn": _re.subn,
        }
    ),
}

def safe_import(name, globals=None, locals=None, fromlist=(), level=0):
    if level or name not in SAFE_MODULES:
        raise ImportError(f"model import is not allowed: {name}")
    return SAFE_MODULES[name]

safe_builtins = {
    "Exception": Exception,
    "ValueError": ValueError,
    "abs": abs,
    "all": all,
    "any": any,
    "bool": bool,
    "dict": dict,
    "enumerate": enumerate,
    "filter": filter,
    "float": float,
    "int": int,
    "len": len,
    "list": list,
    "map": map,
    "max": max,
    "min": min,
    "range": range,
    "reversed": reversed,
    "round": round,
    "set": set,
    "sorted": sorted,
    "str": str,
    "sum": sum,
    "tuple": tuple,
    "zip": zip,
    "__import__": safe_import,
}

def decode_transport(value):
    if not isinstance(value, dict) or set(value) != {"kind", "value"}:
        raise ValueError("invalid benchmark transport value")
    kind = value["kind"]
    data = value["value"]
    if kind == "tuple":
        return tuple(decode_transport(item) for item in data)
    if kind == "list":
        return [decode_transport(item) for item in data]
    if kind == "dict":
        return {decode_transport(key): decode_transport(item) for key, item in data}
    if kind == "scalar":
        return data
    raise ValueError("unsupported benchmark transport kind")

def bounded_repr(value):
    text = repr(value)
    return text if len(text) <= 4096 else text[:4093] + "..."

namespace = {"__builtins__": safe_builtins}
try:
    exec(compile(payload["code"], "<model_code>", "exec"), namespace, namespace)
    func = namespace.get(payload["function_name"])
    if not callable(func):
        raise ValueError(f"missing callable {payload['function_name']}")
    results = []
    passed = True
    for case in payload["tests"]:
        try:
            actual = func(*decode_transport(case["args"]))
            expected = decode_transport(case["expected"])
            case_passed = actual == expected
            actual_repr = bounded_repr(actual)
        except Exception as exc:
            case_passed = False
            actual_repr = f"{type(exc).__name__}: {exc}"[:4096]
        results.append(
            {
                "args": bounded_repr(decode_transport(case["args"])),
                "expected": bounded_repr(decode_transport(case["expected"])),
                "actual": actual_repr,
                "passed": case_passed,
            }
        )
        passed = passed and case_passed
    print(json.dumps({"passed": passed, "case_results": results}))
except Exception as exc:
    print(json.dumps({"passed": False, "error": f"{type(exc).__name__}: {exc}", "case_results": []}))
'''


def post_json(url: str, payload: dict[str, Any], timeout: int) -> tuple[float, dict[str, Any]]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer no-key"},
        method="POST",
    )
    started = time.time()
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))
    return time.time() - started, data if isinstance(data, dict) else {}


def strip_think(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE).strip()


def parse_json_object(text: str) -> dict[str, Any]:
    stripped = strip_think(text).strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    decoder = json.JSONDecoder()
    candidates: list[dict[str, Any]] = []
    for idx, char in enumerate(stripped):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(stripped[idx:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            candidates.append(value)
    if candidates:
        return candidates[-1]
    return {}


def parse_json_candidates(text: str) -> list[dict[str, Any]]:
    stripped = strip_think(text).strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    decoder = json.JSONDecoder()
    candidates: list[dict[str, Any]] = []
    for idx, char in enumerate(stripped):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(stripped[idx:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            candidates.append(value)
    return candidates


def extract_code(text: str) -> str:
    stripped = strip_think(text)
    fenced = re.findall(
        r"```(?:python)?[ \t]*(?:\r?\n)?(.*?)```",
        stripped,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if fenced:
        return textwrap.dedent(fenced[-1]).strip()
    marker = re.search(r"(^|\n)\s*def\s+\w+\s*\(", stripped)
    if marker:
        prefix = stripped[: marker.start()]
        import_marker = re.search(r"(^|\n)\s*(?:import\s+\w+|from\s+\w+\s+import\s+)", prefix)
        start = import_marker.start() if import_marker else marker.start()
        return textwrap.dedent(stripped[start:]).strip()
    return textwrap.dedent(stripped).strip()


def contaminated(text: str) -> bool:
    lower = text.lower()
    markers = [
        "<tool_call",
        "</tool_call",
        "exec_command",
        "update_plan",
        "terminal tool",
        "calling the tool",
        "function_call",
    ]
    return any(marker in lower for marker in markers)


def nvidia_snapshot() -> dict[str, Any]:
    command = [
        "nvidia-smi",
        "--query-gpu=index,uuid,name,memory.total,memory.used,memory.free,utilization.gpu",
        "--format=csv,noheader,nounits",
    ]
    try:
        out = subprocess.check_output(command, text=True)
    except (OSError, subprocess.CalledProcessError):
        return {"available": False}

    gpus: list[dict[str, Any]] = []
    for line in out.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 7:
            continue
        try:
            gpus.append(
                {
                    "gpu_index": int(parts[0]),
                    "gpu_uuid": parts[1],
                    "name": parts[2],
                    "memory_total_mb": int(parts[3]),
                    "memory_used_mb": int(parts[4]),
                    "memory_free_mb": int(parts[5]),
                    "gpu_utilization_percent": int(parts[6]),
                }
            )
        except ValueError:
            continue
    if not gpus:
        return {"available": False}

    selected = gpus[0]
    selection = "first detected GPU"
    try:
        apps_out = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-compute-apps=gpu_uuid,pid,process_name,used_gpu_memory",
                "--format=csv,noheader,nounits",
            ],
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        apps_out = ""

    llama_allocations: dict[str, int] = {}
    for line in apps_out.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 4 or "llama-server" not in parts[2]:
            continue
        try:
            llama_allocations[parts[0]] = llama_allocations.get(parts[0], 0) + int(parts[3])
        except ValueError:
            continue
    if llama_allocations:
        selected_uuid, allocation_mb = max(llama_allocations.items(), key=lambda item: item[1])
        selected = next((gpu for gpu in gpus if gpu["gpu_uuid"] == selected_uuid), selected)
        if selected["gpu_uuid"] == selected_uuid:
            selection = "largest llama-server allocation"
            selected = {**selected, "llama_server_memory_mb": allocation_mb}

    return {
        "available": True,
        "selection": selection,
        **{key: value for key, value in selected.items() if key != "gpu_uuid"},
    }


class Task:
    def __init__(
        self,
        name: str,
        category: str,
        prompt: str,
        grader: Callable[[str], tuple[bool, dict[str, Any]]],
        *,
        max_tokens: int = 512,
        response_schema: dict[str, Any] | None = None,
        structured_prompt: str | None = None,
        structured_tool: bool = False,
    ) -> None:
        self.name = name
        self.category = category
        self.prompt = prompt
        self.grader = grader
        self.max_tokens = max_tokens
        self.response_schema = response_schema
        self.structured_prompt = structured_prompt
        self.structured_tool = structured_tool


def grade_exact(expected: str) -> Callable[[str], tuple[bool, dict[str, Any]]]:
    def inner(text: str) -> tuple[bool, dict[str, Any]]:
        answer = strip_think(text)
        return answer == expected, {"answer": answer}

    return inner


def grade_json(expected: dict[str, Any], *, numeric_tolerance: float = 0.0) -> Callable[[str], tuple[bool, dict[str, Any]]]:
    def same(a: Any, b: Any) -> bool:
        if isinstance(b, float) or isinstance(a, float):
            try:
                return math.isclose(float(a), float(b), rel_tol=numeric_tolerance, abs_tol=numeric_tolerance)
            except (TypeError, ValueError):
                return False
        return a == b

    def inner(text: str) -> tuple[bool, dict[str, Any]]:
        candidates = parse_json_candidates(text)
        parsed = candidates[-1] if candidates else {}
        for candidate in reversed(candidates):
            if all(same(candidate.get(key), value) for key, value in expected.items()):
                return True, {"parsed": candidate, "expected": expected, "candidate_count": len(candidates)}
        return False, {"parsed": parsed, "expected": expected, "candidate_count": len(candidates)}

    return inner


def grade_csv_transform(text: str) -> tuple[bool, dict[str, Any]]:
    candidates = parse_json_candidates(text)
    parsed = {}
    for candidate in candidates:
        if "active_by_region" in candidate:
            parsed = candidate
    if not parsed and candidates:
        parsed = candidates[-1]
    expected = {"east": 17, "west": 7, "south": 4}
    passed = parsed.get("active_by_region") == expected and parsed.get("duplicate_ids") == ["A-002"]
    return passed, {"parsed": parsed, "expected_active_by_region": expected}


def validate_model_code(code: str) -> str:
    if not code.strip():
        return "model code is empty"
    if len(code.encode("utf-8")) > 12_000:
        return "model code exceeds the 12000-byte safety limit"
    try:
        tree = ast.parse(code, filename="<model_code>", mode="exec")
    except SyntaxError as exc:
        return f"model code syntax error: {exc.msg}"
    if any(not isinstance(node, (ast.FunctionDef, ast.Import, ast.ImportFrom)) for node in tree.body):
        return "model code may only define functions and approved imports at module scope"
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.ClassDef, ast.Global, ast.Nonlocal)):
            return f"model code contains forbidden syntax: {type(node).__name__}"
        if isinstance(node, ast.FunctionDef) and node.decorator_list:
            return "model functions may not use decorators"
        if isinstance(node, ast.Import):
            if any(alias.name not in SAFE_MODEL_IMPORTS for alias in node.names):
                return "model code imports a module outside the allowlist"
        if isinstance(node, ast.ImportFrom):
            if node.level or node.module not in SAFE_MODEL_IMPORTS:
                return "model code imports a module outside the allowlist"
            allowed_names = SAFE_MODEL_IMPORTS[node.module]
            if any(alias.name == "*" or alias.name not in allowed_names for alias in node.names):
                return "model code imports a name outside the allowlist"
        if isinstance(node, ast.Name) and node.id.startswith("__"):
            return "model code accesses a dunder name"
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            return "model code accesses a private attribute"
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in UNSAFE_MODEL_CALLS:
            return f"model code calls forbidden builtin: {node.func.id}"
    return ""


def encode_model_transport(value: Any) -> dict[str, Any]:
    """Preserve benchmark values across JSON IPC without changing equality semantics."""
    if isinstance(value, tuple):
        return {"kind": "tuple", "value": [encode_model_transport(item) for item in value]}
    if isinstance(value, list):
        return {"kind": "list", "value": [encode_model_transport(item) for item in value]}
    if isinstance(value, dict):
        return {
            "kind": "dict",
            "value": [[encode_model_transport(key), encode_model_transport(item)] for key, item in value.items()],
        }
    return {"kind": "scalar", "value": value}


def trusted_root_owned_executable(
    value: str | os.PathLike[str] | None,
    *,
    allowed_roots: tuple[Path, ...],
) -> Path | None:
    """Resolve a non-writable root-owned executable below an approved system root."""
    if not value:
        return None
    try:
        resolved = Path(value).resolve(strict=True)
        metadata = resolved.stat()
    except OSError:
        return None
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        return None
    if metadata.st_uid != 0 or metadata.st_mode & 0o022:
        return None
    if not any(root == resolved or root in resolved.parents for root in allowed_roots):
        return None
    return resolved


def model_sandbox_command() -> list[str] | None:
    """Build the fail-closed bubblewrap command used for generated model code."""
    bubblewrap = trusted_root_owned_executable(
        shutil.which("bwrap"),
        allowed_roots=(Path("/usr/bin"), Path("/bin")),
    )
    sandbox_python = trusted_root_owned_executable(
        sys.executable,
        allowed_roots=(Path("/usr"),),
    )
    if bubblewrap is None or sandbox_python is None:
        return None
    return [
        str(bubblewrap),
        "--unshare-all",
        "--unshare-user",
        "--disable-userns",
        "--die-with-parent",
        "--new-session",
        "--clearenv",
        "--setenv",
        "PATH",
        "/usr/bin",
        "--setenv",
        "LANG",
        "C.UTF-8",
        "--setenv",
        "PYTHONHASHSEED",
        "0",
        "--ro-bind",
        "/usr",
        "/usr",
        "--symlink",
        "usr/lib",
        "/lib",
        "--symlink",
        "usr/lib",
        "/lib64",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/tmp",
        "--dir",
        "/etc",
        "--dir",
        "/home",
        "--dir",
        "/run",
        "--dir",
        "/var",
        "--chdir",
        "/tmp",
        "--uid",
        str(MODEL_CODE_SANDBOX_UID),
        "--gid",
        str(MODEL_CODE_SANDBOX_GID),
        "--cap-drop",
        "ALL",
        "--",
    ]


def run_python_function_tests(code: str, function_name: str, tests: list[tuple[tuple[Any, ...], Any]]) -> dict[str, Any]:
    safety_error = validate_model_code(code)
    if safety_error:
        return {"passed": False, "error": safety_error, "case_results": []}
    sandbox_command = model_sandbox_command()
    if sandbox_command is None:
        return {
            "passed": False,
            "error": "secure model-code sandbox is unavailable (bubblewrap with a system Python is required)",
            "case_results": [],
        }
    payload = {
        "code": code,
        "function_name": function_name,
        "tests": [{"args": encode_model_transport(args), "expected": encode_model_transport(expected)} for args, expected in tests],
    }
    try:
        input_payload = json.dumps(payload)
    except (TypeError, ValueError) as exc:
        return {"passed": False, "error": f"benchmark test data is not transportable: {exc}", "case_results": []}
    try:
        process = subprocess.Popen(
            [*sandbox_command, str(Path(sys.executable).resolve()), "-I", "-c", MODEL_CODE_RUNNER],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            start_new_session=True,
        )
        stdout, _ = process.communicate(input_payload, timeout=MODEL_CODE_WALL_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.communicate()
        return {"passed": False, "error": "model code execution timed out", "case_results": []}
    except OSError:
        return {"passed": False, "error": "secure model-code sandbox failed to start", "case_results": []}
    if process.returncode != 0:
        return {
            "passed": False,
            "error": f"isolated model code runner exited with {process.returncode}",
            "case_results": [],
        }
    try:
        payload_result = json.loads(stdout)
    except json.JSONDecodeError:
        return {"passed": False, "error": "isolated model code runner returned invalid JSON", "case_results": []}
    return payload_result if isinstance(payload_result, dict) else {
        "passed": False,
        "error": "isolated model code runner returned an invalid result",
        "case_results": [],
    }


def grade_merge_intervals(text: str) -> tuple[bool, dict[str, Any]]:
    code = extract_code(text)
    tests = [
        (([],), []),
        (([(1, 3), (2, 6), (8, 10), (15, 18)],), [(1, 6), (8, 10), (15, 18)]),
        (([(5, 7), (1, 2), (2, 4), (9, 9)],), [(1, 4), (5, 7), (9, 9)]),
        (([(-3, -1), (-2, 2), (3, 3)],), [(-3, 2), (3, 3)]),
    ]
    result = run_python_function_tests(code, "merge_intervals", tests)
    result["code_prefix"] = code[:1200]
    return bool(result.get("passed")), result


def grade_word_frequencies(text: str) -> tuple[bool, dict[str, Any]]:
    code = extract_code(text)
    tests = [
        (("Apple banana apple.",), {"apple": 2, "banana": 1}),
        (("Red, red; BLUE blue blue!",), {"red": 2, "blue": 3}),
        (("Numbers 42 and 42 are tokens",), {"numbers": 1, "42": 2, "and": 1, "are": 1, "tokens": 1}),
    ]
    result = run_python_function_tests(code, "word_frequencies", tests)
    result["code_prefix"] = code[:1200]
    return bool(result.get("passed")), result


def grade_fix_sum_even_squares(text: str) -> tuple[bool, dict[str, Any]]:
    code = extract_code(text)
    tests = [
        (([1, 2, 3, 4, 5],), 20),
        (([-2, -1, 0, 3, 6],), 40),
        (([1, 3, 5],), 0),
    ]
    result = run_python_function_tests(code, "f", tests)
    result["code_prefix"] = code[:1200]
    return bool(result.get("passed")), result


def build_long_context_prompt(*, structured: bool = False) -> str:
    rows = []
    for idx in range(1, 721):
        code = f"K{idx:03d}"
        value = f"v{(idx * 73 + 19) % 997:03d}"
        rows.append(f"{code}={value}")
    rows[57] = "K058=ORCHID_58"
    rows[238] = "K239=QUARTZ_239"
    rows[511] = "K512=LYRA_512"
    rows[699] = "K700=NOVA_700"
    prefix = "Needle-in-context task. Read the key-value table.\n" if structured else "Needle-in-context task. Read the key-value table and return JSON only.\n"
    suffix = "" if structured else '\nReturn exactly {"K058":"...","K239":"...","K512":"...","K700":"...","count":720}.'
    return prefix + "\n".join(rows) + suffix


def build_tasks() -> list[Task]:
    csv_data = """id,region,status,count
A-001,east,active,8
A-002,west,active,5
A-002,west,active,7
A-003,east,inactive,99
A-004,south,active,4
A-005,east,active,9
"""
    long_context_prompt = build_long_context_prompt()
    structured_long_context_prompt = build_long_context_prompt(structured=True)
    return [
        Task(
            "exact_instruction",
            "instruction",
            "Reply with exactly STANDARD_BENCH_READY and nothing else.",
            grade_exact("STANDARD_BENCH_READY"),
            max_tokens=256,
        ),
        Task(
            "gsm8k_arithmetic",
            "math",
            (
                "Return JSON only. A lab has 18 samples. Each sample gets 7 readings. "
                "Nine readings are discarded as calibration checks. The remaining readings are split equally "
                "among 3 analysts. The answer field must equal the final number shown in work. "
                'Return {"answer": number, "work": "..."}'
            ),
            grade_json({"answer": 39}),
            max_tokens=1024,
            response_schema={
                "type": "object",
                "properties": {
                    "answer": {"type": "number"},
                    "work": {"type": "string"},
                },
                "required": ["answer", "work"],
            },
            structured_prompt=(
                "A lab has 18 samples. Each sample gets 7 readings. Nine readings are discarded as calibration checks. "
                "The remaining readings are split equally among 3 analysts."
            ),
            structured_tool=True,
        ),
        Task(
            "mmlu_style_python",
            "knowledge",
            (
                "Return JSON only. Multiple choice: In Python, what does len({'a': 1, 'b': 2}) return? "
                'A) 1 B) 2 C) 3 D) TypeError. Return {"choice":"<letter>","answer":number}.'
            ),
            grade_json({"choice": "B", "answer": 2}),
            max_tokens=1024,
            response_schema={
                "type": "object",
                "properties": {
                    "choice": {"type": "string"},
                    "answer": {"type": "number"},
                },
                "required": ["choice", "answer"],
            },
            structured_prompt=(
                "Multiple choice: In Python, what does len({'a': 1, 'b': 2}) return? "
                "A) 1 B) 2 C) 3 D) TypeError."
            ),
        ),
        Task(
            "blocker_json",
            "structured",
            (
                "Return JSON only. Rows: "
                '[{"id":"a","status":"complete_validated","blocker":""},'
                '{"id":"b","status":"complete_validated","blocker":"source hold"},'
                '{"id":"c","status":"pending","blocker":""},'
                '{"id":"d","status":"complete_validated","blocker":"manual review"}]. '
                'A row is blocked iff blocker is non-empty. Return {"blocked_ids":[...],"blocked_count":number}.'
            ),
            grade_json({"blocked_ids": ["b", "d"], "blocked_count": 2}),
            max_tokens=1024,
            response_schema={
                "type": "object",
                "properties": {
                    "blocked_ids": {"type": "array", "items": {"type": "string"}},
                    "blocked_count": {"type": "number"},
                },
                "required": ["blocked_ids", "blocked_count"],
            },
            structured_prompt=(
                "Rows: "
                '[{"id":"a","status":"complete_validated","blocker":""},'
                '{"id":"b","status":"complete_validated","blocker":"source hold"},'
                '{"id":"c","status":"pending","blocker":""},'
                '{"id":"d","status":"complete_validated","blocker":"manual review"}]. '
                "A row is blocked iff blocker is non-empty."
            ),
        ),
        Task(
            "csv_transform",
            "structured",
            (
                "Return JSON only. Deduplicate rows by id by keeping the last occurrence. "
                "Then sum count for rows whose status is active by region. CSV:\n"
                f"{csv_data}\n"
                "duplicate_ids must contain each duplicated source id once, in source order. "
                'Return {"active_by_region":{"east":number,"west":number,"south":number},"duplicate_ids":[...]}.'
            ),
            grade_csv_transform,
            max_tokens=1024,
            response_schema={
                "type": "object",
                "properties": {
                    "active_by_region": {
                        "type": "object",
                        "properties": {
                            "east": {"type": "number"},
                            "west": {"type": "number"},
                            "south": {"type": "number"},
                        },
                        "required": ["east", "west", "south"],
                    },
                    "duplicate_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["active_by_region", "duplicate_ids"],
            },
            structured_prompt=(
                "Deduplicate rows by id by keeping the last occurrence. Then sum count for rows whose status is active by region. CSV:\n"
                f"{csv_data}"
            ),
        ),
        Task(
            "long_context_recall",
            "long_context",
            long_context_prompt,
            grade_json({"K058": "ORCHID_58", "K239": "QUARTZ_239", "K512": "LYRA_512", "K700": "NOVA_700", "count": 720}),
            max_tokens=1024,
            response_schema={
                "type": "object",
                "properties": {
                    "K058": {"type": "string"},
                    "K239": {"type": "string"},
                    "K512": {"type": "string"},
                    "K700": {"type": "string"},
                    "count": {"type": "number"},
                },
                "required": ["K058", "K239", "K512", "K700", "count"],
            },
            structured_prompt=structured_long_context_prompt,
        ),
        Task(
            "humaneval_merge_intervals",
            "coding",
            (
                "Return Python code only. Define function merge_intervals(intervals). "
                "Input is a list of (start, end) integer tuples in arbitrary order. Merge overlapping intervals "
                "when next_start <= current_end. Return a list of tuples sorted by start. Do not print."
            ),
            grade_merge_intervals,
            max_tokens=1024,
        ),
        Task(
            "mbpp_word_frequencies",
            "coding",
            (
                "Return Python code only. Define function word_frequencies(text). "
                "It should lowercase text, split into alphanumeric word tokens, and return a dict mapping token to count. "
                "You may import only re and collections from the Python standard library. Do not print."
            ),
            grade_word_frequencies,
            max_tokens=1024,
        ),
        Task(
            "debug_even_square_sum",
            "coding",
            (
                "Return Python code only. This function is wrong because it adds even numbers instead of their squares:\n"
                "def f(xs):\n"
                "    total = 0\n"
                "    for x in xs:\n"
                "        if x % 2 == 0:\n"
                "            total += x\n"
                "    return total\n"
                "Return a corrected definition of f(xs). Do not print."
            ),
            grade_fix_sum_even_squares,
            max_tokens=1024,
        ),
        Task(
            "planning_dependencies",
            "reasoning",
            (
                "Return JSON only. Tasks and dependencies: extract has none; normalize depends on extract; "
                "validate depends on normalize; report depends on validate; archive depends on report. "
                "can_parallelize_initial must be a JSON array, not a Boolean, containing every task that can start "
                'before another task completes. Return {"order":[...],"can_parallelize_initial":[...]}.'
            ),
            grade_json({"order": ["extract", "normalize", "validate", "report", "archive"], "can_parallelize_initial": ["extract"]}),
            max_tokens=1024,
            response_schema={
                "type": "object",
                "properties": {
                    "order": {"type": "array", "items": {"type": "string"}},
                    "can_parallelize_initial": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["order", "can_parallelize_initial"],
            },
            structured_prompt=(
                "Tasks and dependencies: extract has none; normalize depends on extract; validate depends on normalize; "
                "report depends on validate; archive depends on report."
            ),
            structured_tool=True,
        ),
    ]


def structured_tool_content(response: dict[str, Any], tool_name: str = "submit_final") -> str:
    """Return JSON arguments from a declared final-result tool call, or an empty string.

    Tool-call data is model-produced external input. Parse it as JSON only and
    accept an object only; never evaluate or execute it.
    """
    try:
        choices = response.get("choices")
        if not isinstance(choices, list) or not choices:
            return ""
        message = choices[0].get("message")
        if not isinstance(message, dict):
            return ""
        tool_calls = message.get("tool_calls")
        if not isinstance(tool_calls, list):
            return ""
    except (AttributeError, IndexError, TypeError):
        return ""
    for tool_call in tool_calls:
        if not isinstance(tool_call, dict):
            continue
        function = tool_call.get("function")
        if not isinstance(function, dict) or function.get("name") != tool_name:
            continue
        raw_arguments = function.get("arguments")
        if not isinstance(raw_arguments, str):
            continue
        try:
            arguments = json.loads(raw_arguments)
        except json.JSONDecodeError:
            continue
        if isinstance(arguments, dict):
            return json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
    return ""


def run_task(args: argparse.Namespace, task: Task) -> dict[str, Any]:
    max_tokens = max(1, int(math.ceil(task.max_tokens * args.max_token_scale)))
    if args.min_task_max_tokens:
        max_tokens = max(max_tokens, args.min_task_max_tokens)
    use_structured_tool = bool(args.structured_json_tools and task.structured_tool and task.response_schema)
    prompt = task.structured_prompt if use_structured_tool and task.structured_prompt else task.prompt
    if use_structured_tool:
        prompt += "\nUse the available function submit_final as the only response. Do not write ordinary assistant content."
    payload: dict[str, Any] = {
        "model": args.model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": args.temperature,
    }
    if args.seed is not None:
        payload["seed"] = args.seed
    if use_structured_tool:
        payload["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": "submit_final",
                    "description": "Submit only the final result required by the user task.",
                    "parameters": task.response_schema,
                },
            }
        ]
    if args.chat_template_enable_thinking != "default":
        payload["chat_template_kwargs"] = {
            "enable_thinking": args.chat_template_enable_thinking == "true",
        }
    if args.thinking_budget_tokens is not None:
        payload["thinking_budget_tokens"] = args.thinking_budget_tokens
    if args.top_p is not None:
        payload["top_p"] = args.top_p
    if args.top_k is not None:
        payload["top_k"] = args.top_k
    if args.min_p is not None:
        payload["min_p"] = args.min_p
    elapsed, response = post_json(f"{args.chat_base.rstrip('/')}/chat/completions", payload, args.timeout)
    content = ""
    response_transport = "content"
    try:
        content = response["choices"][0]["message"].get("content", "")
    except (KeyError, IndexError, TypeError, AttributeError):
        content = ""
    if use_structured_tool:
        tool_content = structured_tool_content(response)
        if tool_content:
            content = tool_content
            response_transport = "tool_call"
    passed, details = task.grader(content)
    usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
    completion_tokens = int(usage.get("completion_tokens") or 0)
    return {
        "name": task.name,
        "category": task.category,
        "passed": passed,
        "elapsed_s": elapsed,
        "completion_tokens": completion_tokens,
        "max_tokens": max_tokens,
        "base_max_tokens": task.max_tokens,
        "completion_tokens_per_s": completion_tokens / elapsed if completion_tokens and elapsed else None,
        "prompt_tokens": usage.get("prompt_tokens"),
        "total_tokens": usage.get("total_tokens"),
        "response_transport": response_transport,
        "visible_think": "<think>" in content.lower(),
        "contaminated": contaminated(content),
        "content_prefix": content[:1600],
        "details": details,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--profile", default="")
    parser.add_argument("--chat-base", default=DEFAULT_CHAT_BASE)
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--model-sha256", default="")
    parser.add_argument("--top-p", type=float)
    parser.add_argument("--top-k", type=int)
    parser.add_argument("--min-p", type=float)
    parser.add_argument("--max-token-scale", type=float, default=1.0)
    parser.add_argument("--min-task-max-tokens", type=int, default=0)
    parser.add_argument("--chat-template-enable-thinking", choices=["default", "true", "false"], default="default")
    parser.add_argument("--thinking-budget-tokens", type=int)
    parser.add_argument(
        "--structured-json-tools",
        action="store_true",
        help="Use declared function schemas for JSON tasks while leaving instruction and code tasks as plain completions.",
    )
    parser.add_argument("--timeout", type=int, default=240)
    args = parser.parse_args()

    tasks = build_tasks()
    started = time.time()
    result: dict[str, Any] = {
        "schema": "local_llm_stack.apples_to_apples_benchmark.v2",
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "profile": args.profile,
        "model": args.model,
        "model_sha256": args.model_sha256,
        "chat_base": args.chat_base,
        "sampler": {
            "temperature": args.temperature,
            "seed": args.seed,
            "top_p": args.top_p,
            "top_k": args.top_k,
            "min_p": args.min_p,
        },
        "output_budget": {
            "max_token_scale": args.max_token_scale,
            "min_task_max_tokens": args.min_task_max_tokens,
        },
        "request_template_controls": {
            "chat_template_enable_thinking": args.chat_template_enable_thinking,
            "thinking_budget_tokens": args.thinking_budget_tokens,
            "structured_json_tools": args.structured_json_tools,
        },
        "response_mode": "structured_json_tools" if args.structured_json_tools else "plain",
        "structured_json_tool_tasks": [task.name for task in tasks if task.structured_tool] if args.structured_json_tools else [],
        "benchmark_type": "standardized-style local suite: instruction, math, knowledge, structured JSON, long-context recall, coding, reasoning",
        "gpu_before": nvidia_snapshot(),
        "tasks": [],
    }
    for task in tasks:
        result["tasks"].append(run_task(args, task))
    result["gpu_after"] = nvidia_snapshot()
    result["elapsed_s"] = time.time() - started
    result["score"] = sum(1 for task in result["tasks"] if task.get("passed"))
    result["max_score"] = len(result["tasks"])
    result["contamination_count"] = sum(1 for task in result["tasks"] if task.get("contaminated"))
    result["visible_think_count"] = sum(1 for task in result["tasks"] if task.get("visible_think"))
    result["completion_tokens"] = sum(int(task.get("completion_tokens") or 0) for task in result["tasks"])
    task_elapsed = sum(float(task.get("elapsed_s") or 0.0) for task in result["tasks"])
    result["task_elapsed_s"] = task_elapsed
    result["aggregate_completion_tokens_per_s"] = (
        result["completion_tokens"] / task_elapsed if result["completion_tokens"] and task_elapsed else None
    )
    by_category: dict[str, dict[str, int]] = {}
    for task in result["tasks"]:
        category = str(task.get("category"))
        stats = by_category.setdefault(category, {"passed": 0, "total": 0})
        stats["total"] += 1
        if task.get("passed"):
            stats["passed"] += 1
    result["by_category"] = by_category
    result["passed"] = result["score"] == result["max_score"] and result["contamination_count"] == 0

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "passed": result["passed"],
                "score": result["score"],
                "max_score": result["max_score"],
                "elapsed_s": result["elapsed_s"],
                "task_elapsed_s": result["task_elapsed_s"],
                "completion_tokens": result["completion_tokens"],
                "aggregate_completion_tokens_per_s": result["aggregate_completion_tokens_per_s"],
                "visible_think_count": result["visible_think_count"],
                "contamination_count": result["contamination_count"],
                "output": str(output),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
