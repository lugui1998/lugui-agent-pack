"""Developer-only, offline JSONL fixture. Never invokes Codex or an account API.

--run-hook imports the real hook behind a strict process boundary; --serve is
the only child it can start. All fixture data and journals live under the fresh
test home. This file is not an installed hook or a Codex command replacement.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time


SCRIPT = Path(__file__).resolve()
HOOK = SCRIPT.parents[2] / "scripts" / "hooks" / "usage_quota_check.py"
SENTINEL = "offline-codex-only"
ALLOWED_METHODS = frozenset({
    "initialize", "initialized", "account/read", "account/rateLimits/read",
    "account/rateLimitResetCredit/consume",
})


class BoundaryViolation(BaseException):
    """Bypass the production hook's intentional catch-and-return behavior."""


def clean_environment(home: Path) -> dict[str, str]:
    # No tokens, inherited CODEX_HOME, login configuration or Python startup.
    env = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR") if key in os.environ}
    env.update({"CODEX_HOME": str(home), "HOME": str(home), "USERPROFILE": str(home),
                "TEMP": str(home), "TMP": str(home), "PATH": ""})
    return env


def read_json(path: Path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def append_log(home: Path, value) -> None:
    with (home / "requests.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value) + "\n")


def command(mode: str, home: Path) -> list[str]:
    return [sys.executable, "-I", "-B", str(SCRIPT), mode, str(home)]


def install_boundary(home: Path):
    """Capture one real launcher; deny every command except our exact fixture."""
    real_popen = subprocess.Popen
    expected = command("--serve", home)
    # Windows Popen audits its serialized command line, POSIX its argv list.
    audited_argv = subprocess.list2cmdline(expected) if os.name == "nt" else expected

    def audit(event, args):
        if event == "subprocess.Popen":
            executable, argv, cwd, env = args
            if executable != sys.executable or argv != audited_argv or cwd != str(home) or env != clean_environment(home):
                raise BoundaryViolation("non-fixture subprocess denied")
        if event.startswith("socket.") or event in {"os.system", "os.posix_spawn", "os.posix_spawnp"}:
            raise BoundaryViolation("network or alternate launcher denied")

    sys.addaudithook(audit)

    def guard_options(kwargs, *, probe=False):
        if kwargs.get("cwd") != str(home) or kwargs.get("env", {}).get("CODEX_HOME") != str(home):
            raise BoundaryViolation("hook home is outside the fixture")
        if kwargs.get("shell", False):
            raise BoundaryViolation("shell launch denied")
        if probe:
            if kwargs.get("stdin") != subprocess.DEVNULL or kwargs.get("stdout") != subprocess.DEVNULL or kwargs.get("stderr") != subprocess.DEVNULL:
                raise BoundaryViolation("unexpected probe IO")
        elif kwargs.get("stdin") != subprocess.PIPE or kwargs.get("stdout") != subprocess.PIPE or kwargs.get("stderr") != subprocess.DEVNULL:
            raise BoundaryViolation("unexpected server IO")

    def run(argv, **kwargs):
        if argv != [SENTINEL, "app-server", "daemon", "version"]:
            raise BoundaryViolation("unknown probe action denied")
        guard_options(kwargs, probe=True)
        append_log(home, {"fixture": "probe", "argv": argv})
        scenario = read_json(home / "scenario.json")
        return subprocess.CompletedProcess(argv, 0 if scenario.get("daemon") else 1)

    def popen(argv, **kwargs):
        if argv not in ([SENTINEL, "app-server", "proxy"], [SENTINEL, "app-server", "--listen", "stdio://"]):
            raise BoundaryViolation("unknown server action denied")
        guard_options(kwargs)
        append_log(home, {"fixture": "launch", "argv": argv})
        options = {"stdin": subprocess.PIPE, "stdout": subprocess.PIPE,
                   "stderr": subprocess.DEVNULL, "bufsize": 0, "cwd": str(home),
                   "env": clean_environment(home)}
        if os.name == "nt":
            options["creationflags"] = subprocess.CREATE_NO_WINDOW
        return real_popen(expected, executable=sys.executable, **options)

    subprocess.run = run
    subprocess.Popen = popen
    return run, popen


def run_hook(home: Path, helper_args: list[str] | None = None) -> int:
    install_boundary(home)
    spec = importlib.util.spec_from_file_location("offline_usage_quota_hook", HOOK)
    if spec is None or spec.loader is None:
        raise BoundaryViolation("hook source unavailable")
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)

    def executable(value):
        if value != SENTINEL:
            raise BoundaryViolation("only the fixture executable is permitted")
        return SENTINEL

    scenario = read_json(home / "scenario.json")
    hook.executable = executable
    hook.time.time = lambda: scenario["now"]
    hook.RPC_TIMEOUT = 5.0 if scenario.get("pauseOnSnapshot") else 0.5
    hook.PROCESS_BUDGET = 15.0 if scenario.get("pauseOnSnapshot") else 5.0
    sys.argv = [str(HOOK), "--codex-home", str(home)]
    if helper_args is not None:
        sys.argv.extend(helper_args)
        def no_rpc(*_args, **_kwargs):
            raise BoundaryViolation("response helper must never query an account")
        hook.Rpc = no_rpc
        hook.executable = no_rpc
    return hook.main()


def serve(home: Path) -> int:
    scenario = read_json(home / "scenario.json")
    indices = {}

    def scripted(name, default):
        entries = scenario.get(name, [default])
        index = indices.get(name, 0)
        indices[name] = index + 1
        return entries[min(index, len(entries) - 1)]

    for raw in sys.stdin.buffer:
        request = json.loads(raw)
        append_log(home, request)
        method = request.get("method")
        if method not in ALLOWED_METHODS:
            return 20
        if method == "initialized":
            continue
        behavior = scenario.get("errors", {}).get(method)
        if behavior == "drop":
            return 0
        if behavior == "malformed":
            print("not-json", flush=True)
            continue
        if behavior == "error":
            print(json.dumps({"id": request["id"], "error": {"code": -1, "message": "fake error"}}), flush=True)
            continue
        if method == "initialize":
            result = {}
        elif method == "account/read":
            result = scripted("accounts", {"account": {"type": "chatgpt", "email": "offline@example.invalid"}})
        elif method == "account/rateLimits/read":
            result = scripted("snapshots", {})
            index = indices["snapshots"]
            if scenario.get("failOnSnapshot") == index:
                print(json.dumps({"id": request["id"], "error": {"code": -1, "message": "fake refresh error"}}), flush=True)
                continue
            if scenario.get("pauseOnSnapshot") == index:
                (home / "server-paused").touch()
                deadline = time.monotonic() + 10
                while not (home / "server-release").exists():
                    if time.monotonic() > deadline:
                        return 21
                    time.sleep(0.01)
            if scenario.get("revokeOnSnapshot") == index:
                preferences = read_json(home / "lugui-agent-pack" / "preferences.json")
                preferences["automaticResets"]["enabled"] = False
                write_json(home / "lugui-agent-pack" / "preferences.json", preferences)
        else:
            params = request["params"]
            if set(params) != {"idempotencyKey", "creditId"}:
                return 22
            ledger_path = home / "fake-ledger.json"
            ledger = read_json(ledger_path, {"keys": {}, "spends": 0})
            key, credit = params["idempotencyKey"], params["creditId"]
            previous = ledger["keys"].get(key)
            if previous is not None:
                if previous != credit:
                    return 23
                result = {"outcome": "alreadyRedeemed"}
            else:
                action = scenario.get("consume", "reset")
                if action == "error":
                    print(json.dumps({"id": request["id"], "error": {"code": -2, "message": "fake consume error"}}), flush=True)
                    continue
                result = {"outcome": action}
                if action in {"reset", "drop-reply", "unknown-outcome"}:
                    ledger["keys"][key] = credit
                    ledger["spends"] += 1
                    write_json(ledger_path, ledger)
                if action == "drop-reply":
                    return 0  # Fake spend was durable, but the client saw no reply.
            print(json.dumps({"id": request["id"], "result": result}), flush=True)
            continue
        print(json.dumps({"id": request["id"], "result": result}), flush=True)
    return 0


def main() -> int:
    if len(sys.argv) < 3 or sys.argv[1] not in {"--serve", "--run-hook", "--respond-hook"}:
        return 2
    home = Path(sys.argv[2])
    if not home.is_absolute() or not (home / ".offline-quota-fixture").is_file():
        return 3
    if os.environ.get("CODEX_HOME") != str(home):
        return 4
    if sys.argv[1] == "--respond-hook":
        return run_hook(home, sys.argv[3:])
    return run_hook(home) if sys.argv[1] == "--run-hook" else serve(home)


if __name__ == "__main__":
    raise SystemExit(main())
