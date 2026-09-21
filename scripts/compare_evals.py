#!/usr/bin/env python3
"""Read evaluation result JSON and produce conservative comparison summaries."""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from pathlib import Path
from typing import Any

RATES = {
    "gpt-5.6-luna": {"input": 5, "cached": 0.5, "output": 30},
    "gpt-5.6-terra": {"input": 50, "cached": 5, "output": 300},
    "gpt-5.6-sol": {"input": 100, "cached": 10, "output": 500},
    "gpt-6-astra": {"input": 250, "cached": 25, "output": 1250},
}
RATE_NOTE = {"kind": "STANDARD reference credits, not actual account usage, USD, or cost",
             "source": "https://learn.chatgpt.com/docs/pricing", "checked": "2026-09-12", "per": "1M tokens"}


def result_paths(inputs: list[Path]) -> list[Path]:
    paths = []
    for item in inputs:
        candidates = item.rglob("*.json") if item.is_dir() else [item]
        for path in candidates:
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                continue
            if isinstance(document, dict) and isinstance(document.get("variant"), str) and isinstance(document.get("attempts"), list) and ("case" in document or "scenario" in document):
                paths.append(path)
    return sorted(set(paths))


def valid_count(value: Any) -> bool:
    """Counts in telemetry must be nonnegative integers, never booleans."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def identity(document: dict[str, Any], attempt: dict[str, Any]) -> dict[str, Any] | None:
    repro = attempt.get("reproducibility_evidence", {}) if isinstance(attempt, dict) else {}
    pre = repro.get("pre", repro) if isinstance(repro, dict) else {}
    name = document.get("scenario") or document.get("case")
    if not isinstance(name, str) or not isinstance(pre, dict):
        return None
    if "scenario" in document:
        fixture = pre.get("fixture_input_sha256")
        spec = pre.get("task_spec_sha256", pre.get("scenario_spec_sha256"))
    else:
        fixture = pre.get("task_spec_sha256", pre.get("case_spec_sha256"))
        spec = fixture
    if not isinstance(fixture, (str, dict)) or not isinstance(spec, str):
        return None
    encoded = json.dumps({"name": name, "fixture": fixture, "spec": spec}, sort_keys=True)
    return {"name": name, "input_hash": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
            "kind": "scenario" if "scenario" in document else "case"}


def configuration_revision(attempt: dict[str, Any]) -> str | None:
    repro = attempt.get("reproducibility_evidence", {})
    pre = repro.get("pre", repro) if isinstance(repro, dict) else {}
    copied = pre.get("copied_file_sha256") if isinstance(pre, dict) else None
    if not isinstance(copied, dict):
        return None
    return json.dumps(copied, sort_keys=True, separators=(",", ":"))


def trial_rows(path: Path, document: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for attempt in document.get("attempts", []):
        if not isinstance(attempt, dict):
            continue
        config = attempt.get("configuration_validity", {}) if isinstance(attempt, dict) else {}
        rubric = document.get("observation_rubric", [])
        behavior = [item.get("status", "UNKNOWN") for item in rubric if isinstance(item, dict)] or ["UNKNOWN"]
        rows.append({"path": str(path), "identity": identity(document, attempt), "configuration_revision": configuration_revision(attempt), "variant": document["variant"], "number": attempt.get("number"),
                     "latency_seconds": attempt.get("elapsed_seconds", "UNKNOWN"), "accepted": attempt.get("accepted", "UNKNOWN"),
                     "configuration_valid": config.get("valid", "UNKNOWN"), "behavior_statuses": behavior,
                     "whole_task_telemetry": attempt.get("whole_task_telemetry", "UNKNOWN")})
    return rows


def token_breakdown(telemetry: Any) -> dict[str, Any]:
    """Present collector evidence without rebuilding totals from partial data."""
    if not isinstance(telemetry, dict) or not isinstance(telemetry.get("threads"), list):
        return {"full_total": "UNKNOWN", "observed_subtotal": "UNKNOWN", "by_model": "UNKNOWN"}
    by_model: dict[str, dict[str, Any]] = {}
    for thread in telemetry["threads"]:
        if not isinstance(thread, dict):
            continue
        model = thread.get("model")
        usage = thread.get("usage")
        components = usage.get("components") if isinstance(usage, dict) and isinstance(usage.get("components"), dict) else None
        if not isinstance(model, str) or not isinstance(components, dict):
            continue
        bucket = by_model.setdefault(model, {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0,
                                              "observed_subtotal_tokens": 0, "complete_component_coverage": True,
                                              "completed_threads_only": True, "observed_subtotal_coverage": True})
        observed = usage.get("observed_total_tokens") if isinstance(usage, dict) else None
        if valid_count(observed) and valid_count(bucket["observed_subtotal_tokens"]):
            bucket["observed_subtotal_tokens"] += observed
        else:
            bucket["observed_subtotal_coverage"] = False
        values = {key: components.get(key) for key in
                  ("input_tokens", "cached_input_tokens", "output_tokens", "total_tokens")}
        component_complete = (
            thread.get("status") == "COMPLETED"
            and isinstance(usage, dict) and usage.get("status") == "COMPLETE"
            and all(valid_count(value) for value in values.values())
            and values["cached_input_tokens"] <= values["input_tokens"]
            and values["total_tokens"] == values["input_tokens"] + values["output_tokens"]
        )
        if not component_complete:
            bucket["complete_component_coverage"] = False
            bucket["completed_threads_only"] = False
            continue
        for key in ("input_tokens", "cached_input_tokens", "output_tokens"):
            bucket[key] += values[key]
    for model, bucket in by_model.items():
        rate = RATES.get(model)
        if not bucket["complete_component_coverage"]:
            bucket["standard_reference_credits"] = "UNKNOWN"
        elif rate is None:
            bucket["standard_reference_credits"] = "UNKNOWN"
        else:
            bucket["standard_reference_credits"] = ((bucket["input_tokens"] - bucket["cached_input_tokens"]) * rate["input"] +
                                                     bucket["cached_input_tokens"] * rate["cached"] +
                                                     bucket["output_tokens"] * rate["output"]) / 1_000_000
    collector_total = telemetry.get("complete_known_main_and_child_total_tokens")
    full_total = collector_total if telemetry.get("status") == "COMPLETE" and valid_count(collector_total) else "UNKNOWN"
    collector_observed = telemetry.get("observed_subtotal_tokens")
    return {"full_total": full_total,
            "observed_subtotal": collector_observed if valid_count(collector_observed) else "UNKNOWN",
            "collector_status": telemetry.get("status", "UNKNOWN"), "by_model": by_model or "UNKNOWN"}


def run_row(path: Path, document: dict[str, Any]) -> dict[str, Any] | None:
    trials = trial_rows(path, document)
    identities = {json.dumps(row["identity"], sort_keys=True) for row in trials if row["identity"] is not None}
    revisions = {row["configuration_revision"] for row in trials}
    if (len(identities) != 1 or len(revisions) != 1 or None in revisions
            or any(row["identity"] is None for row in trials)
            or len(trials) != len(document.get("attempts", []))):
        return None
    latency_values = [row["latency_seconds"] for row in trials]
    end_to_end = sum(latency_values) if all(
        isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0 for value in latency_values
    ) else "UNKNOWN"
    configuration_valid = bool(trials) and all(row["configuration_valid"] is True for row in trials)
    accepted = document.get("accepted") is True if "accepted" in document else trials[-1]["accepted"] is True
    return {"path": str(path), "identity": trials[0]["identity"], "configuration_revision": trials[0]["configuration_revision"], "variant": document["variant"],
            "attempts": trials, "attempt_count": len(trials), "end_to_end_latency_seconds": end_to_end,
            "success": accepted, "configuration_valid": configuration_valid,
            "successful_attempt_count": sum(row["accepted"] is True for row in trials),
            "configuration_valid_attempt_count": sum(row["configuration_valid"] is True for row in trials)}


def summarize(paths: list[Path]) -> dict[str, Any]:
    rows = []
    runs = []
    for path in paths:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
            rows.extend(trial_rows(path, document))
            run = run_row(path, document)
            if run is not None:
                runs.append(run)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for run in runs:
        ident = run["identity"]
        groups.setdefault((ident["name"], ident["input_hash"]), []).append(run)
    summaries = []
    for (name, input_hash), group_runs in groups.items():
        variants = {}
        for run in group_runs:
            variants.setdefault(run["variant"], []).append(run)
        detail = {}
        for variant, variant_runs in variants.items():
            revisions = {}
            for run in variant_runs:
                revisions.setdefault(run["configuration_revision"], []).append(run)
            detail[variant] = {"configuration_revisions": {}}
            for revision, revision_runs in revisions.items():
                valid = [run["end_to_end_latency_seconds"] for run in revision_runs if run["configuration_valid"] is True and isinstance(run["end_to_end_latency_seconds"], (int, float)) and not isinstance(run["end_to_end_latency_seconds"], bool)]
                successful = [run["end_to_end_latency_seconds"] for run in revision_runs if run["success"] is True and run["configuration_valid"] is True and isinstance(run["end_to_end_latency_seconds"], (int, float)) and not isinstance(run["end_to_end_latency_seconds"], bool)]
                trials = [trial for run in revision_runs for trial in run["attempts"]]
                detail[variant]["configuration_revisions"][revision] = {"runs": revision_runs, "trials": trials, "run_count": len(revision_runs),
                               "success_count": sum(run["success"] is True for run in revision_runs),
                               "configuration_valid_run_count": sum(run["configuration_valid"] is True for run in revision_runs),
                               "median_all_configuration_valid_run_end_to_end_latency_seconds": statistics.median(valid) if valid else "UNKNOWN",
                               "median_successful_configuration_valid_run_end_to_end_latency_seconds": statistics.median(successful) if successful else "UNKNOWN",
                               "token_breakdowns": [token_breakdown(trial["whole_task_telemetry"]) for trial in trials]}
        summaries.append({"name": name, "input_hash": input_hash, "variants": detail})
    return {"rate_reference": RATE_NOTE, "comparison_limit": "No speed, cost, or quality conclusion follows from acceptance alone.",
            "groups": summaries, "uncomparable_trials": [row for row in rows if row["identity"] is None],
            "uncomparable_runs": [str(path) for path in paths if not any(run["path"] == str(path) for run in runs)]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = summarize(result_paths(args.inputs))
    encoded = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
