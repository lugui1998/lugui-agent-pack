import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "compare_evals.py"
SPEC = importlib.util.spec_from_file_location("compare_evals", SCRIPT)
compare = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(compare)


def telemetry(*, model="gpt-5.6-luna", input_tokens=100, cached=40, output=10,
              collector_status="COMPLETE", thread_status="COMPLETED", usage_status="COMPLETE",
              complete_total=110, observed_total=110):
    return {
        "status": collector_status,
        "complete_known_main_and_child_total_tokens": complete_total,
        "observed_subtotal_tokens": observed_total,
        "threads": [{
            "status": thread_status,
            "model": model,
            "usage": {
                "status": usage_status,
                "observed_total_tokens": observed_total,
                "components": {
                    "input_tokens": input_tokens,
                    "cached_input_tokens": cached,
                    "cache_write_input_tokens": 0,
                    "output_tokens": output,
                    "reasoning_output_tokens": 0,
                    "total_tokens": input_tokens + output,
                },
            },
        }],
    }


def attempt(number, elapsed, accepted=True, valid=True, prehash="same", copied="config-a", data=None):
    return {
        "number": number,
        "elapsed_seconds": elapsed,
        "accepted": accepted,
        "configuration_validity": {"valid": valid},
        "whole_task_telemetry": telemetry() if data is None else data,
        "reproducibility_evidence": {"pre": {
            "case_spec_sha256": prehash,
            "copied_file_sha256": {"AGENTS.md": copied, "config.toml": copied},
        }},
    }


class CompareTests(unittest.TestCase):
    def test_credit_estimate_uses_complete_collector_coverage(self):
        result = compare.token_breakdown(telemetry())["by_model"]["gpt-5.6-luna"]
        self.assertEqual((60 * 5 + 40 * .5 + 10 * 30) / 1_000_000,
                         result["standard_reference_credits"])

    def test_incomplete_numeric_usage_remains_unknown(self):
        data = telemetry(collector_status="PARTIAL", thread_status="INCOMPLETE",
                         usage_status="INCOMPLETE", complete_total="UNKNOWN", observed_total=110)
        result = compare.token_breakdown(data)
        model = result["by_model"]["gpt-5.6-luna"]
        self.assertEqual("UNKNOWN", result["full_total"])
        self.assertEqual(110, result["observed_subtotal"])
        self.assertEqual("UNKNOWN", model["standard_reference_credits"])
        self.assertFalse(model["complete_component_coverage"])

    def test_invalid_counts_and_spark_credit_are_unknown(self):
        invalid = telemetry(input_tokens=True, cached=0, output=2, complete_total="UNKNOWN",
                            observed_total="UNKNOWN", collector_status="PARTIAL")
        self.assertEqual("UNKNOWN", compare.token_breakdown(invalid)["by_model"]["gpt-5.6-luna"]["standard_reference_credits"])
        spark = telemetry(model="gpt-5.3-codex-spark")
        self.assertIn("Spark", compare.token_breakdown(spark)["by_model"]["gpt-5.3-codex-spark"]["standard_reference_credits"])
        too_cached = telemetry(input_tokens=2, cached=3, output=1, complete_total="UNKNOWN",
                               observed_total="UNKNOWN", collector_status="PARTIAL")
        self.assertEqual("UNKNOWN", compare.token_breakdown(too_cached)["by_model"]["gpt-5.6-luna"]["standard_reference_credits"])
        missing_cached = telemetry()
        del missing_cached["threads"][0]["usage"]["components"]["cached_input_tokens"]
        self.assertEqual("UNKNOWN", compare.token_breakdown(missing_cached)["by_model"]["gpt-5.6-luna"]["standard_reference_credits"])

    def test_summarize_keeps_retries_in_end_to_end_and_reports_failed_valid_runs(self):
        document = {"case": "x", "variant": "current", "attempts": [
            attempt(1, 1, accepted=False), attempt(2, 9, accepted=True),
        ]}
        failed = {"case": "x", "variant": "current", "accepted": False, "attempts": [
            attempt(1, 4, accepted=False),
        ]}
        invalid = {"case": "x", "variant": "current", "attempts": [
            attempt(1, 8, accepted=True, valid=False),
        ]}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for index, item in enumerate((document, failed, invalid)):
                (root / f"result-{index}.json").write_text(json.dumps(item), encoding="utf-8")
            summary = compare.summarize(compare.result_paths([root]))
        revision = json.dumps({"AGENTS.md": "config-a", "config.toml": "config-a"}, sort_keys=True, separators=(",", ":"))
        detail = summary["groups"][0]["variants"]["current"]["configuration_revisions"][revision]
        self.assertEqual(3, detail["run_count"])
        self.assertEqual(2, detail["success_count"])
        self.assertEqual(2, detail["configuration_valid_run_count"])
        self.assertEqual(7, detail["median_all_configuration_valid_run_end_to_end_latency_seconds"])
        self.assertEqual(10, detail["median_successful_configuration_valid_run_end_to_end_latency_seconds"])
        self.assertEqual(10, detail["runs"][0]["end_to_end_latency_seconds"])

    def test_identity_is_per_attempt_and_configuration_revisions_do_not_pool(self):
        mismatched = {"case": "x", "variant": "current", "attempts": [
            attempt(1, 1, prehash="first"), attempt(2, 2, prehash="second"),
        ]}
        before = {"case": "x", "variant": "current", "attempts": [attempt(1, 1, copied="before")]}
        after = {"case": "x", "variant": "current", "attempts": [attempt(1, 2, copied="after")]}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name, item in (("mismatch", mismatched), ("before", before), ("after", after)):
                (root / f"{name}.json").write_text(json.dumps(item), encoding="utf-8")
            summary = compare.summarize(compare.result_paths([root]))
        self.assertEqual(1, len(summary["groups"]))
        revisions = summary["groups"][0]["variants"]["current"]["configuration_revisions"]
        self.assertEqual(2, len(revisions))
        self.assertEqual(1, len(summary["uncomparable_runs"]))

    def test_directory_reader_skips_batch_and_other_json(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "batch.json").write_text(json.dumps({"runs": []}), encoding="utf-8")
            (root / "other.json").write_text(json.dumps({"variant": "x"}), encoding="utf-8")
            (root / "trial.json").write_text(json.dumps({"case": "x", "variant": "x", "attempts": []}), encoding="utf-8")
            paths = compare.result_paths([root])
        self.assertEqual([root / "trial.json"], paths)


if __name__ == "__main__":
    unittest.main()
