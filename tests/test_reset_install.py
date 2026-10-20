"""Offline installer checks. Never run a hook or contact a Codex account.

Every target is under TemporaryDirectory. The installer runs in this process
with process/network entry points blocked, including during policy imports.
Only the source hook's pure policy validation API may be imported.
"""

import base64
import importlib.util
import io
import json
import os
import shlex
import sys
import tempfile
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
EVENTS = ("UserPromptSubmit", "PostToolUse")
DEFAULTS = {
    "enabled": True,
    "expiresWithinDays": 5,
    "remainingPercentWithCredits": 0,
    "remainingPercentWithoutCredits": 1,
    "minCheckIntervalMinutes": 5,
    "maxCheckIntervalMinutes": 60,
    "initialCheckIntervalMinutes": 30,
    "codexCommand": "codex",
}
CUSTOM_ARGS = (
    "--reset-expiry-days", "2.5",
    "--reset-remaining-with-credits", "2",
    "--reset-remaining-without-credits", "3",
    "--reset-min-check-minutes", "10",
    "--reset-max-check-minutes", "45",
    "--reset-initial-check-minutes", "20",
    "--reset-codex-command", "offline-test-command-never-execute",
)


def snapshot(root):
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*") if path.is_file()
    }


class ResetInstallerTests(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="lugui-offline-install-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.home = self.root / "temporary CODEX_HOME"
        self.project = self.root / "temporary project"
        self.project.mkdir()

    def invoke(self, *arguments, scope="global"):
        """Fail even if production code catches a forbidden-call exception."""
        forbidden = []

        def reject(*args, **kwargs):
            forbidden.append("unexpected process, network, or home lookup")
            raise AssertionError(forbidden[-1])

        stdout, stderr = io.StringIO(), io.StringIO()
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, {"CODEX_HOME": str(self.home)}))
            stack.enter_context(patch.object(sys, "dont_write_bytecode", True))
            stack.enter_context(patch.object(sys, "path", [str(SCRIPTS), *sys.path]))
            for target in ("subprocess.Popen", "subprocess.run", "os.system",
                           "socket.socket", "socket.create_connection", "pathlib.Path.home"):
                stack.enter_context(patch(target, side_effect=reject))
            if hasattr(os, "startfile"):
                stack.enter_context(patch("os.startfile", side_effect=reject))
            stack.enter_context(redirect_stdout(stdout))
            stack.enter_context(redirect_stderr(stderr))
            spec = importlib.util.spec_from_file_location("offline_installer_under_test", SCRIPTS / "install.py")
            module = importlib.util.module_from_spec(spec)
            stack.enter_context(patch.dict(sys.modules, {spec.name: module}))
            spec.loader.exec_module(module)
            args = module.parse_args([
                "--scope", scope, "--target-project", str(self.project),
                "--instructions", "skip", "--skills", "skip", *arguments,
            ])
            installer = module.Installer(args)
            self.assertEqual(installer.home, self.home)
            self.assertEqual(installer.project, self.project)
            code = installer.run()
        self.assertEqual(forbidden, [], "installer attempted a forbidden live operation")
        return code, stdout.getvalue(), stderr.getvalue()

    def apply(self, *arguments, scope="global"):
        code, stdout, stderr = self.invoke(*arguments, "--apply", scope=scope)
        self.assertEqual(code, 0, stdout + stderr)
        return stdout

    def preferences(self):
        return json.loads((self.home / "lugui-agent-pack" / "preferences.json").read_text(encoding="utf-8"))

    def hooks(self):
        return json.loads((self.home / "hooks.json").read_text(encoding="utf-8"))

    def assert_registration(self):
        hooks = self.hooks()["hooks"]
        script = self.home / "hooks" / "usage_quota_check.py"
        self.assertEqual(script.read_bytes(), (SCRIPTS / "hooks" / script.name).read_bytes())
        for event in EVENTS:
            handler = hooks[event][-1]["hooks"][0]
            self.assertEqual(handler["type"], "command")
            self.assertEqual(handler["timeout"], 60)
            self.assertEqual(shlex.split(handler["command"]), [
                str(Path(sys.executable).resolve()), str(script), "--codex-home", str(self.home),
            ])
            command = handler["commandWindows"]
            prefix = "powershell.exe -NoLogo -NoProfile -NonInteractive -EncodedCommand "
            self.assertTrue(command.startswith(prefix))
            decoded = base64.b64decode(command[len(prefix):]).decode("utf-16-le")
            self.assertIn(str(script).replace("'", "''"), decoded)
            self.assertIn("'--codex-home'", decoded)
            self.assertIn(str(self.home).replace("'", "''"), decoded)
        self.assertFalse((self.home / "lugui-agent-pack" / "usage-quota").exists())

    def test_disabled_new_install_has_no_hook_or_script(self):
        self.apply("--automatic-resets", "disable")
        self.assertFalse(self.preferences()["automaticResets"]["enabled"])
        self.assertFalse((self.home / "hooks.json").exists())
        self.assertFalse((self.home / "hooks").exists())

    def test_enable_defaults_registers_only_production_hook(self):
        self.apply("--automatic-resets", "enable")
        self.assertEqual(self.preferences(), {"schemaVersion": 1, "automaticResets": DEFAULTS})
        self.assert_registration()
        python_files = {path.relative_to(self.home).as_posix() for path in self.home.rglob("*.py")}
        self.assertEqual(python_files, {"hooks/usage_quota_check.py"})
        self.assertFalse(any(path.name == "tests" for path in self.home.rglob("*")))
        self.assertFalse(any("fake" in path.name.lower() for path in self.home.rglob("*")))

    def test_custom_policy_and_update_preserve_all_criteria(self):
        self.apply("--automatic-resets", "enable", *CUSTOM_ARGS)
        expected = {
            **DEFAULTS, "expiresWithinDays": 2.5,
            "remainingPercentWithCredits": 2, "remainingPercentWithoutCredits": 3,
            "minCheckIntervalMinutes": 10, "maxCheckIntervalMinutes": 45,
            "initialCheckIntervalMinutes": 20,
            "codexCommand": "offline-test-command-never-execute",
        }
        self.assertEqual(self.preferences()["automaticResets"], expected)
        preferences_path = self.home / "lugui-agent-pack" / "preferences.json"
        preferences_path.write_text(json.dumps({**self.preferences(), "userSetting": "keep"}), encoding="utf-8")
        before = preferences_path.read_bytes()
        hooks_before = (self.home / "hooks.json").read_bytes()
        # The ordinary update default is "ask", which must preserve saved consent.
        self.apply("--update")
        self.assertEqual(preferences_path.read_bytes(), before)
        self.assertEqual((self.home / "hooks.json").read_bytes(), hooks_before)
        self.assertEqual(self.preferences()["automaticResets"], expected)
        self.assert_registration()

    def test_disable_removes_owned_handlers_preserves_unrelated_data(self):
        self.apply("--automatic-resets", "enable", *CUSTOM_ARGS)
        document = self.hooks()
        unrelated = {"hooks": [{"type": "command", "command": "user-hook-never-execute"}]}
        for event in EVENTS:
            document["hooks"][event].insert(0, unrelated)
        document["hooks"]["Stop"] = [unrelated]
        document["userMetadata"] = {"keep": True}
        (self.home / "hooks.json").write_text(json.dumps(document), encoding="utf-8")
        policy_before = self.preferences()["automaticResets"]
        self.apply("--automatic-resets", "disable", "--update")
        self.assertEqual(self.preferences()["automaticResets"], {**policy_before, "enabled": False})
        self.assertEqual(self.hooks(), {
            "hooks": {event: [unrelated] for event in (*EVENTS, "Stop")},
            "userMetadata": {"keep": True},
        })
        # The implementation deliberately retains the dormant production script.
        self.assertTrue((self.home / "hooks" / "usage_quota_check.py").is_file())
        self.apply("--automatic-resets", "disable", "--update")
        self.assertEqual(self.hooks()["hooks"]["Stop"], [unrelated])

    def test_project_install_leaves_global_policy_and_hooks_untouched(self):
        self.apply("--automatic-resets", "enable", *CUSTOM_ARGS)
        before = snapshot(self.home)
        self.apply("--automatic-resets", "disable", scope="project")
        after = snapshot(self.home)
        additions = set(after) - set(before)
        self.assertEqual(len(additions), 1)
        self.assertTrue(next(iter(additions)).startswith("lugui-agent-pack/state/project-"))
        self.assertEqual({path: after[path] for path in before}, before)
        self.assertFalse((self.project / ".codex" / "hooks.json").exists())
        self.assertFalse((self.project / ".codex" / "hooks").exists())

    def test_new_and_existing_dry_runs_make_no_target_writes(self):
        for choice in ("enable", "disable"):
            with self.subTest(choice=choice):
                code, stdout, stderr = self.invoke("--automatic-resets", choice, "--dry-run")
                self.assertEqual(code, 0, stdout + stderr)
                self.assertFalse(self.home.exists())
                self.assertEqual(list(self.project.iterdir()), [])
        self.apply("--automatic-resets", "enable", *CUSTOM_ARGS)
        before = snapshot(self.root)
        directories_before = {path.relative_to(self.root) for path in self.root.rglob("*") if path.is_dir()}
        code, stdout, stderr = self.invoke("--automatic-resets", "disable", "--update", "--dry-run")
        self.assertEqual(code, 0, stdout + stderr)
        self.assertEqual(snapshot(self.root), before)
        self.assertEqual(
            {path.relative_to(self.root) for path in self.root.rglob("*") if path.is_dir()},
            directories_before,
        )

    def test_new_noninteractive_install_requires_explicit_choice(self):
        # Avoid relying on the test runner's terminal state.
        with patch("sys.stdin.isatty", return_value=False):
            code, stdout, stderr = self.invoke("--apply")
        self.assertEqual(code, 2, stdout + stderr)
        self.assertIn("--automatic-resets enable or disable", stdout)
        self.assertFalse(self.home.exists())


if __name__ == "__main__":
    unittest.main()
