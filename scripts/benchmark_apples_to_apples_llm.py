#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import textwrap
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

DEFAULT_CHAT_BASE = "http://127.0.0.1:4000/v1"


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
    fenced = re.findall(r"```(?:python)?\s*(.*?)```", stripped, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        return fenced[-1].strip()
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
        "--query-gpu=name,memory.total,memory.used,memory.free,utilization.gpu",
        "--format=csv,noheader,nounits",
    ]
    try:
        out = subprocess.check_output(command, text=True).strip().split(",", 4)
    except (OSError, subprocess.CalledProcessError):
        return {"available": False}
    name, total, used, free, util = [part.strip() for part in out]
    return {
        "available": True,
        "name": name,
        "memory_total_mb": int(total),
        "memory_used_mb": int(used),
        "memory_free_mb": int(free),
        "gpu_utilization_percent": int(util),
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
    ) -> None:
        self.name = name
        self.category = category
        self.prompt = prompt
        self.grader = grader
        self.max_tokens = max_tokens


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


def run_python_function_tests(code: str, function_name: str, tests: list[tuple[tuple[Any, ...], Any]]) -> dict[str, Any]:
    namespace: dict[str, Any] = {}
    try:
        compiled = compile(code, "<model_code>", "exec")
        exec(compiled, namespace, namespace)
    except Exception as exc:  # noqa: BLE001 - benchmark needs the failure text.
        return {"passed": False, "error": f"{type(exc).__name__}: {exc}", "case_results": []}
    func = namespace.get(function_name)
    if not callable(func):
        return {"passed": False, "error": f"missing callable {function_name}", "case_results": []}
    case_results = []
    all_passed = True
    for args, expected in tests:
        try:
            actual = func(*args)
            case_passed = actual == expected
        except Exception as exc:  # noqa: BLE001
            actual = f"{type(exc).__name__}: {exc}"
            case_passed = False
        case_results.append({"args": repr(args), "expected": repr(expected), "actual": repr(actual), "passed": case_passed})
        all_passed = all_passed and case_passed
    return {"passed": all_passed, "case_results": case_results}


def grade_merge_intervals(text: str) -> tuple[bool, dict[str, Any]]:
    code = extract_code(text)
    tests = [
        (([],), []),
        (([(1, 3), (2, 6), (8, 10), (15, 18)],), [(1, 6), (8, 10), (15, 18)]),
        (([(5, 7), (1, 2), (2, 4), (9, 9)],), [(1, 4), (5, 7), (9, 9)]),
        ((((-3, -1), (-2, 2), (3, 3)),), [(-3, 2), (3, 3)]),
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


def build_long_context_prompt() -> str:
    rows = []
    for idx in range(1, 721):
        code = f"K{idx:03d}"
        value = f"v{(idx * 73 + 19) % 997:03d}"
        rows.append(f"{code}={value}")
    rows[57] = "K058=ORCHID_58"
    rows[238] = "K239=QUARTZ_239"
    rows[511] = "K512=LYRA_512"
    rows[699] = "K700=NOVA_700"
    return (
        "Needle-in-context task. Read the key-value table and return JSON only.\n"
        + "\n".join(rows)
        + '\nReturn exactly {"K058":"...","K239":"...","K512":"...","K700":"...","count":720}.'
    )


def build_tasks() -> list[Task]:
    csv_data = """id,region,status,count
A-001,east,active,8
A-002,west,active,5
A-002,west,active,7
A-003,east,inactive,99
A-004,south,active,4
A-005,east,active,9
"""
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
                'among 3 analysts. Return {"answer": number, "work": "..."}'
            ),
            grade_json({"answer": 39}),
            max_tokens=1024,
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
        ),
        Task(
            "csv_transform",
            "structured",
            (
                "Return JSON only. Deduplicate rows by id by keeping the last occurrence. "
                "Then sum count for rows whose status is active by region. CSV:\n"
                f"{csv_data}\n"
                'Return {"active_by_region":{"east":number,"west":number,"south":number},"duplicate_ids":[...]}.'
            ),
            grade_csv_transform,
            max_tokens=1024,
        ),
        Task(
            "long_context_recall",
            "long_context",
            build_long_context_prompt(),
            grade_json({"K058": "ORCHID_58", "K239": "QUARTZ_239", "K512": "LYRA_512", "K700": "NOVA_700", "count": 720}),
            max_tokens=1024,
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
                "Use only the Python standard library. Do not print."
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
                'Return {"order":[...],"can_parallelize_initial":[...]}.'
            ),
            grade_json({"order": ["extract", "normalize", "validate", "report", "archive"], "can_parallelize_initial": ["extract"]}),
            max_tokens=1024,
        ),
    ]


def run_task(args: argparse.Namespace, task: Task) -> dict[str, Any]:
    max_tokens = max(1, int(math.ceil(task.max_tokens * args.max_token_scale)))
    if args.min_task_max_tokens:
        max_tokens = max(max_tokens, args.min_task_max_tokens)
    payload: dict[str, Any] = {
        "model": args.model,
        "messages": [{"role": "user", "content": task.prompt}],
        "max_tokens": max_tokens,
        "temperature": args.temperature,
    }
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
    try:
        content = response["choices"][0]["message"].get("content", "")
    except (KeyError, IndexError, TypeError, AttributeError):
        content = ""
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
    parser.add_argument("--top-p", type=float)
    parser.add_argument("--top-k", type=int)
    parser.add_argument("--min-p", type=float)
    parser.add_argument("--max-token-scale", type=float, default=1.0)
    parser.add_argument("--min-task-max-tokens", type=int, default=0)
    parser.add_argument("--chat-template-enable-thinking", choices=["default", "true", "false"], default="default")
    parser.add_argument("--thinking-budget-tokens", type=int)
    parser.add_argument("--timeout", type=int, default=240)
    args = parser.parse_args()

    tasks = build_tasks()
    started = time.time()
    result: dict[str, Any] = {
        "schema": "local_llm_stack.apples_to_apples_benchmark.v1",
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "profile": args.profile,
        "model": args.model,
        "chat_base": args.chat_base,
        "sampler": {
            "temperature": args.temperature,
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
        },
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
