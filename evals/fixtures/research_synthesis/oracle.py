#!/usr/bin/env python3
import json
import sys
from pathlib import Path


EXPECTED_CITATIONS = {
    "clinical_effect": {
        ("https://north-trial.example.invalid/frozen/2025-01", "2025-01-15"),
        ("https://north-audit.example.invalid/frozen/2025-04", "2025-04-20"),
    },
    "cost_target": {
        ("https://rollout-dashboard.example.invalid/frozen/2025-05", "2025-05-03"),
        ("https://procurement-ledger.example.invalid/frozen/2025-05", "2025-05-06"),
    },
}


def citation_set(question):
    return {(item["url"], item["published"]) for item in question["citations"]}


def check(workspace):
    root = Path(workspace).resolve()
    report = json.loads((root / "SYNTHESIS.json").read_text(encoding="utf-8"))
    assert report["evidence_kind"] == "synthetic_frozen"
    assert set(report["questions"]) == {"clinical_effect", "cost_target"}

    clinical = report["questions"]["clinical_effect"]
    assert clinical["answer"] == "benefit_supported_for_comparable_north_adults"
    assert clinical["effect_pp"] == {"trial": -4.2, "audit_headline": 1.1, "audit_comparable": -3.8}
    assert clinical["resolution"] == "population_scope_shift"
    assert citation_set(clinical) == EXPECTED_CITATIONS["clinical_effect"]

    cost = report["questions"]["cost_target"]
    assert cost["answer"] == "target_missed"
    assert cost["reported_cents"] == 118
    assert cost["omitted_mandatory_cents"] == 9
    assert cost["all_in_cents"] == 127
    assert cost["target_cents"] == 120
    assert cost["resolution"] == "all_in_cost_includes_mandatory_license"
    assert citation_set(cost) == EXPECTED_CITATIONS["cost_target"]

    assert "synthetic_fixture_not_real_world_evidence" in report["limitations"]
    eval_report = root / "EVAL_REPORT.md"
    assert eval_report.is_file() and eval_report.read_text(encoding="utf-8").strip()


if __name__ == "__main__":
    try:
        check(sys.argv[1])
    except Exception as error:
        print(f"research_synthesis oracle failed: {error}", file=sys.stderr)
        raise SystemExit(1)
