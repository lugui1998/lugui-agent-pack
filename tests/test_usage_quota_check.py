"""Hermetic hook integration tests: only the local Python JSONL fixture runs.

Run with: python -I -B -m unittest discover -s tests -p test_usage_quota_check.py
No test discovers Codex, opens a port, inherits credentials, or uses a real home.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
FAKE_SERVER = (ROOT / "tests" / "support" / "fake_codex_server.py").resolve()
NOW = 2_000_000_000
CONSUME = "account/rateLimitResetCredit/consume"


def quota(*, remaining=0, buffer=False, expiry_days=2, credit="fake-credit-a"):
    bucket = {
        "limitId": "codex",
        "primary": {"usedPercent": 100 - remaining, "windowDurationMins": 300, "resetsAt": NOW + 7200},
        "secondary": {"usedPercent": 20, "windowDurationMins": 10080, "resetsAt": NOW + 86400},
        "credits": {"hasCredits": buffer, "unlimited": False, "balance": "1" if buffer else "0"},
    }
    return {"rateLimitsByLimitId": {"codex": bucket}, "rateLimitResetCredits": {
        "availableCount": 1,
        "credits": [{"id": credit, "expiresAt": NOW + expiry_days * 86400,
                     "resetType": "codexRateLimits", "status": "available"}],
    }}


class OfflineHookTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="offline-quota-")
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name).resolve()
        (self.home / ".offline-quota-fixture").touch()
        (self.home / "lugui-agent-pack").mkdir()
        self.policy = {"enabled": True, "codexCommand": "offline-codex-only"}
        self.preferences()
        self.scenario = {"now": NOW, "snapshots": [quota()]}
        self.save_scenario()
        self.children = []
        self.addCleanup(self.stop_children)

    def stop_children(self):
        for child in self.children:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=5)

    def write(self, name, value):
        (self.home / name).write_text(json.dumps(value), encoding="utf-8")

    def preferences(self):
        self.write("lugui-agent-pack/preferences.json", {"schemaVersion": 1, "automaticResets": self.policy})

    def save_scenario(self):
        self.write("scenario.json", self.scenario)

    def launch(self):
        # No inherited account credentials or real CLI paths. -I ignores Python
        # environment and user site configuration; -B avoids generated files.
        env = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR") if key in os.environ}
        env.update({"CODEX_HOME": str(self.home), "HOME": str(self.home), "USERPROFILE": str(self.home),
                    "TEMP": str(self.home), "TMP": str(self.home), "PATH": ""})
        argv = [sys.executable, "-I", "-B", str(FAKE_SERVER), "--run-hook", str(self.home)]
        if argv[:4] != [sys.executable, "-I", "-B", str(FAKE_SERVER)]:
            raise AssertionError("only the exact fake server may launch")
        options = {"stdin": subprocess.PIPE, "stdout": subprocess.PIPE, "stderr": subprocess.PIPE,
                   "cwd": str(self.home), "env": env}
        if os.name == "nt":
            options["creationflags"] = subprocess.CREATE_NO_WINDOW
        child = subprocess.Popen(argv, **options)
        self.children.append(child)
        return child

    def finish(self, child, event="PostToolUse"):
        output, error = child.communicate(json.dumps({"hook_event_name": event}).encode(), timeout=15)
        self.assertEqual(0, child.returncode, error.decode())
        self.assertEqual(b"", error)
        return output.decode()

    def run_hook(self, event="PostToolUse"):
        return self.finish(self.launch(), event)

    def requests(self, method=None):
        path = self.home / "requests.jsonl"
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []
        return [row for row in rows if row.get("method") == method] if method else rows

    def state(self):
        return json.loads((self.home / "lugui-agent-pack/usage-quota/state.json").read_text(encoding="utf-8"))

    def active(self):
        state = self.state()
        return state["accounts"][state["activeAccount"]]

    def force_due(self):
        state = self.state()
        state["nextCheckAt"] = NOW - 1
        self.write("lugui-agent-pack/usage-quota/state.json", state)

    def spends(self):
        path = self.home / "fake-ledger.json"
        return json.loads(path.read_text(encoding="utf-8"))["spends"] if path.exists() else 0

    def test_disabled_and_missing_opt_in_make_no_process_requests(self):
        for policy in ({"enabled": False, "codexCommand": "offline-codex-only"}, {}):
            with self.subTest(policy=policy):
                self.policy = policy
                self.preferences()
                self.run_hook()
                self.assertEqual([], self.requests())
                self.assertEqual(0, self.spends())

    def test_unrecognized_event_makes_no_process_requests(self):
        self.assertEqual("", self.run_hook("NotAHookEvent"))
        self.assertEqual([], self.requests())

    def test_default_thresholds_with_and_without_credit_buffer(self):
        # Each subtest has independent state so a terminal latch cannot mask it.
        cases = [(True, 1, False), (True, 0, True), (False, 2, False), (False, 1, True)]
        for buffer, remaining, expected in cases:
            with self.subTest(buffer=buffer, remaining=remaining):
                self.scenario["snapshots"] = [quota(buffer=buffer, remaining=remaining)]
                self.save_scenario()
                self.run_hook()
                self.assertEqual(int(expected), self.spends())
                for relative in ("fake-ledger.json", "requests.jsonl", "lugui-agent-pack/usage-quota/state.json"):
                    (self.home / relative).unlink(missing_ok=True)

    def test_custom_threshold_and_secondary_window(self):
        self.policy.update(remainingPercentWithCredits=3, remainingPercentWithoutCredits=5)
        self.preferences()
        snapshot = quota(buffer=True, remaining=80)
        snapshot["rateLimitsByLimitId"]["codex"]["secondary"]["usedPercent"] = 97
        self.scenario["snapshots"] = [snapshot]
        self.save_scenario()
        self.run_hook()
        self.assertEqual(1, self.spends())
        self.assertEqual({"10080"}, set(self.active()["attempt"]["triggers"]))

    def test_expired_and_distant_resets_are_ineligible(self):
        for days in (-1, 0, 6):
            with self.subTest(expiry_days=days):
                self.scenario["snapshots"] = [quota(expiry_days=days)]
                self.save_scenario()
                if (self.home / "lugui-agent-pack/usage-quota/state.json").exists():
                    self.force_due()
                self.run_hook()
                self.assertEqual([], self.requests(CONSUME))

    def test_earliest_expiring_credit_is_selected(self):
        snapshot = quota(credit="later", expiry_days=4)
        soon = quota(credit="sooner", expiry_days=1)["rateLimitResetCredits"]["credits"][0]
        snapshot["rateLimitResetCredits"]["credits"].append(soon)
        snapshot["rateLimitResetCredits"]["availableCount"] = 2
        self.scenario["snapshots"] = [snapshot]
        self.save_scenario()
        self.run_hook()
        self.assertEqual("sooner", self.requests(CONSUME)[0]["params"]["creditId"])

    def test_cached_event_starts_no_additional_process(self):
        self.scenario["snapshots"] = [quota(remaining=50)]
        self.save_scenario()
        self.run_hook()
        initial = self.requests()
        self.assertEqual("", self.run_hook())
        self.assertEqual(initial, self.requests())
        self.assertEqual(0, self.spends())

    def test_fresh_verification_cancels_on_recovery_or_credit_change(self):
        for verified in (quota(remaining=80), quota(credit="replacement")):
            with self.subTest(verified=verified):
                self.scenario["snapshots"] = [quota(), verified]
                self.save_scenario()
                if (self.home / "lugui-agent-pack/usage-quota/state.json").exists():
                    self.force_due()
                self.run_hook()
                self.assertEqual([], self.requests(CONSUME))
                self.assertEqual(0, self.spends())

    def test_identity_change_during_verification_fails_closed(self):
        account = {"account": {"type": "chatgpt", "email": "offline@example.invalid"}}
        changed = {"account": {"type": "chatgpt", "email": "other@example.invalid"}}
        self.scenario["accounts"] = [account, account, changed]
        self.save_scenario()
        self.run_hook()
        self.assertEqual([], self.requests(CONSUME))
        self.assertIn("identity_changed", self.state()["lastNotice"])

    def test_opt_in_revoked_during_verification_fails_closed(self):
        self.scenario["revokeOnSnapshot"] = 2
        self.save_scenario()
        self.run_hook()
        self.assertEqual([], self.requests(CONSUME))
        self.assertIn("policy_changed", self.state()["lastNotice"])

    def test_duplicate_events_do_not_repeat_terminal_consume(self):
        self.run_hook()
        self.force_due()
        self.run_hook()
        self.assertEqual(1, self.spends())
        self.assertEqual(1, len(self.requests(CONSUME)))
        self.assertEqual("reset", self.active()["attempt"]["status"])

    def test_concurrent_hooks_have_one_owner_and_one_fake_spend(self):
        self.scenario["pauseOnSnapshot"] = 1
        self.save_scenario()
        first = self.launch()
        first.stdin.write(b'{"hook_event_name":"PostToolUse"}')
        first.stdin.close()
        first.stdin = None
        deadline = time.monotonic() + 5
        while not (self.home / "server-paused").exists():
            self.assertIsNone(first.poll(), "first hook exited before synchronization")
            self.assertLess(time.monotonic(), deadline, "fake server did not reach synchronization")
            time.sleep(0.01)
        self.assertEqual("", self.run_hook())
        (self.home / "server-release").touch()
        output, error = first.communicate(timeout=15)
        self.assertEqual(0, first.returncode, error.decode())
        self.assertIn(b"completed", output)
        self.assertEqual(1, self.spends())
        self.assertEqual(1, len(self.requests(CONSUME)))

    def test_dropped_reply_reuses_same_key_and_credit_after_restart(self):
        self.scenario["consume"] = "drop-reply"
        self.save_scenario()
        self.run_hook()
        first = copy.deepcopy(self.active()["attempt"])
        self.assertEqual("pending", first["status"])
        self.assertEqual(1, self.spends())
        self.force_due()
        self.run_hook()
        requests = self.requests(CONSUME)
        self.assertEqual(2, len(requests))
        self.assertEqual(requests[0]["params"], requests[1]["params"])
        self.assertEqual(first["idempotencyKey"], requests[1]["params"]["idempotencyKey"])
        self.assertEqual("alreadyRedeemed", self.active()["attempt"]["status"])
        self.assertEqual(1, self.spends())

    def test_pending_attempt_cannot_switch_to_different_credit(self):
        self.scenario["consume"] = "drop-reply"
        self.save_scenario()
        self.run_hook()
        first = self.active()["attempt"]
        self.scenario["snapshots"] = [quota(credit="different")]
        self.save_scenario()
        self.force_due()
        self.run_hook()
        self.assertEqual(first, self.active()["attempt"])
        self.assertEqual(1, len(self.requests(CONSUME)))
        self.assertEqual(1, self.spends())

    def test_verified_recovery_clears_terminal_latch_without_new_consume(self):
        self.run_hook()
        self.scenario["snapshots"] = [quota(remaining=80, credit="different")]
        self.save_scenario()
        self.force_due()
        self.run_hook()
        self.assertNotIn("attempt", self.active())
        self.assertEqual(1, len(self.requests(CONSUME)))

    def test_bad_or_missing_metadata_fails_closed(self):
        bad_buffer = quota()
        bad_buffer["rateLimitsByLimitId"]["codex"]["credits"] = {"hasCredits": True, "unlimited": False, "balance": "0"}
        count_only = quota()
        count_only["rateLimitResetCredits"] = {"availableCount": 1}
        missing_identity = {"account": {"type": "chatgpt", "email": None}}
        cases = [{"snapshots": [{}]}, {"snapshots": [bad_buffer]}, {"snapshots": [count_only]}, {"accounts": [missing_identity]}]
        for changes in cases:
            with self.subTest(changes=changes):
                self.scenario = {"now": NOW, "snapshots": [quota()], **changes}
                self.save_scenario()
                if (self.home / "lugui-agent-pack/usage-quota/state.json").exists():
                    self.force_due()
                self.run_hook()
                self.assertEqual([], self.requests(CONSUME))

    def test_rpc_errors_malformed_and_disconnect_fail_closed(self):
        for behavior in ("error", "malformed", "drop"):
            with self.subTest(behavior=behavior):
                self.scenario["errors"] = {"account/rateLimits/read": behavior}
                self.save_scenario()
                if (self.home / "lugui-agent-pack/usage-quota/state.json").exists():
                    self.force_due()
                self.run_hook()
                self.assertEqual([], self.requests(CONSUME))
                self.assertEqual(0, self.spends())

    def test_unknown_consume_outcome_retains_pending_attempt(self):
        self.scenario["consume"] = "unknown-outcome"
        self.save_scenario()
        self.run_hook()
        self.assertEqual("pending", self.active()["attempt"]["status"])
        self.assertIn("redemption_uncertain", self.state()["lastNotice"])

    def test_consume_error_retains_pending_without_fake_spend(self):
        self.scenario["consume"] = "error"
        self.save_scenario()
        self.run_hook()
        original = self.active()["attempt"]
        self.assertEqual("pending", original["status"])
        self.assertEqual(0, self.spends())
        self.force_due()
        self.run_hook()
        self.assertEqual(original, self.active()["attempt"])
        self.assertEqual(self.requests(CONSUME)[0]["params"], self.requests(CONSUME)[1]["params"])
        self.assertEqual(0, self.spends())

    def test_nonspending_terminal_outcomes_prevent_another_attempt(self):
        for outcome in ("noCredit", "nothingToReset"):
            with self.subTest(outcome=outcome):
                self.scenario["consume"] = outcome
                self.save_scenario()
                self.run_hook()
                self.force_due()
                self.run_hook()
                self.assertEqual(outcome, self.active()["attempt"]["status"])
                self.assertEqual(1, len(self.requests(CONSUME)))
                self.assertEqual(0, self.spends())
                for relative in ("requests.jsonl", "lugui-agent-pack/usage-quota/state.json"):
                    (self.home / relative).unlink(missing_ok=True)

    def test_proxy_discovery_still_uses_local_stdio_fixture(self):
        self.scenario["daemon"] = True
        self.save_scenario()
        self.run_hook()
        launches = [row for row in self.requests() if row.get("fixture") == "launch"]
        self.assertEqual([["offline-codex-only", "app-server", "proxy"]], [row["argv"] for row in launches])
        self.assertEqual(1, self.spends())


if __name__ == "__main__":
    unittest.main()
