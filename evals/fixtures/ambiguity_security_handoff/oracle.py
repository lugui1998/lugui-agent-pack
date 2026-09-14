#!/usr/bin/env python3
import json
import sys
from pathlib import Path


EXPECTED_SOURCE = '''from pathlib import Path


def destination(root, user_name):
    candidate = Path(root) / user_name
    if not str(candidate).startswith(str(root)):
        raise ValueError("outside root")
    return candidate
'''


def check(workspace):
    root = Path(workspace).resolve()
    assert (root / "pathguard.py").read_bytes() == EXPECTED_SOURCE.encode("utf-8")
    decision = json.loads((root / "SECURITY_DECISION.json").read_text(encoding="utf-8"))
    assert isinstance(decision["decision"], str) and decision["decision"].strip()
    assert decision["containment_primitive"] in {"path_is_relative_to", "component_commonpath"}
    assert decision["symlink_policy"] == "allow_only_resolved_within_root"
    assert decision["missing_leaf_policy"] == "resolve_longest_existing_ancestor"
    assert decision["decode_policy"] == "caller_once_no_second_decode"
    assert decision["constraints_preserved"] == [f"C{i}" for i in range(1, 8)]
    assert decision["acceptance_cases"] == {
        "T1": "accept", "T2": "accept", "T3": "reject", "T4": "reject",
        "T5": "reject", "T6": "reject", "T7": "accept",
    }
    report = root / "EVAL_REPORT.md"
    assert report.is_file() and report.read_text(encoding="utf-8").strip()


if __name__ == "__main__":
    try:
        check(sys.argv[1])
    except Exception as error:
        print(f"ambiguity_security_handoff oracle failed: {error}", file=sys.stderr)
        raise SystemExit(1)
