"""Privacy-preserving telemetry for explicitly linked Codex evaluation threads.

Only structural metadata, timestamps, model settings, and token counts leave this
module. Prompts, messages, reasoning, command output, and tool output are never
copied into the returned value.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any


UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
USAGE_KEYS = (
    "input_tokens", "cached_input_tokens", "cache_write_input_tokens",
    "output_tokens", "reasoning_output_tokens", "total_tokens",
)
NON_TOOL_ITEM_TYPES = {"UserMessage", "AgentMessage", "Reasoning"}
KNOWN_TOOL_ITEM_TYPES = {
    "CommandExecution", "ComputerAction", "FileChange", "ImageGeneration", "WebSearch",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rollouts(sessions: Path, thread_id: str) -> list[Path]:
    if not sessions.is_dir():
        return []
    root = sessions.resolve()
    result = []
    for path in sessions.rglob("*.jsonl"):
        if not path.name.endswith(f"-{thread_id}.jsonl"):
            continue
        try:
            path.resolve().relative_to(root)
        except ValueError:
            continue
        if path.is_file():
            result.append(path)
    return sorted(result)


def _records(path: Path) -> tuple[list[dict[str, Any]], int]:
    result = []
    malformed = 0
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                malformed += 1
                continue
            if isinstance(item, dict):
                result.append(item)
            else:
                malformed += 1
    return result, malformed


def _payload(record: dict[str, Any]) -> dict[str, Any]:
    return record.get("payload") if isinstance(record.get("payload"), dict) else {}


def _event_type(record: dict[str, Any]) -> str:
    payload_type = _payload(record).get("type")
    if record.get("type") == "event_msg" and isinstance(payload_type, str):
        return payload_type
    return str(record.get("type") or "")


def _turn_id(record: dict[str, Any]) -> str | None:
    value = _payload(record).get("turn_id")
    return value if isinstance(value, str) and value else None


def _time_ms(record: dict[str, Any], field: str, *, fallback_timestamp: bool = False) -> int | None:
    payload = _payload(record)
    value_ms = payload.get(f"{field}_ms")
    if isinstance(value_ms, int) and not isinstance(value_ms, bool):
        return value_ms
    value = payload.get(field)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value * 1000)
    if isinstance(value, str):
        try:
            return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)
        except ValueError:
            pass
    if fallback_timestamp:
        timestamp = record.get("timestamp")
        if isinstance(timestamp, str):
            try:
                return int(datetime.fromisoformat(timestamp.replace("Z", "+00:00")).timestamp() * 1000)
            except ValueError:
                pass
    return None


def _session_meta(records: list[dict[str, Any]], thread_id: str) -> tuple[dict[str, Any], str]:
    metas = [_payload(record) for record in records if record.get("type") == "session_meta"]
    if len(metas) != 1:
        return {}, "MISSING" if not metas else "AMBIGUOUS"
    if metas[0].get("id") != thread_id:
        return {}, "ID_MISMATCH"
    return metas[0], "VALID"


def _child_link(meta: dict[str, Any]) -> tuple[str | None, str | None]:
    source = meta.get("source")
    subagent = source.get("subagent") if isinstance(source, dict) else None
    spawn = subagent.get("thread_spawn") if isinstance(subagent, dict) else None
    if not isinstance(spawn, dict):
        return None, None
    parent = spawn.get("parent_thread_id")
    role = spawn.get("agent_role")
    return parent if isinstance(parent, str) else None, role if isinstance(role, str) else None


def _task_turns(records: list[dict[str, Any]]) -> set[str]:
    return {
        turn for record in records if _event_type(record) == "task_started"
        for turn in [_turn_id(record)] if turn is not None
    }


def _local_turns(records: list[dict[str, Any]], root_turn_id: str, *, root: bool) -> set[str]:
    if root:
        return {root_turn_id} if root_turn_id in _task_turns(records) else set()
    contextual = {
        payload["turn_id"]
        for record in records if record.get("type") == "turn_context"
        for payload in [_payload(record)]
        if payload.get("root_turn_id") == root_turn_id
        and isinstance(payload.get("turn_id"), str) and payload["turn_id"]
    }
    # A matching turn_context is itself evidence of a scoped child turn. Keep it
    # even when its task_started event is missing so coverage becomes incomplete
    # instead of silently dropping the turn.
    return contextual


def _context(records: list[dict[str, Any]], local_turn_id: str, root_turn_id: str) -> tuple[str, str]:
    contexts = []
    for record in records:
        if record.get("type") != "turn_context":
            continue
        payload = _payload(record)
        if payload.get("turn_id") == local_turn_id and payload.get("root_turn_id") == root_turn_id:
            contexts.append(payload)
    models = {item["model"] for item in contexts if isinstance(item.get("model"), str)}
    efforts = {item["effort"] for item in contexts if isinstance(item.get("effort"), str)}
    return (
        next(iter(models)) if len(models) == 1 else "UNKNOWN",
        next(iter(efforts)) if len(efforts) == 1 else "UNKNOWN",
    )


def _task_interval(records: list[dict[str, Any]], local_turn_id: str) -> dict[str, Any]:
    starts = {
        value
        for record in records
        if _event_type(record) == "task_started" and _turn_id(record) == local_turn_id
        for value in [_time_ms(record, "started_at", fallback_timestamp=True)]
        if value is not None
    }
    ends = {
        value
        for record in records
        if _event_type(record) == "task_complete" and _turn_id(record) == local_turn_id
        for value in [_time_ms(record, "completed_at", fallback_timestamp=True)]
        if value is not None
    }
    start: int | str = next(iter(starts)) if len(starts) == 1 else "UNKNOWN"
    end: int | str = next(iter(ends)) if len(ends) == 1 else "UNKNOWN"
    bounded = isinstance(start, int) and isinstance(end, int) and end >= start
    return {
        "start_ms": start, "complete_ms": end,
        "duration_ms": end - start if bounded else "UNKNOWN",
        "status": "COMPLETED" if bounded else "INCOMPLETE",
    }


def _is_item_event(record: dict[str, Any], local_turn_id: str) -> bool:
    return (
        record.get("type") == "event_msg" and _event_type(record) == "item_completed"
        and _turn_id(record) == local_turn_id
    )


def _explicit_children(
    records: list[dict[str, Any]], thread_id: str, local_turn_id: str
) -> tuple[set[str], str, int]:
    item_events = [record for record in records if _is_item_event(record, local_turn_id)]
    if not item_events:
        return set(), "UNKNOWN_NO_ITEM_EVENTS", 0
    children: set[str] = set()
    malformed = False
    failed_spawn_count = 0
    for record in item_events:
        item = _payload(record).get("item")
        if not isinstance(item, dict) or item.get("type") != "CollabAgentToolCall" or item.get("tool") != "spawn_agent":
            continue
        if item.get("status") != "completed":
            failed_spawn_count += 1
        if item.get("sender_thread_id") != thread_id:
            malformed = True
            continue
        receiver_ids = item.get("receiver_thread_ids")
        if not isinstance(receiver_ids, list):
            malformed = item.get("status") == "completed" or malformed
            continue
        if not receiver_ids:
            malformed = item.get("status") == "completed" or malformed
            continue
        valid = {value for value in receiver_ids if isinstance(value, str) and UUID.fullmatch(value)}
        if len(valid) != len(receiver_ids):
            malformed = True
        children.update(valid)
    return children, "MALFORMED" if malformed else "SUPPORTED", failed_spawn_count


def _is_tool_item(item: dict[str, Any]) -> bool:
    item_type = item.get("type")
    if not isinstance(item_type, str) or item_type in NON_TOOL_ITEM_TYPES:
        return False
    return item_type in KNOWN_TOOL_ITEM_TYPES or item_type.endswith("ToolCall")


def _tool_intervals(records: list[dict[str, Any]], local_turn_id: str) -> list[dict[str, Any]]:
    result = []
    for record in records:
        if not _is_item_event(record, local_turn_id):
            continue
        payload = _payload(record)
        item = payload.get("item")
        if not isinstance(item, dict) or not _is_tool_item(item):
            continue
        start = _time_ms(record, "started_at")
        end = _time_ms(record, "completed_at")
        if not isinstance(start, int) or not isinstance(end, int) or end < start:
            continue
        result.append({
            "type": item["type"], "start_ms": start, "complete_ms": end,
            "duration_ms": end - start,
        })
    return result


def _valid_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _usage_components(unique: list[dict[str, Any]]) -> dict[str, int | str]:
    components: dict[str, int | str] = {}
    for key in USAGE_KEYS:
        values = [usage[key] for usage in unique]
        components[key] = sum(values) if values and all(_valid_count(value) for value in values) else "UNKNOWN"
    return components


def _internally_consistent(usages: list[dict[str, Any]]) -> bool:
    return all(
        all(_valid_count(usage[key]) for key in USAGE_KEYS)
        and usage["total_tokens"] == usage["input_tokens"] + usage["output_tokens"]
        and usage["cached_input_tokens"] <= usage["input_tokens"]
        and usage["cache_write_input_tokens"] <= usage["input_tokens"]
        and usage["reasoning_output_tokens"] <= usage["output_tokens"]
        for usage in usages
    )


def _usage_for_turns(
    records: list[dict[str, Any]], thread_id: str, root_turn_id: str,
    local_turn_ids: set[str],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    unique: dict[str, tuple[str, dict[str, Any]]] = {}
    duplicate_count = conflict_count = invalid_count = 0
    per_turn_invalid = {turn: 0 for turn in local_turn_ids}
    per_turn_conflicts = {turn: 0 for turn in local_turn_ids}
    scoped_records: dict[str, list[dict[str, Any]]] = {turn: [] for turn in local_turn_ids}
    for record in records:
        if record.get("type") != "token_usage_record":
            continue
        payload = _payload(record)
        local_turn_id = payload.get("turn_id")
        if (payload.get("thread_id") != thread_id or payload.get("root_turn_id") != root_turn_id
                or local_turn_id not in local_turn_ids):
            continue
        scoped_records[local_turn_id].append(record)
        response_id = payload.get("response_id")
        usage = payload.get("usage")
        if not isinstance(response_id, str) or not response_id or not isinstance(usage, dict):
            invalid_count += 1
            per_turn_invalid[local_turn_id] += 1
            continue
        normalized = {key: usage.get(key) for key in USAGE_KEYS}
        if response_id in unique:
            duplicate_count += 1
            prior_turn, prior_usage = unique[response_id]
            if prior_turn != local_turn_id or prior_usage != normalized:
                conflict_count += 1
                per_turn_conflicts[prior_turn] += 1
                per_turn_conflicts[local_turn_id] += 1
            continue
        unique[response_id] = local_turn_id, normalized

    turn_results: dict[str, dict[str, Any]] = {}
    for local_turn_id in sorted(local_turn_ids):
        turn_usages = [usage for turn, usage in unique.values() if turn == local_turn_id]
        components = _usage_components(turn_usages)
        cumulative = None
        for record in scoped_records[local_turn_id]:
            candidate = _payload(record).get("turn_token_usage")
            if isinstance(candidate, dict):
                cumulative = candidate
        compared_keys = [key for key in USAGE_KEYS if cumulative is not None and _valid_count(cumulative.get(key))]
        mismatch = any(
            not isinstance(components[key], int) or cumulative[key] != components[key]
            for key in compared_keys
        )
        crosscheck = "MISMATCH" if mismatch else "MATCH" if compared_keys else "UNKNOWN"
        complete = (
            bool(turn_usages) and not per_turn_invalid[local_turn_id]
            and not per_turn_conflicts[local_turn_id] and _internally_consistent(turn_usages)
            and crosscheck != "MISMATCH"
        )
        turn_results[local_turn_id] = {
            "status": "COMPLETE" if complete else "INCOMPLETE",
            "unique_response_count": len(turn_usages),
            "invalid_record_count": per_turn_invalid[local_turn_id],
            "conflicting_response_count": per_turn_conflicts[local_turn_id],
            "components": components if not per_turn_conflicts[local_turn_id]
            else {key: "UNKNOWN" for key in USAGE_KEYS},
            "cumulative_crosscheck": crosscheck,
            "observed_total_tokens": components["total_tokens"] if complete else "UNKNOWN",
        }

    all_complete = bool(turn_results) and all(item["status"] == "COMPLETE" for item in turn_results.values())
    aggregate_components = {
        key: sum(item["components"][key] for item in turn_results.values())
        if all(isinstance(item["components"][key], int) for item in turn_results.values())
        else "UNKNOWN"
        for key in USAGE_KEYS
    }
    aggregate = {
        "status": "COMPLETE" if all_complete else "INCOMPLETE",
        "unique_response_count": len(unique),
        "duplicate_record_count": duplicate_count,
        "conflicting_response_count": conflict_count,
        "invalid_record_count": invalid_count,
        "components": aggregate_components if not conflict_count else {key: "UNKNOWN" for key in USAGE_KEYS},
        "observed_total_tokens": aggregate_components["total_tokens"] if all_complete else "UNKNOWN",
    }
    return aggregate, turn_results


def _base_result(status: str, root_thread_id: str) -> dict[str, Any]:
    return {
        "status": status, "root_thread_id": root_thread_id,
        "complete_known_main_and_child_total_tokens": "UNKNOWN",
    }


def whole_task_telemetry(
    root_thread_id: str,
    codex_home: Path,
    enabled: bool = True,
    root_turn_id: str | None = None,
) -> dict[str, Any]:
    """Read explicitly linked rollouts and return metadata and metrics, never text.

    ``root_turn_id`` selects one root task in a reused root session. Without it,
    a root session containing more than one task is deliberately ambiguous.
    """
    if not enabled:
        return _base_result("DISABLED", root_thread_id)
    if not UUID.fullmatch(root_thread_id):
        return _base_result("INVALID_ROOT_THREAD_ID", root_thread_id)
    sessions = codex_home / "sessions"
    root_paths = _rollouts(sessions, root_thread_id)
    if len(root_paths) != 1:
        return _base_result("ROOT_MISSING" if not root_paths else "ROOT_AMBIGUOUS", root_thread_id)
    try:
        root_records, _ = _records(root_paths[0])
    except (OSError, UnicodeDecodeError):
        return _base_result("ROOT_UNREADABLE", root_thread_id)

    candidates = _task_turns(root_records)
    selected_root_turn = root_turn_id
    if selected_root_turn is None:
        if len(candidates) != 1:
            result = _base_result("ROOT_TURN_MISSING" if not candidates else "ROOT_TURN_AMBIGUOUS", root_thread_id)
            result["available_root_turn_ids"] = sorted(candidates)
            return result
        selected_root_turn = next(iter(candidates))
    elif selected_root_turn not in candidates:
        result = _base_result("ROOT_TURN_MISSING", root_thread_id)
        result["available_root_turn_ids"] = sorted(candidates)
        return result

    nodes: dict[str, dict[str, Any]] = {}
    expected_parents: dict[str, set[str]] = {root_thread_id: set()}
    queue = [root_thread_id]
    processed: set[str] = set()
    graph_ambiguous: set[str] = set()
    while queue:
        thread_id = queue.pop(0)
        if thread_id in processed:
            continue
        processed.add(thread_id)
        parents = expected_parents.get(thread_id, set())
        if thread_id != root_thread_id and len(parents) != 1:
            nodes[thread_id] = {"thread_id": thread_id, "status": "AMBIGUOUS_PARENT_LINKAGE"}
            graph_ambiguous.add(thread_id)
            continue
        expected_parent = next(iter(parents)) if parents else None

        paths = _rollouts(sessions, thread_id)
        if len(paths) != 1:
            nodes[thread_id] = {
                "thread_id": thread_id, "parent_thread_id": expected_parent or "ROOT",
                "status": "MISSING" if not paths else "AMBIGUOUS_ROLLOUT",
            }
            continue
        path = paths[0]
        try:
            records, malformed_lines = _records(path)
        except (OSError, UnicodeDecodeError):
            nodes[thread_id] = {
                "thread_id": thread_id, "parent_thread_id": expected_parent or "ROOT",
                "status": "UNREADABLE", "rollout_path": str(path),
            }
            continue

        meta, meta_status = _session_meta(records, thread_id)
        parent, role = _child_link(meta)
        if meta_status != "VALID":
            nodes[thread_id] = {
                "thread_id": thread_id, "parent_thread_id": expected_parent or "ROOT",
                "status": f"SESSION_META_{meta_status}", "rollout_path": str(path),
                "rollout_sha256": _sha256(path),
            }
            continue
        if expected_parent is not None and parent != expected_parent:
            nodes[thread_id] = {
                "thread_id": thread_id, "parent_thread_id": parent or "UNKNOWN",
                "status": "FORGED_LINKAGE", "rollout_path": str(path),
                "rollout_sha256": _sha256(path),
            }
            continue

        local_turns = _local_turns(records, selected_root_turn, root=thread_id == root_thread_id)
        if not local_turns:
            nodes[thread_id] = {
                "thread_id": thread_id, "parent_thread_id": parent or "ROOT",
                "agent_role": role or "ROOT",
                "status": "TURN_SCOPE_MISSING",
                "rollout_path": str(path), "rollout_sha256": _sha256(path),
            }
            continue
        usage, per_turn_usage = _usage_for_turns(records, thread_id, selected_root_turn, local_turns)
        turns = []
        children: set[str] = set()
        failed_spawn_count = 0
        for local_turn in sorted(local_turns):
            model, effort = _context(records, local_turn, selected_root_turn)
            interval = _task_interval(records, local_turn)
            turn_children, discovery_status, turn_failed_spawns = _explicit_children(
                records, thread_id, local_turn
            )
            if malformed_lines:
                discovery_status = "MALFORMED_RECORDS"
            children.update(turn_children)
            failed_spawn_count += turn_failed_spawns
            turns.append({
                "local_turn_id": local_turn, "model": model, "effort": effort,
                "status": interval["status"], "task_interval": interval,
                "usage": per_turn_usage[local_turn],
                "child_discovery_status": discovery_status,
                "explicit_child_count": len(turn_children),
                "failed_spawn_count": turn_failed_spawns,
                "tool_intervals": _tool_intervals(records, local_turn),
            })
        discovery_statuses = {turn["child_discovery_status"] for turn in turns}
        discovery_status = (
            "SUPPORTED" if discovery_statuses == {"SUPPORTED"}
            else "MALFORMED_RECORDS" if "MALFORMED_RECORDS" in discovery_statuses
            else "MALFORMED" if "MALFORMED" in discovery_statuses
            else "UNKNOWN_NO_ITEM_EVENTS"
        )
        models = {turn["model"] for turn in turns}
        efforts = {turn["effort"] for turn in turns}
        interval = turns[0]["task_interval"] if len(turns) == 1 else "MULTIPLE"
        all_turns_complete = all(turn["status"] == "COMPLETED" for turn in turns)
        complete_thread_total = (
            usage["observed_total_tokens"]
            if all_turns_complete and discovery_status == "SUPPORTED" and usage["status"] == "COMPLETE"
            else "UNKNOWN"
        )
        nodes[thread_id] = {
            "thread_id": thread_id,
            "local_turn_id": turns[0]["local_turn_id"] if len(turns) == 1 else "MULTIPLE",
            "local_turn_ids": [turn["local_turn_id"] for turn in turns],
            "root_turn_id": selected_root_turn, "parent_thread_id": parent or "ROOT",
            "agent_role": role or "ROOT",
            "status": "COMPLETED" if all_turns_complete else "INCOMPLETE",
            "rollout_path": str(path), "rollout_sha256": _sha256(path),
            "model": next(iter(models)) if len(models) == 1 else "UNKNOWN",
            "effort": next(iter(efforts)) if len(efforts) == 1 else "UNKNOWN",
            "turns": turns, "task_interval": interval,
            "task_intervals": [turn["task_interval"] for turn in turns], "usage": usage,
            "complete_thread_total_tokens": complete_thread_total,
            "child_discovery_status": discovery_status, "explicit_child_count": len(children),
            "failed_spawn_count": failed_spawn_count,
            "tool_intervals": [tool for turn in turns for tool in turn["tool_intervals"]],
            "unobserved_tool_delays": "UNKNOWN",
        }
        for child in sorted(children):
            if child == root_thread_id:
                graph_ambiguous.add(child)
                continue
            expected_parents.setdefault(child, set()).add(thread_id)
            if child in processed and len(expected_parents[child]) > 1:
                graph_ambiguous.add(child)
                nodes[child]["status"] = "AMBIGUOUS_PARENT_LINKAGE"
            elif child not in processed:
                queue.append(child)

    observed_values = [
        node.get("usage", {}).get("observed_total_tokens")
        for node in nodes.values() if isinstance(node.get("usage"), dict)
    ]
    observed_subtotal = (
        sum(value for value in observed_values if isinstance(value, int))
        if any(isinstance(value, int) for value in observed_values) else "UNKNOWN"
    )
    incomplete_nodes = [
        thread_id for thread_id, node in nodes.items()
        if node.get("status") != "COMPLETED"
        or node.get("child_discovery_status") != "SUPPORTED"
        or node.get("usage", {}).get("status") != "COMPLETE"
    ]
    unobserved_statuses = {
        "MISSING", "AMBIGUOUS_ROLLOUT", "UNREADABLE", "FORGED_LINKAGE",
        "SESSION_META_MISSING", "SESSION_META_AMBIGUOUS", "SESSION_META_ID_MISMATCH",
        "TURN_SCOPE_MISSING", "TURN_SCOPE_AMBIGUOUS", "AMBIGUOUS_PARENT_LINKAGE",
    }
    unobserved_children = sorted(
        thread_id for thread_id, node in nodes.items()
        if thread_id != root_thread_id and node.get("status") in unobserved_statuses
    )
    complete = bool(nodes) and not incomplete_nodes and not graph_ambiguous
    total = (
        sum(node["complete_thread_total_tokens"] for node in nodes.values())
        if complete else "UNKNOWN"
    )

    bounded_intervals = [
        (node["thread_id"], turn["local_turn_id"], turn["task_interval"]["start_ms"],
         turn["task_interval"]["complete_ms"])
        for node in nodes.values() if isinstance(node.get("turns"), list)
        for turn in node["turns"]
        if isinstance(turn.get("task_interval"), dict)
        and isinstance(turn["task_interval"].get("start_ms"), int)
        and isinstance(turn["task_interval"].get("complete_ms"), int)
        and turn["task_interval"]["complete_ms"] >= turn["task_interval"]["start_ms"]
    ]
    thread_overlap_pairs: set[tuple[str, str]] = set()
    turn_overlaps = []
    for index, (left_id, left_turn, left_start, left_end) in enumerate(bounded_intervals):
        for right_id, right_turn, right_start, right_end in bounded_intervals[index + 1:]:
            if left_start < right_end and right_start < left_end:
                turn_overlaps.append([
                    {"thread_id": left_id, "local_turn_id": left_turn},
                    {"thread_id": right_id, "local_turn_id": right_turn},
                ])
                if left_id != right_id:
                    thread_overlap_pairs.add(tuple(sorted((left_id, right_id))))

    return {
        "status": "COMPLETE" if complete else "PARTIAL",
        "root_thread_id": root_thread_id, "root_turn_id": selected_root_turn,
        "threads": list(nodes.values()), "known_linked_thread_count": len(nodes),
        "complete_thread_count": sum(node.get("status") == "COMPLETED" for node in nodes.values()),
        "observed_subtotal_tokens": observed_subtotal,
        "complete_known_main_and_child_total_tokens": total,
        "unobserved_children": unobserved_children,
        "incomplete_evidence_threads": sorted(set(incomplete_nodes) | graph_ambiguous),
        "overlapping_task_interval_pairs": [list(pair) for pair in sorted(thread_overlap_pairs)],
        "overlapping_turn_interval_pairs": turn_overlaps,
        "simultaneous_execution": "UNKNOWN",
    }
