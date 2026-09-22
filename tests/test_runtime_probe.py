import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


SCRIPT = Path(__file__).parents[1] / "scripts" / "runtime_probe.py"
SPEC = importlib.util.spec_from_file_location("runtime_probe", SCRIPT)
runtime_probe = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = runtime_probe
SPEC.loader.exec_module(runtime_probe)


class RecordingInput(io.StringIO):
    def close(self):
        # Preserve sent requests for assertions while emulating a closed pipe.
        self.was_closed = True


class FakeProcess:
    def __init__(self, responses):
        self.stdin = RecordingInput()
        self.stdout = io.StringIO("".join(json.dumps(item) + "\n" for item in responses))
        self.stderr = io.StringIO("")
        self.returncode = None

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.returncode = 0
        return 0

    def terminate(self):
        self.returncode = -15

    def kill(self):
        self.returncode = -9


class RuntimeProbeTests(unittest.TestCase):
    def config_response(self):
        return {
            "config": {
                "model": "gpt-6-luna",
                "model_reasoning_effort": "medium",
                "sandbox_mode": "workspace-write",
                "approval_policy": "on-request",
                "approvals_reviewer": "user",
                "agents": {
                    "enabled": True,
                    "max_concurrent_threads_per_session": 16,
                    "max_depth": 2,
                    "default_subagent_model": "gpt-6-luna",
                    "default_subagent_reasoning_effort": "low",
                    "private": "must-not-appear",
                },
                "mcp_servers": {"example": {"command": "secret-command", "env": {"API_KEY": "secret"}}},
                "model_provider_settings": {"api_key": "secret"},
            },
            "origins": {
                "model": {"name": {"type": "project", "dotCodexFolder": "C:/work/.codex"}, "version": "v1"},
                "model_reasoning_effort": {"type": "project", "dotCodexFolder": "C:/work/.codex"},
            },
            "layers": [
                {
                    "name": {"type": "project", "dotCodexFolder": "C:/work/.codex"},
                    "version": "v1",
                    "config": {"api_key": "secret", "agents": {"max_depth": 2}},
                }
            ],
        }

    def test_selected_configuration_allowlists_fields_and_provenance(self):
        selected = runtime_probe.selected_configuration(self.config_response())
        serialized = json.dumps(selected)
        self.assertEqual("gpt-6-luna", selected["effective"]["model"])
        self.assertEqual("project", selected["origins"]["model"]["type"])
        self.assertEqual("v1", selected["origins"]["model"]["version"])
        self.assertEqual("project", selected["origins"]["agents"]["max_depth"]["type"])
        self.assertNotIn("must-not-appear", serialized)
        self.assertNotIn("api_key", serialized.lower())
        self.assertNotIn("secret", serialized.lower())

    def test_project_layer_must_be_enabled_for_fixture_trust(self):
        response = self.config_response()
        self.assertTrue(runtime_probe.project_layer_enabled(response, Path("C:/work")))
        response["layers"][0]["disabledReason"] = "not trusted"
        self.assertFalse(runtime_probe.project_layer_enabled(response, Path("C:/work")))

    def test_inline_trust_override_uses_root_table_and_escaped_absolute_key(self):
        cwd = Path("C:/work folder").resolve()
        override = runtime_probe.inline_trust_override(cwd)
        self.assertTrue(override.startswith("projects={"))
        self.assertNotIn(".trust_level", override)
        parsed = runtime_probe.tomllib.loads(override)
        expected = runtime_probe.normalized_trust_key(cwd)
        self.assertEqual("trusted", parsed["projects"][expected]["trust_level"])

    def test_mcp_overrides_disable_names_without_copying_settings(self):
        overrides, count = runtime_probe.mcp_disable_overrides(self.config_response())
        self.assertEqual(1, count)
        self.assertEqual({"mcp_servers": {"example": {"enabled": False}}}, overrides)
        self.assertNotIn("secret-command", json.dumps(overrides))

    def test_configured_roles_only_retains_safe_declarations(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            roles = root / ".codex" / "agents"
            roles.mkdir(parents=True)
            (roles / "worker.toml").write_text(
                'name="worker"\nmodel="gpt-6-luna"\nmodel_reasoning_effort="medium"\n'
                'developer_instructions="private text"\n', encoding="utf-8"
            )
            found = runtime_probe.configured_roles(root, root / "home")
        self.assertEqual("worker", found[0]["role"])
        self.assertEqual("gpt-6-luna", found[0]["model"])
        self.assertNotIn("private text", json.dumps(found))

    def test_materialize_fixture_copies_only_candidate_runtime_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            destination = root / "fixture"
            (source / ".codex" / "agents").mkdir(parents=True)
            (source / "AGENTS.md").write_text("instructions\n", encoding="utf-8")
            (source / ".codex" / "config.toml").write_text('model="gpt-6-luna"\n', encoding="utf-8")
            (source / ".codex" / "agents" / "worker.toml").write_text('model="gpt-6-luna"\n', encoding="utf-8")
            (source / ".codex" / "auth.json").write_text("secret", encoding="utf-8")
            copied = runtime_probe.materialize_fixture(source, destination)
            self.assertEqual(["AGENTS.md", ".codex/config.toml", ".codex/agents/worker.toml"], copied)
            self.assertFalse((destination / ".codex" / "auth.json").exists())

    def test_minimal_isolated_home_has_only_candidate_defaults_and_fixture_trust(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            fixture = root / "fixture"
            (source / ".codex").mkdir(parents=True)
            (source / ".codex" / "config.toml").write_text(
                'model="gpt-6-luna"\nmodel_reasoning_effort="medium"\n'
                '[agents]\nenabled=true\ndefault_subagent_model="gpt-6-luna"\n'
                'default_subagent_reasoning_effort="low"\n', encoding="utf-8"
            )
            config = runtime_probe.minimal_isolated_home_config(source, fixture)
        parsed = runtime_probe.tomllib.loads(config)
        self.assertEqual("gpt-6-luna", parsed["model"])
        self.assertEqual("low", parsed["agents"]["default_subagent_reasoning_effort"])
        self.assertEqual("trusted", next(iter(parsed["projects"].values()))["trust_level"])
        self.assertNotIn("auth", config.lower())

    @patch.object(runtime_probe, "daemon_available", return_value=False)
    @patch.object(runtime_probe.subprocess, "Popen")
    @patch.object(runtime_probe.subprocess, "run")
    def test_runtime_probe_starts_ephemeral_thread_without_turn(self, run, popen, daemon):
        run.return_value = subprocess.CompletedProcess(["codex", "--version"], 0, "codex-cli 0.154.0-alpha.6.2\n", "")
        responses = [
            {"id": 1, "result": {"userAgent": "codex/0.154.0-alpha.6.2"}},
            {"id": 2, "result": self.config_response()},
            {
                "id": 3,
                "result": {
                    "model": "gpt-6-luna",
                    "reasoningEffort": "medium",
                    "modelProvider": "openai",
                    "cwd": "C:/work",
                    "instructionSources": ["C:/work/AGENTS.md"],
                    "sandbox": {"type": "workspaceWrite", "networkAccess": False},
                    "approvalPolicy": "on-request",
                    "approvalsReviewer": "user",
                    "activePermissionProfile": {"id": ":workspace"},
                    "runtimeWorkspaceRoots": ["C:/work"],
                },
            },
        ]
        fake = FakeProcess(responses)
        popen.return_value = fake
        with tempfile.TemporaryDirectory() as temp, patch.object(runtime_probe, "project_layer_enabled", return_value=True):
            report = runtime_probe.inspect_runtime(
                Path(temp), Path(temp) / "home", start_thread=True,
                trust_fixture=True, set_runtime_workspace_root=True,
            )
        sent = [json.loads(line) for line in fake.stdin.getvalue().splitlines()]
        thread_request = next(item for item in sent if item.get("method") == "thread/start")
        self.assertTrue(thread_request["params"]["ephemeral"])
        self.assertEqual([], thread_request["params"]["environments"])
        self.assertEqual([str(Path(temp).resolve())], thread_request["params"]["runtimeWorkspaceRoots"])
        self.assertEqual(False, thread_request["params"]["config"]["mcp_servers"]["example"]["enabled"])
        self.assertFalse(report["isolation"]["model_turn_started"])
        self.assertEqual("gpt-6-luna", report["thread"]["model"])
        self.assertTrue(report["transport"]["client_process_stopped"])
        self.assertTrue(report["isolation"]["runtime_workspace_root_requested"])
        self.assertIn("process-local", report["isolation"]["project_trust"])
        self.assertTrue(report["isolation"]["fixture_trust_applied"])
        app_server_command = popen.call_args.args[0]
        trust_args = [item for item in app_server_command if item.startswith("projects={")]
        self.assertEqual(1, len(trust_args))
        self.assertEqual("trusted", next(iter(runtime_probe.tomllib.loads(trust_args[0])["projects"].values()))["trust_level"])
        self.assertNotIn("secret-command", json.dumps(report))

    def test_safe_error_text_redacts_possible_credentials(self):
        self.assertEqual("[REDACTED]", runtime_probe.safe_error_text("API_KEY=do-not-print"))
        self.assertEqual("ordinary error", runtime_probe.safe_error_text("ordinary error"))


if __name__ == "__main__":
    unittest.main()
