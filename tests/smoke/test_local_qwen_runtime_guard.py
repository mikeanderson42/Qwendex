import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts/local_qwen_runtime_guard.py"


def load_guard_module():
    spec = importlib.util.spec_from_file_location("local_qwen_runtime_guard_test", MODULE_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def user_message(text="Do the task."):
    return {"type": "message", "role": "user", "content": text}


def call(call_id, name="exec_command", arguments=None):
    return {
        "type": "function_call",
        "call_id": call_id,
        "name": name,
        "arguments": arguments if arguments is not None else {"cmd": "git status --short"},
    }


def output(call_id, text="ok\n"):
    return {"type": "function_call_output", "call_id": call_id, "output": text}


def test_balanced_profile_matches_qwen_thresholds():
    guard = load_guard_module()

    config = guard.GuardConfig(profile="balanced")

    assert config.consecutive_identical_tool_call_threshold == 5
    assert config.turn_tool_call_cap == 100
    assert config.global_duplicate_tool_call_threshold == 6
    assert config.alternating_tool_call_pattern_cycles == 3
    assert config.read_loop_threshold == 8
    assert config.read_loop_window == 15
    assert config.action_stagnation_threshold == 8
    assert config.shell_command_stagnation_threshold == 8


def test_consecutive_identical_tool_calls_fire_at_qwen_threshold():
    guard = load_guard_module()
    runtime_guard = guard.RuntimeGuard(guard.GuardConfig(profile="balanced"))
    repeated = {"cmd": "python3 -m pytest tests/smoke/test_example.py -q"}

    below = runtime_guard.evaluate_history(
        [user_message(), *[call(f"call_{index}", arguments=repeated) for index in range(4)]]
    )
    at_threshold = runtime_guard.evaluate_history(
        [user_message(), *[call(f"call_{index}", arguments=repeated) for index in range(5)]]
    )

    assert below.action == guard.GuardAction.ALLOW
    assert at_threshold.action == guard.GuardAction.STOP
    assert at_threshold.loop_type == guard.LoopType.CONSECUTIVE_IDENTICAL_TOOL_CALLS
    assert at_threshold.observed == 5


def test_turn_tool_call_cap_fires_only_after_cap_is_exceeded():
    guard = load_guard_module()
    runtime_guard = guard.RuntimeGuard(guard.GuardConfig(profile="balanced", turn_tool_call_cap=4))

    at_cap = runtime_guard.evaluate_history(
        [user_message(), *[call(f"call_{index}", arguments={"cmd": f"printf '%s\\n' {index}"}) for index in range(4)]]
    )
    over_cap = runtime_guard.evaluate_history(
        [user_message(), *[call(f"call_{index}", arguments={"cmd": f"printf '%s\\n' {index}"}) for index in range(5)]]
    )

    assert at_cap.action == guard.GuardAction.ALLOW
    assert over_cap.action == guard.GuardAction.STOP
    assert over_cap.loop_type == guard.LoopType.TURN_TOOL_CALL_CAP


def test_run_tool_call_budget_blocks_history_and_next_proposed_call():
    guard = load_guard_module()
    runtime_guard = guard.RuntimeGuard(
        guard.GuardConfig(profile="balanced", run_max_tool_calls=2, turn_tool_call_cap=100)
    )
    history_at_budget = [
        user_message(),
        call("call_1", arguments={"cmd": "printf one"}),
        call("call_2", arguments={"cmd": "printf two"}),
    ]

    at_budget = runtime_guard.evaluate_history(history_at_budget)
    over_budget = runtime_guard.evaluate_history(
        [*history_at_budget, call("call_3", arguments={"cmd": "printf three"})]
    )
    proposed = runtime_guard.evaluate_proposed_budget(
        history_at_budget,
        call("call_3", arguments={"cmd": "printf three"}),
    )

    assert at_budget.action == guard.GuardAction.ALLOW
    assert over_budget.action == guard.GuardAction.STOP
    assert over_budget.loop_type == guard.LoopType.TURN_TOOL_CALL_CAP
    assert over_budget.metadata == {"budget": "run_max_tool_calls"}
    assert proposed.action == guard.GuardAction.STOP
    assert proposed.metadata == {"budget": "run_max_tool_calls"}


def test_global_duplicate_tool_call_fires_when_interleaved():
    guard = load_guard_module()
    runtime_guard = guard.RuntimeGuard(
        guard.GuardConfig(profile="balanced", global_duplicate_tool_call_threshold=4)
    )
    duplicate = {"cmd": "sed -n '1,120p' README.md"}
    items = [user_message()]
    for index in range(4):
        items.append(call(f"call_dup_{index}", arguments=duplicate))
        items.append(call(f"call_other_{index}", arguments={"cmd": f"printf '%s\\n' other-{index}"}))

    decision = runtime_guard.evaluate_history(items)

    assert decision.action == guard.GuardAction.STOP
    assert decision.loop_type == guard.LoopType.GLOBAL_TOOL_CALL_DUPLICATE
    assert decision.observed == 4


def test_alternating_tool_call_pattern_matches_qwen_cycles():
    guard = load_guard_module()
    runtime_guard = guard.RuntimeGuard(
        guard.GuardConfig(profile="balanced", alternating_tool_call_pattern_cycles=3)
    )
    commands = [
        "git status --short",
        "git diff --stat",
        "git status --short",
        "git diff --stat",
        "git status --short",
        "git diff --stat",
    ]

    decision = runtime_guard.evaluate_history(
        [user_message(), *[call(f"call_{index}", arguments={"cmd": cmd}) for index, cmd in enumerate(commands)]]
    )

    assert decision.action == guard.GuardAction.STOP
    assert decision.loop_type == guard.LoopType.ALTERNATING_TOOL_CALL_PATTERN


def test_read_loop_has_qwen_cold_start_exemption():
    guard = load_guard_module()
    runtime_guard = guard.RuntimeGuard(guard.GuardConfig(profile="balanced"))
    reads = [
        call(f"read_{index}", arguments={"cmd": f"sed -n '{index},{index + 1}p' README.md"})
        for index in range(8)
    ]
    after_progress = [
        call("write_1", arguments={"cmd": "python3 -c \"open('tmp/progress.txt','w').write('x')\""}),
        *reads,
    ]

    cold_start = runtime_guard.evaluate_history([user_message(), *reads])
    active_loop = runtime_guard.evaluate_history([user_message(), *after_progress])

    assert cold_start.action == guard.GuardAction.ALLOW
    assert active_loop.action == guard.GuardAction.STOP
    assert active_loop.loop_type == guard.LoopType.READ_FILE_LOOP


def test_proposed_duplicate_read_gets_one_recovery_then_stops():
    guard = load_guard_module()
    runtime_guard = guard.RuntimeGuard(guard.GuardConfig(profile="max_safety"))
    read_args = {"cmd": "head -40 README.md"}
    history = [
        user_message("Read the README."),
        call("call_1", arguments=read_args),
        output("call_1", "# README\n"),
    ]

    first = runtime_guard.evaluate_proposed_call(history, call("call_2", arguments=read_args))
    second = runtime_guard.evaluate_proposed_call(
        [
            *history,
            call("call_duplicate_recovery_1", arguments={"cmd": "printf DUPLICATE_READ_ALREADY_DONE"}),
            output("call_duplicate_recovery_1", "DUPLICATE_READ_ALREADY_DONE\n"),
        ],
        call("call_3", arguments=read_args),
    )

    assert first.action == guard.GuardAction.RECOVER
    assert first.loop_type == guard.LoopType.DUPLICATE_READ_COMMAND
    assert second.action == guard.GuardAction.STOP
    assert second.loop_type == guard.LoopType.STALE_RECOVERY_LOOP


def test_new_user_request_resets_duplicate_read_recovery_marker():
    guard = load_guard_module()
    runtime_guard = guard.RuntimeGuard(guard.GuardConfig(profile="max_safety"))
    read_args = {"cmd": "head -40 README.md"}
    history = [
        user_message("Old request."),
        call("old_read", arguments=read_args),
        output("old_read", "# README\n"),
        output("old_recovery", "DUPLICATE_READ_ALREADY_DONE\n"),
        user_message("New request."),
    ]

    decision = runtime_guard.evaluate_proposed_call(
        history,
        call("new_read", arguments=read_args),
    )

    assert decision.action == guard.GuardAction.ALLOW


def test_proposed_duplicate_terminal_upsert_gets_finalize_recovery():
    guard = load_guard_module()
    runtime_guard = guard.RuntimeGuard(guard.GuardConfig(profile="balanced"))
    cmd = (
        f"python3 {ROOT}/scripts/local_harness_document_section_upsert.py "
        "--dir . --file docs/example.md "
        "--section-title 'Compatibility Notes' --body-b64 Zm9v "
        "--item-number 3 --total-items 3 --done-marker ITEM_3_DONE "
        "--already-marker ITEM_3_ALREADY_PRESENT"
    )
    history = [
        user_message("Work through each of the three items."),
        call("call_item_3", arguments={"cmd": cmd}),
        output(
            "call_item_3",
            (
                "ITEM_3_ALREADY_PRESENT docs/example.md "
                "bytes=13444 action=already_present next_item=None\n"
            ),
        ),
    ]

    decision = runtime_guard.evaluate_proposed_call(
        history,
        call("call_repeat_item_3", arguments={"cmd": cmd}),
    )

    assert decision.action == guard.GuardAction.RECOVER
    assert decision.loop_type == guard.LoopType.DUPLICATE_COMPLETED_COMMAND


def test_new_user_request_resets_guard_history():
    guard = load_guard_module()
    runtime_guard = guard.RuntimeGuard(guard.GuardConfig(profile="balanced"))
    repeated = {"cmd": "git status --short"}

    decision = runtime_guard.evaluate_history(
        [
            user_message("Old request."),
            *[call(f"old_{index}", arguments=repeated) for index in range(5)],
            user_message("New request."),
            call("new_1", arguments=repeated),
        ]
    )

    assert decision.action == guard.GuardAction.ALLOW
