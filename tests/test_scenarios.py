import json
import re
import shutil
import subprocess
import sys
import tempfile
try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = (ROOT / "evals" / "fixtures").resolve()
MANIFEST = tomllib.loads((ROOT / "evals" / "scenarios.toml").read_text(encoding="utf-8"))
SCENARIOS = MANIFEST["scenarios"]


class ScenarioFixtureTests(unittest.TestCase):
    def fixture_dir(self, scenario_name):
        fixture = (ROOT / SCENARIOS[scenario_name]["fixture_dir"]).resolve()
        self.assertTrue(fixture.is_relative_to(FIXTURE_ROOT))
        self.assertTrue(fixture.is_dir())
        return fixture

    def materialize(self, scenario_name, solution=None):
        fixture = self.fixture_dir(scenario_name)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        workspace = Path(temp.name) / "workspace"
        shutil.copytree(fixture / "workspace", workspace)
        if solution is not None:
            shutil.copytree(fixture / "solutions" / solution, workspace, dirs_exist_ok=True)
        return fixture, workspace

    def run_oracle(self, scenario_name, workspace):
        fixture = self.fixture_dir(scenario_name)
        check = SCENARIOS[scenario_name]["checks"][0]
        replacements = {
            "{python}": sys.executable,
            "{fixture_dir}": str(fixture),
            "{workspace}": str(workspace),
        }
        argv = []
        for item in check["argv"]:
            expanded = item
            for placeholder, value in replacements.items():
                expanded = expanded.replace(placeholder, value)
            argv.append(expanded)
        return subprocess.run(
            argv,
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=check["timeout_seconds"],
            check=False,
        )

    def assert_control_behavior(self, scenario_name):
        _, initial = self.materialize(scenario_name)
        initial_result = self.run_oracle(scenario_name, initial)
        self.assertNotEqual(0, initial_result.returncode, "initial incomplete fixture unexpectedly passed")

        _, wrong = self.materialize(scenario_name, "known_bad")
        wrong_result = self.run_oracle(scenario_name, wrong)
        self.assertNotEqual(0, wrong_result.returncode, "known wrong solution unexpectedly passed")

        _, reference = self.materialize(scenario_name, "reference")
        reference_result = self.run_oracle(scenario_name, reference)
        self.assertEqual(0, reference_result.returncode, reference_result.stderr)

    def test_manifest_schema_is_explicit(self):
        self.assertEqual(2, MANIFEST["version"])
        self.assertEqual("workspace", MANIFEST["runner_contract"]["workspace_subdir"])
        self.assertEqual(9, len(SCENARIOS))
        self.assertEqual(6, len({item["fixture_dir"] for item in SCENARIOS.values()}))

        rubric_ids = set()
        for name, scenario in SCENARIOS.items():
            for key in ("description", "coordination_mode", "fixture_dir", "prompt", "checks", "observation_rubric"):
                self.assertIn(key, scenario, f"{name} lacks {key}")
            self.assertIn(scenario["coordination_mode"], {"autonomous", "forced_parallel", "forced_coordinator", "forced_specialist", "forced_probe", "forced_scheduling"})
            self.assertEqual(1, len(scenario["checks"]))
            check = scenario["checks"][0]
            self.assertEqual("external_command", check["kind"])
            self.assertEqual(["{python}", "{fixture_dir}/oracle.py", "{workspace}"], check["argv"])
            self.assertGreater(check["timeout_seconds"], 0)
            for observation in scenario["observation_rubric"]:
                self.assertEqual("codex_event_stream", observation["source"])
                self.assertIsInstance(observation["required"], bool)
                key = (name, observation["id"])
                self.assertNotIn(key, rubric_ids)
                rubric_ids.add(key)

    def test_only_workspace_is_model_input(self):
        for fixture_path in {item["fixture_dir"] for item in SCENARIOS.values()}:
            fixture = (ROOT / fixture_path).resolve()
            self.assertTrue((fixture / "workspace").is_dir())
            self.assertTrue((fixture / "oracle.py").is_file())
            self.assertTrue((fixture / "solutions" / "reference").is_dir())
            self.assertTrue((fixture / "solutions" / "known_bad").is_dir())
            self.assertFalse(any(fixture.rglob("run.py")), f"scripted runner remains under {fixture}")
            self.assertFalse((fixture / "workspace" / "solutions").exists())
            self.assertFalse((fixture / "workspace" / "oracle.py").exists())

    def test_parallel_oracle_controls(self):
        self.assert_control_behavior("parallel_modules")
        _, reference = self.materialize("parallel_modules", "reference")
        visible = subprocess.run([sys.executable, "check_integration.py"], cwd=reference,
                                 capture_output=True, text=True, timeout=10)
        self.assertEqual(0, visible.returncode, visible.stderr)
        (reference / "check_integration.py").write_text("print('pretend pass')\n", encoding="utf-8")
        self.assertNotEqual(0, self.run_oracle("parallel_modules", reference).returncode)

    def test_staged_evidence_controls_and_protected_delay(self):
        self.assert_control_behavior("staged_evidence")
        _, reference = self.materialize("staged_evidence", "reference")
        (reference / "archive_lookup.py").write_text("print('bypassed delay')\n", encoding="utf-8")
        self.assertNotEqual(0, self.run_oracle("staged_evidence", reference).returncode)

    def test_conflicting_contracts_controls_and_protection(self):
        self.assert_control_behavior("conflicting_contracts")
        _, initial = self.materialize("conflicting_contracts")
        failed = subprocess.run([sys.executable, "check_migration.py", "."], cwd=initial,
                                capture_output=True, text=True, timeout=10)
        self.assertNotEqual(0, failed.returncode)
        self.assertIn("wrong v2 envelope", failed.stderr)
        _, reference = self.materialize("conflicting_contracts", "reference")
        plan_path = reference / "MIGRATION_PLAN.json"
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        plan["selected_protocol"] = "v2"
        plan["source_scopes"] = [
            {"source": name, "target_identity": "gateway 2.3", "caveat": "draft"}
            for name in ("source_v1.json", "source_v2.json")
        ]
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        self.assertEqual(0, self.run_oracle("conflicting_contracts", reference).returncode)
        (reference / "check_migration.py").write_text("print('pretend pass')\n", encoding="utf-8")
        self.assertNotEqual(0, self.run_oracle("conflicting_contracts", reference).returncode)

    def test_security_oracle_controls(self):
        self.assert_control_behavior("ambiguity_security_handoff")
        _, alternative = self.materialize("ambiguity_security_handoff", "reference_alternative")
        alternative_result = self.run_oracle("ambiguity_security_handoff", alternative)
        self.assertEqual(0, alternative_result.returncode, alternative_result.stderr)
        forced = SCENARIOS["ambiguity_security_handoff"]
        autonomous = SCENARIOS["ambiguity_security_autonomous"]
        self.assertEqual(forced["fixture_dir"], autonomous["fixture_dir"])
        self.assertEqual(forced["checks"], autonomous["checks"])
        self.assertIn("security_reviewer", forced["prompt"])
        self.assertNotIn("security_reviewer", autonomous["prompt"])

    def test_research_oracle_controls_and_pairing(self):
        self.assert_control_behavior("research_synthesis")
        autonomous = SCENARIOS["research_synthesis"]
        forced = SCENARIOS["research_coordinator_comparison"]
        self.assertEqual(autonomous["fixture_dir"], forced["fixture_dir"])
        self.assertEqual(autonomous["checks"], forced["checks"])
        self.assertNotIn("web_coordinator", autonomous["prompt"])
        self.assertIn("web_coordinator", forced["prompt"])

        evidence = self.fixture_dir("research_synthesis") / "workspace" / "evidence"
        packets = [json.loads(path.read_text(encoding="utf-8")) for path in evidence.glob("*.json")]
        self.assertEqual({"clinical_effect", "cost_target"}, {packet["question"] for packet in packets})
        for packet in packets:
            self.assertIn("SYNTHETIC FROZEN EVIDENCE", packet["fixture_label"])
            self.assertRegex(packet["url"], r"^https://.+\.invalid/")
            self.assertRegex(packet["published"], r"^20\d\d-\d\d-\d\d$")

    def test_unavailable_role_override_and_oracle_controls(self):
        self.assert_control_behavior("unavailable_role_fallback")
        scenario = SCENARIOS["unavailable_role_fallback"]
        self.assertEqual(1, len(scenario["copied_role_overrides"]))
        override = scenario["copied_role_overrides"][0]
        fixture = self.fixture_dir("unavailable_role_fallback")
        source = (ROOT / override["source"]).resolve()
        self.assertTrue(source.is_relative_to(fixture / "role_overrides"))
        self.assertEqual(f".codex/agents/{override['role']}.toml", override["destination"])
        contents = source.read_text(encoding="utf-8")
        parsed = tomllib.loads(contents)
        self.assertEqual("gpt-fixture-unavailable-2099", parsed["model"])
        self.assertEqual(1, len(re.findall(r"(?m)^model\s*=", contents)))
        self.assertEqual(1, len(re.findall(r"(?m)^model_reasoning_effort\s*=", contents)))
        self.assertNotIn("availability", parsed)


if __name__ == "__main__":
    unittest.main()
