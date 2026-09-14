#!/usr/bin/env python3
"""Inspect effective Codex configuration through the app-server protocol.

The probe is deliberately metadata-only.  It never starts a turn, never sends a
prompt, and never writes to the user's Codex configuration.  With
``--start-thread`` it creates an ephemeral thread and records the authoritative
model, effort, instruction-source, sandbox, and permission metadata returned by
``thread/start``.
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib


PROBE_SCHEMA = 1
SAFE_CONFIG_KEYS = (
    "model",
    "model_reasoning_effort",
    "sandbox_mode",
    "approval_policy",
    "approvals_reviewer",
)
SAFE_AGENT_KEYS = (
    "enabled",
    "max_concurrent_threads_per_session",
    "max_depth",
    "default_subagent_model",
    "default_subagent_reasoning_effort",
)
SAFE_ROLE_KEYS = ("name", "description", "model", "model_reasoning_effort", "sandbox_mode")
SECRET_PATTERN = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?token|authorization|bearer|password|secret|sk-[a-z0-9_-]{8,})"
)


class ProbeError(RuntimeError):
    """A bounded runtime probe failed."""


def read_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def compact_version(output: str) -> str:
    match = re.search(r"(?:codex-cli\s+)?([0-9]+(?:\.[0-9A-Za-z-]+)+)", output)
    return match.group(1) if match else "UNKNOWN"


def nested_get(mapping: Any, path: tuple[str, ...]) -> Any:
    current = mapping
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def safe_origin(origin: Any) -> dict[str, Any] | None:
    """Keep provenance labels and paths, while dropping arbitrary config data."""
    if not isinstance(origin, dict):
        return None
    result: dict[str, Any] = {}
    source = origin.get("name")
    if isinstance(source, dict):
        nested = safe_origin(source)
        if nested:
            result.update(nested)
    for key in ("type", "file", "dotCodexFolder", "profile", "name", "version", "disabledReason"):
        value = origin.get(key)
        if isinstance(value, (str, int, float, bool)) or value is None and key == "disabledReason":
            result[key] = value
    return result or None


def selected_configuration(response: dict[str, Any]) -> dict[str, Any]:
    """Allowlist effective routing settings and their layer provenance."""
    config = response.get("config") if isinstance(response.get("config"), dict) else {}
    origins = response.get("origins") if isinstance(response.get("origins"), dict) else {}
    effective = {key: config.get(key) for key in SAFE_CONFIG_KEYS}
    agents = config.get("agents") if isinstance(config.get("agents"), dict) else {}
    effective["agents"] = {key: agents.get(key) for key in SAFE_AGENT_KEYS}

    provenance: dict[str, Any] = {}
    for key in SAFE_CONFIG_KEYS:
        value = safe_origin(nested_get(origins, (key,)))
        if value is not None:
            provenance[key] = value
    agent_origins: dict[str, Any] = {}
    for key in SAFE_AGENT_KEYS:
        value = safe_origin(nested_get(origins, ("agents", key))) or setting_origin(response, ("agents", key))
        if value is not None:
            agent_origins[key] = value
    if agent_origins:
        provenance["agents"] = agent_origins

    layers: list[dict[str, Any]] = []
    for layer in response.get("layers") or []:
        if not isinstance(layer, dict):
            continue
        source = safe_origin(layer.get("name"))
        item = {"source": source, "version": layer.get("version")}
        if layer.get("disabledReason") is not None:
            item["disabled_reason"] = str(layer["disabledReason"])
        layers.append(item)
    return {"effective": effective, "origins": provenance, "layers": layers}


def setting_origin(response: dict[str, Any], path: tuple[str, ...]) -> dict[str, Any] | None:
    """Resolve an allowlisted setting origin, falling back to the first defining layer."""
    origins = response.get("origins") if isinstance(response.get("origins"), dict) else {}
    direct = safe_origin(nested_get(origins, path))
    if direct is not None:
        return direct
    for layer in response.get("layers") or []:
        if not isinstance(layer, dict) or nested_get(layer.get("config"), path) is None:
            continue
        source = safe_origin(layer.get("name")) or {}
        if isinstance(layer.get("version"), str):
            source["version"] = layer["version"]
        return source or None
    return None


def mcp_disable_overrides(response: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Create per-thread MCP disables without retaining server configuration."""
    config = response.get("config") if isinstance(response.get("config"), dict) else {}
    servers = config.get("mcp_servers")
    if not isinstance(servers, dict):
        return {}, 0
    names = [name for name in servers if isinstance(name, str)]
    return {"mcp_servers": {name: {"enabled": False} for name in names}}, len(names)


def project_layer_enabled(response: dict[str, Any], cwd: Path) -> bool:
    expected = str((cwd / ".codex").resolve()).casefold()
    for layer in response.get("layers") or []:
        if not isinstance(layer, dict):
            continue
        source = layer.get("name")
        if not isinstance(source, dict) or source.get("type") != "project":
            continue
        folder = source.get("dotCodexFolder")
        actual = str(Path(folder).resolve()).casefold() if isinstance(folder, str) else None
        if actual == expected:
            return layer.get("disabledReason") is None
    return False


def normalized_trust_key(cwd: Path) -> str:
    value = str(cwd.resolve())
    return value.casefold() if os.name == "nt" else value


def inline_trust_override(cwd: Path) -> str:
    """Use a root inline table so quoted path text is a TOML key, not a dotted segment."""
    quoted_cwd = json.dumps(normalized_trust_key(cwd))
    return f'projects={{{quoted_cwd}={{trust_level="trusted"}}}}'


def configured_roles(cwd: Path, codex_home: Path) -> list[dict[str, Any]]:
    """Read safe declarations only; these are not presented as runtime proof."""
    found: dict[str, dict[str, Any]] = {}
    directories = (codex_home / "agents", cwd / ".codex" / "agents")
    for directory in directories:
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.toml")):
            try:
                role = read_toml(path)
            except (OSError, ValueError):
                continue
            item = {key: role.get(key) for key in SAFE_ROLE_KEYS}
            item["config_file"] = str(path.resolve())
            item["source"] = "project" if directory == cwd / ".codex" / "agents" else "user"
            found[path.stem] = item
    return [{"role": name, **found[name]} for name in sorted(found)]


def materialize_fixture(source: Path, destination: Path) -> list[str]:
    """Copy only candidate project instructions/config/roles into a clean fixture."""
    source = source.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    agents_md = source / "AGENTS.md"
    config = source / ".codex" / "config.toml"
    roles = source / ".codex" / "agents"
    if agents_md.is_file():
        shutil.copy2(agents_md, destination / "AGENTS.md")
        copied.append("AGENTS.md")
    if not config.is_file():
        raise ProbeError(f"candidate has no project config: {config}")
    target_config = destination / ".codex" / "config.toml"
    target_config.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(config, target_config)
    copied.append(".codex/config.toml")
    if roles.is_dir():
        shutil.copytree(roles, destination / ".codex" / "agents")
        copied.extend(f".codex/agents/{path.name}" for path in sorted(roles.glob("*.toml")))
    return copied


def minimal_isolated_home_config(candidate_source: Path, fixture: Path) -> str:
    """Build a persisted config containing only routing defaults and fixture trust."""
    candidate = read_toml(candidate_source.resolve() / ".codex" / "config.toml")
    agents = candidate.get("agents") if isinstance(candidate.get("agents"), dict) else {}
    model = candidate.get("model")
    effort = candidate.get("model_reasoning_effort")
    if not isinstance(model, str) or not isinstance(effort, str):
        raise ProbeError("candidate config must declare main model and reasoning effort")
    lines = [f"model = {json.dumps(model)}", f"model_reasoning_effort = {json.dumps(effort)}", "", "[agents]"]
    for key in SAFE_AGENT_KEYS:
        value = agents.get(key)
        if isinstance(value, bool):
            lines.append(f"{key} = {'true' if value else 'false'}")
        elif isinstance(value, (str, int)):
            lines.append(f"{key} = {json.dumps(value)}")
    lines.extend(["", f"[projects.{json.dumps(normalized_trust_key(fixture))}]", 'trust_level = "trusted"', ""])
    return "\n".join(lines)


def inspect_isolated_home(candidate_source: Path, *, codex: str = "codex", timeout: float = 20) -> dict[str, Any]:
    """Probe a non-git candidate fixture with a minimal disposable Codex home."""
    candidate_source = candidate_source.resolve()
    fixture = Path(tempfile.mkdtemp(prefix="codex-runtime-fixture-"))
    codex_home = Path(tempfile.mkdtemp(prefix="codex-runtime-home-"))
    report: dict[str, Any] | None = None
    try:
        copied = materialize_fixture(candidate_source, fixture)
        config_text = minimal_isolated_home_config(candidate_source, fixture)
        (codex_home / "config.toml").write_text(config_text, encoding="utf-8")
        report = inspect_runtime(
            fixture,
            codex_home,
            codex=codex,
            start_thread=True,
            trust_fixture=False,
            timeout=timeout,
        )
        report["fixture"] = {
            "source": str(candidate_source),
            "temporary": True,
            "git_initialized": False,
            "removed_after_probe": None,
            "copied_files": copied,
        }
        report["isolated_codex_home"] = {
            "temporary": True,
            "removed_after_probe": None,
            "initial_files": ["config.toml"],
            "authentication_copied": False,
            "config_contents": "candidate main/default routing values plus exact persisted fixture trust only",
        }
        report["isolation"]["project_trust"] = (
            "exact fixture trust persisted only in disposable CODEX_HOME; real user home untouched"
        )
        report["isolation"]["fixture_trust_applied"] = True
        report["evidence_basis"]["isolated_home"] = (
            "fresh CODEX_HOME with one minimal persisted config; no auth, links, user instructions, roles, or machine state copied"
        )
    finally:
        fixture_removed = remove_task_temp(fixture)
        home_removed = remove_task_temp(codex_home)
    if report is None:
        raise ProbeError("isolated-home probe produced no report")
    report["fixture"]["removed_after_probe"] = fixture_removed
    report["isolated_codex_home"]["removed_after_probe"] = home_removed
    return report


def inspect_nesting_config_comparison(
    candidate_source: Path, *, codex: str = "codex", timeout: float = 20
) -> dict[str, Any]:
    """Compare omitted and explicit agent depth with no user configuration or auth."""
    candidate_source = candidate_source.resolve()
    variants: dict[str, dict[str, Any]] = {}
    cleanup: dict[str, bool] = {}
    for label, explicit_depth in (("candidate_omits_max_depth", None), ("candidate_with_max_depth_2", 2)):
        fixture = Path(tempfile.mkdtemp(prefix=f"codex-nesting-{label}-"))
        codex_home = Path(tempfile.mkdtemp(prefix=f"codex-nesting-home-{label}-"))
        try:
            materialize_fixture(candidate_source, fixture)
            if explicit_depth is not None:
                config_path = fixture / ".codex" / "config.toml"
                contents = config_path.read_text(encoding="utf-8")
                if re.search(r"(?m)^max_depth\s*=", contents):
                    contents = re.sub(r"(?m)^max_depth\s*=.*$", f"max_depth = {explicit_depth}", contents, count=1)
                else:
                    contents = contents.replace("[agents]\n", f"[agents]\nmax_depth = {explicit_depth}\n", 1)
                config_path.write_text(contents, encoding="utf-8")
            runtime = inspect_runtime(
                fixture,
                codex_home,
                codex=codex,
                start_thread=False,
                trust_fixture=True,
                timeout=timeout,
            )
            agents = runtime["configuration"]["effective"]["agents"]
            agent_origins = runtime["configuration"]["origins"].get("agents", {})
            project_layers = [
                layer
                for layer in runtime["configuration"]["layers"]
                if isinstance(layer.get("source"), dict) and layer["source"].get("type") == "project"
            ]
            variants[label] = {
                "requested_max_depth": explicit_depth,
                "effective": {
                    "model": runtime["configuration"]["effective"].get("model"),
                    "model_reasoning_effort": runtime["configuration"]["effective"].get("model_reasoning_effort"),
                    "agents": agents,
                },
                "max_depth_origin": agent_origins.get("max_depth"),
                "project_layer_enabled": bool(project_layers) and all("disabled_reason" not in layer for layer in project_layers),
                "transport": runtime["transport"],
                "model_turn_started": runtime["isolation"]["model_turn_started"],
            }
        finally:
            cleanup[f"{label}_fixture_removed"] = remove_task_temp(fixture)
            cleanup[f"{label}_home_removed"] = remove_task_temp(codex_home)
    return {
        "probe_schema": PROBE_SCHEMA,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "method": (
            "two fresh non-git fixture copies; empty disposable CODEX_HOME; verified root inline-TOML "
            "project trust override; app-server config/read only; no thread or model turn"
        ),
        "candidate_source": str(candidate_source),
        "variants": variants,
        "cleanup": cleanup,
        "limits": {
            "omitted_depth": "a null config/read value is the actual resolved config field; it does not by itself name Codex's internal operational default",
            "runtime_behavior": "tool availability by nesting depth requires rollout metadata and cannot be inferred from config/read alone",
        },
    }


class JsonRpcClient:
    """Small newline-delimited JSON-RPC client for a single app-server process."""

    def __init__(self, process: subprocess.Popen[str], timeout: float) -> None:
        if process.stdin is None or process.stdout is None or process.stderr is None:
            raise ProbeError("app-server pipes were not created")
        self.process = process
        self.timeout = timeout
        self.stdin: TextIO = process.stdin
        self.messages: queue.Queue[dict[str, Any] | BaseException | None] = queue.Queue()
        self.stderr_parts: list[str] = []
        self._next_id = 1
        self._stdout_thread = threading.Thread(target=self._read_stdout, args=(process.stdout,), daemon=True)
        self._stderr_thread = threading.Thread(target=self._read_stderr, args=(process.stderr,), daemon=True)
        self._stdout_thread.start()
        self._stderr_thread.start()

    def _read_stdout(self, stream: TextIO) -> None:
        try:
            for line in stream:
                if not line.strip():
                    continue
                parsed = json.loads(line)
                if isinstance(parsed, dict):
                    self.messages.put(parsed)
        except BaseException as exc:  # Propagate reader failures to the caller.
            self.messages.put(exc)
        finally:
            self.messages.put(None)

    def _read_stderr(self, stream: TextIO) -> None:
        for chunk in iter(lambda: stream.read(4096), ""):
            self.stderr_parts.append(chunk)

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        message: dict[str, Any] = {"method": method}
        if params is not None:
            message["params"] = params
        self.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
        self.stdin.flush()

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        request_id = self._next_id
        self._next_id += 1
        message = {"id": request_id, "method": method, "params": params}
        self.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
        self.stdin.flush()
        deadline = time.monotonic() + self.timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProbeError(f"timed out waiting for {method}")
            try:
                response = self.messages.get(timeout=remaining)
            except queue.Empty as exc:
                raise ProbeError(f"timed out waiting for {method}") from exc
            if response is None:
                detail = safe_error_text("".join(self.stderr_parts))
                raise ProbeError(f"app-server exited while waiting for {method}: {detail}")
            if isinstance(response, BaseException):
                raise ProbeError(f"invalid app-server output: {response}") from response
            if response.get("id") != request_id:
                continue  # Notifications and unrelated server requests are irrelevant here.
            if "error" in response:
                error = response.get("error") if isinstance(response.get("error"), dict) else {}
                code = error.get("code", "UNKNOWN")
                message_text = safe_error_text(str(error.get("message", "request failed")))
                raise ProbeError(f"{method} failed ({code}): {message_text}")
            result = response.get("result")
            if not isinstance(result, dict):
                raise ProbeError(f"{method} returned no object result")
            return result

    def finish_readers(self) -> None:
        self._stdout_thread.join(timeout=1)
        self._stderr_thread.join(timeout=1)


def safe_error_text(value: str) -> str:
    """Keep errors useful without echoing credentials or large private payloads."""
    compact = " ".join(value.split())[:500]
    return "[REDACTED]" if SECRET_PATTERN.search(compact) else compact


def daemon_available(codex: str, env: dict[str, str], cwd: Path, timeout: float) -> bool:
    """Check for an already managed app-server; never starts or stops one."""
    try:
        completed = subprocess.run(
            [codex, "app-server", "daemon", "version"], cwd=cwd, env=env,
            capture_output=True, text=True, encoding="utf-8", timeout=min(timeout, 5), check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def stop_client_process(process: subprocess.Popen[str]) -> bool:
    """Stop only the short-lived stdio server/proxy created by this probe."""
    if process.stdin is not None and not process.stdin.closed:
        process.stdin.close()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
    return process.poll() is not None


def remove_task_temp(path: Path) -> bool:
    """Bounded cleanup for one temporary directory created by this probe."""
    for _ in range(3):
        try:
            shutil.rmtree(path)
            return True
        except FileNotFoundError:
            return True
        except OSError:
            time.sleep(0.1)
    shutil.rmtree(path, ignore_errors=True)
    return not path.exists()


def inspect_runtime(
    cwd: Path,
    codex_home: Path,
    *,
    codex: str = "codex",
    start_thread: bool = False,
    trust_fixture: bool = False,
    set_runtime_workspace_root: bool = False,
    timeout: float = 15,
) -> dict[str, Any]:
    cwd = cwd.resolve()
    codex_home = codex_home.resolve()
    env = os.environ.copy()
    env["CODEX_HOME"] = str(codex_home)
    version = subprocess.run(
        [codex, "--version"], cwd=cwd, env=env, capture_output=True, text=True,
        encoding="utf-8", timeout=min(timeout, 5), check=False,
    )
    daemon = daemon_available(codex, env, cwd, timeout)
    if daemon and not trust_fixture:
        command = [codex, "app-server", "proxy"]
    else:
        command = [codex, "app-server", "--stdio"]
        if trust_fixture:
            command.extend(["-c", inline_trust_override(cwd)])
    process = subprocess.Popen(
        command, cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, encoding="utf-8", bufsize=1,
    )
    client_stopped = False
    try:
        rpc = JsonRpcClient(process, timeout)
        initialized = rpc.request(
            "initialize",
            {
                "clientInfo": {"name": "lugui-runtime-probe", "title": "Lugui runtime probe", "version": "1"},
                "capabilities": {"experimentalApi": True, "optOutNotificationMethods": ["thread/started"]},
            },
        )
        rpc.notify("initialized")
        config_response = rpc.request("config/read", {"cwd": str(cwd), "includeLayers": True})
        fixture_trust_applied = project_layer_enabled(config_response, cwd) if trust_fixture else None
        selection = selected_configuration(config_response)
        mcp_overrides, mcp_count = mcp_disable_overrides(config_response)
        thread_evidence: dict[str, Any] | None = None
        if start_thread:
            params: dict[str, Any] = {"cwd": str(cwd), "ephemeral": True, "environments": []}
            if set_runtime_workspace_root:
                params["runtimeWorkspaceRoots"] = [str(cwd)]
            if mcp_overrides:
                params["config"] = mcp_overrides
            response = rpc.request("thread/start", params)
            thread_evidence = {
                "model": response.get("model"),
                "reasoning_effort": response.get("reasoningEffort"),
                "model_provider": response.get("modelProvider"),
                "service_tier": response.get("serviceTier"),
                "cwd": response.get("cwd"),
                "instruction_sources": response.get("instructionSources", []),
                "sandbox": response.get("sandbox"),
                "approval_policy": response.get("approvalPolicy"),
                "approvals_reviewer": response.get("approvalsReviewer"),
                "active_permission_profile": response.get("activePermissionProfile"),
                "runtime_workspace_roots": response.get("runtimeWorkspaceRoots", []),
                "ephemeral": True,
            }
        report = {
            "probe_schema": PROBE_SCHEMA,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "cwd": str(cwd),
            "codex": {
                "version": compact_version(version.stdout + " " + version.stderr),
                "initialize_user_agent": initialized.get("userAgent"),
            },
            "transport": {
                "mode": "existing-daemon-proxy" if daemon and not trust_fixture else "short-lived-stdio",
                "existing_daemon_detected": daemon,
                "existing_daemon_reused": daemon and not trust_fixture,
            },
            "configuration": selection,
            "configured_roles": configured_roles(cwd, codex_home),
            "thread": thread_evidence,
            "isolation": {
                "model_turn_started": False,
                "thread_started": start_thread,
                "thread_ephemeral": start_thread,
                "runtime_workspace_root_requested": set_runtime_workspace_root,
                "configured_mcp_server_count": mcp_count,
                "mcp_servers_disabled_before_thread_start": start_thread,
                "project_trust": (
                    "process-local trusted session override; no persisted trust change"
                    if trust_fixture else "existing effective trust; no override requested"
                ),
                "fixture_trust_applied": fixture_trust_applied,
                "fixture_trust_limitation": (
                    "config/read reported the candidate project layer disabled; thread/start fields remain authoritative only for the thread it returned"
                    if trust_fixture and not fixture_trust_applied else None
                ),
                "global_config_modified": False,
            },
            "evidence_basis": {
                "configuration": "app-server config/read with cwd and includeLayers=true",
                "thread": "app-server thread/start response" if start_thread else "not requested",
                "roles": "allowlisted declarations from user/project role TOML files; representative spawns remain separate runtime evidence",
            },
            "limitations": {
                "instruction_sources": "thread/start is pre-turn metadata; an absent path does not prove that AGENTS.md will be absent from a later turn context",
                "role_activation": "role TOML declarations do not prove the model and effort used by a spawned child",
                "behavior": "no model turn was started, so this report contains no behavioral activation evidence",
            },
        }
        return report
    finally:
        client_stopped = stop_client_process(process)
        if "rpc" in locals():
            rpc.finish_readers()
        # Mutate a successfully built report only; exceptions retain their original cause.
        if "report" in locals():
            report["transport"]["client_process_stopped"] = client_stopped


def write_report(report: dict[str, Any], output: Path | None) -> None:
    serialized = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if output is None:
        sys.stdout.write(serialized)
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(serialized, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cwd", type=Path, default=Path.cwd())
    parser.add_argument("--codex-home", type=Path, default=Path(os.environ["CODEX_HOME"]) if "CODEX_HOME" in os.environ else Path.home() / ".codex")
    parser.add_argument("--codex", default="codex")
    parser.add_argument("--start-thread", action="store_true", help="start an ephemeral metadata-only thread; no model turn is sent")
    parser.add_argument("--fixture-source", type=Path, help="copy candidate AGENTS/config/roles into a disposable trusted fixture")
    parser.add_argument("--set-runtime-workspace-root", action="store_true", help="pass runtimeWorkspaceRoots=[cwd] to thread/start")
    parser.add_argument("--timeout", type=float, default=15)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("timeout must be positive")
    try:
        if args.fixture_source:
            with tempfile.TemporaryDirectory(prefix="codex-runtime-probe-") as temporary:
                fixture = Path(temporary)
                copied = materialize_fixture(args.fixture_source, fixture)
                report = inspect_runtime(
                    fixture, args.codex_home, codex=args.codex,
                    start_thread=args.start_thread, trust_fixture=True,
                    set_runtime_workspace_root=args.set_runtime_workspace_root, timeout=args.timeout,
                )
                report["fixture"] = {
                    "source": str(args.fixture_source.resolve()),
                    "temporary": True,
                    "removed_after_probe": True,
                    "copied_files": copied,
                    "trust_treatment": (
                        "process-local trusted -c override was accepted; user/global config was not written"
                        if report["isolation"]["fixture_trust_applied"]
                        else "process-local trusted -c override was requested but config/read kept the project layer disabled; user/global config was not written"
                    ),
                }
        else:
            report = inspect_runtime(
                args.cwd, args.codex_home, codex=args.codex,
                start_thread=args.start_thread,
                set_runtime_workspace_root=args.set_runtime_workspace_root, timeout=args.timeout,
            )
        write_report(report, args.output)
    except (OSError, subprocess.SubprocessError, ProbeError) as exc:
        print(f"runtime probe failed: {safe_error_text(str(exc))}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
