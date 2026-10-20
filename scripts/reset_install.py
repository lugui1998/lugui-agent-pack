"""Static planning of opt-in reset preferences and owned Codex hook entries.

No hook, Codex command, authentication, usage request or redemption runs while planning.
The source hook is loaded only to validate policy data through its pure API.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import re
import shlex
import stat
import sys
from pathlib import Path


OVERRIDES = {
    "reset_expiry_days": "expiresWithinDays",
    "reset_natural_wait_days": "naturalResetWaitDays",
    "reset_credit_reserve": "creditReserve",
    "reset_remaining_with_credits": "remainingPercentWithCredits",
    "reset_remaining_without_credits": "remainingPercentWithoutCredits",
    "reset_min_check_minutes": "minCheckIntervalMinutes",
    "reset_max_check_minutes": "maxCheckIntervalMinutes",
    "reset_initial_check_minutes": "initialCheckIntervalMinutes",
    "reset_codex_command": "codexCommand",
}
EVENTS = ("UserPromptSubmit", "PostToolUse")


def windows_launcher_command(path: Path) -> str:
    """Quote the -File path for both cmd.exe and PowerShell command runners.

    Codex does not document which Windows shell processes commandWindows.
    Reject expansion characters whose meaning differs between those shells.
    """
    value = str(path)
    if any(character in value for character in '%!$`"\r\n'):
        raise ValueError("Windows hook launcher path contains unsupported shell expansion characters (% ! $ ` or quotes/newlines)")
    return f'powershell.exe -NoLogo -NoProfile -NonInteractive -File "{value}"'


def render_windows_launcher(source: Path, executable: str, script: Path, home: Path) -> bytes:
    """Render a shipped template; Windows PowerShell needs a BOM for UTF-8 paths."""
    result = source.read_text(encoding="utf-8")
    values = {
        "@@PYTHON@@": executable,
        "@@HOOK@@": str(script),
        "@@CODEX_HOME@@": str(home),
    }
    # One replacement pass keeps literal placeholder-like text in a path intact.
    result = re.sub(r"@@(?:PYTHON|HOOK|CODEX_HOME)@@", lambda match: "'" + values[match[0]].replace("'", "''") + "'", result)
    return result.encode("utf-8-sig")


def require_plain_preferences(path: Path) -> None:
    """Match the runtime policy reader's refusal of redirected/shared files."""
    for item in (path, *path.parents):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("preferences path contains a symlink or reparse point")
        if item == path and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
            raise ValueError("preferences path must be a regular file with one link")


def read_object(path: Path) -> dict:
    """Reject duplicate keys as well as malformed JSON before any write."""
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key {key!r} in {path}")
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError(f"non-JSON numeric constant {value!r} in {path}")

    data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_pairs, parse_constant=invalid_constant)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def policy_api(source: Path):
    spec = importlib.util.spec_from_file_location("lugui_usage_quota_policy", source)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load policy validation API from {source}")
    module = importlib.util.module_from_spec(spec)
    # The bundled hook's import contract is side-effect free; main is never called.
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module.DEFAULT_POLICY, module.validate_policy


def prompt_policy(defaults: dict) -> dict | None:
    print("Enabling automatic reset management installs its two hooks and attempts to trust those exact definitions through Codex.")
    print("Automatic resets may redeem an available reset using your saved policy. "
          "Installation does not check usage or redeem a reset.")
    print("Defaults: expire within 5 days; remaining quota 0% with a balance of at least 200 credits or 1% below that reserve; "
          "skip redemption if normal recovery is within 2 days unless the banked reset expires strictly before recovery; "
          "checks start at 30 minutes and stay between 5 and 60 minutes.")
    print("Set naturalResetWaitDays to 0 to disable only the normal recovery guard.")
    print("Set creditReserve to 0 to treat any positive credit balance as a buffer. "
          "When the normal recovery guard blocks an otherwise eligible reset, quota is at its trigger, "
          "and balance is below the reserve, the agent asks whether to use the reset or wait. "
          "Eligible cases, including a reset expiring before normal recovery, remain automatic.")
    choice = input("Automatic resets: [e]nable defaults, [c]ustomize, [d]isable: ").strip().lower()
    if choice in ("d", "disable"):
        return {**defaults, "enabled": False}
    if choice not in ("e", "enable", "c", "customize"):
        return None
    result = {**defaults, "enabled": True}
    if choice in ("c", "customize"):
        for key in defaults:
            if key == "enabled":
                continue
            value = input(f"{key} [{defaults[key]}]: ").strip()
            if value:
                if key == "codexCommand":
                    result[key] = value
                elif key.endswith("IntervalMinutes"):
                    result[key] = int(value)
                else:
                    result[key] = float(value)
    return result


def validate_hooks(document: dict) -> dict:
    hooks = document.get("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("hooks.json hooks must be an object")
    for event, entries in hooks.items():
        if not isinstance(entries, list):
            raise ValueError(f"hooks.json {event} must be a list")
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("hooks"), list):
                raise ValueError(f"hooks.json {event} requires hook groups with a hooks list")
            for handler in entry["hooks"]:
                if not isinstance(handler, dict) or not isinstance(handler.get("type"), str):
                    raise ValueError(f"hooks.json {event} has a malformed handler")
                if handler["type"] == "command" and not isinstance(handler.get("command"), str):
                    raise ValueError(f"hooks.json {event} command handler requires a command string")
                if "commandWindows" in handler and not isinstance(handler["commandWindows"], str):
                    raise ValueError(f"hooks.json {event} commandWindows must be a string")
    return hooks


def plan_hooks(installer, record_type, source: Path, policy: dict) -> None:
    enabled = policy["enabled"]
    path = installer.home / "hooks.json"
    script = installer.home / "hooks" / "usage_quota_check.py"
    if not enabled and not any(
        installer.owned("reset-hook", path, f"automatic-resets:{event}") for event in EVENTS
    ):
        installer.action("SKIP", path, "automatic resets disabled; no owned reset handlers require cleanup")
        return
    if not installer.require_target(path) or not installer.require_target(script):
        return
    if path.exists() and not path.is_file():
        installer.conflict(path, "hook configuration is not a regular file")
        return
    original = read_object(path) if path.exists() else {}
    hooks = validate_hooks(original)
    result = copy.deepcopy(original)
    result_hooks = copy.deepcopy(hooks)
    executable = str(Path(sys.executable).resolve())
    arguments = [executable, str(script), "--codex-home", str(installer.home)]
    command = shlex.join(arguments)
    handler = {"type": "command", "command": command, "timeout": 60}
    launcher = installer.home / "hooks" / "usage_quota_check.ps1"
    launcher_source = None
    if sys.platform == "win32" and enabled:
        handler["commandWindows"] = windows_launcher_command(launcher)
        launcher_source = installer.manifest_source("usageQuotaWindowsLauncher")
    desired = {"hooks": [handler]}
    records = []
    removals = []
    for event in EVENTS:
        owner = f"automatic-resets:{event}"
        old = installer.owned("reset-hook", path, owner)
        entries = hooks.get(event, [])
        matches = [index for index, item in enumerate(entries) if old and installer.value_hash(item) == old.hash]
        collisions = [index for index, item in enumerate(entries) if any(
            name in json.dumps(item) for name in ("usage_quota_check.py", "usage_quota_check.ps1")
        )]
        if old and len(matches) != 1:
            installer.conflict(path, f"owned {event} handler is missing, duplicated or modified")
            continue
        if any(index not in matches for index in collisions):
            installer.conflict(path, f"unowned {event} usage hook collision; existing handlers remain untouched")
            continue
        updated = list(entries)
        if matches:
            if enabled:
                updated[matches[0]] = desired
            else:
                del updated[matches[0]]
                removals.append(owner)
        elif enabled:
            updated.append(desired)
        if updated != entries:
            result_hooks[event] = updated
            result["hooks"] = result_hooks
        if enabled:
            records.append(record_type("reset-hook", str(path), installer.value_hash(desired), "hook-entry", owner))

    if enabled:
        installer.plan_file("reset-hook-script", source, script)
        payloads = [("reset-hook-script", str(script), installer.file_hash(source))]
        if launcher_source is not None:
            launcher_payload = render_windows_launcher(launcher_source, executable, script, installer.home)
            installer.plan_file(
                "reset-hook-launcher", launcher_source, launcher,
                payload=launcher_payload,
            )
            payloads.append(("reset-hook-launcher", str(launcher), installer.sha_bytes(launcher_payload)))
        from hook_trust import TrustIntent

        installer.reset_trust_intent = TrustIntent(
            json.dumps(policy, sort_keys=True), json.dumps(desired, sort_keys=True), tuple(payloads),
        )
        installer.action("TRUST", path, "after successful apply and state save, trust only these two owned reset hooks through the Codex API; no hook runs")
    elif script.exists():
        installer.notice(script, "disabled hook script is retained dormant; no script is executed during installation")
    if result == original:
        installer.pending_state.extend(records)
        return
    # Disabling a fresh installation must not create an empty hooks.json.
    if not enabled and not path.exists():
        return
    payload = (json.dumps(result, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    expected = installer.file_hash(path) if path.exists() else None
    installer.plan_backup(path)

    def write_hooks():
        installer.assert_target(path)
        current = installer.file_hash(path) if path.exists() else None
        if current != expected:
            raise ValueError("hooks.json changed after preflight; re-plan before applying")
        installer.replace_bytes(path, payload, path.exists())
        installer.state = [item for item in installer.state if not (
            item.kind == "reset-hook" and item.path == str(path) and item.source in removals
        )]
        return records

    installer.add_operation("MERGE", path, "merge only unchanged owned reset handlers; preserve unrelated hooks and metadata", write_hooks)


def plan_automatic_resets(installer, record_type) -> None:
    args = installer.args
    if args.scope != "global":
        return
    path = installer.home / "lugui-agent-pack" / "preferences.json"
    if not installer.require_target(path):
        return
    try:
        source = installer.manifest_source("usageQuotaHook")
        defaults, validate = policy_api(source)
        require_plain_preferences(path)
        original = read_object(path) if path.exists() else {}
        if path.exists() and (type(original.get("schemaVersion")) is not int or original["schemaVersion"] != 1):
            raise ValueError("preferences.json requires schemaVersion 1")
        saved = validate(original["automaticResets"]) if "automaticResets" in original else None
        policy = dict(saved) if saved is not None else None
        choice = args.automatic_resets
        if choice in ("enable", "disable"):
            policy = dict(saved if saved is not None else defaults)
            policy["enabled"] = choice == "enable"
        elif saved is None:
            if choice == "ask" and sys.stdin.isatty() and sys.stdout.isatty():
                policy = prompt_policy(defaults)
            if policy is None:
                installer.choices += 1
                installer.action("PROMPT", path, "choose --automatic-resets enable or disable; AI installs must ask the user and pass their explicit choice")
                return
        for argument, key in OVERRIDES.items():
            value = getattr(args, argument)
            if value is not None:
                policy[key] = value
        policy = validate(policy)
        result = {**original, "schemaVersion": 1, "automaticResets": policy}
        if result != original:
            payload = (json.dumps(result, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
            expected = installer.file_hash(path) if path.exists() else None
            installer.plan_backup(path)

            def write_preferences():
                installer.assert_target(path)
                current = installer.file_hash(path) if path.exists() else None
                if current != expected:
                    raise ValueError("preferences.json changed after preflight; re-plan before applying")
                return installer.write_and_record(path, payload, path.exists(), [
                    record_type("reset-policy", str(path), installer.value_hash(policy), "user-preferences", "automaticResets")
                ])

            installer.add_operation("UPDATE" if path.exists() else "ADD", path, "save explicit reset policy; preserve other preference keys; no reset is redeemed", write_preferences)
        else:
            installer.action("SKIP", path, "preserve the saved reset choice and parameters")
        plan_hooks(installer, record_type, source, policy)
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, EOFError) as error:
        installer.conflict(path, f"automatic reset preflight failed: {error}")
