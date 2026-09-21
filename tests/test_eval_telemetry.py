import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "eval_telemetry.py"
SPEC = importlib.util.spec_from_file_location("eval_telemetry", SCRIPT)
telemetry = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = telemetry
SPEC.loader.exec_module(telemetry)

ROOT = "11111111-1111-1111-1111-111111111111"
CHILDREN = [f"{value:08d}-2222-2222-2222-222222222222" for value in range(1, 6)]
FORGED = "33333333-3333-3333-3333-333333333333"
MISSING = "44444444-4444-4444-4444-444444444444"
TURN = "eval-turn"


def write_rollout(home, thread_id, records, directory="2026/01"):
    path = home / "sessions" / directory / f"rollout-sanitized-{thread_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")


def usage(total):
    output = min(100, total)
    return {
        "input_tokens": total - output,
        "cached_input_tokens": min(1000, total - output),
        "cache_write_input_tokens": 0,
        "output_tokens": output,
        "reasoning_output_tokens": min(20, output),
        "total_tokens": total,
    }


def item_event(turn, item, start=1_000_100, end=1_000_200):
    return {
        "type": "event_msg",
        "payload": {
            "type": "item_completed",
            "turn_id": turn,
            "thread_id": item.get("sender_thread_id"),
            "started_at_ms": start,
            "completed_at_ms": end,
            "item": item,
        },
    }


def thread_records(
    thread_id,
    *,
    root_turn=TURN,
    local_turn=None,
    parent=None,
    role=None,
    model="gpt-5.6-luna",
    effort="medium",
    children=(),
    total=10,
    complete=True,
    include_items=True,
):
    local_turn = local_turn or root_turn
    source = {} if parent is None else {
        "subagent": {"thread_spawn": {"parent_thread_id": parent, "agent_role": role}}
    }
    records = [
        {"type": "session_meta", "payload": {"id": thread_id, "source": source, "private": "hidden"}},
        {"type": "turn_context", "payload": {
            "turn_id": local_turn, "root_turn_id": root_turn,
            "model": model, "effort": effort, "summary": "hidden",
        }},
        {"type": "event_msg", "payload": {
            "type": "task_started", "turn_id": local_turn, "started_at": 1000,
            "model_context_window": 999,
        }},
    ]
    if include_items:
        records.append(item_event(local_turn, {"type": "UserMessage", "content": "hidden"}))
    for index, child in enumerate(children):
        records.append(item_event(local_turn, {
            "type": "CollabAgentToolCall", "tool": "spawn_agent", "status": "completed",
            "sender_thread_id": thread_id, "receiver_thread_ids": [child], "prompt": "hidden",
        }, 1_000_300 + index * 10, 1_000_305 + index * 10))
    records.append({"type": "token_usage_record", "payload": {
        "thread_id": thread_id, "turn_id": local_turn, "root_turn_id": root_turn,
        "response_id": f"response-{thread_id}-{local_turn}", "usage": usage(total),
        "turn_token_usage": usage(total),
        "thread_token_usage": {"total_tokens": 999999},
    }})
    if complete:
        records.append({"type": "event_msg", "payload": {
            "type": "task_complete", "turn_id": local_turn,
            "started_at": 1000, "completed_at": 1002, "duration_ms": 2000,
            "last_agent_message": "hidden",
        }})
    return records


def by_id(evidence):
    return {node["thread_id"]: node for node in evidence.get("threads", [])}


def retime(records, local_turn, start, end):
    for record in records:
        payload = record.get("payload", {})
        if payload.get("turn_id") != local_turn:
            continue
        if payload.get("type") == "task_started":
            payload["started_at"] = start
        elif payload.get("type") == "task_complete":
            payload["started_at"] = start
            payload["completed_at"] = end


class TelemetryTests(unittest.TestCase):
    def test_real_shaped_six_node_rollout_totals_373209_without_private_text(self):
        totals = [163426, 43580, 40031, 39810, 39993, 46369]
        roles = ["ROOT", "fast_scan", "worker", "standard_worker", "complex_worker", "expert"]
        models = ["gpt-5.6-luna", "gpt-5.6-luna", "gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol", "gpt-6-astra"]
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".codex"
            write_rollout(home, ROOT, thread_records(ROOT, children=CHILDREN, total=totals[0]))
            for index, child in enumerate(CHILDREN, 1):
                write_rollout(home, child, thread_records(
                    child, local_turn=f"child-turn-{index}", parent=ROOT, role=roles[index],
                    model=models[index], children=(), total=totals[index],
                ))
            evidence = telemetry.whole_task_telemetry(ROOT, home)

        self.assertEqual("COMPLETE", evidence["status"])
        self.assertEqual(6, evidence["known_linked_thread_count"])
        self.assertEqual(6, evidence["complete_thread_count"])
        self.assertEqual(373209, evidence["observed_subtotal_tokens"])
        self.assertEqual(373209, evidence["complete_known_main_and_child_total_tokens"])
        self.assertEqual([], evidence["unobserved_children"])
        nodes = by_id(evidence)
        self.assertEqual(163426, nodes[ROOT]["usage"]["components"]["total_tokens"])
        self.assertEqual("gpt-6-astra", nodes[CHILDREN[-1]]["model"])
        self.assertEqual("expert", nodes[CHILDREN[-1]]["agent_role"])
        serialized = json.dumps(evidence)
        self.assertNotIn("hidden", serialized)
        self.assertNotIn("999999", serialized)

    def test_scopes_inherited_history_and_requires_root_turn_for_multiturn_root(self):
        old_child = "55555555-5555-5555-5555-555555555555"
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".codex"
            records = thread_records(ROOT, total=10)
            old = thread_records(ROOT, root_turn="old-turn", total=999)
            records.extend(old[1:])
            records.append(item_event("old-turn", {
                "type": "CollabAgentToolCall", "tool": "spawn_agent", "status": "completed",
                "sender_thread_id": ROOT, "receiver_thread_ids": [old_child], "prompt": "hidden",
            }))
            records.append({"type": "token_usage_record", "payload": {
                "thread_id": CHILDREN[0], "turn_id": TURN, "root_turn_id": TURN,
                "response_id": "wrong-thread", "usage": usage(888),
            }})
            write_rollout(home, ROOT, records)
            ambiguous = telemetry.whole_task_telemetry(ROOT, home)
            selected = telemetry.whole_task_telemetry(ROOT, home, root_turn_id=TURN)

        self.assertEqual("ROOT_TURN_AMBIGUOUS", ambiguous["status"])
        self.assertEqual(10, selected["complete_known_main_and_child_total_tokens"])
        self.assertEqual({ROOT}, set(by_id(selected)))

    def test_missing_and_forged_linked_children_make_whole_total_unknown(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".codex"
            write_rollout(home, ROOT, thread_records(ROOT, children=(MISSING, FORGED), total=10))
            write_rollout(home, FORGED, thread_records(
                FORGED, local_turn="forged-turn", parent=CHILDREN[0], role="worker", total=20,
            ))
            evidence = telemetry.whole_task_telemetry(ROOT, home)

        nodes = by_id(evidence)
        self.assertEqual("MISSING", nodes[MISSING]["status"])
        self.assertEqual("FORGED_LINKAGE", nodes[FORGED]["status"])
        self.assertEqual({MISSING, FORGED}, set(evidence["unobserved_children"]))
        self.assertEqual(10, evidence["observed_subtotal_tokens"])
        self.assertEqual("UNKNOWN", evidence["complete_known_main_and_child_total_tokens"])

    def test_deduplicates_identical_usage_and_rejects_conflicts_or_partial_components(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".codex"
            records = thread_records(ROOT, total=10)
            records.insert(-1, json.loads(json.dumps(records[-2])))
            write_rollout(home, ROOT, records)
            duplicate = telemetry.whole_task_telemetry(ROOT, home)
        node = by_id(duplicate)[ROOT]
        self.assertEqual(10, duplicate["complete_known_main_and_child_total_tokens"])
        self.assertEqual(1, node["usage"]["unique_response_count"])
        self.assertEqual(1, node["usage"]["duplicate_record_count"])

        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".codex"
            records = thread_records(ROOT, total=10)
            conflict = json.loads(json.dumps(records[-2]))
            conflict["payload"]["usage"] = usage(11)
            records.insert(-1, conflict)
            write_rollout(home, ROOT, records)
            evidence = telemetry.whole_task_telemetry(ROOT, home)
        self.assertEqual(1, by_id(evidence)[ROOT]["usage"]["conflicting_response_count"])
        self.assertEqual("UNKNOWN", evidence["complete_known_main_and_child_total_tokens"])

        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".codex"
            records = thread_records(ROOT, total=10)
            del records[-2]["payload"]["usage"]["cached_input_tokens"]
            write_rollout(home, ROOT, records)
            evidence = telemetry.whole_task_telemetry(ROOT, home)
        self.assertEqual("INCOMPLETE", by_id(evidence)[ROOT]["usage"]["status"])
        self.assertEqual("UNKNOWN", evidence["complete_known_main_and_child_total_tokens"])

    def test_no_item_events_or_mismatched_child_turn_scope_are_unknown(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".codex"
            write_rollout(home, ROOT, thread_records(ROOT, include_items=False))
            no_events = telemetry.whole_task_telemetry(ROOT, home)
        self.assertEqual("UNKNOWN_NO_ITEM_EVENTS", by_id(no_events)[ROOT]["child_discovery_status"])
        self.assertEqual("UNKNOWN", no_events["complete_known_main_and_child_total_tokens"])

        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".codex"
            write_rollout(home, ROOT, thread_records(ROOT, children=(CHILDREN[0],)))
            records = thread_records(CHILDREN[0], root_turn="unrelated-root", local_turn="child-turn",
                                     parent=ROOT, role="worker")
            write_rollout(home, CHILDREN[0], records)
            mismatch = telemetry.whole_task_telemetry(ROOT, home)
        self.assertEqual("TURN_SCOPE_MISSING", by_id(mismatch)[CHILDREN[0]]["status"])
        self.assertEqual("UNKNOWN", mismatch["complete_known_main_and_child_total_tokens"])

    def test_tool_intervals_filter_non_tools_and_ignore_unbounded_intervals(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".codex"
            records = thread_records(ROOT)
            records.insert(-1, item_event(TURN, {"type": "Reasoning", "raw_content": "hidden"}, 1100, 1200))
            records.insert(-1, item_event(TURN, {"type": "CommandExecution", "output": "hidden"}, 1300, 1345))
            unbounded = item_event(TURN, {"type": "CommandExecution", "output": "hidden"}, 1400, 1450)
            del unbounded["payload"]["started_at_ms"]
            records.insert(-1, unbounded)
            del records[2]["payload"]["started_at"]
            write_rollout(home, ROOT, records)
            evidence = telemetry.whole_task_telemetry(ROOT, home)

        node = by_id(evidence)[ROOT]
        self.assertEqual("INCOMPLETE", node["status"])
        self.assertEqual([
            {"type": "CommandExecution", "start_ms": 1300, "complete_ms": 1345, "duration_ms": 45}
        ], node["tool_intervals"])
        self.assertEqual([], evidence["overlapping_task_interval_pairs"])
        self.assertEqual("UNKNOWN", evidence["complete_known_main_and_child_total_tokens"])

    def test_failed_spawn_with_child_id_is_followed_and_empty_failure_is_visible(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".codex"
            records = thread_records(ROOT)
            records.insert(-2, item_event(TURN, {
                "type": "CollabAgentToolCall", "tool": "spawn_agent", "status": "failed",
                "sender_thread_id": ROOT, "receiver_thread_ids": [MISSING],
            }))
            records.insert(-2, item_event(TURN, {
                "type": "CollabAgentToolCall", "tool": "spawn_agent", "status": "failed",
                "sender_thread_id": ROOT, "receiver_thread_ids": [],
            }))
            write_rollout(home, ROOT, records)
            evidence = telemetry.whole_task_telemetry(ROOT, home)
        nodes = by_id(evidence)
        self.assertEqual("MISSING", nodes[MISSING]["status"])
        self.assertEqual(2, nodes[ROOT]["failed_spawn_count"])
        self.assertEqual("UNKNOWN", evidence["complete_known_main_and_child_total_tokens"])

    def test_malformed_child_discovery_and_ambiguous_rollout_are_conservative(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".codex"
            records = thread_records(ROOT)
            records.insert(-2, item_event(TURN, {
                "type": "CollabAgentToolCall", "tool": "spawn_agent", "status": "completed",
                "sender_thread_id": ROOT, "receiver_thread_ids": ["not-a-thread-id"],
            }))
            write_rollout(home, ROOT, records)
            malformed = telemetry.whole_task_telemetry(ROOT, home)
        self.assertEqual("MALFORMED", by_id(malformed)[ROOT]["child_discovery_status"])
        self.assertEqual("UNKNOWN", malformed["complete_known_main_and_child_total_tokens"])

        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".codex"
            write_rollout(home, ROOT, thread_records(ROOT, children=(CHILDREN[0],)))
            child_records = thread_records(
                CHILDREN[0], local_turn="child-turn", parent=ROOT, role="worker"
            )
            write_rollout(home, CHILDREN[0], child_records, directory="2026/01")
            write_rollout(home, CHILDREN[0], child_records, directory="2026/02")
            ambiguous = telemetry.whole_task_telemetry(ROOT, home)
        self.assertEqual("AMBIGUOUS_ROLLOUT", by_id(ambiguous)[CHILDREN[0]]["status"])
        self.assertIn(CHILDREN[0], ambiguous["unobserved_children"])
        self.assertEqual("UNKNOWN", ambiguous["complete_known_main_and_child_total_tokens"])

    def test_reused_child_counts_all_turns_under_selected_root_and_excludes_later_root(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".codex"
            write_rollout(home, ROOT, thread_records(ROOT, children=(CHILDREN[0],), total=10))
            first = thread_records(
                CHILDREN[0], local_turn="child-first", parent=ROOT, role="worker",
                model="gpt-5.6-luna", effort="medium", total=20,
            )
            second = thread_records(
                CHILDREN[0], local_turn="child-followup", parent=ROOT, role="worker",
                model="gpt-5.6-sol", effort="high", total=30,
            )
            unrelated = thread_records(
                CHILDREN[0], root_turn="later-root", local_turn="child-later",
                parent=ROOT, role="worker", model="gpt-6-astra", effort="high", total=999,
            )
            write_rollout(home, CHILDREN[0], first + second[1:] + unrelated[1:])
            evidence = telemetry.whole_task_telemetry(ROOT, home)

        child = by_id(evidence)[CHILDREN[0]]
        self.assertEqual("COMPLETE", evidence["status"])
        self.assertEqual(60, evidence["complete_known_main_and_child_total_tokens"])
        self.assertEqual(["child-first", "child-followup"], child["local_turn_ids"])
        self.assertEqual(50, child["usage"]["observed_total_tokens"])
        self.assertEqual(50, child["complete_thread_total_tokens"])
        self.assertEqual(2, child["usage"]["unique_response_count"])
        self.assertEqual("UNKNOWN", child["model"])
        self.assertEqual("UNKNOWN", child["effort"])
        contexts = {turn["local_turn_id"]: (turn["model"], turn["effort"]) for turn in child["turns"]}
        self.assertEqual(("gpt-5.6-luna", "medium"), contexts["child-first"])
        self.assertEqual(("gpt-5.6-sol", "high"), contexts["child-followup"])
        self.assertNotIn("child-later", json.dumps(child))

    def test_turn_cumulative_usage_detects_missing_response_but_absence_is_unknown(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".codex"
            records = thread_records(ROOT, total=10)
            records[-2]["payload"]["turn_token_usage"]["total_tokens"] = 11
            write_rollout(home, ROOT, records)
            mismatch = telemetry.whole_task_telemetry(ROOT, home)
        root = by_id(mismatch)[ROOT]
        self.assertEqual("MISMATCH", root["turns"][0]["usage"]["cumulative_crosscheck"])
        self.assertEqual("UNKNOWN", mismatch["complete_known_main_and_child_total_tokens"])

        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".codex"
            records = thread_records(ROOT, total=10)
            del records[-2]["payload"]["turn_token_usage"]
            write_rollout(home, ROOT, records)
            absent = telemetry.whole_task_telemetry(ROOT, home)
        root = by_id(absent)[ROOT]
        self.assertEqual("UNKNOWN", root["turns"][0]["usage"]["cumulative_crosscheck"])
        self.assertEqual(10, absent["complete_known_main_and_child_total_tokens"])

    def test_overlap_uses_each_reused_child_turn_instead_of_spanning_the_gap(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".codex"
            write_rollout(home, ROOT, thread_records(ROOT, children=CHILDREN[:2], total=10))
            first = thread_records(
                CHILDREN[0], local_turn="first", parent=ROOT, role="worker", total=20
            )
            second = thread_records(
                CHILDREN[0], local_turn="second", parent=ROOT, role="worker", total=30
            )
            between = thread_records(
                CHILDREN[1], local_turn="between", parent=ROOT, role="worker", total=40
            )
            retime(first, "first", 1003, 1005)
            retime(second, "second", 1010, 1012)
            retime(between, "between", 1006, 1008)
            write_rollout(home, CHILDREN[0], first + second[1:])
            write_rollout(home, CHILDREN[1], between)
            evidence = telemetry.whole_task_telemetry(ROOT, home)
        self.assertEqual(100, evidence["complete_known_main_and_child_total_tokens"])
        self.assertEqual([], evidence["overlapping_task_interval_pairs"])
        self.assertEqual([], evidence["overlapping_turn_interval_pairs"])

    def test_matching_child_context_without_start_is_retained_as_incomplete_turn(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / ".codex"
            write_rollout(home, ROOT, thread_records(ROOT, children=(CHILDREN[0],), total=10))
            first = thread_records(
                CHILDREN[0], local_turn="complete-turn", parent=ROOT, role="worker", total=20
            )
            incomplete = thread_records(
                CHILDREN[0], local_turn="missing-start", parent=ROOT, role="worker", total=30
            )
            incomplete = [
                record for record in incomplete
                if not (record.get("type") == "event_msg"
                        and record.get("payload", {}).get("type") == "task_started")
            ]
            write_rollout(home, CHILDREN[0], first + incomplete[1:])
            evidence = telemetry.whole_task_telemetry(ROOT, home)
        child = by_id(evidence)[CHILDREN[0]]
        self.assertEqual(["complete-turn", "missing-start"], child["local_turn_ids"])
        self.assertEqual("INCOMPLETE", child["status"])
        self.assertEqual("UNKNOWN", child["complete_thread_total_tokens"])
        self.assertEqual("UNKNOWN", evidence["complete_known_main_and_child_total_tokens"])


if __name__ == "__main__":
    unittest.main()
