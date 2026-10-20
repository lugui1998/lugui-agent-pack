#!/usr/bin/env python3
"""Opt-in account API Codex quota hook (stdlib, Python 3.10+).

Installed at CODEX_HOME/hooks/usage_quota_check.py. The installer supplies an
absolute --codex-home; otherwise CODEX_HOME or ~/.codex is used, never hook cwd.
Only a JSON UserPromptSubmit/PostToolUse event on stdin activates this script.
Importing it has no side effects. No login, thread, turn or approval API is used.

Protocol: https://learn.chatgpt.com/docs/app-server (account/read,
account/rateLimits/read, account/rateLimitResetCredit/consume).
The documented account/read ChatGPT identity is email (nullable), not an account
or workspace ID. We require email and partition state by its SHA-256 hash. Tier
changes do not change that key. Same-email workspaces cannot be distinguished by
this protocol; an outstanding attempt still requires its exact credit to appear
in a fresh response. No email, tokens, raw RPC messages or server errors are saved.

One OS lock per CODEX_HOME serializes all hook processes through verification,
redemption and durable response recording. Pending attempts survive crashes and
reuse their original UUID and credit ID. Terminal or uncertain attempts cannot
be replaced until every triggering window has verifiably recovered. If recovery
is never observed, automation stays blocked for that account rather than guessing.
Polling occurs only on hook events; it is not a background timer. Cached results
never authorize a redemption. Cache account switches are discovered at the next
due query; this affects polling latency, never redemption authorization.

The journal uses file fsync and atomic replacement for process-crash recovery.
Windows stdlib does not guarantee replacement durability after OS/power failure;
this hook does not claim exactly-once spending across that failure boundary.

State paths reject symlinks, Windows reparse points and multiply linked files.
As with other user-owned configuration, this does not defend against malicious
concurrent modification by another process with the same user's OS privileges.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import queue
import shutil
import stat
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterator


DEFAULT_POLICY: dict[str, Any] = {
    "enabled": False,
    "expiresWithinDays": 5,
    "remainingPercentWithCredits": 0,
    "remainingPercentWithoutCredits": 1,
    "minCheckIntervalMinutes": 5,
    "maxCheckIntervalMinutes": 60,
    "initialCheckIntervalMinutes": 30,
    "codexCommand": "codex",
}
EVENTS = frozenset({"UserPromptSubmit", "PostToolUse"})
CORE_DURATIONS = frozenset({300, 10080})
TERMINAL_OUTCOMES = frozenset({"reset", "alreadyRedeemed", "noCredit", "nothingToReset"})
MAX_JSON_BYTES = 256 * 1024
RPC_TIMEOUT = 8.0
PROCESS_BUDGET = 35.0
STATE_SCHEMA = 1


class HookError(Exception):
    """An allowlisted error code, never an untrusted server error string."""


def number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        converted = float(value)
    except (OverflowError, ValueError):
        return None
    return converted if math.isfinite(converted) else None


def validate_policy(policy: Any) -> dict[str, Any]:
    """Validate the automaticResets object; explicit enabled is mandatory.

    Defaults fill omitted tuning fields, never the consent field. Unknown policy
    keys fail closed; unrelated outer preference keys are outside this function.
    Raise ValueError without echoing supplied values on invalid preferences.
    """
    if not isinstance(policy, dict) or type(policy.get("enabled")) is not bool:
        raise ValueError("automaticResets.enabled must be an explicit boolean")
    if set(policy) - set(DEFAULT_POLICY):
        raise ValueError("unknown automaticResets policy field")
    result = {**DEFAULT_POLICY, **policy}
    days = number(result["expiresWithinDays"])
    if days is None or not 0 < days <= 365:
        raise ValueError("expiresWithinDays must be greater than zero and at most 365")
    for key in ("remainingPercentWithCredits", "remainingPercentWithoutCredits"):
        value = number(result[key])
        if value is None or not 0 <= value <= 10:
            raise ValueError("remaining percent thresholds must be between 0 and 10")
    if result["remainingPercentWithCredits"] > result["remainingPercentWithoutCredits"]:
        raise ValueError("the credit-buffer threshold cannot exceed the no-credit threshold")
    for key in ("minCheckIntervalMinutes", "maxCheckIntervalMinutes", "initialCheckIntervalMinutes"):
        if type(result[key]) is not int or not 5 <= result[key] <= 60:
            raise ValueError("check intervals must be integer minutes between 5 and 60")
    if not result["minCheckIntervalMinutes"] <= result["initialCheckIntervalMinutes"] <= result["maxCheckIntervalMinutes"]:
        raise ValueError("initial interval must be within the configured interval bounds")
    command = result["codexCommand"]
    if not isinstance(command, str) or not command.strip() or len(command) > 4096:
        raise ValueError("codexCommand must name one executable")
    if any(ord(char) < 32 for char in command) or command != command.strip():
        raise ValueError("invalid codexCommand")
    if not Path(command).is_absolute() and (Path(command).name != command or any(char in command for char in " /\\")):
        raise ValueError("codexCommand must be a bare command name or an absolute executable path")
    return result


def no_redirect(path: Path) -> None:
    """Check existing ancestors without resolving away redirecting components."""
    for item in reversed((path, *path.parents)):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise HookError("unsafe_state_path")
        if item == path and stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
            raise HookError("unsafe_state_path")


def open_file(path: Path, flags: int, mode: int = 0o600) -> int:
    no_redirect(path)
    descriptor = os.open(path, flags | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0), mode)
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        os.close(descriptor)
        raise HookError("unsafe_state_path")
    return descriptor


def read_json(path: Path, *, missing: Any = None) -> Any:
    try:
        descriptor = open_file(path, os.O_RDONLY)
    except FileNotFoundError:
        return missing
    with os.fdopen(descriptor, "rb") as stream:
        raw = stream.read(MAX_JSON_BYTES + 1)
    if len(raw) > MAX_JSON_BYTES:
        raise HookError("invalid_state")

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON object key")
            result[key] = value
        return result

    def invalid_constant(_value: str) -> Any:
        raise ValueError("non-JSON numeric constant")

    try:
        return json.loads(raw, object_pairs_hook=unique_object, parse_constant=invalid_constant)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise HookError("invalid_state") from exc


def write_state(path: Path, state: dict[str, Any]) -> None:
    encoded = json.dumps(state, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(encoded) > MAX_JSON_BYTES:
        raise HookError("invalid_state")
    no_redirect(path)
    temporary = path.with_name(".quota-" + uuid.uuid4().hex + ".tmp")
    try:
        descriptor = open_file(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        no_redirect(path)
        os.replace(temporary, path)
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


@contextmanager
def state_lock(directory: Path) -> Iterator[bool]:
    """Nonblocking OS lock, automatically released even after a process crash."""
    no_redirect(directory)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    no_redirect(directory)
    descriptor = open_file(directory / "owner.lock", os.O_RDWR | os.O_CREAT)
    acquired = False
    try:
        if os.name == "nt":
            import msvcrt
            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"\0")
            os.lseek(descriptor, 0, os.SEEK_SET)
            try:
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                acquired = True
            except OSError:
                pass
        else:
            import fcntl
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except BlockingIOError:
                pass
        yield acquired
    finally:
        if acquired:
            if os.name == "nt":
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def executable(command: str) -> str:
    if Path(command).is_absolute():
        if not Path(command).is_file():
            raise HookError("cli_unavailable")
        return command
    # Avoid implicit cwd lookup (especially on Windows) and relative PATH entries.
    for entry in os.get_exec_path():
        if not entry or not Path(entry).is_absolute():
            continue
        candidate = shutil.which(command, path=entry)
        if candidate and Path(candidate).is_absolute():
            return candidate
    raise HookError("cli_unavailable")


class Rpc:
    """Bounded JSONL connection to our own app-server helper or daemon proxy."""

    def __init__(self, command: str, home: Path) -> None:
        self.deadline = time.monotonic() + PROCESS_BUDGET
        self.messages: queue.Queue[Any] = queue.Queue(maxsize=64)
        self.sequence = 0
        self.process: subprocess.Popen[bytes] | None = None
        self.reader: threading.Thread | None = None
        env = os.environ.copy()
        env["CODEX_HOME"] = str(home)
        options: dict[str, Any] = {"cwd": str(home), "env": env}
        if os.name == "nt":
            options["creationflags"] = subprocess.CREATE_NO_WINDOW
        try:
            # This read-only discovery does not launch or stop a daemon.
            probe = subprocess.run(
                [command, "app-server", "daemon", "version"], stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2, check=False, **options,
            )
            args = [command, "app-server", "proxy"] if probe.returncode == 0 else [command, "app-server", "--listen", "stdio://"]
        except subprocess.TimeoutExpired:
            args = [command, "app-server", "--listen", "stdio://"]
        except OSError as exc:
            raise HookError("cli_unavailable") from exc
        try:
            self.process = subprocess.Popen(
                args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, bufsize=0, **options,
            )
        except OSError as exc:
            raise HookError("cli_unavailable") from exc
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        try:
            while True:
                line = self.process.stdout.readline(MAX_JSON_BYTES + 1)
                if not line:
                    break
                if len(line) > MAX_JSON_BYTES:
                    raise ValueError("oversized RPC response")
                message = json.loads(line)
                if not isinstance(message, dict):
                    raise ValueError("invalid RPC response")
                self.messages.put_nowait(message)
        except (ValueError, UnicodeError, OSError, queue.Full, RecursionError):
            try:
                self.messages.put_nowait(HookError("protocol_unavailable"))
            except queue.Full:
                pass
        finally:
            try:
                self.messages.put_nowait(None)
            except queue.Full:
                pass

    def send(self, message: dict[str, Any]) -> None:
        assert self.process is not None and self.process.stdin is not None
        if time.monotonic() >= self.deadline:
            raise HookError("query_unavailable")
        try:
            self.process.stdin.write((json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8"))
            self.process.stdin.flush()
        except OSError as exc:
            raise HookError("query_unavailable") from exc

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.sequence += 1
        request_id = self.sequence
        self.send({"id": request_id, "method": method, "params": params or {}})
        deadline = min(self.deadline, time.monotonic() + RPC_TIMEOUT)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise HookError("query_unavailable")
            try:
                response = self.messages.get(timeout=remaining)
            except queue.Empty as exc:
                raise HookError("query_unavailable") from exc
            if response is None or isinstance(response, Exception):
                raise HookError("protocol_unavailable")
            if "method" in response and "id" in response:
                # Close instead of taking part in credentials/approval ceremonies.
                raise HookError("protocol_unavailable")
            if response.get("id") != request_id:
                continue
            if "error" in response:
                raise HookError("query_unavailable")
            result = response.get("result")
            if not isinstance(result, dict):
                raise HookError("protocol_unavailable")
            return result

    def __enter__(self) -> Rpc:
        try:
            self.request("initialize", {"clientInfo": {"name": "lugui-quota-hook", "title": "Lugui quota hook", "version": "1"}})
            self.send({"method": "initialized", "params": {}})
        except BaseException:
            self.close()
            raise
        return self

    def close(self) -> None:
        if self.process is None:
            return
        process = self.process
        try:
            if process.stdin is not None:
                process.stdin.close()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1)
        finally:
            if self.reader is not None:
                self.reader.join(timeout=0.5)
            for stream in (process.stdin, process.stdout):
                if stream is not None:
                    stream.close()

    def __exit__(self, *_args: Any) -> None:
        self.close()


def account_key(response: dict[str, Any]) -> str:
    account = response.get("account")
    if not isinstance(account, dict) or account.get("type") != "chatgpt":
        raise HookError("identity_unavailable")
    email = account.get("email")
    if not isinstance(email, str) or not email.strip() or len(email) > 1024:
        raise HookError("identity_unavailable")
    return hashlib.sha256(("chatgpt:" + email.strip().casefold()).encode("utf-8")).hexdigest()


def credit_buffer(credits: Any) -> bool | None:
    if not isinstance(credits, dict) or type(credits.get("hasCredits")) is not bool or type(credits.get("unlimited")) is not bool:
        return None
    if credits["unlimited"]:
        return True if credits["hasCredits"] else None
    raw = credits.get("balance")
    if isinstance(raw, bool) or not isinstance(raw, (str, int, float)) or len(str(raw)) > 100:
        return None
    try:
        balance = Decimal(str(raw))
    except InvalidOperation:
        return None
    if not balance.is_finite() or balance < 0:
        return None
    if credits["hasCredits"] and balance > 0:
        return True
    if not credits["hasCredits"] and balance == 0:
        return False
    return None  # Contradictory fields are not a known balance.


def snapshot(response: dict[str, Any], now: float) -> dict[str, Any]:
    buckets = response.get("rateLimitsByLimitId")
    if buckets is not None:
        if not isinstance(buckets, dict) or not isinstance(buckets.get("codex"), dict):
            raise HookError("quota_unavailable")
        bucket = buckets["codex"]
    else:
        bucket = response.get("rateLimits")
    if not isinstance(bucket, dict) or bucket.get("limitId") != "codex":
        raise HookError("quota_unavailable")
    windows: dict[str, Any] = {}
    for name in ("primary", "secondary"):
        window = bucket.get(name)
        if not isinstance(window, dict):
            continue
        duration = number(window.get("windowDurationMins"))
        used = number(window.get("usedPercent"))
        resets = number(window.get("resetsAt"))
        if duration not in CORE_DURATIONS or used is None or not 0 <= used <= 100 or resets is None or resets <= now:
            continue
        key = str(int(duration))
        if key in windows:
            raise HookError("quota_unavailable")
        windows[key] = {"usedPercent": used, "remainingPercent": 100 - used, "resetsAt": resets}
    if not windows:
        raise HookError("quota_unavailable")
    reset_info = response.get("rateLimitResetCredits")
    credits: list[dict[str, Any]] = []
    details_known = False
    if isinstance(reset_info, dict):
        count = reset_info.get("availableCount")
        details = reset_info.get("credits")
        if type(count) is int and count >= 0 and isinstance(details, list):
            details_known = True
            ids: set[str] = set()
            for credit in details:
                if not isinstance(credit, dict):
                    continue
                credit_id = credit.get("id")
                expires = number(credit.get("expiresAt"))
                if not isinstance(credit_id, str) or not credit_id or len(credit_id) > 2048:
                    continue
                if credit_id in ids:
                    raise HookError("quota_unavailable")
                ids.add(credit_id)
                if credit.get("resetType") == "codexRateLimits" and credit.get("status") == "available" and expires is not None and expires > now and count > 0:
                    credits.append({"id": credit_id, "expiresAt": expires})
            # Count-only and contradictory rows never grant eligibility.
            if count == 0:
                credits = []
    return {"at": now, "windows": windows, "creditBuffer": credit_buffer(bucket.get("credits")), "resetDetailsKnown": details_known, "resetCredits": credits}


def threshold_for(current: dict[str, Any], policy: dict[str, Any]) -> float | None:
    buffer = current.get("creditBuffer")
    if type(buffer) is not bool:
        return None
    return float(policy["remainingPercentWithCredits" if buffer else "remainingPercentWithoutCredits"])


def eligible(current: dict[str, Any], policy: dict[str, Any], now: float) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    threshold = threshold_for(current, policy)
    if threshold is None or not current.get("resetDetailsKnown"):
        return [], {}
    credits = sorted(
        (credit for credit in current["resetCredits"] if 0 < credit["expiresAt"] - now <= policy["expiresWithinDays"] * 86400),
        key=lambda credit: (credit["expiresAt"], credit["id"]),
    )
    triggers = {
        duration: {**window, "threshold": threshold}
        for duration, window in current["windows"].items()
        if window["resetsAt"] > now and window["remainingPercent"] <= threshold
    }
    return credits, triggers


def recovered(attempt: dict[str, Any], current: dict[str, Any]) -> bool:
    triggers = attempt.get("triggers")
    if not isinstance(triggers, dict) or not triggers:
        raise HookError("invalid_state")
    for duration, trigger in triggers.items():
        if not isinstance(trigger, dict) or duration not in ("300", "10080"):
            raise HookError("invalid_state")
        threshold = number(trigger.get("threshold"))
        if threshold is None or not 0 <= threshold <= 10:
            raise HookError("invalid_state")
        window = current["windows"].get(duration)
        if not window or window["remainingPercent"] <= threshold:
            return False
    return True


def check_interval(previous: Any, current: dict[str, Any], policy: dict[str, Any], now: float) -> float:
    """Estimate time to threshold using comparable observations; always clamp."""
    minimum = policy["minCheckIntervalMinutes"] * 60
    maximum = policy["maxCheckIntervalMinutes"] * 60
    initial = policy["initialCheckIntervalMinutes"] * 60
    credits = current["resetCredits"]
    soon = [credit for credit in credits if 0 < credit["expiresAt"] - now <= policy["expiresWithinDays"] * 86400]
    if not soon:
        return maximum  # No soon-expiring known reset: avoid frequent quota polls.
    threshold = threshold_for(current, policy)
    if threshold is None:
        return maximum
    if any(window["remainingPercent"] <= threshold for window in current["windows"].values()):
        return minimum
    if not isinstance(previous, dict):
        return initial
    previous_at = number(previous.get("at"))
    if previous_at is None or not minimum / 2 <= now - previous_at <= 2 * maximum:
        return initial
    estimates: list[float] = []
    for duration, window in current["windows"].items():
        older = previous.get("windows", {}).get(duration) if isinstance(previous.get("windows"), dict) else None
        if not isinstance(older, dict) or older.get("resetsAt") != window["resetsAt"]:
            continue
        old_used = number(older.get("usedPercent"))
        if old_used is None:
            continue
        used_delta = window["usedPercent"] - old_used
        if used_delta > 0:
            estimates.append((window["remainingPercent"] - threshold) * (now - previous_at) / used_delta * 0.5)
    interval = min(estimates) if estimates else initial
    return min(maximum, max(minimum, interval))


def notice(state: dict[str, Any], code: str, message: str) -> str | None:
    if state.get("lastNotice") == code:
        return None
    state["lastNotice"] = code
    return message


def read_policy(home: Path) -> dict[str, Any] | None:
    preferences = read_json(home / "lugui-agent-pack" / "preferences.json")
    if preferences is None:
        return None
    if not isinstance(preferences, dict) or type(preferences.get("schemaVersion")) is not int or preferences["schemaVersion"] != 1:
        raise HookError("invalid_policy")
    try:
        return validate_policy(preferences.get("automaticResets"))
    except ValueError as exc:
        raise HookError("invalid_policy") from exc


def run_locked(home: Path, policy: dict[str, Any], state_path: Path) -> str | None:
    state = read_json(state_path, missing={"schemaVersion": STATE_SCHEMA, "accounts": {}})
    if not isinstance(state, dict) or type(state.get("schemaVersion")) is not int or state["schemaVersion"] != STATE_SCHEMA or not isinstance(state.get("accounts"), dict):
        raise HookError("invalid_state")
    now = time.time()
    fingerprint = hashlib.sha256(json.dumps(policy, sort_keys=True).encode("utf-8")).hexdigest()
    next_check = number(state.get("nextCheckAt"))
    checked_at = number(state.get("checkedAt"))
    # Future/rolled-back clocks and impossible schedules invalidate cached timing.
    if state.get("policyHash") == fingerprint and checked_at is not None and next_check is not None and checked_at <= now < next_check <= checked_at + 3600:
        return None
    accounts = state["accounts"]
    message: str | None = None
    active: dict[str, Any] | None = None
    fresh: dict[str, Any] | None = None
    try:
        with Rpc(executable(policy["codexCommand"]), home) as rpc:
            key = account_key(rpc.request("account/read", {"refreshToken": False}))
            if key not in accounts:
                if len(accounts) >= 32:
                    raise HookError("state_capacity")
                accounts[key] = {}
            active = accounts[key]
            if not isinstance(active, dict):
                raise HookError("invalid_state")
            previous = active.get("snapshot") if state.get("activeAccount") == key else None
            state["activeAccount"] = key
            fresh = snapshot(rpc.request("account/rateLimits/read"), time.time())
            if account_key(rpc.request("account/read", {"refreshToken": False})) != key:
                raise HookError("identity_changed")
            attempt = active.get("attempt")
            just_recovered = False
            if attempt is not None:
                if not isinstance(attempt, dict) or attempt.get("account") != key or attempt.get("status") not in ({"pending"} | TERMINAL_OUTCOMES):
                    raise HookError("invalid_state")
                if recovered(attempt, fresh):
                    del active["attempt"]
                    attempt = None
                    just_recovered = True
                elif attempt["status"] != "pending":
                    active["snapshot"] = fresh
                    state["checkedAt"] = now
                    state["nextCheckAt"] = now + check_interval(previous, fresh, policy, now)
                    state["policyHash"] = fingerprint
                    write_state(state_path, state)
                    return notice_and_save(state_path, state, "awaiting_recovery:" + key, "Automatic quota reset is waiting for verified quota recovery before another redemption.")
            candidates, triggers = eligible(fresh, policy, time.time())
            if just_recovered:
                candidates = []  # Observe recovery before considering any later credit.
            if attempt is not None:
                pending_id = attempt.get("creditId")
                pending_key = attempt.get("idempotencyKey")
                try:
                    valid_uuid = isinstance(pending_key, str) and str(uuid.UUID(pending_key)) == pending_key
                except ValueError:
                    valid_uuid = False
                if not valid_uuid or not isinstance(pending_id, str) or not pending_id:
                    raise HookError("invalid_state")
                candidates = [credit for credit in candidates if credit["id"] == pending_id and credit["expiresAt"] == attempt.get("expiresAt")]
            if candidates and triggers:
                selected = candidates[0]
                # Fresh verification for every new attempt and every uncertain retry.
                if account_key(rpc.request("account/read", {"refreshToken": False})) != key:
                    raise HookError("identity_changed")
                verified = snapshot(rpc.request("account/rateLimits/read"), time.time())
                if account_key(rpc.request("account/read", {"refreshToken": False})) != key:
                    raise HookError("identity_changed")
                # Consent can be revoked while this process is querying metadata.
                if read_policy(home) != policy:
                    raise HookError("policy_changed")
                fresh = verified
                verified_credits, verified_triggers = eligible(verified, policy, time.time())
                exact_credit = any(credit == selected for credit in verified_credits)
                if attempt is not None and recovered(attempt, verified):
                    del active["attempt"]
                    attempt = None
                    exact_credit = False
                    just_recovered = True
                elif attempt is not None and not set(attempt["triggers"]).intersection(verified_triggers):
                    # A different exhausted window cannot authorize replaying an
                    # unresolved attempt whose original window is now unknown.
                    exact_credit = False
                if exact_credit and verified_triggers:
                    if attempt is None:
                        attempt = {
                            "account": key, "creditId": selected["id"], "expiresAt": selected["expiresAt"],
                            "idempotencyKey": str(uuid.uuid4()), "status": "pending", "createdAt": time.time(),
                            "triggers": verified_triggers,
                        }
                        active["attempt"] = attempt
                    # Write-ahead journal: no RPC may be sent until this is durable.
                    active["snapshot"] = fresh
                    state["checkedAt"] = now
                    state["nextCheckAt"] = now + policy["minCheckIntervalMinutes"] * 60
                    state["policyHash"] = fingerprint
                    write_state(state_path, state)
                    # A slow filesystem, account check, or crossing reset/expiry
                    # boundary cannot turn a journalled stale read into authority.
                    final_now = time.time()
                    final_credits, final_triggers = eligible(fresh, policy, final_now)
                    if not 0 <= final_now - fresh["at"] <= RPC_TIMEOUT or not set(attempt["triggers"]).intersection(final_triggers) or selected not in final_credits:
                        raise HookError("verification_expired")
                    if read_policy(home) != policy:
                        raise HookError("policy_changed")
                    result = rpc.request("account/rateLimitResetCredit/consume", {
                        "idempotencyKey": attempt["idempotencyKey"], "creditId": attempt["creditId"],
                    })
                    outcome = result.get("outcome")
                    if outcome not in TERMINAL_OUTCOMES:
                        raise HookError("redemption_uncertain")
                    attempt["status"] = outcome
                    write_state(state_path, state)  # Keep terminal latch even if refresh fails.
                    if outcome in {"reset", "alreadyRedeemed"}:
                        message = notice(state, "redeemed:" + attempt["idempotencyKey"], "An opted-in automatic Codex quota reset completed.")
                    # Never infer updated windows from the consume result.
                    refreshed = snapshot(rpc.request("account/rateLimits/read"), time.time())
                    if account_key(rpc.request("account/read", {"refreshToken": False})) != key:
                        raise HookError("identity_changed")
                    fresh = refreshed
                    if recovered(attempt, fresh):
                        del active["attempt"]
            elif attempt is not None:
                message = notice(state, "pending:" + key, "An automatic quota reset has an unresolved attempt; no different reset will be spent until quota recovery is verified.")
            elif threshold_for(fresh, policy) is None or not fresh["resetDetailsKnown"]:
                message = notice(state, "metadata:" + key, "Automatic quota reset skipped: credit balance or reset details are unavailable.")
            else:
                state.pop("lastNotice", None)
            active["snapshot"] = fresh
            state["checkedAt"] = now
            state["nextCheckAt"] = now + check_interval(previous, fresh, policy, now)
            # Never cache beyond a known quota-window reset or expiry-boundary.
            boundaries = [window["resetsAt"] for window in fresh["windows"].values()]
            for credit in fresh["resetCredits"]:
                boundaries.extend([credit["expiresAt"], credit["expiresAt"] - policy["expiresWithinDays"] * 86400])
            future = [boundary for boundary in boundaries if boundary > time.time()]
            if future:
                state["nextCheckAt"] = min(state["nextCheckAt"], min(future))
            state["policyHash"] = fingerprint
    except HookError as exc:
        # A pending attempt remains in this state, including on transport errors.
        code = str(exc)
        state["checkedAt"] = time.time()
        state["nextCheckAt"] = state["checkedAt"] + policy["maxCheckIntervalMinutes"] * 60
        state["policyHash"] = fingerprint
        failure = notice(state, "failure:" + code, "Automatic quota reset paused: " + ERROR_MESSAGES.get(code, "metadata is unavailable") + ".")
        message = message or failure
    write_state(state_path, state)
    return message


def notice_and_save(path: Path, state: dict[str, Any], code: str, message: str) -> str | None:
    result = notice(state, code, message)
    write_state(path, state)
    return result


ERROR_MESSAGES = {
    "cli_unavailable": "the configured Codex CLI is unavailable",
    "protocol_unavailable": "the CLI account protocol is unavailable",
    "query_unavailable": "the account query did not complete",
    "identity_unavailable": "a ChatGPT account email is unavailable",
    "identity_changed": "the signed-in account changed during verification",
    "policy_changed": "the opt-in policy changed during verification",
    "quota_unavailable": "eligible core quota metadata is unavailable",
    "redemption_uncertain": "the redemption outcome is uncertain; the original attempt was retained",
    "verification_expired": "the fresh verification expired before redemption",
    "invalid_state": "the durable state needs inspection",
    "state_capacity": "the account state limit was reached",
    "invalid_policy": "the saved opt-in policy is invalid",
    "unsafe_state_path": "a state path redirects to another location",
}


def configuration_failure(home: Path, code: str) -> str | None:
    """Report a configuration failure once without overwriting a damaged journal."""
    if code not in ERROR_MESSAGES:
        return None
    directory = home / "lugui-agent-pack" / "usage-quota"
    try:
        with state_lock(directory) as acquired:
            if not acquired:
                return None
            signature: list[Any] = [code]
            for path in (home / "lugui-agent-pack" / "preferences.json", directory / "state.json"):
                no_redirect(path)
                try:
                    info = path.stat()
                    signature.append([info.st_mtime_ns, info.st_size])
                except FileNotFoundError:
                    signature.append(None)
            fingerprint = hashlib.sha256(json.dumps(signature).encode("utf-8")).hexdigest()
            path = directory / "diagnostic.json"
            previous = read_json(path, missing={})
            if isinstance(previous, dict) and previous.get("failure") == fingerprint:
                return None
            write_state(path, {"schemaVersion": 1, "failure": fingerprint})
            return "Automatic quota reset paused: " + ERROR_MESSAGES[code] + "."
    except (HookError, OSError, ValueError):
        return None


def hook_event() -> str | None:
    """No event, oversized input or unknown event means no reads or redemption."""
    if sys.stdin.isatty():
        return None
    incoming: queue.Queue[bytes | None] = queue.Queue(maxsize=1)

    def read_input() -> None:
        # Raw OS reads avoid buffered-stdin locks during interpreter shutdown if
        # a malformed caller leaves the input pipe open indefinitely.
        parts: list[bytes] = []
        total = 0
        try:
            while total <= MAX_JSON_BYTES:
                chunk = os.read(sys.stdin.fileno(), min(65536, MAX_JSON_BYTES + 1 - total))
                if not chunk:
                    break
                parts.append(chunk)
                total += len(chunk)
            incoming.put_nowait(b"".join(parts))
        except (OSError, ValueError):
            incoming.put_nowait(None)

    threading.Thread(target=read_input, daemon=True).start()
    try:
        raw = incoming.get(timeout=1)
    except queue.Empty:
        return None
    if raw is None:
        return None
    if len(raw) > MAX_JSON_BYTES:
        return None
    try:
        event = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError):
        return None
    if not isinstance(event, dict):
        return None
    name = event.get("hook_event_name")
    return name if isinstance(name, str) and name in EVENTS else None


def main() -> int:
    event = hook_event()
    if event is None:
        return 0
    argv = sys.argv[1:]
    if argv and (len(argv) != 2 or argv[0] != "--codex-home"):
        return 0
    home = Path(argv[1]) if argv else Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    if not home.is_absolute():
        return 0
    message = None
    try:
        no_redirect(home)
        if not home.is_dir():
            return 0
        policy = read_policy(home)
        if policy is None or not policy["enabled"]:
            return 0
        directory = home / "lugui-agent-pack" / "usage-quota"
        with state_lock(directory) as acquired:
            if acquired:
                message = run_locked(home, policy, directory / "state.json")
    except HookError as exc:
        message = configuration_failure(home, str(exc))
    except Exception:
        # No stack traces, raw account data or unrepeatable warnings. Invalid or
        # unwritable policy/state fails closed without spending or blocking Codex.
        return 0
    if message:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": message}}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
