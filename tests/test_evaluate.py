import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).parents[1] / "scripts" / "evaluate.py"
SPEC = importlib.util.spec_from_file_location("evaluate", SCRIPT)
evaluate = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = evaluate
SPEC.loader.exec_module(evaluate)


class EvaluateTests(unittest.TestCase):
    @staticmethod
    def reference_normalize(text):
        replacements = {"ab": "X", "aba": "Y", "b": "Z"}
        result = []
        index = 0
        keys = sorted(replacements, key=len, reverse=True)
        while index < len(text):
            key = next((candidate for candidate in keys if text.startswith(candidate, index)), None)
            if key is None:
                result.append(text[index])
                index += 1
            else:
                result.append(replacements[key])
                index += len(key)
        return "".join(result)

    def test_fixture_definitions_have_artifact_checks(self):
        cases = evaluate.read_toml(evaluate.ROOT / "evals" / "cases.toml")["cases"]
        self.assertTrue(all(case["checks"] for case in cases.values()))

    def test_scenario_fixture_must_stay_inside_fixture_directory(self):
        for unsafe in ("evals/baseline", "evals/fixtures/../baseline"):
            manifest = {"version": 2, "scenarios": {"unsafe": {"fixture_dir": unsafe}}}
            with patch.object(evaluate, "read_toml", return_value=manifest):
                with self.assertRaises(ValueError):
                    evaluate.load_scenario("unsafe")
        scenario, fixture = evaluate.load_scenario("parallel_modules")
        self.assertEqual(evaluate.ROOT / "evals" / "fixtures" / "parallel_modules", fixture)

    def test_checks_validate_file_content(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            (workspace / "answer.txt").write_text("yes\n", encoding="utf-8")
            checks = evaluate.evaluate_checks(workspace, [{"path": "answer.txt", "equals": "yes\n"},
                                                          {"path": "answer.txt", "contains": "yes"},
                                                          {"path": "missing.txt", "contains": "no"}])
        self.assertEqual([True, True, False], [item["passed"] for item in checks])

    def test_case_materialization_preserves_utf8_lf_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            evaluate.materialize_case("simple_luna", {"files": {"input.txt": "café\n"}}, workspace)
            data = (workspace / "input.txt").read_bytes()
        self.assertEqual("café\n".encode("utf-8"), data)
        self.assertNotIn(b"\r\n", data)

    def test_case_insensitive_report_check_and_failed_equality_are_auditable(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            (workspace / "report.md").write_text("Check: passed\n", encoding="utf-8")
            checks = evaluate.evaluate_checks(workspace, [{"path": "report.md", "contains_ci": "check"},
                                                          {"path": "report.md", "equals": "different\n"}])
        self.assertTrue(checks[0]["passed"])
        self.assertEqual("different\n", checks[1]["expected"])
        self.assertEqual("Check: passed\n", checks[1]["actual"])

    def test_python_check_executes_fixture_behavior(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            (workspace / "logic.py").write_text("def answer(): return 42\n", encoding="utf-8")
            checks = evaluate.evaluate_checks(workspace, [{"python": "from logic import answer; assert answer() == 42"}])
        self.assertTrue(checks[0]["passed"])

    def test_functional_check_failures_are_recorded(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(evaluate.subprocess, "run", side_effect=subprocess.TimeoutExpired("python", 15)):
            check = evaluate.evaluate_checks(Path(temp), [{"python": "while True: pass"}])[0]
        self.assertEqual("timeout", check["failure"])
        self.assertFalse(check["passed"])

        with tempfile.TemporaryDirectory() as temp, patch.object(evaluate.subprocess, "run", side_effect=UnicodeDecodeError("utf-8", b"\\xff", 0, 1, "invalid")):
            check = evaluate.evaluate_checks(Path(temp), [{"python": "print('x')"}])[0]
        self.assertEqual("decode_error", check["failure"])
        self.assertFalse(check["passed"])

    def test_normalize_fixture_uses_independent_reference_oracle(self):
        cases = evaluate.read_toml(evaluate.ROOT / "evals" / "cases.toml")["cases"]
        oracle = next(check["python"] for check in cases["deceptive_difficult"]["checks"] if "python" in check)
        self.assertEqual("YZa", self.reference_normalize("ababa"))
        self.assertEqual("Za", self.reference_normalize("ba"))
        self.assertIn(repr(self.reference_normalize("ababa")), oracle)
        self.assertIn(repr(self.reference_normalize("ba")), oracle)

    def test_simulated_cases_are_not_labeled_runtime_evidence(self):
        cases = evaluate.read_toml(evaluate.ROOT / "evals" / "cases.toml")["cases"]
        self.assertIn("Simulated instruction test", cases["independent_parallel"]["description"])
        self.assertIn("Simulated instruction test", cases["unavailable_model_fallback"]["description"])

    def test_jsonl_and_tokens_are_conservative(self):
        events, malformed = evaluate.parse_jsonl('{"type":"done","usage":{"total_tokens":12}}\nnot-json\n')
        self.assertEqual(1, len(events))
        self.assertEqual(1, malformed)
        self.assertEqual({"main": "UNKNOWN", "children": "UNKNOWN", "total": "UNKNOWN"}, evaluate.observed_tokens(events))
        self.assertEqual({"main": "UNKNOWN", "children": "UNKNOWN", "total": "UNKNOWN"}, evaluate.observed_tokens([]))

    def test_tokens_and_scheduling_use_observed_events_only(self):
        events, _ = evaluate.parse_jsonl(
            '{"type":"thread.started","thread_id":"a"}\n'
            '{"type":"thread.started","thread_id":"b"}\n'
            '{"type":"thread.completed","thread_id":"a","usage":{"input_tokens":10,"cached_input_tokens":4,"cache_write_input_tokens":2,"output_tokens":3,"reasoning_output_tokens":1}}\n'
        )
        self.assertEqual({"main": {"input_tokens": 10, "cached_input_tokens": 4, "cache_write_input_tokens": 2, "output_tokens": 3, "reasoning_output_tokens": 1,
                                   "main_only_total_tokens": 13}, "children": "UNKNOWN", "total": "UNKNOWN"},
                         evaluate.observed_tokens(events))
        self.assertEqual("UNKNOWN", evaluate.scheduling_evidence(events)["concurrent_execution_observed"])

    def test_absent_usage_component_stays_unknown(self):
        events, _ = evaluate.parse_jsonl('{"type":"done","usage":{"input_tokens":10}}\n')
        tokens = evaluate.observed_tokens(events)
        self.assertEqual(10, tokens["main"]["input_tokens"])
        self.assertEqual("UNKNOWN", tokens["main"]["output_tokens"])
        self.assertEqual("UNKNOWN", tokens["main"]["main_only_total_tokens"])

    def test_default_codex_home_is_portable_and_env_wins(self):
        without_codex_home = dict(evaluate.os.environ)
        without_codex_home.pop("CODEX_HOME", None)
        with patch.dict(evaluate.os.environ, without_codex_home, clear=True):
            self.assertEqual(Path.home() / ".codex", evaluate.default_codex_home())
        with patch.dict(evaluate.os.environ, {"CODEX_HOME": "D:/temporary-codex"}, clear=False):
            self.assertEqual(Path("D:/temporary-codex"), evaluate.default_codex_home())

    def test_sanitize_removes_common_credentials(self):
        output = evaluate.sanitize("api_key=secret-value Authorization: Bearer token-value sk-abcdefghijklmnop")
        self.assertNotIn("secret-value", output)
        self.assertNotIn("token-value", output)
        self.assertNotIn("sk-abcdefghijklmnop", output)

    def test_sanitize_removes_machine_specific_paths(self):
        output = evaluate.sanitize(
            r'workspace="C:\\Users\\lugui\\AppData\\Local\\Temp\\codex-eval-case-abc" '
            r'rollout="C:/Users/lugui/.codex/sessions/run.jsonl" '
            r'temp="/tmp/codex-eval-case-abc"'
        )
        self.assertNotIn("lugui", output)
        self.assertNotIn("codex-eval-case-abc", output)
        self.assertEqual(3, output.count("[LOCAL_PATH]"))

    def test_sanitize_value_recurses_through_evidence(self):
        value = {"path": r"C:\\Users\\lugui\\AppData\\Local\\Temp\\fixture", "count": 2}
        sanitized = evaluate.sanitize_value(value)
        self.assertEqual("[LOCAL_PATH]", sanitized["path"])
        self.assertEqual(2, sanitized["count"])

    def test_command_is_isolated_and_variant_override_is_explicit(self):
        command = evaluate.command_for(Path("C:/temp/work"), "do it", {"model": "gpt-6-astra", "effort": "high", "config": ["agents.enabled=false"]})
        self.assertIn("--ignore-user-config", command)
        self.assertNotIn("--ignore-rules", command)
        self.assertIn("--approve-for-me", command)
        self.assertNotIn("--ephemeral", command)
        self.assertNotIn("--sandbox", command)
        self.assertIn("--skip-git-repo-check", command)
        self.assertIn('model_reasoning_effort="high"', command)
        self.assertIn("agents.enabled=false", command)
        trust_override = next(item for item in command if item.startswith("projects="))
        self.assertEqual({"C:\\temp\\work": {"trust_level": "trusted"}}, evaluate.tomllib.loads(trust_override)["projects"])
        self.assertEqual(["-"], command[-1:])

    def test_copy_variant_config_excludes_machine_state(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source"
            workspace = Path(temp) / "workspace"
            (source / ".codex" / "agents").mkdir(parents=True)
            (source / ".codex" / "config.toml").write_text("[agents]\nenabled = true\n", encoding="utf-8")
            (source / ".codex" / "auth.json").write_text("secret", encoding="utf-8")
            (source / ".codex" / "agents" / "worker.toml").write_text("developer_instructions = 'x'", encoding="utf-8")
            evaluate.copy_variant_config(source, workspace)
            self.assertTrue((workspace / ".codex" / "config.toml").is_file())
            self.assertTrue((workspace / ".codex" / "agents" / "worker.toml").is_file())
            self.assertFalse((workspace / ".codex" / "auth.json").exists())

    def test_role_override_only_changes_copied_role_file(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            role = workspace / ".codex" / "agents" / "web_coordinator.toml"
            role.parent.mkdir(parents=True)
            role.write_text('model = "gpt-6-luna"\nmodel_reasoning_effort = "medium"\n', encoding="utf-8")
            evaluate.apply_role_overrides(workspace, {"web_coordinator": {"model_reasoning_effort": "max"}})
            self.assertIn('model_reasoning_effort = "max"', role.read_text(encoding="utf-8"))

    def test_reproducibility_hashes_include_copied_setup(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            (workspace / "AGENTS.md").write_text("rules", encoding="utf-8")
            role = workspace / ".codex" / "agents" / "worker.toml"
            role.parent.mkdir(parents=True)
            (workspace / ".codex" / "config.toml").write_text("model = 'x'", encoding="utf-8")
            role.write_text("name = 'worker'", encoding="utf-8")
            evidence = evaluate.reproducibility_evidence(workspace)
        self.assertIn("case_spec_sha256", evidence)
        self.assertIn("variant_spec_sha256", evidence)
        self.assertIn("runner_sha256", evidence)
        self.assertEqual({"AGENTS.md", ".codex/config.toml", ".codex/agents/worker.toml"}, set(evidence["copied_file_sha256"]))

    def test_primary_thread_id_requires_thread_start_event(self):
        thread_id = "12345678-1234-1234-1234-123456789abc"
        events, _ = evaluate.parse_jsonl(json.dumps({"type": "thread.started", "thread_id": thread_id}) + "\n")
        self.assertEqual(thread_id, evaluate.primary_thread_id(events))
        self.assertIsNone(evaluate.primary_thread_id([{"thread_id": thread_id}]))

    def test_session_activation_reads_exact_rollout_and_filters_private_content(self):
        thread_id = "12345678-1234-1234-1234-123456789abc"
        events, _ = evaluate.parse_jsonl(json.dumps({"type": "thread.started", "thread_id": thread_id}) + "\n")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / "AGENTS.md").write_text("Project rule: retain evidence.", encoding="utf-8")
            rollout = root / ".codex" / "sessions" / "2026" / "01" / f"rollout-x-{thread_id}.jsonl"
            rollout.parent.mkdir(parents=True)
            rollout.write_text("\n".join(json.dumps(item) for item in [
                {"type": "turn_context", "payload": {"model": "gpt-6-luna", "effort": "low", "private": "secret"}},
                {"type": "response_item", "payload": {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "Project rule: retain evidence. private=secret"}]}},
            ]) + "\n", encoding="utf-8")
            evidence = evaluate.session_activation_evidence(events, workspace, root / ".codex", True)
        record = evidence["records"][0]
        self.assertEqual("FOUND", record["status"])
        self.assertEqual("gpt-6-luna", record["turn_context_model"])
        self.assertEqual("low", record["turn_context_effort"])
        self.assertTrue(record["copied_agents_complete_presence"])
        self.assertNotIn("secret", json.dumps(evidence))

    def test_session_activation_uses_pre_run_agents_snapshot(self):
        thread_id = "12345678-1234-1234-1234-123456789abc"
        events, _ = evaluate.parse_jsonl(json.dumps({"type": "thread.started", "thread_id": thread_id}) + "\n")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / "AGENTS.md").write_text("Original instruction", encoding="utf-8")
            snapshot = evaluate.normalize_text((workspace / "AGENTS.md").read_text(encoding="utf-8"))
            (workspace / "AGENTS.md").write_text("Mutated after launch", encoding="utf-8")
            rollout = root / ".codex" / "sessions" / "a" / f"rollout-x-{thread_id}.jsonl"
            rollout.parent.mkdir(parents=True)
            rollout.write_text(json.dumps({"type": "response_item", "payload": {"type": "message", "role": "developer",
                              "content": [{"type": "input_text", "text": "Original instruction"}]}}) + "\n", encoding="utf-8")
            evidence = evaluate.session_activation_evidence(events, workspace, root / ".codex", True, snapshot)
        self.assertTrue(evidence["records"][0]["copied_agents_complete_presence"])

    def test_session_activation_reports_missing_and_ambiguous_logs(self):
        thread_id = "12345678-1234-1234-1234-123456789abc"
        events, _ = evaluate.parse_jsonl(json.dumps({"thread_id": thread_id}) + "\n")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            missing = evaluate.session_activation_evidence(events, workspace, root / ".codex", True)
            for directory in ("a", "b"):
                path = root / ".codex" / "sessions" / directory / f"rollout-x-{thread_id}.jsonl"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("", encoding="utf-8")
            ambiguous = evaluate.session_activation_evidence(events, workspace, root / ".codex", True)
        self.assertEqual("MISSING", missing["records"][0]["status"])
        self.assertEqual("AMBIGUOUS", ambiguous["records"][0]["status"])

    def test_configuration_validity_requires_actual_matching_telemetry(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            (workspace / ".codex").mkdir()
            (workspace / ".codex" / "config.toml").write_text('model = "gpt-6-luna"\nmodel_reasoning_effort = "medium"\n', encoding="utf-8")
            evidence = {"status": "COMPLETE", "records": [{"status": "FOUND", "thread_id": "root", "turn_context_model": "gpt-6-astra",
                        "turn_context_effort": "UNKNOWN", "copied_agents_complete_presence": True}]}
            expected = evaluate.expected_configuration(workspace, {})
            (workspace / ".codex" / "config.toml").write_text('model = "mutated"\n', encoding="utf-8")
            validity = evaluate.configuration_validity(expected, evidence, "root")
            unknown = evaluate.configuration_validity(expected, {"status": "DISABLED"}, "root")
        self.assertFalse(validity["valid"])
        self.assertFalse(validity["main_model_matches_expected"])
        self.assertEqual("UNKNOWN", validity["main_reasoning_effort_matches_expected"])
        self.assertEqual("UNKNOWN", unknown["valid"])

    def test_configuration_validity_selects_primary_not_child(self):
        expected = {"expected_main_model": "gpt-6-luna", "expected_main_reasoning_effort": "medium"}
        evidence = {"status": "COMPLETE", "records": [
            {"status": "FOUND", "thread_id": "root", "turn_context_model": "gpt-6-luna", "turn_context_effort": "medium", "copied_agents_complete_presence": True},
            {"status": "FOUND", "thread_id": "child", "turn_context_model": "gpt-6-astra", "turn_context_effort": "high", "copied_agents_complete_presence": True},
        ]}
        validity = evaluate.configuration_validity(expected, evidence, "root")
        self.assertTrue(validity["valid"])
        self.assertEqual("gpt-6-luna", validity["actual_main_model"])

    def test_scenario_workspace_excludes_oracle_and_external_oracle_rejects_bad_artifact(self):
        scenario, fixture = evaluate.load_scenario("parallel_modules")
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp) / "workspace"
            evaluate.copy_utf8_tree(fixture / "workspace", workspace)
            checks = evaluate.scenario_checks(scenario["checks"], fixture, workspace)
            no_oracle = not (workspace / "oracle.py").exists()
            no_reference = not (workspace / "reference").exists()
        self.assertTrue(no_oracle)
        self.assertTrue(no_reference)
        self.assertFalse(checks[0]["passed"])

    def test_scenario_role_override_is_valid_and_isolated(self):
        scenario, fixture = evaluate.load_scenario("unavailable_role_fallback")
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp) / "workspace"
            workspace.mkdir()
            evaluate.copy_variant_config(evaluate.ROOT, workspace)
            evaluate.apply_copied_role_overrides(workspace, fixture, scenario["copied_role_overrides"])
            copied = workspace / ".codex" / "agents" / "fast_scan.toml"
            with copied.open("rb") as handle:
                evaluate.tomllib.load(handle)
            copied_exists = copied.is_file()
        self.assertTrue(copied_exists)

    def test_external_check_timeout_and_unknown_rubric_are_not_success(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(evaluate.subprocess, "run", side_effect=subprocess.TimeoutExpired("oracle", 1)):
            checks = evaluate.scenario_checks([{"kind": "external_command", "argv": ["{python}", "-c", "pass"], "timeout_seconds": 1}], Path(temp), Path(temp))
        rubric = evaluate.observation_rubric([{"id": "required", "required": True}, {"id": "optional", "required": False}])
        self.assertFalse(checks[0]["passed"])
        self.assertEqual("timeout", checks[0]["failure"])
        self.assertEqual(["PENDING_REVIEW", "UNKNOWN"], [item["status"] for item in rubric])

    def test_fixture_hashes_exclude_ephemeral_python_cache_and_external_stdout_is_retained(self):
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as copied:
            fixture = Path(temp)
            (fixture / "input.txt").write_text("input", encoding="utf-8")
            cache = fixture / "__pycache__"
            cache.mkdir()
            (cache / "oracle.cpython-310.pyc").write_bytes(b"\xff\x00cache")
            (fixture / "loose.pyo").write_bytes(b"\xff\x00cache")
            hashes = evaluate.fixture_hashes(fixture)
            evaluate.copy_utf8_tree(fixture, Path(copied))
            self.assertEqual(["input.txt"], sorted(p.name for p in Path(copied).iterdir()))
            checks = evaluate.scenario_checks([{"kind": "external_command", "argv": ["{python}", "-c", "print('oracle detail')"]}], fixture, fixture)
        self.assertEqual({"input.txt"}, set(hashes))
        self.assertIn("oracle detail", checks[0]["stdout"])

    def test_run_case_retries_and_writes_sanitized_result(self):
        responses = iter([
            {"exit_code": 1, "timed_out": False, "elapsed_seconds": 0.01, "stdout": '{"type":"x"}\n', "stderr": "api_key=secret"},
            {"exit_code": 0, "timed_out": False, "elapsed_seconds": 0.02, "stdout": '{"type":"done"}\n', "stderr": ""},
        ])
        calls = 0
        def fake_process(command, workspace, timeout, stdin_text, codex_home):
            nonlocal calls
            calls += 1
            if calls == 2:
                (workspace / "result.txt").write_text("LUNA STAYS FOCUSED\n", encoding="utf-8")
                (workspace / "EVAL_REPORT.md").write_text("check\n", encoding="utf-8")
            response = next(responses)
            response["stderr"] = evaluate.sanitize(response["stderr"])
            return response
        with tempfile.TemporaryDirectory() as temp, patch.object(evaluate, "run_process", side_effect=fake_process):
            result = evaluate.run_case("simple_luna", "baseline", Path(temp), 1, 1)
            saved = json.loads((Path(temp) / "simple_luna--baseline.json").read_text(encoding="utf-8"))
            raw_exists = (Path(temp) / saved["attempts"][0]["raw_jsonl"]).is_file()
        self.assertTrue(result["accepted"])
        self.assertEqual(2, len(result["attempts"]))
        self.assertIn("[REDACTED]", saved["attempts"][0]["stderr"])
        self.assertTrue(raw_exists)
        self.assertEqual("simple_luna--baseline--attempt-1.jsonl", saved["attempts"][0]["raw_jsonl"])
        self.assertEqual("codex", saved["attempts"][0]["command_argv"][0])
        self.assertIn("--approve-for-me", saved["attempts"][0]["command_argv"])

    def test_successful_artifacts_do_not_accept_failed_cli(self):
        def fake_process(command, workspace, timeout, stdin_text, codex_home):
            (workspace / "result.txt").write_text("LUNA STAYS FOCUSED\n", encoding="utf-8")
            (workspace / "EVAL_REPORT.md").write_text("check\n", encoding="utf-8")
            return {"exit_code": 1, "timed_out": False, "elapsed_seconds": 0.01, "stdout": "", "stderr": "failure"}
        with tempfile.TemporaryDirectory() as temp, patch.object(evaluate, "run_process", side_effect=fake_process):
            result = evaluate.run_case("simple_luna", "baseline", Path(temp), 1, 0)
        self.assertFalse(result["accepted"])
        self.assertFalse(result["attempts"][0]["cli_succeeded"])


if __name__ == "__main__":
    unittest.main()
