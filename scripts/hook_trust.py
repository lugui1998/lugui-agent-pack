"""Register trust for exactly the installed reset hooks through Codex's public API.

Only called after successful opt-in installation and ownership-state saving.
The opaque trust key/hash come from hooks/list, as in Codex's tui/hooks_rpc.rs.
No hook execution, account request, session or turn is part of this protocol.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from reset_install import EVENTS, read_object, require_plain_preferences, validate_hooks


RPC_TIMEOUT = 5
PROCESS_BUDGET = 30
MAX_RESPONSE = 2 * 1024 * 1024
ALLOWED_METHODS = frozenset(("initialize", "hooks/list", "config/read", "config/batchWrite"))


class TrustError(ValueError):
    """A controlled diagnostic that never includes untrusted server output."""


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate protocol key")
        result[key] = value
    return result


def invalid_constant(_value):
    raise ValueError("non-JSON protocol constant")


@dataclass(frozen=True)
class TrustIntent:
    policy_json: str
    group_json: str
    payloads: tuple[tuple[str, str, str], ...]


class TrustRpc:
    """Short-lived stdio client, reusing a running daemon through its proxy."""

    def __init__(self, command: str, home: Path):
        command = resolve_executable(command)
        self.deadline = time.monotonic() + PROCESS_BUDGET
        self.messages = queue.Queue(maxsize=64)
        self.sequence = 0
        self.process = None
        self.reader = None
        env = {**os.environ, "CODEX_HOME": str(home)}
        options = {"cwd": str(home), "env": env}
        if os.name == "nt":
            options["creationflags"] = subprocess.CREATE_NO_WINDOW
        try:
            probe = subprocess.run(
                [command, "app-server", "daemon", "version"],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=2, check=False, **options,
            )
            args = [command, "app-server", "proxy"] if probe.returncode == 0 else [command, "app-server", "--listen", "stdio://"]
        except subprocess.TimeoutExpired as error:
            # An uncertain daemon probe must not start a second service.
            raise TrustError("Codex daemon discovery timed out") from error
        except OSError as error:
            raise TrustError("Codex executable is unavailable") from error
        try:
            self.process = subprocess.Popen(
                args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                bufsize=0, **options,
            )
        except OSError as error:
            raise TrustError("Codex trust API could not be started") from error
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        try:
            while True:
                line = self.process.stdout.readline(MAX_RESPONSE + 1)
                if not line:
                    break
                if len(line) > MAX_RESPONSE:
                    raise ValueError()
                value = json.loads(line, object_pairs_hook=unique_object, parse_constant=invalid_constant)
                if not isinstance(value, dict):
                    raise ValueError()
                self.messages.put_nowait(value)
        except (ValueError, OSError, UnicodeError, RecursionError, queue.Full):
            pass
        finally:
            try:
                self.messages.put_nowait(None)
            except queue.Full:
                pass

    def _send(self, message):
        complete = queue.Queue(maxsize=1)

        def write():
            try:
                self.process.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
                self.process.stdin.flush()
                complete.put_nowait(True)
            except (OSError, ValueError):
                complete.put_nowait(False)

        writer = threading.Thread(target=write, daemon=True)
        writer.start()
        try:
            if not complete.get(timeout=max(0, min(RPC_TIMEOUT, self.deadline - time.monotonic()))):
                raise TrustError("Codex trust API pipe closed")
        except queue.Empty as error:
            raise TrustError("Codex trust API write timed out") from error

    def request(self, method, params):
        if method not in ALLOWED_METHODS:
            raise TrustError("request outside the hook trust protocol refused")
        self.sequence += 1
        self._send({"id": self.sequence, "method": method, "params": params})
        deadline = min(self.deadline, time.monotonic() + RPC_TIMEOUT)
        while True:
            try:
                message = self.messages.get(timeout=max(0, deadline - time.monotonic()))
            except queue.Empty as error:
                raise TrustError("Codex trust API response timed out") from error
            if not isinstance(message, dict) or ("method" in message and "id" in message):
                raise TrustError("unsupported Codex trust API response")
            if "method" in message:
                continue  # Notifications never authorize another request.
            if message.get("id") != self.sequence:
                raise TrustError("unexpected Codex trust API response id")
            if "error" in message:
                raise TrustError("Codex rejected the trust request (unsupported API or config conflict)")
            if not isinstance(message.get("result"), dict):
                raise TrustError("malformed Codex trust API result")
            return message["result"]

    def __enter__(self):
        try:
            self.request("initialize", {"clientInfo": {"name": "lugui-hook-installer", "version": "1"}})
            self._send({"method": "initialized", "params": {}})
            return self
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.process is None:
            return
        # Terminate only this task's child/proxy, never the shared daemon.
        try:
            self.process.terminate()
            try:
                self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=1)
        finally:
            if self.reader is not None:
                self.reader.join(timeout=1)
            for stream in (self.process.stdin, self.process.stdout):
                if stream is not None:
                    stream.close()

    def __exit__(self, *_args):
        self.close()


def resolve_executable(command):
    """Resolve an explicit file or absolute PATH directory, never implicit cwd."""
    path = Path(command)
    if path.is_absolute():
        if path.is_file():
            return str(path)
        raise TrustError("configured Codex executable is unavailable")
    if path.name != command or any(separator in command for separator in ("/", "\\")):
        raise TrustError("relative Codex executable paths are not supported")
    suffixes = [""]
    if os.name == "nt" and not path.suffix:
        suffixes.extend(os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(";"))
    for directory in os.get_exec_path():
        if directory and Path(directory).is_absolute():
            for suffix in suffixes:
                candidate = Path(directory) / (command + suffix)
                if candidate.is_file() and os.access(candidate, os.X_OK):
                    return str(candidate)
    raise TrustError("Codex executable is unavailable on the absolute PATH")


def same_path(value, expected):
    return isinstance(value, str) and Path(value).is_absolute() and Path(value) == expected


def plain_bytes(path):
    require_plain_preferences(path)
    return path.read_bytes() if path.exists() else None


def verify_installation(installer, intent):
    if installer.args.scope != "global" or not installer.args.apply or installer.conflicts or installer.choices:
        raise TrustError("trust requires a successful opted-in global apply")
    policy_path = installer.home / "lugui-agent-pack" / "preferences.json"
    require_plain_preferences(policy_path)
    preferences = read_object(policy_path)
    policy = json.loads(intent.policy_json)
    if (preferences.get("schemaVersion") != 1 or policy.get("enabled") is not True
            or preferences.get("automaticResets") != policy):
        raise TrustError("saved reset policy changed; trust was not granted")
    require_plain_preferences(installer.state_path)
    saved_state = installer.state_path.read_bytes()
    for kind, name, wanted in intent.payloads:
        path = Path(name)
        installer.assert_target(path)
        record = installer.owned(kind, path)
        if record is None or record.hash != wanted or installer.sha_bytes(plain_bytes(path) or b"") != wanted:
            raise TrustError("installed reset payload changed or is not owned")
        if ("\t".join(("managed", record.kind, record.path, record.hash, record.mode, record.source, record.created))).encode() not in saved_state.splitlines():
            raise TrustError("installed reset payload ownership was not saved")
    hooks_path = installer.home / "hooks.json"
    require_plain_preferences(hooks_path)
    hooks = validate_hooks(read_object(hooks_path))
    group = json.loads(intent.group_json)
    for event in EVENTS:
        record = installer.owned("reset-hook", hooks_path, f"automatic-resets:{event}")
        if record is None or record.hash != installer.value_hash(group) or hooks.get(event, []).count(group) != 1:
            raise TrustError("installed reset handler changed, duplicated or is not owned")
        if ("\t".join(("managed", record.kind, record.path, record.hash, record.mode, record.source, record.created))).encode() not in saved_state.splitlines():
            raise TrustError("installed reset handler ownership was not saved")
    return policy, group["hooks"][0]


def select_hooks(response, home, handler):
    data = response.get("data")
    if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
        raise TrustError("unsupported hooks/list shape")
    entry = data[0]
    if not same_path(entry.get("cwd"), home) or entry.get("errors") != [] or not isinstance(entry.get("warnings"), list):
        raise TrustError("hooks/list could not resolve the installed home cleanly")
    hooks = entry.get("hooks")
    if not isinstance(hooks, list) or any(not isinstance(hook, dict) for hook in hooks):
        raise TrustError("malformed hooks/list metadata")
    command = handler.get("commandWindows", handler["command"]) if os.name == "nt" else handler["command"]
    matches = []
    for event in EVENTS:
        candidates = [hook for hook in hooks if (
            same_path(hook.get("sourcePath"), home / "hooks.json")
            and hook.get("eventName") == event[0].lower() + event[1:]
            and hook.get("handlerType") == "command" and hook.get("command") == command
        )]
        if len(candidates) != 1:
            raise TrustError("exact installed reset hook is missing or ambiguous")
        hook = candidates[0]
        if (hook.get("source") != "user" or hook.get("isManaged") is not False
                or hook.get("pluginId") is not None or hook.get("matcher") is not None
                or hook.get("async", False) is not False or hook.get("timeoutSec") != handler["timeout"]
                or type(hook.get("enabled")) is not bool
                or hook.get("trustStatus") not in ("untrusted", "modified", "trusted")):
            raise TrustError("reset hook metadata does not identify an ordinary owned user hook")
        for field in ("key", "currentHash"):
            value = hook.get(field)
            if not isinstance(value, str) or not value or len(value) > 2048 or any(ord(c) < 32 for c in value):
                raise TrustError("unsupported hook key or current hash")
        if sum(other.get("key") == hook["key"] for other in hooks) != 1:
            raise TrustError("ambiguous hook key")
        matches.append(hook)
    return matches


def config_version(response, path):
    layers = response.get("layers")
    if not isinstance(layers, list) or any(not isinstance(layer, dict) for layer in layers):
        raise TrustError("Codex cannot supply a safe config version")
    users = [layer for layer in layers if isinstance(layer.get("name"), dict)
             and layer["name"].get("type") == "user"
             and same_path(layer["name"].get("file"), path) and layer["name"].get("profile") is None]
    if (len(users) != 1 or not isinstance(users[0].get("version"), str) or not users[0]["version"]
            or users[0].get("disabledReason") is not None):
        raise TrustError("user config version is missing, disabled or ambiguous")
    return users[0]["version"]


def trust_installed_hooks(installer, intent, rpc_factory=None):
    """Best effort activation; installation remains usable when trust is unavailable."""
    try:
        policy, handler = verify_installation(installer, intent)
        config = installer.home / "config.toml"
        installer.assert_target(config)
        before = plain_bytes(config)
        factory = TrustRpc if rpc_factory is None else rpc_factory
        with factory(policy["codexCommand"], installer.home) as rpc:
            params = {"cwds": [str(installer.home)]}
            hooks = select_hooks(rpc.request("hooks/list", params), installer.home, handler)
            if any(hook["trustStatus"] != "trusted" for hook in hooks):
                version = config_version(rpc.request("config/read", {"includeLayers": True}), config)
                current = select_hooks(rpc.request("hooks/list", params), installer.home, handler)
                if current != hooks:
                    raise TrustError("hook metadata changed before trust registration")
                verify_installation(installer, intent)
                if plain_bytes(config) != before:
                    raise TrustError("config changed before trust registration")
                # Separate backup preserves any earlier installer backup from this run.
                if before is not None:
                    backup = installer.backup_path(config).with_name("before-hook-trust-" + installer.backup_path(config).name)
                    installer.assert_target(backup, installer.home)
                    require_plain_preferences(backup)
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    with backup.open("xb") as stream:
                        stream.write(before)
                    installer.action("BACKUP", config, str(backup))
                verify_installation(installer, intent)
                if plain_bytes(config) != before:
                    raise TrustError("config changed while backing up")
                update = {hook["key"]: {"trusted_hash": hook["currentHash"]}
                          for hook in hooks if hook["trustStatus"] != "trusted"}
                result = rpc.request("config/batchWrite", {
                    "edits": [{"keyPath": "hooks.state", "value": update, "mergeStrategy": "upsert"}],
                    "filePath": str(config), "expectedVersion": version, "reloadUserConfig": True,
                })
                if result.get("status") not in ("ok", "okOverridden") or not same_path(result.get("filePath"), config):
                    raise TrustError("trust write outcome could not be verified")
            verified = select_hooks(rpc.request("hooks/list", params), installer.home, handler)
            verify_installation(installer, intent)
            for old, new in zip(hooks, verified):
                if any(old[field] != new[field] for field in ("key", "currentHash", "enabled")) or new["trustStatus"] != "trusted":
                    raise TrustError("trust or enabled state could not be verified after registration")
        for event, hook in zip(EVENTS, verified):
            state = "trusted" if hook["enabled"] else "trusted-disabled"
            installer.action("TRUST", installer.home / "hooks.json", f"{event}: {state}; hook execution was not tested")
        return True
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as error:
        # Do not echo arbitrary server content, credentials or commands from errors.
        reason = str(error) if isinstance(error, TrustError) else "local verification or trust transport failed"
        installer.action("NOTICE", installer.home / "hooks.json", f"automatic reset activation-unverified: {reason}")
        handler = json.loads(intent.group_json)["hooks"][0]
        command = handler.get("commandWindows", handler["command"]) if os.name == "nt" else handler["command"]
        print(f"Manual activation: open Codex with CODEX_HOME={installer.home}, run /hooks, and review only these user hooks from {installer.home / 'hooks.json'}:")
        for event in EVENTS:
            print(f"  {event}: {command}")
        print("Trust those exact definitions if they match; preserve any intentionally disabled setting. Installation did not run a hook or check/reset account usage.")
        return False
