"""Offline installer checks. Never run a hook or contact a Codex account.

Every target is under TemporaryDirectory. The installer runs in this process
with process/network entry points blocked, including during policy imports.
Only the source hook's pure policy validation API may be imported.
One Windows launcher test executes a generated wrapper with a fake child only.
"""

import base64
import hashlib
import importlib.util
import io
import json
import os
import shlex
import shutil
import subprocess
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
    "naturalResetWaitDays": 2,
    "creditReserve": 200,
    "remainingPercentWithCredits": 0,
    "remainingPercentWithoutCredits": 1,
    "minCheckIntervalMinutes": 5,
    "maxCheckIntervalMinutes": 60,
    "initialCheckIntervalMinutes": 30,
    "codexCommand": "codex",
}
CUSTOM_ARGS = (
    "--reset-expiry-days", "2.5",
    "--reset-natural-wait-days", "1.5",
    "--reset-credit-reserve", "125.5",
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
        self.home = self.root / "temporary Lúgui's CODEX_HOME"
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
            fixture_spec = importlib.util.spec_from_file_location("offline_trust_fixture", ROOT / "tests" / "support" / "fake_hook_trust_server.py")
            fixture = importlib.util.module_from_spec(fixture_spec)
            fixture_spec.loader.exec_module(fixture)
            import hook_trust
            self.trust_servers = []

            def fake_server(command, home):
                server = fixture.FakeTrustServer(command, home)
                self.trust_servers.append(server)
                if hasattr(self, "configure_trust_server"):
                    self.configure_trust_server(server)
                return server

            stack.enter_context(patch.object(hook_trust, "TrustRpc", side_effect=fake_server))
            args = module.parse_args([
                "--scope", scope, "--target-project", str(self.project),
                "--instructions", "skip", "--skills", "skip", *arguments,
            ])
            installer = module.Installer(args)
            self.last_installer = installer
            if hasattr(self, "prepare_installer"):
                self.prepare_installer(installer)
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

    def assert_registration(self, *, windows=None):
        windows = sys.platform == "win32" if windows is None else windows
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
            launcher = self.home / "hooks" / "usage_quota_check.ps1"
            if windows:
                self.assertEqual(handler["commandWindows"],
                                 f'powershell.exe -NoLogo -NoProfile -NonInteractive -File "{launcher}"')
                content = launcher.read_text(encoding="utf-8-sig")
                self.assertTrue(launcher.read_bytes().startswith(b"\xef\xbb\xbf"))
                for value in (str(Path(sys.executable).resolve()), str(script), str(self.home)):
                    self.assertIn("'" + value.replace("'", "''") + "'", content)
                self.assertIn("exit $LASTEXITCODE", content)
                self.assertNotIn("@@", content)
                self.assertNotIn("EncodedCommand", handler["commandWindows"])
                self.assertNotIn("ExecutionPolicy", handler["commandWindows"])
            else:
                self.assertNotIn("commandWindows", handler)
                self.assertFalse(launcher.exists())
        self.assertFalse((self.home / "lugui-agent-pack" / "usage-quota").exists())

    def test_disabled_new_install_has_no_hook_or_script(self):
        self.apply("--automatic-resets", "disable")
        self.assertFalse(self.preferences()["automaticResets"]["enabled"])
        self.assertFalse((self.home / "hooks.json").exists())
        self.assertFalse((self.home / "hooks").exists())
        self.assertEqual(self.trust_servers, [])

    def test_enable_defaults_registers_only_production_hook(self):
        self.apply("--automatic-resets", "enable")
        self.assertEqual(self.preferences(), {"schemaVersion": 1, "automaticResets": DEFAULTS})
        self.assert_registration()
        self.assertEqual(len(self.trust_servers), 1)
        self.assertTrue(any(method == "config/batchWrite" for method, _ in self.trust_servers[0].calls))
        python_files = {path.relative_to(self.home).as_posix() for path in self.home.rglob("*.py")}
        self.assertEqual(python_files, {"hooks/usage_quota_check.py"})
        self.assertFalse(any(path.name == "tests" for path in self.home.rglob("*")))
        self.assertFalse(any("fake" in path.name.lower() for path in self.home.rglob("*")))

    def test_custom_policy_and_update_preserve_all_criteria(self):
        self.apply("--automatic-resets", "enable", *CUSTOM_ARGS)
        expected = {
            **DEFAULTS, "expiresWithinDays": 2.5,
            "naturalResetWaitDays": 1.5,
            "creditReserve": 125.5,
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
        if sys.platform == "win32":
            self.assertTrue((self.home / "hooks" / "usage_quota_check.ps1").is_file())
        self.apply("--automatic-resets", "disable", "--update")
        self.assertEqual(self.hooks()["hooks"]["Stop"], [unrelated])

    def test_update_adds_natural_wait_default_preserving_saved_choice_and_criteria(self):
        for choice in ("enable", "disable"):
            with self.subTest(choice=choice):
                self.home = self.root / ("legacy-" + choice)
                self.apply("--automatic-resets", choice, *CUSTOM_ARGS)
                path = self.home / "lugui-agent-pack" / "preferences.json"
                legacy = self.preferences()
                del legacy["automaticResets"]["naturalResetWaitDays"]
                legacy["userSetting"] = "keep"
                path.write_text(json.dumps(legacy), encoding="utf-8")
                self.apply("--update")
                self.assertEqual(self.preferences(), {
                    **legacy,
                    "automaticResets": {**legacy["automaticResets"], "naturalResetWaitDays": 2},
                })
                if choice == "disable":
                    self.assertFalse((self.home / "hooks.json").exists())

    def test_update_adds_credit_reserve_default_preserving_saved_choice_and_criteria(self):
        for choice in ("enable", "disable"):
            with self.subTest(choice=choice):
                self.home = self.root / ("legacy-credit-reserve-" + choice)
                self.apply("--automatic-resets", choice, *CUSTOM_ARGS)
                path = self.home / "lugui-agent-pack" / "preferences.json"
                legacy = self.preferences()
                del legacy["automaticResets"]["creditReserve"]
                legacy["userSetting"] = "keep"
                path.write_text(json.dumps(legacy), encoding="utf-8")
                self.apply("--update")
                self.assertEqual(self.preferences(), {
                    **legacy,
                    "automaticResets": {**legacy["automaticResets"], "creditReserve": 200},
                })
                if choice == "disable":
                    self.assertFalse((self.home / "hooks.json").exists())

    def test_credit_reserve_accepts_zero_fractional_and_large_values(self):
        for value in (0, 0.25, 1000000):
            with self.subTest(value=value):
                self.apply("--automatic-resets", "enable", "--reset-credit-reserve", str(value), "--update")
                self.assertEqual(self.preferences()["automaticResets"], {
                    **DEFAULTS, "creditReserve": value,
                })

    def test_invalid_credit_reserve_argument_blocks_every_write(self):
        self.apply("--automatic-resets", "enable", *CUSTOM_ARGS)
        before = snapshot(self.root)
        for value in ("-1", "nan", "inf", "-inf", "1e309"):
            with self.subTest(value=value):
                code, stdout, stderr = self.invoke(
                    "--update", "--apply", "--reset-credit-reserve=" + value,
                )
                self.assertEqual(code, 2, stdout + stderr)
                self.assertIn("creditReserve", stdout)
                self.assertEqual(snapshot(self.root), before)

    def test_invalid_saved_credit_reserve_blocks_every_write(self):
        self.apply("--automatic-resets", "enable")
        path = self.home / "lugui-agent-pack" / "preferences.json"
        for value in (True, False, None, "200", -1):
            with self.subTest(value=value):
                document = {"schemaVersion": 1, "automaticResets": {**DEFAULTS, "creditReserve": value}}
                path.write_text(json.dumps(document), encoding="utf-8")
                before = snapshot(self.root)
                code, stdout, stderr = self.invoke("--update", "--apply")
                self.assertEqual(code, 2, stdout + stderr)
                self.assertIn("creditReserve", stdout)
                self.assertEqual(snapshot(self.root), before)

    def test_project_scope_rejects_credit_reserve_override_even_when_zero(self):
        for choice in ("ask", "enable", "disable", "preserve"):
            with self.subTest(choice=choice), self.assertRaises(SystemExit) as raised:
                self.invoke("--automatic-resets", choice, "--reset-credit-reserve", "0", scope="project")
            self.assertEqual(raised.exception.code, 2)
        self.assertFalse(self.home.exists())

    def test_natural_wait_accepts_zero_fractional_and_upper_bound(self):
        for value in (0, 0.25, 365):
            with self.subTest(value=value):
                self.apply("--automatic-resets", "enable", "--reset-natural-wait-days", str(value), "--update")
                self.assertEqual(self.preferences()["automaticResets"], {
                    **DEFAULTS, "naturalResetWaitDays": value,
                })

    def test_invalid_natural_wait_argument_blocks_every_write(self):
        self.apply("--automatic-resets", "enable", *CUSTOM_ARGS)
        before = snapshot(self.root)
        for value in ("-1", "365.01", "nan", "inf", "-inf"):
            with self.subTest(value=value):
                code, stdout, stderr = self.invoke(
                    "--update", "--apply", "--reset-natural-wait-days=" + value,
                )
                self.assertEqual(code, 2, stdout + stderr)
                self.assertIn("naturalResetWaitDays", stdout)
                self.assertEqual(snapshot(self.root), before)

    def test_invalid_saved_natural_wait_blocks_every_write(self):
        self.apply("--automatic-resets", "enable")
        path = self.home / "lugui-agent-pack" / "preferences.json"
        for value in (True, None, "2", -1, 365.01):
            with self.subTest(value=value):
                document = {"schemaVersion": 1, "automaticResets": {**DEFAULTS, "naturalResetWaitDays": value}}
                path.write_text(json.dumps(document), encoding="utf-8")
                before = snapshot(self.root)
                code, stdout, stderr = self.invoke("--update", "--apply")
                self.assertEqual(code, 2, stdout + stderr)
                self.assertIn("naturalResetWaitDays", stdout)
                self.assertEqual(snapshot(self.root), before)

    def test_project_scope_rejects_natural_wait_override_even_when_zero(self):
        for choice in ("ask", "enable", "disable", "preserve"):
            with self.subTest(choice=choice), self.assertRaises(SystemExit) as raised:
                self.invoke("--automatic-resets", choice, "--reset-natural-wait-days", "0", scope="project")
            self.assertEqual(raised.exception.code, 2)
        self.assertFalse(self.home.exists())

    def test_interactive_defaults_explain_natural_recovery_guard(self):
        class TerminalOutput(io.StringIO):
            def isatty(self):
                return True

        with patch("sys.stdin.isatty", return_value=True), patch("io.StringIO", TerminalOutput), \
                patch("builtins.input", return_value="e"):
            stdout = self.apply()
        self.assertIn("normal recovery is within 2 days", stdout)
        self.assertIn("expires strictly before recovery", stdout)
        self.assertIn("0 to disable only", stdout)
        self.assertIn("at least 200 credits", stdout)
        self.assertIn("any positive credit balance", stdout)
        self.assertIn("quota is at its trigger", stdout)
        self.assertIn("balance is below the reserve", stdout)
        self.assertIn("asks whether to use the reset or wait", stdout)
        self.assertIn("remain automatic", stdout)
        self.assertIn("attempts to trust those exact definitions", stdout)
        self.assertEqual(self.preferences()["automaticResets"], DEFAULTS)

    def test_project_install_leaves_global_policy_and_hooks_untouched(self):
        self.apply("--automatic-resets", "enable", *CUSTOM_ARGS)
        before = snapshot(self.home)
        self.apply("--automatic-resets", "disable", scope="project")
        self.assertEqual(self.trust_servers, [])
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
                self.assertEqual(self.trust_servers, [])
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
        self.assertEqual(self.trust_servers, [])

    def test_posix_uses_python_without_a_powershell_launcher(self):
        with patch.object(sys, "platform", "linux"):
            self.apply("--automatic-resets", "enable")
            self.assert_registration(windows=False)

    def test_owned_encoded_groups_migrate_and_back_up(self):
        self.apply("--automatic-resets", "enable")
        path = self.home / "hooks.json"
        document = self.hooks()
        for event in EVENTS:
            handler = document["hooks"][event][-1]["hooks"][0]
            program = "& 'old-python' 'usage_quota_check.py'; exit $LASTEXITCODE"
            handler["commandWindows"] = ("powershell.exe -NoLogo -NoProfile -NonInteractive -EncodedCommand "
                                         + base64.b64encode(program.encode("utf-16-le")).decode("ascii"))
        path.write_text(json.dumps(document), encoding="utf-8")
        before = path.read_bytes()
        state_path = self.home / "lugui-agent-pack" / "state" / "global.tsv"
        lines = []
        for line in state_path.read_text(encoding="utf-8").splitlines():
            fields = line.split("\t")
            if len(fields) >= 7 and fields[1] == "reset-hook":
                event = fields[5].split(":", 1)[1]
                canonical = json.dumps(document["hooks"][event][-1], sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                fields[3] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
                line = "\t".join(fields)
            if len(fields) >= 7 and fields[1] == "reset-hook-launcher":
                continue
            lines.append(line)
        state_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        launcher = self.home / "hooks" / "usage_quota_check.ps1"
        if launcher.exists():
            launcher.unlink()
        self.apply("--update")
        self.assert_registration()
        backups = list((state_path.parent / "backups").rglob("*-hooks.json"))
        self.assertTrue(any(candidate.read_bytes() == before for candidate in backups))

    def test_modified_owned_group_blocks_every_write(self):
        self.apply("--automatic-resets", "enable")
        path = self.home / "hooks.json"
        document = self.hooks()
        document["hooks"][EVENTS[0]][-1]["hooks"][0]["timeout"] = 61
        path.write_text(json.dumps(document), encoding="utf-8")
        before = snapshot(self.root)
        code, stdout, stderr = self.invoke("--update", "--apply")
        self.assertEqual(code, 2, stdout + stderr)
        self.assertIn("missing, duplicated or modified", stdout)
        self.assertEqual(snapshot(self.root), before)

    @unittest.skipUnless(sys.platform == "win32", "Windows launcher ownership is platform-specific")
    def test_modified_launcher_blocks_every_write(self):
        self.apply("--automatic-resets", "enable")
        launcher = self.home / "hooks" / "usage_quota_check.ps1"
        launcher.write_text("# user modified launcher\n", encoding="utf-8")
        before = snapshot(self.root)
        code, stdout, stderr = self.invoke("--update", "--apply")
        self.assertEqual(code, 2, stdout + stderr)
        self.assertIn("existing content differs", stdout)
        self.assertEqual(snapshot(self.root), before)

    @unittest.skipUnless(sys.platform == "win32", "Windows launcher ownership is platform-specific")
    def test_owned_launcher_update_uses_file_backup_policy(self):
        self.apply("--automatic-resets", "enable")
        launcher = self.home / "hooks" / "usage_quota_check.ps1"
        before = launcher.read_bytes()
        # Capture a different interpreter without starting either interpreter.
        with patch.object(sys, "executable", sys.executable + ".offline-alternate"):
            self.apply("--update")
            self.assert_registration()
        self.assertNotEqual(launcher.read_bytes(), before)
        backups = list((self.home / "lugui-agent-pack" / "state" / "backups").rglob("*-usage_quota_check.ps1"))
        self.assertTrue(any(candidate.read_bytes() == before for candidate in backups))

    @unittest.skipUnless(sys.platform == "win32", "Windows launcher ownership is platform-specific")
    def test_unowned_launcher_collision_blocks_every_write(self):
        launcher = self.home / "hooks" / "usage_quota_check.ps1"
        launcher.parent.mkdir(parents=True)
        launcher.write_text("# user launcher\n", encoding="utf-8")
        before = snapshot(self.root)
        code, stdout, stderr = self.invoke("--automatic-resets", "enable", "--apply")
        self.assertEqual(code, 2, stdout + stderr)
        self.assertIn("existing content differs", stdout)
        self.assertEqual(snapshot(self.root), before)

    @unittest.skipUnless(sys.platform == "win32", "Windows command path restrictions are platform-specific")
    def test_ambiguous_windows_shell_paths_fail_before_writes(self):
        original_home = self.home
        for character in ("%", "!", "$", "`"):
            with self.subTest(character=character):
                self.home = self.root / ("home" + character + "literal")
                code, stdout, stderr = self.invoke("--automatic-resets", "enable", "--apply")
                self.assertEqual(code, 2, stdout + stderr)
                self.assertIn("unsupported shell expansion characters", stdout)
                self.assertFalse(self.home.exists())
        self.home = original_home

    @unittest.skipUnless(sys.platform == "win32", "requires Windows PowerShell")
    def test_windows_wrapper_forwards_stdin_bytes_arguments_and_exit_status(self):
        """Execute only a fake child script; no production hook or account is loaded."""
        powershell = shutil.which("powershell.exe")
        if powershell is None:
            self.skipTest("Windows PowerShell executable is unavailable")
        spec = importlib.util.spec_from_file_location("offline_launcher_renderer", SCRIPTS / "reset_install.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        fake = self.root / "fake Lúgui's child.py"
        output = self.root / "fake-output.bin"
        args_output = self.root / "fake-args.json"
        fake.write_text("import json, pathlib, sys\n"
                        f"pathlib.Path({str(output)!r}).write_bytes(sys.stdin.buffer.read())\n"
                        f"pathlib.Path({str(args_output)!r}).write_text(json.dumps(sys.argv[1:]), encoding='utf-8')\n"
                        "raise SystemExit(23)\n", encoding="utf-8")
        wrapper = self.root / "fake Lúgui's wrapper.ps1"
        wrapper.write_bytes(module.render_windows_launcher(
            SCRIPTS / "hooks" / "usage_quota_check.ps1", str(Path(sys.executable).resolve()), fake, self.home,
        ))
        event = b'{"event":"offline", "text":"\xc3\xa7"}\r\n\x00\xff'
        completed = subprocess.run(
            [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(wrapper)],
            input=event, capture_output=True, timeout=30,
        )
        self.assertEqual(completed.returncode, 23, completed.stderr.decode(errors="replace"))
        self.assertEqual(output.read_bytes(), event)
        self.assertEqual(json.loads(args_output.read_text(encoding="utf-8")), ["--codex-home", str(self.home)])


if __name__ == "__main__":
    unittest.main()
