import json
import os
import shlex
import shutil
import sqlite3
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
QDEX = ROOT / "scripts" / "qdex"
QWENDEX = ROOT / "scripts" / "qwendex"


def process_start_ticks(pid: int) -> str:
    stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    closing = stat.rfind(")")
    fields = stat[closing + 2 :].split()
    return fields[19]


def cd_selector_count(command: list[str]) -> int:
    return sum(
        item in {"-C", "--cd"} or item.startswith("--cd=")
        for item in command
    )


class QdexManagerAttachmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="qwendex-manager-attachment-")
        self.temp = Path(self.tempdir.name)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def run_qwendex(
        self,
        *args: str,
        env: dict[str, str],
        check: bool = True,
    ) -> tuple[subprocess.CompletedProcess[str], dict[str, object]]:
        result = subprocess.run(
            [str(QWENDEX), *args],
            cwd=ROOT,
            env={**os.environ, **env},
            text=True,
            capture_output=True,
            check=False,
            timeout=60,
        )
        payload = json.loads(result.stdout)
        if check:
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        return result, payload

    def make_argv_fixture(self) -> tuple[Path, Path, dict[str, str]]:
        dev_root = self.temp / "argv-dev"
        work_root = dev_root / ".qwendex-dev"
        scripts = dev_root / "scripts"
        meta = work_root / "results" / "meta"
        codex_home = work_root / "codex_home"
        runtime = work_root / "bin" / "fake-codex"
        repo = self.temp / "argv-repo"
        for path in (scripts, meta, codex_home, runtime.parent, repo):
            path.mkdir(parents=True, exist_ok=True)
        runtime.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        runtime.chmod(0o755)
        fake_qwendex = scripts / "qwendex"
        fake_qwendex.write_text(
            textwrap.dedent(
                """\
                #!/usr/bin/env python3
                import json
                import sys
                from pathlib import Path

                args = sys.argv[1:]
                if args[:1] != ["codex-status"]:
                    raise SystemExit(2)
                target = Path(args[args.index("--write") + 1])
                payload = {
                    "status": "pass",
                    "data": {
                        "agent_use": "Auto",
                        "manager_preflight_required": False,
                    },
                }
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(json.dumps(payload), encoding="utf-8")
                print(json.dumps(payload))
                """
            ),
            encoding="utf-8",
        )
        fake_qwendex.chmod(0o755)
        env_file = work_root / "env.sh"
        exports = {
            "QWENDEX_DEV_ROOT": str(dev_root),
            "QWENDEX_CODEX_HOME": str(codex_home),
            "QWENDEX_CODEX_RUNTIME": str(runtime),
            "QWENDEX_CODEX_STATUS_FILE": str(work_root / "codex_status.json"),
            "QWENDEX_META_ROOT": str(meta),
        }
        env_file.write_text(
            "".join(f"export {key}={shlex.quote(value)}\n" for key, value in exports.items()),
            encoding="utf-8",
        )
        return dev_root, repo, {
            **os.environ,
            "QWENDEX_DEV_ROOT": str(dev_root),
            "QWENDEX_QDEX_DRY_RUN": "1",
        }

    def qdex_dry_run(
        self,
        *args: str,
        cwd: Path,
        env: dict[str, str],
    ) -> dict[str, object]:
        result = subprocess.run(
            [str(QDEX), *args, "--json"],
            cwd=cwd,
            env=env,
            text=True,
            capture_output=True,
            check=False,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        return json.loads(result.stdout)

    def manager_env(self, name: str) -> tuple[Path, dict[str, str]]:
        root = self.temp / name
        repo = root / "repo"
        codex_home = root / "codex_home"
        repo.mkdir(parents=True)
        codex_home.mkdir(parents=True)
        env = {
            "QWENDEX_STATE_DB": str(root / "qwendex.sqlite"),
            "QWENDEX_LEDGER_DB": str(root / "qwendex_ledger.sqlite"),
            "QWENDEX_RESULTS_ROOT": str(root / "results"),
            "QWENDEX_CODEX_STATUS_FILE": str(root / "codex_status.json"),
            "CODEX_HOME": str(codex_home),
            "QWENDEX_MANAGER_ALLOW_UNHOOKED": "1",
            "QWENDEX_MANAGER_TARGET_REPO": str(repo),
            "QWENDEX_AGENT_USE": "Manager",
            "QWENDEX_MANAGER_LAUNCH_PID": str(os.getpid()),
            "QWENDEX_MANAGER_LAUNCH_START_TICKS": process_start_ticks(os.getpid()),
            "QWENDEX_MANAGER_LAUNCH_NONCE": f"test-launch-{name}",
        }
        return repo, env

    def preflight(self, env: dict[str, str]) -> dict[str, object]:
        _, payload = self.run_qwendex(
            "manager",
            "preflight",
            "--mode",
            "manager",
            "--interactive-prompt-unknown",
            "--json",
            env=env,
        )
        return payload

    def hook(
        self,
        event: str,
        event_payload: dict[str, object],
        *,
        env: dict[str, str],
        check: bool = True,
    ) -> tuple[subprocess.CompletedProcess[str], dict[str, object]]:
        return self.run_qwendex(
            "agent",
            "hook",
            event,
            "--event-json",
            json.dumps(event_payload),
            "--json",
            env=env,
            check=check,
        )

    def duplicate_decision(self, state_db: Path, ledger_id: str, duplicate_id: str) -> None:
        with sqlite3.connect(state_db) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM qwendex_manager_decisions WHERE ledger_id = ?",
                (ledger_id,),
            ).fetchone()
            self.assertIsNotNone(row)
            values = dict(row)
            values["ledger_id"] = duplicate_id
            values["receipt_paths_json"] = "[]"
            columns = list(values)
            placeholders = ", ".join("?" for _ in columns)
            conn.execute(
                f"INSERT INTO qwendex_manager_decisions ({', '.join(columns)}) VALUES ({placeholders})",
                tuple(values[column] for column in columns),
            )

    def test_bare_qdex_preserves_cwd_without_injected_selector(self) -> None:
        _, repo, env = self.make_argv_fixture()
        payload = self.qdex_dry_run(cwd=repo, env=env)

        self.assertEqual(payload["target_repo"], str(repo))
        self.assertEqual(cd_selector_count(payload["command"]), 0)

    def test_native_qdex_c_preserves_exactly_one_selector_and_argument_order(self) -> None:
        _, repo, env = self.make_argv_fixture()
        payload = self.qdex_dry_run(
            "-C",
            str(repo),
            "--model",
            "test-model",
            "--search",
            cwd=self.temp,
            env=env,
        )

        command = payload["command"]
        self.assertEqual(cd_selector_count(command), 1)
        self.assertEqual(command[-5:], ["-C", str(repo), "--model", "test-model", "--search"])

    def test_legacy_repo_translates_to_exactly_one_native_selector(self) -> None:
        _, repo, env = self.make_argv_fixture()
        payload = self.qdex_dry_run("--repo", str(repo), cwd=self.temp, env=env)

        self.assertEqual(payload["target_repo"], str(repo))
        self.assertEqual(payload["command"][-2:], ["-C", str(repo)])
        self.assertEqual(cd_selector_count(payload["command"]), 1)

    def test_real_qdex_exec_boundary_keeps_attachment_after_in_place_runtime_edit(self) -> None:
        source_root = self.temp / "boundary-source"
        source_scripts = source_root / "scripts"
        dev_root = self.temp / "boundary-dev"
        work_root = dev_root / ".qwendex-dev"
        dev_scripts = dev_root / "scripts"
        meta = work_root / "results" / "meta"
        codex_home = work_root / "codex_home"
        runtime = work_root / "bin" / "fake-codex"
        repo = self.temp / "boundary-repo"
        for path in (source_scripts, dev_scripts, meta, codex_home, runtime.parent, repo):
            path.mkdir(parents=True, exist_ok=True)
        for root, scripts in ((source_root, source_scripts), (dev_root, dev_scripts)):
            shutil.copy2(QWENDEX, scripts / "qwendex")
            shutil.copy2(QDEX, scripts / "qdex")
            shutil.copy2(ROOT / "scripts" / "qwendex_cli.py", scripts / "qwendex_cli.py")
            shutil.copy2(ROOT / "scripts" / "qwendex_performance.py", scripts / "qwendex_performance.py")
            shutil.copytree(ROOT / "config", root / "config")
        state_db = work_root / "state" / "qwendex.sqlite"
        performance_db = work_root / "state" / "qwendex-performance.sqlite"
        ledger_db = work_root / "state" / "qwendex_ledger.sqlite"
        results_root = work_root / "results" / "qwendex"
        status_file = work_root / "codex_status.json"
        state_db.parent.mkdir(parents=True)
        runtime.write_text(
            textwrap.dedent(
                """\
                #!/usr/bin/env python3
                import json
                import os
                import subprocess
                from pathlib import Path

                repo = os.environ["QWENDEX_MANAGER_TARGET_REPO"]
                hooks = json.loads((Path(os.environ["CODEX_HOME"]) / "hooks.json").read_text(encoding="utf-8"))

                def run_hook(name, payload):
                    command = hooks["hooks"][name][0]["hooks"][0]["command"]
                    result = subprocess.run(
                        command,
                        cwd=repo,
                        env=os.environ.copy(),
                        input=json.dumps(payload),
                        text=True,
                        shell=True,
                        capture_output=True,
                        check=False,
                        timeout=30,
                    )
                    return result.returncode, json.loads(result.stdout)

                common = {"session_id": "root-session", "turn_id": "root-turn", "cwd": repo}
                prompt_rc, prompt = run_hook("UserPromptSubmit", {**common, "prompt": "Inspect status."})
                runtime_source = Path(os.environ["QWENDEX_RUNTIME_SOURCE_TO_EDIT"])
                runtime_source.write_text(
                    runtime_source.read_text(encoding="utf-8") + "\\n",
                    encoding="utf-8",
                )
                pre_rc, pre = run_hook(
                    "PreToolUse",
                    {
                        **common,
                        "tool_name": "apply_patch",
                        "tool_use_id": "tool-1",
                        "tool_input": {"path": "note.txt"},
                    },
                )
                post_rc, post = run_hook(
                    "PostToolUse",
                    {
                        **common,
                        "tool_name": "apply_patch",
                        "tool_use_id": "tool-1",
                        "tool_input": {"path": "note.txt"},
                    },
                )
                stop_rc, stop = run_hook(
                    "Stop",
                    {**common, "last_assistant_message": "No edits.", "edit_happened": False},
                )
                required = [
                    "QWENDEX_MANAGER_LEDGER_ID",
                    "QWENDEX_MANAGER_SESSION_ID",
                    "QWENDEX_MANAGER_ROOT_AGENT_ID",
                    "QWENDEX_MANAGER_LAUNCH_KEY",
                    "QWENDEX_MANAGER_LAUNCH_NONCE",
                    "QWENDEX_MANAGER_STATE_DB_IDENTITY",
                    "QWENDEX_MANAGER_RUNTIME_IDENTITY",
                ]
                print(json.dumps({
                    "exports_present": all(os.environ.get(key) for key in required),
                    "performance_run_id": os.environ.get("QWENDEX_RUN_ID", ""),
                    "returncodes": [prompt_rc, pre_rc, post_rc, stop_rc],
                    "prompt_blocked": prompt.get("decision") == "block",
                    "pre_blocked": pre.get("decision") == "block",
                    "stop_blocked": stop.get("decision") == "block",
                }))
                """
            ),
            encoding="utf-8",
        )
        runtime.chmod(0o755)
        exports = {
            "QWENDEX_DEV_ROOT": str(dev_root),
            "QWENDEX_ROOT": str(source_root),
            "QWENDEX_STATE_DB": str(state_db),
            "QWENDEX_PERFORMANCE_DB": str(performance_db),
            "QWENDEX_LEDGER_DB": str(ledger_db),
            "QWENDEX_RESULTS_ROOT": str(results_root),
            "QWENDEX_CODEX_STATUS_FILE": str(status_file),
            "QWENDEX_CODEX_HOME": str(codex_home),
            "QWENDEX_CODEX_RUNTIME": str(runtime),
            "QWENDEX_META_ROOT": str(meta),
        }
        (work_root / "env.sh").write_text(
            "".join(f"export {key}={shlex.quote(value)}\n" for key, value in exports.items()),
            encoding="utf-8",
        )
        env = {
            **os.environ,
            **exports,
            "CODEX_HOME": str(codex_home),
            "QWENDEX_AGENT_USE": "Manager",
            "QWENDEX_PERFORMANCE_CAPTURE": "metadata",
            "QWENDEX_MANAGER_TARGET_REPO": str(repo),
            "QWENDEX_RUNTIME_SOURCE_TO_EDIT": str(source_scripts / "qwendex_cli.py"),
        }
        install_args = [
            str(source_scripts / "qwendex"),
            "agent",
            "hook-config",
            "--install",
            "--codex-home",
            str(codex_home),
            "--json",
        ]
        install = subprocess.run(
            install_args,
            cwd=source_root,
            env=env,
            text=True,
            capture_output=True,
            check=False,
            timeout=60,
        )
        self.assertEqual(install.returncode, 0, install.stderr or install.stdout)
        hooks_path = codex_home / "hooks.json"
        hooks = json.loads(hooks_path.read_text(encoding="utf-8"))
        stale_hooks = json.loads(json.dumps(hooks))
        for entries in stale_hooks["hooks"].values():
            for entry in entries:
                for hook in entry["hooks"]:
                    hook["command"] = hook["command"].replace(
                        str(dev_scripts / "qwendex"),
                        str(source_scripts / "qwendex"),
                    )
        hooks_path.write_text(json.dumps(stale_hooks), encoding="utf-8")
        stale_verify = subprocess.run(
            [
                str(source_scripts / "qwendex"),
                "agent",
                "hook-config",
                "--verify",
                "--codex-home",
                str(codex_home),
                "--json",
            ],
            cwd=source_root,
            env=env,
            text=True,
            capture_output=True,
            check=False,
            timeout=60,
        )
        self.assertNotEqual(stale_verify.returncode, 0)
        stale_status = json.loads(stale_verify.stdout)["data"]["hook_status"]
        self.assertEqual(
            set(stale_status["runtime_command_mismatch_events"]),
            set(hooks["hooks"]),
        )
        install = subprocess.run(
            install_args,
            cwd=source_root,
            env=env,
            text=True,
            capture_output=True,
            check=False,
            timeout=60,
        )
        self.assertEqual(install.returncode, 0, install.stderr or install.stdout)
        hooks = json.loads(hooks_path.read_text(encoding="utf-8"))
        pre_tool_command = hooks["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        self.assertIn(str(dev_scripts / "qwendex"), pre_tool_command)
        self.assertNotIn(str(source_scripts / "qwendex"), pre_tool_command)

        result = subprocess.run(
            [str(source_scripts / "qdex")],
            cwd=repo,
            env=env,
            text=True,
            capture_output=True,
            check=False,
            timeout=60,
        )

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        boundary = json.loads(result.stdout)
        self.assertTrue(boundary["exports_present"])
        self.assertRegex(str(boundary["performance_run_id"]), r"^[0-9a-f]{32}$")
        self.assertEqual(boundary["returncodes"], [0, 0, 0, 0])
        self.assertFalse(boundary["prompt_blocked"])
        self.assertFalse(boundary["pre_blocked"])
        self.assertFalse(boundary["stop_blocked"])
        performance_summary = subprocess.run(
            [str(dev_scripts / "qwendex"), "performance", "summary", "--json"],
            cwd=repo,
            env=env,
            text=True,
            capture_output=True,
            check=False,
            timeout=60,
        )
        self.assertEqual(
            performance_summary.returncode,
            0,
            performance_summary.stderr or performance_summary.stdout,
        )
        aggregate = json.loads(performance_summary.stdout)["data"]["summary"]
        self.assertEqual(aggregate["runs_observed"], 1)
        self.assertEqual(aggregate["tool_calls_by_family"], {"edit": 1})
        self.assertEqual(aggregate["telemetry_coverage"]["rate"], 1.0)
        with sqlite3.connect(state_db) as conn:
            row = conn.execute(
                "SELECT root_session_id, turn_id, final_status, stop_status FROM qwendex_manager_decisions"
            ).fetchone()
        self.assertEqual(row, ("root-session", "root-turn", "closed", "STOP_MANAGER_CLOSED"))

    def test_preflight_and_first_event_turn_admission_are_idempotent_across_mode_toggle(self) -> None:
        repo, env = self.manager_env("idempotent")
        first = self.preflight(env)
        second = self.preflight(env)
        self.assertEqual(first["data"]["ledger_id"], second["data"]["ledger_id"])
        self.assertTrue(second["data"]["idempotent_reuse"])
        manager_env = {**env, **first["data"]["exports"]}
        toggled_env = {key: value for key, value in manager_env.items() if key != "QWENDEX_AGENT_USE"}
        self.run_qwendex("manager", "mode", "--set", "auto", "--json", env=toggled_env)
        event = {
            "session_id": "goal-session",
            "turn_id": "goal-turn",
            "cwd": str(repo),
            "tool_name": "spawn_agent",
            "tool_use_id": "spawn-1",
            "tool_input": {"agent_type": "explorer"},
        }

        first_hook, _ = self.hook("PreToolUse", event, env=toggled_env)
        second_hook, _ = self.hook("PreToolUse", event, env=toggled_env)
        prompt_hook, _ = self.hook(
            "UserPromptSubmit",
            {
                "session_id": "goal-session",
                "turn_id": "goal-turn",
                "cwd": str(repo),
                "prompt": "Inspect one repository fact.",
            },
            env=toggled_env,
        )

        self.assertEqual(first_hook.returncode, 0)
        self.assertEqual(second_hook.returncode, 0)
        self.assertEqual(prompt_hook.returncode, 0)
        with sqlite3.connect(env["QWENDEX_STATE_DB"]) as conn:
            rows = conn.execute(
                "SELECT root_session_id, turn_id, prompt_known, policy_hash FROM qwendex_manager_decisions"
            ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][:3], ("goal-session", "goal-turn", 1))
        self.assertEqual(rows[0][3], first["data"]["policy_hash"])

    def test_ambiguous_decisions_block_subagent_admission_without_selection(self) -> None:
        repo, env = self.manager_env("ambiguous-spawn")
        preflight = self.preflight(env)
        manager_env = {**env, **preflight["data"]["exports"]}
        event = {
            "session_id": "root-session",
            "turn_id": "root-turn",
            "cwd": str(repo),
            "tool_name": "spawn_agent",
            "tool_use_id": "spawn-1",
            "tool_input": {"agent_type": "explorer"},
        }
        self.hook("PreToolUse", event, env=manager_env)
        self.duplicate_decision(
            Path(env["QWENDEX_STATE_DB"]),
            preflight["data"]["ledger_id"],
            f"{preflight['data']['ledger_id']}.duplicate",
        )

        result, payload = self.hook("PreToolUse", event, env=manager_env, check=False)

        self.assertNotEqual(result.returncode, 0)
        hook_result = payload["data"]["hook_result"]
        self.assertEqual(hook_result["event"], "manager.subagent_admission_rejected")
        self.assertEqual(hook_result["reason_code"], "decision_ambiguous")
        self.assertEqual(hook_result["manager_resolution"]["candidate_count"], 2)

    def test_ambiguous_stop_is_non_blocking_and_non_mutating(self) -> None:
        repo, env = self.manager_env("ambiguous-stop")
        preflight = self.preflight(env)
        manager_env = {**env, **preflight["data"]["exports"]}
        prompt = {
            "session_id": "root-session",
            "turn_id": "root-turn",
            "cwd": str(repo),
            "prompt": "Inspect status.",
        }
        self.hook("UserPromptSubmit", prompt, env=manager_env)
        duplicate_id = f"{preflight['data']['ledger_id']}.duplicate"
        self.duplicate_decision(
            Path(env["QWENDEX_STATE_DB"]),
            preflight["data"]["ledger_id"],
            duplicate_id,
        )
        with sqlite3.connect(env["QWENDEX_STATE_DB"]) as conn:
            before = dict(conn.execute("SELECT ledger_id, final_status FROM qwendex_manager_decisions"))

        result, payload = self.hook(
            "Stop",
            {
                "session_id": "root-session",
                "turn_id": "root-turn",
                "cwd": str(repo),
                "last_assistant_message": "Done.",
                "edit_happened": False,
            },
            env=manager_env,
        )
        with sqlite3.connect(env["QWENDEX_STATE_DB"]) as conn:
            after = dict(conn.execute("SELECT ledger_id, final_status FROM qwendex_manager_decisions"))

        self.assertEqual(result.returncode, 0)
        self.assertEqual(payload["data"]["hook_result"]["event"], "manager.untrusted_stop_allowed")
        self.assertEqual(payload["data"]["hook_result"]["reason_code"], "decision_ambiguous")
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
