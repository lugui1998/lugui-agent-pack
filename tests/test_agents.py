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
                self.assertEqual(data["model"], "gpt-5.6-luna")
                self.assertEqual(data["agents"]["default_subagent_model"], "gpt-5.6-luna")
                self.assertEqual(data["agents"]["max_depth"], 2)
                continue
            self.assertIn(data["model_reasoning_effort"], agents.EFFORTS)
            if "worker" not in data["name"] and not data["name"].startswith("test_"):
                self.assertEqual(data["sandbox_mode"], "read-only")
            if data["name"] != "web_coordinator":
                self.assertIn("Do not spawn agents", data["developer_instructions"])

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
