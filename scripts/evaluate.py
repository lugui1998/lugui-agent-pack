#!/usr/bin/env python3
"""Run isolated, reproducible Codex CLI evaluation fixtures.

This is deliberately a runner, not an orchestration layer.  Fixture acceptance
is based on files the task creates; parsed routing events are recorded only as
diagnostics.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
try:
    from eval_telemetry import whole_task_telemetry
except ModuleNotFoundError:
    def whole_task_telemetry(root_thread_id: str, codex_home: Path, enabled: bool = True) -> dict[str, Any]:
        return {"status": "TELEMETRY_MODULE_UNAVAILABLE", "root_thread_id": root_thread_id,
                "complete_known_main_and_child_total_tokens": "UNKNOWN"}

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib


ROOT = Path(__file__).resolve().parents[1]
REDACTIONS = (
    (re.compile(r"(?i)(api[_-]?key\s*[=:]\s*)[^\s\"']+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(authorization:\s*bearer\s+)[^\s]+"), r"\1[REDACTED]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"), "[REDACTED]"),
    # Evaluation workspaces and Codex homes are disposable local state. Keep
    # their shape out of persisted evidence without removing useful messages.
    (re.compile(r"(?i)(?:[A-Za-z]:[\\/]+)(?:Users|home)[\\/]+[^\r\n\"']+"), "[LOCAL_PATH]"),
    (re.compile(r"(?i)(?:[A-Za-z]:[\\/]+)(?:Projetos|Documents)[\\/]+[^\r\n\"']+"), "[LOCAL_PATH]"),
    (re.compile(r"(?i)(?:/)(?:Users|home)/[^\r\n\"']+"), "[LOCAL_PATH]"),
    (re.compile(r"(?i)(?:[A-Za-z]:[\\/]+)?(?:tmp|var[\\/]tmp)[\\/]+[^\r\n\"']+"), "[LOCAL_PATH]"),
)
UUID_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def read_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def sanitize(value: str) -> str:
    for pattern, replacement in REDACTIONS:
        value = pattern.sub(replacement, value)
    return value


def sanitize_value(value: Any) -> Any:
    """Recursively sanitize persisted evidence, including telemetry paths."""
    if isinstance(value, str):
        return sanitize(value)
    if isinstance(value, dict):
        return {key: sanitize_value(child) for key, child in value.items()}
    if isinstance(value, list):
        return [sanitize_value(child) for child in value]
    return value


def default_codex_home() -> Path:
    return Path(os.environ["CODEX_HOME"]) if "CODEX_HOME" in os.environ else Path.home() / ".codex"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def safe_name(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", value):
        raise ValueError(f"unsafe name: {value!r}")
    return value


def contained(path: Path, root: Path) -> Path:
    resolved_root = root.resolve()
    resolved = path.resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError as error:
        raise ValueError(f"path escapes allowed root: {path}") from error
    return resolved


def repo_relative(path_text: str) -> Path:
    path = Path(path_text)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"unsafe repository path: {path_text!r}")
    return contained(ROOT / path, ROOT)


def copy_utf8_tree(source: Path, destination: Path) -> None:
    source = source.resolve()
    if not source.is_dir():
        raise ValueError(f"fixture workspace is not a directory: {source}")
    destination.mkdir(parents=True, exist_ok=True)
    for item in source.rglob("*"):
        resolved = contained(item, source)
        relative = resolved.relative_to(source)
        if "__pycache__" in relative.parts or resolved.suffix in {".pyc", ".pyo"}:
            continue
        target = destination / relative
        contained(target, destination)
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif item.is_file():
            data = item.read_bytes()
            data.decode("utf-8")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)


def fixture_hashes(fixture_dir: Path) -> dict[str, str]:
    result = {}
    for item in fixture_dir.rglob("*"):
        if item.is_file():
            resolved = contained(item, fixture_dir)
            relative = resolved.relative_to(fixture_dir)
            if "__pycache__" in relative.parts or resolved.suffix in {".pyc", ".pyo"}:
                continue
            result[relative.as_posix()] = sha256_file(resolved)
    return result


def load_scenario(name: str) -> tuple[dict[str, Any], Path]:
    scenarios_path = ROOT / "evals" / "scenarios.toml"
    document = read_toml(scenarios_path)
    if document.get("version") != 2:
        raise ValueError("unsupported scenarios.toml version")
    scenario = document.get("scenarios", {}).get(name) if isinstance(document.get("scenarios"), dict) else None
    if not isinstance(scenario, dict):
        raise ValueError(f"unknown scenario: {name}")
    fixture_dir = contained(repo_relative(str(scenario["fixture_dir"])), ROOT / "evals" / "fixtures")
    return scenario, fixture_dir


def apply_copied_role_overrides(workspace: Path, fixture_dir: Path, overrides: list[dict[str, Any]]) -> None:
    roles_root = (workspace / ".codex" / "agents").resolve()
    for override in overrides:
        role = safe_name(str(override["role"]))
        source = contained(repo_relative(str(override["source"])), fixture_dir)
        destination_text = str(override["destination"])
        destination = Path(destination_text)
        if destination.as_posix() != f".codex/agents/{role}.toml":
            raise ValueError(f"unsafe role override destination: {destination_text!r}")
        target = contained(workspace / destination, workspace)
        contained(target, roles_root)
        data = source.read_bytes()
        data.decode("utf-8")
        with source.open("rb") as handle:
            tomllib.load(handle)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def scenario_checks(checks: list[dict[str, Any]], fixture_dir: Path, workspace: Path) -> list[dict[str, Any]]:
    results = []
    substitutions = {"{python}": sys.executable, "{fixture_dir}": str(fixture_dir), "{workspace}": str(workspace)}
    for check in checks:
        if check.get("kind") != "external_command" or not isinstance(check.get("argv"), list):
            results.append({"expectation": "external_command", "passed": False, "failure": "invalid_check"})
            continue
        argv = []
        for argument in check["argv"]:
            if not isinstance(argument, str):
                raise ValueError("external command arguments must be strings")
            for token, value in substitutions.items():
                argument = argument.replace(token, value)
            argv.append(argument)
        timeout = check.get("timeout_seconds", 15)
        try:
            completed = subprocess.run(argv, cwd=fixture_dir, capture_output=True, text=True, encoding="utf-8",
                                       timeout=timeout, check=False, shell=False)
            results.append({"expectation": "external_command", "argv": [sanitize(item) for item in argv],
                            "passed": completed.returncode == 0, "exit_code": completed.returncode,
                            "stdout": sanitize(completed.stdout)[:1000], "stderr": sanitize(completed.stderr)[:1000]})
        except subprocess.TimeoutExpired as error:
            results.append({"expectation": "external_command", "passed": False, "failure": "timeout",
                            "stderr": sanitize(str(error.stderr or ""))[:1000]})
        except (OSError, UnicodeDecodeError) as error:
            results.append({"expectation": "external_command", "passed": False, "failure": "process_error",
                            "stderr": sanitize(str(error))[:1000]})
    return results


def observation_rubric(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"id": item.get("id", "UNKNOWN"), "source": item.get("source", "UNKNOWN"),
             "required": bool(item.get("required")), "criterion": item.get("criterion", ""),
             "status": "PENDING_REVIEW" if item.get("required") else "UNKNOWN"} for item in items]


def materialize_case(case_name: str, case: dict[str, Any], destination: Path) -> None:
    safe_name(case_name)
    destination.mkdir(parents=True, exist_ok=True)
    for name, contents in case.get("files", {}).items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"unsafe fixture path: {name!r}")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(str(contents).encode("utf-8"))


def copy_variant_config(source: Path, workspace: Path) -> None:
    agents = source / "AGENTS.md"
    codex = source / ".codex"
    if agents.is_file():
        shutil.copy2(agents, workspace / "AGENTS.md")
    # Copy only project configuration and role definitions.  In particular, do
    # not copy machine state such as auth/session databases into an evaluation.
    if (codex / "config.toml").is_file():
        target = workspace / ".codex"
        target.mkdir(parents=True, exist_ok=True)
        shutil.copy2(codex / "config.toml", target / "config.toml")
    if (codex / "agents").is_dir():
        shutil.copytree(codex / "agents", workspace / ".codex" / "agents", dirs_exist_ok=True)


def apply_role_overrides(workspace: Path, overrides: dict[str, Any]) -> None:
    for role, settings in overrides.items():
        role = safe_name(role)
        path = workspace / ".codex" / "agents" / f"{role}.toml"
        if not path.is_file() or not isinstance(settings, dict):
            raise ValueError(f"cannot apply role override for {role!r}")
        contents = path.read_text(encoding="utf-8")
        for key, value in settings.items():
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
                raise ValueError(f"unsafe role setting: {key!r}")
            replacement = f"{key} = {json.dumps(value)}"
            contents, changed = re.subn(rf"(?m)^{re.escape(key)}\s*=.*$", replacement, contents, count=1)
            if changed != 1:
                raise ValueError(f"role setting not found: {role}.{key}")
        path.write_text(contents, encoding="utf-8")


def evaluate_checks(workspace: Path, checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for check in checks:
        if "python" in check:
            try:
                completed = subprocess.run([sys.executable, "-c", check["python"]], cwd=workspace,
                                           capture_output=True, text=True, encoding="utf-8", timeout=15, check=False)
            except subprocess.TimeoutExpired as error:
                results.append({"expectation": "python", "passed": False, "failure": "timeout",
                                "stderr": sanitize(str(error.stderr or ""))[:1000]})
                continue
            except UnicodeDecodeError as error:
                results.append({"expectation": "python", "passed": False, "failure": "decode_error",
                                "stderr": sanitize(str(error))[:1000]})
                continue
            except OSError as error:
                results.append({"expectation": "python", "passed": False, "failure": "process_error",
                                "stderr": sanitize(str(error))[:1000]})
                continue
            results.append({"expectation": "python", "passed": completed.returncode == 0,
                            "stderr": sanitize(completed.stderr)[:1000]})
            continue
        relative = Path(check["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"unsafe acceptance path: {relative}")
        file_path = workspace / relative
        actual = file_path.read_text(encoding="utf-8") if file_path.is_file() else None
        if check.get("nonempty") is True:
            passed = actual is not None and bool(actual.strip())
            results.append({"path": str(relative), "expectation": "nonempty", "passed": passed})
            continue
        if "equals" in check:
            passed = actual == check["equals"]
            expectation = "equals"
            result = {"path": str(relative), "expectation": expectation, "passed": passed}
            if not passed:
                result["expected"] = sanitize(str(check["equals"]))
                result["actual"] = sanitize(actual) if actual is not None else None
            results.append(result)
            continue
        if "contains_ci" in check:
            passed = actual is not None and check["contains_ci"].casefold() in actual.casefold()
            expectation = "contains_ci"
        else:
            passed = actual is not None and check["contains"] in actual
            expectation = "contains"
        results.append({"path": str(relative), "expectation": expectation, "passed": passed})
    return results


def parse_jsonl(stdout: str) -> tuple[list[dict[str, Any]], int]:
    events: list[dict[str, Any]] = []
    malformed = 0
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            malformed += 1
            continue
        if isinstance(event, dict):
            event["_evaluation_sequence"] = len(events) + 1
            events.append(event)
    return events, malformed


def observed_tokens(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Report observed main-process components without inventing child totals."""
    components: dict[str, list[int]] = {key: [] for key in
                                        ("input_tokens", "cached_input_tokens", "cache_write_input_tokens",
                                         "output_tokens", "reasoning_output_tokens")}
    for event in events:
        usage = event.get("usage")
        if not isinstance(usage, dict):
            continue
        for key in components:
            if isinstance(usage.get(key), int):
                components[key].append(usage[key])
    if not any(components.values()):
        return {"main": "UNKNOWN", "children": "UNKNOWN", "total": "UNKNOWN"}
    main = {key: sum(values) if values else "UNKNOWN" for key, values in components.items()}
    main["main_only_total_tokens"] = (main["input_tokens"] + main["output_tokens"]
                                      if isinstance(main["input_tokens"], int) and isinstance(main["output_tokens"], int)
                                      else "UNKNOWN")
    return {"main": main,
            "children": "UNKNOWN", "total": "UNKNOWN"}


def scheduling_evidence(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Derive only directly observable lifecycle overlap from ordered CLI events."""
    active: set[str] = set()
    maximum = 0
    participants: set[str] = set()
    for event in events:
        participant = next((str(event[key]) for key in ("thread_id", "agent_id", "subagent_id") if event.get(key)), None)
        if participant is None:
            continue
        participants.add(participant)
        event_type = str(event.get("type", "")).lower()
        if "start" in event_type:
            active.add(participant)
            maximum = max(maximum, len(active))
        elif any(word in event_type for word in ("complete", "fail", "cancel", "error")):
            active.discard(participant)
    return {"basis": "ordered CLI lifecycle events without duration telemetry", "participants_observed": len(participants),
            "maximum_unclosed_lifecycle_participants": maximum, "concurrent_execution_observed": "UNKNOWN",
            "serial_execution_observed": "UNKNOWN"}


def copied_file_hashes(workspace: Path) -> dict[str, str]:
    copied = [workspace / "AGENTS.md", workspace / ".codex" / "config.toml"]
    copied.extend(sorted((workspace / ".codex" / "agents").glob("*.toml")) if (workspace / ".codex" / "agents").is_dir() else [])
    return {path.relative_to(workspace).as_posix(): sha256_file(path) for path in copied if path.is_file()}


def reproducibility_evidence(workspace: Path) -> dict[str, Any]:
    return {"case_spec_sha256": sha256_file(ROOT / "evals" / "cases.toml"),
            "variant_spec_sha256": sha256_file(ROOT / "evals" / "variants.toml"),
            "runner_sha256": sha256_file(Path(__file__).resolve()),
            "copied_file_sha256": copied_file_hashes(workspace)}


def activation_evidence(workspace: Path, command: list[str], events: list[dict[str, Any]]) -> dict[str, Any]:
    config = workspace / ".codex" / "config.toml"
    observed_models = sorted({str(event["model"]) for event in events if isinstance(event.get("model"), str)})
    observed_efforts = sorted({str(event[key]) for event in events for key in ("model_reasoning_effort", "reasoning_effort")
                               if isinstance(event.get(key), str)})
    return {"project_config_present": config.is_file(),
            "project_config_sha256": sha256_file(config) if config.is_file() else "UNKNOWN",
            "trusted_fixture_override_requested": any(item.startswith("projects=") for item in command),
            "observed_model_values": observed_models or "UNKNOWN",
            "observed_reasoning_effort_values": observed_efforts or "UNKNOWN",
            "effective_main_model": "UNKNOWN",
            "effective_main_reasoning_effort": "UNKNOWN",
            "instruction_loading": "UNKNOWN: CLI JSONL does not inventory loaded global/project instructions"}


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def observed_thread_ids(events: list[dict[str, Any]]) -> list[str]:
    found: set[str] = set()
    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key == "thread_id" and isinstance(child, str) and UUID_PATTERN.fullmatch(child):
                    found.add(child)
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    for event in events:
        visit(event)
    return sorted(found)


def primary_thread_id(events: list[dict[str, Any]]) -> str | None:
    for event in events:
        event_type = str(event.get("type", "")).lower()
        thread_id = event.get("thread_id")
        if "thread" in event_type and "start" in event_type and isinstance(thread_id, str) and UUID_PATTERN.fullmatch(thread_id):
            return thread_id
    return None


def matching_rollouts(sessions_root: Path, thread_id: str) -> list[Path]:
    if not sessions_root.is_dir():
        return []
    root = sessions_root.resolve()
    suffix = f"-{thread_id}.jsonl"
    matches = []
    for path in sessions_root.rglob("*.jsonl"):
        if not path.name.endswith(suffix):
            continue
        try:
            path.resolve().relative_to(root)
        except ValueError:
            continue
        if path.is_file():
            matches.append(path)
    return sorted(matches)


def message_text(record: dict[str, Any]) -> str | None:
    payload = record.get("payload")
    if not isinstance(payload, dict) or payload.get("type") != "message" or payload.get("role") not in {"user", "developer"}:
        return None
    content = payload.get("content")
    if not isinstance(content, list):
        return None
    texts = [item.get("text") for item in content if isinstance(item, dict) and isinstance(item.get("text"), str)]
    return "\n".join(texts) if texts else None


def session_activation_evidence(events: list[dict[str, Any]], workspace: Path, codex_home: Path | None,
                                enabled: bool, expected_agents_text: str | None = None) -> dict[str, Any]:
    thread_ids = observed_thread_ids(events)
    if not enabled:
        return {"status": "DISABLED", "known_thread_ids": thread_ids, "children": "UNKNOWN"}
    if codex_home is None:
        return {"status": "NO_CODEX_HOME", "known_thread_ids": thread_ids, "children": "UNKNOWN"}
    agents_file = workspace / "AGENTS.md"
    normalized_agents = (expected_agents_text if expected_agents_text is not None else
                         (normalize_text(agents_file.read_text(encoding="utf-8")) if agents_file.is_file() else None))
    records = []
    for thread_id in thread_ids:
        matches = matching_rollouts(codex_home / "sessions", thread_id)
        if len(matches) != 1:
            records.append({"thread_id": thread_id, "status": "MISSING" if not matches else "AMBIGUOUS",
                            "matching_paths": [str(path) for path in matches]})
            continue
        rollout = matches[0]
        model = effort = "UNKNOWN"
        complete_agents_presence: bool | str = "UNKNOWN" if normalized_agents is None else False
        try:
            with rollout.open(encoding="utf-8") as handle:
                for line in handle:
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(record, dict):
                        continue
                    if record.get("type") == "turn_context" and isinstance(record.get("payload"), dict):
                        payload = record["payload"]
                        if isinstance(payload.get("model"), str):
                            model = payload["model"]
                        if isinstance(payload.get("effort"), str):
                            effort = payload["effort"]
                    text = message_text(record)
                    if normalized_agents is not None and text is not None and normalized_agents in normalize_text(text):
                        complete_agents_presence = True
        except (OSError, UnicodeDecodeError):
            records.append({"thread_id": thread_id, "status": "UNREADABLE", "matching_paths": [str(rollout)]})
            continue
        records.append({"thread_id": thread_id, "status": "FOUND", "rollout_path": str(rollout),
                        "rollout_sha256": sha256_file(rollout), "turn_context_model": model,
                        "turn_context_effort": effort, "copied_agents_complete_presence": complete_agents_presence})
    return {"status": "COMPLETE", "known_thread_ids": thread_ids, "records": records, "children": "UNKNOWN"}


def expected_configuration(workspace: Path, variant: dict[str, Any]) -> dict[str, Any]:
    config_path = workspace / ".codex" / "config.toml"
    config = read_toml(config_path) if config_path.is_file() else {}
    return {"expected_main_model": variant.get("model", config.get("model", "UNKNOWN")),
            "expected_main_reasoning_effort": variant.get("effort", config.get("model_reasoning_effort", "UNKNOWN")),
            "copied_agents_sha256": sha256_file(workspace / "AGENTS.md") if (workspace / "AGENTS.md").is_file() else "UNKNOWN"}


def configuration_validity(expected: dict[str, Any], session_evidence: dict[str, Any], primary_id: str | None) -> dict[str, Any]:
    expected_model = expected["expected_main_model"]
    expected_effort = expected["expected_main_reasoning_effort"]
    records = session_evidence.get("records") if isinstance(session_evidence.get("records"), list) else []
    actual = next((record for record in records if isinstance(record, dict) and record.get("status") == "FOUND" and record.get("thread_id") == primary_id), {})
    actual_model = actual.get("turn_context_model", "UNKNOWN")
    actual_effort = actual.get("turn_context_effort", "UNKNOWN")
    agents_presence = actual.get("copied_agents_complete_presence", "UNKNOWN")
    def comparison(expected: Any, observed: Any) -> bool | str:
        if not isinstance(expected, str) or expected == "UNKNOWN" or not isinstance(observed, str) or observed == "UNKNOWN":
            return "UNKNOWN"
        return observed == expected
    model_matches = comparison(expected_model, actual_model)
    effort_matches = comparison(expected_effort, actual_effort)
    conditions = [model_matches, effort_matches, agents_presence]
    valid: bool | str = False if False in conditions else (True if all(item is True for item in conditions) else "UNKNOWN")
    return {"expected_main_model": expected_model, "expected_main_reasoning_effort": expected_effort,
            "actual_main_model": actual_model, "actual_main_reasoning_effort": actual_effort,
            "main_model_matches_expected": model_matches, "main_reasoning_effort_matches_expected": effort_matches,
            "copied_agents_complete_presence": agents_presence, "valid": valid,
            "evidence_status": session_evidence.get("status", "UNKNOWN"), "pre_run_configuration": expected}


def stop_process_tree(process: subprocess.Popen[str]) -> None:
    """Stop only the timed-out Codex process and processes it created."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, check=False)
    else:
        os.killpg(process.pid, signal.SIGTERM)


def run_process(command: list[str], workspace: Path, timeout: float, stdin_text: str, codex_home: Path | None) -> dict[str, Any]:
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
    started = time.monotonic()
    child_env = os.environ.copy()
    if codex_home is not None:
        child_env["CODEX_HOME"] = str(codex_home)
    process = subprocess.Popen(command, cwd=workspace, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, encoding="utf-8", creationflags=flags,
                               start_new_session=os.name != "nt", env=child_env)
    timed_out = False
    try:
        stdout, stderr = process.communicate(stdin_text, timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        stop_process_tree(process)
        try:
            stdout, stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
    return {"exit_code": process.returncode, "timed_out": timed_out,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "stdout": sanitize(stdout), "stderr": sanitize(stderr)}


def command_for(workspace: Path, prompt: str, variant: dict[str, Any]) -> list[str]:
    trusted_workspace = json.dumps(str(workspace))
    command = ["codex", "exec", "--json", "--ignore-user-config", "--skip-git-repo-check",
               "--approve-for-me", "-C", str(workspace)]
    # `-c` accepts TOML values. A root inline table avoids unsupported quoted
    # key segments in a dotted override path on Windows fixture paths.
    command.extend(["-c", "projects={" + trusted_workspace + '={trust_level="trusted"}}'])
    if variant.get("model"):
        command.extend(["-m", variant["model"]])
    if variant.get("effort"):
        command.extend(["-c", f'model_reasoning_effort="{variant["effort"]}"'])
    for setting in variant.get("config", []):
        command.extend(["-c", str(setting)])
    # A lone dash makes Codex read the task prompt from standard input.  Keeping
    # it out of argv also prevents the prompt from appearing in process listings.
    return command + ["-"]


def run_case(case_name: str, variant_name: str, output: Path, timeout: float, retries: int,
             keep_workspace: bool = False, codex_home: Path | None = None,
             read_session_activation: bool = False, read_whole_task_telemetry: bool = False,
             scenario: dict[str, Any] | None = None, fixture_dir: Path | None = None) -> dict[str, Any]:
    cases = read_toml(ROOT / "evals" / "cases.toml")["cases"] if scenario is None else {}
    variants = read_toml(ROOT / "evals" / "variants.toml")["variants"]
    case = cases[safe_name(case_name)] if scenario is None else scenario
    variant = variants[safe_name(variant_name)]
    source = (ROOT / variant["source"]).resolve()
    if not source.is_dir():
        raise ValueError(f"variant source does not exist: {source}")
    output.mkdir(parents=True, exist_ok=True)
    attempts: list[dict[str, Any]] = []
    for attempt_number in range(1, retries + 2):
        workspace = Path(tempfile.mkdtemp(prefix=f"codex-eval-{case_name}-"))
        try:
            if scenario is None:
                materialize_case(case_name, case, workspace)
            else:
                assert fixture_dir is not None
                copy_utf8_tree(contained(fixture_dir / "workspace", fixture_dir), workspace)
            copy_variant_config(source, workspace)
            apply_role_overrides(workspace, variant.get("role_overrides", {}))
            if scenario is not None:
                apply_copied_role_overrides(workspace, fixture_dir, scenario.get("copied_role_overrides", []))
            pre_reproducibility = reproducibility_evidence(workspace)
            pre_reproducibility["task_specification"] = case
            pre_reproducibility["task_spec_sha256"] = hashlib.sha256(
                json.dumps(case, sort_keys=True, ensure_ascii=False).encode("utf-8")
            ).hexdigest()
            telemetry_path = SCRIPT_DIR / "eval_telemetry.py"
            pre_reproducibility["telemetry_sha256"] = sha256_file(telemetry_path) if telemetry_path.is_file() else "UNKNOWN"
            if scenario is not None:
                pre_reproducibility["scenario_spec_sha256"] = sha256_file(ROOT / "evals" / "scenarios.toml")
                pre_reproducibility["fixture_input_sha256"] = fixture_hashes(fixture_dir)
            pre_configuration = expected_configuration(workspace, variant)
            pre_agents_text = (normalize_text((workspace / "AGENTS.md").read_text(encoding="utf-8"))
                               if (workspace / "AGENTS.md").is_file() else None)
            command = command_for(workspace, case["prompt"], variant)
            process = run_process(command, workspace, timeout, case["prompt"], codex_home)
            stdout = process.pop("stdout")
            events, malformed = parse_jsonl(stdout)
            raw_jsonl = output / f"{case_name}--{variant_name}--attempt-{attempt_number}.jsonl"
            # Keep the CLI's line order and payload intact apart from secret
            # redaction, so a failed case remains inspectable after cleanup.
            raw_jsonl.write_text(sanitize(stdout), encoding="utf-8")
            checks = (scenario_checks(case.get("checks", []), fixture_dir, workspace)
                      if scenario is not None else evaluate_checks(workspace, case.get("checks", [])))
            session_evidence = session_activation_evidence(events, workspace, codex_home, read_session_activation, pre_agents_text)
            root_thread_id = primary_thread_id(events)
            whole_task = (whole_task_telemetry(root_thread_id, codex_home, True)
                          if read_whole_task_telemetry and root_thread_id is not None and codex_home is not None
                          else {"status": "DISABLED" if not read_whole_task_telemetry else "ROOT_THREAD_ID_UNKNOWN"})
            attempt = {"number": attempt_number, **process, "jsonl_events": len(events),
                       "command_argv": [sanitize(item) for item in command],
                       "jsonl_malformed_lines": malformed, "raw_jsonl": raw_jsonl.name,
                       "event_types": sorted({str(e.get("type")) for e in events}), "scheduling_evidence": scheduling_evidence(events),
                       "activation_evidence": activation_evidence(workspace, command, events),
                       "session_activation_evidence": session_evidence,
                       "configuration_validity": configuration_validity(pre_configuration, session_evidence, root_thread_id),
                       "reproducibility_evidence": {"pre": pre_reproducibility,
                                                      "post_copied_file_sha256": copied_file_hashes(workspace)},
                       "whole_task_telemetry": whole_task, "tokens": observed_tokens(events), "checks": checks,
                       "cli_succeeded": process["exit_code"] == 0 and not process["timed_out"],
                       "accepted": process["exit_code"] == 0 and not process["timed_out"] and all(item["passed"] for item in checks)}
            if keep_workspace:
                attempt["workspace"] = str(workspace)
                workspace = None  # type: ignore[assignment]
            attempts.append(sanitize_value(attempt))
            if attempt["accepted"] or process["timed_out"]:
                break
        finally:
            if workspace is not None:
                shutil.rmtree(workspace, ignore_errors=True)
    result = {"scenario" if scenario is not None else "case": case_name, "variant": variant_name, "description": case["description"],
              "variant_overrides": {key: variant[key] for key in ("model", "effort", "config", "role_overrides") if key in variant},
              "requested_main_model": variant.get("model", "fixture configuration"),
              "requested_main_reasoning_effort": variant.get("effort", "fixture configuration"),
              "attempts": attempts, "accepted": bool(attempts and attempts[-1]["accepted"])}
    if scenario is not None:
        result["coordination_mode"] = scenario.get("coordination_mode", "UNKNOWN")
        result["observation_rubric"] = observation_rubric(scenario.get("observation_rubric", []))
    target = output / f"{case_name}--{variant_name}.json"
    target.write_text(json.dumps(sanitize_value(result), indent=2) + "\n", encoding="utf-8")
    return result


def run_scenario(name: str, variant_name: str, output: Path, timeout: float, retries: int,
                 keep_workspace: bool = False, codex_home: Path | None = None,
                 read_session_activation: bool = False, read_whole_task_telemetry: bool = False) -> dict[str, Any]:
    scenario, fixture_dir = load_scenario(safe_name(name))
    return run_case(name, variant_name, output, timeout, retries, keep_workspace, codex_home,
                    read_session_activation, read_whole_task_telemetry, scenario, fixture_dir)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    subparsers.add_parser("list")
    run = subparsers.add_parser("run")
    selection = run.add_mutually_exclusive_group(required=True)
    selection.add_argument("--case")
    selection.add_argument("--scenario")
    run.add_argument("--variant", required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--timeout", type=float, default=300)
    run.add_argument("--retries", type=int, default=0)
    run.add_argument("--repetitions", type=int, default=1, help="run the same case repeatedly in separate result directories")
    run.add_argument("--keep-workspace", action="store_true")
    run.add_argument("--codex-home", type=Path, default=default_codex_home())
    run.add_argument("--read-session-activation", action="store_true", help="read matching persisted rollout metadata only")
    run.add_argument("--read-whole-task-telemetry", action="store_true", help="read explicitly linked persisted child rollouts")
    args = parser.parse_args()
    if args.action == "list":
        for name, item in read_toml(ROOT / "evals" / "cases.toml")["cases"].items():
            print(f"{name}: {item['description']}")
        return 0
    if args.timeout <= 0 or args.retries < 0 or args.repetitions <= 0:
        parser.error("timeout and repetitions must be positive and retries cannot be negative")
    results = []
    for repetition in range(1, args.repetitions + 1):
        destination = args.output if args.repetitions == 1 else args.output / f"repetition-{repetition:03d}"
        if args.scenario:
            results.append(run_scenario(args.scenario, args.variant, destination, args.timeout, args.retries,
                                        args.keep_workspace, args.codex_home, args.read_session_activation,
                                        args.read_whole_task_telemetry))
        else:
            results.append(run_case(args.case, args.variant, destination, args.timeout, args.retries,
                                    args.keep_workspace, args.codex_home, args.read_session_activation,
                                    args.read_whole_task_telemetry))
    accepted = all(result["accepted"] for result in results)
    print(json.dumps({"scenario" if args.scenario else "case": args.scenario or args.case,
                      "variant": args.variant, "repetitions": args.repetitions, "accepted": accepted}))
    return 0 if accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
