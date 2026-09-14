#!/usr/bin/env python3
import json
import sys
from pathlib import Path


EXPECTED = {
    "files": [
        {"path": "records/east.txt", "id": "E-04", "nonblank_lines": 3},
        {"path": "records/north.txt", "id": "N-17", "nonblank_lines": 4},
        {"path": "records/west.txt", "id": "W-09", "nonblank_lines": 2},
    ]
}


def check(workspace):
    root = Path(workspace).resolve()
    actual = json.loads((root / "RESULT.json").read_text(encoding="utf-8"))
    assert actual == EXPECTED
    report = root / "EVAL_REPORT.md"
    assert report.is_file() and report.read_text(encoding="utf-8").strip()
    forbidden = ["availability.json", "events.json", "trace.json", "spawn_result.json"]
    assert not any((root / name).exists() for name in forbidden)


if __name__ == "__main__":
    try:
        check(sys.argv[1])
    except Exception as error:
        print(f"unavailable_role_fallback oracle failed: {error}", file=sys.stderr)
        raise SystemExit(1)
