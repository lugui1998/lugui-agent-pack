import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("kit_agents", ROOT / "scripts/agents.py")
agents = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agents)


class AgentCatalogTests(unittest.TestCase):
    def test_generated_files_match_catalog(self):
        for relative, expected in agents.render().items():
            self.assertEqual((ROOT / relative).read_text(encoding="utf-8"), expected, relative)

    def test_explicit_efforts_and_least_privilege_roles(self):
        for path, text in agents.render().items():
            data = agents.tomllib.loads(text)
            if "/agents/" not in path:
                self.assertEqual(data["model"], "gpt-6-luna")
                self.assertEqual(data["agents"]["default_subagent_model"], "gpt-6-luna")
                self.assertEqual(data["agents"]["max_depth"], 2)
                continue
            self.assertIn(data["model_reasoning_effort"], agents.EFFORTS)
            if ("worker" not in data["name"] and not data["name"].startswith("test_")
                    and data["name"] not in {"quick_evaluator", "visual_evaluator", "evaluator"}):
                self.assertEqual(data["sandbox_mode"], "read-only")
            if data["name"] != "web_coordinator":
                self.assertIn("Do not spawn agents", data["developer_instructions"])

    def test_terra_roles_preserve_routing_and_explicit_fallbacks(self):
        catalog = agents.load_catalog()
        self.assertEqual(catalog["main"], {"model": "gpt-6-luna", "effort": "medium"})
        self.assertEqual(catalog["defaultSubagent"], {"model": "gpt-6-luna", "effort": "low"})
        roles = {role["name"]: role for role in catalog["roles"]}
        terra_roles = {name for name, role in roles.items() if role["model"] == "gpt-6.1-terra"}
        self.assertEqual(terra_roles, {"standard_worker", "visual_worker", "writing_worker", "visual_evaluator"})
        for name in terra_roles:
            self.assertEqual(roles[name]["effort"], "medium")
            self.assertEqual(roles[name]["sandbox"], "workspace-write")
        self.assertEqual(roles["standard_worker"]["fallbacks"], ["complex_worker"])
        self.assertIn("understood interfaces", roles["standard_worker"]["description"])
        self.assertEqual(roles["visual_worker"]["template"], "visual")
        self.assertEqual(roles["writing_worker"]["template"], "writing")
        preserved_models = {
            "gpt-6-luna": {"fast_scan", "explorer", "worker", "test_runner", "reviewer",
                           "docs_researcher", "web_searcher", "web_coordinator", "quick_evaluator"},
            "gpt-6.2-sol": {"deep_explorer", "complex_worker", "test_diagnostician", "deep_reviewer",
                           "security_reviewer", "planner", "web_verifier", "evaluator"},
            "gpt-6.2-astra": {"expert_worker", "brainstorm", "expert", "advisor"},
        }
        for model, names in preserved_models.items():
            self.assertEqual({name for name, role in roles.items() if role["model"] == model}, names)
        outputs = agents.render()
        for name in ("visual_worker", "writing_worker"):
            self.assertEqual(roles[name]["fallbacks"], [])
            instructions = agents.tomllib.loads(outputs[f".codex/agents/{name}.toml"])["developer_instructions"]
            self.assertIn("Do not spawn agents", instructions)
            self.assertIn("No automatic model fallback is configured", instructions)

    def test_evaluation_levels_share_contract_and_preserve_capabilities(self):
        roles = {r["name"]: r for r in agents.load_catalog()["roles"]}
        outputs = agents.render()
        contract = (ROOT / "agents/prompts/evaluate.md").read_text(encoding="utf-8").strip()
        profiles = {
            "quick_evaluator": ("gpt-6-luna", "medium", "quickevaluate"),
            "visual_evaluator": ("gpt-6.1-terra", "medium", "visualevaluate"),
            "evaluator": ("gpt-6.2-sol", "high", "deepevaluate"),
        }
        for name, (model, effort, template) in profiles.items():
            with self.subTest(role=name):
                self.assertEqual(roles[name]["template"], template)
                self.assertEqual(roles[name]["fallbacks"], [])
                rendered = agents.tomllib.loads(outputs[f".codex/agents/{name}.toml"])
                self.assertEqual(rendered["model"], model)
                self.assertEqual(rendered["model_reasoning_effort"], effort)
                self.assertEqual(rendered["sandbox_mode"], "workspace-write")
                instructions = rendered["developer_instructions"]
                self.assertIn("Do not spawn agents", instructions)
                self.assertEqual(instructions.count(contract), 1)
                specific = (ROOT / f"agents/prompts/{template}.md").read_text(encoding="utf-8").strip()
                self.assertIn(specific, instructions)
                self.assertLess(instructions.index(contract), instructions.index(specific))

    def test_advisor_is_read_only_leaf_without_evaluation_contract(self):
        role = next(r for r in agents.load_catalog()["roles"] if r["name"] == "advisor")
        self.assertEqual(role["template"], "advise")
        self.assertEqual(role["fallbacks"], [])
        rendered = agents.tomllib.loads(agents.render()[".codex/agents/advisor.toml"])
        self.assertEqual(rendered["model"], "gpt-6.2-astra")
        self.assertEqual(rendered["model_reasoning_effort"], "high")
        self.assertEqual(rendered["sandbox_mode"], "read-only")
        instructions = rendered["developer_instructions"]
        self.assertIn("Do not spawn agents", instructions)
        self.assertIn((ROOT / "agents/prompts/advise.md").read_text(encoding="utf-8").strip(), instructions)
        self.assertNotIn((ROOT / "agents/prompts/evaluate.md").read_text(encoding="utf-8").strip(), instructions)

    def test_catalog_rejects_cycle_duplicate_and_invalid_profile(self):
        for mutation in ("cycle", "duplicate", "effort", "traversal", "depth", "boolean_depth"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                shutil.copytree(ROOT / "agents", root / "agents")
                path = root / "agents/catalog.json"
                data = json.loads(path.read_text())
                if mutation == "cycle":
                    data["roles"][1]["fallbacks"] = ["fast_scan"]
                elif mutation == "duplicate":
                    data["roles"].append(data["roles"][0])
                elif mutation == "effort":
                    data["roles"][0]["effort"] = "unsupported"
                elif mutation == "depth":
                    data["maxDepth"] = 0
                elif mutation == "boolean_depth":
                    data["maxDepth"] = True
                else:
                    data["roles"][0]["template"] = "../elsewhere"
                path.write_text(json.dumps(data))
                with self.assertRaises(ValueError):
                    agents.load_catalog(root)

    def test_check_does_not_write_and_reports_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(ROOT / "agents", root / "agents")
            self.assertEqual(agents.main(["--root", str(root), "--check"]), 1)
            self.assertFalse((root / ".codex").exists())
            self.assertEqual(agents.main(["--root", str(root), "--write"]), 0)
            stale = root / ".codex/agents/obsolete.toml"
            stale.write_text('name = "obsolete"\n')
            self.assertEqual(agents.main(["--root", str(root), "--check"]), 1)
            self.assertTrue(stale.exists())


if __name__ == "__main__":
    unittest.main()
