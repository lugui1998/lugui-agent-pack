"""Hermetic hook integration tests: only the local Python JSONL fixture runs.

Run with: python -I -B -m unittest discover -s tests -p test_usage_quota_check.py
No test discovers Codex, opens a port, inherits credentials, or uses a real home.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
FAKE_SERVER = (ROOT / "tests" / "support" / "fake_codex_server.py").resolve()
NOW = 2_000_000_000
CONSUME = "account/rateLimitResetCredit/consume"
HOOK_PATH = ROOT / "scripts" / "hooks" / "usage_quota_check.py"
SPEC = importlib.util.spec_from_file_location("quota_hook_under_test", HOOK_PATH)
HOOK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HOOK)


def quota(*, remaining=0, buffer=False, expiry_days=2, credit="fake-credit-a", primary_days=2 / 24, weekly_days=1, weekly_remaining=80):
    bucket = {
        "limitId": "codex",
        "primary": {"usedPercent": 100 - remaining, "windowDurationMins": 300, "resetsAt": NOW + primary_days * 86400},
        "secondary": {"usedPercent": 100 - weekly_remaining, "windowDurationMins": 10080, "resetsAt": NOW + weekly_days * 86400},
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
        # Existing cases isolate the original expiry/threshold/idempotency rules.
        # New guard cases below explicitly exercise the default or custom wait.
        self.policy = {"enabled": True, "codexCommand": "offline-codex-only", "naturalResetWaitDays": 0, "creditReserve": 0}
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

    def launch(self, helper_args=None):
        # No inherited account credentials or real CLI paths. -I ignores Python
        # environment and user site configuration; -B avoids generated files.
        env = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR") if key in os.environ}
        env.update({"CODEX_HOME": str(self.home), "HOME": str(self.home), "USERPROFILE": str(self.home),
                    "TEMP": str(self.home), "TMP": str(self.home), "PATH": ""})
        argv = [sys.executable, "-I", "-B", str(FAKE_SERVER),
                "--run-hook" if helper_args is None else "--respond-hook", str(self.home), *(helper_args or [])]
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
        payload = event if isinstance(event, dict) else {"hook_event_name": event}
        output, error = child.communicate(json.dumps(payload).encode(), timeout=15)
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

    def clear_observations(self):
        for relative in ("fake-ledger.json", "requests.jsonl", "lugui-agent-pack/usage-quota/state.json"):
            (self.home / relative).unlink(missing_ok=True)

    def choice_setup(self, **quota_options):
        self.policy.pop("naturalResetWaitDays", None)
        self.policy.pop("creditReserve", None)
        self.preferences()
        self.scenario["snapshots"] = [quota(**quota_options)]
        self.save_scenario()

    def session_event(self, session="session-one", **extra):
        return {"hook_event_name": "PostToolUse", "session_id": session, **extra}

    def response(self, decision="approve", session="session-one", request_id=None, expected=0):
        request_id = request_id or self.active()["decision"]["requestId"]
        child = self.launch(["--respond", request_id, "--decision", decision, "--session-id", session])
        output, error = child.communicate(timeout=15)
        self.assertEqual(expected, child.returncode, error.decode())
        self.assertEqual(b"", error)
        return json.loads(output)

    def context_data(self, output):
        context = json.loads(output)["hookSpecificOutput"]["additionalContext"]
        return json.loads(context[context.index("{"):])

    def test_choice_only_when_wait_alone_blocks_and_session_known(self):
        self.choice_setup()
        self.assertEqual("", self.run_hook())
        self.assertNotIn("decision", self.active())
        self.force_due()
        output = self.run_hook(self.session_event(prompt="yes use all resets"))
        data = self.context_data(output)
        self.assertIn("Ask the user", output)
        self.assertEqual("pending", self.active()["decision"]["status"])
        self.assertEqual(NOW + 1800, data["deadline"])
        self.assertEqual(NOW + 7200, data["naturalRecoveryAt"])
        self.assertEqual(0, self.spends())
        before = self.requests()
        self.assertEqual("", self.run_hook(self.session_event()))
        self.assertEqual(before, self.requests())
        self.force_due()
        self.assertEqual("", self.run_hook(self.session_event()))
        self.assertEqual(data["requestId"], self.active()["decision"]["requestId"])

    def test_no_question_for_automatic_expiry_exception_or_other_failed_gates(self):
        self.choice_setup()
        for current, expected_spends in (
            (quota(expiry_days=0.01), 1), (quota(primary_days=3), 1),
            (quota(remaining=10), 0), (quota(expiry_days=6), 0),
        ):
            with self.subTest(current=current):
                self.scenario["snapshots"] = [current]
                self.save_scenario()
                output = self.run_hook(self.session_event())
                self.assertNotIn("Ask the user", output)
                self.assertNotIn("decision", self.active())
                self.assertEqual(expected_spends, self.spends())
                self.clear_observations()

    def test_known_credit_reserve_boundaries_and_unlimited(self):
        self.policy.pop("creditReserve")
        self.preferences()
        for balance, remaining, expected in (("199.99", 1, 1), ("200", 1, 0), ("201", 1, 0),
                                               ("200", 0, 1), ("0", 1, 1)):
            with self.subTest(balance=balance, remaining=remaining):
                current = quota(remaining=remaining)
                current["rateLimitsByLimitId"]["codex"]["credits"] = {
                    "hasCredits": balance != "0", "unlimited": False, "balance": balance}
                self.scenario["snapshots"] = [current]
                self.save_scenario()
                self.run_hook()
                self.assertEqual(expected, self.spends())
                self.clear_observations()
        self.choice_setup()
        current = quota(remaining=0)
        current["rateLimitsByLimitId"]["codex"]["credits"] = {"hasCredits": True, "unlimited": True}
        self.scenario["snapshots"] = [current]
        self.save_scenario()
        self.assertEqual("", self.run_hook(self.session_event()))
        self.assertNotIn("decision", self.active())

    def test_unknown_credits_never_prompt_or_spend(self):
        self.choice_setup()
        for credits in ({}, {"hasCredits": True, "unlimited": False, "balance": "NaN"},
                        {"hasCredits": False, "unlimited": True},
                        {"hasCredits": True, "unlimited": False, "balance": "0"}):
            with self.subTest(credits=credits):
                current = quota()
                current["rateLimitsByLimitId"]["codex"]["credits"] = credits
                self.scenario["snapshots"] = [current]
                self.save_scenario()
                output = self.run_hook(self.session_event())
                self.assertNotIn("Ask the user", output)
                self.assertNotIn("decision", self.active())
                self.assertEqual(0, self.spends())
                self.clear_observations()

    def test_approval_helper_is_local_and_single_use_then_fresh_hook_spends(self):
        self.choice_setup()
        self.run_hook(self.session_event())
        before = self.requests()
        result = self.response()
        self.assertEqual("recorded", result["status"])
        self.assertEqual(before, self.requests())
        self.assertEqual(0, self.spends())
        self.assertEqual(NOW, self.state()["nextCheckAt"])
        self.response(expected=2)
        output = self.run_hook(self.session_event())
        self.assertIn("completed", output)
        self.assertEqual(1, self.spends())
        self.assertEqual("used", self.active()["decision"]["status"])
        self.assertEqual(result["requestId"], self.active()["attempt"]["decisionId"])
        self.force_due()
        self.assertNotIn("completed", self.run_hook(self.session_event()))
        self.assertEqual(1, len(self.requests(CONSUME)))

    def test_keep_and_expired_request_suppress_same_episode(self):
        for action in ("keep", "expire"):
            with self.subTest(action=action):
                self.choice_setup()
                self.scenario["now"] = NOW
                self.save_scenario()
                self.run_hook(self.session_event())
                request_id = self.active()["decision"]["requestId"]
                if action == "keep":
                    self.response("keep")
                self.scenario["now"] = NOW + 1900
                self.save_scenario()
                self.response(expected=2)
                self.force_due()
                self.assertEqual("", self.run_hook(self.session_event()))
                self.assertEqual(request_id, self.active()["decision"]["requestId"])
                self.assertEqual("keep" if action == "keep" else "expired", self.active()["decision"]["status"])
                self.assertEqual(0, self.spends())
                self.clear_observations()

    def test_response_rejects_wrong_session_request_policy_and_malformed_state(self):
        self.choice_setup()
        self.run_hook(self.session_event())
        original = self.state()
        self.response(session="other-session", expected=2)
        self.response(request_id="00000000-0000-0000-0000-000000000000", expected=2)
        self.response(decision="yes", expected=2)
        self.assertEqual(original, self.state())
        self.policy["creditReserve"] = 201
        self.preferences()
        self.response(expected=2)
        self.policy.pop("creditReserve")
        self.preferences()
        malformed = copy.deepcopy(original)
        malformed["accounts"][malformed["activeAccount"]]["decision"]["binding"]["windows"] = {}
        self.write("lugui-agent-pack/usage-quota/state.json", malformed)
        self.response(request_id=original["accounts"][original["activeAccount"]]["decision"]["requestId"], expected=2)
        self.assertEqual(malformed, self.state())
        self.assertEqual(0, self.spends())

    def test_approval_requires_origin_session_and_unexpired_deadline(self):
        self.choice_setup(primary_days=600 / 86400)
        self.run_hook(self.session_event())
        self.assertEqual(NOW + 600, self.active()["decision"]["deadline"])
        self.response()
        self.assertEqual("", self.run_hook(self.session_event("other-session")))
        self.assertEqual(0, self.spends())
        self.scenario["now"] = NOW + 600
        self.save_scenario()
        self.force_due()
        self.run_hook(self.session_event())
        self.assertEqual(0, self.spends())

    def test_approval_does_not_follow_credit_policy_or_window_changes(self):
        variants = [
            quota(credit="replacement"), quota(expiry_days=3), quota(primary_days=0.2),
            quota(remaining=80, weekly_remaining=0),
        ]
        buffered = quota()
        buffered["rateLimitsByLimitId"]["codex"]["credits"] = {"hasCredits": True, "unlimited": False, "balance": "200"}
        variants.append(buffered)
        for current in variants:
            with self.subTest(current=current):
                self.choice_setup()
                self.run_hook(self.session_event())
                self.response()
                self.scenario["snapshots"] = [current]
                self.save_scenario()
                self.run_hook(self.session_event())
                self.assertEqual(0, self.spends())
                self.clear_observations()
        self.choice_setup()
        self.run_hook(self.session_event())
        self.response()
        self.policy["creditReserve"] = 201
        self.preferences()
        self.run_hook(self.session_event())
        self.assertEqual(0, self.spends())

    def test_approval_rechecks_binding_on_second_snapshot(self):
        self.choice_setup()
        self.run_hook(self.session_event())
        self.response()
        self.scenario["snapshots"] = [quota(), quota(credit="replacement")]
        self.save_scenario()
        self.run_hook(self.session_event())
        self.assertEqual(0, self.spends())
        self.assertNotIn("attempt", self.active())

    def test_approval_account_switch_invalidates_old_request(self):
        self.choice_setup()
        self.run_hook(self.session_event())
        old_key = self.state()["activeAccount"]
        self.response()
        self.scenario["accounts"] = [{"account": {"type": "chatgpt", "email": "other@example.invalid"}}]
        self.save_scenario()
        self.run_hook(self.session_event())
        self.assertEqual("invalid", self.state()["accounts"][old_key]["decision"]["status"])
        self.scenario.pop("accounts")
        self.save_scenario()
        self.force_due()
        self.run_hook(self.session_event())
        self.assertEqual(0, self.spends())

    def test_approved_uncertain_retry_uses_same_attempt_and_expiry_never_releases_latch(self):
        self.choice_setup()
        self.scenario["consume"] = "drop-reply"
        self.save_scenario()
        self.run_hook(self.session_event())
        self.response()
        self.run_hook(self.session_event())
        original = copy.deepcopy(self.active()["attempt"])
        self.force_due()
        self.run_hook(self.session_event())
        self.assertEqual(2, len(self.requests(CONSUME)))
        self.assertEqual(self.requests(CONSUME)[0]["params"], self.requests(CONSUME)[1]["params"])
        self.assertEqual(1, self.spends())
        self.assertEqual(original["idempotencyKey"], self.active()["attempt"]["idempotencyKey"])
        self.clear_observations()
        self.scenario["now"] = NOW
        self.save_scenario()
        self.run_hook(self.session_event())
        self.response()
        self.run_hook(self.session_event())
        original = copy.deepcopy(self.active()["attempt"])
        self.scenario["now"] = NOW + 1801
        self.save_scenario()
        self.force_due()
        self.run_hook(self.session_event())
        self.assertEqual(original, self.active()["attempt"])
        self.assertEqual(1, len(self.requests(CONSUME)))
        self.assertEqual("used", self.active()["decision"]["status"])

    def test_rich_success_status_uses_reported_count_and_refresh_data(self):
        refreshed = quota(remaining=90, weekly_remaining=75, credit="remaining-credit", expiry_days=4)
        refreshed["rateLimitResetCredits"]["availableCount"] = 7
        refreshed["rateLimitsByLimitId"]["codex"]["credits"] = {"hasCredits": True, "unlimited": False, "balance": "123.45"}
        self.scenario["snapshots"] = [quota(), quota(), refreshed]
        self.save_scenario()
        data = self.context_data(self.run_hook())
        self.assertEqual({"id": "fake-credit-a", "expiresAt": NOW + 2 * 86400}, data["usedReset"])
        self.assertEqual(7, data["bankedResetCount"])
        self.assertEqual(NOW + 4 * 86400, data["earliestKnownRemainingExpiry"])
        self.assertFalse(data["resetListComplete"])
        self.assertFalse(data["stale"])
        self.assertFalse(data["refreshFailed"])
        self.assertEqual("123.45", data["credits"]["balance"])
        self.assertEqual(90, data["quota"]["300"]["remainingPercent"])
        self.assertEqual(NOW + 7200, data["quota"]["300"]["resetsAt"])
        self.force_due()
        self.scenario["snapshots"] = [refreshed]
        self.save_scenario()
        self.assertNotIn("completed", self.run_hook())

    def test_reset_notification_survives_refresh_failure_and_flags_stale_listing(self):
        self.scenario["failOnSnapshot"] = 3
        self.save_scenario()
        data = self.context_data(self.run_hook())
        self.assertTrue(data["refreshFailed"])
        self.assertTrue(data["stale"])
        self.assertTrue(data["usedResetStillListed"])
        self.assertEqual(1, data["bankedResetCount"])
        self.assertIsNone(data["earliestKnownRemainingExpiry"])
        self.assertEqual("reset", self.active()["attempt"]["status"])
        self.force_due()
        self.assertNotIn("completed", self.run_hook())
        self.assertEqual(1, len(self.requests(CONSUME)))

    def test_decision_log_is_deterministic_bounded_and_cache_does_not_append(self):
        self.choice_setup()
        self.run_hook(self.session_event())
        path = self.home / "lugui-agent-pack/usage-quota/decisions.jsonl"
        first = path.read_bytes()
        row = json.loads(first)
        self.assertEqual(NOW, row["at"])
        self.assertEqual("requested", row["status"])
        self.assertNotIn("offline@example", first.decode())
        self.assertNotIn("session-one", first.decode())
        self.assertEqual(first.decode().strip(), json.dumps(row, sort_keys=True, separators=(",", ":")))
        self.run_hook(self.session_event())
        self.assertEqual(first, path.read_bytes())
        path.write_bytes(b"x" * HOOK.MAX_LOG_BYTES)
        self.response("keep")
        self.assertLess(path.stat().st_size, 1024)
        self.assertEqual(HOOK.MAX_LOG_BYTES, path.with_name("decisions.1.jsonl").stat().st_size)

    def test_logging_failure_does_not_change_redemption_or_repeat(self):
        directory = self.home / "lugui-agent-pack/usage-quota"
        directory.mkdir()
        (directory / "decisions.jsonl").mkdir()
        self.assertIn("completed", self.run_hook())
        self.force_due()
        self.run_hook()
        self.assertEqual(1, len(self.requests(CONSUME)))
        self.assertEqual("reset", self.active()["attempt"]["status"])

    def test_helper_and_hooks_share_lock_without_network_or_state_change(self):
        self.choice_setup()
        self.run_hook(self.session_event())
        before, requests = self.state(), self.requests()
        directory = self.home / "lugui-agent-pack/usage-quota"
        with HOOK.state_lock(directory) as acquired:
            self.assertTrue(acquired)
            self.response(expected=2)
            self.assertEqual("", self.run_hook(self.session_event()))
        self.assertEqual(before, self.state())
        self.assertEqual(requests, self.requests())

    def test_two_helper_answers_cannot_both_record_and_approved_hooks_spend_once(self):
        self.choice_setup()
        self.run_hook(self.session_event())
        request_id = self.active()["decision"]["requestId"]
        args = ["--respond", request_id, "--decision", "approve", "--session-id", "session-one"]
        first, second = self.launch(args), self.launch(args)
        first.communicate(timeout=15)
        second.communicate(timeout=15)
        self.assertEqual([0, 2], sorted([first.returncode, second.returncode]))
        self.scenario["pauseOnSnapshot"] = 1
        self.save_scenario()
        owner = self.launch()
        owner.stdin.write(json.dumps(self.session_event()).encode())
        owner.stdin.close()
        owner.stdin = None
        deadline = time.monotonic() + 5
        while not (self.home / "server-paused").exists():
            self.assertIsNone(owner.poll())
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.01)
        self.assertEqual("", self.run_hook(self.session_event()))
        (self.home / "server-release").touch()
        output, error = owner.communicate(timeout=15)
        self.assertEqual(0, owner.returncode, error.decode())
        self.assertIn(b"completed", output)
        self.assertEqual(1, self.spends())
        self.assertEqual(1, len(self.requests(CONSUME)))

    def test_observed_recovery_cancels_unused_approval(self):
        self.choice_setup()
        self.run_hook(self.session_event())
        self.response()
        self.scenario["snapshots"] = [quota(remaining=80)]
        self.save_scenario()
        self.run_hook(self.session_event())
        self.assertEqual("invalid", self.active()["decision"]["status"])
        self.scenario["snapshots"] = [quota()]
        self.save_scenario()
        self.force_due()
        self.assertEqual("", self.run_hook(self.session_event()))
        self.assertEqual(0, self.spends())

    def test_recovery_during_second_verification_cancels_unused_approval(self):
        self.choice_setup()
        self.run_hook(self.session_event())
        self.response()
        self.scenario["snapshots"] = [quota(), quota(remaining=80)]
        self.save_scenario()
        self.run_hook(self.session_event())
        self.assertEqual("invalid", self.active()["decision"]["status"])
        self.assertNotIn("attempt", self.active())
        self.scenario["snapshots"] = [quota()]
        self.save_scenario()
        self.force_due()
        self.assertEqual("", self.run_hook(self.session_event()))
        self.assertEqual(0, self.spends())

    def test_other_session_observation_cannot_cache_away_approved_origin_event(self):
        self.choice_setup()
        self.run_hook(self.session_event())
        self.response()
        self.run_hook(self.session_event("other-session"))
        self.assertEqual(0, self.spends())
        self.assertEqual("session-one", self.state()["responseDueSession"])
        self.assertIn("completed", self.run_hook(self.session_event()))
        self.assertEqual(1, self.spends())
        self.assertNotIn("responseDueSession", self.state())

    def test_redirected_log_and_response_state_do_not_write_link_target(self):
        self.choice_setup()
        self.run_hook(self.session_event())
        directory = self.home / "lugui-agent-pack/usage-quota"
        state_path = directory / "state.json"
        original = state_path.read_bytes()
        external = self.home / "protected-state.json"
        try:
            os.link(state_path, external)
        except PermissionError:
            self.skipTest("filesystem sandbox denies creating hard links; run on a host allowing hard-link fixtures")
        self.response(expected=2)
        self.assertEqual(original, external.read_bytes())
        external.unlink()
        path = directory / "decisions.jsonl"
        log_before = path.read_bytes()
        external_log = self.home / "protected-log.jsonl"
        os.link(path, external_log)
        self.response("keep")
        self.assertEqual(log_before, external_log.read_bytes())
        self.assertEqual("keep", self.active()["decision"]["status"])

    def test_status_reports_unlimited_unknown_and_nonspending_is_not_success(self):
        for credits, kind in (({"hasCredits": True, "unlimited": True}, "unlimited"), ({}, "unknown")):
            with self.subTest(kind=kind):
                refreshed = quota(remaining=80)
                refreshed["rateLimitsByLimitId"]["codex"]["credits"] = credits
                refreshed["rateLimitResetCredits"] = {"availableCount": 0, "credits": []}
                self.scenario["snapshots"] = [quota(), quota(), refreshed]
                self.save_scenario()
                data = self.context_data(self.run_hook())
                self.assertEqual(kind, data["credits"]["kind"])
                self.assertEqual(0, data["bankedResetCount"])
                self.assertTrue(data["resetListComplete"])
                self.clear_observations()
        for outcome in ("noCredit", "nothingToReset", "unknown-outcome"):
            self.scenario.update(consume=outcome, snapshots=[quota()])
            self.save_scenario()
            self.assertNotIn("completed", self.run_hook())
            self.assertNotIn("notification", self.active())
            self.clear_observations()

    def test_default_wait_boundary_and_strict_expiry_exception(self):
        self.policy.pop("naturalResetWaitDays")
        self.preferences()
        # natural renewal before/equal/after the inclusive two-day boundary;
        # expiry before/equal/after renewal inside that boundary.
        for natural_days, expiry_days, expected in ((1.5, 3, False), (2, 3, False), (2.5, 3, True),
                                                    (1.5, 1, True), (1.5, 1.5, False), (1.5, 2, False)):
            with self.subTest(natural_days=natural_days, expiry_days=expiry_days):
                self.scenario["snapshots"] = [quota(primary_days=natural_days, expiry_days=expiry_days)]
                self.save_scenario()
                self.run_hook()
                self.assertEqual(int(expected), self.spends())
                self.assertEqual(int(expected), len(self.requests(CONSUME)))
                if not expected:
                    self.assertNotIn("attempt", self.active())
                self.clear_observations()

    def test_wait_uses_latest_limiting_window_and_retains_all_triggers(self):
        self.policy.pop("naturalResetWaitDays")
        self.preferences()
        cases = [
            (quota(remaining=80, weekly_remaining=0, weekly_days=3), True, {"10080"}),
            (quota(remaining=80, weekly_remaining=0, weekly_days=1), False, set()),
            (quota(weekly_remaining=0, primary_days=0.1, weekly_days=3), True, {"300", "10080"}),
            (quota(weekly_remaining=0, primary_days=3, weekly_days=0.1), True, {"300", "10080"}),
            (quota(weekly_remaining=0, primary_days=0.1, weekly_days=1), False, set()),
            # A healthy window later than renewal does not extend the wait.
            (quota(primary_days=1, weekly_days=6), False, set()),
        ]
        for current, expected, triggers in cases:
            with self.subTest(current=current):
                self.scenario["snapshots"] = [current]
                self.save_scenario()
                self.run_hook()
                self.assertEqual(int(expected), self.spends())
                if expected:
                    self.assertEqual(triggers, set(self.active()["attempt"]["triggers"]))
                self.clear_observations()

    def test_wait_exception_still_requires_expiry_and_usage_criteria(self):
        self.policy.pop("naturalResetWaitDays")
        self.policy["expiresWithinDays"] = 0.5
        self.preferences()
        for current in (quota(primary_days=2, expiry_days=1),
                        quota(primary_days=2, expiry_days=0.25, remaining=2)):
            with self.subTest(current=current):
                self.scenario["snapshots"] = [current]
                self.save_scenario()
                self.run_hook()
                self.assertEqual([], self.requests(CONSUME))
                self.clear_observations()

    def test_custom_fractional_wait_and_zero(self):
        for wait_days, expected in ((0, True), (0.5, True), (0.75, False)):
            with self.subTest(wait_days=wait_days):
                self.policy["naturalResetWaitDays"] = wait_days
                self.preferences()
                self.scenario["snapshots"] = [quota(primary_days=0.75)]
                self.save_scenario()
                self.run_hook()
                self.assertEqual(int(expected), self.spends())
                self.clear_observations()

    def test_wait_rechecked_when_verification_changes_renewal_or_expiry(self):
        self.policy.pop("naturalResetWaitDays")
        self.preferences()
        cases = ((quota(primary_days=3), quota(primary_days=1)),
                 (quota(primary_days=1, expiry_days=0.5), quota(primary_days=1, expiry_days=1)))
        for initial, verified in cases:
            with self.subTest(verified=verified):
                # First case moves renewal into the wait; second removes the
                # expiry-before-renewal exception and changes selected metadata.
                self.scenario["snapshots"] = [initial, verified]
                self.save_scenario()
                self.run_hook()
                self.assertEqual(2, len(self.requests("account/rateLimits/read")))
                self.assertEqual([], self.requests(CONSUME))
                self.clear_observations()

    def test_unknown_trigger_reset_time_fails_closed(self):
        self.policy.pop("naturalResetWaitDays")
        self.preferences()
        for bad_time in (None, "unknown", NOW, NOW - 1):
            with self.subTest(bad_time=bad_time):
                current = quota(primary_days=3, weekly_remaining=0, weekly_days=4)
                current["rateLimitsByLimitId"]["codex"]["secondary"]["resetsAt"] = bad_time
                self.scenario["snapshots"] = [current]
                self.save_scenario()
                self.run_hook()
                self.assertEqual([], self.requests(CONSUME))
                self.assertIn("10080", self.active()["snapshot"]["windows"])
                self.clear_observations()

    def test_unknown_reset_time_on_verification_blocks_redemption(self):
        self.policy.pop("naturalResetWaitDays")
        self.preferences()
        initial = quota(primary_days=3, weekly_remaining=0, weekly_days=4)
        verified = copy.deepcopy(initial)
        verified["rateLimitsByLimitId"]["codex"]["secondary"].pop("resetsAt")
        self.scenario["snapshots"] = [initial, verified]
        self.save_scenario()
        self.run_hook()
        self.assertEqual(2, len(self.requests("account/rateLimits/read")))
        self.assertEqual([], self.requests(CONSUME))
        self.assertNotIn("attempt", self.active())

    def test_unknown_healthy_reset_time_does_not_break_rate_estimate_or_cache(self):
        current = quota(remaining=50)
        current["rateLimitsByLimitId"]["codex"]["secondary"]["resetsAt"] = None
        self.scenario["snapshots"] = [current]
        self.save_scenario()
        self.run_hook()
        self.assertEqual(NOW + 1800, self.state()["nextCheckAt"])
        self.force_due()
        self.scenario["now"] = NOW + 600
        self.scenario["snapshots"][0]["rateLimitsByLimitId"]["codex"]["secondary"]["usedPercent"] = 21
        self.save_scenario()
        self.run_hook()
        self.assertEqual([], self.requests(CONSUME))
        self.assertEqual(NOW + 4200, self.state()["nextCheckAt"])

    def test_natural_wait_deferral_uses_maximum_interval_and_reset_boundary(self):
        self.policy.pop("naturalResetWaitDays")
        self.preferences()
        for natural_seconds, expected_seconds in ((7200, 3600), (600, 600)):
            with self.subTest(natural_seconds=natural_seconds):
                self.scenario["snapshots"] = [quota(primary_days=natural_seconds / 86400)]
                self.save_scenario()
                self.run_hook()
                self.assertEqual(NOW + expected_seconds, self.state()["nextCheckAt"])
                self.assertEqual([], self.requests(CONSUME))
                self.clear_observations()

    def test_terminal_latch_deferral_keeps_earlier_natural_reset_boundary(self):
        self.policy.pop("naturalResetWaitDays")
        self.preferences()
        self.scenario["snapshots"] = [quota(primary_days=3)]
        self.save_scenario()
        self.run_hook()
        original = self.active()["attempt"]
        self.scenario["snapshots"] = [quota(primary_days=600 / 86400)]
        self.save_scenario()
        self.force_due()
        self.run_hook()
        self.assertEqual(original, self.active()["attempt"])
        self.assertEqual(NOW + 600, self.state()["nextCheckAt"])
        self.assertEqual(1, len(self.requests(CONSUME)))

    def test_pending_retry_respects_new_wait_without_switching_credit(self):
        self.policy.pop("naturalResetWaitDays")
        self.preferences()
        self.scenario.update(consume="drop-reply", snapshots=[quota(primary_days=3)])
        self.save_scenario()
        self.run_hook()
        original = self.active()["attempt"]
        changed = quota(primary_days=1)
        alternate = quota(credit="earlier-expiry", expiry_days=0.5)["rateLimitResetCredits"]["credits"][0]
        changed["rateLimitResetCredits"]["credits"].append(alternate)
        changed["rateLimitResetCredits"]["availableCount"] = 2
        self.scenario["snapshots"] = [changed]
        self.save_scenario()
        self.force_due()
        self.run_hook()
        self.assertEqual(original, self.active()["attempt"])
        self.assertEqual(1, len(self.requests(CONSUME)))
        # An eligible initial read followed by an imminent verified renewal
        # must also block retrying the original uncertain attempt.
        self.scenario["snapshots"] = [quota(primary_days=3), changed]
        self.save_scenario()
        self.force_due()
        self.run_hook()
        self.assertEqual(original, self.active()["attempt"])
        self.assertEqual(1, len(self.requests(CONSUME)))

    def test_pending_attempt_with_unknown_original_window_cannot_retry(self):
        self.scenario.update(consume="drop-reply", snapshots=[quota(weekly_remaining=0)])
        self.save_scenario()
        self.run_hook()
        original = self.active()["attempt"]
        missing = quota(weekly_remaining=0)
        missing["rateLimitsByLimitId"]["codex"].pop("secondary")
        for snapshots in ([missing], [quota(weekly_remaining=0), missing]):
            with self.subTest(snapshots=snapshots):
                self.scenario["snapshots"] = snapshots
                self.save_scenario()
                self.force_due()
                self.run_hook()
                self.assertEqual(original, self.active()["attempt"])
                self.assertEqual(1, len(self.requests(CONSUME)))

    def test_all_original_trigger_windows_must_recover_to_clear_latch(self):
        self.policy.pop("naturalResetWaitDays")
        self.preferences()
        self.scenario["snapshots"] = [quota(primary_days=0.1, weekly_remaining=0, weekly_days=3)]
        self.save_scenario()
        self.run_hook()
        original = self.active()["attempt"]
        self.assertEqual({"300", "10080"}, set(original["triggers"]))
        self.scenario["snapshots"] = [quota(remaining=80, weekly_remaining=0, weekly_days=3, credit="different")]
        self.save_scenario()
        self.force_due()
        self.run_hook()
        self.assertEqual(original, self.active()["attempt"])
        self.assertEqual(1, len(self.requests(CONSUME)))
        self.scenario["snapshots"] = [quota(remaining=80, weekly_remaining=80, credit="different")]
        self.save_scenario()
        self.force_due()
        self.run_hook()
        self.assertNotIn("attempt", self.active())
        self.assertEqual(1, len(self.requests(CONSUME)))

    def test_final_time_boundary_crossing_retains_journal_without_consume(self):
        self.policy.pop("naturalResetWaitDays")
        self.preferences()
        policy = HOOK.validate_policy(self.policy)
        clock = [NOW]
        requests = []
        current = quota(primary_days=2 + 0.5 / 86400, expiry_days=3)

        class FakeRpc:
            def __init__(self, *_args):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                pass
            def request(self, method, _params=None):
                requests.append(method)
                if method == "account/read":
                    return {"account": {"type": "chatgpt", "email": "offline@example.invalid"}}
                if method == "account/rateLimits/read":
                    return current
                raise AssertionError("final wait guard must prevent consume")

        real_write = HOOK.write_state

        def write_then_advance(path, state):
            real_write(path, state)
            # Cross into the inclusive two-day wait after durable journaling.
            clock[0] = NOW + 1

        state_path = self.home / "lugui-agent-pack/usage-quota/state.json"
        state_path.parent.mkdir()
        with mock.patch.object(HOOK, "Rpc", FakeRpc), mock.patch.object(HOOK, "executable", return_value="fixture"), \
                mock.patch.object(HOOK.time, "time", side_effect=lambda: clock[0]), \
                mock.patch.object(HOOK, "write_state", side_effect=write_then_advance):
            HOOK.run_locked(self.home, policy, state_path)
        self.assertNotIn(CONSUME, requests)
        self.assertEqual("pending", self.active()["attempt"]["status"])
        self.assertIn("verification_expired", self.state()["lastNotice"])

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


class NaturalWaitPolicyTests(unittest.TestCase):
    def test_credit_reserve_defaults_and_numeric_validation(self):
        self.assertEqual(200, HOOK.validate_policy({"enabled": True})["creditReserve"])
        for value in (0, 0.5, 200, 1000000):
            self.assertEqual(value, HOOK.validate_policy({"enabled": True, "creditReserve": value})["creditReserve"])
        for value in (True, False, None, "200", -1, float("inf"), float("nan"), {}, []):
            with self.subTest(value=value), self.assertRaises(ValueError):
                HOOK.validate_policy({"enabled": True, "creditReserve": value})
        finite = {"hasCredits": True, "unlimited": False, "balance": "0.001"}
        self.assertTrue(HOOK.credit_buffer(finite, 0))
        self.assertFalse(HOOK.credit_buffer(finite))
        self.assertFalse(HOOK.credit_buffer({"hasCredits": False, "unlimited": False, "balance": "0"}, 0))

    def test_default_custom_and_disabled_wait(self):
        self.assertEqual(2, HOOK.validate_policy({"enabled": True})["naturalResetWaitDays"])
        self.assertFalse(HOOK.validate_policy({"enabled": False})["enabled"])
        for value in (0, 0.25, 2, 365):
            with self.subTest(value=value):
                self.assertEqual(value, HOOK.validate_policy({"enabled": True, "naturalResetWaitDays": value})["naturalResetWaitDays"])

    def test_invalid_wait_and_missing_consent_fail_closed(self):
        for value in (True, False, None, "2", -0.1, 365.1, float("nan"), float("inf"), -float("inf"), [], {}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                HOOK.validate_policy({"enabled": True, "naturalResetWaitDays": value})
        with self.assertRaises(ValueError):
            HOOK.validate_policy({"naturalResetWaitDays": 0})


if __name__ == "__main__":
    unittest.main()
