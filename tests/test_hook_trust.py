"""Offline exact-trust checks. All live process/network entry points are blocked.

Transport checks permit only this interpreter running the explicit local fixture.
No installed hook is executed and no real Codex app-server is started.
"""

import copy
import importlib.util
import io
import json
import os
import subprocess
import sys
import unittest
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import test_reset_install as reset_tests
from test_reset_install import ROOT, SCRIPTS, EVENTS


class HookTrustTests(unittest.TestCase):
    invoke = reset_tests.ResetInstallerTests.invoke
    apply = reset_tests.ResetInstallerTests.apply
    preferences = reset_tests.ResetInstallerTests.preferences
    hooks = reset_tests.ResetInstallerTests.hooks

    def setUp(self):
        reset_tests.ResetInstallerTests.setUp(self)
        self.guard = ExitStack()
        self.addCleanup(self.guard.close)
        self.denied = []

        def deny(*args, **kwargs):
            self.denied.append("live operation")
            raise AssertionError("live operation forbidden")

        self.real_popen = subprocess.Popen
        for name in ("subprocess.Popen", "subprocess.run", "os.system", "socket.socket", "socket.create_connection"):
            self.guard.enter_context(patch(name, side_effect=deny))
        self.guard.enter_context(patch.object(sys, "dont_write_bytecode", True))
        self.guard.enter_context(patch.object(sys, "path", [str(SCRIPTS), *sys.path]))
        import hook_trust
        self.module = hook_trust
        spec = importlib.util.spec_from_file_location("trust_fixture", ROOT / "tests/support/fake_hook_trust_server.py")
        self.fixture = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.fixture)

    def tearDown(self):
        self.assertEqual(self.denied, [])

    def installed(self):
        self.apply("--automatic-resets", "enable")
        self.installer = self.last_installer
        self.intent = self.installer.reset_trust_intent
        self.config = self.home / "config.toml"
        self.config.write_text('model = "user-model"\n', encoding="utf-8")
        self.server = self.fixture.FakeTrustServer("offline", self.home)
        return self.server

    def trust(self, server=None):
        output = io.StringIO()
        with redirect_stdout(output):
            result = self.module.trust_installed_hooks(
                self.installer, self.intent, rpc_factory=lambda *_: server or self.server,
            )
        return result, output.getvalue()

    def writes(self):
        return [params for method, params in self.server.calls if method == "config/batchWrite"]

    def test_success_exact_sparse_write_backup_and_idempotence(self):
        self.installed()
        original = self.config.read_bytes()
        result, output = self.trust()
        self.assertTrue(result, output)
        write, = self.writes()
        self.assertEqual(set(write["edits"][0]["value"]), {hook["key"] for hook in self.server.hook_list()["data"][0]["hooks"]})
        self.assertEqual(write["expectedVersion"], __import__("hashlib").sha256(original).hexdigest())
        backups = list(self.installer.state_path.parent.rglob("before-hook-trust-*-config.toml"))
        self.assertTrue(any(path.read_bytes() == original for path in backups))
        self.assertEqual(self.server.document()["model"], "user-model")
        self.server.calls.clear()
        self.assertTrue(self.trust()[0])
        self.assertEqual(self.writes(), [])

    def test_mixed_trusted_disabled_and_unrelated_state_is_preserved(self):
        self.installed()
        first, second = self.server.hook_list()["data"][0]["hooks"]
        unrelated = {"enabled": False, "trusted_hash": "unrelated-hash", "custom": "keep"}
        document = {"model": "user-model", "hooks": {"state": {
            first["key"]: {"enabled": False, "trusted_hash": first["currentHash"], "custom": "keep"},
            "unrelated": unrelated,
        }}}
        self.config.write_text("\n".join(self.fixture.encode_toml(document)), encoding="utf-8")
        result, output = self.trust()
        self.assertTrue(result, output)
        self.assertIn("trusted-disabled", output)
        self.assertEqual(set(self.writes()[0]["edits"][0]["value"]), {second["key"]})
        self.assertEqual(self.server.document()["hooks"]["state"]["unrelated"], unrelated)
        self.assertEqual(self.server.document()["hooks"]["state"][first["key"]], document["hooks"]["state"][first["key"]])

    def test_wrong_or_ambiguous_metadata_never_writes(self):
        self.installed()
        baseline = self.server.hook_list()
        edits = [
            ("source", "project"), ("source", "plugin"), ("source", "system"),
            ("sourcePath", str(self.root / "elsewhere/hooks.json")),
            ("command", "other-command"), ("isManaged", True), ("pluginId", "plugin"),
            ("matcher", "user-modified"), ("timeoutSec", 61), ("async", True),
            ("enabled", "yes"), ("currentHash", ""), ("trustStatus", "managed"),
        ]
        for field, value in edits:
            with self.subTest(field=field, value=value):
                changed = copy.deepcopy(baseline)
                changed["data"][0]["hooks"][0][field] = value
                self.server.on_request = lambda method, params: changed if method == "hooks/list" else None
                self.server.calls.clear()
                self.assertFalse(self.trust()[0])
                self.assertEqual(self.writes(), [])
        for mutation in ("duplicate", "missing", "same-key", "bad-shape", "errors"):
            with self.subTest(mutation=mutation):
                changed = copy.deepcopy(baseline)
                hooks = changed["data"][0]["hooks"]
                if mutation == "duplicate":
                    hooks.append(copy.deepcopy(hooks[0]))
                elif mutation == "missing":
                    hooks.pop()
                elif mutation == "same-key":
                    hooks[1]["key"] = hooks[0]["key"]
                elif mutation == "errors":
                    changed["data"][0]["errors"] = [{"message": "fixture"}]
                else:
                    changed = {"data": None}
                self.server.on_request = lambda method, params: changed if method == "hooks/list" else None
                self.server.calls.clear()
                self.assertFalse(self.trust()[0])
                self.assertEqual(self.writes(), [])

    def test_changed_policy_payload_group_or_state_stops_before_rpc(self):
        self.installed()
        targets = [self.home / "lugui-agent-pack/preferences.json", self.home / "hooks/usage_quota_check.py",
                   self.home / "hooks.json", self.installer.state_path]
        for target in targets:
            with self.subTest(target=target):
                original = target.read_bytes()
                target.write_bytes(b"{}" if target.suffix == ".json" else b"changed")
                self.server.calls.clear()
                self.assertFalse(self.trust()[0])
                self.assertEqual(self.server.calls, [])
                target.write_bytes(original)

    def test_changes_during_discovery_prevent_write(self):
        self.installed()
        for target in (self.home / "lugui-agent-pack/preferences.json", self.home / "hooks/usage_quota_check.py", self.config):
            with self.subTest(target=target):
                original = target.read_bytes()
                self.server.calls.clear()
                def mutate(method, params):
                    if method == "config/read":
                        target.write_bytes(original + b" ")
                        if target.suffix == ".json":
                            document = json.loads(original)
                            document["automaticResets"]["enabled"] = False
                            target.write_text(json.dumps(document), encoding="utf-8")
                self.server.on_request = mutate
                self.assertFalse(self.trust()[0])
                self.assertEqual(self.writes(), [])
                target.write_bytes(original)

    def test_metadata_change_between_lists_prevents_write(self):
        self.installed()
        def mutate(method, params):
            if method == "hooks/list" and len(self.server.calls) > 1:
                changed = self.server.hook_list()
                changed["data"][0]["hooks"][0]["currentHash"] = "changed-opaque-hash"
                return changed
        self.server.on_request = mutate
        self.assertFalse(self.trust()[0])
        self.assertEqual(self.writes(), [])

    def test_missing_config_version_and_cas_conflict_do_not_retry(self):
        self.installed()
        self.server.on_request = lambda method, params: {"layers": []} if method == "config/read" else None
        self.assertFalse(self.trust()[0])
        self.assertEqual(self.writes(), [])
        def conflict(method, params):
            if method == "config/batchWrite":
                raise self.module.TrustError("config conflict")
        self.server.on_request = conflict
        self.assertFalse(self.trust()[0])
        self.assertEqual(len(self.writes()), 1)

    def test_postverification_failure_reports_manual_exact_hooks(self):
        self.installed()
        def fail_after_write(method, params):
            if method == "hooks/list" and any(name == "config/batchWrite" for name, _ in self.server.calls):
                raise self.module.TrustError("verification unavailable")
        self.server.on_request = fail_after_write
        result, output = self.trust()
        self.assertFalse(result)
        self.assertEqual(len(self.writes()), 1)
        self.assertIn("activation-unverified", output)
        self.assertIn("/hooks", output)
        for event in EVENTS:
            self.assertIn(event, output)
        self.assertIn(str(self.home / "hooks.json"), output)

    def test_partial_apply_or_state_save_failure_never_starts_trust(self):
        def fail():
            raise OSError("offline failure")
        for phase in ("operation", "state"):
            with self.subTest(phase=phase):
                self.home = self.root / phase
                def prepare(installer):
                    if phase == "state":
                        installer.save_state = fail
                    else:
                        original = installer.plan
                        def plan():
                            original()
                            installer.add_operation("FAIL", installer.home, "offline injected failure", fail)
                        installer.plan = plan
                self.prepare_installer = prepare
                code, output, errors = self.invoke("--automatic-resets", "enable", "--apply")
                self.assertIn(code, (1, 2), output + errors)
                self.assertEqual(self.trust_servers, [])

    def test_preflight_conflict_never_starts_trust(self):
        self.home.mkdir()
        (self.home / "hooks.json").write_text("malformed", encoding="utf-8")
        code, _, _ = self.invoke("--automatic-resets", "enable", "--apply")
        self.assertEqual(code, 2)
        self.assertEqual(self.trust_servers, [])

    def test_resolver_ignores_cwd_and_relative_path_entries(self):
        suffix = ".exe" if os.name == "nt" else ""
        executable = self.root / ("fake-codex" + suffix)
        executable.write_bytes(b"offline fixture never executed")
        executable.chmod(0o700)
        with patch("os.get_exec_path", return_value=["", ".", "relative"]):
            with self.assertRaises(self.module.TrustError):
                self.module.resolve_executable(executable.name)
        with patch("os.get_exec_path", return_value=[str(self.root)]):
            self.assertEqual(self.module.resolve_executable(executable.name), str(executable))
            with self.assertRaises(self.module.TrustError):
                self.module.resolve_executable("./" + executable.name)
        self.assertEqual(self.module.resolve_executable(str(executable)), str(executable))

    def test_transport_allows_only_fixture_and_rejects_errors(self):
        self.installed()
        fixture = ROOT / "tests/support/fake_hook_trust_server.py"
        for mode in ("normal", "malformed", "duplicate-key", "wrong-id", "scalar-result", "unknown-method", "timeout", "server-request"):
            with self.subTest(mode=mode):
                children = []
                def spawn(args, **kwargs):
                    self.assertEqual(args, [str(Path(sys.executable)), "app-server", "--listen", "stdio://"])
                    process = self.real_popen([sys.executable, "-B", str(fixture), mode, str(self.home)], **kwargs)
                    children.append(process)
                    return process
                with patch.object(self.module, "RPC_TIMEOUT", 0.15), \
                        patch("subprocess.run", return_value=subprocess.CompletedProcess([], 1)), \
                        patch("subprocess.Popen", side_effect=spawn):
                    if mode == "normal":
                        with self.module.TrustRpc(sys.executable, self.home) as rpc:
                            self.assertIn("data", rpc.request("hooks/list", {"cwds": [str(self.home)]}))
                            for method in ("account/read", "account/rateLimits/read", "account/reset", "thread/start", "turn/start"):
                                with self.assertRaises(self.module.TrustError):
                                    rpc.request(method, {})
                    else:
                        with self.assertRaises(self.module.TrustError):
                            with self.module.TrustRpc(sys.executable, self.home):
                                self.fail("bad fixture initialized")
                self.assertTrue(children)
                self.assertTrue(all(child.poll() is not None for child in children))

    def test_unavailable_executable_and_daemon_timeout_do_not_launch(self):
        with self.assertRaises(self.module.TrustError):
            self.module.TrustRpc(str(self.root / "missing-executable"), self.home)
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("offline-probe", 2)), \
                patch("subprocess.Popen") as spawn:
            with self.assertRaises(self.module.TrustError):
                self.module.TrustRpc(sys.executable, self.home)
            spawn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
