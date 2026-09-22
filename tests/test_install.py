import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install.py"


class InstallerTests(unittest.TestCase):
    maxDiff = None

    def invoke(self, installer, project, home, *args, scope="project", extra_env=None):
        env = os.environ.copy()
        env["CODEX_HOME"] = str(home)
        if extra_env:
            env.update(extra_env)
        command = [sys.executable, str(installer), "--scope", scope]
        if scope == "project":
            command += ["--target-project", str(project)]
        return subprocess.run(command + list(args), text=True, capture_output=True, env=env)

    def current(self, project, home, *args, scope="project", extra_env=None):
        return self.invoke(INSTALLER, project, home, *args, scope=scope, extra_env=extra_env)

    def make_kit(self, parent):
        kit = parent / "kit"
        (kit / "scripts").mkdir(parents=True)
        shutil.copy2(INSTALLER, kit / "scripts" / "install.py")
        shutil.copy2(ROOT / "kit.json", kit / "kit.json")
        shutil.copy2(ROOT / "AGENTS.md", kit / "AGENTS.md")
        shutil.copytree(ROOT / ".codex", kit / ".codex")
        shutil.copytree(ROOT / "agents", kit / "agents")
        skill = kit / ".agents" / "skills" / "stop-slop"
        skill.mkdir(parents=True)
        shutil.copy2(ROOT / ".agents" / "skills" / "stop-slop" / "SKILL.md", skill / "SKILL.md")
        return kit

    @staticmethod
    def directory_link(link, target):
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError:
            if os.name != "nt":
                raise
            result = subprocess.run(
                ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
                text=True,
                capture_output=True,
            )
            if result.returncode != 0:
                raise OSError(result.stderr or result.stdout)

    @staticmethod
    def state_path(project, home):
        digest = hashlib.sha256(str(project.resolve()).encode()).hexdigest()[:16]
        return home / "lugui-agent-pack" / "state" / f"project-{digest}.tsv"

    def test_dry_run_has_no_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); project = root / "project"; project.mkdir(); home = root / "home"
            result = self.current(project, home, "--instructions", "append", "--dry-run")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((project / ".codex").exists())
            self.assertFalse((project / "AGENTS.md").exists())
            self.assertFalse(home.exists())

    def test_catalog_missing_source_role_blocks_apply_without_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); kit = self.make_kit(root); project = root / "project"; project.mkdir(); home = root / "home"
            (kit / ".codex" / "agents" / "worker.toml").unlink()
            result = self.invoke(kit / "scripts" / "install.py", project, home, "--instructions", "skip", "--apply")
            self.assertEqual(result.returncode, 2)
            self.assertFalse((project / ".codex").exists())
            self.assertFalse(home.exists())
            self.assertIn("no corresponding source role definition", result.stdout)
            self.assertIn("preflight made no target changes", result.stderr)

    def test_catalog_model_mismatch_is_rejected_in_dry_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); kit = self.make_kit(root); project = root / "project"; project.mkdir(); home = root / "home"
            catalog_path = kit / "agents" / "catalog.json"
            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
            next(role for role in catalog["roles"] if role["name"] == "worker")["model"] = "gpt-5.5"
            catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
            result = self.invoke(kit / "scripts" / "install.py", project, home, "--instructions", "skip", "--dry-run")
            self.assertEqual(result.returncode, 2)
            self.assertFalse((project / ".codex").exists())
            self.assertIn("does not match catalog", result.stdout)

    def test_catalog_effort_mismatch_is_rejected_without_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); kit = self.make_kit(root); project = root / "project"; project.mkdir(); home = root / "home"
            catalog_path = kit / "agents" / "catalog.json"
            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
            next(role for role in catalog["roles"] if role["name"] == "worker")["effort"] = "high"
            catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
            result = self.invoke(kit / "scripts" / "install.py", project, home, "--instructions", "skip", "--apply")
            self.assertEqual(result.returncode, 2)
            self.assertEqual(list(project.iterdir()), [])
            self.assertFalse(home.exists())
            self.assertIn("does not match catalog", result.stdout)

    def test_catalog_invalid_schema_and_roles_block_apply(self):
        for mutation, expected in (
            (lambda data: data.update(schemaVersion=2), "unsupported agent catalog schema"),
            (lambda data: data["roles"].__setitem__(0, "invalid"), "must be an object"),
            (lambda data: data["roles"].append(dict(data["roles"][0])), "duplicates role name"),
        ):
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); kit = self.make_kit(root); project = root / "project"; project.mkdir(); home = root / "home"
                catalog_path = kit / "agents" / "catalog.json"
                catalog = json.loads(catalog_path.read_text(encoding="utf-8")); mutation(catalog)
                catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
                result = self.invoke(kit / "scripts" / "install.py", project, home, "--instructions", "skip", "--apply")
                self.assertEqual(result.returncode, 2)
                self.assertEqual(list(project.iterdir()), [])
                self.assertFalse(home.exists())
                self.assertIn(expected, result.stdout)

    def test_legacy_manifest_without_catalog_remains_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); kit = self.make_kit(root); project = root / "project"; project.mkdir(); home = root / "home"
            manifest_path = kit / "kit.json"; manifest = json.loads(manifest_path.read_text(encoding="utf-8")); manifest.pop("agentCatalog")
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            result = self.invoke(kit / "scripts" / "install.py", project, home, "--instructions", "skip", "--apply")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((project / ".codex" / "agents" / "worker.toml").is_file())

    def test_manifest_rejects_escaping_sources_and_unsafe_leaf_names(self):
        cases = (
            (("instructions", "source"), "../outside"),
            (("agents", "source"), "../outside"),
            (("projectConfig",), "../outside.toml"),
            (("skill", "source"), str(ROOT / ".agents" / "skills" / "stop-slop")),
            (("skill", "name"), "../outside"),
            (("instructions", "marker"), "bad/name"),
            (("agentCatalog",), "../outside/catalog.json"),
        )
        for index, (field, value) in enumerate(cases):
            with self.subTest(field=".".join(field)), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                kit = self.make_kit(root)
                manifest_path = kit / "kit.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                owner = manifest
                for key in field[:-1]:
                    owner = owner[key]
                owner[field[-1]] = value
                manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
                project = root / f"project-{index}"
                project.mkdir()
                home = root / f"home-{index}"
                result = self.invoke(
                    kit / "scripts" / "install.py",
                    project,
                    home,
                    "--instructions",
                    "append",
                    "--apply",
                )
                self.assertEqual(result.returncode, 1)
                self.assertIn("kit.json", result.stderr)
                self.assertEqual(list(project.iterdir()), [])
                self.assertFalse(home.exists())

    def test_redirected_project_codex_directory_blocks_all_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            outside = root / "outside"
            outside.mkdir()
            self.directory_link(project / ".codex", outside)
            home = root / "home"
            result = self.current(project, home, "--instructions", "append", "--apply")
            self.assertEqual(result.returncode, 2)
            self.assertIn("escapes intended root", result.stdout)
            self.assertFalse((project / "AGENTS.md").exists())
            self.assertEqual(list(outside.iterdir()), [])
            self.assertFalse(home.exists())

    def test_redirected_state_parent_blocks_project_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            home = root / "home"
            home.mkdir()
            outside = root / "outside-state"
            outside.mkdir()
            self.directory_link(home / "lugui-agent-pack", outside)
            result = self.current(project, home, "--instructions", "append", "--apply")
            self.assertEqual(result.returncode, 2)
            self.assertIn("state path escapes intended root", result.stdout)
            self.assertFalse((project / "AGENTS.md").exists())
            self.assertFalse((project / ".codex").exists())
            self.assertEqual(list(outside.iterdir()), [])

    def test_redirected_global_skill_parent_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "home"
            home.mkdir()
            outside = root / "outside-skills"
            outside.mkdir()
            self.directory_link(home / "skills", outside)
            result = self.current(
                root / "unused",
                home,
                "--instructions",
                "skip",
                "--skills",
                "install",
                "--apply",
                scope="global",
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("escapes intended root", result.stdout)
            self.assertEqual(list(outside.iterdir()), [])
            self.assertFalse((home / "agents").exists())

    def test_explicit_symlink_project_root_uses_resolved_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "real-project"
            project.mkdir()
            alias = root / "project-alias"
            self.directory_link(alias, project)
            result = self.current(alias, root / "home", "--instructions", "skip", "--apply")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((project / ".codex" / "agents" / "worker.toml").is_file())

    def test_any_preflight_conflict_blocks_all_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); project = root / "project"; project.mkdir(); home = root / "home"
            agents = project / "AGENTS.md"
            agents.write_text("<!-- BEGIN lugui-agent-pack -->\n", encoding="utf-8")
            before = agents.read_bytes()
            result = self.current(project, home, "--instructions", "append", "--apply")
            self.assertEqual(result.returncode, 2)
            self.assertEqual(before, agents.read_bytes())
            self.assertFalse((project / ".codex").exists())
            self.assertFalse(home.exists())
            self.assertIn("preflight made no target changes", result.stderr)

    def test_unresolved_instruction_choice_blocks_all_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); project = root / "project"; project.mkdir(); home = root / "home"
            instructions = project / "AGENTS.md"
            instructions.write_text("user instructions\n", encoding="utf-8")
            result = self.current(project, home, "--apply")
            self.assertEqual(result.returncode, 2)
            self.assertEqual(instructions.read_text(encoding="utf-8"), "user instructions\n")
            self.assertFalse((project / ".codex").exists())
            self.assertFalse(home.exists())
            self.assertIn("explicit --instructions choice", result.stderr)

    def test_root_keys_are_inserted_before_existing_tables(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); project = root / "project"
            config = project / ".codex" / "config.toml"; config.parent.mkdir(parents=True)
            config.write_text('[mcp_servers.demo]\ncommand = "demo"\n', encoding="utf-8")
            result = self.current(project, root / "home", "--instructions", "skip", "--apply")
            self.assertEqual(result.returncode, 0, result.stderr)
            updated = config.read_text(encoding="utf-8")
            self.assertLess(updated.index("model ="), updated.index("[mcp_servers.demo]"))
            parsed = tomllib.loads(updated)
            self.assertEqual(parsed["model"], "gpt-6-luna")
            self.assertEqual(parsed["mcp_servers"]["demo"]["command"], "demo")

    def test_merge_preserves_user_values_comments_and_unrelated_tables(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); project = root / "project"
            config = project / ".codex" / "config.toml"; config.parent.mkdir(parents=True)
            config.write_text('# keep\nmodel = "custom"\nunrelated = 7\n\n[agents]\nenabled = false # mine\n\n[mcp_servers.x]\ncommand = "x"\n', encoding="utf-8")
            result = self.current(project, root / "home", "--instructions", "skip", "--apply")
            self.assertEqual(result.returncode, 0, result.stderr)
            updated = config.read_text(encoding="utf-8")
            self.assertIn('model = "custom"', updated)
            self.assertIn("enabled = false # mine", updated)
            self.assertEqual(tomllib.loads(updated)["mcp_servers"]["x"]["command"], "x")

    def test_dotted_agent_keys_receive_missing_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); project = root / "project"
            config = project / ".codex" / "config.toml"; config.parent.mkdir(parents=True)
            config.write_text('agents.enabled = false # user\ncustom = 1\n', encoding="utf-8")
            result = self.current(project, root / "home", "--instructions", "skip", "--apply")
            self.assertEqual(result.returncode, 0, result.stderr)
            parsed = tomllib.loads(config.read_text(encoding="utf-8"))
            self.assertFalse(parsed["agents"]["enabled"])
            self.assertEqual(parsed["agents"]["default_subagent_model"], "gpt-6-luna")

    def test_inline_agent_table_conflict_blocks_other_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); project = root / "project"; home = root / "home"
            config = project / ".codex" / "config.toml"; config.parent.mkdir(parents=True)
            config.write_text("agents = { enabled = false }\n", encoding="utf-8"); before = config.read_bytes()
            result = self.current(project, home, "--instructions", "append", "--apply")
            self.assertEqual(result.returncode, 2)
            self.assertEqual(before, config.read_bytes())
            self.assertFalse((project / "AGENTS.md").exists())
            self.assertFalse((project / ".codex" / "agents").exists())
            self.assertFalse(home.exists())
            self.assertIn("inline table agents", result.stdout)

    def test_per_key_ownership_updates_kit_value_despite_unrelated_edit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); kit = self.make_kit(root); project = root / "project"; project.mkdir(); home = root / "home"
            installer = kit / "scripts" / "install.py"
            first = self.invoke(installer, project, home, "--instructions", "skip", "--apply")
            self.assertEqual(first.returncode, 0, first.stderr)
            target = project / ".codex" / "config.toml"
            target_text = target.read_text(encoding="utf-8")
            target.write_text(target_text.replace("[agents]", "unrelated = 9\n\n[agents]"), encoding="utf-8")
            source = kit / ".codex" / "config.toml"
            source.write_text(source.read_text(encoding="utf-8").replace('model = "gpt-6-luna"', 'model = "next-luna"'), encoding="utf-8")
            second = self.invoke(installer, project, home, "--instructions", "skip", "--update", "--apply")
            self.assertEqual(second.returncode, 0, second.stderr)
            parsed = tomllib.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(parsed["model"], "next-luna")
            self.assertEqual(parsed["unrelated"], 9)
            state = self.state_path(project, home).read_text(encoding="utf-8")
            self.assertIn("state v2", state); self.assertIn("\tconfig-key\t", state)

    def test_user_drift_blocks_other_planned_updates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); kit = self.make_kit(root); project = root / "project"; project.mkdir(); home = root / "home"
            installer = kit / "scripts" / "install.py"
            first = self.invoke(installer, project, home, "--instructions", "skip", "--apply")
            self.assertEqual(first.returncode, 0, first.stderr)
            config = project / ".codex" / "config.toml"
            config.write_text(config.read_text(encoding="utf-8").replace('model = "gpt-6-luna"', 'model = "mine"'), encoding="utf-8")
            source_agent = kit / ".codex" / "agents" / "worker.toml"
            target_agent = project / ".codex" / "agents" / "worker.toml"; target_before = target_agent.read_bytes()
            source_agent.write_text(source_agent.read_text(encoding="utf-8") + "\n# next kit\n", encoding="utf-8")
            result = self.invoke(installer, project, home, "--instructions", "skip", "--update", "--apply")
            self.assertEqual(result.returncode, 2)
            self.assertEqual(target_before, target_agent.read_bytes())
            self.assertIn("managed setting model was modified", result.stdout)

    def test_v1_state_upgrades_matching_config_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); project = root / "project"; home = root / "home"
            config = project / ".codex" / "config.toml"; config.parent.mkdir(parents=True)
            payload = (ROOT / ".codex" / "config.toml").read_bytes(); config.write_bytes(payload)
            state = self.state_path(project, home); state.parent.mkdir(parents=True)
            state.write_text("# lugui-agent-pack state v1\nmanaged\tconfig\t%s\t%s\tmerged-config\tC:\\old\\kit\\.codex\\config.toml\ttrue\n" % (config, hashlib.sha256(payload).hexdigest()), encoding="utf-8")
            result = self.current(project, home, "--instructions", "skip", "--apply")
            self.assertEqual(result.returncode, 0, result.stderr)
            upgraded = state.read_text(encoding="utf-8")
            self.assertIn("state v2", upgraded); self.assertIn("\tconfig-key\t", upgraded)
            self.assertIn("upgrading compatible v1", result.stdout)

    def test_instruction_skip_does_not_update_managed_block(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); kit = self.make_kit(root); project = root / "project"; project.mkdir(); home = root / "home"
            installer = kit / "scripts" / "install.py"
            first = self.invoke(installer, project, home, "--instructions", "append", "--apply")
            self.assertEqual(first.returncode, 0, first.stderr)
            target = project / "AGENTS.md"; before = target.read_bytes()
            source = kit / "AGENTS.md"; source.write_text(source.read_text(encoding="utf-8") + "\nNew instruction.\n", encoding="utf-8")
            second = self.invoke(installer, project, home, "--instructions", "skip", "--update", "--apply")
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(before, target.read_bytes())

    def test_agent_bytes_are_idempotent_with_crlf_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); kit = self.make_kit(root); project = root / "project"; project.mkdir(); home = root / "home"
            source = kit / ".codex" / "agents" / "worker.toml"; source.write_bytes(source.read_bytes().replace(b"\n", b"\r\n"))
            installer = kit / "scripts" / "install.py"
            first = self.invoke(installer, project, home, "--instructions", "skip", "--apply")
            second = self.invoke(installer, project, home, "--instructions", "skip", "--update", "--apply")
            self.assertEqual(first.returncode, 0, first.stderr); self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(source.read_bytes(), (project / ".codex" / "agents" / "worker.toml").read_bytes())
            self.assertIn("already matches the kit", second.stdout)

    def test_global_skill_link_repeat_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); home = root / "home"; args = ("--instructions", "skip", "--skills", "install", "--apply")
            first = self.current(root / "unused", home, *args, scope="global")
            self.assertEqual(first.returncode, 0, first.stderr)
            second = self.current(root / "unused", home, *args, scope="global")
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertIn("already points to the kit skill", second.stdout)
            self.assertTrue((home / "skills" / "stop-slop" / "SKILL.md").is_file())

    def test_project_submodule_repeat_is_idempotent(self):
        if shutil.which("git") is None: self.skipTest("git unavailable")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); kit = self.make_kit(root); upstream = root / "upstream"; upstream.mkdir()
            (upstream / "SKILL.md").write_text("# Test skill\n", encoding="utf-8")
            subprocess.run(["git", "init", str(upstream)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(upstream), "add", "SKILL.md"], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(upstream), "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "initial"], check=True, capture_output=True)
            manifest_path = kit / "kit.json"; manifest = json.loads(manifest_path.read_text(encoding="utf-8")); manifest["skill"]["upstream"] = str(upstream); manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            project = root / "project"; subprocess.run(["git", "init", str(project)], check=True, capture_output=True)
            home = root / "home"; installer = kit / "scripts" / "install.py"; args = ("--instructions", "skip", "--skills", "install", "--apply"); env = {"GIT_ALLOW_PROTOCOL": "file"}
            first = self.invoke(installer, project, home, *args, extra_env=env)
            self.assertEqual(first.returncode, 0, first.stderr)
            second = self.invoke(installer, project, home, *args, extra_env=env)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertIn("expected upstream", second.stdout)
            shutil.rmtree(project / ".agents" / "skills" / "stop-slop")
            third = self.invoke(installer, project, home, *args, extra_env=env)
            self.assertEqual(third.returncode, 0, third.stderr)
            self.assertIn("initialize the already configured submodule", third.stdout)
            self.assertTrue((project / ".agents" / "skills" / "stop-slop" / "SKILL.md").is_file())

    def test_partial_commit_failure_saves_prior_ownership(self):
        if shutil.which("git") is None: self.skipTest("git unavailable")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); kit = self.make_kit(root)
            manifest_path = kit / "kit.json"; manifest = json.loads(manifest_path.read_text(encoding="utf-8")); manifest["skill"]["upstream"] = str(root / "missing-upstream"); manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            project = root / "project"; subprocess.run(["git", "init", str(project)], check=True, capture_output=True); home = root / "home"
            result = self.invoke(kit / "scripts" / "install.py", project, home, "--instructions", "skip", "--skills", "install", "--apply", extra_env={"GIT_ALLOW_PROTOCOL": "file"})
            self.assertEqual(result.returncode, 2)
            state = self.state_path(project, home).read_text(encoding="utf-8")
            self.assertIn("\tagent\t", state); self.assertTrue((project / ".codex" / "agents" / "worker.toml").is_file())
            self.assertIn("ownership state was saved", result.stderr)

    def test_moved_v1_source_path_is_not_stale(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); project = root / "project"; project.mkdir(); home = root / "home"
            state = self.state_path(project, home); state.parent.mkdir(parents=True)
            state.write_text("# lugui-agent-pack state v1\nmanaged\tagent\t%s\tdeadbeef\tfile\tC:\\old-checkout\\.codex\\agents\\worker.toml\ttrue\n" % (project / ".codex/agents/worker.toml"), encoding="utf-8")
            result = self.current(project, home, "--instructions", "skip", "--update", "--dry-run")
            self.assertEqual(result.returncode, 0, result.stderr); self.assertNotIn("[STALE]", result.stdout)

    def test_duplicate_role_and_layered_instruction_notices(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); project = root / "project"; agents = project / ".codex" / "agents"; agents.mkdir(parents=True)
            (agents / "custom-worker.toml").write_text('name = "worker"\n', encoding="utf-8")
            (project / "AGENTS.md").write_text("local\n", encoding="utf-8")
            home = root / "home"; home.mkdir(); (home / "AGENTS.md").write_text("global\n", encoding="utf-8")
            result = self.current(project, home, "--instructions", "append", "--dry-run")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("duplicates kit role", result.stdout); self.assertIn("will layer with global instructions", result.stdout)


if __name__ == "__main__":
    unittest.main()
