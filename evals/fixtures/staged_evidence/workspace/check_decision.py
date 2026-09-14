#!/usr/bin/env python3
"""Artifact-only checker; it does not inspect or control runtime child processes."""
import json
import sys
from pathlib import Path


EXPECTED = {
    "decision": "ship_current",
    "release": "Atlas",
    "version": "4.2.1",
    "record_date": "2026-09-10",
    "source_url": "https://release-record.example.invalid/atlas/4.2.1",
    "evidence_basis": "current_record_authoritative",
}


def check(decision_path):
    decision_file = Path(decision_path).resolve()
    root = decision_file.parent
    decision = json.loads(decision_file.read_text(encoding="utf-8"))
    record = json.loads((root / "current_record.json").read_text(encoding="utf-8"))
    for key, expected in EXPECTED.items():
        assert decision.get(key) == expected, f"{key} does not match authoritative decision"
        if key in {"release", "version", "record_date", "source_url"}:
            assert record.get(key) == expected, f"current record mismatch for {key}"
    limitations = decision.get("limitations")
    assert isinstance(limitations, list) and limitations and all(isinstance(item, str) and item.strip() for item in limitations)
    report = root / "EVAL_REPORT.md"
    report_text = report.read_text(encoding="utf-8").strip()
    assert report_text


if __name__ == "__main__":
    try:
        check(sys.argv[1])
    except Exception as error:
        print(f"staged_evidence checker failed: {error}", file=sys.stderr)
        raise SystemExit(1)
